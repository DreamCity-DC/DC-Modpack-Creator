from __future__ import annotations

import json
from pathlib import Path

from .errors import ExportError


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(value, dict):
            raise ValueError("版本信息必须为 JSON 对象")
        for field in ("id", "type", "mainClass", "clientVersion"):
            if value.get(field) is not None and not isinstance(value[field], str):
                raise ValueError(f"{field} 必须是字符串")
        return value
    except (OSError, UnicodeError, ValueError) as exc:
        raise ExportError(f"无法读取版本 JSON {path}：{exc}") from exc


def resolve_dependencies(instance: Path) -> dict[str, str]:
    path = instance / f"{instance.name}.json"
    mc_versions: set[str] = set()
    loaders: dict[str, set[str]] = {}
    data = read_json(path)
    client_version = data.get("clientVersion")
    if client_version is not None and (not isinstance(client_version, str) or not client_version):
        raise ExportError(f"无效的 clientVersion：{path}")
    if isinstance(client_version, str) and client_version:
        mc_versions.add(client_version)
    downloads = data.get("downloads", {})
    if not isinstance(downloads, dict):
        raise ExportError(f"无效的 downloads：{path}")
    # The official vanilla manifest supplies id, type, mainClass and client download.
    if not client_version and data.get("type") in {"release", "snapshot", "old_beta", "old_alpha"}:
        if (
            data.get("mainClass")
            in {
                "net.minecraft.client.main.Main",
                "net.minecraft.launchwrapper.Launch",
            }
            or "client" in downloads
        ):
            if isinstance(data.get("id"), str) and data["id"]:
                mc_versions.add(data["id"])
    main = str(data.get("mainClass", "")).lower()
    if any(x in main for x in ("quilt", "liteloader", "optifine")):
        raise ExportError(f"未支持的加载器：{data.get('mainClass')}")
    libs = data.get("libraries", [])
    if not isinstance(libs, list):
        raise ExportError(f"libraries 应为数组：{path}")
    for library in libs:
        if not isinstance(library, dict) or not isinstance(library.get("name"), str):
            raise ExportError(f"无效的库声明：{path}")
        parts = library["name"].split(":")
        if len(parts) < 3:
            raise ExportError(f"无效的库坐标：{library['name']}")
        group, artifact, version = parts[:3]
        if any(x in group.lower() for x in ("quiltmc", "liteloader", "optifine")):
            raise ExportError(f"未支持的加载器：{library['name']}")
        key = ""
        if (group, artifact) == ("net.fabricmc", "fabric-loader"):
            key = "fabric-loader"
        elif group == "net.minecraftforge" and artifact in {"forge", "fmlloader"}:
            key = "forge"
            if "-" in version:
                mc, version = version.split("-", 1)
                mc_versions.add(mc)
        elif group == "net.neoforged" and artifact in {"neoforge", "forge"}:
            key = "neoforge"
            if artifact == "forge" and "-" in version:
                mc, version = version.split("-", 1)
                mc_versions.add(mc)
        if key:
            if not version or "$" in version:
                raise ExportError(f"无法确定加载器版本：{library['name']}")
            loaders.setdefault(key, set()).add(version)
    if len(mc_versions) != 1:
        raise ExportError(
            f"无法唯一确定 Minecraft 版本：{', '.join(sorted(mc_versions)) or '无版本依据'}"
        )
    if len(loaders) > 1 or any(len(versions) != 1 for versions in loaders.values()):
        raise ExportError(f"加载器版本冲突：{loaders}")
    main_class = str(data.get("mainClass", ""))
    if (
        not loaders
        and main_class
        and main_class
        not in {"net.minecraft.client.main.Main", "net.minecraft.launchwrapper.Launch"}
    ):
        raise ExportError(f"未支持或无法识别的加载器：{main_class}")
    return {"minecraft": next(iter(mc_versions)), **{k: next(iter(v)) for k, v in loaders.items()}}
