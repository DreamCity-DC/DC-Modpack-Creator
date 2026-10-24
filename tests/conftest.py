import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from dc_modpack_creator.config import AppPaths, Settings
from dc_modpack_creator.ui_common import configure_font


@pytest.fixture(scope="session", autouse=True)
def qt_app():
    app = QApplication.instance() or QApplication([])
    configure_font(app)
    yield app
    # Native styles can retain animations until their widgets are destroyed.
    # Dispose widgets while Python callbacks and the Qt event loop are still available.
    for widget in app.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


@pytest.fixture
def workspace(tmp_path):
    app = AppPaths(tmp_path)
    app.initialize()
    instance = tmp_path / ".minecraft/versions/test"
    instance.mkdir(parents=True)
    (instance / "test.json").write_text(
        json.dumps(
            {
                "id": "1.21.1",
                "type": "release",
                "mainClass": "net.minecraft.client.main.Main",
            }
        ),
        encoding="utf-8",
    )
    (instance / "mods").mkdir()
    (instance / "mods/模组.jar").write_bytes(os.urandom(180_000))
    (instance / "mods/skip.disabled").write_bytes(b"disabled")
    (instance / "config").mkdir()
    (instance / "config/中文 $ 配置.txt").write_text("hello world" * 200, encoding="utf-8")
    (tmp_path / "launcher.exe").write_bytes(b"local launcher fixture")
    settings = Settings(temporary_directory="tmp")
    return app, instance, settings


def write_task(
    app: AppPaths,
    name="task",
    *,
    full=False,
    volume=0,
    extra="",
    rules="mods/\n!*.disabled\nconfig/",
    output="out",
    pack_name=None,
    pack_version="v1",
) -> Path:
    if pack_name is None:
        pack_name = "测试包" if name == "task" else name
    path = app.configs / f"{name}.toml"
    path.write_text(
        f"""schema_version = 1
[pack]
name = "{pack_name}"
version = "{pack_version}"
full_package = {str(full).lower()}
[source]
instance_dir = ".minecraft/versions/test"
[launcher]
executable = "launcher.exe"
[files]
rules = """
        + "'''"
        + rules
        + "'''"
        + f'''
[output]
directory = "{output}"
[compression]
volume_size_bytes = "{str(volume) + "B" if volume else "0"}"
'''
        + extra,
        encoding="utf-8",
    )
    return path
