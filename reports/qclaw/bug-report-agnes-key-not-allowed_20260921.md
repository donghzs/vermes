# Bug 修复：Agnes Key 保存 403「Key 'AGNES_API_KEY' is not allowed」

## 结论
已修复。根因是 `/api/env` PUT 的写白名单（`_allowed_env_keys()`）只含硬编码 key + 动态注册的 service，**遗漏了 `chat.PROVIDERS` 里声明的 provider env_key**（`AGNES_API_KEY`、`SCNET_API_KEY` 等），导致前端 Settings 能渲染/填写这些 key，但保存时被 403 拒绝——「能看不能存」。

## 三层根因（逐条核实属实）

1. **`chat.PROVIDERS` 声明了 env_key，但白名单不读它**：`vermes_cli/blueprints/chat.py:660-730` 的 `PROVIDERS` 表里 `agnes`（`env_key="AGNES_API_KEY"`, free/recommended）、`scnet`（`env_key="SCNET_API_KEY"`, recommended）都有 env_key，但 `config.py` 的 `_allowed_env_keys()` 从未 union 这张表。

2. **动态注册路径失效**（百度搭子报告补充，QClaw 核实属实）：
   - `plugins/image_gen/agnes/__init__.py:347` 有模块级 `register_service("agnes", api_key_env_var="AGNES_API_KEY")`，但该目录**缺 plugin.yaml**，扫描器 `plugins.py:1104` 只识别含 manifest 的子目录 → 该 `__init__.py` 从未被 import → `register_service` 从未执行。
   - `plugins/model-providers/agnes/` 有 plugin.yaml（kind: model-provider）+ `env_vars=("AGNES_API_KEY",)`，但 `plugins.py:992` 对 `kind==model-provider` 直接 `continue` 跳过 import，其 env_vars 只注入读路径（`_inject_profile_env_vars`）不注入写白名单。

3. **scnet 无 model-provider profile**：只在 `chat.PROVIDERS` 与 `PROVIDER_TEMPLATES`（`providers.py:157`，`api_key_env="SCNET_API_KEY"`）存在，动态注册兜不住。

「能看不能存」不对称根源：GET /api/env 读 `OPTIONAL_ENV_VARS`（经 `_inject_profile_env_vars` 注入 provider env_vars）→ AGNES 可见；PUT /api/env 校验 `_allowed_env_keys()` → 硬编码不含 + 动态注册未执行 → 403。

## 修复（方案 3：union 多权威来源，最彻底）

`vermes_cli/blueprints/config.py` 的 `_allowed_env_keys()` 改为 union 5 个来源（每个 try/except 延迟 import 防循环/防断）：
1. 硬编码 `_ENV_WRITE_ALLOWED_KEYS`
2. `chat.PROVIDERS` 所有非空 `env_key`（← 前端 getEnvKey 的映射源）
3. `providers.list_providers()` 所有 `env_vars`（← 读路径来源）
4. `PROVIDER_TEMPLATES` 所有 `api_key_env`（覆盖 scnet 等无 profile 的）
5. `get_registered_services()` 的 fields[].key（← 文献源等业务服务）

## 顺带清理（死代码）

`vermes_cli/web_server.py` 里的第二份 `_ENV_WRITE_ALLOWED_KEYS`（`frozenset`，已与 config.py 漂移）+ 死代码 `set_env_var`（无装饰器、无 add_api_route，/api/env 真正路由由 `blueprints.config.register_to(app)` 注册）+ 死类 `EnvVarUpdate`/`EnvVarDelete` + unused import `save_env_value` 全部删除，堵住「两份白名单漂移」的隐患。

## 验证

- 运行时：`AGNES_API_KEY in _allowed_env_keys()` = True，`SCNET_API_KEY` = True，共 86 key。
- 新增 `tests/vermes_cli/test_env_write_allowlist.py`（6 例，含不变式：chat.PROVIDERS 每个 env_key / 每个 model-provider env_var / 每个 template api_key_env 都必须在白名单）→ 6 passed。
- 回归：`test_env_write_allowlist + test_provider_add_no_wipe + test_runtime_provider_resolution + test_api_key_providers` = **303 passed / 0 failed**。

## 附带发现（pre-existing，未修，留待拍板）

`vermes_cli/web_server.py:1530` 的 `_ws_client_is_allowed()` 引用 `_is_public_bind()`，但**全仓库无该函数定义**（`NameError`，打包产物 dist/ 里同样存在）。导致 `tests/vermes_cli/test_web_server.py` 的 25 个测试失败（TestPtyWebSocket / TestProbeGatewayHealth / TestStatusRemoteGateway / TestNewEndpoints）。经 `git show HEAD:vermes_cli/web_server.py` 确认 **HEAD（本次改动前）就缺定义**，属 pre-existing bug，与本次修复无关。建议另立工单：补 `_is_public_bind()` 定义或改 `_ws_client_is_allowed` 逻辑（判定 --insecure 绑定非 loopback 时的 IP 放行策略）。

## 证据索引
- 白名单：`vermes_cli/blueprints/config.py:350` `_ENV_WRITE_ALLOWED_KEYS`、`:366` `_allowed_env_keys`（已改）、`:812` `set_env_var`
- 死代码（已删）：`vermes_cli/web_server.py` 原 `:1340` `_ENV_WRITE_ALLOWED_KEYS`、`:1367` `set_env_var`、`:936` `EnvVarUpdate`、`:942` `EnvVarDelete`
- 动态注册失效：`plugins/image_gen/agnes/__init__.py:347`（缺 plugin.yaml）、`plugins/model-providers/agnes/__init__.py`（kind 跳过）、`plugins.py:992/1104`
- 前端映射：`Settings.vue` getEnvKey `:786`、保存循环 `:943-970`
- 扫码不写 .env：`quota.py claim` 仅 return agnes 配置
