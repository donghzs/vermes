# 技能索引 P3 · deny-list 细校后收益（2026-09-20）

- 相对 P1 基线：`reports/skills-index-p1-baseline-20260920.md`
- 本切片：P3 名单 + M7 GUI；测量 `reports/skills-index-p3-after.json`
- 门控仍默认 **off**；auto + 代码目录时才降级

## P3 新增 deny-list（保守）

| 类目 | 理由 |
|---|---|
| metaphysics, weather, eco-decision-framework | 纯生活/玄学，编码场景无关 |
| wechat-official-account | 运营/公众号 |
| email-skill, imap-smtp-email | 邮件（与上游 email 同类；原名单键名不同） |
| agnes-multi-modal-integration, agnes-shot-generator, agnes-video-i2v/keyframes/multi-img/t2v | 图像/视频生成（creative 系，散落在顶层目录名） |

**明确保留全量**（编码相邻 / 办公 / 研究）：`research`、`software-*`、`devops`、`github`、`ppt`、`docx`、`reportlab-*`、`mlops*`、`security`、`hardware` 等。

## 收益对比（同一 245 技能库）

| 版本 | deny 类目数 | prompt 节省 | 比例 | index 比例 |
|---|---:|---:|---:|---:|
| P1 | 21 | 5,180 B | 18.41% | 20.2% |
| **P1+P3** | **33** | **6,531 B** | **23.21%** | **25.31%** |
| Δ | +12 | **+1,351 B** | **+4.80 pp** | +5.1 pp |

`skill_lines`：228 → **152**（P1 为 164）。

## M7 GUI

- 位置：设置 → **安全** →「编码场景技能索引」
- 控件：关闭 / 自动
- 写入：`PATCH /api/config` `{ agent: { compact_skill_categories: mode } }`
- `DEFAULT_CONFIG.agent.compact_skill_categories = "off"`
- 实测：PATCH 落盘后 `resolve_compact_skill_categories` 在代码目录返回含 P3 类目的 deny-list（真行为测试）

## P2 结论更新

P3 后 auto 场景 **~23%** 节省，接近规格书上沿；**仍不建议**为字节收益移植 591 行 `coding_context.py`。P2 仅在需要 focus 姿态/工具集折叠时再议。

## 复现

```bash
PYTHONPATH=. .venv/bin/python scripts/measure_skills_index_p1.py \
  --json reports/skills-index-p3-after.json
```
