# Harness mutation probes (E-P0 实证资产)

可选反向验证脚本。运行方式（在仓库根，修复后的工作树上）：

1. **E-P0-1**：临时去掉 `agent/tool_executor.py` 无 spinner 路径的 `max_attempts=_max_att`，
   跑 `tests/harness/test_circuit_max_attempts_wiring.py` → `test_every_invoke_with_retry_passes_max_attempts` 应失败（约 L1231）。
2. **E-P0-2**：把某处 `_harness_fail_log("result_validator", ...)` 改回 `pass  # H3.1 永不阻塞`，
   跑 `tests/harness/test_harness_fail_observability.py` → `test_no_silent_pass_on_result_validator` 应失败。

恢复改动后测试必须全绿。契约型测试本身已抗回归，本目录仅作独立复跑资产。
