import os
import time
from dataclasses import replace
from importlib.resources import files
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import write_task
from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QPushButton, QWidget

from dc_modpack_creator.config import load_settings, load_task, save_settings
from dc_modpack_creator.dialogs import DetailDialog, SettingsDialog
from dc_modpack_creator.queue import BatchWorker
from dc_modpack_creator.ui import MainWindow
from dc_modpack_creator.ui_common import STYLE, CheckBox, DirectoryDialog, MessageDialog


def wait_worker(worker, qt_app, seconds=15):
    deadline = time.monotonic() + seconds
    while worker.isRunning() and time.monotonic() < deadline:
        qt_app.processEvents()
        QTest.qWait(5)
    if worker.isRunning():
        worker.cancel_all()
        worker.answer_overwrite(False)
        worker.wait(5000)
        pytest.fail("Worker timeout")
    qt_app.processEvents()


@pytest.mark.skipif(os.name != "nt", reason="Windows engine")
def test_serial_queue_failure_isolation_and_cancel_waiting(workspace, qt_app):
    app, _, settings = workspace
    paths = [
        write_task(app, "first"),
        write_task(app, "bad", extra='\n[[extras]]\nsource="missing"\n'),
        write_task(app, "cancelled"),
        write_task(app, "last"),
    ]
    worker = BatchWorker(paths, app, settings)
    worker.cancel(2)
    events = []
    worker.changed.connect(lambda i, r: events.append((i, r.status)))
    worker.start()
    wait_worker(worker, qt_app)
    assert [r.status for r in worker.records] == ["success", "failed", "cancelled", "success"]
    first_success = events.index((0, "success"))
    last_running = events.index((3, "running"))
    assert first_success < last_running
    assert all(r.log_path.exists() for r in worker.records)


@pytest.mark.skipif(os.name != "nt", reason="Windows engine")
def test_overwrite_cancel_starts_no_exports(workspace, qt_app):
    app, _, settings = workspace
    path = write_task(app)
    output = load_task(path, app, settings).target
    output.parent.mkdir()
    output.write_bytes(b"old")
    worker = BatchWorker([path], app, settings)
    worker.overwrite_requested.connect(lambda files: worker.answer_overwrite(False))
    worker.start()
    wait_worker(worker, qt_app)
    assert worker.records[0].status == "cancelled"
    assert output.read_bytes() == b"old"


def test_settings_exact_units_without_volume_limits(workspace):
    app, _, settings = workspace
    save_settings(app, settings)
    dialog = SettingsDialog(app, settings)
    dialog.volume.setCurrentIndex(1)
    assert "1,073,741,824" in dialog.bytes_hint.text()
    assert [dialog.unit.itemText(i) for i in range(dialog.unit.count())] == ["MB", "GB"]
    dialog.volume.setCurrentIndex(3)
    assert dialog.volume.currentText() == "4 GB"
    assert dialog.volume_bytes() == 4 * 1024**3
    dialog.save()
    assert load_settings(app).volume_size == 4_294_967_296
    assert 'volume_size_bytes = "4GB"' in app.settings.read_text(encoding="utf-8")
    dialog = SettingsDialog(app, load_settings(app))
    assert dialog.volume.currentText() == "4 GB"
    dialog.volume.setCurrentIndex(4)
    dialog.amount.setText("5")
    dialog.unit.setCurrentText("GB")
    dialog.save()
    assert load_settings(app).volume_size == 5 * 1024**3
    dialog = SettingsDialog(app, load_settings(app))
    dialog.amount.setText("0.0000001")
    dialog.unit.setCurrentText("MB")
    dialog.save()
    assert "整数字节" in dialog.error.text()
    dialog.amount.setText("1.5")
    dialog.unit.setCurrentText("MB")
    assert dialog.volume_bytes() == 1_572_864
    dialog.save()
    assert load_settings(app).volume_size == 1_572_864
    assert 'volume_size_bytes = "1.5MB"' in app.settings.read_text(encoding="utf-8")


@pytest.mark.parametrize("volume_size", [0, 2 * 1024**3, 1_572_864])
def test_settings_advanced_collapse_restores_geometry(workspace, qt_app, volume_size):
    app, _, settings = workspace
    dialog = SettingsDialog(app, replace(settings, volume_size=volume_size))
    dialog.show()
    qt_app.processEvents()
    collapsed_size = dialog.size()
    collapsed_minimum = dialog.minimumSize()
    toggle_position = dialog.advanced.pos()
    save = next(value for value in dialog.findChildren(QPushButton) if value.text() == "保存")
    save_position = save.pos()
    try:
        for _ in range(3):
            QTest.mouseClick(dialog.advanced, Qt.MouseButton.LeftButton)
            qt_app.processEvents()
            assert dialog.advanced_panel.isVisible()
            assert dialog.height() > collapsed_size.height()

            QTest.mouseClick(dialog.advanced, Qt.MouseButton.LeftButton)
            qt_app.processEvents()
            assert not dialog.advanced_panel.isVisible()
            assert dialog.size() == collapsed_size
            assert dialog.minimumSize() == collapsed_minimum
            assert dialog.advanced.pos() == toggle_position
            assert save.pos() == save_position
    finally:
        dialog.close()


def test_settings_volume_controls_collapse_restores_height(workspace, qt_app):
    app, _, settings = workspace
    dialog = SettingsDialog(app, settings)
    dialog.show()
    qt_app.processEvents()
    collapsed_height = dialog.height()
    try:
        dialog.volume.setCurrentIndex(dialog.volume.findData(-1))
        qt_app.processEvents()
        assert dialog.custom.isVisible()
        assert dialog.height() > collapsed_height

        dialog.volume.setCurrentIndex(dialog.volume.findData(0))
        qt_app.processEvents()
        assert not dialog.custom.isVisible()
        assert dialog.height() == collapsed_height
    finally:
        dialog.close()


def test_custom_checkbox_label_keyboard_and_disabled(qt_app):
    checkbox = CheckBox("全选")
    checkbox.setStyleSheet(STYLE)
    checkbox.resize(checkbox.sizeHint())
    checkbox.show()
    qt_app.processEvents()
    # The label remains clickable even though Qt no longer paints the control.
    QTest.mouseClick(checkbox, Qt.MouseButton.LeftButton, pos=checkbox.rect().topRight())
    assert checkbox.isChecked()
    checkbox.setFocus()
    QTest.keyClick(checkbox, Qt.Key.Key_Space)
    assert not checkbox.isChecked()
    checkbox.setTristate(True)
    checkbox.setCheckState(Qt.CheckState.PartiallyChecked)
    QTest.keyClick(checkbox, Qt.Key.Key_Space)
    assert checkbox.checkState() == Qt.CheckState.Checked
    checkbox.setEnabled(False)
    QTest.mouseClick(checkbox, Qt.MouseButton.LeftButton)
    QTest.keyClick(checkbox, Qt.Key.Key_Space)
    assert checkbox.checkState() == Qt.CheckState.Checked
    checkbox.close()


def test_config_title_does_not_toggle_selection(workspace, qt_app):
    app, _, settings = workspace
    path = write_task(app)
    window = MainWindow(app, settings)
    window.show()
    qt_app.processEvents()
    row = window.config_rows[0]
    title = next(value for value in row.findChildren(QLabel) if value.text() == path.name)
    QTest.mouseClick(title, Qt.MouseButton.LeftButton)
    assert not row.checkbox.isChecked()
    QTest.mouseClick(row.checkbox, Qt.MouseButton.LeftButton)
    assert row.checkbox.isChecked()
    QTest.mouseClick(title, Qt.MouseButton.LeftButton)
    assert row.checkbox.isChecked()
    assert not any(value.text() == path.name for value in row.findChildren(QPushButton))
    window.close()


@pytest.mark.parametrize("kind", ["main", "settings", "message", "directory"])
def test_window_construction_never_shows_temporary_native_windows(workspace, qt_app, kind):
    app, _, settings = workspace
    shown_windows = []

    class ShowObserver(QObject):
        def eventFilter(self, obj, event):
            if isinstance(obj, QWidget) and event.type() == QEvent.Type.Show and obj.isWindow():
                shown_windows.append(obj)
            return False

    observer = ShowObserver()
    qt_app.installEventFilter(observer)
    window = None
    try:
        window = {
            "main": lambda: MainWindow(app, settings),
            "settings": lambda: SettingsDialog(app, settings),
            "message": lambda: MessageDialog("提示", "显示顺序检查"),
            "directory": lambda: DirectoryDialog(app.work),
        }[kind]()
        qt_app.processEvents()
        assert shown_windows == [], "Construction must not display any top-level window"
        window.show()
        qt_app.processEvents()
        assert shown_windows == [window]
        assert window.windowFlags() & Qt.WindowType.FramelessWindowHint
        assert window.surface.parentWidget() is not None
        assert not window.surface.isWindow()
    finally:
        qt_app.removeEventFilter(observer)
        if window is not None:
            window.close()


@pytest.mark.parametrize("dismiss", ["close", "escape", "enter", "confirm"])
def test_frameless_confirmation_cancel_and_accept(qt_app, dismiss):
    dialog = MessageDialog(
        "退出", "取消任务并退出？", accept_text="取消任务并退出", reject_text="返回"
    )
    dialog.show()
    qt_app.processEvents()
    assert dialog.windowFlags() & Qt.WindowType.FramelessWindowHint
    assert dialog.surface.graphicsEffect() is None
    if dismiss == "close":
        QTest.mouseClick(dialog.header.close_button, Qt.MouseButton.LeftButton)
    elif dismiss == "confirm":
        QTest.mouseClick(dialog.accept_button, Qt.MouseButton.LeftButton)
    else:
        QTest.keyClick(dialog, Qt.Key.Key_Escape if dismiss == "escape" else Qt.Key.Key_Return)
    assert dialog.result() == (
        QDialog.DialogCode.Accepted if dismiss == "confirm" else QDialog.DialogCode.Rejected
    )
    assert not dialog.isVisible()


@pytest.mark.parametrize("confirm", [False, True])
def test_frameless_overwrite_confirmation(workspace, qt_app, confirm):
    app, _, settings = workspace
    window = MainWindow(app, settings)
    window.show()
    answers, dialogs = [], []
    window.worker = SimpleNamespace(answer_overwrite=answers.append, isRunning=lambda: False)

    def answer():
        dialog = QApplication.activeModalWidget()
        dialogs.append(dialog)
        if confirm:
            next(b for b in dialog.findChildren(QPushButton) if b.text() == "全部替换").click()
        else:
            dialog.header.close_button.click()

    QTimer.singleShot(0, answer)
    window.confirm_overwrite([str(app.configs / "existing.zip")])
    assert answers == [confirm]
    assert dialogs[0].windowFlags() & Qt.WindowType.FramelessWindowHint
    window.worker = None
    window.close()


def test_directory_picker_uses_one_window_and_returns_selection(workspace, qt_app):
    app, _, _ = workspace
    dialog = DirectoryDialog(app.work)
    dialog.show()
    qt_app.processEvents()
    assert dialog.windowFlags() & Qt.WindowType.FramelessWindowHint
    assert not dialog.picker.isWindow()
    assert not dialog.picker.isSizeGripEnabled()
    dialog.picker.setDirectory(str(app.configs))
    dialog.picker.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert [Path(p) for p in dialog.picker.selectedFiles()] == [app.configs]


@pytest.mark.parametrize("confirm", [False, True])
def test_busy_window_exit_confirmation_preserves_cancel_behavior(workspace, qt_app, confirm):
    app, _, settings = workspace
    window = MainWindow(app, settings)
    window.show()
    cancelled, answers = [], []
    window.worker = SimpleNamespace(
        isRunning=lambda: True,
        cancel_all=lambda: cancelled.append(True),
        answer_overwrite=answers.append,
    )

    def answer():
        dialog = QApplication.activeModalWidget()
        if confirm:
            dialog.accept_button.click()
        else:
            dialog.header.close_button.click()

    QTimer.singleShot(0, answer)
    window.close()
    assert window.isVisible()
    assert window._closing == confirm
    assert cancelled == ([True] if confirm else [])
    assert answers == ([False] if confirm else [])
    window.worker = None
    window.close()


def test_main_selection_restore_missing_and_empty(workspace, qt_app):
    app, _, settings = workspace
    path = write_task(app)
    settings = replace(settings, selected_configs=("导出/task.toml", "导出/moved.toml"))
    save_settings(app, settings)
    window = MainWindow(app, settings)
    window.show()
    qt_app.processEvents()
    assert [info.path for info in window.configs] == [path]
    assert load_settings(app).selected_configs == ("导出/task.toml",)
    assert window.toolbar_title.isVisible()
    assert window.all_checkbox.checkState() == Qt.CheckState.Checked
    window.config_rows[0].checkbox.setChecked(False)
    assert window.all_checkbox.checkState() == Qt.CheckState.Unchecked
    assert not load_settings(app).selected_configs
    window.config_rows[0].checkbox.setChecked(True)
    path.unlink()
    window.refresh()
    assert not window.configs
    assert not load_settings(app).selected_configs
    assert window.config_page.currentIndex() == 1
    assert not window.toolbar_title.isVisible()
    assert not window.action.isEnabled()
    assert not window.all_checkbox.isEnabled()
    write_task(app)
    window.refresh()
    assert window.toolbar_title.isVisible()
    assert window.all_checkbox.isEnabled()
    assert not window.action.isEnabled()
    QTest.mouseClick(window.all_checkbox, Qt.MouseButton.LeftButton)
    assert window.action.isEnabled()
    window.close()


def test_failed_discovery_preserves_remembered_configs(workspace, monkeypatch):
    app, _, settings = workspace
    write_task(app)
    settings = replace(settings, selected_configs=("导出/task.toml",))
    save_settings(app, settings)

    def fail_discovery(paths):
        raise PermissionError("无法读取配置目录")

    monkeypatch.setattr(type(app), "discover", fail_discovery)
    window = MainWindow(app, settings)
    assert load_settings(app).selected_configs == settings.selected_configs
    assert window.notice.text() == "无法读取配置目录"
    window.close()


def test_generate_example_config_refreshes_and_opens_without_overwriting(
    workspace, qt_app, monkeypatch
):
    app, _, settings = workspace
    window = MainWindow(app, settings)
    window.show()
    qt_app.processEvents()
    opened = []
    monkeypatch.setattr(
        "dc_modpack_creator.ui.open_text_file", lambda parent, path: opened.append(path)
    )
    generate = next(b for b in window.findChildren(QPushButton) if b.text() == "生成示例导出配置")
    assert generate.isVisible()
    QTest.mouseClick(generate, Qt.MouseButton.LeftButton)
    example = app.configs / "导出配置示例.toml"
    template = files("dc_modpack_creator").joinpath("assets", "templates", "导出配置示例.toml")
    assert example.read_bytes() == template.read_bytes()
    config = load_task(example, app, settings)
    assert config.name and config.rules and config.full_package
    assert opened == [example]
    assert [info.path for info in window.configs] == [example]
    assert window.config_page.currentIndex() == 0
    example.write_text("# user edits", encoding="utf-8")
    window.generate_example_config()
    assert example.read_text(encoding="utf-8") == "# user edits"
    second = app.configs / "导出配置示例 (2).toml"
    assert second.read_bytes() == template.read_bytes()
    assert opened == [example, second]
    window.close()


def test_generate_example_config_reports_write_failure(workspace, monkeypatch):
    app, _, settings = workspace
    window = MainWindow(app, settings)
    errors, opened = [], []

    def fail(paths):
        raise PermissionError("read-only config directory")

    monkeypatch.setattr("dc_modpack_creator.ui.create_example_task", fail)
    monkeypatch.setattr(window, "show_error", lambda title, text: errors.append((title, text)))
    monkeypatch.setattr(
        "dc_modpack_creator.ui.open_text_file", lambda parent, path: opened.append(path)
    )
    window.generate_example_config()
    assert errors == [("无法生成示例导出配置", "read-only config directory")]
    assert not opened
    assert not app.discover()
    window.close()


def test_details_runs_background_check(workspace, qt_app, monkeypatch):
    app, _, settings = workspace
    save_settings(app, settings)
    path = write_task(app)
    dialog = DetailDialog(path, app, settings)
    dialog.show()
    qt_app.processEvents()
    assert not dialog.rules.isVisible()
    assert not dialog.check_state.isVisible()
    assert not dialog.results.isVisible()
    QTest.mouseClick(dialog.rules_section.toggle, Qt.MouseButton.LeftButton)
    assert dialog.rules.isVisible()
    dialog.rules_section.toggle.setFocus()
    QTest.keyClick(dialog.rules_section.toggle, Qt.Key.Key_Space)
    assert not dialog.rules.isVisible()
    opened = []
    monkeypatch.setattr(
        "dc_modpack_creator.dialogs.open_text_file", lambda parent, path: opened.append(path)
    )
    dialog.edit_button.click()
    assert opened == [path]
    dialog.check_config()
    assert dialog.check_section.toggle.isChecked()
    deadline = time.monotonic() + 5
    while dialog.worker.isRunning() and time.monotonic() < deadline:
        qt_app.processEvents()
        QTest.qWait(5)
    qt_app.processEvents()
    assert dialog.plan
    assert dialog.summary_values["minecraft"].text() == "1.21.1"
    assert dialog.summary_values["source"].text() == ".minecraft/versions/test"
    assert "规则命中统计" in dialog.results.toPlainText()
    dialog.header.close_button.click()
    assert not dialog.isVisible()


def test_corrupt_settings_repair_preserves_original(workspace):
    app, _, settings = workspace
    original = b"broken = ["
    app.settings.write_bytes(original)
    window = MainWindow(app, None, "bad")
    assert not window.action.isEnabled()
    window.repair_settings()
    backups = list(app.data.glob("config-broken-*.toml"))
    assert len(backups) == 1 and backups[0].read_bytes() == original
    assert load_settings(app).level == 5
    window.close()
