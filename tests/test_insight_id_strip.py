"""insight 본문 ID 누출 결함 회귀 테스트.

배경 (ADR-001 본문-출처 미검증 관련): insight는 referenced_article_ids 필드가
없는 자유 서술인데, system_prompt의 "출처는 article_id로 표시" 지침을 LLM이
적용해 '(`[46]`)' 같은 ID를 본문에 그대로 박는다. renderer._clean_insight_ids가
1차 안전망(프롬프트 명시 금지)을 우회한 잔여 표기를 2차로 제거.

이 테스트는 2026-05-27 실제 운영 산출물에서 추출한 케이스로 정규식 검증.

실행: python tests/test_insight_id_strip.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from renderer import _clean_insight_ids


# 2026-05-27 실제 운영 산출물에서 추출한 패턴
CASES_LEAK = [
    # 정치 인사이트 - 백틱+괄호 단일 ID
    (
        "캐나다 알루미늄의 유럽행 급증(`[46]`)은 미국의 관세와 이란 전쟁의 복합적인 결과로, 글로벌 원자재 시장의 재편을 가속화하고 있음을 보여줍니다.",
        "캐나다 알루미늄의 유럽행 급증은 미국의 관세와 이란 전쟁의 복합적인 결과로, 글로벌 원자재 시장의 재편을 가속화하고 있음을 보여줍니다.",
    ),
    # 사회 인사이트 - 한 문장에 2개 ID
    (
        "공공주택 건설 지연(`[37]`)은 단순히 건설 산업의 문제를 넘어, 서소문 고가차도 붕괴 사고(`[35]`)에서 드러난 안전 불감증과 중대재해처벌법의 복합적인 영향으로 이해해야 합니다.",
        "공공주택 건설 지연은 단순히 건설 산업의 문제를 넘어, 서소문 고가차도 붕괴 사고에서 드러난 안전 불감증과 중대재해처벌법의 복합적인 영향으로 이해해야 합니다.",
    ),
    # 산업 인사이트 - 콤마 구분 다중 ID + 단일 ID 혼합
    (
        "주요 기업들의 노사 갈등(`[42], [43]`)은 단순히 임금 인상률을 넘어 보상 체계의 불균형(`[41]`)과 경영권 사안까지 포괄하는 구조적 문제로 심화되고 있습니다.",
        "주요 기업들의 노사 갈등은 단순히 임금 인상률을 넘어 보상 체계의 불균형과 경영권 사안까지 포괄하는 구조적 문제로 심화되고 있습니다.",
    ),
]

# 회귀 방지: ID 없는 정상 인사이트
CASES_NORMAL = [
    "AI 에이전트의 확산은 기업 내부 시스템의 근본적인 보안 아키텍처 변화를 촉진할 것이며, 이는 향후 12개월 내 기업들의 IT 예산에서 AI 보안 솔루션 도입 비중을 크게 늘릴 가능성이 높습니다.",
    "기술 혁신이 가져오는 경제적 이익을 어떻게 공정하게 분배하고 조직의 결속력을 유지할 것인가에 대한 중요한 시험대가 될 것이다.",
]

# 다양한 변형 패턴 (1차 안전망 우회 가능성)
CASES_VARIANT = [
    # 백틱 없음
    ("정책 지연([46])이 산업에 영향을 미친다.", "정책 지연이 산업에 영향을 미친다."),
    # 괄호 없이 단독
    ("정책 지연 [46] 이 영향을 미친다.", "정책 지연 이 영향을 미친다."),
    # 백틱만, 괄호 없음
    ("정책 지연 `[46]` 이 영향을 미친다.", "정책 지연 이 영향을 미친다."),
]


def test_leak_cases_stripped():
    """실제 운영 결함 패턴이 제거되어야 한다."""
    for input_text, expected in CASES_LEAK:
        result = _clean_insight_ids(input_text)
        assert result == expected, (
            f"\n  입력: {input_text}\n  기대: {expected}\n  실제: {result}"
        )


def test_normal_unchanged():
    """ID 없는 정상 인사이트는 변형되지 않아야 한다."""
    for text in CASES_NORMAL:
        result = _clean_insight_ids(text)
        assert result == text, (
            f"\n  정상 텍스트가 변형됨\n  입력: {text}\n  실제: {result}"
        )


def test_variants_stripped():
    """1차 안전망(프롬프트) 우회 변형도 코드가 잡아야 한다."""
    for input_text, expected in CASES_VARIANT:
        result = _clean_insight_ids(input_text)
        assert result == expected, (
            f"\n  입력: {input_text}\n  기대: {expected}\n  실제: {result}"
        )


if __name__ == "__main__":
    failures = 0
    for fn in [test_leak_cases_stripped, test_normal_unchanged, test_variants_stripped]:
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