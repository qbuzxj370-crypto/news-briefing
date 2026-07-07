"""실패 배치 재시도 테스트 (ADR-017, Phase 0b).

배경: _classify_batch는 LLM 호출/파싱 실패를 빈 리스트로 흡수한다. 한 번의
일시적 실패(5xx·간헐 파싱)로 100건 배치가 통째로 미해결로 떨어지는 걸 막기
위해, 빈 결과면 BATCH_RETRY회 재시도한다. 재시도까지 모두 실패한 잔여만
미해결(0a)로 남는다.

실행: python tests/test_classify_retry.py
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import classifier
from classifier import classify, BATCH_RETRY, FALLBACK_CATEGORY
from collectors import Article

KST = timezone(timedelta(hours=9))


class FakeLLM:
    """스크립트된 동작을 호출 순서대로 수행하는 가짜 LLM.

    각 동작: Exception 인스턴스 → raise, str → 그대로 raw 반환.
    리스트가 소진되면 마지막 동작을 반복.
    """

    def __init__(self, scripted):
        self._scripted = list(scripted)
        self.calls = 0
        self.last_used = "fake"

    def generate(self, system, user, max_tokens=8000, json_mode=False, thinking_budget=None, temperature=None):
        beh = self._scripted[self.calls] if self.calls < len(self._scripted) else self._scripted[-1]
        self.calls += 1
        if isinstance(beh, Exception):
            raise beh
        return beh


def _arts(n):
    return [
        Article(
            title=f"제목{i}", summary="", link=f"https://x/{i}",
            published=datetime(2026, 6, 28, 8, tzinfo=KST), source="동아일보",
        )
        for i in range(1, n + 1)
    ]


def _items(*pairs):
    """(id, code) 쌍들 → items JSON 문자열."""
    objs = ", ".join(f'{{"id": {i}, "cat": "{c}", "cross": false}}' for i, c in pairs)
    return f'{{"items": [{objs}]}}'


def _no_sleep():
    classifier.BATCH_RETRY_SLEEP = 0


def test_success_no_retry():
    """1차 성공이면 재시도 없음."""
    _no_sleep()
    arts = _arts(2)
    llm = FakeLLM([_items((1, "IT"), (2, "POL"))])
    classify(llm, arts)
    assert llm.calls == 1, llm.calls
    assert arts[0].category == "IT·테크·AI" and arts[1].category == "정치"
    assert not any(a.classification_unresolved for a in arts)


def test_failed_batch_retried_then_recovers():
    """1차 실패(예외) → 재시도에서 성공하면 미해결 0."""
    _no_sleep()
    arts = _arts(2)
    llm = FakeLLM([RuntimeError("일시적 5xx"), _items((1, "IT"), (2, "POL"))])
    classify(llm, arts)
    assert llm.calls == 2, llm.calls  # 1차 + 재시도 1
    assert not any(a.classification_unresolved for a in arts)
    assert arts[0].category == "IT·테크·AI"


def test_retry_exhausted_marks_unresolved():
    """1차 + BATCH_RETRY회 모두 실패하면 그 배치는 미해결."""
    _no_sleep()
    arts = _arts(2)
    llm = FakeLLM([RuntimeError("지속 실패")])  # 모든 호출 실패
    classify(llm, arts)
    assert llm.calls == 1 + BATCH_RETRY, llm.calls
    assert all(a.classification_unresolved for a in arts)
    assert all(a.category is None for a in arts)


def test_partial_failure_only_failed_batch_unresolved():
    """여러 배치 중 일부만 실패: 성공 배치는 분류, 실패 배치만 미해결.

    CLASSIFY_BATCH_SIZE를 2로 낮춰 4건=2배치를 강제. 배치1 성공, 배치2 전부 실패.
    """
    _no_sleep()
    orig_size = classifier.CLASSIFY_BATCH_SIZE
    classifier.CLASSIFY_BATCH_SIZE = 2
    try:
        arts = _arts(4)
        # 배치1(id 1·2) 성공, 배치2(id 3·4) 1차+재시도 모두 실패
        llm = FakeLLM([_items((1, "IT"), (2, "POL")), RuntimeError("배치2 실패")])
        classify(llm, arts)
    finally:
        classifier.CLASSIFY_BATCH_SIZE = orig_size

    # 배치1: 분류됨
    assert arts[0].category == "IT·테크·AI" and arts[1].category == "정치"
    assert not arts[0].classification_unresolved and not arts[1].classification_unresolved
    # 배치2: 미해결 (genuine '기타' 아님)
    assert arts[2].classification_unresolved and arts[3].classification_unresolved
    assert arts[2].category is None and arts[3].category is None
    # 호출 수: 배치1 1회 + 배치2 (1+BATCH_RETRY)회
    assert llm.calls == 1 + (1 + BATCH_RETRY), llm.calls


if __name__ == "__main__":
    failures = 0
    tests = [
        test_success_no_retry,
        test_failed_batch_retried_then_recovers,
        test_retry_exhausted_marks_unresolved,
        test_partial_failure_only_failed_batch_unresolved,
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
