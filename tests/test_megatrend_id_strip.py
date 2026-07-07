"""mega_trend·tldr 본문 ID 누출 결함 회귀 테스트.

배경: insight ID 누출(test_insight_id_strip.py)을 _clean_insight_ids로 막았지만,
같은 자유 서술인 mega_trend는 클리닝 없이 템플릿으로 전달되는 사각지대가 있었다.
2026-06-09 운영 산출물(Notion 페이지) 메가 트렌드에 '(SK그룹의 뉴 이천포럼, [40])'
'(중국 AI 시장 동향, [1])' 등이 그대로 노출됐고, [26]은 본문에 대응 기사도 없어
독자에게 무의미한 내부 ID였다. _build_template_context가 tldr·mega_trend
(summary, key_threads)에도 클리닝을 적용하도록 수정하고 이 테스트로 고정.

이 테스트는 2026-06-09 실제 운영 산출물에서 추출한 케이스로 검증.

실행: python tests/test_megatrend_id_strip.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from renderer import _clean_insight_ids, _build_template_context


# 2026-06-09 실제 운영 산출물(메가 트렌드)에서 추출한 패턴.
# '(서술, [N])' 꼴은 ID 앞 콤마까지 제거되어 '(서술)'로 남아야 한다.
CASES_LEAK = [
    (
        "기업들은 AI 전환(AX)을 핵심 전략으로 삼아 대규모 투자를 단행하고 있으며(SK그룹의 뉴 이천포럼, [40]), 이는 AI 인프라 및 클라우드 시장의 경쟁 구도를 재편하고 있습니다(중국 AI 시장 동향, [1]).",
        "기업들은 AI 전환(AX)을 핵심 전략으로 삼아 대규모 투자를 단행하고 있으며(SK그룹의 뉴 이천포럼), 이는 AI 인프라 및 클라우드 시장의 경쟁 구도를 재편하고 있습니다(중국 AI 시장 동향).",
    ),
    (
        "'정책적 요소'를 광범위하게 고려하는 새로운 패러다임이 등장하고 있습니다(EU 기업결합 심사 가이드라인 개정, [25]).",
        "'정책적 요소'를 광범위하게 고려하는 새로운 패러다임이 등장하고 있습니다(EU 기업결합 심사 가이드라인 개정).",
    ),
    (
        "기존의 사회적 통념과 제도를 재검토하려는 움직임으로 이어지고 있습니다(교사 정치기본권 논의, [26]).",
        "기존의 사회적 통념과 제도를 재검토하려는 움직임으로 이어지고 있습니다(교사 정치기본권 논의).",
    ),
]

# 회귀 방지: ID 없는 정상 메가 트렌드 서술 (06-04 실물 — 누출 없던 날)
CASES_NORMAL = [
    "글로벌 AI 경쟁이 심화되면서 한국은 핵심 공급망과 기술 협력의 중심지로 부상하고 있습니다.",
    # 괄호 안 일반 서술·콤마는 보존되어야 한다
    "AI 전환(AX)을 생존을 넘어 주도권 확보의 핵심 전략으로 삼았다(투자, 인수합병 포함).",
]


def test_megatrend_leak_cases_stripped():
    """06-09 실물 누출 패턴이 콤마째 제거되어야 한다."""
    for input_text, expected in CASES_LEAK:
        result = _clean_insight_ids(input_text)
        assert result == expected, (
            f"\n  입력: {input_text}\n  기대: {expected}\n  실제: {result}"
        )


def test_megatrend_normal_unchanged():
    """ID 없는 정상 서술(괄호·콤마 포함)은 변형되지 않아야 한다."""
    for text in CASES_NORMAL:
        result = _clean_insight_ids(text)
        assert result == text, (
            f"\n  정상 텍스트가 변형됨\n  입력: {text}\n  실제: {result}"
        )


def test_template_context_applies_cleaning():
    """_build_template_context가 tldr·mega_trend(summary, key_threads)에
    클리닝을 적용해야 한다 (insight만 정리하던 사각지대 회귀 방지)."""
    analysis = {
        "tldr": "오늘의 핵심 흐름입니다(분야 종합, [3]).",
        "mega_trend": {
            "summary": CASES_LEAK[0][0],
            "key_threads": [
                "AI 기술 혁신이 투자 전략을 재편한다(산업 동향, [12]).",
                "지정학 리스크가 정책 결정에 영향을 준다.",
            ],
        },
        "categories": [],
    }
    ctx = _build_template_context("2026-06-09", analysis, {}, [])

    assert ctx["tldr"] == "오늘의 핵심 흐름입니다(분야 종합).", ctx["tldr"]
    assert ctx["mega_trend"]["summary"] == CASES_LEAK[0][1], ctx["mega_trend"]["summary"]
    assert ctx["mega_trend"]["key_threads"][0] == (
        "AI 기술 혁신이 투자 전략을 재편한다(산업 동향)."
    ), ctx["mega_trend"]["key_threads"][0]
    assert ctx["mega_trend"]["key_threads"][1] == (
        "지정학 리스크가 정책 결정에 영향을 준다."
    ), ctx["mega_trend"]["key_threads"][1]
    # 어떤 필드에도 [N] 패턴이 남아 있으면 안 된다
    import json
    flat = json.dumps(
        {"tldr": ctx["tldr"], "mega_trend": ctx["mega_trend"]}, ensure_ascii=False
    )
    import re
    assert not re.search(r"\[\d+\]", flat), f"ID 잔존: {flat}"


if __name__ == "__main__":
    failures = 0
    tests = [
        test_megatrend_leak_cases_stripped,
        test_megatrend_normal_unchanged,
        test_template_context_applies_cleaning,
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
