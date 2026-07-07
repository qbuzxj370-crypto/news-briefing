"""RSS URL 후보 검증 + 분야 매칭 검사.

각 매체별 후보 RSS를 시험하고, 작동하는 피드에 대해 샘플 제목 5건을 출력한다.
운영자가 직접 보고 "이 피드가 정말 이 분야 기사만 담고 있는지" 판정 가능.

전체 피드(`allArticle`, `mt_news` 등)는 사회·정치 기사가 섞여 들어와
분야 분류 누수를 일으키므로 반드시 섹션별 피드로 교체해야 한다.

사용:
    python rss_probe.py
"""

import feedparser

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

# 분야별 RSS URL 후보
# - "현재": 운영 중인 RSS (검증 대상)
# - "후보": 섹션별 RSS / 대체 매체
CANDIDATES = {
    "IT·테크·AI": [
        # 현재 운영
        ("https://it.chosun.com/rss/allArticle.xml", "IT조선 전체 (allArticle, 누수 의심)"),
        ("https://rss.etnews.com/Section902.xml", "전자신문 902"),
        ("https://rss.etnews.com/Section901.xml", "전자신문 901"),
        # IT조선 섹션별 후보 (S1N127류는 IT조선의 카테고리 ID 추정)
        ("https://it.chosun.com/rss/section_S1N127.xml", "IT조선 IT 섹션 후보 1"),
        ("https://it.chosun.com/rss/section_S1N52.xml", "IT조선 IT 섹션 후보 2"),
        ("https://it.chosun.com/rss/section_S1N42.xml", "IT조선 IT 섹션 후보 3"),
        # 전자신문 추가 섹션 (902가 IT 메인 섹션. 901은 다른 분야일 가능성)
        ("https://rss.etnews.com/Section904.xml", "전자신문 904"),
        ("https://rss.etnews.com/Section905.xml", "전자신문 905"),
        # 머니투데이 IT 섹션 후보
        ("https://rss.mt.co.kr/mt_news_T03.xml", "머니투데이 IT 후보 T03"),
        ("https://rss.mt.co.kr/mt_news_T05.xml", "머니투데이 IT 후보 T05"),
    ],
    "경제·금융·증시": [
        # 현재 운영
        ("https://rss.mt.co.kr/mt_news.xml", "머니투데이 전체 (mt_news, 누수 의심)"),
        ("https://www.hankyung.com/feed/economy", "한경 경제"),
        ("https://www.mk.co.kr/rss/30000001/", "매경 30000001"),
        # 머니투데이 섹션별 후보
        ("https://rss.mt.co.kr/mt_news_T01.xml", "머니투데이 경제 후보 T01"),
        ("https://rss.mt.co.kr/mt_news_T02.xml", "머니투데이 경제 후보 T02"),
        ("https://rss.mt.co.kr/mt_news_T07.xml", "머니투데이 증권 후보 T07"),
        ("https://rss.mt.co.kr/mt_news_E01.xml", "머니투데이 경제 후보 E01"),
        # 한경 추가
        ("https://www.hankyung.com/feed/finance", "한경 금융"),
        ("https://www.hankyung.com/feed/stock", "한경 증권"),
        # 매경 추가 카테고리
        ("https://www.mk.co.kr/rss/40300001/", "매경 40300001"),
    ],
    "정치": [
        # 현재 운영 (다 섹션 피드)
        ("https://rss.donga.com/politics.xml", "동아 정치"),
        ("https://www.khan.co.kr/rss/rssdata/politic_news.xml", "경향 정치"),
        ("https://rss.ohmynews.com/rss/politics.xml", "오마이뉴스 정치"),
        # 추가 후보
        ("https://www.hankyung.com/feed/politics", "한경 정치"),
        ("https://rss.joins.com/joins_politics_list.xml", "중앙 정치"),
    ],
    "사회·시사": [
        # 현재 운영
        ("https://www.yna.co.kr/rss/society.xml", "연합 사회"),
        ("https://rss.donga.com/national.xml", "동아 사회 (national)"),
        ("https://www.hani.co.kr/rss/society/", "한겨레 사회"),
        # 추가 후보
        ("https://www.khan.co.kr/rss/rssdata/society_news.xml", "경향 사회"),
    ],
    "산업": [
        # 현재 운영
        ("https://www.mk.co.kr/rss/50100032/", "매경 50100032"),
        ("https://rss.etnews.com/Section903.xml", "전자신문 903"),
        # 한경 추가 후보
        ("https://www.hankyung.com/feed/industry", "한경 산업"),
        # 매경 다른 카테고리 ID 후보
        ("https://www.mk.co.kr/rss/50300009/", "매경 50300009"),
    ],
}


def probe(url: str):
    """URL 시험. (엔트리 수, 상태, 샘플 제목 5건) 반환."""
    try:
        p = feedparser.parse(url, request_headers={"User-Agent": UA})
        if p.entries:
            samples = [e.get("title", "(제목 없음)") for e in p.entries[:5]]
            return len(p.entries), "OK", samples
        status = getattr(p, "status", "?")
        return 0, f"empty (http={status}, bozo={p.bozo})", []
    except Exception as e:
        return 0, f"error: {type(e).__name__}: {e}", []


def main():
    print("=" * 80)
    print("RSS URL 검증 + 분야 매칭 검사")
    print("=" * 80)
    print()
    print("각 작동 피드의 샘플 제목을 보고 '정말 이 분야 기사만 담는지' 판정하세요.")
    print("다른 분야 기사가 섞여 있으면 분야 분류 누수. 더 좁은 섹션 피드로 교체 필요.")
    print()

    for category, items in CANDIDATES.items():
        print(f"\n{'=' * 80}")
        print(f"분야: {category}")
        print("=" * 80)

        for url, label in items:
            n, status, samples = probe(url)
            marker = "✅" if n > 0 else "❌"
            print(f"\n  {marker} [{label}]")
            print(f"     URL: {url}")
            print(f"     상태: {n}건, {status}")
            if samples:
                print(f"     샘플 제목 (이 분야 맞는지 확인):")
                for i, title in enumerate(samples, 1):
                    print(f"        {i}. {title[:60]}")

    print()
    print("=" * 80)
    print("판정 가이드")
    print("=" * 80)
    print("""
- '분야: IT·테크·AI'에서 샘플에 스타벅스/노조/외교 기사 보임 → 누수. 교체.
- '분야: 경제·금융·증시'에서 샘플에 사회/정치 기사 보임 → 누수. 교체.
- 샘플 5건 모두 그 분야 기사 → 안전. 채택 후보.
- 같은 매체에서 여러 섹션 후보가 다 작동하면 가장 분야 적합도 높은 것 선택.

판정 후 collectors.py의 RSS_FEEDS를 안전한 것들로 갱신하세요.
""")


if __name__ == "__main__":
    main()