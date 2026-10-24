import json
import tomllib
from dataclasses import replace

import pytest
from conftest import write_task

from dc_modpack_creator.config import (
    archive_path,
    format_volume_size,
    load_settings,
    load_task,
    parse_volume_size,
    save_settings,
    validate_compression,
)
from dc_modpack_creator.errors import ExportError
from dc_modpack_creator.metadata import resolve_dependencies
from dc_modpack_creator.planner import preflight_batch
from dc_modpack_creator.rules import RuleSet


def test_rule_order_windows_paths_and_literals():
    r = RuleSet("""# ignored
mods/
!mods/*.jar
mods/[a-c][0-9]?.jar
mods/*]*.jar
xaero/mw$-647822281/*.zip
""")
    assert r.select("MODS\\b4中.JAR")
    assert not r.select("mods/other.jar")
    assert r.select("mods/可选]材质.jar")
    assert r.select("xaero/mw$-647822281/map.zip")
    assert r.select("mods/nested/a.disabled")
    assert not r.select("unselected.txt")


@pytest.mark.parametrize(
    "pattern",
    ["a/[z-a]", "!", "a/\\"],
)
def test_invalid_rules(pattern):
    with pytest.raises(ExportError):
        RuleSet(pattern)


def test_character_classes():
    assert RuleSet("mods/[!abc].jar").select("mods/d.jar")
    assert not RuleSet("mods/[!abc].jar").select("mods/a.jar")
    assert RuleSet("mods/[]].jar").select("mods/].jar")
    assert RuleSet("mods/[-a].jar").select("mods/-.jar")


@pytest.mark.parametrize(
    "path", ["", "../x", "a/./x", "C:/x", "/x", "a//x", "CON.txt", "a:ads", "a/..", "a/"]
)
def test_archive_paths(path):
    with pytest.raises(ExportError):
        archive_path(path)


def test_settings_roundtrip_and_discovery(workspace):
    app, _, settings = workspace
    settings = replace(
        settings,
        selected_configs=("导出/中文.toml",),
        volume_size=3 * 1024**3,
        window={"x": 12, "y": 34},
    )
    save_settings(app, settings)
    assert (
        tomllib.loads(app.settings.read_text(encoding="utf-8"))["compression"]["volume_size_bytes"]
        == "3GB"
    )
    assert load_settings(app) == settings
    task = write_task(app)
    assert app.discover() == [task]


def test_compression_volume_accepts_nonnegative_integers():
    for n in (0, 1, 65_535, 4_294_967_296, 8 * 1024**3):
        validate_compression(5, n, 0)
    for n in (-1, True, 1.5):
        with pytest.raises(ExportError):
            validate_compression(5, n, 0)


def test_legacy_global_output_is_ignored_and_removed_on_save(workspace):
    app, _, settings = workspace
    save_settings(app, settings)
    app.settings.write_text(
        app.settings.read_text(encoding="utf-8") + '\n[output]\ndirectory = "旧输出目录"\n',
        encoding="utf-8",
    )
    loaded = load_settings(app)
    assert loaded == settings
    path = write_task(app)
    path.write_text(
        path.read_text(encoding="utf-8").replace('[output]\ndirectory = "out"', ""),
        encoding="utf-8",
    )
    assert load_task(path, app, loaded).output_dir == app.work / "导出"
    save_settings(app, loaded)
    assert "output" not in tomllib.loads(app.settings.read_text(encoding="utf-8"))


@pytest.mark.parametrize("base", [None, ".", "游戏/生存服", "absolute"])
@pytest.mark.parametrize("task_output", [False, True])
def test_source_base_dir_preflight(workspace, monkeypatch, base, task_output):
    app, _, settings = workspace
    root = app.work if base in (None, ".") else app.work / "游戏/生存服"
    if root != app.work:
        root.mkdir(parents=True)
        (app.work / ".minecraft").rename(root / ".minecraft")
        (app.work / "launcher.exe").rename(root / "launcher.exe")
    (root / "说明.txt").write_text("使用说明", encoding="utf-8")
    path = write_task(app, full=True, extra='\n[[extras]]\nsource = "说明.txt"\n')
    content = path.read_text(encoding="utf-8")
    if base is not None:
        value = str(root) if base == "absolute" else base
        content = content.replace("[source]\n", f"[source]\nbase_dir = {json.dumps(value)}\n")
    if not task_output:
        content = content.replace('directory = "out"', "")
    path.write_text(content, encoding="utf-8")
    # Paths use the captured startup directory even if the process directory changes.
    monkeypatch.chdir(app.configs)
    result = preflight_batch([path], app, settings)[0]
    assert not result.error
    config = result.plan.config
    assert config.instance_dir == root / ".minecraft/versions/test"
    assert config.launcher == root / "launcher.exe"
    assert config.extras[0].source == root / "说明.txt"
    assert config.extras[0].destination == "说明.txt"
    assert config.output_dir == root / ("out" if task_output else "导出")
    assert config.temp_root == app.work / "tmp"
    assert any(f.source == root / "说明.txt" for f in result.plan.files)


def test_source_base_dir_preserves_absolute_paths(workspace):
    app, instance, settings = workspace
    path = write_task(app, full=True, extra='\n[[extras]]\nsource = "launcher.exe"\n')
    content = path.read_text(encoding="utf-8").replace(
        "[source]\n", '[source]\nbase_dir = "other"\n'
    )
    for relative in (".minecraft/versions/test", "launcher.exe", "out"):
        content = content.replace(f'"{relative}"', json.dumps(str(app.work / relative)))
    path.write_text(content, encoding="utf-8")
    config = load_task(path, app, settings)
    assert config.instance_dir == instance
    assert config.launcher == app.work / "launcher.exe"
    assert config.extras[0].source == app.work / "launcher.exe"
    assert config.output_dir == app.work / "out"


@pytest.mark.parametrize("value", ['""', '"  "', '"\\u0000"', "42", "false", "[]", "{}"])
def test_invalid_source_base_dir(workspace, value):
    app, _, settings = workspace
    path = write_task(app)
    path.write_text(
        path.read_text(encoding="utf-8").replace("[source]\n", f"[source]\nbase_dir = {value}\n"),
        encoding="utf-8",
    )
    with pytest.raises(ExportError, match="base_dir"):
        load_task(path, app, settings)


@pytest.mark.parametrize(
    ("text", "size"),
    [
        ("0", 0),
        ("0GB", 0),
        ("3GB", 3_221_225_472),
        (" 1.5 gb ", 1_610_612_736),
        ("1.5MB", 1_572_864),
        ("1MB", 1_048_576),
        ("4GB", 4_294_967_296),
        ("4294967296B", 4_294_967_296),
        ("64KB", 65_536),
        ("65537B", 65_537),
        ("3.999999999068677425384521484375GB", 4_294_967_295),
        ("1B", 1),
        ("65535B", 65_535),
        ("4294967297B", 4_294_967_297),
        ("8GB", 8 * 1024**3),
    ],
)
def test_volume_strings_load_in_both_configs(workspace, text, size):
    app, _, settings = workspace
    path = write_task(app, full=True)
    for config in (app.settings, path):
        config.write_text(
            config.read_text(encoding="utf-8").replace(
                'volume_size_bytes = "0"', f'volume_size_bytes = "{text}"'
            ),
            encoding="utf-8",
        )
    assert load_settings(app).volume_size == size
    assert load_task(path, app, settings).volume_size == size


@pytest.mark.parametrize(
    "value",
    [
        0,
        4_000_000_000,
        True,
        1.5,
        None,
        [],
        "",
        "4",
        "4TB",
        "-1GB",
        "NaNGB",
        "1e3MB",
        "1,5GB",
        "0.1B",
        "4GiB",
        "1MiB",
        "64KiB",
        "0.000065536000000000000000000000001GB",
    ],
)
def test_invalid_volume_strings(value):
    with pytest.raises(ExportError):
        parse_volume_size(value)


@pytest.mark.parametrize(
    "size",
    [
        0,
        1,
        65_535,
        65_536,
        65_537,
        1_572_864,
        4_000_000_000,
        4_294_967_296,
        8 * 1024**3,
        10**80 + 1,
    ],
)
def test_settings_volume_string_roundtrip_is_exact(workspace, size):
    app, _, settings = workspace
    save_settings(app, replace(settings, volume_size=size))
    saved = tomllib.loads(app.settings.read_text(encoding="utf-8"))["compression"][
        "volume_size_bytes"
    ]
    assert isinstance(saved, str)
    assert load_settings(app).volume_size == size
    assert parse_volume_size(format_volume_size(size)) == size


def test_full_task_inherits_global_volume_and_can_disable_it(workspace):
    app, _, settings = workspace
    settings = replace(settings, volume_size=4_000_000_000)
    path = write_task(app, full=True)
    content = path.read_text(encoding="utf-8")
    path.write_text(content.replace('volume_size_bytes = "0"', ""), encoding="utf-8")
    assert load_task(path, app, settings).volume_size == settings.volume_size
    path.write_text(content, encoding="utf-8")
    assert load_task(path, app, settings).volume_size == 0


def test_task_values_override_global_and_mrpack_ignores_global_split(workspace):
    app, _, settings = workspace
    path = write_task(app)
    path.write_text(
        path.read_text(encoding="utf-8").replace('volume_size_bytes = "0"', "level = 9"),
        encoding="utf-8",
    )
    c = load_task(path, app, replace(settings, volume_size=1_000_000_000))
    assert c.level == 9 and c.volume_size == 0
    path = write_task(app, volume=65536)
    with pytest.raises(ExportError, match="mrpack"):
        load_task(path, app, settings)


@pytest.mark.parametrize("full", [False, True])
@pytest.mark.parametrize(
    ("name", "version", "stem"),
    [
        ("测试包", "v1", "测试包 v1"),
        ("测试包", "", "测试包"),
        ("", "v1", "v1"),
        ("测试包", None, "测试包"),
        (None, "v1", "v1"),
        ("  测试包  ", "  ", "测试包"),
    ],
)
def test_output_filename_uses_available_name_and_version(workspace, full, name, version, stem):
    app, _, settings = workspace
    path = write_task(app, full=full, pack_name=name or "", pack_version=version or "")
    content = path.read_text(encoding="utf-8")
    if name is None:
        content = content.replace('name = ""\n', "")
    if version is None:
        content = content.replace('version = ""\n', "")
    path.write_text(content, encoding="utf-8")
    config = load_task(path, app, settings)
    extension = ".zip" if full else ".mrpack"
    assert config.target == app.work / "out" / (stem + extension)


@pytest.mark.parametrize(("name", "version"), [("", ""), ("  ", "\t"), (None, None)])
def test_missing_output_name_fails_before_export(workspace, name, version):
    app, _, settings = workspace
    path = write_task(app, pack_name=name or "", pack_version=version or "")
    if name is None and version is None:
        path.write_text(
            path.read_text(encoding="utf-8").replace('name = ""\nversion = ""\n', ""),
            encoding="utf-8",
        )
    result = preflight_batch([path], app, settings)[0]
    assert result.error == "配置名称缺失"
    assert result.plan is None
    assert not (app.work / "out").exists()


@pytest.mark.parametrize("name", ["../越界", "非法:名称", "CON"])
def test_generated_output_filename_is_validated(workspace, name):
    app, _, settings = workspace
    path = write_task(app, pack_name=name, pack_version="")
    with pytest.raises(ExportError, match="不合法的 Windows 文件名"):
        load_task(path, app, settings)


@pytest.mark.parametrize(
    ("library", "key", "version"),
    [
        ("net.fabricmc:fabric-loader:0.16.9", "fabric-loader", "0.16.9"),
        ("net.minecraftforge:forge:1.21.1-52.0.0", "forge", "52.0.0"),
        ("net.neoforged:neoforge:21.1.10", "neoforge", "21.1.10"),
    ],
)
def test_metadata_from_instance(workspace, library, key, version):
    _, instance, _ = workspace
    (instance / "test.json").write_text(
        json.dumps({"clientVersion": "1.21.1", "libraries": [{"name": library}]}),
        encoding="utf-8",
    )
    assert resolve_dependencies(instance) == {
        "minecraft": "1.21.1",
        key: version,
    }


def test_missing_version_and_unsupported_loader(workspace):
    _, instance, _ = workspace
    json_path = instance / "test.json"
    for data in (
        {},
        {"id": "custom-1.21.1"},
        {"clientVersion": "1.21.1", "libraries": [{"name": "org.quiltmc:quilt-loader:1.0"}]},
    ):
        json_path.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(ExportError):
            resolve_dependencies(instance)


def test_metadata_reads_only_instance_json(workspace):
    _, instance, _ = workspace
    parent = instance.parent / "1.21.1"
    parent.mkdir()
    (parent / "1.21.1.json").write_bytes((instance / "test.json").read_bytes())
    json_path = instance / "test.json"
    json_path.write_text(json.dumps({"inheritsFrom": "1.21.1"}), encoding="utf-8")
    with pytest.raises(ExportError, match="无版本依据"):
        resolve_dependencies(instance)
    json_path.write_text(
        json.dumps({"clientVersion": "1.21.1", "inheritsFrom": "missing"}), encoding="utf-8"
    )
    assert resolve_dependencies(instance) == {"minecraft": "1.21.1"}


def test_pcl_self_contained_fabric_uses_client_version(workspace):
    _, instance, _ = workspace
    (instance / "test.json").write_text(
        json.dumps(
            {
                "id": "【DreamCity】生存服26.3",
                "clientVersion": "26.3",
                "type": "release",
                "mainClass": "net.fabricmc.loader.impl.launch.knot.KnotClient",
                "downloads": {"client": {}},
                "libraries": [{"name": "net.fabricmc:fabric-loader:0.19.5"}],
            }
        ),
        encoding="utf-8",
    )
    assert resolve_dependencies(instance) == {
        "minecraft": "26.3",
        "fabric-loader": "0.19.5",
    }


def test_malformed_metadata_is_an_actionable_error(workspace):
    _, instance, _ = workspace
    (instance / "test.json").write_text('{"type": [], "mainClass": {}}', encoding="utf-8")
    with pytest.raises(ExportError, match="必须是字符串"):
        resolve_dependencies(instance)


def test_standalone_instance_without_minecraft_root(workspace):
    app, instance, settings = workspace
    instance.rename(app.work / "test")
    instance.parent.rmdir()
    instance.parent.parent.rmdir()
    path = write_task(app)
    path.write_text(
        path.read_text(encoding="utf-8").replace(".minecraft/versions/test", "test"),
        encoding="utf-8",
    )
    result = preflight_batch([path], app, settings)[0]
    assert not result.error
    assert result.plan.dependencies == {"minecraft": "1.21.1"}


def test_planning_warns_and_lists_final_files(workspace):
    app, _, settings = workspace
    path = write_task(app, rules="mods/\n!*.disabled\nnot-found/")
    result = preflight_batch([path], app, settings)[0]
    assert not result.error
    assert [f.destination for f in result.plan.files] == ["overrides/mods/模组.jar"]
    assert len(result.plan.warnings) == 1
    assert result.plan.rule_hits[0][2] == 2


def test_conflicts_and_cross_batch_input_overlap(workspace):
    app, instance, settings = workspace
    a = write_task(app, "a", output=".minecraft/versions/test/成品")
    b = write_task(app, "b")
    results = preflight_batch([a, b], app, settings)
    assert "输入目录" in results[0].error
    assert results[1].plan
    assert not (instance / "成品").exists()
    a = write_task(app, "a")
    b.write_text(a.read_text(encoding="utf-8"), encoding="utf-8")
    assert all("冲突" in r.error for r in preflight_batch([a, b], app, settings))


def test_extra_collision_and_missing_input_are_isolated(workspace):
    app, _, settings = workspace
    a = write_task(app, "bad", extra='\n[[extras]]\nsource="missing.txt"\n')
    b = write_task(app, "ok")
    r = preflight_batch([a, b], app, settings)
    assert r[0].error and r[1].plan
    a = write_task(
        app, "bad", extra='\n[[extras]]\nsource="launcher.exe"\ndestination="modrinth.index.json"\n'
    )
    assert "冲突" in preflight_batch([a], app, settings)[0].error
