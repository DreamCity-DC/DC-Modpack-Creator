from __future__ import annotations

import codecs
import hashlib
import locale
import os
import re
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QProcess, QProcessEnvironment

from .errors import ExportError

ENGINE_SHA256 = "574bb90d17732f3cc4145fd4ba12d8f29b9d63400881c0e6fe3110c88b0485de"
SPLITTER_SHA256 = "3c79453b5b4dffa656951179f00336dd3887169c075fc552e092aa4c76918ff1"


def engine_path() -> Path:
    return Path(__file__).parent / "vendor" / "7za.exe"


def check_engine() -> Path:
    return checked_binary(engine_path(), ENGINE_SHA256)


def check_splitter() -> Path:
    return checked_binary(Path(__file__).parent / "vendor" / "zip.exe", SPLITTER_SHA256)


def checked_binary(path: Path, expected: str) -> Path:
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise ExportError(f"缺少随程序携带的归档工具：{path}") from exc
    if digest != expected:
        raise ExportError(f"归档工具校验失败：{path}")
    return path


class ProgressParser:
    def __init__(self, progress: Callable[[int], None], log: Callable[[str], None]):
        self.progress, self.log = progress, log
        self.buffer = ""
        self.last_percent = -1

    def feed(self, text: str, final: bool = False) -> None:
        self.buffer += text.replace("\b", "\r")
        parts = re.split(r"[\r\n]", self.buffer)
        self.buffer = parts.pop()
        if final:
            parts.append(self.buffer)
            self.buffer = ""
        for part in parts:
            line = part.strip()
            if not line:
                continue
            found = re.search(r"(?<!\d)(100|[0-9]{1,2})%", line)
            if found:
                percent = int(found[1])
                if percent != self.last_percent:
                    self.progress(percent)
                    self.last_percent = percent
            else:
                self.log(line)


class SevenZip:
    """A synchronous adapter for background QThreads and the headless CLI."""

    def __init__(
        self,
        check: Callable[[], None],
        log: Callable[[str], None],
        progress: Callable[[int], None],
    ):
        self.executable = check_engine()
        self.check, self.log, self.progress = check, log, progress

    def add(
        self,
        target: Path,
        root: Path,
        names: list[str],
        level: int,
        threads: int,
        list_path: Path,
    ) -> None:
        self.check()
        if not names:
            raise ExportError("压缩清单为空")
        # Explicit names, UTF-8, wildcard expansion off; never pass names through a shell.
        list_path.write_text("\n".join(names) + "\n", encoding="utf-8")
        args = [
            "a",
            "-tzip",
            f"-mx={level}",
            f"-mm={'Copy' if level == 0 else 'Deflate'}",
            f"-mmt={threads or os.cpu_count() or 1}",
            "-bsp1",
            "-bso1",
            "-bse1",
            "-sccUTF-8",
            "-scsUTF-8",
            "-spd",
            "-w" + str(list_path.parent),
            "-y",
            str(target),
            "@" + str(list_path),
        ]
        self.run(args, root)

    def run(self, args: list[str], cwd: Path) -> None:
        run_process(self.executable, args, cwd, self.check, self.log, self.progress)


def run_process(
    executable: Path,
    args: list[str],
    cwd: Path,
    check: Callable[[], None],
    log: Callable[[str], None],
    progress: Callable[[int], None],
    *,
    splitting: bool = False,
    tick: Callable[[], None] = lambda: None,
) -> None:
    label = "分卷" if splitting else "压缩"
    log(label + "命令：" + str(executable) + " " + repr(args))
    process = QProcess()
    process.setProgram(str(executable))
    process.setArguments(args)
    process.setWorkingDirectory(str(cwd))
    process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
    env = QProcessEnvironment.systemEnvironment()
    # Info-ZIP reads these as extra command-line arguments. Do not inherit user options.
    if splitting:
        env.remove("ZIP")
        env.remove("ZIPOPT")
    process.setProcessEnvironment(env)
    encoding = locale.getpreferredencoding(False) if splitting else "utf-8"
    decoder = codecs.getincrementaldecoder(encoding)("replace")
    parser = ProgressParser(progress, log)

    def read() -> None:
        data = bytes(process.readAllStandardOutput())
        if data:
            parser.feed(decoder.decode(data))

    try:
        process.start()
        if not process.waitForStarted(10_000):
            raise ExportError(f"无法启动{label}进程：" + process.errorString())
        while process.state() != QProcess.ProcessState.NotRunning:
            check()
            process.waitForReadyRead(100)
            read()
            tick()
        read()
        parser.feed(decoder.decode(b"", final=True), final=True)
        check()
        code = process.exitCode()
        log(f"{label}进程退出码：{code}")
        if process.exitStatus() != QProcess.ExitStatus.NormalExit or code != 0:
            raise ExportError(f"{label}失败（退出码 {code}），请查看任务日志")
    except BaseException:
        if process.state() != QProcess.ProcessState.NotRunning:
            process.kill()
            process.waitForFinished(-1)
            read()
        raise
    finally:
        process.close()


class InfoZip:
    """Copy ZIP entries into native volumes without recompressing their payloads."""

    def __init__(
        self,
        check: Callable[[], None],
        log: Callable[[str], None],
        progress: Callable[[int, int], None],
    ):
        self.executable = check_splitter()
        self.check, self.log, self.progress = check, log, progress

    def split(
        self, source: Path, target: Path, volume_size: int, created: list[Path]
    ) -> list[Path]:
        # Info-ZIP 3.0 only accepts whole KB (1024 bytes). Never exceed the configured byte limit.
        limit = volume_size // 1024 * 1024
        size = source.stat().st_size
        if size <= limit:
            files = [source]
        else:
            self.log(f"原生分卷上限：{limit} 字节（配置 {volume_size} 字节）")

            def tick():
                done = 0
                for part in source.parent.glob("split.*"):
                    try:
                        done += part.stat().st_size
                    except FileNotFoundError:
                        # Info-ZIP renames the current .zip to .zNN while writing.
                        continue
                self.progress(min(done, size), size)

            # ASCII relative names avoid the legacy tool's command-line path encoding limits.
            run_process(
                self.executable,
                ["-s", f"{limit // 1024}k", source.name, "--out", "split.zip"],
                source.parent,
                self.check,
                self.log,
                lambda _: None,
                splitting=True,
                tick=tick,
            )
            volumes = sorted(source.parent.glob("split.z[0-9]*"), key=lambda p: int(p.suffix[2:]))
            files = [*volumes, source.parent / "split.zip"]
            if not files[-1].is_file() or any(p.stat().st_size > limit for p in files):
                raise ExportError("原生分卷文件组不完整或超过分卷大小上限")
        outputs = []
        for part in files:
            self.check()
            dest = target if part == source else target.with_suffix(part.suffix)
            # On Windows rename fails if dest exists, including files appearing after preflight.
            part.rename(dest)
            created.append(dest)
            outputs.append(dest)
        self.progress(size, size)
        return outputs
