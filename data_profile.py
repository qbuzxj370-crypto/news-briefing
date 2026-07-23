"""수집·분류 완료 풀 → LLM 프롬프트용 '데이터 프로필' 텍스트 생성.

배경 (외부 분석 평가 #10): 현 브리핑은 기사를 '요약'만 하고 기사 집합을
'데이터처럼' 분석하지 못한다 — 빈도·분포·전일 대비 같은 정량 근거가 없어
인사이트가 "주목해야 한다" 류 일반론에 머문다.

설계 원칙 (ADR-001과 동일): 통계는 코드가 결정론적으로 집계해 프롬프트에
주입한다. LLM이 숫자를 생성하게 두지 않는다 — 환각 수치 차단. LLM은 이
프로필의 수치를 '인용'만 하고, 여기 없는 숫자는 만들지 않도록 지시받는다.

의존성 주의: 이 모듈은 collectors를 import하지 않는다 (feedparser 등 무거운
런타임 의존을 끌어오지 않아, 테스트가 CI 의존성 없이 순수 실행된다).
기사 객체는 .category / .source / .title 속성만 덕 타이핑으로 사용한다.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Iterable, List, Optional


def _category_distribution(articles: Iterable[Any]) -> List[tuple]:
    """분야별 기사 수를 (분야, 건수) 내림차순 리스트로. category 없으면 제외."""
    counter: Counter = Counter()
    for a in articles:
        cat = getattr(a, "category", None)
        if cat:
            counter[cat] += 1
    return counter.most_common()


def _media_distribution(articles: Iterable[Any]) -> List[tuple]:
    """매체별 기사 수를 (매체, 건수) 내림차순 리스트로. source 없으면 제외."""
    counter: Counter = Counter()
    for a in articles:
        src = getattr(a, "source", None)
        if src:
            counter[src] += 1
    return counter.most_common()


def _fmt_pairs(pairs: List[tuple]) -> str:
    return ", ".join(f"{name} {n}" for name, n in pairs)


def build_data_profile(articles: List[Any]) -> str:
    """수집·분류 완료 풀에서 데이터 프로필 텍스트 블록 생성.

    비어 있거나 집계할 게 없으면 빈 문자열 반환 (호출자가 프롬프트에서 생략).
    """
    if not articles:
        return ""

    cat_dist = _category_distribution(articles)
    media_dist = _media_distribution(articles)
    if not cat_dist and not media_dist:
        return ""

    total = len(articles)
    lines: List[str] = [
        "[오늘의 데이터 프로필]",
        "아래 수치는 코드가 집계한 사실이다. 인사이트·함의의 근거로 이 숫자를 인용하되,",
        "여기 없는 수치는 만들지 말 것. (분석 대상으로 선별되기 전, 수집·분류된 전체 풀 기준)",
    ]
    if cat_dist:
        lines.append(f"· 분야별 기사 수 (총 {total}건): {_fmt_pairs(cat_dist)}")
    if media_dist:
        lines.append(f"· 매체별 기사 수: {_fmt_pairs(media_dist)}")
    return "\n".join(lines)
