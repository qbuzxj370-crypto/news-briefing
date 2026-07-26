"""analyzer._validate_analysis 검증 분기 테스트 (PLAN P2 핵심 경로 보강).

배경: LLM JSON 출력 검증은 출처 무결성(ADR-001)의 코드 측 안전망인데
회귀 테스트가 전혀 없었다. 결함 시 분석 페이지가 통째로 깨지거나(검증 누락)
환각 ID가 출처로 둔갑하는(필터 누락) 고위험 경로라 선별 보강.

실행: python tests/test_analyzer_validation.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analyzer import (
    _validate_analysis,
    AnalysisValidationError,
    insight_grounding_stats,
)


def _minimal_analysis():
    """스키마를 만족하는 최소 분석 JSON."""
    return {
        "tldr": "요약",
        "mega_trend": {"summary": "흐름", "key_threads": ["t1"]},
        "categories": [
            {
                "name": "정치",
                "deep_issues": [
                    {
                        "title": "이슈",
                        "context": "맥락",
                        "implication": "함의",
                        "watch_points": "관전",
                        "referenced_article_ids": [1, 2],
                    }
                ],
                "minor_issues": [],
                "insight": "통찰",
            }
        ],
    }


def test_valid_passes_and_fills_defaults():
    """정상 입력은 통과하고, 선택 필드는 기본값이 채워진다."""
    data = _minimal_analysis()
    del data["categories"][0]["insight"]
    out = _validate_analysis(data, valid_ids={1, 2, 3})
    cat = out["categories"][0]
    # insight는 누락돼도 3필드 객체로 정규화된다
    assert cat["insight"] == {
        "observation": "", "interpretation": "", "limitation": "",
    }
    assert cat["has_sufficient_data"] is True
    assert cat["key_flows"] == []
    assert cat["deep_issues"][0]["referenced_article_ids"] == [1, 2]
    # confidence 누락 시 기본값 "중간"
    assert cat["deep_issues"][0]["confidence"] == "중간"


def test_confidence_is_computed_not_taken_from_llm():
    """confidence는 코드가 산출한다 — LLM이 보낸 값은 무시하고 덮어쓴다.

    배경(2026-07-25 운영): LLM에게 물었더니 deep_issue 12건 전부 "높음",
    참조 1건짜리 3건도 "높음"이었다(프롬프트 규칙 위반). 자기평가 확신도는
    캘리브레이션되지 않으므로 라벨 결정을 코드로 회수했다.
    """
    data = _minimal_analysis()
    data["categories"][0]["deep_issues"] = [
        # LLM이 "높음"이라 우겨도 참조 1건이면 판단유보
        {"title": "a", "confidence": "높음", "referenced_article_ids": [1]},
        # 참조 2건 → 중간
        {"title": "b", "confidence": "높음", "referenced_article_ids": [1, 2]},
        # 참조 3건 + 짧은 본문 → 높음
        {"title": "c", "confidence": "판단유보", "referenced_article_ids": [1, 2, 3]},
        # 오타·타입오류·누락 모두 무관 — 참조 수만 본다
        {"title": "d", "confidence": "매우높음", "referenced_article_ids": []},
        {"title": "e", "confidence": 5, "referenced_article_ids": [1, 2]},
    ]
    out = _validate_analysis(data, valid_ids={1, 2, 3})
    got = [i["confidence"] for i in out["categories"][0]["deep_issues"]]
    assert got == ["판단유보", "중간", "높음", "판단유보", "중간"], got


def test_confidence_uses_valid_ids_only():
    """환각 ID가 제거된 뒤의 '유효' 참조 수로 산출해야 한다.

    ID 3개를 보냈어도 2개가 범위 밖이면 실제 근거는 1건 → 판단유보.
    """
    data = _minimal_analysis()
    data["categories"][0]["deep_issues"] = [
        {"title": "a", "referenced_article_ids": [1, 99, 100]},
    ]
    out = _validate_analysis(data, valid_ids={1, 2, 3})
    issue = out["categories"][0]["deep_issues"][0]
    assert issue["referenced_article_ids"] == [1]
    assert issue["confidence"] == "판단유보", issue["confidence"]


def test_confidence_downgraded_when_body_too_long_for_refs():
    """참조는 3건이어도 참조당 서술이 과하게 길면 '중간'으로 강등."""
    data = _minimal_analysis()
    long_body = "가" * 400  # 3필드 합산 1200자 > 300 * 3
    data["categories"][0]["deep_issues"] = [
        {
            "title": "a",
            "context": long_body,
            "implication": long_body,
            "watch_points": long_body,
            "referenced_article_ids": [1, 2, 3],
        },
    ]
    out = _validate_analysis(data, valid_ids={1, 2, 3})
    assert out["categories"][0]["deep_issues"][0]["confidence"] == "중간"


def test_insight_string_normalized_to_three_fields():
    """LLM이 구 형식(문자열)으로 회귀해도 3필드로 받아준다 (graceful)."""
    data = _minimal_analysis()
    data["categories"][0]["insight"] = "표면 뉴스 뒤의 구조 변화"
    out = _validate_analysis(data, valid_ids={1, 2})
    assert out["categories"][0]["insight"] == {
        "observation": "",
        "interpretation": "표면 뉴스 뒤의 구조 변화",
        "limitation": "",
    }


def test_insight_grounding_flags_missing_number_and_limitation():
    """관찰에 수치가 없거나 한계 절이 비면 분야명을 집계해 경고 대상으로 올린다.

    배경(2026-07-25): 프롬프트로 요구한 수치 인용이 5개 분야 모두 무시됐는데
    아무도 몰랐다. 코드가 세어 로그로 드러내야 무시가 드러난다.
    """
    analysis = {
        "categories": [
            {   # 정상: 수치 있음 + 한계 있음
                "name": "IT",
                "insight": {
                    "observation": "AI 18건, 반도체 7건으로 인프라 기사가 많았다",
                    "interpretation": "관심 이동 신호",
                    "limitation": "하루치라 단정 어려움",
                },
            },
            {   # 수치 없음 (인상 서술)
                "name": "정치",
                "insight": {
                    "observation": "관심이 두드러졌다",
                    "interpretation": "해석",
                    "limitation": "한계",
                },
            },
            {   # 한계 없음
                "name": "산업",
                "insight": {
                    "observation": "기사 3건이 같은 방향",
                    "interpretation": "해석",
                    "limitation": "",
                },
            },
            {   # 인사이트 자체가 빈 분야는 집계 제외
                "name": "사회",
                "insight": {"observation": "", "interpretation": "", "limitation": ""},
            },
        ]
    }
    stats = insight_grounding_stats(analysis)
    assert stats["total"] == 3, stats          # 빈 분야 제외
    assert stats["no_number"] == ["정치"], stats
    assert stats["no_limitation"] == ["산업"], stats


def test_insight_grounding_safe_on_empty():
    assert insight_grounding_stats({})["total"] == 0
    assert insight_grounding_stats(None)["total"] == 0


def test_top_level_not_dict_raises():
    for bad in (None, [], "json", 42):
        try:
            _validate_analysis(bad, valid_ids=set())
            assert False, f"예외가 나야 함: {bad!r}"
        except AnalysisValidationError:
            pass


def test_missing_mega_trend_raises():
    data = _minimal_analysis()
    del data["mega_trend"]
    try:
        _validate_analysis(data, valid_ids={1, 2})
        assert False, "mega_trend 누락인데 통과"
    except AnalysisValidationError as e:
        assert "mega_trend" in str(e)


def test_missing_summary_raises():
    data = _minimal_analysis()
    del data["mega_trend"]["summary"]
    try:
        _validate_analysis(data, valid_ids={1, 2})
        assert False, "summary 누락인데 통과"
    except AnalysisValidationError as e:
        assert "summary" in str(e)


def test_empty_categories_raises():
    data = _minimal_analysis()
    data["categories"] = []
    try:
        _validate_analysis(data, valid_ids={1, 2})
        assert False, "categories 빈 배열인데 통과"
    except AnalysisValidationError:
        pass


def test_invalid_article_ids_filtered():
    """valid_ids 밖 ID·비숫자 ID는 제거된다 (환각 출처 차단 — ADR-001)."""
    data = _minimal_analysis()
    data["categories"][0]["deep_issues"][0]["referenced_article_ids"] = [
        1, 99, "2", None, 2.0, -5,
    ]
    out = _validate_analysis(data, valid_ids={1, 2, 3})
    ids = out["categories"][0]["deep_issues"][0]["referenced_article_ids"]
    # 1(유효), 99(범위 밖 제거), "2"(문자열 제거), None(제거),
    # 2.0(float→int 2 유효), -5(범위 밖 제거)
    assert ids == [1, 2], ids


def test_non_dict_issues_dropped():
    """deep/minor_issues 안의 비객체 항목은 버려진다."""
    data = _minimal_analysis()
    data["categories"][0]["deep_issues"].append("문자열 이슈")
    data["categories"][0]["minor_issues"] = [None, {"title": "ok"}]
    out = _validate_analysis(data, valid_ids={1, 2})
    assert len(out["categories"][0]["deep_issues"]) == 1
    minor = out["categories"][0]["minor_issues"]
    assert len(minor) == 1 and minor[0]["title"] == "ok"
    # 기본값 채움도 확인
    assert minor[0]["referenced_article_ids"] == []


def test_tldr_and_key_threads_coerced():
    """tldr 비문자열 → 빈 문자열, key_threads 비배열 → 빈 배열 (예외 아님)."""
    data = _minimal_analysis()
    data["tldr"] = {"oops": 1}
    data["mega_trend"]["key_threads"] = "한 줄"
    out = _validate_analysis(data, valid_ids={1, 2})
    assert out["tldr"] == ""
    assert out["mega_trend"]["key_threads"] == []


if __name__ == "__main__":
    failures = 0
    tests = [
        test_valid_passes_and_fills_defaults,
        test_confidence_is_computed_not_taken_from_llm,
        test_confidence_uses_valid_ids_only,
        test_confidence_downgraded_when_body_too_long_for_refs,
        test_insight_string_normalized_to_three_fields,
        test_insight_grounding_flags_missing_number_and_limitation,
        test_insight_grounding_safe_on_empty,
        test_top_level_not_dict_raises,
        test_missing_mega_trend_raises,
        test_missing_summary_raises,
        test_empty_categories_raises,
        test_invalid_article_ids_filtered,
        test_non_dict_issues_dropped,
        test_tldr_and_key_threads_coerced,
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
