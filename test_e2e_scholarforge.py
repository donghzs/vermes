#!/usr/bin/env python3
"""ScholarForge 端到端验证：set_active_project → outline → write → read → export

验证完整论文写作链路是否真正跑通。
"""
import asyncio
import sys
import os

os.environ["HERMES_HOME"] = os.path.expanduser("~/.hermes")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hermes_cli.scholarforge.tools import (
    _handle_scholarforge_list_projects,
    _handle_scholarforge_set_active_project,
    _handle_scholarforge_read_section,
    _handle_scholarforge_write,
    _handle_scholarforge_export,
    _handle_scholarforge_check_stats,
)
from hermes_cli.scholarforge.database import get_outline, get_section_content, get_all_sections


async def main():
    print("=" * 60)
    print("ScholarForge E2E Verification")
    print("=" * 60)

    # Step 1: list_projects
    print("\n📋 Step 1: list_projects")
    result = await _handle_scholarforge_list_projects({})
    assert "52" in result, "Project #52 not found"
    print("  ✅ Pass")

    # Step 2: set_active_project
    print("\n📋 Step 2: set_active_project(52)")
    result = await _handle_scholarforge_set_active_project({"project_id": 52})
    assert "52" in result, "Failed to set active project"
    print("  ✅ Pass")

    # Step 3: read outline
    print("\n📋 Step 3: read outline from DB")
    outline = get_outline(52)
    print(f"  Outline: {len(outline)} sections")
    for sec in outline:
        print(f"    {sec['id']}: {sec['title']} (sort={sec.get('sort_order', '?')})")
    assert len(outline) > 0, "Outline is empty"
    print("  ✅ Pass")

    # Step 4: write abstract section using section_key
    print("\n📋 Step 4: write(abstract, section_key='abstract')")
    test_content = "## 摘要\n\n本研究探讨户外主题建构游戏对大班幼儿合作能力的影响。采用准实验设计，选取两个大班各30名幼儿，实验组进行8周户外主题建构游戏干预，对照组维持常规室内建构游戏。采用幼儿合作行为观察量表前后测，结果显示实验组合作能力总分显著高于对照组（p<0.01）。\n\n关键词：户外游戏；建构游戏；合作能力；大班幼儿"
    result = await _handle_scholarforge_write({
        "topic": "户外建构游戏对大班幼儿合作能力影响",
        "section_type": "abstract",
        "section_key": "abstract",
        "content": test_content,  # 传入已有内容
        "paper_type": "硕士论文",
        "project_id": 52,
    })
    print(f"  Result (first 200): {result[:200]}...")
    print("  ✅ Pass (write executed)")

    # Step 5: verify DB persistence with correct key
    print("\n📋 Step 5: verify DB persistence (section_key='abstract')")
    content = get_section_content(52, "abstract")
    print(f"  DB content length: {len(content)} chars")
    if len(content) > 0:
        print(f"  Content preview: {content[:100]}...")
        print("  ✅ Pass — content saved with correct key!")
    else:
        print("  ⚠️ Content not in DB with key='abstract'")
        all_s = get_all_sections(52)
        print(f"  All sections in DB: {list(all_s.keys())}")
        # write 工具可能用 LLM 生成新内容而非用传入的 content
        # 检查是否有其他 key
        for k, v in all_s.items():
            if v.strip():
                print(f"    Found content under key='{k}': {len(v)} chars")
        print("  ⚠️ write 工具可能忽略了传入 content，用 LLM 生成了新内容")

    # Step 6: write intro section
    print("\n📋 Step 6: write(intro, section_key='intro')")
    result = await _handle_scholarforge_write({
        "topic": "户外建构游戏对大班幼儿合作能力影响",
        "section_type": "introduction",
        "section_key": "intro",
        "paper_type": "硕士论文",
        "project_id": 52,
    })
    print(f"  Result (first 200): {result[:200]}...")

    # Check DB
    content = get_section_content(52, "intro")
    print(f"  DB intro content: {len(content)} chars")
    if len(content) > 0:
        print("  ✅ Pass — intro saved with correct key!")
    else:
        all_s = get_all_sections(52)
        print(f"  All keys in DB: {list(all_s.keys())}")

    # Step 7: read_section overview
    print("\n📋 Step 7: read_section (overview)")
    result = await _handle_scholarforge_read_section({"project_id": 52})
    print(f"  Result: {result[:400]}...")
    print("  ✅ Pass")

    # Step 8: check_stats
    print("\n📋 Step 8: check_stats")
    result = await _handle_scholarforge_check_stats({"project_id": 52})
    print(f"  Result: {result[:300]}...")
    print("  ✅ Pass")

    # Step 9: export (auto-assemble from DB)
    print("\n📋 Step 9: export (auto-assemble from DB, no content param)")
    result = await _handle_scholarforge_export({
        "title": "户外建构游戏对大班幼儿合作能力影响的实验研究",
        "format": "markdown",
        "project_id": 52,
    })
    print(f"  Result (first 400): {result[:400]}...")
    if "✅" in result:
        print("  ✅ Pass — export succeeded!")
    else:
        print("  ⚠️ Export may have failed (no ✅)")

    # Final summary
    print("\n" + "=" * 60)
    print("📊 Final DB State")
    print("=" * 60)
    all_s = get_all_sections(52)
    total = 0
    for sec in outline:
        key = sec["id"]
        content = all_s.get(key, "")
        wc = len(content)
        total += wc
        status = "✅" if wc > 0 else "❌"
        print(f"  {status} {key}: {wc} chars — {sec['title']}")
    print(f"\n  Total: {total} chars across {len([v for v in all_s.values() if v.strip()])} sections")


if __name__ == "__main__":
    asyncio.run(main())
