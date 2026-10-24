from __future__ import annotations

import json
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path

from .archive import InfoZip, SevenZip
from .errors import ExportError
from .planner import Plan, fingerprint, linked, target_family


def export_plan(
    plan: Plan,
    *,
    replace: bool = False,
    check: Callable[[], None] = lambda: None,
    progress: Callable[[str, int, int], None] = lambda *_: None,
    log: Callable[[str], None] = lambda _: None,
) -> list[Path]:
    c = plan.config
    # Validate the engine before any deletion or staging.
    stage = "生成 mrpack"
    engine = SevenZip(check, log, lambda p: progress(stage, p, 100))
    splitter = InfoZip(check, log, lambda a, b: progress(stage, a, b)) if c.volume_size else None
    tmp: Path | None = None
    output_tmp: Path | None = None
    output_owned = False
    created: list[Path] = []
    error: BaseException | None = None
    result: list[Path] = []
    try:
        check()
        if plan.existing and not replace:
            raise ExportError("目标文件已存在，需要确认全部替换")
        progress("准备文件", 0, 0)
        tmp = Path(tempfile.mkdtemp(prefix="task-", dir=c.temp_root))
        log(f"临时目录：{tmp}")
        inner, outer = tmp / "inner", tmp / "outer"
        inner.mkdir()
        outer.mkdir()
        roots = {"inner": inner, "outer": outer}
        for layer, dest in sorted(plan.directories):
            check()
            (roots[layer] / dest).mkdir(parents=True, exist_ok=True)
        copied = 0
        progress("复制文件", 0, max(1, plan.total_bytes))
        for item in plan.files:
            check()
            src, dst = item.source, roots[item.layer] / item.destination
            if linked(src) or fingerprint(src) != (item.size, item.mtime_ns):
                raise ExportError(f"源文件在预检后发生变化，请重试：{src}")
            dst.parent.mkdir(parents=True, exist_ok=True)
            with src.open("rb") as reader, dst.open("xb") as writer:
                while chunk := reader.read(1_048_576):
                    check()
                    writer.write(chunk)
                    copied += len(chunk)
                    progress("复制文件", copied, max(1, plan.total_bytes))
            if fingerprint(src) != (item.size, item.mtime_ns):
                raise ExportError(f"复制期间源文件发生变化：{src}")
            shutil.copystat(src, dst)
            log(f"复制：{src} → {item.layer}/{item.destination}")
        index = {
            "formatVersion": 1,
            "game": "minecraft",
            "versionId": c.version,
            "name": c.name,
            "files": [],
            "dependencies": plan.dependencies,
        }
        if c.summary:
            index["summary"] = c.summary
        (inner / "modrinth.index.json").write_text(
            json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        inner_names = [
            p.relative_to(inner).as_posix()
            for p in sorted(inner.rglob("*"))
            if p.is_file() or not any(p.iterdir())
        ]

        def delete_old() -> None:
            nonlocal output_owned
            check()
            progress("准备输出", 0, 0)
            current = {p: fingerprint(p) for p in target_family(c)}
            if current != plan.existing:
                raise ExportError("目标文件在预检后发生变化，请重新预检并确认替换")
            for old in plan.existing:
                check()
                log(f"删除已确认的旧文件：{old}")
                old.unlink()
            output_owned = True

        if not c.full_package:
            delete_old()
            stage = "生成 mrpack"
            progress(stage, 0, 100)
            engine.add(c.target, inner, inner_names, c.level, c.threads, tmp / "inner-list.txt")
            result = [c.target]
        else:
            stage = "生成 mrpack"
            progress(stage, 0, 100)
            engine.add(
                outer / "modpack.mrpack",
                inner,
                inner_names,
                c.level,
                c.threads,
                tmp / "inner-list.txt",
            )
            delete_old()
            # Conversion needs both archives on the output disk; publish only finished volumes.
            archive = c.target
            if splitter:
                output_tmp = Path(tempfile.mkdtemp(prefix=".dc-zip-", dir=c.output_dir))
                archive = output_tmp / "complete.zip"
            stage = "压缩完整 ZIP"
            progress(stage, 0, 100)
            outer_names = [
                p.relative_to(outer).as_posix()
                for p in sorted(outer.rglob("*"))
                if p != outer / "modpack.mrpack" and (p.is_file() or not any(p.iterdir()))
            ]
            if outer_names:
                engine.add(archive, outer, outer_names, c.level, c.threads, tmp / "outer-list.txt")
            stage = "写入内嵌 mrpack"
            progress(stage, 0, 100)
            engine.add(archive, outer, ["modpack.mrpack"], 0, c.threads, tmp / "stored-list.txt")
            result = [c.target]
            if splitter:
                stage = "生成分卷"
                progress(stage, 0, archive.stat().st_size)
                result = splitter.split(archive, c.target, c.volume_size, created)
        check()
        log("导出完成：" + ", ".join(str(p) for p in result))
    except BaseException as exc:
        error = exc
    finally:
        cleanup_errors = []
        if error and output_owned:
            # Private conversion files are removed below; only remove published files we own.
            for p in [c.target] if not splitter else created:
                try:
                    if p.exists():
                        p.unlink()
                except OSError as exc:
                    cleanup_errors.append(f"{p}：{exc}")
        for folder, root in ((tmp, c.temp_root), (output_tmp, c.output_dir)):
            if folder is None:
                continue
            try:
                if folder.parent.resolve() != root.resolve() or linked(folder):
                    raise ExportError(f"临时目录发生变化：{folder}")
                shutil.rmtree(folder)
            except (OSError, ExportError) as exc:
                cleanup_errors.append(f"{folder}：{exc}")
        if cleanup_errors:
            message = (
                (str(error) + "\n" if error else "")
                + "清理失败，残留路径：\n"
                + "\n".join(cleanup_errors)
            )
            log(message)
            raise ExportError(message) from error
    if error:
        raise error
    return result
