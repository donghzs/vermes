# 文件上传全链路修复分工

## Hermes 负责（前端，5项）

| # | 任务 | 文件 | 说明 |
|---|------|------|------|
| 1 | 拖拽上传 | ChatInput.vue | 加 drop/dragover/dragenter 事件 |
| 2 | 多轮图片上下文 | chat.js | apiMessages 不再剥离 data:image，保留图片在历史中 |
| 5 | 请求体大小限制 | ChatInput.vue | 总附件 ≤ 50MB，单文件 ≤ 20MB |
| 8 | accept 过滤补全 | ChatInput.vue | 加 .docx/.xlsx/.pptx/.zip/.yaml/.toml/.sh/.ts/.java 等 |
| 10 | 视频文件识别 | chat-storage.js | fileToBase64 区分 video 类型 |

## QClaw 负责（后端，4项）

| # | 任务 | 文件 | 说明 |
|---|------|------|------|
| 3 | 二进制文件处理 | web_server.py | PDF 用 pymupdf 提取文本，其他二进制只报元信息 |
| 4 | run_conversation 类型 | run_agent.py | str → Union[str, list] |
| 6 | 日志中间件优化 | web_server.py | 大请求体跳过完整日志 |
| 7 | 服务端文件校验 | web_server.py | 白名单 MIME 类型 + 大小限制 |

## 可选（共同评估）

| # | 任务 | 说明 |
|---|------|------|
| 9 | 视频支持 | 需要讨论方案（转码/截帧/直接传？） |

## 约束
- 各自独立完成，不交叉修改同一文件
- 前端改完构建+同步 web_dist
- 后端改完验证 import 无报错
