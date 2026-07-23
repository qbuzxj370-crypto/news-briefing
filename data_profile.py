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
from typing import Any, Dict, Iterable, List, Optional, Tuple


# 도메인 키워드 사전 (표제어 → 표기 변이들). 제목에 변이 중 하나라도 있으면 1건.
# 형태소 분석기(kiwipiepy) 대신 큐레이션 목록을 쓰는 이유: 결정론적이고, 과분할
# 노이즈가 없으며, 무엇을 세는지 명시적이라 유지보수가 투명하고, CI에 무의존.
# 소규모 도메인 지식 자산 — 운영하며 표제어를 추가/조정한다.
# 주의: 부분 문자열 매칭이라 드물게 오탐 가능(예: 영문 'AI'가 긴 영단어 내부).
# 한국 기사 제목은 대부분 한글이라 실무 영향은 미미. 표제어는 이 트레이드오프를
# 감안해 변별력 있는 것만 담는다.
DOMAIN_TERMS: Dict[str, Tuple[str, ...]] = {
    "AI": ("AI", "인공지능", "생성형", "LLM"),
    "반도체": ("반도체", "칩"),
    "HBM": ("HBM",),
    "데이터센터": ("데이터센터", "IDC"),
    "전력": ("전력", "전기요금"),
    "원전": ("원전", "원자력", "SMR"),
    "배터리": ("배터리", "이차전지", "2차전지"),
    "환율": ("환율", "원/달러", "원달러"),
    "금리": ("금리",),
    "부동산": ("부동산", "아파트", "집값", "전세"),
    "증시": ("코스피", "코스닥", "증시", "주가"),
    "수출": ("수출",),
    "관세": ("관세",),
    "공급망": ("공급망", "희토류"),
    "중국": ("중국",),
    "미국": ("미국", "트럼프"),
    "북한": ("북한",),
    "국회": ("국회", "여야", "예산안"),
    "대통령": ("대통령", "대통령실"),
    "검찰": ("검찰", "특검"),
    "노동": ("노동", "노조", "파업"),
    "의료": ("의료", "의대", "병원"),
    "연금": ("연금",),
}

# 프로필에 노출할 상위 키워드 수 (프롬프트 길이 제어).
_KEYWORD_TOP_N = 12


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


def keyword_frequency(
    articles: Iterable[Any],
    terms: Dict[str, Tuple[str, ...]] = DOMAIN_TERMS,
) -> List[tuple]:
    """제목에 도메인 키워드가 등장한 기사 수를 (표제어, 건수) 내림차순으로.

    한 기사가 여러 표제어에 걸릴 수 있다(주제 중첩 — 분할이 아니라 관심도 측정).
    한 표제어의 변이가 제목에 여러 번 나와도 그 기사는 1건으로 센다.
    count 0인 표제어는 제외.
    """
    counter: Counter = Counter()
    for a in articles:
        title = getattr(a, "title", "") or ""
        for canonical, variants in terms.items():
            if any(v in title for v in variants):
                counter[canonical] += 1
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
    kw_freq = keyword_frequency(articles)
    if not cat_dist and not media_dist and not kw_freq:
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
    if kw_freq:
        lines.append(
            f"· 주요 키워드 빈도(제목 기준, 상위 {_KEYWORD_TOP_N}): "
            f"{_fmt_pairs(kw_freq[:_KEYWORD_TOP_N])}"
        )
    return "\n".join(lines)
