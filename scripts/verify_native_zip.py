"""Verify the actual native-volume toolchain, optionally with a >4 GiB ZIP64 entry."""

import argparse
import hashlib
import io
import json
import os
import struct
import subprocess
import tempfile
import zipfile
from pathlib import Path

from PySide6.QtCore import QCoreApplication

from dc_modpack_creator.archive import InfoZip, engine_path


def compressed_digest(archive, info, volumes=None):
    volumes = volumes or [Path(archive.filename)]
    remaining = info.compress_size
    digest = hashlib.sha256()
    for index, part in enumerate(volumes[info.volume :]):
        with part.open("rb") as reader:
            if index == 0:
                reader.seek(info.header_offset)
                header = reader.read(30)
                assert header.startswith(b"PK\x03\x04")
                name_size, extra_size = struct.unpack_from("<HH", header, 26)
                reader.seek(name_size + extra_size, 1)
            while remaining:
                chunk = reader.read(min(remaining, 1_048_576))
                if not chunk:
                    break
                digest.update(chunk)
                remaining -= len(chunk)
        if not remaining:
            return digest.hexdigest()
    raise AssertionError("Truncated compressed payload")


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--large-entry", action="store_true")
parser.add_argument(
    "--large-archive", action="store_true", help="Write >4 GiB ZIP; needs ~9 GiB free"
)
args = parser.parse_args()
qt = QCoreApplication([])
cache = Path(".cache/native-zip").resolve()
cache.mkdir(parents=True, exist_ok=True)
root = Path(tempfile.mkdtemp(prefix="verify-中文-", dir=cache))
stage = root / "staging"
stage.mkdir()
source = stage / "complete.zip"
with zipfile.ZipFile(source, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
    archive.writestr("modpack.mrpack", os.urandom(200_000), compress_type=zipfile.ZIP_STORED)
    archive.writestr("中文🧪/文本.txt", b"hello world" * 3000)
    if args.large_entry or args.large_archive:
        block = bytes(1_048_576)
        info = zipfile.ZipInfo("ZIP64/大于4GiB.bin")
        info.compress_type = zipfile.ZIP_STORED if args.large_archive else zipfile.ZIP_DEFLATED
        with archive.open(info, "w", force_zip64=True) as writer:
            for _ in range(4096):
                writer.write(block)
            writer.write(b"end")
splitter = InfoZip(lambda: None, lambda _: None, lambda *_: None)
limit = 1_073_741_824 if args.large_archive else (1_048_576 if args.large_entry else 65_536)
outputs = splitter.split(source, root / "native.zip", limit, [])
assert len(outputs) > 1
assert all(p.stat().st_size <= limit for p in outputs)
subprocess.run(
    [str(engine_path().resolve()), "t", str(outputs[-1])],
    capture_output=True,
    check=True,
    timeout=120,
)
# Read the final volume's central directory and hash entry bytes across their actual volumes.
# Do not use another Info-ZIP conversion as the verification oracle.
metadata = bytearray(outputs[-1].read_bytes())
locator = metadata.rfind(b"PK\x06\x07")
if locator >= 0:
    # Python refuses multi-disk ZIP64 even for metadata-only reads. Normalize the locator
    # in an in-memory view; hashes below still read the original, unmodified volume files.
    assert struct.unpack_from("<I", metadata, locator + 16)[0] == len(outputs)
    struct.pack_into("<I", metadata, locator + 4, 0)
    struct.pack_into("<I", metadata, locator + 16, 1)
with zipfile.ZipFile(source) as before, zipfile.ZipFile(io.BytesIO(metadata)) as after:
    assert before.namelist() == after.namelist()
    for old, new in zip(before.infolist(), after.infolist(), strict=True):
        assert (old.file_size, old.compress_size, old.compress_type, old.CRC) == (
            new.file_size,
            new.compress_size,
            new.compress_type,
            new.CRC,
        )
        assert compressed_digest(before, old) == compressed_digest(after, new, outputs)
    if args.large_entry or args.large_archive:
        assert after.getinfo("ZIP64/大于4GiB.bin").file_size > 4_294_967_295
    if args.large_archive:
        assert source.stat().st_size > 4_294_967_295
result = {
    "large_entry": args.large_entry or args.large_archive,
    "large_archive": args.large_archive,
    "source_archive_bytes": source.stat().st_size,
    "volumes": len(outputs),
    "volume_limit": limit,
    "seven_zip_integrity": True,
    "compressed_payloads_unchanged": True,
    "unicode_names_unchanged": True,
    "methods_crc_sizes_unchanged": True,
    "directory": str(root),
}
(root / "result.json").write_text(
    json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
)
print(json.dumps(result, ensure_ascii=True))
