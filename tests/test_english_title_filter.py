"""영문판 기사 제외 회귀 테스트.

배경 (2026-07-26 운영): IT조선 RSS에 영문판 기사가 섞여 들어와 한국어 브리핑의
경제 분야 출처에 영문 제목이 그대로 노출됐다.
  - "Same Stock, So Why the Big Difference?"… Rising Volatility Sparks ...
  - "KOSDAQ Loses Ground, Plunging from 4th to 38th in Global Market Returns"

판정은 보수적이다 — 한글이 하나도 없을 때만 영문으로 본다. 한국 매체 제목은
영문 고유명사가 섞여도 한글이 남으므로(예: "SKT, AI 자율 네트워크 레벨4") 오탐되지
않는다. 이 테스트는 그 경계를 고정한다.

실행: python tests/test_english_title_filter.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collectors import is_english_title


# 실제 운영에서 노출된 영문판 기사 — 제외되어야 함
CASES_ENGLISH = [
    '"Same Stock, So Why the Big Difference?"… Rising Volatility Sparks '
    'Widely Divergent Brokerage Price Targets',
    "KOSDAQ Loses Ground, Plunging from 4th to 38th in Global Market Returns",
    "Samsung Electronics Begins Mass Production of HBM4 Memory Chips",
]

# 정상 한국어 제목 (영문 고유명사·약어 포함) — 반드시 보존되어야 함
CASES_KOREAN = [
    "SKT, AI 자율 네트워크 레벨4 추진…차세대 OSS 전환 박차",
    "삼성·SK하이닉스 '엎치락뒤치락' 시총 경쟁…승부처는 HBM4",
    "리사 수 CEO \"4년간 추론 성능 2000배 향상\"… MI500·젠 7 출격 예고 ［AMD AAI 2026］",
    "［속보］코스피 7000선 회복",
    "네이버, 소상공인에 1420억 임팩트 펀드 집행",
]

# 라틴 문자가 적은 짧은 제목 — 영문으로 오인하면 안 됨 (기호·약어·숫자만)
CASES_SHORT_NON_ENGLISH = [
    "AI·5G",
    "KOSPI 7000",
    "MI500",
    "",
    "2026-07-26",
]


def test_english_titles_detected():
    for title in CASES_ENGLISH:
        assert is_english_title(title), f"영문 기사를 못 걸렀다: {title}"


def test_korean_titles_preserved():
    """영문이 섞여도 한글이 있으면 절대 제외하지 않는다 (오탐 방지)."""
    for title in CASES_KOREAN:
        assert not is_english_title(title), f"한국어 기사가 제외됐다: {title}"


def test_short_latin_titles_not_flagged():
    """라틴 문자가 하한 미만이면 영문 기사로 보지 않는다."""
    for title in CASES_SHORT_NON_ENGLISH:
        assert not is_english_title(title), f"짧은 제목이 영문으로 오인됐다: {title}"


if __name__ == "__main__":
    failures = 0
    tests = [
        test_english_titles_detected,
        test_korean_titles_preserved,
        test_short_latin_titles_not_flagged,
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
