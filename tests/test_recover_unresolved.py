"""미해결 부분집합 회복 재분류 테스트 (ADR-017, Phase 2a).

배경: 스냅샷 재개 시 '미해결'(분류 실패) 기사만 다시 분류해 회복한다.
genuine '기타'와 정상 분류분은 건드리지 않아야 한다(결정론 보존 — 분류는
temperature>0라 재굴림 시 라벨이 바뀜). 회복이 실패해도 파이프라인을 막지
않는다(graceful). 완전한 스냅샷이면 LLM을 아예 호출하지 않는다(수렴, 비용 0).

실행: python tests/test_recover_unresolved.py
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import classifier
from collectors import Article
from main import recover_unresolved

classifier.BATCH_RETRY_SLEEP = 0  # 테스트 속도 (재시도 대기 제거)

KST = timezone(timedelta(hours=9))


class FakeLLM:
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


def _resolved(cat):
    return Article(title="해결됨", summary="", link="https://x/r", source="동아일보",
                   published=datetime(2026, 6, 28, 8, tzinfo=KST),
                   category=cat, classification_unresolved=False)


def _genuine_misc():
    return Article(title="진짜기타", summary="", link="https://x/m", source="동아일보",
                   published=datetime(2026, 6, 28, 8, tzinfo=KST),
                   category="기타", classification_unresolved=False)


def _unresolved(i):
    return Article(title=f"미해결{i}", summary="", link=f"https://x/u{i}", source="동아일보",
                   published=datetime(2026, 6, 28, 8, tzinfo=KST),
                   category=None, classification_unresolved=True)


def _items(*pairs):
    objs = ", ".join(f'{{"id": {i}, "cat": "{c}", "cross": false}}' for i, c in pairs)
    return f'{{"items": [{objs}]}}'


def test_only_unresolved_reclassified():
    """미해결만 재분류되고, 정상 분류분·genuine 기타는 불변."""
    arts = [_resolved("정치"), _genuine_misc(), _unresolved(1), _unresolved(2)]
    # 부분집합 = [미해결1, 미해결2] → subset 내 id 1·2
    llm = FakeLLM([_items((1, "IT"), (2, "SOC"))])
    n = recover_unresolved(llm, arts)

    assert n == 2, n
    # 정상 분류분 불변
    assert arts[0].category == "정치" and arts[0].classification_unresolved is False
    # genuine 기타 불변 (재굴림 금지)
    assert arts[1].category == "기타" and arts[1].classification_unresolved is False
    # 미해결 → 회복(분류됨, 플래그 해제)
    assert arts[2].category == "IT·테크·AI" and arts[2].classification_unresolved is False
    assert arts[3].category == "사회·시사" and arts[3].classification_unresolved is False


def test_graceful_when_recovery_still_fails():
    """회복 시도가 또 실패해도 예외 없이, 미해결 상태를 유지한다."""
    arts = [_unresolved(1), _unresolved(2)]
    llm = FakeLLM([RuntimeError("LLM still down")])  # 모든 호출 실패
    n = recover_unresolved(llm, arts)  # 예외 안 남
    assert n == 2
    assert all(a.classification_unresolved for a in arts)
    assert all(a.category is None for a in arts)


def test_noop_when_complete():
    """미해결이 없으면 LLM을 호출하지 않는다 (수렴 — '매번 다시' 없음)."""
    arts = [_resolved("정치"), _genuine_misc()]
    llm = FakeLLM([RuntimeError("호출되면 안 됨")])
    n = recover_unresolved(llm, arts)
    assert n == 0
    assert llm.calls == 0
    assert arts[0].category == "정치" and arts[1].category == "기타"


def test_partial_recovery_some_still_unresolved():
    """부분집합 중 일부만 분류되면, 나머지는 미해결로 남는다."""
    arts = [_unresolved(1), _unresolved(2)]
    # subset id 1만 분류, 2는 응답 누락 → 여전히 미해결
    llm = FakeLLM([_items((1, "ECO"))])
    recover_unresolved(llm, arts)
    assert arts[0].category == "경제·금융·증시" and arts[0].classification_unresolved is False
    assert arts[1].classification_unresolved is True and arts[1].category is None


if __name__ == "__main__":
    failures = 0
    tests = [
        test_only_unresolved_reclassified,
        test_graceful_when_recovery_still_fails,
        test_noop_when_complete,
        test_partial_recovery_some_still_unresolved,
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
