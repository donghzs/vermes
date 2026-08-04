"""P3-⑪ L1 自动抽取规则引擎测试。

纪律（用户强调）：抽取类改动必须**中英双用例**；中文断言不得只验非空，
必须断言具体子串/具体值。验证尺子：单测断言（extract_facts 返回值 /
L1 表行数 + 内容子串）。
"""
import sqlite3


def _count_l1_auto(db):
    conn = sqlite3.connect(str(db))
    n = conn.execute("SELECT COUNT(*) FROM memories WHERE source='l1_auto'").fetchone()[0]
    conn.close()
    return n


def _contents_l1_auto(db):
    conn = sqlite3.connect(str(db))
    rows = conn.execute("SELECT fts_content FROM memories WHERE source='l1_auto'").fetchall()
    conn.close()
    return [r[0] for r in rows]


# ── IP：中英双用例 ────────────────────────────────────────────────────────

def test_extract_ip_en_and_zh():
    from agent.l1_extractor import extract_facts

    f_en = extract_facts("Deploy to server 10.20.30.40 on port 8080")
    ips_en = [f.value for f in f_en if f.kind == "ip"]
    assert "10.20.30.40" in ips_en, "英文 IP 应被抽到"

    f_zh = extract_facts("把数据库迁到 192.168.1.100，这是内网地址")
    ips_zh = [f.value for f in f_zh if f.kind == "ip"]
    assert "192.168.1.100" in ips_zh, "中文上下文的 IP 应被抽到"


def test_extract_ip_rejects_octet_over_255():
    from agent.l1_extractor import extract_facts

    f = extract_facts("bad ip 999.1.1.1 here")
    assert not any(fact.kind == "ip" for fact in f), "非法 octet 不应被当作 IP"


# ── API Key：中英双用例 ───────────────────────────────────────────────────

def test_extract_api_key_en_and_zh():
    from agent.l1_extractor import extract_facts

    f_en = extract_facts("use sk-abc123DEF456-ghi789JKL012 for openai")
    keys_en = [f.value for f in f_en if f.kind == "api_key"]
    assert "sk-abc123DEF456-ghi789JKL012" in keys_en, "英文 sk- 前缀 Key 应被抽到"

    # 中文：长随机串兜底命中（赋值式被'是'隔断，验证兜底路径）
    f_zh = extract_facts("我的 token 是 abcdefghijklmnopqrstuvwxyz123456 请保存")
    keys_zh = [f.value for f in f_zh if f.kind == "api_key"]
    assert "abcdefghijklmnopqrstuvwxyz123456" in keys_zh, "中文语境的 32+ 位 Key 应被抽到"


# ── 密码：脱敏（中英双用例，断言不落明文）─────────────────────────────────

def test_extract_password_masked_en_and_zh():
    from agent.l1_extractor import extract_facts, MASK_PASSWORDS

    f_en = extract_facts("password = SuP3rS3cret!")
    pw_en = [f for f in f_en if f.kind == "password"]
    assert pw_en, "英文密码应被抽到"
    if MASK_PASSWORDS:
        assert "SuP3rS3cret!" not in pw_en[0].value, "密码应脱敏，不得存明文"
        assert "*" in pw_en[0].value, "脱敏形态应含 *"

    f_zh = extract_facts("数据库密码是 MyDbPass9 别泄露")
    pw_zh = [f for f in f_zh if f.kind == "password"]
    assert pw_zh, "中文密码应被抽到"
    assert "MyDbPass9" not in [p.value for p in pw_zh], "中文密码明文不得落库"


# ── 偏好：中英双用例（中文断言具体子串）──────────────────────────────────

def test_extract_preference_en_and_zh():
    from agent.l1_extractor import extract_facts

    f_zh = extract_facts("我偏好用中文回复，不要英文")
    pref_zh = [f.value for f in f_zh if f.kind == "preference"]
    assert pref_zh, "中文偏好应被抽到"
    assert any("中文" in p for p in pref_zh), "中文偏好断言必须含'中文'具体子串"

    f_en = extract_facts("I prefer using TypeScript for frontend work")
    pref_en = [f.value for f in f_en if f.kind == "preference"]
    assert pref_en, "英文偏好应被抽到"
    assert any("TypeScript" in p for p in pref_en), "英文偏好断言必须含'TypeScript'具体子串"


# ── 端到端：写入 L1 且条数增长（验证尺子：L1 表行数）────────────────────

def test_run_extraction_writes_l1_and_grows(tmp_path, monkeypatch):
    from agent import memory_fabric as mf
    from agent.l1_extractor import run_l1_extraction_for_turn

    db = tmp_path / "l1.db"
    mf._init_db(db)
    monkeypatch.setattr(mf, "_get_index_db", lambda: db)

    before = _count_l1_auto(db)
    n = run_l1_extraction_for_turn(
        "服务器 IP 是 10.0.0.5，密码 secretPass1，我习惯用 vim",
        "好的，已记录 IP 10.0.0.5",
    )
    after = _count_l1_auto(db)

    assert n >= 3, f"应至少抽取 IP+密码+偏好 3 条，实际 {n}"
    assert after - before == n, "L1 条数应随抽取同步增长"
    contents = _contents_l1_auto(db)
    assert any("10.0.0.5" in c for c in contents), "L1 应包含 IP 值"
    assert any("vim" in c for c in contents), "L1 应包含偏好 vim"


# ── 幂等：相同事实重复轮次不累积重复行 ────────────────────────────────────

def test_idempotent_dedupe(tmp_path, monkeypatch):
    from agent import memory_fabric as mf
    from agent.l1_extractor import run_l1_extraction_for_turn

    db = tmp_path / "l1b.db"
    mf._init_db(db)
    monkeypatch.setattr(mf, "_get_index_db", lambda: db)

    run_l1_extraction_for_turn("my ip 172.16.0.1", "ok")
    first = _count_l1_auto(db)
    run_l1_extraction_for_turn("my ip 172.16.0.1", "ok again")
    second = _count_l1_auto(db)

    assert first >= 1
    assert second == first, "相同事实重复轮次不应累积重复行"


# ── P3-⑪ fix: 反思 evidence 文本不得被误抽为密码 ──────────────────────

def test_reflection_evidence_not_extracted_as_password():
    """反思系统 flag evidence 含"密码属强时效性事实"等描述性文本。
    密码正则会匹配到"属强时效性事实"这串汉字——不是密码值而是元描述。
    不阻止会导致癌细胞式繁殖：反思→flag evidence 含"密码"→L1 抽取器
    扫到→新密码残片入库→下一轮反思再 flag→无限循环（3 天 3377 条垃圾）。
    """
    from agent.l1_extractor import extract_facts

    evidence_texts = [
        "密码属强时效性事实（记忆上下文自述承认）",
        "密码本身属于强时效性事实",
        "密码值是易变凭据",
        "密码类凭据随时间失效",
        "密码随时可能被用户修改或失效",
        "密码会被定期更换/重置",
        "密码值可能在任意一次改密后即过期",
        "密码值极可能已过时且无法验证有效性",
        "密码属典型易变数据（条目自身已注明）",
        "密码及长度=18均为易变信息",
        "密码写入即已过期",
        "密码后此条目即失效",
    ]
    for text in evidence_texts:
        facts = extract_facts(text)
        pw_facts = [f for f in facts if f.kind == "password"]
        assert not pw_facts, (
            f"反思 evidence 不应被抽为密码: {text!r} → {[f.content[:30] for f in pw_facts]}"
        )


def test_real_password_still_extracted_after_evidence_filter():
    """evidence 过滤器不得误杀真实密码。"""
    from agent.l1_extractor import extract_facts

    real_cases = [
        ("我的密码是 MyP@ssw0rd123", "MyP@ssw0rd123"),
        ("password: Tr0ub4dour&3", "Tr0ub4dour&3"),
        ("密码: a8xK9mP2", "a8xK9mP2"),
        ("password=Sup3rS3cr3t", "Sup3rS3cr3t"),
    ]
    for text, expected_raw in real_cases:
        facts = extract_facts(text)
        pw_facts = [f for f in facts if f.kind == "password"]
        assert pw_facts, f"真实密码应被抽取: {text!r}"
        assert expected_raw not in pw_facts[0].value, f"密码应脱敏: {text!r}"
        assert "*" in pw_facts[0].value, f"脱敏形态应含*: {text!r}"


# ── index_skills 不再写入 memories 表 ──────────────────────────

def test_index_skills_does_not_write_memories(tmp_path, monkeypatch):
    """技能描述通过 skill 注册表注入，不应写入 memories 表。
    写入只会导致反思系统误 flag 为重复/范围漂移。
    """
    from agent import memory_fabric as mf

    db = tmp_path / "skill_test.db"
    mf._init_db(db)
    monkeypatch.setattr(mf, "_get_index_db", lambda: db)

    import sqlite3
    conn = sqlite3.connect(str(db))
    before = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]

    n = mf.index_skills([
        {"name": "test-skill", "description": "A test skill for unit testing."},
        {"name": "another-skill", "description": "Another skill description."},
    ], scope="test")

    after = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    conn.close()
    assert n == 2, "index_skills 应返回技能数"
    assert after == before, f"index_skills 不应写入 memories 表 (before={before}, after={after})"
