# 上游意图级巡检 · 安全/正确性修复候选（2026-09-21）

> 上游 `v2026.9.14` → `5a0c2fb89e`，扫描 3154 commits（上限 --max 8000），命中候选 220 条。
> 命令行：`python3 scripts/upstream_watch.py intake --since v2026.9.14 --max 8000 --upstream-repo ~/.hermes/hermes-agent`
> 只出清单不自动改代码；人月更，采纳→改代码+契约测试+TAKEALONG §7c，拒绝也记一行。
> **「建议」列是脚本预判，最终以人工 ledger 为准。**

## 0. 校准摘要

| 口径 | 数量 |
|---|---|
| 显式 GHSA | 1 |
| fix(security) 标签 | 6 |
| 安全语义候选 | 220 |
| 有 Vermes 对应物 | 21 |

## 1. 候选清单

| 信号 | hash | 日期 | 主题 | Vermes 对应物 | 建议 |
|---|---|---|---|---|---|
| GHSA | `9345c67854f6` | 2026-09-18 | fix(webhook): per-route toolsets bind to the authenticated route, not  | gateway/platforms/webhook.py | 移植/评估 |
| fix(security) | `d966b34cc36f` | 2026-08-24 | fix(security): gateway lifecycle guard recognises Windows command spel | — | 人工判 |
| fix(security) | `1c0d95badbac` | 2026-09-14 | fix(security): write-deny HERMES_HOME secret stores, keep control file | agent/file_safety.py | 移植/评估 |
| fix(security) | `e7cd1848c9bb` | 2026-09-14 | fix(security): deny writes to read-blocked Hermes credential stores | agent/file_safety.py | 移植/评估 |
| fix(security) | `56d2438a45fa` | 2026-09-14 | fix(security): secure HERMES_HOME when only an ancestor path is a syml | — | 人工判 |
| fix(security) | `3933fdf63b48` | 2026-09-17 | fix(security): route remote-party-supplied URL fetches through the SSR | — | 人工判 |
| fix(security) | `fbc5df3c413f` | 2026-09-17 | fix(security): allow emoji variation selectors | — | 人工判 |
| 安全语义 | `e218f3641c5e` | 2026-06-19 | fix(anthropic): preserve docs URL in OAuth sanitizer | — | 人工判 |
| 安全语义 | `8a55373dbf42` | 2026-07-13 | fix(whatsapp): authorize first-contact LID senders | — | 人工判 |
| 安全语义 | `a6e934e0fdb6` | 2026-08-16 | feat(gateway): 'decline' unauthorized-DM behavior — one-time polite de | — | 人工判 |
| 安全语义 | `06a3a98751e1` | 2026-08-19 | fix: gate dynamic shell words in approval checks | — | 人工判 |
| 安全语义 | `2db1f25f2ca9` | 2026-08-19 | fix(pairing): accept spaced approval codes | — | 人工判 |
| 安全语义 | `5349aa609d33` | 2026-08-20 | Inspired by Copilot CLI: /context now lists each context file with loa | — | 人工判 |
| 安全语义 | `7eed615690ec` | 2026-08-23 | fix(mcp): send a User-Agent on SDK-built OAuth discovery/registration  | — | 人工判 |
| 安全语义 | `65625dfe874f` | 2026-08-23 | fix(computer_use): attach element_token when the driver schema accepts | — | 人工判 |
| 安全语义 | `fe68349cd64f` | 2026-08-25 | fix(threat-scanner): close reviewer-noted ssh_access_write bypass shap | — | 人工判 |
| 安全语义 | `d93f74b7589d` | 2026-08-26 | fix(auth): isolate dashboard provider registration by launch home | — | 人工判 |
| 安全语义 | `46503f16727d` | 2026-09-04 | fix(auth): isolate Codex singleton sync by principal | — | 人工判 |
| 安全语义 | `23afade67bf9` | 2026-09-04 | fix(credential-pool): seed env-source entries not in registry tuple | — | 人工判 |
| 安全语义 | `d968db3c4b9f` | 2026-09-05 | fix(mcp): propagate an extended connect_timeout into config for OAuth  | — | 人工判 |
| 安全语义 | `c0362da9a6e9` | 2026-09-06 | fix(cron): redact secrets from delivery content before sending | — | 人工判 |
| 安全语义 | `ea870b4d3eac` | 2026-09-07 | fix(approval): anchor launchctl lookaheads to prevent GIL starvation | — | 人工判 |
| 安全语义 | `1a6503a5203c` | 2026-09-08 | fix(mcp): thread RFC 9207 iss through every OAuth callback relay | — | 人工判 |
| 安全语义 | `56ea9bab92cc` | 2026-09-09 | feat(desktop): stack approvals and advance exact requests with Enter | — | 人工判 |
| 安全语义 | `dd68d175674d` | 2026-09-09 | feat(approval): prepare GUI terminal asks before ordered execution | tools/approval.py | 移植/评估 |
| 安全语义 | `a12b3c7aa3dd` | 2026-09-09 | refactor(agent): import the transform bypass from its defining module, | — | 人工判 |
| 安全语义 | `47ee79a6473e` | 2026-09-09 | fix(mcp-oauth): serialize token-store access across processes | — | 人工判 |
| 安全语义 | `a0810c9cc93b` | 2026-09-12 | fix(mcp-oauth): fence one refresh generation across the consuming POST | — | 人工判 |
| 安全语义 | `6d4ca15d38fe` | 2026-09-13 | fix(agent): mirror Claude Code OAuth refresh into the macOS Keychain ( | — | 人工判 |
| 安全语义 | `0d6fec59d40e` | 2026-09-13 | fix(secret_scope): memoise the shared .env tokenizer once per file cha | — | 人工判 |
| 安全语义 | `6201a8236fd5` | 2026-09-13 | fix(desktop): drop unsaved credential edits when the settings target c | — | 人工判 |
| 安全语义 | `b415a18153f9` | 2026-09-13 | plugin catalog: bump hermes-newswire pin (authorship fix rebased histo | — | 人工判 |
| 安全语义 | `f5907fd052d2` | 2026-09-14 | fix(redact): config.yaml backup copies under HERMES_HOME are secret-be | — | 人工判 |
| 安全语义 | `d205cef41886` | 2026-09-14 | fix(redact): mask assignments in secret-bearing file reads | — | 人工判 |
| 安全语义 | `95987fb85a2d` | 2026-09-14 | fix: bypass the proxy on loopback HTTP CDP discovery and keep NO_PROXY | — | 人工判 |
| 安全语义 | `8f6f92d901c7` | 2026-09-14 | fix(browser): one loopback proxy-bypass helper covers child envs and i | — | 人工判 |
| 安全语义 | `00a68a8768ec` | 2026-09-14 | fix(browser): bypass proxies for loopback hosts in browser child envs | — | 人工判 |
| 安全语义 | `05e7e891751c` | 2026-09-14 | fix(approval): honor pattern-key allowlists when unattended | tools/approval.py | 移植/评估 |
| 安全语义 | `a9a8a3fa2e63` | 2026-09-14 | fix(config): `hermes config get` masks credentials on every path; `--r | — | 人工判 |
| 安全语义 | `a9db7ed5a8a5` | 2026-09-14 | fix(cli): mask credentials in approvals suggest proposals | — | 人工判 |
| 安全语义 | `417b707f5967` | 2026-09-14 | fix: refresh the credential that 401'd on the Codex /usage retry | — | 人工判 |
| 安全语义 | `70ff4863d7f8` | 2026-09-14 | fix(aux): read HERMES_CODEX_BASE_URL through the profile secret scope | — | 人工判 |
| 安全语义 | `7e06625687a2` | 2026-09-14 | fix(auth): avoid auto provider resolution recursion | — | 人工判 |
| 安全语义 | `d4e0df1e8d4f` | 2026-09-14 | fix: keep the OpenCode keyless Authorization blank on the async aux cl | — | 人工判 |
| 安全语义 | `e77060e081fb` | 2026-09-14 | fix(agent): keyless OpenCode placeholder blanks Authorization under th | — | 人工判 |
| 安全语义 | `64a4687153ef` | 2026-09-14 | fix(agent): fall through to the configured chain when an auth-refresh  | — | 人工判 |
| 安全语义 | `23bdb546cc3e` | 2026-09-14 | feat(auth): accept top-level allow_all_users as a known config.yaml ke | — | 人工判 |
| 安全语义 | `71d229f5cd2e` | 2026-09-14 | fix(cron): the silence instruction names [SILENT] as an untranslatable | — | 人工判 |
| 安全语义 | `7c6b29e1e4a4` | 2026-09-14 | fix(gateway): 401 hint cites hermes auth add, not the removed hermes l | — | 人工判 |
| 安全语义 | `da1fb702c361` | 2026-09-14 | fix(updater): import probe children never resolve external secret sour | — | 人工判 |
| 安全语义 | `04fcf9159c18` | 2026-09-14 | fix: keep api_server approval bridge and cron self-scheduling after pr | tools/approval.py | 移植/评估 |
| 安全语义 | `b6b7802447f4` | 2026-09-14 | fix(approval): every unattended context clears leaked presence vars | cron/scheduler.py | 移植/评估 |
| 安全语义 | `2a630671d7bc` | 2026-09-14 | fix(approval): cron context is never interactive, even with leaked pre | tools/approval.py | 移植/评估 |
| 安全语义 | `5ca670b398c4` | 2026-09-14 | fix(dashboard): profile-routed routers run under the profile's secret  | — | 人工判 |
| 安全语义 | `b00ebf62105b` | 2026-09-14 | fix(skills): live-dashboard credits the human author and drops the pro | — | 人工判 |
| 安全语义 | `34a45b35e5fa` | 2026-09-14 | fix(update): also ignore the flat-install config/credential/profile ro | — | 人工判 |
| 安全语义 | `d5430fb0c1fa` | 2026-09-14 | refactor(matrix): classify sync auth errors on errcode/http_status onl | — | 人工判 |
| 安全语义 | `60262f71bdf8` | 2026-09-14 | refactor(mcp-oauth): split the refresh fence into acquire/release func | — | 人工判 |
| 安全语义 | `f07ea70de702` | 2026-09-14 | fix(mcp-oauth): only treat contention errnos as "a peer holds the fenc | — | 人工判 |
| 安全语义 | `8933d355a96d` | 2026-09-14 | fix(mcp-oauth): share one rotated-candidate rule between adopt and rel | — | 人工判 |
| 安全语义 | `76e2e8fb6427` | 2026-09-14 | refactor(mcp-oauth): drop the per-access token-store lock now that the | — | 人工判 |
| 安全语义 | `1a1345aba457` | 2026-09-14 | fix(mcp-oauth): make the refresh fence async and skip the POST after a | — | 人工判 |
| 安全语义 | `09a7c297eca0` | 2026-09-14 | fix(tools): uncached check_fn probes classify UnscopedSecretError from | — | 人工判 |
| 安全语义 | `3275ca88ec62` | 2026-09-14 | fix(image_gen): Codex-auth images use the native images endpoints, no  | — | 人工判 |
| 安全语义 | `e383c28d2ffc` | 2026-09-14 | fix(approval): judge shell quoting on the raw command, not the escape- | — | 人工判 |
| 安全语义 | `2dfb795cb78f` | 2026-09-15 | fix(approval): undelivered or unanswered CLI approval prompts are not  | tools/approval.py | 移植/评估 |
| 安全语义 | `a457e91a5042` | 2026-09-15 | fix(secrets): preserve OP_CONFIG_DIR for 1Password | — | 人工判 |
| 安全语义 | `c001881d8548` | 2026-09-15 | fix(mcp): route /v1/runs MCP trust-gate consent through the run's appr | — | 人工判 |
| 安全语义 | `341f8b4d9354` | 2026-09-15 | fix: cap, loopback-bypass and share the MCP proxy mounts | — | 人工判 |
| 安全语义 | `e133f3f607a6` | 2026-09-15 | fix: device OAuth login scans every advertised authorization server | — | 人工判 |
| 安全语义 | `60f436b5f6fb` | 2026-09-15 | fix: redact '/'- and '~'-led secrets whose segments cannot be a path | — | 人工判 |
| 安全语义 | `93a0ec705a11` | 2026-09-15 | fix(redact): anchor the path/var exemption so leading-/ and $ secrets  | — | 人工判 |
| 安全语义 | `79bf0be53b24` | 2026-09-15 | fix(slack): resolve a cold channel's workspace from the sole authentic | — | 人工判 |
| 安全语义 | `ac829e8dee9b` | 2026-09-15 | fix(web): gh auth refresh waits out a probe that started before it was | — | 人工判 |
| 安全语义 | `460768056d54` | 2026-09-15 | fix(web): bound and deduplicate gh auth probes | — | 人工判 |
| 安全语义 | `4179c5a99cf3` | 2026-09-15 | fix: describe protected_instruction_files as an always-ask approval ga | — | 人工判 |
| 安全语义 | `1e2cb5797362` | 2026-09-15 | fix(approval): session teardown and interrupted leaders withdraw the p | tools/approval.py | 移植/评估 |
| 安全语义 | `6332216384b7` | 2026-09-15 | fix(approval): withdrawn gateway approval prompts no longer read as a  | tools/approval.py | 移植/评估 |
| 安全语义 | `54d7f7559015` | 2026-09-15 | fix(bot-mode): a pending command approval is not a failed DM delivery | — | 人工判 |
| 安全语义 | `4432bccea89c` | 2026-09-15 | chore(contributors): map co-author emails for TheNeuralVault and gongy | — | 人工判 |
| 安全语义 | `996f7bc5638c` | 2026-09-15 | feat(credential-pool): numbered env siblings (KEY_2, KEY_3, …) seed ro | — | 人工判 |
| 安全语义 | `40b47b84e728` | 2026-09-15 | chore(contributors): map Cr4ckMe email for the #20151 co-author credit | — | 人工判 |
| 安全语义 | `e47b14f960fa` | 2026-09-15 | chore(contributors): map wangtaotaotao95 author email | — | 人工判 |
| 安全语义 | `a1cbed3592d9` | 2026-09-15 | chore: map atmaksri co-author email for contributor audit | — | 人工判 |
| 安全语义 | `cfd752e6f7a0` | 2026-09-15 | fix(sessions): token-accounting guard stamps the agent's real source;  | — | 人工判 |
| 安全语义 | `f55d4f674748` | 2026-09-15 | fix(aux): bare-custom AuthError yields no custom endpoint instead of a | — | 人工判 |
| 安全语义 | `879d65ec7846` | 2026-09-15 | fix(runtime): fail fast for bare custom credentials | — | 人工判 |
| 安全语义 | `423bc7e4e45e` | 2026-09-15 | fix(credential-pool): hydrate on-disk env rows on the openrouter branc | — | 人工判 |
| 安全语义 | `d84ece48b855` | 2026-09-15 | fix(mcp): Figma OAuth login completes despite the omitted iss paramete | — | 人工判 |
| 安全语义 | `95dba8d9a5e9` | 2026-09-15 | fix(free-tier): classify_mint_exception keeps an uncoded AuthError's w | — | 人工判 |
| 安全语义 | `16def7b8cc1b` | 2026-09-15 | fix(free-tier): the desktop renders a free-tier refusal as its own car | — | 人工判 |
| 安全语义 | `20d80bb36f14` | 2026-09-15 | fix(mcp): refresh expired cold-loaded OAuth tokens | — | 人工判 |
| 安全语义 | `2a6a9b0427c5` | 2026-09-15 | fix(auth): retry Codex usage after 401 | — | 人工判 |
| 安全语义 | `569b4242a3b4` | 2026-09-15 | fix(auth): a Portal-returned inference host is accepted only when the  | — | 人工判 |
| 安全语义 | `aaed2a238f45` | 2026-09-15 | fix(auth): routing overrides fail closed when a multi-profile call has | — | 人工判 |
| 安全语义 | `288fdc1a4c8c` | 2026-09-15 | fix(auth): accept a non-production Portal's own inference host when th | — | 人工判 |
| 安全语义 | `9366f595330d` | 2026-09-15 | fix(auth): read the Portal env override through the profile scope | — | 人工判 |
| 安全语义 | `9bb985c5d43d` | 2026-09-15 | fix(desktop): OAuth REST preflight gets its own dial budget | — | 人工判 |
| 安全语义 | `8bdac1b17a79` | 2026-09-15 | fix(desktop): omit a null iss from the oauth.callback relay; hoist the | — | 人工判 |
| 安全语义 | `1c243f86de60` | 2026-09-15 | fix(tui_gateway): relay RFC 9207 iss through the oauth.callback RPC | — | 人工判 |
| 安全语义 | `cedf4a3d7867` | 2026-09-15 | fix(secret-scope): compose the managed .env into every profile secret  | — | 人工判 |
| 安全语义 | `840c00c124be` | 2026-09-15 | refactor(cron): reuse _install_fire_secret_scope for the external-work | cron/scheduler.py | 移植/评估 |
| 安全语义 | `dcdbcb8a2b14` | 2026-09-15 | fix(env-loader): split source_supplied_names() out of secret_source_na | tools/environments/local.py | 移植/评估 |
| 安全语义 | `f4bf786c57b6` | 2026-09-15 | fix(mcp-oauth): return a verdict from disk-pair install, count a missi | — | 人工判 |
| 安全语义 | `2e89c5da48de` | 2026-09-15 | fix(mcp-oauth): keep the refresh-fence sidecar when removing token sta | — | 人工判 |
| 安全语义 | `73f808e47f9d` | 2026-09-15 | fix(plugins): only mark a timed-out hook worker abandoned while it sti | — | 人工判 |
| 安全语义 | `0b78a26903fb` | 2026-09-16 | feat(plugin-catalog): add hermes-security-audit | — | 人工判 |
| 安全语义 | `2afb405337c3` | 2026-09-16 | fix(approval): withdraw the queue entry when no client can answer; com | tools/approval.py | 移植/评估 |
| 安全语义 | `f9d178f78ed8` | 2026-09-16 | fix(tui_gateway): old app builds no longer stall the agent on clarify/ | — | 人工判 |
| 安全语义 | `428056e7b98a` | 2026-09-16 | fix(auth): Claude Code dead refresh token is reported once; CLAUDE_CON | — | 人工判 |
| 安全语义 | `d6add1605953` | 2026-09-16 | fix(auth): dead OAuth logins are reported once and leave rotation; hin | — | 人工判 |
| 安全语义 | `ba2bcfa6e790` | 2026-09-16 | fix(gateway): auth-fallback test stub accepts the target_model kwarg t | — | 人工判 |
| 安全语义 | `931b5ff9e7b1` | 2026-09-16 | fix(opencode): every credential-resolution surface keys off the model  | 红线区 | 红线只读 |
| 安全语义 | `3fe9e8ff0fe9` | 2026-09-16 | fix(aux): cached auxiliary client follows the pooled credential after  | — | 人工判 |
| 安全语义 | `bec892459453` | 2026-09-16 | fix(update): hand-off carries sibling snapshots + Windows pause token  | — | 人工判 |
| 安全语义 | `140d12545a1e` | 2026-09-16 | fix(desktop): settle approval and tool-row layout together | — | 人工判 |
| 安全语义 | `9e232a7ff5c1` | 2026-09-16 | fix(approval): resolve the plugin classification lazily inside the gua | tools/approval.py | 移植/评估 |
| 安全语义 | `3fc1a184f8c0` | 2026-09-16 | fix(approval): honor plugin container guard classification | tools/approval.py | 移植/评估 |
| 安全语义 | `4a78de28053f` | 2026-09-16 | fix(tools): avoid duplicate sandbox env type dispatch | — | 人工判 |
| 安全语义 | `cf7895c1bd9e` | 2026-09-16 | fix(tests): sandbox a dev shell whose HERMES_HOME is the Windows platf | — | 人工判 |
| 安全语义 | `9bcba9bef75e` | 2026-09-16 | fix(auth): a terminally rejected OAuth refresh token is logged at WARN | — | 人工判 |
| 安全语义 | `e79ba2d7f2ec` | 2026-09-16 | fix(desktop): retry the update check anonymously when the env token is | — | 人工判 |
| 安全语义 | `c7d4d3280039` | 2026-09-16 | fix(desktop): env-only GitHub token for the update check, honest rate- | — | 人工判 |
| 安全语义 | `44af26851390` | 2026-09-16 | feat(desktop): authenticate the update check's GitHub API calls | — | 人工判 |
| 安全语义 | `437d62b86d82` | 2026-09-16 | chore: map contributor email for hanamizuki (co-author credit on #1124 | — | 人工判 |
| 安全语义 | `dd2141b928ee` | 2026-09-16 | fix: cap only generic sample tokens, one step, last; bump plugin-guard | — | 人工判 |
| 安全语义 | `4037b009b669` | 2026-09-16 | fix(plugins): demote main-guard sample tokens | — | 人工判 |
| 安全语义 | `c358a6fba0a5` | 2026-09-16 | fix(cli): resolve runtime credentials for the model the CLI will send | — | 人工判 |
| 安全语义 | `713a9377ccd3` | 2026-09-16 | fix: only call an auth DB backup "locked" when it made no progress | — | 人工判 |
| 安全语义 | `864e9dccfc8e` | 2026-09-16 | fix(browser): real-profile snapshot names the locked auth databases an | — | 人工判 |
| 安全语义 | `97066a0eb5c7` | 2026-09-16 | fix(gateway): Weixin media sends honour iLink ret and re-send without  | 红线区 | 红线只读 |
| 安全语义 | `2995063bcdc8` | 2026-09-16 | fix(gateway): Weixin tokenless re-send no longer consumes the retry bu | 红线区 | 红线只读 |
| 安全语义 | `46ab37ce3512` | 2026-09-16 | fix(gateway): retry Weixin prepare failures without token | 红线区 | 红线只读 |
| 安全语义 | `e166791f9ffb` | 2026-09-16 | fix(mcp): treat a non-object client.json as corrupt instead of crashin | — | 人工判 |
| 安全语义 | `f8f89b20d273` | 2026-09-16 | fix(mcp): skip malformed cached OAuth redirect_uris instead of crashin | — | 人工判 |
| 安全语义 | `1b722403b971` | 2026-09-16 | fix(desktop): fold accented Latin, keep NFC tokens and cut CJK profile | — | 人工判 |
| 安全语义 | `8c8003f80b52` | 2026-09-16 | fix(auth): credential audit reads profile config.yaml through read_use | — | 人工判 |
| 安全语义 | `93889b770da3` | 2026-09-16 | fix(auth): named profiles no longer inherit the root profile's auth.js | — | 人工判 |
| 安全语义 | `03b0c7947262` | 2026-09-16 | fix(desktop): send Cloudflare Access headers on OAuth login (#110987) | — | 人工判 |
| 安全语义 | `cb95c278af54` | 2026-09-16 | feat(desktop): keep live approval stacks stable during execution | — | 人工判 |
| 安全语义 | `42e933b411af` | 2026-09-16 | fix(approval): prepare batches for desktop session sources | — | 人工判 |
| 安全语义 | `db54f5448d7c` | 2026-09-16 | fix(kanban): worker fingerprint carries a boot witness; an uncaptured  | — | 人工判 |
| 安全语义 | `86097433893b` | 2026-09-16 | fix(serve): launch-profile scope decided at entry; send keeps scope au | — | 人工判 |
| 安全语义 | `3fe8e5e443d1` | 2026-09-16 | fix(multiplex): routed children never inherit launch-only credentials, | tools/environments/local.py | 移植/评估 |
| 安全语义 | `0aec64c874f8` | 2026-09-16 | fix(skills): honor profile-scoped readiness secrets | — | 人工判 |
| 安全语义 | `98ad0d8148f3` | 2026-09-17 | fix(image-gen): record token-billed OpenRouter calls to session usage | — | 人工判 |
| 安全语义 | `d34423e4def6` | 2026-09-17 | fix(mcp): connect resolves credentials under the connection owner's pr | — | 人工判 |
| 安全语义 | `ac70c4f0427c` | 2026-09-17 | fix(auxiliary): honor task fallback after explicit auth failure | — | 人工判 |
| 安全语义 | `7c478ac257a3` | 2026-09-17 | fix(file-safety): anchor credential write guards to every home a write | agent/file_safety.py | 移植/评估 |
| 安全语义 | `3b0aae77ee47` | 2026-09-17 | fix(tts): prefer explicit XAI_API_KEY over subscription OAuth in strea | — | 人工判 |
| 安全语义 | `a51c65994476` | 2026-09-17 | fix(aux): a callable key_cmd credential survives every custom-provider | — | 人工判 |
| 安全语义 | `2ba6fcd750ce` | 2026-09-17 | fix: keep API-key xai auto routes off the xai-oauth refresh row | — | 人工判 |
| 安全语义 | `c739ab15f6d3` | 2026-09-17 | fix: refresh xai-oauth on auto-routed auxiliary 403 bad-credentials | — | 人工判 |
| 安全语义 | `78756858b784` | 2026-09-17 | fix: only WARN about an unavailable Nous auxiliary client when a Nous  | — | 人工判 |
| 安全语义 | `1539a1a9dabd` | 2026-09-17 | fix: goal loop names the Nous auth failure instead of an opaque judge  | — | 人工判 |
| 安全语义 | `a3967ddbc006` | 2026-09-17 | fix(tools): sandbox read paths recognise SQLite sidecars as binary | — | 人工判 |
| 安全语义 | `cbe2413b54c1` | 2026-09-17 | fix(aux): max_tokens rung retries even when the wire kwargs no longer  | — | 人工判 |
| 安全语义 | `6b4aaaf2e29c` | 2026-09-17 | refactor(config): load_env is a thin wrapper over the memoised tokeniz | — | 人工判 |
| 安全语义 | `3711f36ee9fd` | 2026-09-17 | refactor(secret_scope): key the .env memo on utils.file_signature | — | 人工判 |
| 安全语义 | `4eff83cdecca` | 2026-09-17 | fix(approval): deobfuscate every command word in one detection variant | — | 人工判 |
| 安全语义 | `48566fd9422d` | 2026-09-17 | fix(docs): clarify bot credential isolation | — | 人工判 |
| 安全语义 | `c96987a51987` | 2026-09-17 | chore(contributors): map KoNit-K for the bot-mode credential docs salv | — | 人工判 |
| 安全语义 | `48257c6e3143` | 2026-09-17 | fix(plugin-catalog): repin stewardship security fix | — | 人工判 |
| 安全语义 | `564687b113fa` | 2026-09-17 | fix(nous): classify the desktop's escaped-exception card with the requ | — | 人工判 |
| 安全语义 | `2eb5395d3fe4` | 2026-09-17 | fix(launchd): park EX_CONFIG token conflicts instead of KeepAlive-loop | — | 人工判 |
| 安全语义 | `b9ba9d503f75` | 2026-09-18 | fix(acp): approval prompts wait approvals.timeout instead of a hardcod | — | 人工判 |
| 安全语义 | `6c7f693473f3` | 2026-09-18 | feat(auth): opt out of borrowing Codex CLI / Claude Code logins (auth. | — | 人工判 |
| 安全语义 | `255b4fd9da31` | 2026-09-18 | fix(image-gen): record token usage as soon as the billed HTTP 200 land | — | 人工判 |
| 安全语义 | `6babdc96b800` | 2026-09-18 | fix(image-gen): every token-billed image backend records session usage | — | 人工判 |
| 安全语义 | `3ed40556cea1` | 2026-09-18 | fix(profiles): a child spawned for another profile no longer inherits  | tools/environments/local.py | 移植/评估 |
| 安全语义 | `2c78f88666c0` | 2026-09-18 | Merge pull request #115285 from NousResearch/security/webhook-route-to | — | 人工判 |
| 安全语义 | `75b62813942e` | 2026-09-18 | fix(anthropic): OAuth slug rewrite skips paths, repo slugs and mailbox | — | 人工判 |
| 安全语义 | `7530f4038358` | 2026-09-18 | fix(errors): auth refusals from a non-stock route name the contacted h | — | 人工判 |
| 安全语义 | `668e505ac7b7` | 2026-09-18 | fix(mcp): OAuth discovery/registration carry a User-Agent; cancelled l | — | 人工判 |
| 安全语义 | `cab9a2795479` | 2026-09-18 | fix(tests): cover the 'no credentials were found' permanent-failure ma | — | 人工判 |
| 安全语义 | `a0562d17d851` | 2026-09-18 | fix(auth): missing-credential hints name the real env var or the OAuth | — | 人工判 |
| 安全语义 | `8669e47a60c7` | 2026-09-18 | fix(picker): curated fallback for cold OAuth rows; Z.AI failed-probe n | — | 人工判 |
| 安全语义 | `f81ac9a45b6c` | 2026-09-18 | fix(desktop): approval card hands focus back to the pane the user was  | — | 人工判 |
| 安全语义 | `f685eab0d31a` | 2026-09-18 | fix(tests): gate test_mcp_oauth_metadata on the MCP SDK | — | 人工判 |
| 安全语义 | `4c5a70065c8b` | 2026-09-18 | fix: arm the pool revert only when the benched credential outranks the | — | 人工判 |
| 安全语义 | `92bb5b92b853` | 2026-09-18 | fix: revert a quota-benched credential through the pool, not a private | — | 人工判 |
| 安全语义 | `dad20814e44b` | 2026-09-18 | fix(dashboard): bind the profile secret scope around PUT /api/profiles | — | 人工判 |
| 安全语义 | `7529ff0de1a5` | 2026-09-18 | fix(mcp): discover binds the owner's secret scope before ${VAR} interp | — | 人工判 |
| 安全语义 | `e63da95318bc` | 2026-09-18 | fix(cli): auth.json-only login with a benched credential is explained, | — | 人工判 |
| 安全语义 | `d98b1480b984` | 2026-09-18 | fix(cli): a benched or signed-out credential prints its reason instead | — | 人工判 |
| 安全语义 | `5dbeaf2366bf` | 2026-09-18 | fix(auth): Nous unusable-JWT-without-refresh-token is a terminal pool  | — | 人工判 |
| 安全语义 | `89c6a8a14586` | 2026-09-18 | fix(auth): Nous "not logged in" refresh failures leave rotation instea | — | 人工判 |
| 安全语义 | `e6d4dcf91790` | 2026-09-18 | fix(aux): explicit-provider auth fallback stays on the task chain thro | — | 人工判 |
| 安全语义 | `e4557d1febb7` | 2026-09-18 | fix(tests): gate only the two AuthorizationCodeResult.code assertions  | — | 人工判 |
| 安全语义 | `964fbaae2cb2` | 2026-09-18 | fix(auth): status snapshot peeks the credential pool instead of leasin | — | 人工判 |
| 安全语义 | `21642218445e` | 2026-09-18 | fix(send): name the default-root gateway and external secret sources i | — | 人工判 |
| 安全语义 | `547fff75003a` | 2026-09-18 | fix(tools): scope-only passthrough overlay raises instead of silently  | tools/environments/local.py | 移植/评估 |
| 安全语义 | `802a9975d283` | 2026-09-18 | fix(cron): no_agent scripts get the owning profile's declared secret,  | tools/env_passthrough.py | 移植/评估 |
| 安全语义 | `c4857de87c57` | 2026-09-18 | fix(plugins): fold the non-credential failure case into the fetch/pull | — | 人工判 |
| 安全语义 | `9b55e7e6ac25` | 2026-09-18 | fix(plugins): attach stored git credentials only after an anonymous re | — | 人工判 |
| 安全语义 | `c082b013957a` | 2026-09-18 | fix(plugins): clone anonymously first; only attach stored credential o | — | 人工判 |
| 安全语义 | `69deb4337259` | 2026-09-18 | fix(auth): Codex credential and relogin copy name the failing profile' | — | 人工判 |
| 安全语义 | `6b457953f61c` | 2026-09-18 | fix(auth): a revoked Codex grant names openai-codex, the failing profi | — | 人工判 |
| 安全语义 | `c61bbc24618c` | 2026-09-18 | fix(gateway): persist the authored text, not the Discord triggering no | — | 人工判 |
| 安全语义 | `df44a6e4df96` | 2026-09-18 | chore: map contributor email for @sanastasiou (Co-authored-by on #1148 | — | 人工判 |
| 安全语义 | `27a30d8515d3` | 2026-09-18 | fix(setup): xAI TTS wizard checks XAI_API_KEY before OAuth to match ru | — | 人工判 |
| 安全语义 | `ca3c42562761` | 2026-09-18 | fix(tts): pin _xai_requirements to key-first credential resolution in  | — | 人工判 |
| 安全语义 | `9dd36c56cf66` | 2026-09-18 | fix(mcp): remote-session OAuth hint names the configured redirect_host | — | 人工判 |
| 安全语义 | `b0cd35e25953` | 2026-09-18 | fix(mcp): dashboard/Desktop Authorize honours a pre-registered client' | — | 人工判 |
| 安全语义 | `c553df915c19` | 2026-09-18 | fix(mcp): Asana catalog installs a working V2 pre-registered OAuth cli | — | 人工判 |
| 安全语义 | `7b4e052bda91` | 2026-09-18 | chore(contributors): map Sasni, fotedev, whyyagswhy author emails | — | 人工判 |
| 安全语义 | `edd9fd66a4be` | 2026-09-18 | fix(telegram): slash-confirm card budgets the MarkdownV2 rendering; ap | 红线区 | 红线只读 |
| 安全语义 | `b502504b744d` | 2026-09-18 | fix(telegram): approval button card fits the 4096-char cap after HTML  | 红线区 | 红线只读 |
| 安全语义 | `c1eb1592e8fa` | 2026-09-18 | fix(discord): keep the exec-approval reason bounded after dropping the | — | 人工判 |
| 安全语义 | `5e0a1852f505` | 2026-09-18 | fix(discord): avoid duplicate exec approval details | — | 人工判 |
| 安全语义 | `4a15e049fd7a` | 2026-09-18 | fix(cli): re-resolve reasoning after every startup model move, not jus | — | 人工判 |
| 安全语义 | `8e1b4216b5b1` | 2026-09-18 | fix(cli): startup auth fallback re-resolves reasoning effort through t | — | 人工判 |
| 安全语义 | `a764a5442dea` | 2026-09-18 | fix(mcp): dashboard OAuth failures keep their first cause on every sur | — | 人工判 |
| 安全语义 | `8e3d540534c1` | 2026-09-18 | fix(mcp): propagate dashboard OAuth flow errors to the callback waiter | — | 人工判 |
| 安全语义 | `227384e332ed` | 2026-09-18 | fix(auth): drop dead code from the Codex login retry; trim to two inva | — | 人工判 |
| 安全语义 | `14b50a763495` | 2026-09-18 | chore(contributors): map yahuo email for co-author credit | — | 人工判 |
| 安全语义 | `045eb4436346` | 2026-09-18 | refactor(desktop): let the foreground rearm own the reauth clear | — | 人工判 |
| 安全语义 | `1655edd3e91d` | 2026-09-18 | fix(desktop): retain auth rejection across socket cleanup | — | 人工判 |
| 安全语义 | `14a346345486` | 2026-09-19 | fix(anthropic): mirror only the Keychain item that held the spent pair | — | 人工判 |
| 安全语义 | `bf5a6f6ae6b4` | 2026-09-19 | fix(anthropic): mirror the Keychain item through `security -i` with a  | — | 人工判 |

## 2. 处置纪律

1. 采纳：改代码 + 契约测试 + TAKEALONG_LEDGER §7c 登记（含来源 commit/落点/验收/人时）。
2. 拒绝：也在 §7c 记一行（防重复考古）。
3. 红线：只输出思路参考，默认不直接搬。
4. 「无对应物」≠「无需」：可能是同域不同文件名（分叉太深），需人工对照 upstream diff 判定。
