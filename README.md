# DreamCity 整合包导出工具

导出PCL / Modrinth 整合包的实用工具。

按工作目录 `导出/` 中的 TOML 配置，导出内嵌全部文件的 mrpack 或 PCL 完整 ZIP整合包。

## 运行

```powershell
uv sync
uv run dc-modpack-creator
```

- 无界面预检：`uv run dc-modpack-creator --check 导出/任务.toml`
- 无界面导出：`uv run dc-modpack-creator --export 导出/任务.toml`

## 文档

配置和使用说明见 [技术文档](docs/技术文档.md) 和 [导出配置示例](src/dc_modpack_creator/assets/templates/导出配置示例.toml)。

## 打包EXE

运行 `build_exe.bat`，产物位于 `dist/`
