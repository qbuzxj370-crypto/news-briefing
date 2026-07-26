"""deep_issue 출처 참조 지표(source_coverage_stats) 테스트.

배경 (ADR-001 미해결 이슈): 본문-출처 의미 일치는 자동 검증이 불가능해서
(검증은 valid_ids 범위만 검사), 근사 신호로 deep_issue당 referenced_article_ids
개수 분포를 로깅한다. ID 1개 이하의 장문 분석이 잦으면 비례 임계 도입 검토.

실행: python tests/test_source_coverage.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analyzer import source_coverage_stats, LONG_BODY_THRESHOLD


def _issue(n_refs, body_len, title="이슈"):
    """참조 n_refs개, 본문 body_len자짜리 deep_issue 생성."""
    third = body_len // 3
    return {
        "title": title,
        "context": "가" * third,
        "implication": "나" * third,
        "watch_points": "다" * (body_len - 2 * third),
        "referenced_article_ids": list(range(1, n_refs + 1)),
    }


def test_avg_and_count():
    analysis = {
        "categories": [
            {"name": "정치", "deep_issues": [_issue(2, 100), _issue(4, 100)]},
            {"name": "산업", "deep_issues": [_issue(3, 100)]},
        ]
    }
    stats = source_coverage_stats(analysis)
    assert stats["deep_issue_count"] == 3
    assert abs(stats["avg_refs"] - 3.0) < 1e-9, stats["avg_refs"]
    assert stats["low_ref_long"] == []


def test_low_ref_long_flagged():
    """참조 1개 이하 + 장문(임계 이상)만 점검 후보로 표시."""
    analysis = {
        "categories": [
            {
                "name": "정치",
                "deep_issues": [
                    _issue(1, LONG_BODY_THRESHOLD + 50, title="장문 단일 참조"),
                    _issue(0, LONG_BODY_THRESHOLD + 10, title="장문 무참조"),
                    _issue(1, LONG_BODY_THRESHOLD - 100, title="단문 단일 참조"),
                    _issue(5, LONG_BODY_THRESHOLD + 500, title="장문 다참조"),
                ],
            }
        ]
    }
    stats = source_coverage_stats(analysis)
    flagged_titles = [t for _, t, _, _ in stats["low_ref_long"]]
    assert flagged_titles == ["장문 단일 참조", "장문 무참조"], flagged_titles
    # (분야, 제목, 참조수, 본문길이) 구조 확인
    cat, title, n, body_len = stats["low_ref_long"][0]
    assert cat == "정치" and n == 1 and body_len >= LONG_BODY_THRESHOLD


def test_empty_and_none_safe():
    """분석 없음/빈 분석에서도 안전하게 0 반환."""
    for analysis in (None, {}, {"categories": []}):
        stats = source_coverage_stats(analysis)
        assert stats["deep_issue_count"] == 0
        assert stats["avg_refs"] == 0.0
        assert stats["low_ref_long"] == []


def test_source_concentration_flags_single_outlet():
    """참조 2건 이상이 전부 같은 매체면 편중으로 잡고, 섞이면 잡지 않는다.

    배경(2026-07-25 운영): 사회 분야 deep_issue 3건의 출처가 전부 한겨레였는데
    아무 신호도 남지 않았다. 단일 매체 프레임이 그대로 분석이 되는 위험(#8).
    """
    from types import SimpleNamespace
    from analyzer import source_concentration_stats

    arts = {
        1: SimpleNamespace(source="한겨레"),
        2: SimpleNamespace(source="한겨레"),
        3: SimpleNamespace(source="동아일보"),
    }
    analysis = {
        "categories": [
            {
                "name": "사회·시사",
                "deep_issues": [
                    # 2건 전부 한겨레 → 편중
                    {"title": "편중", "referenced_article_ids": [1, 2]},
                    # 매체 섞임 → 정상
                    {"title": "혼합", "referenced_article_ids": [1, 3]},
                    # 1건은 confidence가 이미 판단유보로 표시 → 중복 경고 안 함
                    {"title": "단일참조", "referenced_article_ids": [1]},
                ],
            }
        ]
    }
    flagged = source_concentration_stats(analysis, arts)
    assert len(flagged) == 1, flagged
    cat_name, title, source, n = flagged[0]
    assert (cat_name, title, source, n) == ("사회·시사", "편중", "한겨레", 2), flagged


def test_source_concentration_safe_on_empty():
    from analyzer import source_concentration_stats
    assert source_concentration_stats({}, {}) == []
    assert source_concentration_stats(None, {}) == []
    # id_to_article에 없는 ID만 있으면 매체를 알 수 없어 잡지 않는다
    analysis = {"categories": [{"name": "X", "deep_issues": [
        {"title": "t", "referenced_article_ids": [98, 99]}]}]}
    assert source_concentration_stats(analysis, {}) == []


if __name__ == "__main__":
    failures = 0
    tests = [
        test_avg_and_count,
        test_low_ref_long_flagged,
        test_empty_and_none_safe,
        test_source_concentration_flags_single_outlet,
        test_source_concentration_safe_on_empty,
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
