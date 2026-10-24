from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QLockFile
from PySide6.QtWidgets import QApplication

from . import APPLICATION_NAME, __version__
from .config import AppPaths, load_settings, resolve_path
from .errors import ExportError
from .exporter import export_plan
from .planner import preflight_batch


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=APPLICATION_NAME)
    parser.add_argument("--version", action="version", version=__version__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", nargs="+", metavar="TOML", help="无界面预检")
    group.add_argument("--export", dest="exports", nargs="+", metavar="TOML", help="无界面串行导出")
    parser.add_argument("--replace", action="store_true", help="确认替换所有已存在的产物")
    args = parser.parse_args(argv)
    headless = bool(args.check or args.exports)
    qt = QCoreApplication([sys.argv[0]]) if headless else QApplication([sys.argv[0]])
    qt.setApplicationName(APPLICATION_NAME)
    qt.setApplicationVersion(__version__)
    if not headless:
        from .ui_common import configure_font, show_message

        configure_font(qt)
    paths = AppPaths(Path.cwd().resolve())
    try:
        paths.initialize()
    except (OSError, ExportError) as exc:
        if headless:
            print(str(exc), file=sys.stderr)
        else:
            show_message(None, "无法启动", str(exc))
        return 1
    lock = QLockFile(str(paths.data / "application.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        if headless:
            print("本工作目录已有工具实例正在运行", file=sys.stderr)
        else:
            show_message(None, APPLICATION_NAME, "本工作目录已有工具实例正在运行")
        return 1
    error = ""
    try:
        settings = load_settings(paths)
    except (OSError, ExportError) as exc:
        if headless:
            print(str(exc), file=sys.stderr)
            return 1
        settings, error = None, str(exc)
    if not headless:
        from .ui import MainWindow

        window = MainWindow(paths, settings, error)
        window.show()
        return qt.exec()
    configs = [resolve_path(p, paths.work) for p in (args.check or args.exports)]
    results = preflight_batch(configs, paths, settings)
    failed = False
    # All targets are known before exporting anything.
    if args.exports and not args.replace and any(r.plan and r.plan.existing for r in results):
        print("已有目标文件；使用 --replace 确认全部替换。", file=sys.stderr)
        return 1
    for result in results:
        if result.error:
            failed = True
            print(
                json.dumps({"config": str(result.path), "error": result.error}, ensure_ascii=False)
            )
            continue
        p = result.plan
        if p is None:
            continue
        try:
            if args.check:
                print(
                    json.dumps(
                        {
                            "config": str(result.path),
                            "dependencies": p.dependencies,
                            "files": len(p.files),
                            "bytes": p.total_bytes,
                            "warnings": p.warnings,
                            "existing": [str(x) for x in p.existing],
                            "selected": [f"{x.layer}/{x.destination}" for x in p.files],
                        },
                        ensure_ascii=False,
                    )
                )
            else:
                from datetime import datetime
                from uuid import uuid4

                name = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid4().hex[:8] + ".log"
                with (paths.logs / name).open("w", encoding="utf-8") as stream:
                    outputs = export_plan(
                        p, replace=args.replace, log=lambda line: stream.write(line + "\n")
                    )
                print(
                    json.dumps(
                        {"config": str(result.path), "outputs": [str(x) for x in outputs]},
                        ensure_ascii=False,
                    )
                )
        except KeyboardInterrupt:
            return 130
        except (ExportError, OSError) as exc:
            failed = True
            print(json.dumps({"config": str(result.path), "error": str(exc)}, ensure_ascii=False))
    return 1 if failed else 0
