from __future__ import annotations

import os
import re
import tempfile
import tomllib
from dataclasses import dataclass, field
from decimal import Decimal, localcontext
from pathlib import Path, PureWindowsPath
from typing import Any

import tomli_w

from .errors import ExportError

LEVELS = (0, 1, 3, 5, 7, 9)
VOLUME_UNITS = {
    "B": 1,
    "KB": 1_024,
    "MB": 1_048_576,
    "GB": 1_073_741_824,
}


@dataclass(frozen=True)
class AppPaths:
    work: Path

    @property
    def configs(self) -> Path:
        return self.work / "导出"

    @property
    def data(self) -> Path:
        return self.configs / "dc-modpack-creator"

    @property
    def settings(self) -> Path:
        return self.data / "config.toml"

    @property
    def logs(self) -> Path:
        return self.data / "logs"

    def initialize(self) -> None:
        self.logs.mkdir(parents=True, exist_ok=True)
        if not self.settings.exists():
            save_settings(self, Settings())

    def discover(self) -> list[Path]:
        return sorted(
            (p for p in self.configs.iterdir() if p.is_file() and p.suffix.lower() == ".toml"),
            key=lambda p: (p.name.casefold(), p.name),
        )


@dataclass(frozen=True)
class Settings:
    level: int = 5
    volume_size: int = 0
    threads: int = 0
    temporary_directory: str = ""
    selected_configs: tuple[str, ...] = ()
    window: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class Extra:
    source: Path
    destination: str


@dataclass(frozen=True)
class TaskConfig:
    path: Path
    name: str
    version: str
    summary: str
    full_package: bool
    instance_dir: Path
    launcher: Path | None
    output_dir: Path
    rules: str
    extras: tuple[Extra, ...]
    level: int
    volume_size: int
    threads: int
    temp_root: Path

    @property
    def output_stem(self) -> str:
        return " ".join(part for part in (self.name, self.version) if part)

    @property
    def target(self) -> Path:
        return self.output_dir / (self.output_stem + (".zip" if self.full_package else ".mrpack"))


def resolve_path(value: str, work: Path) -> Path:
    # No shell, environment-variable or user-home expansion.
    p = Path(value.replace("\\", "/"))
    return (p if p.is_absolute() else work / p).resolve()


def create_example_task(paths: AppPaths) -> Path:
    template = Path(__file__).parent / "assets" / "templates" / "导出配置示例.toml"
    content = template.read_bytes()
    paths.configs.mkdir(parents=True, exist_ok=True)
    index = 1
    while True:
        suffix = f" ({index})" if index > 1 else ""
        path = paths.configs / f"导出配置示例{suffix}.toml"
        try:
            with path.open("xb") as stream:
                stream.write(content)
            return path
        except FileExistsError:
            index += 1


def read_toml(path: Path) -> dict[str, Any]:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise ExportError(f"{path.name}：{exc}") from exc


def table(data: dict, key: str, allowed: set[str]) -> dict:
    value = data.get(key, {})
    if not isinstance(value, dict):
        raise ExportError(f"{key} 必须是 TOML 表")
    unknown = value.keys() - allowed
    if unknown:
        raise ExportError(f"{key} 中存在未知字段：{', '.join(sorted(unknown))}")
    return value


def string(data: dict, key: str, default: str | None = None, *, allow_empty: bool = False) -> str:
    v = data.get(key, default)
    if not isinstance(v, str) or (not allow_empty and key != "summary" and not v.strip()):
        raise ExportError(f"{key} 必须是非空字符串")
    if "\x00" in v:
        raise ExportError(f"{key} 包含空字符")
    return v


def integer(data: dict, key: str, default: int) -> int:
    v = data.get(key, default)
    if type(v) is not int:
        raise ExportError(f"{key} 必须是整数")
    return v


def parse_volume_size(value: Any) -> int:
    if not isinstance(value, str):
        raise ExportError('compression_volume_size 必须是容量字符串，例如 "2GB"；"0" 关闭分卷')
    value = value.strip()
    if value == "0":
        return 0
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*(B|KB|MB|GB)", value, re.IGNORECASE)
    if match is None:
        raise ExportError('compression_volume_size 格式无效，请使用 "2GB"、"1.5MB" 或 "0"')
    amount, unit = match.groups()
    factor = next(f for u, f in VOLUME_UNITS.items() if u.casefold() == unit.casefold())
    with localcontext() as context:
        context.prec = len(amount) + 20
        size = Decimal(amount) * factor
    if size != size.to_integral_value():
        raise ExportError("分卷大小必须换算为整数字节")
    return int(size)


def format_volume_size(size: int) -> str:
    if size == 0:
        return "0"
    # Keep the exact byte count while choosing a concise unit (base 1024).
    candidates = []
    with localcontext() as context:
        context.prec = len(str(size)) + 30
        for unit, factor in VOLUME_UNITS.items():
            amount = format(Decimal(size) / factor, "f")
            if "." in amount:
                amount = amount.rstrip("0").rstrip(".")
            candidates.append(f"{amount}{unit}")
    return min(candidates, key=len)


def validate_compression(level: int, volume: int, threads: int) -> None:
    if type(level) is not int or level not in LEVELS:
        raise ExportError("压缩等级只允许 0、1、3、5、7、9")
    if type(volume) is not int or volume < 0:
        raise ExportError("分卷大小必须为非负整数字节")
    if type(threads) is not int or threads < 0:
        raise ExportError("压缩线程数必须为非负整数")


def validate_name(value: str) -> str:
    stem = value.split(".", 1)[0].upper()
    if (
        not value
        or value in {".", ".."}
        or value.endswith((" ", "."))
        or re.search(r'[<>:"/\\|?*\x00-\x1f]', value)
        or stem
        in {
            "CON",
            "PRN",
            "AUX",
            "NUL",
            *(f"COM{i}" for i in range(1, 10)),
            *(f"LPT{i}" for i in range(1, 10)),
        }
    ):
        raise ExportError(f"不合法的 Windows 文件名：{value!r}")
    return value


def archive_path(value: str) -> str:
    value = value.replace("\\", "/")
    if not value or value.startswith("/") or PureWindowsPath(value).drive:
        raise ExportError(f"包内目标必须是非空相对路径：{value!r}")
    parts = value.split("/")
    for part in parts:
        validate_name(part)
    return "/".join(parts)


def load_settings(paths: AppPaths) -> Settings:
    d = read_toml(paths.settings)
    unknown = d.keys() - {
        "settings_version",
        "execution",
        "output",
        "state",
        "window",
    }
    if unknown:
        raise ExportError(f"未知工具设置：{', '.join(sorted(unknown))}")
    if integer(d, "settings_version", 1) != 1:
        raise ExportError("不支持的工具配置版本")
    # Accept the legacy global output directory without applying or saving it.
    output = table(d, "output", {"directory", "compression_level", "compression_volume_size"})
    e = table(d, "execution", {"compression_threads", "temporary_directory"})
    s = table(d, "state", {"selected_configs"})
    w = table(d, "window", {"x", "y"})
    level, volume, threads = (
        integer(output, "compression_level", 5),
        parse_volume_size(output.get("compression_volume_size", "0")),
        integer(e, "compression_threads", 0),
    )
    validate_compression(level, volume, threads)
    selected = s.get("selected_configs", [])
    if not isinstance(selected, list) or not all(isinstance(p, str) for p in selected):
        raise ExportError("selected_configs 必须是路径字符串数组")
    for p in selected:
        normalized = p.replace("\\", "/")
        if (
            not normalized.startswith("导出/")
            or len(normalized.split("/")) != 2
            or not normalized.lower().endswith(".toml")
        ):
            raise ExportError(f"无效的已选配置路径：{p}")
    for k, v in w.items():
        if type(v) is not int:
            raise ExportError(f"window.{k} 必须是整数")
    return Settings(
        level,
        volume,
        threads,
        string(e, "temporary_directory") if "temporary_directory" in e else "",
        tuple(selected),
        dict(w),
    )


def save_settings(paths: AppPaths, s: Settings) -> None:
    validate_compression(s.level, s.volume_size, s.threads)
    execution: dict[str, Any] = {"compression_threads": s.threads}
    if s.temporary_directory:
        execution["temporary_directory"] = s.temporary_directory
    data = {
        "settings_version": 1,
        "output": {
            "compression_level": s.level,
            "compression_volume_size": format_volume_size(s.volume_size),
        },
        "execution": execution,
        "state": {"selected_configs": list(s.selected_configs)},
        "window": s.window,
    }
    paths.data.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="config-", suffix=".tmp", dir=paths.data)
    tmp = Path(name)
    try:
        with os.fdopen(fd, "wb") as f:
            tomli_w.dump(data, f)
        tmp.replace(paths.settings)
    finally:
        tmp.unlink(missing_ok=True)


def load_task(path: Path, paths: AppPaths, settings: Settings) -> TaskConfig:
    d = read_toml(path)
    unknown = d.keys() - {
        "schema_version",
        "pack",
        "source",
        "launcher",
        "output",
        "files",
        "extras",
    }
    if unknown:
        raise ExportError(f"未知任务字段：{', '.join(sorted(unknown))}")
    if integer(d, "schema_version", 1) != 1:
        raise ExportError("不支持的任务配置版本")
    p = table(d, "pack", {"name", "version", "summary", "full_package"})
    source = table(d, "source", {"base_dir", "instance_dir"})
    base_dir = resolve_path(string(source, "base_dir", "."), paths.work)
    launcher = table(d, "launcher", {"executable"})
    output = table(d, "output", {"directory", "compression_level", "compression_volume_size"})
    files = table(d, "files", {"rules"})
    name, version = (string(p, key, "", allow_empty=True).strip() for key in ("name", "version"))
    if not name and not version:
        raise ExportError("配置名称缺失")
    validate_name(" ".join(part for part in (name, version) if part))
    full = p.get("full_package", False)
    if type(full) is not bool:
        raise ExportError("full_package 必须是 true 或 false")
    level = integer(output, "compression_level", settings.level)
    volume = (
        parse_volume_size(output["compression_volume_size"])
        if "compression_volume_size" in output
        else settings.volume_size
        if full
        else 0
    )
    validate_compression(level, volume, settings.threads)
    if not full and volume:
        raise ExportError("mrpack 包不支持分卷，请移除任务中的分卷设置")
    rules = files.get("rules", "")
    if not isinstance(rules, str):
        raise ExportError("files.rules 必须是多行字符串")
    extra_list = d.get("extras", [])
    if not isinstance(extra_list, list):
        raise ExportError("extras 必须是映射数组")
    extras = []
    for ex in extra_list:
        if not isinstance(ex, dict) or ex.keys() - {"source", "destination"}:
            raise ExportError("extras 只允许 source 与 destination 字段")
        src = resolve_path(string(ex, "source"), base_dir)
        extras.append(Extra(src, archive_path(string(ex, "destination", src.name))))
    return TaskConfig(
        path.resolve(),
        name,
        version,
        string(p, "summary", ""),
        full,
        resolve_path(string(source, "instance_dir"), base_dir),
        resolve_path(string(launcher, "executable"), base_dir) if full else None,
        resolve_path(string(output, "directory", "导出"), base_dir),
        rules,
        tuple(extras),
        level,
        volume,
        settings.threads,
        resolve_path(settings.temporary_directory, paths.work)
        if settings.temporary_directory
        else (Path(tempfile.gettempdir()) / "DC-Modpack-Creator").resolve(),
    )
