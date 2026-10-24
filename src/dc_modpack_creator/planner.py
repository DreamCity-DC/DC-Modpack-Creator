from __future__ import annotations

import re
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .config import AppPaths, Settings, TaskConfig, archive_path, load_task
from .errors import Cancelled, ExportError
from .metadata import resolve_dependencies
from .rules import RuleSet

PERSONAL_DIRS = ("Pictures", "Musics", "Help")
PERSONAL_FILES = ("Custom.xaml", "Setup.ini", "hints.txt", "Logo.png")


def path_key(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/").casefold().rstrip("/")


def within(child: Path, parent: Path) -> bool:
    c, p = path_key(child), path_key(parent)
    return c == p or c.startswith(p + "/")


def linked(path: Path) -> bool:
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def walk_files(root: Path, check: Callable[[], None]) -> tuple[list[Path], list[Path]]:
    if linked(root):
        raise ExportError(f"不支持符号链接或目录联接：{root}")
    if not root.is_dir():
        raise ExportError(f"目录不存在：{root}")
    files, dirs = [], []
    stack = [root]
    while stack:
        check()
        current = stack.pop()
        try:
            entries = sorted(current.iterdir(), key=lambda p: p.name.casefold())
            for p in entries:
                check()
                if linked(p):
                    raise ExportError(f"不支持符号链接或目录联接：{p}")
                if p.is_dir():
                    dirs.append(p)
                    stack.append(p)
                elif p.is_file():
                    files.append(p)
                else:
                    raise ExportError(f"无法读取源文件：{p}")
        except OSError as exc:
            raise ExportError(f"无法枚举目录 {current}：{exc}") from exc
    return files, dirs


@dataclass(frozen=True)
class PlannedFile:
    source: Path
    destination: str
    layer: str
    size: int
    mtime_ns: int


@dataclass
class Plan:
    config: TaskConfig
    dependencies: dict[str, str]
    files: list[PlannedFile] = field(default_factory=list)
    directories: set[tuple[str, str]] = field(default_factory=set)
    warnings: list[str] = field(default_factory=list)
    rule_hits: list[tuple[int, str, int]] = field(default_factory=list)
    existing: dict[Path, tuple[int, int]] = field(default_factory=dict)

    @property
    def total_bytes(self) -> int:
        return sum(f.size for f in self.files)


@dataclass
class PreflightResult:
    path: Path
    plan: Plan | None = None
    error: str = ""
    cancelled: bool = False


def target_family(config: TaskConfig) -> list[Path]:
    if not config.output_dir.is_dir():
        return []
    stem = re.escape(config.output_stem)
    pattern = re.compile(
        stem + (r"(?:\.zip|\.z\d{2,}|\.zip\.\d{3,})" if config.full_package else r"\.mrpack"),
        re.IGNORECASE,
    )
    return sorted(
        (p for p in config.output_dir.iterdir() if pattern.fullmatch(p.name)),
        key=lambda p: p.name.casefold(),
    )


def matches_target(config: TaskConfig, path: Path) -> bool:
    if path_key(config.output_dir) != path_key(path.parent):
        return False
    suffix = r"(?:\.zip|\.z\d{2,}|\.zip\.\d{3,})" if config.full_package else r"\.mrpack"
    return re.fullmatch(re.escape(config.output_stem) + suffix, path.name, re.IGNORECASE) is not None


def fingerprint(path: Path) -> tuple[int, int]:
    if not path.is_file() or linked(path):
        raise ExportError(f"目标不是普通文件：{path}")
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns


def recursive_inputs(c: TaskConfig) -> list[Path]:
    roots = [c.instance_dir]
    roots.extend(e.source for e in c.extras if e.source.is_dir())
    if c.full_package and c.launcher:
        roots.append(c.launcher.parent / "PCL")
    return roots


def writable(directory: Path) -> None:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=directory):
            pass
    except OSError as exc:
        raise ExportError(f"目录不可写 {directory}：{exc}") from exc


def plan_task(c: TaskConfig, check: Callable[[], None] = lambda: None) -> Plan:
    check()
    dependencies = resolve_dependencies(c.instance_dir)
    rules = RuleSet(c.rules)
    plan = Plan(c, dependencies)
    entries: dict[tuple[str, str], tuple[str, Path | None]] = {}

    def directory(layer: str, dest: str) -> None:
        dest = archive_path(dest)
        for i in range(1, len(dest.split("/")) + 1):
            part = "/".join(dest.split("/")[:i])
            key = layer, part.casefold()
            if key in entries and entries[key][0] == "file":
                raise ExportError(f"包内文件与目录冲突：{part}")
            entries.setdefault(key, ("dir", None))
            plan.directories.add((layer, part))

    def add(src: Path, dest: str, layer: str) -> None:
        check()
        dest = archive_path(dest)
        if linked(src):
            raise ExportError(f"不支持符号链接：{src}")
        key = layer, dest.casefold()
        if key in entries:
            # Instance PCL may already be selected by a game rule.
            if entries[key] == ("file", src):
                return
            raise ExportError(f"包内目标冲突：{dest}")
        if "/" in dest:
            directory(layer, dest.rsplit("/", 1)[0])
        try:
            with src.open("rb") as f:
                f.read(1)
            st = src.stat()
        except OSError as exc:
            raise ExportError(f"源文件不可读 {src}：{exc}") from exc
        entries[key] = "file", src
        plan.files.append(PlannedFile(src, dest, layer, st.st_size, st.st_mtime_ns))

    def tree(src: Path, dest: str, layer: str) -> None:
        directory(layer, dest)
        files, dirs = walk_files(src, check)
        for sub in dirs:
            directory(layer, f"{dest}/{sub.relative_to(src).as_posix()}")
        for sub in files:
            add(sub, f"{dest}/{sub.relative_to(src).as_posix()}", layer)

    entries["inner", "modrinth.index.json"] = "file", None
    directory("inner", "overrides")
    if c.full_package:
        entries["outer", "modpack.mrpack"] = "file", None
    files, _ = walk_files(c.instance_dir, check)
    for src in files:
        rel = src.relative_to(c.instance_dir).as_posix()
        if rules.select(rel):
            add(src, f"overrides/{rel}", "inner")
    plan.warnings.extend(rules.warnings)
    plan.rule_hits = [(r.line, r.text, n) for r, n in zip(rules.rules, rules.hits, strict=True)]
    if c.full_package:
        assert c.launcher is not None
        add(c.launcher, "启动Minecraft.exe", "outer")
        instance_pcl = c.instance_dir / "PCL"
        if instance_pcl.exists():
            tree(instance_pcl, "overrides/PCL", "inner")
        personal = c.launcher.parent / "PCL"
        for name in PERSONAL_DIRS:
            src = personal / name
            if src.exists():
                tree(src, f"PCL/{name}", "outer")
        for name in PERSONAL_FILES:
            src = personal / name
            if src.exists():
                add(src, f"PCL/{name}", "outer")
    for e in c.extras:
        layer = "outer" if c.full_package else "inner"
        if (layer, e.destination.casefold()) in entries:
            raise ExportError(f"包内目标冲突：{e.destination}")
        if not c.full_package and e.destination.split("/")[0].casefold() in {
            "overrides",
            "client-overrides",
            "server-overrides",
        }:
            raise ExportError(f"额外文件目标使用 mrpack 保留目录：{e.destination}")
        if e.source.is_dir():
            tree(e.source, e.destination, layer)
        else:
            add(e.source, e.destination, layer)
    plan.existing = {p: fingerprint(p) for p in target_family(c)}
    for f in plan.files:
        if path_key(f.source) in {path_key(p) for p in plan.existing} or path_key(
            f.source
        ) == path_key(c.target):
            raise ExportError(f"输出文件与源文件相同：{f.source}")
    writable(c.output_dir)
    writable(c.temp_root)
    # Staging + inner mrpack + direct output; combine the estimate on the same device.
    staging = plan.total_bytes * (3 if c.full_package else 1) + 1_048_576
    output = plan.total_bytes * (2 if c.full_package and c.volume_size else 1) + 1_048_576
    if c.output_dir.stat().st_dev == c.temp_root.stat().st_dev:
        if shutil.disk_usage(c.temp_root).free < staging + output:
            raise ExportError("输出与临时目录所在磁盘空间不足（按未压缩体积估算）")
    elif (
        shutil.disk_usage(c.temp_root).free < staging
        or shutil.disk_usage(c.output_dir).free < output
    ):
        raise ExportError("输出或临时目录空间不足（按未压缩体积估算）")
    check()
    return plan


def preflight_batch(
    paths: list[Path],
    app: AppPaths,
    settings: Settings,
    check: Callable[[int], None] = lambda _: None,
    progress: Callable[[int], None] = lambda _: None,
) -> list[PreflightResult]:
    results = [PreflightResult(p) for p in paths]
    configs: dict[int, TaskConfig] = {}
    for i, path in enumerate(paths):
        try:
            check(i)
            configs[i] = load_task(path, app, settings)
        except Cancelled:
            results[i].cancelled = True
        except (ExportError, OSError) as exc:
            results[i].error = str(exc)
    # All loaded input roots participate even if their own planning later fails.
    roots = [p for c in configs.values() for p in recursive_inputs(c)]
    input_files = [e.source for c in configs.values() for e in c.extras if not e.source.is_dir()]
    targets: dict[tuple[str, bool], list[int]] = {}
    for i, c in configs.items():
        key = path_key(c.output_dir / c.output_stem), c.full_package
        targets.setdefault(key, []).append(i)
        if any(matches_target(c, src) for src in input_files):
            results[i].error = f"输出目标会覆盖本批额外源文件：{c.target}"
        for output in (c.output_dir, c.temp_root):
            if any(within(output, root) for root in roots):
                results[i].error = f"输出或临时目录位于本批输入目录中：{output}"
    for indices in targets.values():
        if len(indices) > 1:
            for i in indices:
                results[i].error = "本批任务输出目标冲突：" + ", ".join(
                    paths[n].name for n in indices
                )
    for i, c in configs.items():
        if results[i].error or results[i].cancelled:
            continue
        progress(i)
        try:
            check(i)
            results[i].plan = plan_task(c, lambda index=i: check(index))
        except Cancelled:
            results[i].cancelled = True
        except (ExportError, OSError) as exc:
            results[i].error = str(exc)
    return results
