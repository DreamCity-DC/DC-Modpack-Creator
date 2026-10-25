"""Exercise the packaged application from a separate working directory."""

import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

from dc_modpack_creator.config import AppPaths, Settings, save_settings

exe = Path(sys.argv[1]).resolve()
root = Path(".cache/exe-smoke").resolve()
paths = AppPaths(root)
paths.initialize()
save_settings(paths, Settings(temporary_directory="temporary"))
instance = root / ".minecraft/versions/客户端"
instance.mkdir(parents=True, exist_ok=True)
(instance / "客户端.json").write_text(
    json.dumps(
        {
            "id": "客户端",
            "clientVersion": "26.3",
            "type": "release",
            "mainClass": "net.fabricmc.loader.impl.launch.knot.KnotClient",
            "libraries": [{"name": "net.fabricmc:fabric-loader:0.19.5"}],
        }
    ),
    encoding="utf-8",
)
(instance / "mods").mkdir(exist_ok=True)
(instance / "mods/中文.jar").write_bytes(os.urandom(180_000))
(root / "本地启动器.exe").write_bytes(b"launcher fixture")
for name, full, volume in (("single", False, 0), ("full", True, 65536)):
    (paths.configs / f"{name}.toml").write_text(
        f'''[pack]
name = "{name}"
version = "1"
full_package = {str(full).lower()}
[source]
instance_dir = ".minecraft/versions/客户端"
[launcher]
executable = "本地启动器.exe"
[files]
rules = "mods/"
[output]
compression_volume_size = "{str(volume) + "B" if volume else "0"}"
''',
        encoding="utf-8",
    )
result = subprocess.run(
    [str(exe), "--export", "导出/single.toml", "导出/full.toml", "--replace"],
    cwd=root,
    capture_output=True,
    timeout=60,
)
if result.returncode:
    raise RuntimeError(f"Frozen application failed: {result.returncode}; {result.stderr!r}")
with zipfile.ZipFile(paths.configs / "single 1.mrpack") as archive:
    assert archive.read("overrides/mods/中文.jar") == (instance / "mods/中文.jar").read_bytes()
    assert json.loads(archive.read("modrinth.index.json"))["dependencies"] == {
        "minecraft": "26.3",
        "fabric-loader": "0.19.5",
    }
parts = sorted(paths.configs.glob("full 1.z[0-9]*")) + [paths.configs / "full 1.zip"]
assert len(parts) > 1
engine = Path("src/dc_modpack_creator/vendor/7za.exe").resolve()
for target in (paths.configs / "single 1.mrpack", parts[-1]):
    result = subprocess.run([str(engine), "t", str(target)], capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout
extracted = root / "extracted"
subprocess.run(
    [str(engine), "x", str(parts[-1]), "-o" + str(extracted), "-y"],
    capture_output=True,
    check=True,
    timeout=30,
)
assert (extracted / "启动Minecraft.exe").read_bytes() == b"launcher fixture"
with zipfile.ZipFile(extracted / "modpack.mrpack") as archive:
    assert archive.read("overrides/mods/中文.jar") == (instance / "mods/中文.jar").read_bytes()
listing = subprocess.run(
    [str(engine), "l", "-slt", "-sccUTF-8", str(parts[-1])],
    capture_output=True,
    check=True,
    timeout=30,
).stdout.decode("utf-8")
assert "Multivolume = +" in listing
assert "Method = Store" in listing.split("Path = modpack.mrpack", 1)[1]
assert not list(paths.configs.glob("full 1.zip.*"))
assert not list(paths.configs.glob(".dc-zip-*"))
assert not list((root / "temporary").iterdir())
print(f"Frozen EXE smoke passed: mrpack + split full ZIP; {len(parts)} volumes; verified by 7-Zip.")
