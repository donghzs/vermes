

# ── P4-①: run_variant_evolution_for_all 仅对 kind: tool 进化 ─────────────

def test_run_for_all_skips_non_tool_processors(tmp_path, monkeypatch):
    """非 tool kind 的 processor 不应被 run_variant_evolution_for_all 处理。"""
    import json
    from agent import variant_ranker
    from agent import variant_store as vs

    procs = tmp_path / "processors"
    procs.mkdir()

    # tool processor — 应被处理
    tool_dir = procs / "tool-ssh"
    tool_dir.mkdir()
    (tool_dir / "processor.yaml").write_text("kind: tool\nname: ssh\ntools: []\n")
    var_dir = tool_dir / "variants"
    var_dir.mkdir()
    (var_dir / "_registry.json").write_text(json.dumps({"variants": []}))

    # prompt_fragment processor — 不应被处理
    prompt_dir = procs / "prompt-ctx"
    prompt_dir.mkdir()
    (prompt_dir / "processor.yaml").write_text("kind: prompt_fragment\nname: ctx\ncontent: test\n")
    var_dir2 = prompt_dir / "variants"
    var_dir2.mkdir()
    (var_dir2 / "_registry.json").write_text(json.dumps({"variants": []}))

    monkeypatch.setattr(vs, "_get_user_dir", lambda: procs)
    monkeypatch.setattr(vs, "_processor_dir", lambda pid: procs / pid)

    call_count = {"n": 0}
    orig = variant_ranker.run_variant_evolution
    def _mock_run(pid, db_path):
        call_count["n"] += 1
        return {"processor_id": pid}
    monkeypatch.setattr(variant_ranker, "run_variant_evolution", _mock_run)

    db = str(tmp_path / "test.db")
    variant_ranker.run_variant_evolution_for_all(db)

    assert call_count["n"] == 1, f"只应处理 tool processor, 实际调用了 {call_count['n']} 次"
