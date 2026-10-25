"""Render the actual Qt widgets offscreen for visual QA (no production demo mode)."""

# ruff: noqa: E402

import argparse
import json
import os
import time
import tomllib
from dataclasses import replace
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--scale", default="1")
args = parser.parse_args()
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_SCALE_FACTOR"] = args.scale

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

from dc_modpack_creator.config import AppPaths, Settings, save_settings
from dc_modpack_creator.dialogs import DetailDialog, LogDialog, SettingsDialog, result_dialog
from dc_modpack_creator.queue import TaskRecord
from dc_modpack_creator.ui import MainWindow, TaskRow
from dc_modpack_creator.ui_common import (
    STYLE,
    CheckBox,
    DirectoryDialog,
    MessageDialog,
    configure_font,
)

qt = QApplication([])
configure_font(qt)
root = Path(".cache/ui-review").resolve()
app = AppPaths(root)
app.initialize()
instance = root / ".minecraft/versions/test"
instance.mkdir(parents=True, exist_ok=True)
(instance / "test.json").write_text(
    json.dumps(
        {
            "id": "1.21.1",
            "type": "release",
            "mainClass": "net.minecraft.client.main.Main",
            "libraries": [{"name": "net.fabricmc:fabric-loader:0.16.9"}],
        }
    ),
    encoding="utf-8",
)
(instance / "mods").mkdir(exist_ok=True)
(instance / "mods/fabric-api.jar").write_bytes(b"visual QA fixture")
(root / "启动Minecraft.exe").write_bytes(b"visual QA fixture")
example = Path("src/dc_modpack_creator/assets/templates/导出配置示例.toml")
preview_rules = tomllib.loads(example.read_text(encoding="utf-8"))["files"]["rules"]
for filename, full in (("DC生存服", True), ("DC生存服-voxy", True), ("DC活动服", False)):
    (app.configs / (filename + ".toml")).write_text(
        f"""[pack]
name = "【DreamCity】生存服"
version = "26.3-1"
full_package = {str(full).lower()}
[source]
instance_dir = ".minecraft/versions/test"
[launcher]
executable = "启动Minecraft.exe"
[files]
rules = '''{preview_rules}'''
""",
        encoding="utf-8",
    )
settings = Settings(
    selected_configs=("导出/DC生存服.toml", "导出/DC生存服-voxy.toml"), temporary_directory="tmp"
)
save_settings(app, settings)
out = Path("artifacts/ui") / args.scale
out.mkdir(parents=True, exist_ok=True)


def capture(widget, name):
    widget.show()
    for _ in range(3):
        qt.processEvents()
    widget.grab().save(str(out / (name + ".png")))


checkbox_preview = QWidget()
checkbox_preview.setStyleSheet(STYLE)
checkbox_layout = QVBoxLayout(checkbox_preview)
checkbox_layout.setContentsMargins(24, 24, 24, 24)
checkbox_layout.setSpacing(16)
for enabled in (True, False):
    for text, state in (
        ("未选中", Qt.CheckState.Unchecked),
        ("已选中", Qt.CheckState.Checked),
        ("部分选中", Qt.CheckState.PartiallyChecked),
    ):
        checkbox = CheckBox(text if enabled else "禁用 · " + text)
        checkbox.setTristate(True)
        checkbox.setCheckState(state)
        checkbox.setEnabled(enabled)
        checkbox_layout.addWidget(checkbox)
capture(checkbox_preview, "checkboxes")
checkbox_preview.close()

empty_app = AppPaths(root / "empty-workspace")
empty_app.initialize()
empty_window = MainWindow(empty_app, Settings())
capture(empty_window, "empty")
empty_window.close()

window = MainWindow(app, settings)
capture(window, "configs")
window.config_view.setCurrentIndex(window.config_model.index(0, 0))
window.config_rows[0].checkbox.setFocus(Qt.FocusReason.TabFocusReason)
capture(window, "configs-focus")
dialog = SettingsDialog(app, settings, window)
capture(dialog, "settings")
dialog.toggle_advanced(True)
capture(dialog, "settings-advanced")
dialog.close()
dialog = SettingsDialog(app, replace(settings, volume_size=1_073_741_824), window)
capture(dialog, "settings-native-volume")
dialog.close()
detail = DetailDialog(app.configs / "DC生存服.toml", app, settings, window)
detail.metadata_worker.wait(5000)
qt.processEvents()
capture(detail, "details")
detail.show_page("rules")
qt.processEvents()
capture(detail, "details-rules")
detail.check_config()
deadline = time.monotonic() + 10
while detail.worker.isRunning() and time.monotonic() < deadline:
    qt.processEvents()
    QTest.qWait(5)
qt.processEvents()
assert detail.plan, detail.results.toPlainText()
capture(detail, "details-checked")
detail.show_page("game_files")
capture(detail, "details-files")
detail.show_page("external_files")
capture(detail, "details-external-files")
detail.show_page("overview")
detail.update_summary(
    replace(
        detail.config,
        name="【DreamCity】生存服 · 带有较长说明的整合包名称 " * 3,
        instance_dir=root / ("较长的实例目录名称/" * 8) / "test",
        output_dir=root / ("较长的输出目录名称/" * 8),
    )
)
detail.resize(760, 520)
qt.processEvents()
detail.overview_scroll.verticalScrollBar().setValue(0)
capture(detail, "details-long-paths")
assert detail.check_button.geometry().bottom() < detail.surface.height()
assert not detail.overview_scroll.horizontalScrollBar().isVisible()
detail.close()
invalid = root / "invalid-config.toml"
invalid.write_text("[pack\n", encoding="utf-8")
detail = DetailDialog(invalid, app, settings, window)
capture(detail, "details-invalid")
detail.close()
dialog = MessageDialog("无法打开", "无法打开配置文件，请检查文件路径和访问权限。", window)
capture(dialog, "message")
dialog.close()
dialog = MessageDialog(
    "退出", "取消任务并退出？", window, accept_text="取消任务并退出", reject_text="返回"
)
capture(dialog, "exit-confirmation")
dialog.close()
dialog = DirectoryDialog(app.configs, window)
capture(dialog, "directory-picker")
dialog.close()

# Capture modal-only entry points using the real dialog, then dismiss safely.
from PySide6.QtCore import QTimer


def capture_modal(name):
    dialog = qt.activeModalWidget()
    capture(dialog, name)
    # Offscreen windows are separate surfaces; compose their actual widget grabs.
    layers = []
    owner = dialog
    while owner is not window:
        if owner.isWindow():
            layers.append(owner)
        owner = owner.parentWidget()
    combined = window.grab()
    painter = QPainter(combined)
    for layer in reversed(layers):
        painter.drawPixmap(layer.pos() - window.pos(), layer.grab())
    painter.end()
    combined.save(str(out / (name + "-overlay.png")))
    dialog.reject()


for name, dialog in (
    ("details-modal", DetailDialog(app.configs / "DC生存服.toml", app, settings, window)),
    ("settings-modal", SettingsDialog(app, settings, window)),
    ("log-modal", LogDialog(TaskRecord(app.configs / "DC生存服.toml"), window)),
):
    QTimer.singleShot(0, lambda name=name: capture_modal(name))
    dialog.exec()

dialog = SettingsDialog(app, settings, window)


def capture_nested_directory():
    nested = DirectoryDialog(app.configs, dialog)
    QTimer.singleShot(0, lambda: capture_modal("nested-directory"))
    nested.exec()
    capture_modal("settings-after-directory")


QTimer.singleShot(0, capture_nested_directory)
dialog.exec()
capture(window, "configs-after-modals")


from types import SimpleNamespace

window.worker = SimpleNamespace(answer_overwrite=lambda answer: None, isRunning=lambda: False)
QTimer.singleShot(0, lambda: capture_modal("overwrite-confirmation"))
window.confirm_overwrite([str(root / "导出/【DreamCity】生存服26.3-1.zip")])
window.worker = None
window.stack.setCurrentIndex(1)
window.all_checkbox.hide()
window.refresh_button.hide()
window.config_folder_button.hide()
window.toolbar_title.setText("导出任务")
window.action.setText("取消全部")
window.status.setText("正在导出 1 项 · 等待 1 项")
records = [
    TaskRecord(app.configs / "DC生存服.toml", "running", "生成 mrpack", 47, 100, 86),
    TaskRecord(app.configs / "DC生存服-voxy.toml", "waiting", "等待导出"),
]
from PySide6.QtCore import QSize
from PySide6.QtGui import QStandardItem

for i, record in enumerate(records):
    item = QStandardItem()
    item.setSizeHint(QSize(1, 100))
    window.task_model.appendRow(item)
    row = TaskRow(i, record, window)
    window.task_rows.append(row)
    window.task_view.setIndexWidget(item.index(), row)
capture(window, "running")
window.task_view.setCurrentIndex(window.task_model.index(0, 0))
window.task_rows[0].log_button.setFocus(Qt.FocusReason.TabFocusReason)
capture(window, "running-focus")
records[0] = replace(
    records[0],
    status="success",
    stage="已完成",
    outputs=[root / "导出/【DreamCity】生存服26.3-1.zip"],
)
records[1] = replace(
    records[1], status="failed", stage="导出失败", error="源文件无法读取：mods/voxy.jar"
)
for row, record in zip(window.task_rows, records, strict=True):
    row.update_record(record)
window.toolbar_title.setText("导出结果")
window.status.setText("成功 1 · 失败 1 · 取消 0")
window.back_button.show()
window.action.setText("重试失败项")
capture(window, "results")
dialog = LogDialog(records[1], window)
capture(dialog, "log")
dialog.close()
QTimer.singleShot(0, lambda: capture_modal("result-details"))
result_dialog(records[1], [], window)
window.close()
print(out.resolve())
