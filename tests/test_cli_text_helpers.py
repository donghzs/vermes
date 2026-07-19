"""C1: cli.py god-file split — text/reasoning helpers relocated to cli_text_helpers.

These tests lock the behavior of the extracted helpers so the relocation
stays behavior-preserving (identical to the code that lived in cli.py).
"""
import cli_text_helpers as h


def test_strip_reasoning_closed_pairs():
    # NOTE: trailing "\s*" after the close tag collapses the space that
    # followed it, so "before <think>..</think> after" -> "before after".
    # This whitespace-collapse is PRE-EXISTING behavior (identical to the
    # code that lived in cli.py); C1 is a behavior-preserving relocation.
    text = "before <think>secret reasoning</think> after"
    assert h._strip_reasoning_tags(text) == "before after"


def test_strip_reasoning_unterminated():
    text = "visible <reasoning>leaked thoughts"
    assert h._strip_reasoning_tags(text) == "visible"


def test_strip_reasoning_orphan_close():
    # Pre-existing artifact: a stray orphan close tag eats the following
    # space, merging the adjacent tokens ("answermore"). Locked here so the
    # relocation stays behavior-preserving; tracked as a known cleanup item.
    text = "answer</think> more"
    assert h._strip_reasoning_tags(text) == "answermore"


def test_strip_reasoning_tool_call_xml():
    # Pre-existing: the "\s*" after the close tag collapses the following
    # space -> "result done" (single space). Behavior-preserving relocation.
    text = "result <tool_call>secret</tool_call> done"
    assert h._strip_reasoning_tags(text) == "result done"


def test_strip_reasoning_keeps_prose_function_mention():
    # boundary-gated: prose mention of <function> is preserved
    text = 'Use the <function> keyword in JS'
    assert "<function>" in h._strip_reasoning_tags(text)


def test_assistant_content_as_text_str():
    assert h._assistant_content_as_text("hi") == "hi"


def test_assistant_content_as_text_list():
    content = [
        {"type": "text", "text": "a"},
        {"type": "tool_use", "text": "b"},
        {"type": "text", "text": "c"},
    ]
    assert h._assistant_content_as_text(content) == "a\nc"


def test_assistant_content_as_text_none():
    assert h._assistant_content_as_text(None) == ""


def test_assistant_copy_text_strips_and_extracts():
    content = [{"type": "text", "text": "answer <think>hidden</think>"}]
    assert h._assistant_copy_text(content) == "answer"
