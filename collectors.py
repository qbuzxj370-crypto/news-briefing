"""RSS 수집 + 본문 크롤링 + 유사 제목 dedup."""

from __future__ import annotations

import html
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Union
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import feedparser
import requests
import trafilatura
from bs4 import BeautifulSoup

from timewindow import collect_window


# ----------------------------------------------------------------------
# 설정
# ----------------------------------------------------------------------
# RSS 피드 목록. 분야 분류는 LLM이 사후에 하므로 분야별 묶음 없음.
# 매체 다양성 + 본문 확보율 + 분야 다양성을 고려해 선정.
RSS_FEEDS: List[str] = [
    # IT/테크 중심 매체
    "https://it.chosun.com/rss/allArticle.xml",
    "https://rss.etnews.com/Section902.xml",
    # 경제/금융 중심 매체
    "https://rss.mt.co.kr/mt_news.xml",
    "https://www.hankyung.com/feed/economy",
    "https://www.mk.co.kr/rss/30000001/",
    # 정치 (검증 완료 — 섹션 RSS 깨끗함)
    "https://rss.donga.com/politics.xml",
    "https://www.khan.co.kr/rss/rssdata/politic_news.xml",
    "https://rss.ohmynews.com/rss/politics.xml",
    # 사회·시사 (검증 완료)
    "https://rss.donga.com/national.xml",
    "https://www.hani.co.kr/rss/society/",
    "https://www.khan.co.kr/rss/rssdata/society_news.xml",
]

# RSS URL 호스트명 → 매체명 매핑 (출처 표기용)
SOURCE_MAP: Dict[str, str] = {
    "rss.etnews.com": "전자신문",
    "etnews.com": "전자신문",
    "it.chosun.com": "IT조선",
    "rss.mt.co.kr": "머니투데이",
    "mt.co.kr": "머니투데이",
    "www.hankyung.com": "한국경제",
    "hankyung.com": "한국경제",
    "rss.hankyung.com": "한국경제",
    "www.mk.co.kr": "매일경제",
    "mk.co.kr": "매일경제",
    "rss.mk.co.kr": "매일경제",
    "rss.donga.com": "동아일보",
    "donga.com": "동아일보",
    "www.khan.co.kr": "경향신문",
    "khan.co.kr": "경향신문",
    "rss.ohmynews.com": "오마이뉴스",
    "ohmynews.com": "오마이뉴스",
    "www.yna.co.kr": "연합뉴스",
    "yna.co.kr": "연합뉴스",
    "www.hani.co.kr": "한겨레",
    "hani.co.kr": "한겨레",
}

# 보이지 않는 zero-width 문자 제거용 (BOM/ZWSP/ZWNJ/ZWJ/WORD JOINER).
# 일부 매체가 제목 선두에 U+FEFF를 붙여 보내 Notion 링크 텍스트에 그대로
# 노출됐다 (2026-06-04·06-09 운영 산출물에서 확인).
_ZERO_WIDTH_RE = re.compile("[\\ufeff\\u200b\\u200c\\u200d\\u2060]")


def clean_feed_title(raw: str) -> str:
    """RSS 제목 정제: HTML 엔티티 디코딩 + zero-width 문자 제거.

    디코딩은 dedup 전에 수행해야 한다 — 같은 기사가 피드마다 엔티티/디코딩
    형태로 갈리면 다른 제목으로 취급되어 dedup이 깨진다 (2026-06-03 결함).
    """
    return _ZERO_WIDTH_RE.sub("", html.unescape(raw)).strip()


def clean_feed_summary(raw: str) -> str:
    """RSS 요약 정제: HTML 태그 제거 → 엔티티 디코딩 → zero-width 제거.

    태그 제거가 디코딩보다 먼저여야 한다. 순서가 반대면 &lt;tag&gt;처럼
    텍스트로 쓰인 꺾쇠가 실제 태그로 풀린 뒤 삭제되어 데이터가 손실된다.
    """
    s = re.sub(r"<[^>]+>", "", raw).strip()
    return _ZERO_WIDTH_RE.sub("", html.unescape(s)).strip()


BODY_MIN_LEN = 300       # 본문 확보 성공 판정 기준
DEDUP_JACCARD = 0.7      # 유사 제목 dedup 임계값
PER_FEED_LIMIT = 30      # 한 피드당 처리할 최대 엔트리 (오래된 것 컷)
CRAWL_TIMEOUT = 10       # 본문 크롤링 타임아웃 (초)
UA_DESKTOP = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


# ----------------------------------------------------------------------
# robots.txt 준수 게이트 (ADR-018)
# ----------------------------------------------------------------------
# 기사 페이지 요청(본문 크롤링·날짜 폴백) 전에 해당 호스트의 robots.txt를
# 확인한다. RSS 피드 자체는 매체가 배포 목적으로 제공하므로 게이트 대상 아님.
#
# 판정 규칙 (RFC 9309 §2.3.1):
# - 200: 파싱해 URL별 판정. 우리 UA는 명명된 봇 그룹에 안 걸리므로 `*` 그룹 적용.
# - 4xx: robots.txt 부재/접근 불가 → 제한 없음으로 간주 (허용).
# - 5xx·네트워크 오류: 판단 불가 → 보수적으로 그 호스트 본문 수집 생략.
#   본문 실패는 요약 폴백(content_for_llm)이 흡수하므로 파이프라인은 계속된다.
#
# 주의: RobotFileParser.read()를 쓰면 안 된다 — urllib 기본 UA(Python-urllib)로
# 요청해 일부 매체(한겨레·매경 등)가 403을 주고, 그러면 read()가 전체 차단으로
# 오판한다 (2026-07-07 프로브로 확인). 반드시 requests + UA_DESKTOP으로 받는다.
_robots_cache: Dict[str, Union[RobotFileParser, str]] = {}  # host → parser | "allow" | "deny"


def _load_robots(host: str) -> Union[RobotFileParser, str]:
    try:
        resp = requests.get(
            f"https://{host}/robots.txt",
            timeout=CRAWL_TIMEOUT,
            headers={"User-Agent": UA_DESKTOP},
        )
    except Exception as e:
        print(f"  [robots] {host}: 로드 실패({type(e).__name__}) — 본문 수집 생략 (보수 판정)")
        return "deny"
    if resp.status_code >= 500:
        print(f"  [robots] {host}: HTTP {resp.status_code} — 본문 수집 생략 (보수 판정)")
        return "deny"
    if resp.status_code != 200:
        return "allow"
    rp = RobotFileParser()
    rp.parse(resp.text.splitlines())
    return rp


def _robots_allowed(url: str) -> bool:
    """url의 페이지 요청이 robots.txt상 허용되는지. 호스트별 캐시."""
    host = urlparse(url).netloc
    if not host:
        return False
    if host not in _robots_cache:
        _robots_cache[host] = _load_robots(host)
    entry = _robots_cache[host]
    if isinstance(entry, str):
        return entry == "allow"
    return entry.can_fetch(UA_DESKTOP, url)


# ----------------------------------------------------------------------
# 데이터 모델
# ----------------------------------------------------------------------
@dataclass
class Article:
    title: str
    summary: str
    link: str
    published: datetime
    source: str
    body: Optional[str] = None
    # LLM 분류 결과 (Phase 1b의 classifier가 채움)
    category: Optional[str] = None
    is_cross_category: bool = False
    # 분류 미해결(unresolved): 배치가 알고리즘적으로 실패해 LLM 판정을 못 받은
    # 상태 (ADR-017). genuine '기타'(LLM이 MISC로 판정)와 구분 — 회복(재분류)
    # 대상이며 분석에선 잠정 제외(category=None). 정상 분류 시 False.
    classification_unresolved: bool = False

    @property
    def has_body(self) -> bool:
        return self.body is not None and len(self.body) >= BODY_MIN_LEN

    @property
    def content_for_llm(self) -> str:
        """LLM에 넘길 본문. 본문 없으면 요약 사용."""
        return self.body if self.has_body else self.summary


# ----------------------------------------------------------------------
# 매체명 추출
# ----------------------------------------------------------------------
def _source_from_url(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if host in SOURCE_MAP:
        return SOURCE_MAP[host]
    # 도메인 일부 매칭 시도
    for key, name in SOURCE_MAP.items():
        if key in host:
            return name
    return host or "Unknown"


# ----------------------------------------------------------------------
# RSS 수집
# ----------------------------------------------------------------------
def _parse_published(entry) -> Optional[datetime]:
    """RSS 엔트리의 발행 시각 파싱.
    
    한국 매체는 타임존 표기가 비표준(+090 등)이거나 누락인 경우가 흔함.
    feedparser의 published_parsed는 타임존 파싱 실패 시 로컬 시각 그대로 담기므로
    한국 매체 전제로 KST(+09:00)를 기본 부여.
    """
    kst = timezone(timedelta(hours=9))
    
    for attr in ("published_parsed", "updated_parsed"):
        parsed = getattr(entry, attr, None)
        if parsed:
            # feedparser의 time_struct → naive datetime
            dt_naive = datetime(*parsed[:6])
            # feedparser가 타임존을 파싱했으면 tm_gmtoff(index 9)에 값이 있을 수 있으나
            # 대부분의 한국 매체는 파싱 실패 → 로컬 시각 그대로 들어옴
            # 안전하게 KST로 간주
            return dt_naive.replace(tzinfo=kst)
    
    # published_parsed도 updated_parsed도 없으면 published 문자열 직접 파싱 시도
    pub_str = getattr(entry, "published", None) or getattr(entry, "updated", None)
    if pub_str:
        # "2026-05-23 16:28:09" (타임존 없음, IT조선 형식)
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                dt = datetime.strptime(pub_str.strip(), fmt)
                return dt.replace(tzinfo=kst)
            except ValueError:
                continue
    
    return None


def _date_from_page(url: str) -> tuple:
    """기사 페이지에서 발행 날짜를 추출. (발행시각 or None, 본문 or None) 반환.

    배경 (2026-06-10 진단): 한겨레 RSS는 엔트리에 날짜 필드가 아예 없다
    (published/updated/dc:date 전무). _parse_published가 None을 반환해 전량
    버려졌고, 그 결과 한겨레가 산출물에서 며칠간 조용히 빠졌다(06-04~09).
    피드 자체는 정상이므로 기사 페이지의 메타데이터에서 날짜를 가져온다.

    - trafilatura.extract_metadata의 date는 'YYYY-MM-DD' (시각 없음).
      정오(12:00 KST)로 고정 부여 — 같은 날짜 문자열이면 언제 실행해도 같은
      윈도우 판정이 나오므로 ADR-015의 재실행 결정론이 유지된다.
      경계 오차(±수 시간)는 48h 윈도우 + dedup으로 흡수.
    - 추출 실패 시 (None, None) — 해당 엔트리는 보수적으로 스킵.
    - 내려받은 페이지는 본문 추출에 재사용해 추가 요청 비용을 상쇄
      (enrich_with_bodies가 이미 본문 있는 기사를 건너뜀).
    """
    kst = timezone(timedelta(hours=9))
    try:
        # robots.txt 차단 시 요청하지 않음 (ADR-018). 날짜를 못 얻으므로 해당
        # 엔트리는 수집에서 스킵된다 — "추정 금지" 원칙과 동일한 보수 처리.
        if not _robots_allowed(url):
            return None, None
        downloaded = trafilatura.fetch_url(url, no_ssl=True)
        if not downloaded:
            return None, None
        meta = trafilatura.extract_metadata(downloaded)
        date_str = meta.date if meta else None
        if not date_str:
            return None, None
        dt = datetime.strptime(date_str, "%Y-%m-%d").replace(hour=12, tzinfo=kst)
        body = trafilatura.extract(
            downloaded, include_comments=False, include_tables=False
        )
        return dt, (body.strip() if body else None)
    except Exception:
        return None, None


def collect_articles() -> List[Article]:
    """수집 윈도우(예정 시각 07:00 KST 기준 48시간) 내 발행 기사를 한 풀로 수집.
    
    분야 분류는 LLM이 사후에 수행 (classifier.py). 여기서는 category=None.
    
    윈도우는 timewindow.collect_window가 계산 (실행 시각이 아니라 cron 예정
    시각 기준이라 재실행/지연에도 표본 고정). 48시간 폭은 RSS 반영 지연·매체
    타임존 오차 보상용이며, 날짜 간 중복은 dedup이 제거.
    """
    start, end = collect_window()

    print(f"[수집 범위] {start.isoformat()} ~ {end.isoformat()}")

    all_articles: List[Article] = []
    source_stats: Dict[str, int] = {}

    for feed_url in RSS_FEEDS:
        source = _source_from_url(feed_url)
        feed_count = 0
        parsed = None
        try:
            # 일부 매체는 UA 있으면 오히려 차단. 2번 시도.
            headers = {"User-Agent": UA_DESKTOP}
            parsed = feedparser.parse(feed_url, request_headers=headers)
            if not parsed.entries:
                # UA 없이 재시도
                parsed = feedparser.parse(feed_url)
            for entry in parsed.entries[:PER_FEED_LIMIT]:
                pub_kst = _parse_published(entry)
                prefetched_body = None
                if pub_kst is None and entry.get("link"):
                    # RSS에 날짜가 없는 매체(한겨레) — 기사 페이지에서 추출.
                    # 실패하면 스킵 (윈도우 고정 원칙 유지를 위해 추정 금지).
                    pub_kst, prefetched_body = _date_from_page(entry["link"])
                    time.sleep(0.2)  # 페이지 요청 간 매체 부담 완화
                if pub_kst is None:
                    continue
                if pub_kst < start or pub_kst >= end:
                    continue
                title = clean_feed_title(entry.get("title", ""))
                if not title:
                    continue
                all_articles.append(Article(
                    title=title,
                    summary=clean_feed_summary(entry.get("summary", "")),
                    link=entry.get("link", ""),
                    published=pub_kst,
                    source=source,
                    body=prefetched_body,
                    # category는 None (LLM 분류 후 채움)
                ))
                feed_count += 1
        except Exception as e:
            print(f"  [경고] {feed_url} 수집 실패: {e}")

        if feed_count == 0:
            # 주요 매체가 48h 윈도우에서 0건이면 비정상 (피드 사망/차단 의심).
            # 한겨레 피드가 0건인 채 수 일간 조용히 지나간 사례(2026-06-04~09)
            # 재발 방지용 가시화 — 실패가 아니라 경고만 남긴다.
            # http 상태·원시 엔트리 수로 원인 구분: 엔트리 0 + 4xx → IP/UA 차단,
            # 엔트리 N>0인데 수집 0 → 발행시각 파싱 실패 또는 전부 윈도우 밖.
            status = getattr(parsed, "status", "?") if parsed is not None else "?"
            raw_n = len(parsed.entries) if parsed is not None else 0
            print(
                f"  [경고] {source} 수집 0건 — 피드 점검 필요: {feed_url} "
                f"(http={status}, 원시 엔트리 {raw_n}건)"
            )

        source_stats[source] = source_stats.get(source, 0) + feed_count

    # 정확 일치 제목 dedup
    seen_titles = set()
    unique = []
    for a in all_articles:
        if a.title not in seen_titles:
            seen_titles.add(a.title)
            unique.append(a)

    unique.sort(key=lambda a: a.published, reverse=True)

    print(f"  총 {len(unique)}건 수집 (정확 일치 dedup 후)")
    for src, cnt in sorted(source_stats.items(), key=lambda x: -x[1]):
        print(f"    {src}: {cnt}건")

    return unique


# ----------------------------------------------------------------------
# 본문 크롤링 (3단계 재시도)
# ----------------------------------------------------------------------
def _fetch_with_trafilatura(url: str) -> Optional[str]:
    """1단계: trafilatura 기본 fetch + extract."""
    try:
        downloaded = trafilatura.fetch_url(url, no_ssl=True)
        if not downloaded:
            return None
        return trafilatura.extract(downloaded, include_comments=False, include_tables=False)
    except Exception:
        return None


def _fetch_with_requests_trafilatura(url: str) -> Optional[str]:
    """2단계: requests로 받아서 trafilatura.extract."""
    try:
        resp = requests.get(url, timeout=CRAWL_TIMEOUT, headers={"User-Agent": UA_DESKTOP})
        if resp.status_code != 200:
            return None
        return trafilatura.extract(resp.text, include_comments=False, include_tables=False)
    except Exception:
        return None


def _fetch_with_bs4(url: str) -> Optional[str]:
    """3단계: BeautifulSoup 휴리스틱 본문 추출 (최후 수단)."""
    try:
        resp = requests.get(url, timeout=CRAWL_TIMEOUT, headers={"User-Agent": UA_DESKTOP})
        if resp.status_code != 200:
            return None
        soup = BeautifulSoup(resp.text, "html.parser")
        # 후보 셀렉터들 (한국 매체 공통)
        candidates = soup.select(
            "article, div#article-view-content-div, div.article_view, "
            "div.news_view, div#articleBody, div.article-body, "
            "div[itemprop='articleBody'], section.article-body"
        )
        if not candidates:
            # p 태그만 모아서 휴리스틱
            paragraphs = soup.find_all("p")
            text = "\n".join(p.get_text(strip=True) for p in paragraphs)
        else:
            text = "\n".join(c.get_text(separator="\n", strip=True) for c in candidates)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text if text.strip() else None
    except Exception:
        return None


def fetch_body(url: str) -> Optional[str]:
    """3단계 재시도로 본문 추출. 성공 시 본문, 실패 시 None.

    robots.txt가 차단하는 URL은 요청 자체를 하지 않는다 (ADR-018).
    """
    if not _robots_allowed(url):
        return None
    for fn in (_fetch_with_trafilatura, _fetch_with_requests_trafilatura, _fetch_with_bs4):
        body = fn(url)
        if body and len(body) >= BODY_MIN_LEN:
            return body.strip()
    return None


def enrich_with_bodies(
    articles: List[Article],
    sleep_sec: float = 0.3,
    target: Optional[int] = None,
    max_attempts: Optional[int] = None,
) -> None:
    """기사 리스트에 in-place로 본문 채움.

    수집 단계에서 이미 본문을 확보한 기사(날짜 추출 위해 페이지를 받은
    한겨레 — _date_from_page)는 다시 요청하지 않는다.

    target이 주어지면 선별 후보 모드 (ADR-018): 최신 기사부터 시도해
    본문 확보 건수가 target에 닿거나 시도가 max_attempts(기본 target*3)에
    닿으면 중단한다. 분류·dedup 후 분야별 후보에만 호출해 기사 페이지
    요청 수를 최소화하기 위함 — 전량(수백 건) 크롤링하던 구버전 동작은
    target=None. 시도 상한은 크롤링 실패가 연속돼도 요청 폭주를 막는 안전망.
    """
    if target is not None and max_attempts is None:
        max_attempts = target * 3
    got = sum(1 for a in articles if a.has_body)
    attempts = 0
    for a in sorted(articles, key=lambda a: a.published, reverse=True):
        if target is not None and (got >= target or attempts >= max_attempts):
            break
        if not a.link or a.has_body:
            continue
        attempts += 1
        a.body = fetch_body(a.link)
        if a.has_body:
            got += 1
        time.sleep(sleep_sec)  # 매체 부담 줄이기


# ----------------------------------------------------------------------
# 유사 제목 dedup (Jaccard)
# ----------------------------------------------------------------------
_TOKEN_RE = re.compile(r"[가-힣A-Za-z0-9]+")
_STOPWORDS = {"의", "이", "가", "을", "를", "에", "에서", "은", "는", "도", "와", "과", "the", "a", "an"}


def _tokenize(title: str) -> set:
    tokens = _TOKEN_RE.findall(title.lower())
    return {t for t in tokens if len(t) >= 2 and t not in _STOPWORDS}


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def dedup_similar(articles: List[Article], threshold: float = DEDUP_JACCARD) -> List[Article]:
    """유사 제목 dedup. 본문 확보된 기사를 우선 보존, 매체 다양성 고려."""
    result: List[Article] = []
    token_sets: List[set] = []

    # 본문 확보된 기사부터 정렬
    sorted_articles = sorted(articles, key=lambda a: (not a.has_body, a.title))

    for art in sorted_articles:
        tokens = _tokenize(art.title)
        is_dup = False
        for i, existing_tokens in enumerate(token_sets):
            if _jaccard(tokens, existing_tokens) >= threshold:
                # 이미 비슷한 게 있음. 새 기사가 본문 있고 기존이 없으면 교체
                existing = result[i]
                if art.has_body and not existing.has_body:
                    result[i] = art
                    token_sets[i] = tokens
                is_dup = True
                break
        if not is_dup:
            result.append(art)
            token_sets.append(tokens)

    return result


# ----------------------------------------------------------------------
# 하이브리드 보충
# ----------------------------------------------------------------------
def select_for_analysis(
    articles: List[Article],
    min_with_body: int = 5,
    max_total: int = 12,
) -> List[Article]:
    """본문 확보 기사 우선 선택, 부족하면 요약만 있는 기사로 보충.
    
    - 본문 기사 >= min_with_body: 본문 기사만으로 max_total까지 채움
    - 본문 기사 < min_with_body: 본문 기사 전부 + 요약 기사로 max_total까지 채움
    """
    with_body = [a for a in articles if a.has_body]
    without_body = [a for a in articles if not a.has_body]

    # 최신순 정렬
    with_body.sort(key=lambda a: a.published, reverse=True)
    without_body.sort(key=lambda a: a.published, reverse=True)

    if len(with_body) >= min_with_body:
        return with_body[:max_total]

    selected = list(with_body)
    needed = max_total - len(selected)
    selected.extend(without_body[:needed])
    return selected


# ----------------------------------------------------------------------
# 엔트리포인트 (수동 점검용)
# ----------------------------------------------------------------------
# 본문 크롤링은 분류·dedup 후 분야별 후보에만 수행한다 (ADR-018 —
# main.py step 3에서 enrich_with_bodies(target=...) 호출). 여기서는
# 수집만 점검하고, 본문 크롤링은 상위 몇 건만 샘플로 확인한다.
if __name__ == "__main__":
    arts = collect_articles()
    print(f"\n총 {len(arts)}건")
    enrich_with_bodies(arts, target=5)
    for a in arts[:10]:
        tag = "[본문]" if a.has_body else "[요약만]"
        print(f"  {tag}[{a.source}] {a.title[:60]}")