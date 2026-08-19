# vermes-mod-freecad-engine

Vermes 的 **FreeCAD 引擎模块**（重资产），作为 `ProToolAdapter` 的首个专业后端，承载
STEP→Body、编辑操作翻译（fillet/draft/pattern/boolean…）、特征树提取等「专业精修」能力，
叠加在既有 build123d 粗模兜底之上。

## 模块性质

- **asset-only**：不含 Python 代码包，仅分发 FreeCAD 引擎 tarball（顶层 `freecadcmd`）。
- 经 P7 catalog 注册（`vermes_cli/modules/catalog.json`），前端模块商店「按需下载」。
- 落到 `~/.vermes/engines/freecad`（与 `engine_setup.get_freecad_engine_dir()` 默认一致），
  由 `FreeCADAdapter.ensure_ready(auto_setup=True)` / `ensure_freecad_ready()` 自动拉起。

## 发布流程（M1-6 真机）

1. 用户机器安装 **FreeCAD 1.0**（freecadcmd 可跑）。
2. 跑 `python3 vermes-mod-freecad-engine/build_engine_asset.py`：
   - 自动定位 freecadcmd → 反推应用根 → 打成 `dist-modules/vermes-mod-freecad-engine-<ver>.tar.gz`
     （顶层 `freecadcmd` 符号链接 + `freecadcmd_app/` 完整应用）。
   - 打印真实 `url`/`sha256`/`size` 资产块。
3. 上传 tarball 到 GitHub Release `v<ver>`。
4. `python3 scripts/build_modules.py --push-catalog`：
   - 检测到已构建的 tarball → 把真实 url/sha256/size 写进 catalog → 推送远程官方 catalog 仓
     （即时触达所有已发布的 Vermes app 版本）。

> 设计文档：`PRO_TOOL_ADAPTER_DESIGN.md` §7（引擎分发）。M1-4 已落地 `ensure_freecad_ready`，
> M1-5 完成模块注册，M1-6 完成真机构建 + 发布 + 端到端 PoC。
