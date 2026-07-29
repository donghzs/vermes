# -*- coding: utf-8 -*-
"""流式衔接兜底判定测试 — _should_emit_final_fallback (空回复修复).

背景：chat.py 的 SSE 生成器只转发流式 delta，run_conversation 返回的
final_response 从不入队。当最终回答未经过 stream_delta_callback 流出
（非流式回退 / fallback_prior_turn_content / guardrail halt / partial
recovery 等路径），前端收到 0 个 content delta → "⚠ 回复为空"。

本测试锁定兜底判定的边界行为。
"""

from vermes_cli.blueprints.chat import _should_emit_final_fallback


class TestShouldEmitFinalFallback:
    def test_never_streamed_real_final_emits(self):
        """整轮零文本 delta + 真实 final → 必须补发（核心修复场景）。"""
        assert _should_emit_final_fallback("这是最终回答", "") is True

    def test_streamed_final_does_not_reemit(self):
        """final 已通过流式发出 → 不补发（避免重复）。"""
        assert _should_emit_final_fallback("最终回答", "最终回答") is False

    def test_streamed_with_whitespace_diff_does_not_reemit(self):
        """流式文本与 final 仅空白差异（分块换行）→ 归一化后视为已流出。"""
        assert _should_emit_final_fallback(
            "第一段\n\n第二段", "第一段\n\n第二段\n"
        ) is False
        assert _should_emit_final_fallback(
            "a b c", "a\nb\nc"
        ) is False

    def test_midturn_narration_then_unstreamed_final_emits(self):
        """中途工具叙述流出过、但最终回答走了非流式路径 → 仍需补发。"""
        assert _should_emit_final_fallback(
            "任务已完成，共修改 3 个文件。",
            "我先扫描一下目录结构……",
        ) is True

    def test_empty_sentinel_not_emitted(self):
        """"(empty)" 失败哨兵不是回答，不补发（warn 事件负责传达原因）。"""
        assert _should_emit_final_fallback("(empty)", "") is False
        assert _should_emit_final_fallback("  (empty)  ", "") is False

    def test_none_or_blank_final_not_emitted(self):
        assert _should_emit_final_fallback(None, "") is False
        assert _should_emit_final_fallback("", "") is False
        assert _should_emit_final_fallback("   \n  ", "") is False

    def test_final_substring_of_streamed_does_not_reemit(self):
        """partial recovery：较长 final 是已流出文本的子串/前缀 → 已呈现，不补发。"""
        assert _should_emit_final_fallback(
            "结论是该方案可行且风险可控",
            "经过推导结论是该方案可行且风险可控，详见上文",
        ) is False

    def test_short_final_embedded_midstream_emits(self):
        """P2#2 收紧：极短 final 夹在句子中间 → 不能漏发，否则重现空回复。"""
        assert _should_emit_final_fallback(
            "OK", "请稍候…… OK 服务器已响应"
        ) is True

    def test_short_final_as_tail_suffix_emits(self):
        """极短 final 恰为更长流式句子的尾部子串（"一切 ok" 的尾 "ok"）→ 作为独立
        最终回答并未呈现，保守补发（最多一条短重复），不分位置漏判。"""
        assert _should_emit_final_fallback(
            "OK", "处理完成 OK"
        ) is True

    def test_short_final_exact_streamed_does_not_reemit(self):
        """极短 final 且整段流文本精确等于它（确为已流式展示的短答）→ 不补发。"""
        assert _should_emit_final_fallback("OK", "OK") is False
        assert _should_emit_final_fallback("收到", "收到") is False
