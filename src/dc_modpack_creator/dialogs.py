from __future__ import annotations

from dataclasses import replace
from decimal import Decimal, InvalidOperation
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QScrollArea,
    QSizeGrip,
    QSizePolicy,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .config import (
    LEVELS,
    AppPaths,
    Settings,
    TaskConfig,
    load_settings,
    load_task,
    save_settings,
    validate_compression,
)
from .errors import ExportError
from .planner import PERSONAL_DIRS, PERSONAL_FILES, PreflightResult
from .queue import CheckWorker, MetadataWorker, TaskRecord
from .ui_common import (
    CheckBox,
    DirectoryDialog,
    FramelessDialog,
    button,
    label,
    open_path,
    open_text_file,
    size_text,
)


class SettingsDialog(FramelessDialog):
    def __init__(self, app: AppPaths, settings: Settings, parent=None):
        super().__init__("设置", parent)
        self.app, self.settings = app, settings
        self.setMinimumWidth(590)
        layout = self.body
        form = QFormLayout()
        form.setSpacing(12)
        self.level = QComboBox()
        for n, text in zip(
            LEVELS,
            ("0 · Store", "1 · 快速", "3 · 较快", "5 · 标准", "7 · 高压缩", "9 · 极限"),
            strict=True,
        ):
            self.level.addItem(text, n)
        self.level.setCurrentIndex(LEVELS.index(settings.level))
        form.addRow("压缩等级", self.level)
        self.volume = QComboBox()
        for text, n in (
            ("关闭", 0),
            ("1 GB", 1_073_741_824),
            ("2 GB", 2_147_483_648),
            ("4 GB", 4_294_967_296),
            ("自定义", -1),
        ):
            self.volume.addItem(text, n)
        index = self.volume.findData(settings.volume_size)
        self.volume.setCurrentIndex(index if index >= 0 else 4)
        form.addRow("完整包分卷", self.volume)
        self.custom = QWidget()
        row = QHBoxLayout(self.custom)
        row.setContentsMargins(0, 0, 0, 0)
        self.amount = QLineEdit(
            str(Decimal(settings.volume_size or 1_073_741_824) / Decimal(1_048_576))
        )
        self.unit = QComboBox()
        for unit, factor in (
            ("MB", 1_048_576),
            ("GB", 1_073_741_824),
        ):
            self.unit.addItem(unit, factor)
        row.addWidget(self.amount, 1)
        row.addWidget(self.unit)
        form.addRow("", self.custom)
        self.bytes_hint = label("", "muted", True)
        form.addRow("", self.bytes_hint)
        self.custom.setVisible(self.volume.currentData() == -1)
        layout.addLayout(form)
        self.advanced = button("高级设置", kind="textButton")
        self.advanced.setCheckable(True)
        layout.addWidget(self.advanced, alignment=Qt.AlignmentFlag.AlignLeft)
        self.advanced_panel = QWidget()
        advanced = QFormLayout(self.advanced_panel)
        advanced.setContentsMargins(0, 0, 0, 0)
        advanced.setSpacing(12)
        self.threads = QSpinBox()
        self.threads.setRange(0, 2_147_483_647)
        self.threads.setValue(settings.threads)
        self.threads.setToolTip("0 为自动")
        advanced.addRow("压缩线程数", self.threads)
        advanced.addRow("", label("0 为自动", "muted"))
        self.temp = QLineEdit(settings.temporary_directory)
        self.temp.setPlaceholderText("系统临时目录")
        advanced.addRow("临时目录", self.path_row(self.temp))
        log_row = QVBoxLayout()
        log_path = label(str(app.logs), "muted", True)
        log_path.setMaximumWidth(440)
        log_path.setMinimumHeight(max(36, log_path.heightForWidth(440)))
        log_row.addWidget(log_path)
        log_row.addWidget(
            button("打开日志目录", lambda: open_path(self, app.logs), "textButton"),
            alignment=Qt.AlignmentFlag.AlignLeft,
        )
        advanced.addRow("日志目录", log_row)
        self.advanced_panel.hide()
        layout.addWidget(self.advanced_panel)
        self.error = label("", "error", True)
        self.error.hide()
        layout.addWidget(self.error)
        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(button("取消", self.reject))
        buttons.addWidget(button("保存", self.save, "primary"))
        layout.addLayout(buttons)
        self.advanced.toggled.connect(self.toggle_advanced)
        self.volume.currentIndexChanged.connect(self.update_volume)
        self.unit.currentIndexChanged.connect(self.update_volume)
        self.amount.textChanged.connect(self.update_volume)
        self.update_volume()

    def path_row(self, edit: QLineEdit) -> QWidget:
        widget = QWidget()
        row = QHBoxLayout(widget)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(edit, 1)

        def choose():
            dialog = DirectoryDialog(self.app.work, self)
            if dialog.exec() == dialog.DialogCode.Accepted:
                selected = dialog.picker.selectedFiles()
                if selected:
                    edit.setText(selected[0])

        row.addWidget(button("浏览", choose))
        return widget

    def toggle_advanced(self, checked: bool) -> None:
        self.advanced_panel.setVisible(checked)
        self.advanced.setText("收起高级设置" if checked else "高级设置")
        # Let layout requests propagate through the surface before resizing;
        # otherwise the outer layout still enforces the expanded minimum height.
        QTimer.singleShot(0, self.adjustSize)

    def volume_bytes(self) -> int:
        preset = self.volume.currentData()
        if preset != -1:
            return preset
        try:
            n = Decimal(self.amount.text().strip()) * self.unit.currentData()
            if not n.is_finite() or n != n.to_integral_value():
                raise ValueError()
            return int(n)
        except (InvalidOperation, ValueError, OverflowError) as exc:
            raise ExportError("分卷大小必须换算为整数字节") from exc

    def update_volume(self) -> None:
        self.custom.setVisible(self.volume.currentData() == -1)
        try:
            value = self.volume_bytes()
            validate_compression(self.level.currentData(), value, self.threads.value())
            self.bytes_hint.setText(f"{value:,} 字节" if value else "")
            if value and value % 1024:
                self.bytes_hint.setText(
                    f"{value:,} 字节；实际分卷上限 {value // 1024 * 1024:,} 字节"
                )
            self.bytes_hint.setObjectName("muted")
        except ExportError as exc:
            self.bytes_hint.setText(str(exc))
            self.bytes_hint.setObjectName("error")
        self.bytes_hint.style().unpolish(self.bytes_hint)
        self.bytes_hint.style().polish(self.bytes_hint)
        QTimer.singleShot(0, self.adjustSize)

    def save(self) -> None:
        try:
            volume = self.volume_bytes()
            validate_compression(self.level.currentData(), volume, self.threads.value())
            # Preserve the latest remembered selection/window state.
            latest = load_settings(self.app)
            value = replace(
                latest,
                level=self.level.currentData(),
                volume_size=volume,
                threads=self.threads.value(),
                temporary_directory=self.temp.text().strip(),
            )
            save_settings(self.app, value)
            self.settings = value
            self.accept()
        except (ExportError, OSError) as exc:
            self.error.setText(str(exc))
            self.error.show()
            QTimer.singleShot(0, self.adjustSize)


class DetailSection(QFrame):
    def __init__(self, title: str):
        super().__init__()
        self.setObjectName("detailSection")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.toggle = QToolButton()
        self.toggle.setObjectName("detailToggle")
        self.toggle.setText(title)
        self.toggle.setCheckable(True)
        self.toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.content = QWidget()
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(16, 0, 0, 16)
        self.content_layout.setSpacing(12)
        layout.addWidget(self.toggle)
        layout.addWidget(self.content)
        self.toggle.toggled.connect(self.set_expanded)
        self.set_expanded(False)

    def set_expanded(self, expanded: bool) -> None:
        self.toggle.setChecked(expanded)
        self.toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        self.content.setVisible(expanded)


class DetailDialog(FramelessDialog):
    def __init__(self, path: Path, app: AppPaths, settings: Settings, parent=None):
        super().__init__(path.name, parent)
        self.resize_grip = QSizeGrip(self.surface)
        self.resize_grip.setFixedSize(14, 14)
        self.path, self.app, self.settings = path, app, settings
        self.worker: CheckWorker | None = None
        self.metadata_worker: MetadataWorker | None = None
        self.config = None
        self.checked_stamp = None
        self.plan = None
        self.resize(660, 470)
        self.setMinimumSize(580, 450)
        self.header.title.setObjectName("detailTitle")
        layout = self.body
        layout.setSpacing(18)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        content.setObjectName("listContent")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 10, 4, 0)
        content_layout.setSpacing(0)
        content_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll.setWidget(content)
        layout.addWidget(self.scroll, 1)
        summary = QGridLayout()
        summary.setContentsMargins(0, 0, 0, 20)
        summary.setHorizontalSpacing(16)
        summary.setVerticalSpacing(14)
        summary.setColumnMinimumWidth(0, 72)
        summary.setColumnStretch(1, 1)
        self.summary_values = {}
        for row, (key, title) in enumerate(
            (
                ("pack", "整合包"),
                ("product", "产物"),
                ("minecraft", "Minecraft"),
                ("source", "来源"),
                ("output", "输出"),
            )
        ):
            heading = label(title, "detailKey")
            value = label("", "detailValue", wrap=True)
            value.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            summary.addWidget(heading, row, 0, Qt.AlignmentFlag.AlignTop)
            summary.addWidget(value, row, 1)
            self.summary_values[key] = value
        content_layout.addLayout(summary)
        self.config_error = label("", "error", wrap=True)
        self.config_error.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.config_error.setContentsMargins(0, 0, 0, 16)
        self.config_error.hide()
        content_layout.addWidget(self.config_error)
        try:
            c = load_task(path, app, settings)
            self.config = c
            self.update_summary(c)
            self.summary_values["minecraft"].setText("正在识别…")
            rule_text = (
                "游戏规则\n"
                + c.rules
                + "\n\n额外映射\n"
                + (
                    "\n".join(f"{self.display_path(e.source)} → {e.destination}" for e in c.extras)
                    or "无"
                )
            )
            if c.full_package:
                rule_text += (
                    "\n\n自动 PCL 内容\n实例 PCL → overrides/PCL\n启动器 → 启动Minecraft.exe\n"
                    + "、".join((*PERSONAL_DIRS, *PERSONAL_FILES))
                    + " → PCL/"
                )
        except (ExportError, OSError) as exc:
            for value in self.summary_values.values():
                value.setText("—")
            self.config_error.setText(str(exc))
            self.config_error.show()
            rule_text = "无法解析配置"
        self.rules_section = DetailSection("文件与规则")
        content_layout.addWidget(self.rules_section)
        self.rules = QPlainTextEdit()
        self.rules.setObjectName("detailText")
        self.rules.setReadOnly(True)
        self.rules.setPlainText(rule_text)
        self.rules.setFixedHeight(180)
        self.rules_section.content_layout.addWidget(self.rules)
        self.check_section = DetailSection("预检结果")
        content_layout.addWidget(self.check_section)
        self.check_state = label("尚未检查", "muted", True)
        self.check_state.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.check_section.content_layout.addWidget(self.check_state)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        self.check_section.content_layout.addWidget(self.progress)
        self.results = QPlainTextEdit()
        self.results.setObjectName("detailText")
        self.results.setReadOnly(True)
        self.results.setFixedHeight(150)
        self.results.hide()
        self.check_section.content_layout.addWidget(self.results)
        self.game_files_section = DetailSection("最终文件清单（游戏实例）")
        self.external_files_section = DetailSection("最终文件清单（外部文件）")
        self.game_files = QPlainTextEdit()
        self.external_files = QPlainTextEdit()
        for section, files in (
            (self.game_files_section, self.game_files),
            (self.external_files_section, self.external_files),
        ):
            section.hide()
            content_layout.addWidget(section)
            files.setObjectName("detailText")
            files.setReadOnly(True)
            files.setFixedHeight(180)
            section.content_layout.addWidget(files)
        bottom = QHBoxLayout()
        self.edit_button = button("编辑配置", lambda: open_text_file(self, path), "secondary")
        self.edit_button.setMinimumWidth(118)
        bottom.addWidget(self.edit_button)
        bottom.addStretch()
        self.check_button = button("检查配置", self.check_config, "primary")
        self.check_button.setMinimumWidth(118)
        bottom.addWidget(self.check_button)
        layout.addLayout(bottom)
        self.watcher = QFileSystemWatcher([str(path)], self)
        self.watcher.fileChanged.connect(self.file_changed)
        if self.config:
            self.metadata_worker = MetadataWorker(self.config, self)
            self.metadata_worker.result.connect(self.metadata_ready)
            self.metadata_worker.start()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.resize_grip.move(self.surface.width() - 22, self.surface.height() - 22)

    def display_path(self, path: Path) -> str:
        try:
            return path.relative_to(self.app.work).as_posix()
        except ValueError:
            return str(path)

    def update_summary(self, config: TaskConfig, dependencies: dict | None = None) -> None:
        product = "mrpack包"
        if config.full_package:
            product = "完整包 ZIP · " + (
                f"分卷 {size_text(config.volume_size)}" if config.volume_size else "不分卷"
            )
        for key, text in (
            ("pack", f"{config.name} · {config.version}"),
            ("product", product),
            ("source", self.display_path(config.instance_dir)),
            ("output", self.display_path(config.target)),
        ):
            self.summary_values[key].setText(text)
        self.summary_values["source"].setToolTip(str(config.instance_dir))
        self.summary_values["output"].setToolTip(str(config.target))
        if dependencies is not None:
            names = {
                "minecraft": "",
                "fabric-loader": "Fabric",
                "forge": "Forge",
                "neoforge": "NeoForge",
            }
            self.summary_values["minecraft"].setText(
                " · ".join(
                    f"{names.get(key, key)} {version}".strip()
                    for key, version in dependencies.items()
                )
            )

    def metadata_ready(self, dependencies: dict, error: str) -> None:
        if self.plan or not self.config:
            return
        self.update_summary(self.config, dependencies)
        if error:
            self.summary_values["minecraft"].setText(error)

    def stamp(self):
        try:
            s = self.path.stat()
            return s.st_size, s.st_mtime_ns
        except OSError:
            return None

    def file_changed(self) -> None:
        self.check_state.setText("需要重新检查")
        self.reveal_check()
        if self.path.exists() and str(self.path) not in self.watcher.files():
            self.watcher.addPath(str(self.path))

    def reveal_check(self) -> None:
        self.check_section.set_expanded(True)
        # Expansion and result updates change the scroll content's layout first.
        QTimer.singleShot(0, self.scroll_to_check)

    def scroll_to_check(self) -> None:
        self.scroll.widget().layout().activate()
        self.scroll.ensureWidgetVisible(self.check_section, 0, 0)

    def check_config(self) -> None:
        if self.worker and self.worker.isRunning():
            return
        self.reveal_check()
        try:
            settings = load_settings(self.app)
        except (ExportError, OSError) as exc:
            self.check_state.setText(str(exc))
            return
        self.checked_stamp = self.stamp()
        self.plan = None
        self.results.clear()
        self.results.hide()
        self.game_files.clear()
        self.external_files.clear()
        self.game_files_section.set_expanded(False)
        self.external_files_section.set_expanded(False)
        self.game_files_section.hide()
        self.external_files_section.hide()
        self.check_state.setText("正在检查")
        self.check_button.setEnabled(False)
        self.progress.show()
        if self.worker:
            self.worker.deleteLater()
        self.worker = CheckWorker(self.path, self.app, settings, self)
        self.worker.result.connect(self.checked)
        self.worker.finished.connect(lambda: self.check_button.setEnabled(True))
        self.worker.start()

    def checked(self, result: PreflightResult) -> None:
        self.progress.hide()
        self.plan = result.plan
        self.results.setVisible(bool(result.error or result.plan))
        self.game_files_section.set_expanded(False)
        self.external_files_section.set_expanded(False)
        self.game_files_section.setVisible(bool(result.plan))
        self.external_files_section.hide()
        if result.cancelled:
            self.check_state.setText("已取消")
        elif result.error:
            self.check_state.setText("预检失败")
            self.results.setPlainText(result.error)
        elif result.plan:
            p = result.plan
            self.config = p.config
            self.config_error.hide()
            self.update_summary(p.config, p.dependencies)
            self.check_state.setText(
                f"{len(p.files)} 个文件 · {size_text(p.total_bytes)} · {len(p.warnings)} 项警告"
            )
            self.render_result()
        if self.stamp() != self.checked_stamp:
            self.check_state.setText("需要重新检查")
        self.reveal_check()

    def render_result(self) -> None:
        if not self.plan:
            return
        p = self.plan
        text = "\n".join(p.warnings) if p.warnings else "预检通过"
        text += "\n\n规则命中统计\n" + "\n".join(
            f"第 {line} 行 · {hits} 个命中 · {rule}" for line, rule, hits in p.rule_hits
        )
        if p.existing:
            text += "\n\n已有目标文件\n" + "\n".join(str(x) for x in p.existing)
        self.results.setPlainText(text)
        game_files, external_files = [], []
        for f in p.files:
            if f.layer == "inner" and f.destination.startswith("overrides/"):
                game_files.append(
                    f"{f.destination.removeprefix('overrides/')}  ({size_text(f.size)})"
                )
            else:
                external_files.append(f"{f.destination}  ({size_text(f.size)})")
        self.game_files.setPlainText("\n".join(game_files))
        self.external_files.setPlainText("\n".join(external_files))
        self.external_files_section.setVisible(bool(external_files))

    def done(self, result: int) -> None:
        if self.metadata_worker and self.metadata_worker.isRunning():
            self.metadata_worker.wait()
        if self.worker and self.worker.isRunning():
            self.worker.cancelled.set()
            self.worker.wait()
        super().done(result)


class LogDialog(FramelessDialog):
    def __init__(self, record: TaskRecord, parent=None, provider=None):
        super().__init__(record.path.name + " · 日志", parent, resizable=True)
        self.record = record
        self.provider = provider
        self.resize(740, 520)
        layout = self.body
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        layout.addWidget(self.text, 1)
        row = QHBoxLayout()
        self.auto_scroll = CheckBox("自动滚动")
        self.auto_scroll.setChecked(True)
        row.addWidget(self.auto_scroll)
        row.addStretch()
        row.addWidget(
            button("复制", lambda: QApplication.clipboard().setText(self.text.toPlainText()))
        )
        row.addWidget(button("打开完整日志", self.open_log))
        layout.addLayout(row)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(400)
        self.last_text = ""
        self.refresh()

    def refresh(self) -> None:
        if self.provider:
            self.record = self.provider()
        try:
            if self.record.log_path:
                with self.record.log_path.open("rb") as f:
                    f.seek(0, 2)
                    length = f.tell()
                    f.seek(max(0, length - 128_000))
                    value = f.read().decode("utf-8", errors="replace")
                if length > 128_000:
                    value = "（仅显示最近日志，完整内容可打开日志文件）\n" + value
            else:
                value = self.record.error or "等待任务开始"
        except OSError as exc:
            value = str(exc)
        if value != self.last_text:
            bar = self.text.verticalScrollBar()
            position = bar.value()
            self.text.setPlainText(value)
            bar.setValue(bar.maximum() if self.auto_scroll.isChecked() else position)
            self.last_text = value

    def open_log(self) -> None:
        if self.record.log_path:
            open_path(self, self.record.log_path)


def result_dialog(record: TaskRecord, history: list[TaskRecord], parent=None) -> None:
    dialog = FramelessDialog(record.path.name + " · 详情", parent, resizable=True)
    dialog.resize(650, 480)
    layout = dialog.body
    text = QPlainTextEdit()
    text.setReadOnly(True)
    parts = []
    for i, r in enumerate([record, *reversed(history)]):
        lines = ["本次记录" if i == 0 else f"历史记录 {i}", r.path.name, r.stage]
        if r.error:
            lines.append(r.error)
        if r.outputs:
            lines.append("成品文件组：")
            lines.extend(str(p) for p in r.outputs)
            if len(r.outputs) > 1:
                lines.append("所有分卷放在同一目录，使用第三方解压软件从 .zip 打开。")
        if r.plan:
            lines.extend(r.plan.warnings)
        if r.log_path:
            lines.append("日志：" + str(r.log_path))
        parts.append("\n".join(lines))
    text.setPlainText("\n\n".join(parts))
    layout.addWidget(text, 1)
    layout.addWidget(
        button("日志", lambda: LogDialog(record, dialog).exec()),
        alignment=Qt.AlignmentFlag.AlignRight,
    )
    dialog.exec()
