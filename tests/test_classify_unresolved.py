"""분류 '미해결'(unresolved) 상태 분리 테스트 (ADR-017, Phase 0a).

배경: 배치가 알고리즘적으로 실패하면(빈 응답) 그 기사들이 누락 폴백에서
genuine '기타'로 세탁돼, 분석에서 조용히 빠지고 스냅샷에 박제됐다
(silent error swallow). 이제 LLM 판정을 못 받은 기사는
category=None + classification_unresolved=True로 표시해 genuine '기타'
(LLM이 명시적으로 MISC 판정)와 구분한다 — 회복/관측의 토대.

실행: python tests/test_classify_unresolved.py
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collectors import Article
from classifier import _apply_classifications, _apply_fallback, FALLBACK_CATEGORY

KST = timezone(timedelta(hours=9))


def _arts(n):
    return [
        Article(
            title=f"제목{i}", summary="", link=f"https://x/{i}",
            published=datetime(2026, 6, 28, 8, tzinfo=KST), source="동아일보",
        )
        for i in range(1, n + 1)
    ]


def test_omitted_ids_become_unresolved_not_misc():
    """LLM 응답에서 빠진 id는 '미해결'이지 genuine '기타'가 아니다."""
    arts = _arts(4)
    # id 1·2만 분류, 3·4는 응답에서 누락 (배치 실패/응답 누락 모사)
    cls = [
        {"id": 1, "cat": "POL", "cross": False},
        {"id": 2, "cat": "IT", "cross": False},
    ]
    stats = _apply_classifications(arts, cls)

    # 누락분(3·4)은 미해결
    assert arts[2].classification_unresolved is True
    assert arts[3].classification_unresolved is True
    assert arts[2].category is None and arts[3].category is None
    assert stats.get("_unresolved", 0) == 2, stats
    # genuine '기타' 카운트는 오염되지 않음 (배치 실패가 기타로 세탁되지 않음)
    assert stats.get(FALLBACK_CATEGORY, 0) == 0, stats
    # 정상 분류분은 미해결 아님
    assert arts[0].classification_unresolved is False
    assert arts[0].category == "정치"


def test_genuine_misc_is_resolved():
    """LLM이 명시적으로 MISC로 판정한 것은 genuine '기타'(해결됨), 미해결 아님."""
    arts = _arts(2)
    cls = [
        {"id": 1, "cat": "MISC", "cross": False},
        {"id": 2, "cat": "SOC", "cross": False},
    ]
    stats = _apply_classifications(arts, cls)
    assert arts[0].category == FALLBACK_CATEGORY
    assert arts[0].classification_unresolved is False
    assert stats.get(FALLBACK_CATEGORY, 0) == 1, stats
    assert stats.get("_unresolved", 0) == 0, stats


def test_full_fallback_marks_all_unresolved():
    """전면 실패 폴백은 전량 '미해결'(genuine 기타 아님) — 재실행 회복 대상."""
    arts = _arts(3)
    _apply_fallback(arts)
    assert all(a.classification_unresolved for a in arts)
    assert all(a.category is None for a in arts)


def test_reclassify_clears_unresolved_flag():
    """이전에 미해결이던 기사가 재분류되면 플래그가 풀린다 (회복 idempotency).

    Phase 2a(부분집합 재분류)가 해결분을 다시 정상 상태로 되돌릴 수 있어야 한다.
    """
    arts = _arts(1)
    arts[0].classification_unresolved = True  # 직전 런에서 미해결로 표시됐다 가정
    _apply_classifications(arts, [{"id": 1, "cat": "ECO", "cross": False}])
    assert arts[0].classification_unresolved is False
    assert arts[0].category == "경제·금융·증시"


if __name__ == "__main__":
    failures = 0
    tests = [
        test_omitted_ids_become_unresolved_not_misc,
        test_genuine_misc_is_resolved,
        test_full_fallback_marks_all_unresolved,
        test_reclassify_clears_unresolved_flag,
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
