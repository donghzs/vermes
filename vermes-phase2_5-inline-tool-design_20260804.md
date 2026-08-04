# Vermes 乐高式自进化 · Phase 2.5 设计：handler.inline 纯声明式工具执行

> 范围：Phase 2 的延迟半段——让**没有任何 Python 后端**的新工具也能**纯 YAML 落地**。Phase 2（`handler.ref` 薄桥接）解决了"声明/治理/生命周期外置"；Phase 2.5 补上"执行体也能外置（声明式）"，闭合一等公民闭环。
> 依赖：Phase 1（`449cb64fb`+`767eb2d60`）+ Phase 2（`7d2d8de86`，运行态待 build）。
> 关联：`vermes-phase1-schema-design_20260803.md`、`vermes-phase2-tool-processor-design_20260804.md`。

---

## 0. 为什么做 Phase 2.5

Phase 2 把工具外置成 YAML，但**执行体仍是 Python**（`handler.ref` 指向一个 `(args, **kw)` 函数）。这要求：
- 新增工具仍要写一个 Python 文件 + `registry.register`；
- 用户/AEGIS 只能 override 既有工具的**声明/治理**，不能凭空造一个全新工具。

Phase 2.5 让 `handler.inline` 承载**声明式执行规格**（shell 命令 / HTTP 调用），loader 据此生成一个标准 `(args, **kw)` handler 闭包注册进 `ToolRegistry`。**registry / dispatch / 治理 / hooks 全部零改动**——和 Phase 2 同构的薄桥接，只是这次桥的另一头不是既有 Python 函数，而是 loader 现造的执行器。

收益：小白用户/小白开箱即用的 Vermes 也能"写一个 YAML 就多一个工具"，无需碰 Python。

---

## 1. 现状事实（设计依据，非假设）

### 1.1 handler 调用契约（实测）
- `tools/registry.py:dispatch` → `entry.handler(args, **kwargs)`；`model_tools.handle_function_call` 经 `registry.dispatch(name, function_args, task_id=..., user_task=..., enabled_tools=..., ...)` 传入。
- 即 handler 契约 = `handler(args: dict, **kw)`，`kw` 含 `task_id`/`user_task`/`enabled_tools`/`session_key` 等**调用方注入的杂项**，handler 必须忽略它们（现有 `_handle_*` 全部如此）。
- handler 返回值 = **JSON 字符串**（统一用 `tools.registry.tool_result` / `tool_error` 包装）。

→ inline handler 就是 `def handler(args, **kw): ...`，与 `_handle_read_file` 同契约，注册进 registry 后下游透明。

### 1.2 执行底座（可复用，不重造）
- `tools/code_execution_tool.py` 已有 `subprocess` 调用范式（`subprocess.Popen`/`run`，stdout/stderr PIPE，超时 `TimeoutExpired` 兜底，`get_subprocess_home()` 定 CWD）。inline shell 复用其超时/CWD 范式（不强制 gVisor sandbox，见 §2-D 安全分层）。
- HTTP：用标准库 `urllib.request`（零依赖）或 `httpx`（若已装）；2.5 用 `urllib` 避免新增依赖。

### 1.3 Phase 2 loader 已具备的接缝
- `agent/tool_processor_loader.py` 的 `register_tool_processors()` 当前 handler 解析：`handler.ref` → 解析为可调用；否则沿用 `existing.handler`（override）；否则跳过。
- `_parse_tool_yaml` 已解析 `handler.ref`。**仅需扩展**：支持 `handler.inline` 分支，并在解析期校验规格。

---

## 2. 设计决策（主推方案）

### 决策 A — 两种声明式后端：`shell` + `http`
2.5 支持两类 inline 执行（覆盖绝大多数"无 Python 也能做"的工具场景）：
- **`shell`**：执行一条命令（argv 列表，非 shell 字符串）。等同 RCE，**最高风险**。
- **`http`**：调用一个 HTTP 端点（GET/POST，query/headers/body 模板化）。中风险（可外发数据）。

> 推迟：`python` 脚本后端（本质是 `execute_code`）——它和现有 `code_execution_tool` 重叠，且任意代码执行风险更高，2.5 不做；需要时在 P3+ 复用 `execute_code` 底座。

### 决策 B — loader 现造 handler 闭包（registry/dispatch 零改动）
`agent/tool_processor_loader.py` 新增 `_make_inline_handler(spec)`：
```python
def _make_inline_handler(spec: dict) -> Callable:
    kind = spec["type"]   # "shell" | "http"
    if kind == "shell":
        argv_tmpl = spec["command"]          # List[str]，含 {arg} 占位
        timeout = spec.get("timeout", 30)
        def _run(args, **kw):
            argv = [_subst(tok, args) for tok in argv_tmpl]   # 逐元素替换，绝不拼成 shell 串
            return _run_shell(argv, timeout)
        return _run
    if kind == "http":
        method = spec.get("method", "GET")
        url = spec["url"]
        timeout = spec.get("timeout", 30)
        def _run(args, **kw):
            q = {k: _subst(v, args) for k, v in (spec.get("query") or {}).items()}
            hdrs = {k: _subst(v, args) for k, v in (spec.get("headers") or {}).items()}
            body = _subst(spec.get("body", ""), args) if spec.get("body") else None
            return _run_http(method, url, q, hdrs, body, timeout)
        return _run
    raise ValueError(f"unknown inline type '{kind}'")
```
`register_tool_processors()` 的 handler 解析扩展为三分支：
```python
if p.handler_ref:
    handler = _resolve_handler_ref(p.handler_ref)          # Phase 2 既有
elif p.inline_spec:
    handler = _make_inline_handler(p.inline_spec)          # 2.5 新增
elif existing is not None:
    handler = existing.handler                            # override 保留
else:
    skip
```
→ 对 registry/dispatch/get_definitions/valid_tool_names **零改动**。inline 工具注册后和 Python 工具完全等价。

### 决策 C — 参数替换：逐元素、非字符串插值（防注入）
- `shell`：`command` 是 **argv 列表**（如 `["curl","-s","https://x/lookup","-d","{query}"]`）。每个元素的 `{arg}` 占位被对应参数值**逐元素替换**，绝不把整串拼进 `shell=True`。
- `handler(args, **kw)` 里 `subprocess` 用 `shell=False` + argv 列表 → **彻底杜绝 shell 注入**（哪怕参数含 `; rm -rf /` 也只是 argv 的一个普通元素，不会被 shell 解释）。
- `http`：`query`/`headers`/`body` 的 `{arg}` 同样逐字段替换；URL 有两种来源（均受 scheme 白名单约束，仅 http/https）：
  - **静态 base URL**（`url:` 字段，如 `https://api.ipify.org`）+ 模板化 `query` —— 安全默认，URL 不被参数插值。
  - **整 URL 来自参数**（`url_arg: <param>` 字段）—— 泛型 fetcher 逃逸口（让 `web_get` 这类工具真正"传一个 URL 就抓一个网页"）。解析后**强制 scheme 白名单**（拒绝 `file://`/`gopher://`/`ftp://` 等），这是 §2-D http 行的 scheme 白名单在运行期的落地；SSRF 到内网仍是任何 fetch 工具固有风险，超出本阶段范围（建议配合静态 base URL 或出口防火墙）。

> **实现注记（相对原稿的小扩展）**：原稿 §2-C 只写了"静态 base URL + 模板化 query"。为使内置 `web_get` 成为真正的泛型抓取工具（P2.5 的卖点："写一个 YAML 就多一个工具，含抓网页"），实现新增 `url_arg` 整 URL 来自参数模式，且严格 scheme 白名单。安全姿态（默认关 shell、scheme 白名单、钳 L2）与拍板完全一致的。

### 决策 D — 安全分层（inline 比 handler.ref 危险，必须更硬）
inline 的"定义"本身就是执行体——**改 inline YAML = 直接改 agent 会执行什么**，提权面比改 `handler.ref`（指向固定既有函数）更大。因此：

| 控制 | shell inline | http inline |
|---|---|---|
| 启用开关 | `approvals.allow_inline_shell`（**默认 False**） | 无需独立开关（默认允许，但见下） |
| risk_tier 下限 | **钳到 L2，不可降级**（L0/L1 一律抬到 L2） | **钳到 L2，不可降级** |
| 执行方式 | `subprocess` `shell=False` + argv 列表 + 超时 30s | `urllib` + 超时 30s + scheme 白名单(http/https) |
| 输入 | argv 列表（禁 shell 串） | URL 静态 + query 模板化 |
| 输出 | stdout 截断到 `max_result_size_chars` | body 截断到 `max_result_size_chars` |

- **钳档理由**：Phase 1 把 processor 钳到 L1（L1 不弹窗）；但 inline shell/http 是"改定义=改执行"，必须**强制 L2**（每次改写定义都要人工确认），与"工具默认 L2"一致且更严——这是 2.5 相对 Phase 2 的**新增治理 hardening**，不是放宽。
- `allow_inline_shell` 默认 False：小白开箱即用时 shell inline 完全不可用，只有显式开启（且仍需 L2 审批每处改写）才能用。http inline 风险较低，默认可用但同样钳 L2。
- 解析期校验：inline 规格非法（缺 `command`/`url`、未知 `type`、`allow_inline_shell=False` 时遇到 shell inline）→ **error 跳过该 processor（绝不静默）**，与 Phase 2 的 `handler.ref` 错误处理同严格度。

### 决策 E — 校验在 loader 解析期完成
`_parse_tool_yaml` 解析 `handler.inline`：
- 必须含 `type` ∈ {shell, http}；
- `shell` 必须含 `command`（argv 列表，且每个元素为 str）；
- `http` 必须含 `url`（scheme ∈ http/https）、可选 `method`/`query`/`headers`/`body`；
- 非法 → 记 error + 返回 None（跳过）。
- `risk_tier` 解析后若 < L2 且为 inline → loader 在 `register_tool_processors` 里钳到 L2（记 warning）。

### 决策 F — 治理/可用性/hooks 全部复用 Phase 2（无新机制）
- `governance.risk_tier`（钳 L2）、`availability.requires_env`、`lifecycle.hooks`（已注册进 plugin manager、dispatch 触发）、`compute_manifest_hash`（Phase 3 变体隔离身份键）——**一行不改，直接复用**。
- 改 inline YAML（热路径）触发 Phase 1 已建好的 `processor_hot_path` 审批（`_resolve_processor_tier` + `processor_modify_always_confirm`）。

### 决策 G — 内置示范
- **http inline 示范**：随 2.5 落一个内置 `web_get` inline 工具（`kind: tool` + `handler.inline.type: http`），作为"纯 YAML 工具"模板与治理锚点。
- **shell inline 不内置**：shell inline 等同 RCE，内置示范无意义且危险；shell inline 仅允许用户在 hot path 显式开启 `allow_inline_shell` 后自定义。

---

## 3. 迁移路径（非破坏性）
1. **83 个 Python 工具**：不变。Phase 2 的 `handler.ref` 路径继续服务它们。
2. **Phase 2 的 `handler.ref` 工具**：不变。inline 是并列的第三种 handler 来源。
3. **全新工具**：现在可纯 YAML 声明（`kind: tool` + `handler.inline`），零 Python。
4. **内置 inline 示范**：`web_get`（http）随 2.5 落地；shell inline 用户自定。

---

## 4. 拍板点（请逐条裁决）

- **P2.5-① 后端范围**：采纳 `shell` + `http` 两种？还是本阶段只要更安全的 `http`？
  - 推荐：`shell` + `http` 都做，但 `shell` 默认关闭（决策 D）。`python` 后端推迟。

- **P2.5-② shell 默认开关**：`approvals.allow_inline_shell` 默认 **False**（小白安全）？还是默认 True（开箱即用但更危险）？
  - 推荐：**False**。Vermes 是小白开箱即用，默认不应暴露 RCE 式能力；高级用户显式开。

- **P2.5-③ inline risk_tier 下限**：shell/http inline 都**钳到 L2 不可降级**？还是允许 L1？
  - 推荐：**钳 L2**。改 inline 定义=改执行体，必须每次人工确认。这是 2.5 新增 hardening。

- **P2.5-④ 参数替换方式**：shell 用 **argv 列表（禁 shell 串）** 防注入？还是允许 `shell: true` 字符串？
  - 推荐：**argv 列表 + `shell=False`**。字符串拼接是注入温床，2.5 直接禁止 `shell: true`。

- **P2.5-⑤ 内置示范**：落 `web_get`（http inline）作为模板？还是暂不内置、纯用户自定？
  - 推荐：落 `web_get`（http，安全后端）作为示范与治理锚点；shell 不内置。

---

## 5. 与 Phase 1/2 反模式对照（必查项）

| 踩过的坑 | Phase 2.5 对应防范 |
|---|---|
| Phase 1-A：字段写了零消费方 | inline 生成的 handler 必须真注册进 registry 并被 dispatch 调用；加 no-mock 测试断言"inline 工具 dispatch 后返回真实执行结果" |
| 测试 mock 掉唯一会出错的那行 | inline 执行路径（subprocess/urllib 真实调用）必须有不 mock 回归：用无害命令（`echo`/`python -c "print"`）或本地 httpbin 自起 server 验证真实往返 |
| `except:pass` 吞错 | inline 规格非法 / `allow_inline_shell=False` 遇 shell → error 跳过 + 记日志，绝不宽 except |
| 自证式治理 | inline 的 risk_tier 同样走 `processor_hot_path` 判定，且额外钳 L2（loader 强制，不读自身声明定档） |
| 注入 | shell 用 argv 列表 + `shell=False`，参数逐元素替换，杜绝 `; rm -rf` 类注入 |
| Phase 2 handler.ref 审计补正 | inline 是 handler.ref 的"无 Python"对偶；同样要求 `(args,**kw)` 契约（生成的闭包满足），且非法规格 error 跳过 |

---

## 6. 验证清单（Phase 2.5 收口标准）

- [ ] `handler.inline.type: http` 工具真实注册进 `ToolRegistry`；`get_definitions` 返回其 schema；dispatch 后返回真实 HTTP 响应（no-mock 往返，本地 server）。
- [ ] `handler.inline.type: shell`（`allow_inline_shell=true`）dispatch 后执行真实命令（如 `echo`），返回截断后的 stdout；参数含 `; rm -rf` 仅作 argv 元素、不被 shell 解释。
- [ ] `allow_inline_shell=false` 时遇到 shell inline → **error 跳过**（不注册），http inline 不受影响。
- [ ] inline 工具 `risk_tier` 声明 L0/L1 → loader **钳到 L2**（记 warning），不降级。
- [ ] inline 规格非法（缺 command/url、未知 type）→ error 跳过（非静默）。
- [ ] 改 inline YAML（热路径）→ 触发 Phase 1 审批（L2 人工确认）。
- [ ] `lifecycle.hooks` 对 inline 工具同样在 dispatch 触发（复用 Phase 2 注册）。
- [ ] 内置 `web_get`（http inline）示范随构建落地；冻结包验证 `handler.inline` 段存在且非 mock 执行成功。
- [ ] `test_tool_processors.py` 新增 inline 用例（http 真实往返 / shell argv 防注入 / 钳档 / 开关 / 非法跳过）全绿。

---

## 7. 后续路线
- **Phase 3**：变体隔离（依赖 Phase 1/2 共用的 `governance.hash` 作为身份键）。
- **Phase 4**：闭环串联 + 模型-Harness 联合进化（GRPO）。
- **P2.5+**：`python` 脚本后端（复用 `execute_code` 底座，按需）。
