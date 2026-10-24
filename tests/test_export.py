import io
import json
import os
import subprocess
import zipfile
from pathlib import Path

import pytest
from conftest import write_task

from dc_modpack_creator.archive import InfoZip, engine_path
from dc_modpack_creator.errors import Cancelled, ExportError
from dc_modpack_creator.exporter import export_plan
from dc_modpack_creator.planner import preflight_batch

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Bundled engine is Windows x64")


def plan(workspace, **kwargs):
    app, _, settings = workspace
    path = write_task(app, **kwargs)
    r = preflight_batch([path], app, settings)[0]
    assert not r.error, r.error
    return r.plan


@pytest.mark.parametrize(
    ("name", "version", "filename"),
    [
        ("测试包", "v1", "测试包 v1.mrpack"),
        ("测试包", "", "测试包.mrpack"),
        ("", "v1", "v1.mrpack"),
    ],
)
def test_real_mrpack_unicode_and_index(workspace, name, version, filename):
    p = plan(workspace, pack_name=name, pack_version=version)
    outputs = export_plan(p)
    assert outputs[0].name == filename
    with zipfile.ZipFile(outputs[0]) as z:
        index = json.loads(z.read("modrinth.index.json"))
        assert index["dependencies"] == {"minecraft": "1.21.1"}
        assert index["files"] == []
        assert index["name"] == name
        assert index["versionId"] == version
        assert "overrides/mods/模组.jar" in z.namelist()
        assert "overrides/mods/skip.disabled" not in z.namelist()
        assert z.read("overrides/config/中文 $ 配置.txt").decode() == "hello world" * 200
    assert not list(p.config.temp_root.iterdir())


def test_full_package_pcl_extras_and_store(workspace):
    app, instance, _ = workspace
    (instance / "PCL").mkdir()
    (instance / "PCL/Setup.ini").write_bytes(b"instance")
    (app.work / "PCL/Pictures").mkdir(parents=True)
    (app.work / "PCL/Pictures/picture.png").write_bytes(b"picture")
    (app.work / "PCL/Setup.ini").write_bytes(b"personal" * 1000)
    p = plan(
        workspace,
        full=True,
        extra='\n[[extras]]\nsource="launcher.exe"\ndestination="附加/说明.exe"\n',
    )
    out = export_plan(p)[0]
    with zipfile.ZipFile(out) as z:
        assert z.read("启动Minecraft.exe") == b"local launcher fixture"
        assert z.getinfo("modpack.mrpack").compress_type == zipfile.ZIP_STORED
        assert z.getinfo("PCL/Setup.ini").compress_type == zipfile.ZIP_DEFLATED
        assert z.read("PCL/Setup.ini") == b"personal" * 1000
        assert "附加/说明.exe" in z.namelist()
        with zipfile.ZipFile(io.BytesIO(z.read("modpack.mrpack"))) as inner:
            assert inner.read("overrides/PCL/Setup.ini") == b"instance"


def test_split_roundtrip_and_single_volume(workspace, monkeypatch):
    # Ambient options must not change conversion or prompt for passwords/media.
    monkeypatch.setenv("ZIPOPT", "-e -sp")
    p = plan(workspace, name="中文 空格🧪", full=True, volume=65537)
    outputs = export_plan(p)
    assert len(outputs) > 1 and outputs[0].suffix == ".z01"
    assert outputs[-1] == p.config.target
    assert all(x.stat().st_size <= 65536 for x in outputs)
    assert not list(p.config.output_dir.glob("*.zip.*"))
    extracted = p.config.output_dir / "extracted"
    subprocess.run(
        [str(engine_path()), "x", str(outputs[-1]), "-o" + str(extracted), "-y"],
        capture_output=True,
        check=True,
        timeout=30,
    )
    assert (extracted / "启动Minecraft.exe").read_bytes() == b"local launcher fixture"
    with zipfile.ZipFile(extracted / "modpack.mrpack") as z:
        assert z.read("overrides/mods/模组.jar") == (workspace[1] / "mods/模组.jar").read_bytes()
    listing = subprocess.run(
        [str(engine_path()), "l", "-slt", "-sccUTF-8", str(outputs[-1])],
        capture_output=True,
        check=True,
        timeout=30,
    ).stdout.decode("utf-8")
    assert "Multivolume = +" in listing
    assert "Method = Store" in listing.split("Path = modpack.mrpack", 1)[1]
    assert not list(p.config.output_dir.glob(".dc-zip-*"))
    p = plan(workspace, name="single", full=True, volume=1_000_000)
    assert export_plan(p) == [p.config.target]


def test_overwrite_requires_confirmation_and_rechecks_changes(workspace):
    p = plan(workspace)
    p.config.target.write_bytes(b"old")
    app, _, settings = workspace
    p = preflight_batch([p.config.path], app, settings)[0].plan
    with pytest.raises(ExportError, match="确认"):
        export_plan(p)
    assert p.config.target.read_bytes() == b"old"
    p.config.target.write_bytes(b"changed after preflight")
    with pytest.raises(ExportError, match="预检后"):
        export_plan(p, replace=True)
    assert p.config.target.read_bytes() == b"changed after preflight"
    p = preflight_batch([p.config.path], app, settings)[0].plan
    export_plan(p, replace=True)
    assert zipfile.is_zipfile(p.config.target)


def test_copy_cancellation_preserves_old_and_cleans_staging(workspace):
    p = plan(workspace)
    cancelled = False

    def progress(stage, done, total):
        nonlocal cancelled
        if stage == "复制文件" and done:
            cancelled = True

    def check():
        if cancelled:
            raise Cancelled("已取消")

    with pytest.raises(Cancelled):
        export_plan(p, check=check, progress=progress)
    assert not p.config.target.exists()
    assert not list(p.config.temp_root.iterdir())


def test_compression_failure_removes_partial_output(workspace, monkeypatch):
    p = plan(workspace)

    def fail(self, target, *args, **kwargs):
        target.write_bytes(b"partial")
        raise ExportError("test compression failure")

    monkeypatch.setattr("dc_modpack_creator.archive.SevenZip.add", fail)
    with pytest.raises(ExportError, match="compression failure"):
        export_plan(p)
    assert not p.config.target.exists()
    assert not list(p.config.temp_root.iterdir())


def test_cancel_running_qprocess_cleans_output(workspace):
    p = plan(workspace)
    started = False

    def log(line):
        nonlocal started
        if line.startswith("压缩命令"):
            started = True

    def check():
        if started:
            raise Cancelled("已取消")

    with pytest.raises(Cancelled):
        export_plan(p, check=check, log=log)
    assert started
    assert not p.config.target.exists()
    assert not list(p.config.temp_root.iterdir())


def test_cancel_split_cleans_all_created_volumes(workspace):
    p = plan(workspace, full=True, volume=65536)
    cancelled = False

    def progress(stage, done, total):
        nonlocal cancelled
        if stage == "生成分卷" and done:
            cancelled = True

    def check():
        if cancelled:
            raise Cancelled("已取消")

    with pytest.raises(Cancelled):
        export_plan(p, check=check, progress=progress)
    assert not list(p.config.output_dir.iterdir())
    assert not list(p.config.temp_root.iterdir())


def test_cancel_running_splitter_stops_process_and_cleans_output(workspace):
    p = plan(workspace, full=True, volume=65536)
    started = False

    def log(line):
        nonlocal started
        if line.startswith("分卷命令"):
            started = True

    def check():
        if started:
            raise Cancelled("已取消")

    with pytest.raises(Cancelled):
        export_plan(p, check=check, log=log)
    assert started
    assert not list(p.config.output_dir.iterdir())
    assert not list(p.config.temp_root.iterdir())


def test_splitter_failure_cleans_private_and_published_files(workspace, monkeypatch):
    p = plan(workspace, full=True, volume=65536)

    def fail(self, source, target, volume_size, created):
        (source.parent / "split.z01").write_bytes(b"partial")
        first = target.with_suffix(".z01")
        first.write_bytes(b"published")
        created.append(first)
        raise ExportError("test conversion failure")

    monkeypatch.setattr(InfoZip, "split", fail)
    with pytest.raises(ExportError, match="conversion failure"):
        export_plan(p)
    assert not list(p.config.output_dir.iterdir())
    assert not list(p.config.temp_root.iterdir())


def test_split_publish_does_not_overwrite_new_file(workspace, monkeypatch):
    p = plan(workspace, full=True, volume=65536)
    original = Path.rename

    def race(self, target):
        if self.name == "split.z02":
            target.write_bytes(b"appeared after preflight")
        return original(self, target)

    monkeypatch.setattr(Path, "rename", race)
    with pytest.raises(FileExistsError):
        export_plan(p)
    assert [(f.name, f.read_bytes()) for f in p.config.output_dir.iterdir()] == [
        (p.config.target.with_suffix(".z02").name, b"appeared after preflight")
    ]
    assert not list(p.config.temp_root.iterdir())


def test_missing_splitter_preserves_old_output(workspace, monkeypatch):
    p = plan(workspace, full=True, volume=65536)
    p.config.target.write_bytes(b"old")
    app, _, settings = workspace
    p = preflight_batch([p.config.path], app, settings)[0].plan

    def missing():
        raise ExportError("test missing splitter")

    monkeypatch.setattr("dc_modpack_creator.archive.check_splitter", missing)
    with pytest.raises(ExportError, match="missing splitter"):
        export_plan(p, replace=True)
    assert p.config.target.read_bytes() == b"old"


def test_native_export_replaces_old_byte_and_native_volume_families(workspace):
    p = plan(workspace, full=True, volume=65536)
    old = [
        p.config.target.with_name(p.config.target.name + ".001"),
        p.config.target.with_suffix(".z99"),
        p.config.target.with_suffix(".z100"),
    ]
    for path in old:
        path.write_bytes(b"old volume")
    unrelated = p.config.target.with_name(p.config.target.name + ".notes")
    unrelated.write_bytes(b"keep")
    app, _, settings = workspace
    p = preflight_batch([p.config.path], app, settings)[0].plan
    assert set(p.existing) == set(old)
    outputs = export_plan(p, replace=True)
    assert outputs[-1] == p.config.target and outputs[0].suffix == ".z01"
    assert not any(path.exists() for path in old)
    assert unrelated.read_bytes() == b"keep"


def test_native_split_copies_forced_zip64_entry(tmp_path):
    stage = tmp_path / "staging"
    stage.mkdir()
    source = stage / "complete.zip"
    payload = os.urandom(200_000)
    with zipfile.ZipFile(source, "w") as z:
        with z.open("中文🧪.bin", "w", force_zip64=True) as f:
            f.write(payload)
    assert b"\x01\x00\x10\x00" in source.read_bytes()[:100]
    target = tmp_path / "结果.zip"
    outputs = InfoZip(lambda: None, lambda _: None, lambda *_: None).split(
        source, target, 65536, []
    )
    extracted = tmp_path / "extracted"
    subprocess.run(
        [str(engine_path()), "x", str(outputs[-1]), "-o" + str(extracted), "-y"],
        capture_output=True,
        check=True,
        timeout=30,
    )
    assert (extracted / "中文🧪.bin").read_bytes() == payload
