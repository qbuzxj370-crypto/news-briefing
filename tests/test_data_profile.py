"""data_profile 집계 테스트 (분석 품질 개선 그룹 2).

배경: 외부 평가 지적(#10) — 빈도·분포 같은 정량 근거 부재. 코드가 결정론적으로
집계해 프롬프트에 주입하는 데이터 프로필의 정확성이 인사이트 품질의 전제이므로
회귀 방지 테스트로 고정한다. LLM이 아니라 코드가 세는 값이라 100% 검증 가능.

data_profile은 collectors를 import하지 않으므로(덕 타이핑) 이 테스트는
feedparser 등 런타임 의존 없이 순수 실행된다.

실행: python tests/test_data_profile.py
"""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data_profile import (
    build_data_profile,
    keyword_frequency,
    _category_distribution,
    _media_distribution,
)


def _art(title="", category=None, source=None):
    return SimpleNamespace(title=title, category=category, source=source)


def test_category_and_media_distribution():
    arts = [
        _art(category="정치", source="동아일보"),
        _art(category="정치", source="경향신문"),
        _art(category="IT·테크·AI", source="동아일보"),
        _art(category=None, source="동아일보"),  # 미해결 → 분야 집계 제외
    ]
    cat = dict(_category_distribution(arts))
    assert cat == {"정치": 2, "IT·테크·AI": 1}, cat
    media = dict(_media_distribution(arts))
    assert media == {"동아일보": 3, "경향신문": 1}, media


def test_keyword_frequency_counts_titles_once():
    arts = [
        _art(title="삼성 HBM4 양산…AI 반도체 훈풍"),   # AI, 반도체, HBM 각 1
        _art(title="AI 모델 규제 논의"),                  # AI
        _art(title="인공지능 기본법 시행령"),             # AI (변이 인공지능)
        _art(title="환율 급등 비상"),                      # 환율
    ]
    freq = dict(keyword_frequency(arts))
    assert freq["AI"] == 3, freq          # 3개 제목에 AI/인공지능
    assert freq["반도체"] == 1, freq
    assert freq["HBM"] == 1, freq
    assert freq["환율"] == 1, freq
    # 등장 안 한 표제어는 키에 없음
    assert "원전" not in freq


def test_keyword_variant_counts_article_once():
    """한 제목에 같은 표제어 변이가 여러 번 나와도 1건."""
    arts = [_art(title="AI, 인공지능, 생성형 AI 총출동")]  # 모두 'AI' 표제어
    freq = dict(keyword_frequency(arts))
    assert freq["AI"] == 1, freq


def test_build_profile_empty_input():
    assert build_data_profile([]) == ""


def test_build_profile_contains_sections():
    arts = [
        _art(title="AI 반도체", category="IT·테크·AI", source="전자신문"),
        _art(title="환율 급등", category="경제·금융·증시", source="한국경제"),
    ]
    out = build_data_profile(arts)
    assert "[오늘의 데이터 프로필]" in out
    assert "분야별 기사 수 (총 2건)" in out
    assert "매체별 기사 수" in out
    assert "주요 키워드 빈도" in out
    # 전일 없음이면 증감 안내 문구 없음
    assert "전일 대비 증감" not in out


def test_build_profile_prev_delta():
    today = [
        _art(title="AI 반도체 HBM", category="IT·테크·AI", source="S"),
        _art(title="AI 규제", category="IT·테크·AI", source="S"),
        _art(title="환율", category="경제·금융·증시", source="S"),
    ]
    prev = [
        _art(title="AI 기본법", category="IT·테크·AI", source="S"),
        _art(title="부동산", category="사회·시사", source="S"),
    ]
    out = build_data_profile(today, prev_articles=prev)
    assert "전일 대비 증감" in out
    # IT 오늘 2 vs 전일 1 → (+1)
    assert "IT·테크·AI 2 (+1)" in out, out
    # AI 키워드 오늘 2 vs 전일 1 → (+1)
    assert "AI 2 (+1)" in out, out
    # 환율 오늘 1 vs 전일 0 → (+1)
    assert "환율 1 (+1)" in out, out


def test_delta_zero_omitted():
    """증감 0인 항목은 (+0) 병기 없이 값만."""
    today = [_art(title="환율", category="경제·금융·증시", source="S")]
    prev = [_art(title="환율", category="경제·금융·증시", source="S")]
    out = build_data_profile(today, prev_articles=prev)
    assert "경제·금융·증시 1" in out       # 값은 있고
    assert "(+0)" not in out               # 증감 0 병기는 없음
    assert "(-0)" not in out


if __name__ == "__main__":
    failures = 0
    tests = [
        test_category_and_media_distribution,
        test_keyword_frequency_counts_titles_once,
        test_keyword_variant_counts_article_once,
        test_build_profile_empty_input,
        test_build_profile_contains_sections,
        test_build_profile_prev_delta,
        test_delta_zero_omitted,
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
