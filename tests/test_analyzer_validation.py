"""analyzer._validate_analysis 검증 분기 테스트 (PLAN P2 핵심 경로 보강).

배경: LLM JSON 출력 검증은 출처 무결성(ADR-001)의 코드 측 안전망인데
회귀 테스트가 전혀 없었다. 결함 시 분석 페이지가 통째로 깨지거나(검증 누락)
환각 ID가 출처로 둔갑하는(필터 누락) 고위험 경로라 선별 보강.

실행: python tests/test_analyzer_validation.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analyzer import _validate_analysis, AnalysisValidationError


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
    assert cat["insight"] == ""
    assert cat["has_sufficient_data"] is True
    assert cat["key_flows"] == []
    assert cat["deep_issues"][0]["referenced_article_ids"] == [1, 2]


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
