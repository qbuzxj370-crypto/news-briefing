"""LLM JSON 출력 + Article 매핑 → 마크다운 렌더링.

LLM은 article_id만 반환. 매체명/링크/기사 제목은 코드가 SOURCE_MAP과
id_to_article로 결정론적으로 매핑한다. 출처 무결성은 LLM이 아닌 코드 책임.

Jinja2 템플릿(report.md.j2) 사용. 토글은 마크다운 마커("▶ ###")로 표시하고
Notion 변환 단계에서 is_toggleable=True로 변환.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from collectors import Article


# ----------------------------------------------------------------------
# 분야별 이모지 매핑
# ----------------------------------------------------------------------
CATEGORY_EMOJI: Dict[str, str] = {
    "IT·테크·AI": "💻",
    "경제·금융·증시": "💰",
    "정치": "🏛️",
    "사회·시사": "🗞️",
    "산업": "🏭",
}


# ----------------------------------------------------------------------
# 출처 매핑
# ----------------------------------------------------------------------
def _clean_insight_ids(text: str) -> str:
    """자유 서술 본문에 새어나온 기사 ID 참조([46], (`[42], [43]`) 등)를 제거.

    배경 (ADR-001 본문-출처 미검증 관련): insight는 deep/minor_issues와 달리
    referenced_article_ids 같은 구조화된 출처 필드가 없는 자유 서술이다.
    그런데 system_prompt의 "출처는 article_id로 표시" 지침을 LLM이 insight에도
    적용해 '(`[46]`)' 같은 ID를 본문에 인라인으로 박는다(2026-05-27 운영서 확인).
    renderer는 insight를 가공 없이 출력하므로 사용자에게 내부 ID가 그대로 노출됐다.

    더 문제는 insight가 분야 전체 종합이라 그 ID가 해당 분야 입력에 없는 경우도
    있어(정치 insight의 [46]=알루미늄 등) 매체명 치환 대상조차 불명확하다는 점.
    따라서 치환이 아니라 '제거'한다 — insight는 종합 통찰이라 개별 출처가 불필요.
    프롬프트(방향 A)로 ID 사용을 막고, 그래도 새어나온 것을 여기서 정리(2차 안전망).

    2026-06-10 확장: 같은 누출이 mega_trend에서도 발생(06-09 운영 산출물의
    '(SK그룹의 뉴 이천포럼, [40])' 등 — insight만 정리하던 사각지대). 이 함수는
    insight 외에 tldr·mega_trend(summary, key_threads)에도 적용되며,
    '(서술, [N])' 형태에서 ID 앞 콤마까지 함께 제거해 '(서술)'로 남긴다.
    """
    # (`[N]`) 또는 (`[N], [M]`) 괄호로 감싼 형태 통째 제거
    text = re.sub(r"\s*\(\s*`?\[[\d,\s\[\]]+\]`?\s*\)", "", text)
    # 백틱으로 감싼 잔여 `[N]`
    text = re.sub(r"`\[[\d,\s\[\]]+\]`", "", text)
    # 단독 [N] — 앞에 콤마가 붙은 '(서술, [N])' 꼴이면 콤마까지 제거
    text = re.sub(r",?\s*\[\d+\]", "", text)
    # 제거 후 생긴 이중 공백 정리
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


def _sanitize_link_text(text: str) -> str:
    """Notion 인라인 파서가 [text](url) 링크를 깨뜨리지 않도록 링크 텍스트의
    대괄호를 전각 괄호로 치환.

    배경 (ADR-002 미해결 이슈): notion_writer의 인라인 정규식은
    링크 텍스트를 [^\\]]+ (닫는 대괄호가 아닌 문자)로만 본다. 한국 기사 제목에
    흔한 '[속보]', '[단독]'이 [text](url)의 text에 들어가면 중첩 대괄호가 되어
    링크 매칭이 통째로 실패하고, 그 줄이 평문으로 떨어져 출처 링크가 소실된다.

    해법으로 전각 괄호(［ ］) 치환을 택한 이유 (재현 검증 결과):
    - 표준 마크다운 이스케이프(\\[)는 자체 파서가 백슬래시를 해석 못 해 실패
    - 정규식 보강(.+? 또는 ](경계)은 매체명 라벨까지 링크에 빨려들고 정상 케이스 회귀
    - 전각 치환만이 파서 무수정 + URL 무변경 + 시각 보존으로 통과
    URL에는 적용하지 않는다(URL에 대괄호가 들어갈 일이 없고, 변형 시 링크가 깨짐).
    """
    return text.replace("[", "［").replace("]", "］")


def _map_sources(
    article_ids: List[int],
    id_to_article: Dict[int, Article],
) -> List[Dict[str, str]]:
    """article_id 리스트를 {name, title, url} 리스트로 변환.
    
    - id_to_article에 없는 id는 무시
    - 매체명 기준 중복 제거 (같은 매체 여러 기사라도 한 줄에 안 묶이게,
      대신 각 기사마다 항목 생성)
    - title은 링크 텍스트로 쓰이므로 대괄호를 전각으로 치환 (_sanitize_link_text)
    """
    sources: List[Dict[str, str]] = []
    for aid in article_ids:
        art = id_to_article.get(aid)
        if art is None:
            continue
        sources.append({
            "name": art.source,
            "title": _sanitize_link_text(art.title),
            "url": art.link,
        })
    return sources


# ----------------------------------------------------------------------
# 전처리: LLM JSON → 템플릿 입력 구조
# ----------------------------------------------------------------------
def _build_template_context(
    date_str: str,
    analysis: Dict[str, Any],
    id_to_article: Dict[int, Article],
    collected_sources: List[str],
) -> Dict[str, Any]:
    """LLM JSON 분석 결과를 Jinja2 템플릿 입력 구조로 변환."""
    # 분야별 이모지 + 출처 매핑 주입
    categories_ctx = []
    for cat in analysis.get("categories", []):
        # deep_issues 출처 매핑
        deep_with_sources = []
        for issue in cat.get("deep_issues", []):
            deep_with_sources.append({
                **issue,
                "sources": _map_sources(
                    issue.get("referenced_article_ids", []),
                    id_to_article,
                ),
            })

        # minor_issues 출처 매핑
        minor_with_sources = []
        for issue in cat.get("minor_issues", []):
            minor_with_sources.append({
                **issue,
                "sources": _map_sources(
                    issue.get("referenced_article_ids", []),
                    id_to_article,
                ),
            })

        categories_ctx.append({
            "name": cat.get("name", ""),
            "emoji": CATEGORY_EMOJI.get(cat.get("name", ""), "📂"),
            "has_sufficient_data": cat.get("has_sufficient_data", True),
            "limitation_note": cat.get("limitation_note"),
            "key_flows": cat.get("key_flows", []),
            "deep_issues": deep_with_sources,
            "minor_issues": minor_with_sources,
            "insight": _clean_insight_ids(cat.get("insight", "")),
        })

    # tldr·mega_trend도 insight와 같은 자유 서술이라 ID 누출 정리 대상
    # (2026-06-09 운영 산출물에서 mega_trend 누출 확인)
    mega_raw = analysis.get("mega_trend", {"summary": "", "key_threads": []})
    mega_trend = {
        **mega_raw,
        "summary": _clean_insight_ids(mega_raw.get("summary", "")),
        "key_threads": [
            _clean_insight_ids(t) for t in mega_raw.get("key_threads", [])
        ],
    }

    return {
        "date_str": date_str,
        "tldr": _clean_insight_ids(analysis.get("tldr", "")),
        "mega_trend": mega_trend,
        "categories": categories_ctx,
        "source_list_str": " · ".join(collected_sources),
    }


# ----------------------------------------------------------------------
# 메인 렌더러
# ----------------------------------------------------------------------
def render_report(
    date_str: str,
    analysis: Dict[str, Any],
    id_to_article: Dict[int, Article],
) -> str:
    """LLM JSON 분석 결과를 마크다운 문자열로 렌더링.
    
    Args:
        date_str: "2026-05-22" 형식 날짜
        analysis: analyzer.analyze()가 반환한 검증된 JSON
        id_to_article: {article_id: Article} 매핑
    
    Returns:
        Jinja2로 렌더링된 마크다운 문자열. 토글 마커 포함.
    """
    if analysis is None:
        # 수집 0건 또는 분석 실패
        return _render_empty(date_str)

    # 수집된 매체 목록 추출 (중복 제거, 정렬 보존)
    seen = set()
    collected_sources = []
    for art in id_to_article.values():
        if art.source not in seen:
            seen.add(art.source)
            collected_sources.append(art.source)

    context = _build_template_context(
        date_str=date_str,
        analysis=analysis,
        id_to_article=id_to_article,
        collected_sources=collected_sources,
    )

    template_dir = Path(__file__).parent / "templates"
    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        undefined=StrictUndefined,
        trim_blocks=False,
        lstrip_blocks=False,
        keep_trailing_newline=True,
    )
    template = env.get_template("report.md.j2")
    return template.render(**context)


def _render_empty(date_str: str) -> str:
    """수집 0건 / 분석 실패 시 안내 페이지."""
    return (
        f"# 📰 {date_str} 데일리 브리핑\n\n"
        f"> ⚠️ **수집된 기사 없음**\n"
        f"> 어제 RSS 피드에서 기사를 수집하지 못했습니다.\n"
        f"> 피드 상태 및 네트워크를 확인해 주세요.\n"
    )


# ----------------------------------------------------------------------
# 단독 실행 (목업 데이터로 템플릿 검증)
# ----------------------------------------------------------------------
if __name__ == "__main__":
    from datetime import datetime, timezone, timedelta

    # 가짜 Article들로 출처 매핑 테스트
    mock_articles = {
        1: Article(
            title="삼성전자, HBM4 12단 양산 본격화",
            summary="...",
            link="https://www.etnews.com/20260522000123",
            published=datetime.now(timezone(timedelta(hours=9))),
            category="IT·테크·AI",
            source="전자신문",
            body="(본문 생략)" * 50,
        ),
        2: Article(
            title="삼성 HBM 시장 재편 노린다",
            summary="...",
            link="https://www.hankyung.com/article/20260522001",
            published=datetime.now(timezone(timedelta(hours=9))),
            category="IT·테크·AI",
            source="한국경제",
            body="(본문 생략)" * 50,
        ),
        3: Article(
            title="카카오 데이터센터 화재 후속 정상화",
            summary="...",
            link="https://www.hankyung.com/article/20260522002",
            published=datetime.now(timezone(timedelta(hours=9))),
            category="IT·테크·AI",
            source="한국경제",
            body="(본문 생략)" * 50,
        ),
    }

    mock_analysis = {
        "tldr": "미·중 반도체 갈등이 다시 격화되는 가운데 국내 기업은 HBM 양산 경쟁에 박차.\n부동산 규제 완화 시그널이 시장에 영향.",
        "mega_trend": {
            "summary": "미·중 기술 패권 경쟁이 반도체에서 AI 모델 규제로 확대되고 있다. 한편 국내에서는 정부의 부동산 정책 변화가 증시와 산업 전반에 영향을 주는 흐름이 동시에 진행 중이다. 두 흐름은 결국 한국 기업의 글로벌 포지셔닝과 내수 시장 모두에 동시 압력을 가하고 있다.",
            "key_threads": [
                "미·중 기술 패권 경쟁이 반도체에서 AI 모델 규제로 확대",
                "정부의 부동산 시그널, 증시·산업 정책에 연쇄 영향"
            ]
        },
        "categories": [
            {
                "name": "IT·테크·AI",
                "has_sufficient_data": True,
                "limitation_note": None,
                "key_flows": [
                    "삼성전자, HBM4 양산 본격화 발표",
                    "정부, AI 기본법 시행령 초안 공개",
                    "네이버·카카오, 클라우드 사업 구조조정"
                ],
                "deep_issues": [
                    {
                        "title": "삼성전자 HBM4 양산 본격화",
                        "context": "엔비디아 차세대 칩 일정에 맞춘 공급 확보 경쟁의 일환.",
                        "implication": "HBM 시장 점유율 재편 가능성. 메모리 사이클 회복 신호.",
                        "watch_points": "수율 안정화 시점, 엔비디아 공식 채택 발표 여부.",
                        "referenced_article_ids": [1, 2]
                    }
                ],
                "minor_issues": [
                    {
                        "title": "카카오 데이터센터 화재 후속",
                        "summary": "카카오 11일 만에 모든 서비스 정상화.",
                        "implication": "이중화 체계 작동 입증. 운영 표준 영향.",
                        "referenced_article_ids": [3]
                    }
                ],
                "insight": "HBM 양산 경쟁은 단순 메모리 시장을 넘어 AI 인프라 공급망의 권력 재편 신호다."
            },
            {
                "name": "경제·금융·증시",
                "has_sufficient_data": False,
                "limitation_note": "본문 확보 기사가 적어 심층 분석에 제약이 있습니다",
                "key_flows": ["환율 변동성 확대"],
                "deep_issues": [],
                "minor_issues": [],
                "insight": ""
            }
        ]
    }

    output = render_report("2026-05-22", mock_analysis, mock_articles)
    print(output)