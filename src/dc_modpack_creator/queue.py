from __future__ import annotations

import threading
import time
import traceback
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QThread, Signal

from .archive import check_engine
from .config import AppPaths, Settings, TaskConfig
from .errors import Cancelled
from .exporter import export_plan
from .metadata import resolve_dependencies
from .planner import Plan, preflight_batch

TERMINAL = {"success", "failed", "cancelled"}


@dataclass
class TaskRecord:
    path: Path
    status: str = "pending"
    stage: str = "等待预检"
    done: int = 0
    total: int = 0
    elapsed: float = 0
    error: str = ""
    outputs: list[Path] = field(default_factory=list)
    log_path: Path | None = None
    plan: Plan | None = None


class BatchWorker(QThread):
    changed = Signal(int, object)
    overwrite_requested = Signal(list)

    def __init__(self, paths: list[Path], app: AppPaths, settings: Settings, parent=None):
        super().__init__(parent)
        self.paths, self.app, self.settings = paths, app, settings
        self.records = [TaskRecord(p) for p in paths]
        self.cancel_events = [threading.Event() for _ in paths]
        self.answer_event = threading.Event()
        self.allow_replace = False
        self._streams = {}
        self._started_at = {}
        self._last_emit = {}

    def cancel(self, index: int) -> None:
        self.cancel_events[index].set()

    def cancel_all(self) -> None:
        for event in self.cancel_events:
            event.set()

    def answer_overwrite(self, accepted: bool) -> None:
        self.allow_replace = accepted
        self.answer_event.set()

    def check(self, index: int) -> None:
        if self.cancel_events[index].is_set():
            raise Cancelled("已取消")

    def publish(self, index: int) -> None:
        record = self.records[index]
        if index in self._started_at:
            record.elapsed = time.monotonic() - self._started_at[index]
        self.changed.emit(index, replace(record))

    def log(self, index: int, text: str) -> None:
        stream = self._streams.get(index)
        if stream:
            stream.write(datetime.now().strftime("%H:%M:%S") + "  " + text + "\n")
            stream.flush()

    def progress(self, index: int, stage: str, done: int, total: int) -> None:
        r = self.records[index]
        changed = r.stage != stage
        r.stage, r.done, r.total = stage, done, total
        now = time.monotonic()
        if changed or now - self._last_emit.get(index, 0) > 0.1 or done == total:
            if changed:
                self.log(index, stage)
            self._last_emit[index] = now
            self.publish(index)

    def run(self) -> None:
        try:
            for i, record in enumerate(self.records):
                filename = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid4().hex[:8] + ".log"
                record.log_path = self.app.logs / filename
                self._streams[i] = record.log_path.open("w", encoding="utf-8", buffering=1)
                self.log(i, f"任务：{record.path}\n工作目录：{self.app.work}")
                self.publish(i)
            check_engine()

            def precheck(index: int) -> None:
                for previous, record in enumerate(self.records):
                    if record.status == "checking":
                        record.status, record.stage = "checked", "预检完成"
                        self.publish(previous)
                r = self.records[index]
                r.status, r.stage = "checking", "正在预检"
                self.publish(index)

            results = preflight_batch(self.paths, self.app, self.settings, self.check, precheck)
            for i, result in enumerate(results):
                r = self.records[i]
                r.plan = result.plan
                if result.cancelled or self.cancel_events[i].is_set():
                    r.status, r.stage = "cancelled", "已取消"
                elif result.error:
                    r.status, r.stage, r.error = "failed", "预检失败", result.error
                    self.log(i, "预检失败：" + result.error)
                else:
                    r.status, r.stage = "waiting", "等待导出"
                    assert result.plan
                    self.log(i, "依赖：" + repr(result.plan.dependencies))
                    for warning in result.plan.warnings:
                        self.log(i, "警告：" + warning)
                    for line, text, hits in result.plan.rule_hits:
                        self.log(i, f"规则 {line} 命中 {hits}：{text}")
                    self.log(
                        i, f"最终文件：{len(result.plan.files)} 个，{result.plan.total_bytes} 字节"
                    )
                    for f in result.plan.files:
                        self.log(i, f"计划：{f.layer}/{f.destination} ← {f.source}")
                self.publish(i)
            existing = sorted(
                {
                    str(p)
                    for i, r in enumerate(self.records)
                    if r.status == "waiting" and r.plan and not self.cancel_events[i].is_set()
                    for p in r.plan.existing
                }
            )
            if existing:
                self.overwrite_requested.emit(existing)
                while not self.answer_event.wait(0.1):
                    if all(
                        e.is_set() or r.status in TERMINAL
                        for e, r in zip(self.cancel_events, self.records, strict=True)
                    ):
                        break
                if not self.allow_replace:
                    self.cancel_all()
            for i, r in enumerate(self.records):
                if r.status in TERMINAL:
                    continue
                if self.cancel_events[i].is_set():
                    r.status, r.stage = "cancelled", "已取消"
                    self.publish(i)
                    continue
                assert r.plan
                self._started_at[i] = time.monotonic()
                r.status, r.stage = "running", "准备文件"
                self.publish(i)
                try:
                    r.outputs = export_plan(
                        r.plan,
                        replace=self.allow_replace,
                        check=lambda n=i: self.check(n),
                        progress=lambda stage, a, b, n=i: self.progress(n, stage, a, b),
                        log=lambda text, n=i: self.log(n, text),
                    )
                    r.status, r.stage = "success", "已完成"
                except Cancelled:
                    r.status, r.stage = "cancelled", "已取消"
                    self.log(i, "任务已取消，后台作业已停止并完成清理")
                except Exception as exc:
                    r.status, r.stage, r.error = "failed", "导出失败", str(exc)
                    self.log(i, traceback.format_exc())
                r.done, r.total = 0, 0
                self.publish(i)
        except Exception as exc:
            for i, r in enumerate(self.records):
                if r.status not in TERMINAL:
                    r.status, r.stage, r.error = "failed", "无法导出", str(exc)
                    self.log(i, traceback.format_exc())
                    self.publish(i)
        finally:
            for stream in self._streams.values():
                stream.close()


class MetadataWorker(QThread):
    result = Signal(object, str)

    def __init__(self, config: TaskConfig, parent=None):
        super().__init__(parent)
        self.config = config

    def run(self) -> None:
        try:
            self.result.emit(resolve_dependencies(self.config.instance_dir), "")
        except Exception as exc:
            self.result.emit({}, str(exc))


class CheckWorker(QThread):
    result = Signal(object)

    def __init__(self, path: Path, app: AppPaths, settings: Settings, parent=None):
        super().__init__(parent)
        self.path, self.app, self.settings = path, app, settings
        self.cancelled = threading.Event()

    def run(self) -> None:
        def check(_: int) -> None:
            if self.cancelled.is_set():
                raise Cancelled("已取消")

        try:
            self.result.emit(preflight_batch([self.path], self.app, self.settings, check)[0])
        except Exception as exc:
            from .planner import PreflightResult

            self.result.emit(PreflightResult(self.path, error=str(exc)))
