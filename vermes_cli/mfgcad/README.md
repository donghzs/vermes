# mfgcad — 制造业 text-to-CAD 模块

把清华 IEI Lab 开源的 **Multi-Agent-CAD (MAC)** 作为独立引擎嵌入 Vermes，提供
`mfg_text_to_cad` 工具：自然语言需求 → STEP 三维模型 → 双引擎几何/网格校验。
状态化 design session + CHECKPOINT 人工核对，默认**不自动打印**。

---

## 1. 架构：子进程桥接（为什么不是直接 import）

MAC 的重依赖（`build123d` / `cadquery-ocp` / `trimesh` / `langgraph` / `aider` /
`cadpy`）与主 Vermes venv 的 `numpy` 版本锁冲突，纯 pip 解析会得到
`ResolutionImpossible`。因此集成形态为：

```
Vermes 主进程 (主 venv)
  └─ mfg_text_to_cad (vermes_cli/mfgcad/tools.py)
       └─ subprocess → engine/.venv/bin/python  (MAC 独立 venv)
            └─ engine/run_mac.py  →  multi_agent_cad 4-Agent 流水线
```

* 引擎代码随模块 vendored 在 `engine/`（含 `multi_agent_cad/`、`packages/cadpy`、
  `legacy_refs/check_mesh.py`），不依赖外部 MAC 仓库。
* 进度/调试全部走 **STDERR**；**STDOUT 只吐最终 1 行 JSON**（见 §4 契约）。
* 引擎 venv 与主 venv 完全隔离，重装主 venv 不影响 MAC。

---

## 2. 引擎 venv 配置

`tools.py::_resolve_engine()` 按以下顺序定位 python：

1. 环境变量 `MFG_CAD_ENGINE_PY` —— 显式指向任一已装好 MAC 依赖的 python。
2. 否则 `vermes_cli/mfgcad/engine/.venv/bin/python` —— 需自行创建。

推荐做法（开发期直接复用 mac_poc 的 venv，零重装）：

```bash
# ~/.zshrc 或 Vermes 启动环境
export MFG_CAD_ENGINE_PY="$HOME/WorkBuddy/2026-07-16-12-56-04/mac_poc/.venv/bin/python"
```

若要从零建引擎 venv（在 `engine/` 下）：

```bash
cd vermes_cli/mfgcad/engine
python3 -m venv .venv && .venv/bin/pip install -r <MAC requirements>
# 需含: build123d cadquery-ocp trimesh langgraph aider cadpy(本地)
```

引擎入口位置本身可用 `MFG_CAD_ENGINE_DIR` 覆盖（默认即 `engine/`）。

---

## 3. LLM key 配置

MAC 的 `nodes._llm_client()` 读 `DASHSCOPE_API_KEY`（POC 借名塞 DeepSeek key，
命名是历史遗留）。桥接时 `tools.py` 统一透传以下变量给子进程：

优先级：`MFG_CAD_API_KEY` > `DEEPSEEK_API_KEY` > `DASHSCOPE_API_KEY`。

```bash
export MFG_CAD_API_KEY="<valid DeepSeek / OpenAI-compatible key>"
```

> ⚠️ zshrc 里旧的 `DEEPSEEK_API_KEY` 已失效（401）。当前有效 key 在
> `~/.vermes/.env`（`DEEPSEEK_API_KEY`）。确保启动 Vermes 的环境能读到它，
> 或显式设 `MFG_CAD_API_KEY`。缺失 key 时工具直接返回 `❌ 未配置 ...`。

config.py 中 `DS_BASE_URL="https://api.deepseek.com/v1"`，四阶段均用
`deepseek-chat`。

---

## 4. 工具契约

### mfg_text_to_cad 入参
| 参数 | 类型 | 说明 |
|------|------|------|
| `request` | str (必填) | 自然语言建模需求，含关键尺寸。如「外径 60mm 壁厚 3mm 高 100mm 的笔筒」。 |
| `session_id` | str | 状态化会话 ID，留空自动生成（`auto_<ts>`）。续作/核对时用同一 ID。 |
| `output_dir` | str | STEP/STL 输出目录，默认 `~/.vermes/mfgcad/output/<session_id>`。 |
| `workflow_id` | `original`\|`aider` | 引擎工作流。original=确定性 build123d 翻译优先（推荐）；aider=Aider 优先。 |
| `checkpoint` | bool | `true`=生成候选 STEP 后暂停人工核对，不自动定稿；`false`=直接定稿。默认 false。 |

### engine/run_mac.py 输出（STDOUT 末行 JSON）
```json
{
  "ok": true,
  "error_type": "NONE",
  "step_path": "/abs/.../temp_output_xxx.step",
  "stl_path": "/abs/.../temp_output_xxx.stl",
  "volume_mm3": 53721.23,
  "qa": {"passed": 5, "failed": 0, "issues": []},
  "iterations": 3,
  "message": "✅ 建模成功；STEP: ...；体积: 53721.23 mm³（53.721 cm³）"
}
```
退出码 0 = JSON 已吐（调用方仍需查 `ok`）；2 = 建模失败；其余 = 崩溃。

### 状态落盘
每次调用落 `~/.vermes/mfgcad/sessions/<session_id>/session.json`（含 request /
ok / step_path / volume / qa / error_type / ts），便于续作与排查。失败也记。

---

## 5. 已落地的桥接修复（重要）

这些修复在 `run_mac.py` 侧完成，**不改动 MAC 源码**：

1. **跨请求缓存污染（实锤 bug）**：MAC `nodes.py` 把
   `cad_brief.json` / `architect_plan.json` 缓存在**固定文件名**的共享
   `pipeline_cache` 目录。第二次不同请求会 `[CACHE] Loaded cad_brief.json —
   skipping Spec Planner`，复用首请求的零件方案（实测：六角螺母返回了笔筒体积
   53721.23）。修复：按 `sha1(request)[:16]` 隔离 `_nodes._CACHE_DIR`，
   相同请求仍可复用、不同请求永不碰撞。
2. **输出残骸复用**：引擎节点把产物落在 `Path.cwd()`，重跑同目录会复用旧
   STEP。修复：运行前 purge `temp_output_*.{step,stl}`、`temp_design_*.py`、
   `temp_measurements_*.json`。

---

## 6. 已知限制 (KNOWN ISSUES)

### CHAMFER_FAILED — build123d 0.11.1 倒角 API 差异
带倒角的零件（如六角螺母的 `chamfer()`）在 `build123d` 0.11.1 下偶发
`CHAMFER_FAILED: 'Edge' object has no attribute 'center_point'`。**STEP 几何本身
正确**（六角螺母体积实算 401.18 mm³ ≈ 理论 402 ✓），但 autonomous loop 把该异常
判为 `fatal`，阻断定稿。

* 笔筒（无倒角）全绿，不受影响。
* 根因：MAC 源码按旧版 build123d 的 chamfer API 编写，与 0.11.1 不兼容。
* 建议（引擎侧，非本模块）：降级锁定 build123d 版本，或把 chamfer 调用改为
  0.11.1 兼容写法。本模块暂不屏蔽该 fatal（避免掩盖真实几何错误）。

### 其余待办（低优先级，用户已降级）
* `#392` 轻量 DFM 闸门 `mfg_dfm_prescreen` + `mfg_printer` mock 接口 —— 延后。
* 跨请求缓存隔离逻辑可考虑上游反馈给 MAC 源码。

---

## 7. 模块注册链路

`mfgcad` 通过 `agent/module_loader.py` 接入，无需 `module.yaml`：

* `_BUILTIN_MODULES["mfgcad"] = "vermes_cli/mfgcad"`
* `discover_builtin_modules()` 走 `_synth_mfgcad_manifest()`（manifest 设
  `backend_entry="tools.py"`、`tools_entry="tools.py"`、`frontend_entry=None`，
  确保 `register_modules` 在 `if mod is None: continue` 之后调用
  `mod.register_tools(host_api)`）。
* `register_tools()` 把 `mfg_text_to_cad` 注册进全局 `tools.registry`（与
  ScholarForge 同机制）。

验证（主 venv 干净导入 + 注册）：
```bash
python -c "from agent.module_loader import discover_builtin_modules as d; \
print([m.name for m in d() if m.name=='mfgcad'])"
python -c "from tools.registry import registry; \
from vermes_cli.mfgcad.tools import register_tools; \
register_tools(); print('mfg_text_to_cad' in registry._tools)"
```

## 8. 端到端验证记录
* 笔筒（H=100mm）：体积 53721.23 mm³ = π·(30²−27²)·100 ≈ 53721 ✓，qa 全绿。
* 六角螺母：缓存修复后体积 401.18 mm³（理论≈402 ✓），仅 CHAMFER_FAILED 判
  fatal（§6 已知限制），几何正确。
* 纪律：所有体积均用 build123d `import_step` 实算核验，不轻信引擎返回串。
