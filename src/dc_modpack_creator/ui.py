from __future__ import annotations

import shutil
import time
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import (
    QCloseEvent,
    QKeySequence,
    QShortcut,
    QStandardItem,
    QStandardItemModel,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QListView,
    QMainWindow,
    QPlainTextEdit,
    QProgressBar,
    QSizePolicy,
    QStackedWidget,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from . import APPLICATION_NAME
from .config import AppPaths, Settings, create_example_task, load_settings, load_task, save_settings
from .dialogs import DetailDialog, LogDialog, SettingsDialog, result_dialog
from .errors import ExportError
from .queue import TERMINAL, BatchWorker, TaskRecord
from .ui_common import (
    STYLE,
    CheckBox,
    FramelessDialog,
    MessageDialog,
    WindowHeader,
    button,
    elapsed_text,
    label,
    open_path,
    open_text_file,
    show_message,
    size_text,
    window_surface,
)


@dataclass
class ConfigInfo:
    path: Path
    selected: bool = False
    subtitle: str = ""


class WidgetRowDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index) -> None:
        # Index widgets paint the complete row; native focus/hover panels are redundant.
        pass


class ConfigRow(QFrame):
    def __init__(self, info: ConfigInfo, window):
        super().__init__()
        self.info = info
        self.setObjectName("card")
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 10, 12, 10)
        row.setSpacing(12)
        self.checkbox = CheckBox()
        self.checkbox.setAccessibleName(info.path.name)
        self.checkbox.setChecked(info.selected)
        self.checkbox.toggled.connect(lambda value: window.select_config(info, value))
        row.addWidget(self.checkbox)
        texts = QVBoxLayout()
        texts.setSpacing(2)
        name = label(info.path.name, "name")
        name.setMinimumHeight(24)
        name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        name.setToolTip(info.path.name)
        texts.addWidget(name)
        subtitle = label(
            info.subtitle, "error" if info.subtitle.startswith("配置错误") else "muted"
        )
        subtitle.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        subtitle.setToolTip(info.subtitle)
        texts.addWidget(subtitle)
        row.addLayout(texts, 1)
        row.addWidget(button("详情", lambda: window.detail(info), "textButton"))


class TaskRow(QFrame):
    def __init__(self, index: int, record: TaskRecord, window):
        super().__init__()
        self.index, self.record, self.window = index, record, window
        self.received_at = time.monotonic()
        self.setObjectName("card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 9, 12, 10)
        layout.setSpacing(5)
        top = QHBoxLayout()
        top.setSpacing(4)
        self.name = label(record.path.name, "name")
        self.name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.name.setToolTip(record.path.name)
        top.addWidget(self.name, 1)
        self.open_button = button("打开目录", lambda: window.open_task_output(index), "textButton")
        self.log_button = button("日志", lambda: window.show_log(index), "textButton")
        self.details_button = button("详情", lambda: window.show_result(index), "textButton")
        self.cancel_button = button("取消", lambda: window.cancel_task(index), "textButton")
        for b in (self.open_button, self.log_button, self.details_button, self.cancel_button):
            top.addWidget(b)
        layout.addLayout(top)
        state_row = QHBoxLayout()
        self.stage = label("", "muted")
        self.stage.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.amount = label("", "muted")
        self.elapsed = label("", "muted")
        state_row.addWidget(self.stage, 1)
        state_row.addWidget(self.amount)
        state_row.addWidget(self.elapsed)
        layout.addLayout(state_row)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        layout.addWidget(self.progress)
        self.update_record(record)

    def update_record(self, record: TaskRecord) -> None:
        self.record = record
        self.received_at = time.monotonic()
        r = record
        terminal = r.status in TERMINAL
        active = r.status in {"checking", "running", "cancelling"}
        self.stage.setText(r.error.splitlines()[0] if r.error else r.stage)
        self.stage.setToolTip(r.error or r.stage)
        self.stage.setObjectName(
            "error" if r.status == "failed" else "success" if r.status == "success" else "muted"
        )
        self.stage.style().unpolish(self.stage)
        self.stage.style().polish(self.stage)
        self.open_button.setVisible(r.status == "success")
        self.details_button.setVisible(r.status in {"success", "failed"})
        self.log_button.setVisible(r.status not in {"success", "failed"})
        self.cancel_button.setVisible(not terminal)
        self.cancel_button.setEnabled(r.status != "cancelling")
        self.progress.setVisible(active)
        self.amount.setText("")
        if active:
            if r.total:
                self.progress.setRange(0, 1000)
                self.progress.setValue(min(1000, r.done * 1000 // r.total))
                percent = min(100, r.done * 100 // r.total)
                self.amount.setText(
                    f"{percent}%"
                    if r.total == 100
                    else f"{size_text(r.done)} / {size_text(r.total)} · {percent}%"
                )
            else:
                self.progress.setRange(0, 0)
        if r.status == "success" and r.outputs:
            name = r.outputs[0].name + (f" · {len(r.outputs)} 卷" if len(r.outputs) > 1 else "")
            self.stage.setText("已完成 · " + name)
            self.stage.setToolTip("\n".join(str(p) for p in r.outputs))
        if r.plan and r.plan.warnings and r.status not in {"failed", "cancelled", "cancelling"}:
            self.stage.setText(self.stage.text() + f" · {len(r.plan.warnings)} 项警告")
        self.tick()

    def tick(self) -> None:
        r = self.record
        seconds = r.elapsed + (
            time.monotonic() - self.received_at if r.status in {"running", "cancelling"} else 0
        )
        self.elapsed.setText(
            elapsed_text(seconds) if r.elapsed or r.status in {"running", "cancelling"} else ""
        )


class MainWindow(QMainWindow):
    def __init__(self, app: AppPaths, settings: Settings | None, config_error: str = ""):
        super().__init__()
        self.app, self.settings, self.config_error = app, settings or Settings(), config_error
        self.worker: BatchWorker | None = None
        self.records: list[TaskRecord] = []
        self.history: dict[Path, list[TaskRecord]] = {}
        self.configs: list[ConfigInfo] = []
        self.config_rows: list[ConfigRow] = []
        self.task_rows: list[TaskRow] = []
        self._closing = False
        self._selecting_all = False
        self.setWindowTitle(APPLICATION_NAME)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(900, 600)
        self.setStyleSheet(STYLE)
        central = QWidget()
        central.setObjectName("central")
        self.setCentralWidget(central)
        self.surface = window_surface(central)
        layout = QVBoxLayout(self.surface)
        layout.setContentsMargins(28, 16, 28, 22)
        layout.setSpacing(14)
        header = WindowHeader(self)
        header.actions.insertWidget(0, button("设置", self.show_settings, "topButton"))
        minimize = button("−", self.showMinimized, "topButton")
        minimize.setFixedWidth(34)
        minimize.setAccessibleName("最小化")
        header.actions.insertWidget(1, minimize)
        layout.addWidget(header)
        toolbar = QHBoxLayout()
        self.toolbar_title = label("选择导出配置：", "muted")
        toolbar.addWidget(self.toolbar_title)
        toolbar.addStretch(1)
        self.config_folder_button = button(
            "导出配置文件夹", lambda: open_path(self, self.app.configs), "topButton"
        )
        toolbar.addWidget(self.config_folder_button)
        self.refresh_button = button("刷新", self.refresh, "topButton")
        toolbar.addWidget(self.refresh_button)
        layout.addLayout(toolbar)
        self.stack = QStackedWidget()
        self.config_page = QStackedWidget()
        self.config_view = self.make_list()
        self.config_model = QStandardItemModel(self)
        self.config_view.setModel(self.config_model)
        self.config_page.addWidget(self.config_view)
        empty = QWidget()
        e = QVBoxLayout(empty)
        e.setSpacing(0)
        e.addStretch()
        for text, kind in (("还没有导出配置", "name"), ("将 TOML 放入“导出”文件夹", "muted")):
            value = label(text, kind)
            value.setAlignment(Qt.AlignmentFlag.AlignCenter)
            e.addWidget(value)
            e.addSpacing(8 if kind == "name" else 20)
        open_configs = button("打开配置文件夹", lambda: open_path(self, app.configs), "primary")
        open_configs.setFixedWidth(180)
        e.addWidget(
            open_configs,
            alignment=Qt.AlignmentFlag.AlignCenter,
        )
        e.addSpacing(12)
        generate_example = button("生成示例导出配置", self.generate_example_config, "secondary")
        generate_example.setFixedWidth(180)
        e.addWidget(
            generate_example,
            alignment=Qt.AlignmentFlag.AlignCenter,
        )
        e.addStretch()
        self.config_page.addWidget(empty)
        self.stack.addWidget(self.config_page)
        self.task_view = self.make_list()
        self.task_model = QStandardItemModel(self)
        self.task_view.setModel(self.task_model)
        self.stack.addWidget(self.task_view)
        layout.addWidget(self.stack, 1)
        self.notice = label("", "error", True)
        self.notice.hide()
        layout.addWidget(self.notice)
        footer = QHBoxLayout()
        footer.setSpacing(12)
        self.all_checkbox = CheckBox("全选")
        self.all_checkbox.setTristate(True)
        self.all_checkbox.clicked.connect(self.select_all)
        footer.addWidget(self.all_checkbox)
        self.status = label("", "muted")
        self.status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        footer.addWidget(self.status, 1)
        self.repair_button = button("修复", self.repair_settings)
        self.repair_button.hide()
        footer.addWidget(self.repair_button)
        self.back_button = button("返回配置", self.back_to_configs)
        self.back_button.hide()
        footer.addWidget(self.back_button)
        self.action = button("开始导出", self.primary_action, "primary")
        footer.addWidget(self.action)
        layout.addLayout(footer)
        self.refresh_shortcut = QShortcut(QKeySequence("F5"), self)
        self.refresh_shortcut.activated.connect(self.refresh)
        self.tick_timer = QTimer(self)
        self.tick_timer.timeout.connect(self.tick)
        self.tick_timer.start(1000)
        self.restore_position()
        self.refresh()

    def make_list(self) -> QListView:
        view = QListView()
        view.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        view.setItemDelegate(WidgetRowDelegate(view))
        view.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        view.setSpacing(5)
        view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        view.setUniformItemSizes(True)
        return view

    @property
    def busy(self) -> bool:
        return bool(self.worker and self.worker.isRunning())

    def restore_position(self) -> None:
        w = self.settings.window
        if "x" in w and "y" in w:
            self.move(w["x"], w["y"])
            if not any(
                s.availableGeometry().intersects(self.frameGeometry())
                for s in QApplication.screens()
            ):
                screen = QApplication.primaryScreen().availableGeometry()
                self.move(screen.center() - self.rect().center())

    def refresh(self) -> None:
        if self.busy or self.stack.currentIndex() != 0:
            return
        try:
            self.settings = load_settings(self.app)
            self.config_error = ""
        except (ExportError, OSError) as exc:
            self.config_error = str(exc)
        self.config_model.clear()
        self.config_rows.clear()
        self.configs.clear()
        selected = {
            memory.replace("\\", "/").casefold() for memory in self.settings.selected_configs
        }
        discovery_error = ""
        try:
            found = self.app.discover()
        except OSError as exc:
            discovery_error = str(exc)
            found = []
        known = set()
        for path in found:
            rel = path.relative_to(self.app.work).as_posix()
            known.add(rel.casefold())
            try:
                c = load_task(path, self.app, self.settings)
                sub = c.version + " · " + ("完整包 ZIP" if c.full_package else "mrpack包")
            except (ExportError, OSError) as exc:
                sub = "配置错误 · " + str(exc).splitlines()[0]
            self.configs.append(ConfigInfo(path, rel.casefold() in selected, sub))
        for info in self.configs:
            item = QStandardItem()
            item.setSizeHint(QSize(1, 74))
            self.config_model.appendRow(item)
            row = ConfigRow(info, self)
            self.config_rows.append(row)
            self.config_view.setIndexWidget(item.index(), row)
        self.config_page.setCurrentIndex(0 if self.configs else 1)
        self.toolbar_title.setVisible(bool(self.configs))
        self.notice.setVisible(bool(self.config_error or discovery_error))
        if discovery_error:
            self.notice.setText(discovery_error)
        if self.config_error:
            self.notice.setText("工具设置无法读取")
            self.notice.setToolTip(self.config_error)
        self.repair_button.setVisible(bool(self.config_error))
        # Only a successful directory scan can establish that remembered files are gone.
        if not discovery_error and selected - known:
            self.remember_selection()
        self.update_selection()

    def generate_example_config(self) -> None:
        try:
            path = create_example_task(self.app)
        except OSError as exc:
            self.show_error("无法生成示例导出配置", str(exc))
            return
        self.refresh()
        open_text_file(self, path)

    def select_config(self, info: ConfigInfo, checked: bool) -> None:
        info.selected = checked
        if not self._selecting_all:
            self.remember_selection()
            self.update_selection()

    def remember_selection(self) -> None:
        if self.config_error:
            return
        try:
            self.settings = replace(
                load_settings(self.app),
                selected_configs=tuple(
                    i.path.relative_to(self.app.work).as_posix() for i in self.configs if i.selected
                ),
            )
            save_settings(self.app, self.settings)
        except (OSError, ExportError) as exc:
            self.notice.setText("无法保存勾选状态：" + str(exc))
            self.notice.show()

    def update_selection(self) -> None:
        count = sum(i.selected for i in self.configs)
        state = (
            Qt.CheckState.Checked
            if count and count == len(self.configs)
            else Qt.CheckState.PartiallyChecked
            if count
            else Qt.CheckState.Unchecked
        )
        self.all_checkbox.setCheckState(state)
        self.all_checkbox.setEnabled(bool(self.configs))
        self.status.setText(f"已选择 {count} 项" if count else "请选择配置")
        self.action.setEnabled(bool(count) and not self.config_error)

    def select_all(self) -> None:
        checked = not all(i.selected for i in self.configs)
        self._selecting_all = True
        for row in self.config_rows:
            row.checkbox.setChecked(checked)
        self._selecting_all = False
        self.remember_selection()
        self.update_selection()

    def detail(self, info: ConfigInfo) -> None:
        DetailDialog(info.path, self.app, self.settings, self).exec()

    def show_settings(self) -> None:
        if self.config_error:
            self.show_error("工具设置无法读取", self.config_error)
            return
        try:
            settings = load_settings(self.app)
        except (ExportError, OSError) as exc:
            self.config_error = str(exc)
            self.notice.setText("工具设置无法读取")
            self.notice.show()
            self.repair_button.show()
            self.action.setEnabled(self.busy)
            return
        dialog = SettingsDialog(self.app, settings, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.settings = dialog.settings
            self.notice.setObjectName("muted")
            self.notice.setText("已保存")
            self.notice.show()
            if not self.busy and self.stack.currentIndex() == 0:
                self.refresh()
                self.notice.setText("已保存")
                self.notice.show()

    def repair_settings(self) -> None:
        try:
            if self.app.settings.exists():
                backup = self.app.settings.with_name(
                    "config-broken-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".toml"
                )
                shutil.copy2(self.app.settings, backup)
            save_settings(self.app, Settings())
            self.config_error = ""
            self.refresh()
        except (ExportError, OSError) as exc:
            self.show_error("无法修复设置", str(exc))

    def primary_action(self) -> None:
        if self.busy:
            self.cancel_all()
        elif self.stack.currentIndex() == 0:
            self.start_batch([i.path for i in self.configs if i.selected])
        elif any(r.status == "failed" for r in self.records):
            self.start_batch([r.path for r in self.records if r.status == "failed"])
        else:
            self.open_outputs()

    def start_batch(self, paths: list[Path]) -> None:
        if not paths or self.busy:
            return
        try:
            settings = load_settings(self.app)
        except (ExportError, OSError) as exc:
            self.show_error("工具设置无法读取", str(exc))
            return
        for record in self.records:
            self.history.setdefault(record.path, []).append(record)
        self.records = [TaskRecord(p) for p in paths]
        self.task_model.clear()
        self.task_rows.clear()
        for i, record in enumerate(self.records):
            item = QStandardItem()
            item.setSizeHint(QSize(1, 100))
            self.task_model.appendRow(item)
            row = TaskRow(i, record, self)
            self.task_rows.append(row)
            self.task_view.setIndexWidget(item.index(), row)
        self.stack.setCurrentIndex(1)
        self.toolbar_title.setText("导出任务")
        self.toolbar_title.show()
        self.refresh_button.hide()
        self.config_folder_button.hide()
        self.all_checkbox.hide()
        self.back_button.hide()
        self.notice.hide()
        if self.worker:
            self.worker.deleteLater()
        self.worker = BatchWorker(paths, self.app, settings, self)
        self.worker.changed.connect(self.task_changed)
        self.worker.overwrite_requested.connect(self.confirm_overwrite)
        self.worker.finished.connect(self.batch_finished)
        self.action.setText("取消全部")
        self.action.setEnabled(True)
        self.worker.start()
        self.update_batch_status()

    def confirm_overwrite(self, files: list[str]) -> None:
        if self._closing:
            self.worker.answer_overwrite(False)
            return
        dialog = FramelessDialog("替换已有文件？", self, resizable=True)
        dialog.resize(620, 370)
        layout = dialog.body
        text = QPlainTextEdit()
        text.setReadOnly(True)
        text.setPlainText("\n".join(files))
        layout.addWidget(text, 1)
        row = QHBoxLayout()
        row.addStretch()
        cancel = button("取消", dialog.reject)
        cancel.setDefault(True)
        row.addWidget(cancel)
        replace_all = button("全部替换", dialog.accept, "primary")
        replace_all.setAutoDefault(False)
        row.addWidget(replace_all)
        layout.addLayout(row)
        self.worker.answer_overwrite(dialog.exec() == QDialog.DialogCode.Accepted)

    def task_changed(self, index: int, record: TaskRecord) -> None:
        if (
            self.worker
            and self.worker.cancel_events[index].is_set()
            and record.status not in TERMINAL
        ):
            active = record.status in {"checking", "running"}
            record = replace(
                record,
                status="cancelling" if active else "cancelled",
                stage="取消中" if active else "已取消",
                done=0,
                total=0,
            )
        self.records[index] = record
        self.task_rows[index].update_record(record)
        self.update_batch_status()

    def update_batch_status(self) -> None:
        if self.records and all(r.status in TERMINAL for r in self.records):
            self.status.setText(
                f"成功 {sum(r.status == 'success' for r in self.records)} · 失败 {sum(r.status == 'failed' for r in self.records)} · 取消 {sum(r.status == 'cancelled' for r in self.records)}"
            )
            return
        checking = any(r.status == "checking" for r in self.records)
        running = any(r.status in {"running", "cancelling"} for r in self.records)
        waiting = sum(r.status in {"pending", "checked", "waiting"} for r in self.records)
        if checking or running:
            text = "正在预检 1 项" if checking else "正在导出 1 项"
            if waiting:
                text += f" · 等待 {waiting} 项"
        elif self.busy:
            text = (
                "等待确认替换" if any(r.status == "waiting" for r in self.records) else "正在预检"
            )
        else:
            text = f"成功 {sum(r.status == 'success' for r in self.records)} · 失败 {sum(r.status == 'failed' for r in self.records)} · 取消 {sum(r.status == 'cancelled' for r in self.records)}"
        self.status.setText(text)

    def cancel_task(self, index: int) -> None:
        if not self.busy or self.records[index].status in TERMINAL:
            return
        self.worker.cancel(index)
        r = self.records[index]
        # Only local UI changes; worker publishes the terminal status after cleanup.
        status = "cancelling" if r.status in {"checking", "running"} else "cancelled"
        local = replace(
            r,
            status=status,
            stage="取消中" if status == "cancelling" else "已取消",
            done=0,
            total=0,
        )
        self.records[index] = local
        self.task_rows[index].update_record(local)
        self.update_batch_status()

    def cancel_all(self) -> None:
        if self.worker:
            self.worker.cancel_all()
        for i in range(len(self.records)):
            self.cancel_task(i)
        self.action.setEnabled(False)

    def batch_finished(self) -> None:
        self.toolbar_title.setText("导出结果")
        self.update_batch_status()
        self.back_button.show()
        failed = any(r.status == "failed" for r in self.records)
        success = any(r.status == "success" for r in self.records)
        self.action.setText("重试失败项" if failed else "打开成品目录")
        self.action.setEnabled(failed or success)
        if self._closing:
            self.close()

    def back_to_configs(self) -> None:
        if self.busy:
            return
        self.stack.setCurrentIndex(0)
        self.toolbar_title.setText("选择导出配置：")
        self.refresh_button.show()
        self.config_folder_button.show()
        self.all_checkbox.show()
        self.back_button.hide()
        self.action.setText("开始导出")
        self.refresh()

    def open_task_output(self, index: int) -> None:
        record = self.records[index]
        if record.outputs:
            open_path(self, record.outputs[0].parent)

    def open_outputs(self) -> None:
        parents = {r.outputs[0].parent for r in self.records if r.outputs}
        for parent in sorted(parents):
            open_path(self, parent)

    def show_log(self, index: int) -> None:
        LogDialog(self.records[index], self, provider=lambda: self.records[index]).exec()

    def show_result(self, index: int) -> None:
        r = self.records[index]
        result_dialog(r, self.history.get(r.path, []), self)

    def show_error(self, title: str, text: str) -> None:
        show_message(self, title, text)

    def tick(self) -> None:
        if self.stack.currentIndex() == 1:
            for row in self.task_rows:
                row.tick()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.busy:
            if not self._closing:
                box = MessageDialog(
                    "退出",
                    "取消任务并退出？",
                    self,
                    accept_text="取消任务并退出",
                    reject_text="返回",
                )
                if box.exec() != QDialog.DialogCode.Accepted:
                    event.ignore()
                    return
                self._closing = True
                self.cancel_all()
                self.worker.answer_overwrite(False)
            event.ignore()
            return
        if not self.config_error:
            try:
                latest = load_settings(self.app)
                save_settings(self.app, replace(latest, window={"x": self.x(), "y": self.y()}))
            except (OSError, ExportError):
                pass
        event.accept()
