# max_tokens 配置说明

## 什么是 max_tokens？

`max_tokens` 控制模型单次回复的最大 token 数量。

## 影响

| 设置 | 好处 | 影响 |
|------|------|------|
| **太小**（如 4096） | 节省 tokens、降低成本 | 长任务输出被截断，子 agent 无法完成复杂任务 |
| **太大**（如 16384） | 长任务能完整输出 | 浪费 tokens、成本增加 |
| **合适** | 平衡输出长度和成本 | — |

## 默认值

Vermes 会根据模型类型自动设置默认值：

| 模型类型 | 默认 max_tokens |
|----------|-----------------|
| 推理模型（o1/o3/deepseek-reasoner） | 16384 |
| Claude/DeepSeek/MiMo/Qwen/Gemini | 8192 |
| GPT-4/3.5 | 4096 |

## 自定义配置

在 `~/.vermes/config.yaml` 的 `model` 部分添加 `max_tokens`：

```yaml
model:
  provider: xiaomi
  default: mimo-v2.5-pro
  max_tokens: 8192  # 自定义 max_tokens，优先级最高
```

## 建议

- **日常使用**：保持默认值（8192）即可
- **长任务**：如果经常遇到输出被截断，可以增大到 12288 或 16384
- **节省成本**：如果想节省 tokens，可以减小到 4096

## 验证配置

配置后重启 Vermes，查看日志中的 `[Stream]` 行，会显示实际使用的 max_tokens 值。
