"""notion_writer 마크다운→블록 변환 핵심 경로 테스트 (PLAN P2 보강).

배경: notion_writer는 산출물 표시의 마지막 관문(465줄)인데 parse_inline의
링크 보존(test_link_sanitize)만 부분 커버되고, 긴 텍스트 분할(Notion 2000자
한도)·토글 children·콜아웃 변환은 무테스트였다. 결함 시 페이지가 통째로
깨지는 고위험 경로만 선별 보강.

실행: python tests/test_notion_blocks.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from notion_writer import (
    NOTION_TEXT_CHUNK,
    _split_long_text,
    parse_inline,
    parse_markdown_to_blocks,
)


def test_split_long_text_limits_and_lossless():
    """모든 조각이 한도 이하이고, 이어붙이면 원문과 동일 (데이터 무손실)."""
    text = "가나다라마" * 1000  # 5000자
    parts = _split_long_text(text)
    assert all(len(p) <= NOTION_TEXT_CHUNK for p in parts), [len(p) for p in parts]
    assert "".join(parts) == text
    # 짧은 텍스트는 분할 없음
    assert _split_long_text("짧음") == ["짧음"]


def test_parse_inline_long_text_lossless():
    """긴 평문이 rich_text 여러 개로 쪼개져도 내용이 보존된다."""
    text = "본문" * 2000  # 4000자
    runs = parse_inline(text)
    assert len(runs) >= 2
    joined = "".join(r["text"]["content"] for r in runs)
    assert joined == text


def test_parse_inline_styles_and_link():
    """**굵게**·*기울임*·[링크](url)가 올바른 run 속성으로 변환된다."""
    runs = parse_inline("앞 **굵게** 그리고 [기사](https://x.com/1) 끝")
    texts = [(r["text"]["content"], r["annotations"]["bold"], r["text"].get("link"))
             for r in runs]
    assert ("굵게", True, None) in texts
    link_runs = [r for r in runs if r["text"].get("link")]
    assert len(link_runs) == 1
    assert link_runs[0]["text"]["content"] == "기사"
    assert link_runs[0]["text"]["link"]["url"] == "https://x.com/1"
    # 평문 재조합 확인
    joined = "".join(r["text"]["content"] for r in runs)
    assert joined == "앞 굵게 그리고 기사 끝"


def test_toggle_heading_children():
    """▶ 헤더 다음의 인덴트 줄들이 토글 children으로 묶인다 (템플릿 핵심 구조)."""
    md = (
        "▶ ### 심층 이슈 제목\n"
        "   - **맥락**: 상황 설명\n"
        "   - [기사](https://x.com/1)\n"
        "## 다음 섹션"
    )
    blocks = parse_markdown_to_blocks(md)
    assert len(blocks) == 2, [b["type"] for b in blocks]
    toggle = blocks[0]
    assert toggle["type"] == "heading_3"
    assert toggle["heading_3"]["is_toggleable"] is True
    children = toggle["heading_3"]["children"]
    assert len(children) == 2
    assert all(c["type"] == "bulleted_list_item" for c in children)
    # children 안 링크 보존
    child_link = children[1]["bulleted_list_item"]["rich_text"][0]["text"]["link"]
    assert child_link["url"] == "https://x.com/1"
    # 토글 범위 밖 줄은 별도 블록
    assert blocks[1]["type"] == "heading_2"
    assert blocks[1]["heading_2"]["is_toggleable"] is False


def test_callout_grouping_and_emoji():
    """연속 > 줄이 콜아웃 1개로 묶이고, 첫 줄 이모지가 아이콘으로 추출된다."""
    md = "> 💡 **오늘의 핵심**\n> 두 번째 줄"
    blocks = parse_markdown_to_blocks(md)
    assert len(blocks) == 1
    co = blocks[0]
    assert co["type"] == "callout"
    assert co["callout"]["icon"]["emoji"] == "💡"
    joined = "".join(r["text"]["content"] for r in co["callout"]["rich_text"])
    assert "오늘의 핵심" in joined and "두 번째 줄" in joined
    # 아이콘으로 뽑힌 이모지는 본문에서 제거
    assert "💡" not in joined


def test_divider_heading_paragraph():
    """구분선·일반 헤더·평문 paragraph 기본 변환."""
    blocks = parse_markdown_to_blocks("# 제목\n---\n평문 한 줄")
    types = [b["type"] for b in blocks]
    assert types == ["heading_1", "divider", "paragraph"], types


if __name__ == "__main__":
    failures = 0
    tests = [
        test_split_long_text_limits_and_lossless,
        test_parse_inline_long_text_lossless,
        test_parse_inline_styles_and_link,
        test_toggle_heading_children,
        test_callout_grouping_and_emoji,
        test_divider_heading_paragraph,
    ]
    for fn in tests:
        try:
            fn()
            print(f"  PASS: {fn.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"  FAIL: {fn.__name__}{e}")
    if failures:
        print(f"\n{failures}개 실패")
        sys.exit(1)
    print("\n전체 통과")
