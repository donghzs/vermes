# Spike 验证报告 · CLI-Anything 作为 L3 操作层（2026-08-21）

> 对应：`UNIVERSAL_OPERATION_LAYER_DESIGN.md` §8.1 spike + §13 结论。
> 方法：trust-but-verify，全部为沙箱实跑真实数据，不凭记忆。
> 环境：macOS 沙箱，managed Python 3.13.12，**未安装 FreeCAD**（几何执行归用户机器 B 桶）。

---

## 1. 仓库成熟度（GitHub API 实测 → 解除 §9 #1 维护风险）

| 项 | 实测值 |
|---|---|
| full_name | `HKUDS/CLI-Anything` |
| 协议 | Apache-2.0 |
| 创建 | 2026-03-08 |
| **最后 push** | **2026-08-13（距 spike 仅 8 天）** |
| stars / forks | 47.9k / 4.4k |
| open issues | 92 |
| archived | 否 |

→ **维护依赖风险实测不成立**：项目极活跃，非停更轮子。
→ FreeCAD 绑定：新闻 **2026-03-25 "updated for v1.1"** → 与当前 FreeCAD 1.1.3 同代，API 兼容差距小。

## 2. 安装与枚举（沙箱实跑）

- `pip install -e .`（路径 `freecad/agent-harness`）仅依赖 **click / prompt-toolkit / wcwidth**，**不依赖 FreeCAD** → 沙箱可装可枚举。
- 源码 `@command` 装饰器计数：**277** 个。
- 薄插槽 `SoftwareAdapter.discover_tools()` 实跑内省出 **273** 个可注册 leaf 工具（≥ 宣传的 258 / 17–18 组）。
- 顶层 **20 个 group**（18 工作台 + repl / session 两个管理组）。

## 3. --json 稳定性（解除 §9 #1b 风险）

- `--json` 是全局一级 flag（`freecad_cli.py:247`），`output()` 统一 JSON 序列化。
- 实测 `cli-anything-freecad --json document new --name spike` 返回结构化 JSON（含 `parts_count` 等字段）。
- → L2「自动注册」可行，schema 稳定可内省。

## 4. 真实端到端链路（沙箱，状态层）

```
$ cli-anything-freecad --json document new --name spike --output /tmp/p.json
{ "name": "spike", "units": "mm", "version": "1.0", ... }
$ cli-anything-freecad --json --project /tmp/p.json part add box --name b
{ "id": 1, "name": "b", "type": "box",
  "params": {"length": 10.0, "width": 10.0, "height": 10.0}, ... }
```

- **边界（实测）**：`freecadcmd` 调用仅出现在 `preview.py` / `motion.py`（渲染）；状态层（document / part-spec / sketch-spec / session …）是纯 Python JSON，**不需 FreeCAD**。
- 需 FreeCAD 的：几何内核真实执行 + `export` 到 .FCStd/.STEP/.STL + 渲染预览 → 归用户机器（B 桶）。

## 5. 薄插槽代码（已写 + 实跑）

- 新增 `vermes_cli/adapters/software_adapter.py`（L2 唯一新增代码面）+ `__init__.py`。
- `discover_tools()` 内省 CLI → 273 工具；`register()` 实跑**成功注册 273 个工具进 `tools/registry`**（toolset=`freecad_adapter`），无报错。
- `invoke()` 走 `--json` subprocess；v1 强制 `domain_vocab={}`（§5.3 护栏）。

## 6. 阈值判定（§8.1 step5）

- 沙箱无 FreeCAD，**无法测量「≥80% 命令端到端执行」阈值**（执行需目标软件）。
- 但用户最担心的两项风险已实测解除：① 成熟度（8 天前仍 push）；② --json 稳定性（一等公民，已验证返回 JSON）。
- 薄插槽 + 状态层已在沙箱端到端跑通 → **杠杆成立，L2 保持「薄」**。
- 唯一未验：几何内核真实执行（fillet/draft/export 真实文件）。**建议作为 B 桶最终门禁在用户机器完成**；若届时 <80% 命令可用，按 §9 #2 回退路径处理。

## 7. 结论

spike 达成预期：
- L3 轮子（CLI-Anything）成熟可用、FreeCAD v1.1 绑定、277 命令；
- L2 薄插槽自动注册 273 工具、状态层沙箱可跑；
- 手搓 18 工具进入**冻结**（见设计文档 §6）。

下一步：B 桶几何执行验证（你机器）+ 第二域选型（§8.2）。
