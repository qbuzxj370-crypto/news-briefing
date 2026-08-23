"""LLM에 JSON 구조화 출력 요청. 마크다운 생성은 renderer.py가 담당.

LLM의 책임: 분석 데이터(구조화된 JSON) 생성만
코드의 책임: JSON 검증 + 출처/링크 매핑 + 마크다운 렌더링

출처/링크 무결성: LLM은 article_ids만 반환, 실제 매체명/링크는 코드가 매핑.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from collectors import Article
from llm import LLMWithFallback


# ----------------------------------------------------------------------
# 시스템 프롬프트
# ----------------------------------------------------------------------
SYSTEM_PROMPT = """당신은 20년 경력의 전문 애널리스트입니다. 한국 독자를 대상으로 매일 아침 뉴스 브리핑을 제공합니다.

[분석 원칙]
1. 단순 요약이 아닌 '맥락'과 '함의'를 짚는다. 왜 이 뉴스가 중요한지, 어떤 흐름의 일부인지 설명한다.
2. 표면적 사실 너머 구조적 요인, 이해관계자의 의도, 시장/사회적 파급효과를 짚는다.
3. 추측은 명확히 표시한다. "추측이다", "가능성이 있다"는 표현 사용.
4. 톤은 차분하고 분석적이다. 자극적 표현, 과장, 정치적 편향을 피한다.
5. 한국어로 작성. 핵심 용어는 영문 병기 가능.

[중요 제약]
- 입력 기사에 명시되지 않은 수치/발언/사건은 절대 만들지 않는다.
- [요약만] 태그가 붙은 기사는 정보가 제한적이다. 해당 기사 기반 심층 분석은 자제.
- [본문] 태그가 붙은 기사를 분석의 주축으로 삼는다.
- 출처는 article_id로만 표시한다. 매체명/링크는 본문에 쓰지 않는다 (코드가 자동 매핑).

[공통 패턴 작성 규칙 — 내부 필드명은 mega_trend]
- 이 섹션은 '분야 간 인과 연결'을 보여준다. A 분야 사건이 B 분야에 어떻게 파급되는지 명시.
- 단순히 여러 분야의 뉴스를 나열하지 않는다. ("AI 뉴스도 있고 정치 뉴스도 있다" 식 금지)
- [경계] 태그가 붙은 기사는 분야 경계를 넘는 사건이다. 이 섹션의 주요 후보로 우선 활용.
- key_threads는 각 흐름의 인과 관계를 한 줄로 압축. ("X 분야의 ~가 Y 분야의 ~로 이어진다" 형식 권장)
- **하루치 데이터라는 한계를 지킨다.** 이것은 '오늘의 공통 패턴'이지 '메가 트렌드/구조적 추세'가
  아니다. 며칠 이상 반복 관찰해야 확인되는 흐름을 하루 기사만으로 단정하지 말 것. 추세로 보이면
  "~로 보이나 며칠 더 확인 필요", "~일 가능성" 처럼 추정으로 표시한다.

[같은 사건 분야 중복 방지]
- 한 사건은 가장 핵심적인 한 분야의 deep_issues에서만 다룬다.
- 분야 경계를 넘는 사건([경계] 태그 기사가 핵심인 사건)은 공통 패턴에서 다루고, 개별 분야 deep_issues에서는 제외하거나 minor_issues로만 간단히 언급.
- 같은 사건의 다른 측면을 다른 분야에서 다루는 것은 허용. 단, 본문이 중복되지 않도록 측면을 명확히 구분.

[함의(implication) 작성 규칙]
- 작성 후 self-check: 이 함의 문장을 오늘 다른 기사에도 그대로 붙일 수 있으면 일반론이다. 다시 써라.
  ("편의성이 증가한다", "시장이 확대될 것이다", "새로운 기회가 생긴다"는 어디에나 붙는 공허한 문장.)
- 이 사건 고유의 영향으로 구체화한다. 누가·무엇이·어느 방향으로 바뀌는지 명시.
  (나쁜 예: "시장이 확대될 것" → 좋은 예: "API 판매보다 에이전트 실행 수수료로 수익 모델이 이동")

[전문가 인사이트 작성 규칙]
- 일반론·격언·당위론 금지. ("~가 중요하다", "~를 주목해야 한다" 같은 추상적 문장 지양)
- insight는 문자열이 아니라 **observation / interpretation / limitation 3개 필드를 가진 객체**다.
  - **observation(관찰)**: 위 [오늘의 데이터 프로필]의 수치를 **직접 인용**한다.
    **숫자를 반드시 포함할 것** — "AI 18건, 반도체 7건으로 인프라 관련 기사가 배 이상 많았다"처럼
    구체적 수치를 적는다. "관심이 두드러졌다" 같은 인상 서술은 관찰이 아니다.
    **반드시 프로필의 【분야별 기준】 줄에서 자기 분야 수치를 인용한다.**
    【전체 풀 기준】 수치를 자기 분야 수치처럼 쓰면 산술이 맞지 않는다
    (분야 기사가 25건인데 "키워드 31건"이라고 쓰는 모순). 전체 풀 수치를 인용해야 할
    때는 "전체 기사 중"이라고 스코프를 밝힌다.
    프로필이 제공되지 않은 경우에만 입력 기사의 구체적 사실(건수·금액·비율)로 대체한다.
  - **interpretation(해석)**: 그 관찰이 무엇을 시사하는지. 아래 갈래 A/B/C 중 하나를 선택.
    **반드시 observation에 적은 수치·사실에서 출발한다.** 관찰과 무관한 일반 전망은 해석이 아니다.
    관찰을 지우고도 그대로 성립하는 문장이면 다시 써라.
    금지: 결론이 전제에 이미 들어 있는 동어반복 조건문.
      (나쁜 예: "만약 제도 개혁이 실제 법안 통과로 이어진다면 신뢰가 향상될 것이다"
       — '개혁이 성공하면 좋아진다'는 아무것도 말하지 않는다.
       좋은 예: "정치 기사가 19건 줄어든 날에도 거버넌스 관련 기사 비중은 유지됐다.
       이슈 총량이 아니라 관심 축이 이동하고 있다는 신호다.")
  - **limitation(한계)**: 근거의 제약을 명시한다. "하루치 표본이라 추세로 단정하기 어렵다",
    "며칠 더 확인 필요" 등. 확신이 정당한 경우에만 빈 문자열("")을 넣는다.
- **insight 본문에는 article_id 표기([46], `[42]`, (`[37]`) 등 어떤 형태로도) 절대 쓰지 않는다.** insight는 deep_issues·minor_issues와 달리 referenced_article_ids 필드가 없고 종합 통찰이라 개별 출처 표시가 불필요하다.

[인사이트 작성 갈래 — 매번 다른 갈래 선택]
다음 3개 갈래 중 하나를 선택해 작성한다. **5개 분야의 insight가 모두 같은 갈래를 쓰면 안 된다** — 분야별로 최소 2개 이상의 다른 갈래가 등장하도록 분배.

- 갈래 A (조건부 예측): "X 조건이 충족되면 Y가 발생할 것" 형식. 구체적 수치·시점 포함.
- 갈래 B (구조 신호 해석): 표면적 현상 뒤의 구조적 원인 1개를 짚는다. 예측보다 진단 중심.
- 갈래 C (분야 간 파급): 이 분야 변화가 다른 분야에 어떤 경로로 영향을 미치는지 인과 사슬 한 줄.

[절대 금지 표현]
다음 패턴은 매일 반복되어 사용자에게 식상함만 남기므로 절대 사용 금지:
- "향후 12개월 내 ~할 것이다", "향후 N개월 내 ~할 것" (시점 표현이 12개월 한 가지로 고착됨)
- "~의 중요성이 더욱 부각될 것이다", "~의 패러다임 전환이 가속화될 것이다"
- "ESG", "디지털 전환", "패러다임", "초개인화" 같은 추상 키워드로 문장을 끝내는 형식
- 모든 분야 insight를 같은 문장 구조("향후 ~, 만약 ~")로 시작하는 것

시점이 필요하면 "이번 분기", "올해 하반기", "2~3년 내", "선거 직후" 등 구체적 시간 단위로 다양화. 미래 예측이 부적절한 분야면 갈래 B(진단)나 갈래 C(파급)를 선택.

[출력 형식 — 엄격히 준수]
반드시 JSON 객체로만 응답한다. 마크다운, 설명, 코드블록 표시(```) 모두 금지.
스키마는 사용자 메시지에 명시된다. 모든 필수 필드를 포함하고, article_ids는 입력에서 본 정수만 사용한다."""


# ----------------------------------------------------------------------
# JSON 스키마 (LLM에게 보여줄 출력 형식)
# ----------------------------------------------------------------------
OUTPUT_SCHEMA_DESCRIPTION = """{
  "tldr": "오늘의 핵심을 2~3줄로 압축한 한 문단. 상단 콜아웃 박스용.",
  "mega_trend": {
    "summary": "분야 횡단 '오늘의 공통 패턴' 본문. 8~12줄. 여러 분야 간 연결고리를 보여줄 것. 하루치라 구조적 추세로 단정 금지.",
    "key_threads": ["분야 횡단 흐름 1 (한 문장)", "흐름 2", "..."]
  },
  "categories": [
    {
      "name": "IT·테크·AI",
      "has_sufficient_data": true,
      "limitation_note": null,
      "key_flows": [
        "핵심 흐름 한 줄 1",
        "핵심 흐름 한 줄 2",
        "핵심 흐름 한 줄 3"
      ],
      "deep_issues": [
        {
          "title": "이슈 제목 (간결하게)",
          "context": "맥락: 왜 지금 일어났나, 어떤 배경이 있나",
          "implication": "함의: 누구에게 어떤 영향",
          "watch_points": "관전 포인트: 앞으로 무엇을 지켜봐야 하나",
          "referenced_article_ids": [3, 7]
        }
      ],
      "minor_issues": [
        {
          "title": "이슈 제목 (간결)",
          "summary": "무슨 일이 있었나 - 한 줄 사실 요약",
          "implication": "왜 의미 있나 - 한 줄 간단 분석",
          "referenced_article_ids": [12]
        }
      ],
      "insight": {
        "observation": "관찰: 데이터 프로필의 수치를 인용한 사실 서술. 숫자를 반드시 포함한다.",
        "interpretation": "해석: 그 관찰이 무엇을 시사하는지 (갈래 A/B/C 중 하나)",
        "limitation": "한계: 근거의 제약. 하루치·표본 부족 등. 확신이 정당하면 빈 문자열."
      }
    }
  ]
}

[필드 규칙]
- categories 배열 순서: IT·테크·AI, 경제·금융·증시, 정치, 사회·시사, 산업 (입력 순서와 동일)
- key_flows: 1~3개 유연 (분야 데이터 부족 시 적게)
- deep_issues: 2~4개 유연. 분야 데이터 매우 부족하면 0~1개도 허용
- minor_issues: 상한 없음. 단 아래 [minor 포함 테스트]를 통과한 이슈만 넣는다.
  목적은 "분야를 빠짐없이 덮는 것"이 아니라 "deep_issues에서 다루지 않았지만
  놓치면 안 될 구조적 이슈를 보완"하는 것이다.

  [minor 포함 테스트] — 아래 둘을 모두 만족해야 포함. 하나라도 아니오면 제외.
  (1) 확장성: 이 이슈가 개별 기업의 활동(신제품·행사·실적 그 자체)을 넘어
      산업 구조·시장 추세·정책·사회 현상으로 연결되는가?
      ※ "추세의 일부"라고 보려면, 그 추세를 보여주는 다른 기사가 입력 풀에
        최소 1건 더 있어야 한다. 단일 기사로만 존재하는 이슈는 구조적 추세로
        격상하지 말 것. (예: "무알코올 맥주 1위 경쟁"은 풀에 주류/무알코올
        트렌드 기사가 더 있으면 통과, 그 기사 하나뿐이면 단순 제품 동향 → 제외)
  (2) 지속성: 1주일 후에도 언급할 만한가? 그날 한정 이벤트·프로모션이면 제외.

  [제외 예시] 신제품/신메뉴 출시, 할인·프로모션 행사, 단일 기업 CSR·상생 프로그램
  (산업 구조로 연결될 때만 예외), 도서·콘텐츠 출간 홍보, 분양 마케팅성 르포,
  브랜드 협업 마케팅 자체. 기존 제외: 인사 변동, 단순 수치 동향(주가 등락 등).

  [작성 원칙]
  - 대부분의 날, 한 분야 minor_issues는 0~2건이 정상이다. 0건이어도 전혀 문제없다
    (오히려 그 분야에 보완할 구조적 이슈가 없었다는 정상 신호).
  - 한 분야에서 5건 이상이 나오면 대개 테스트를 느슨하게 적용했다는 신호다.
    다시 거를 것. 광고성 기사로 칸을 채우면 보고서 신뢰도가 떨어진다.
- referenced_article_ids: 그 이슈를 분석할 때 참조한 기사의 [번호]만 포함. 정수 배열.
  매체명/링크는 코드가 자동 매핑하므로 본문에 직접 쓸 필요 없음.
- confidence(신뢰도)는 **출력하지 않는다** — 참조 기사 수와 서술 길이를 근거로 코드가
  결정론적으로 산출한다. 대신 아래 서술 규칙을 지킨다:
  - **참조 기사가 1건뿐인 이슈**: 확신 표현("~할 것이다", "~를 견인할 것") 금지.
    "~일 가능성", "현재 기사만으로는 판단하기 어렵다"로 쓰고, watch_points에
    "추가 데이터 필요" 취지를 명시한다.
  - **참조 기사가 2건 이상인 이슈**: 사실 기반 서술은 단정해도 되나, 해석·추정 부분은
    추정임을 드러낸다.
  - 근거가 얇으면 분량을 늘려 메우지 말 것. 짧아도 된다 — 참조 대비 서술이 길면
    코드가 신뢰도를 낮춰 표시한다.
- has_sufficient_data: 본문 확보 기사가 충분(2건 이상)하면 true, 부족하면 false
- limitation_note: has_sufficient_data가 false일 때만 한 줄 설명. true면 null."""


# ----------------------------------------------------------------------
# 기사 블록 구성
# ----------------------------------------------------------------------
def _format_articles(
    article_data: Dict[str, List[Article]],
) -> tuple[str, Dict[int, Article]]:
    """LLM 입력용 기사 블록 생성 + article_id → Article 매핑 반환.

    각 기사에 1부터 시작하는 정수 ID 부여. 이 ID는 LLM 출력의 referenced_article_ids와 매칭됨.
    """
    lines: List[str] = []
    id_to_article: Dict[int, Article] = {}
    idx = 1
    for category, articles in article_data.items():
        lines.append(f"\n### {category} ({len(articles)}건)\n")
        if not articles:
            lines.append("(어제 수집된 기사 없음)\n")
            continue
        for a in articles:
            tag = "[본문]" if a.has_body else "[요약만]"
            cross_tag = " [경계]" if a.is_cross_category else ""
            content = a.content_for_llm
            if a.has_body and len(content) > 2000:
                content = content[:2000] + "...(이하 생략)"
            lines.append(f"[{idx}]{cross_tag} {tag} {a.title}\n    내용: {content}\n")
            id_to_article[idx] = a
            idx += 1
    return "\n".join(lines), id_to_article


# ----------------------------------------------------------------------
# JSON 검증
# ----------------------------------------------------------------------
class AnalysisValidationError(Exception):
    """LLM 출력 JSON이 스키마를 만족하지 않음."""


# deep_issue.confidence 허용값 (코드가 산출 — LLM 출력이 아님).
_VALID_CONFIDENCE = {"높음", "중간", "판단유보"}

# confidence 산출 임계 (2026-07-25 운영 관찰 대응).
# 배경: 초판은 confidence를 LLM에게 물었는데, 12건 전부 "높음"이 나왔다.
# 참조 1건짜리 이슈 3건도 "높음" — 프롬프트에 "참조 1건 이하면 판단유보"라고
# 명시했는데도 어겼다. LLM의 자기평가 확신도는 캘리브레이션되지 않으며
# ("내 분석에 대한 자신감"으로 해석), 프롬프트로 교정되지 않는 종류의 문제다.
# 따라서 라벨은 코드가 결정론적으로 산출한다 (ADR-001의 코드 위임 원리 확장).
_CONF_REFS_HIGH = 3       # 이 수 이상 참조하면 "높음" 후보
_CONF_REFS_MEDIUM = 2     # 이 수 이상이면 "중간", 미만(0~1건)은 "판단유보"
_CONF_CHARS_PER_REF = 300  # 참조 1건당 서술 길이 상한 — 초과 시 한 단계 강등


def _normalize_insight(raw: Any) -> Dict[str, str]:
    """insight를 {observation, interpretation, limitation} 3필드로 정규화.

    LLM이 구 형식(문자열)으로 회귀해도 파이프라인이 멈추지 않게 한다 —
    문자열이면 전체를 interpretation으로 보고 관찰·한계는 빈 값으로 둔다
    (그 경우 수치 인용 경고가 뜨므로 회귀 자체는 로그로 드러난다).
    """
    if isinstance(raw, dict):
        return {
            k: str(raw.get(k, "") or "").strip()
            for k in ("observation", "interpretation", "limitation")
        }
    if isinstance(raw, str):
        return {"observation": "", "interpretation": raw.strip(), "limitation": ""}
    return {"observation": "", "interpretation": "", "limitation": ""}


def compute_confidence(ref_count: int, body_len: int) -> str:
    """참조 기사 수 + 서술 길이로 deep_issue 신뢰도를 결정론적으로 산출.

    LLM에게 묻지 않는다 — 자기평가 확신도는 신뢰할 수 없다(위 주석 참조).

    규칙:
      - 참조 0~1건: "판단유보" (근거 1건으로 단정 불가)
      - 참조 2건: "중간"
      - 참조 3건 이상: "높음", 단 참조당 서술이 _CONF_CHARS_PER_REF를 넘으면
        근거 대비 서술이 과한 것으로 보고 "중간"으로 강등
    """
    if ref_count < _CONF_REFS_MEDIUM:
        return "판단유보"
    if ref_count < _CONF_REFS_HIGH:
        return "중간"
    # 참조는 충분하나 서술이 근거에 비해 과하게 길면 강등
    if body_len > _CONF_CHARS_PER_REF * ref_count:
        return "중간"
    return "높음"


def _validate_analysis(data: Any, valid_ids: set) -> Dict[str, Any]:
    """LLM JSON 출력 검증. 누락/타입 오류 시 예외, 잘못된 article_id는 제거."""
    if not isinstance(data, dict):
        raise AnalysisValidationError("최상위가 객체가 아님")

    # tldr
    if not isinstance(data.get("tldr"), str):
        data["tldr"] = ""

    # mega_trend
    mt = data.get("mega_trend")
    if not isinstance(mt, dict):
        raise AnalysisValidationError("mega_trend 누락 또는 객체 아님")
    if not isinstance(mt.get("summary"), str):
        raise AnalysisValidationError("mega_trend.summary 누락")
    if not isinstance(mt.get("key_threads"), list):
        mt["key_threads"] = []

    # categories
    cats = data.get("categories")
    if not isinstance(cats, list) or not cats:
        raise AnalysisValidationError("categories 누락 또는 빈 배열")

    for i, cat in enumerate(cats):
        if not isinstance(cat, dict):
            raise AnalysisValidationError(f"categories[{i}]가 객체 아님")
        cat.setdefault("name", f"분야 {i + 1}")
        cat.setdefault("has_sufficient_data", True)
        cat.setdefault("limitation_note", None)
        cat.setdefault("key_flows", [])
        cat.setdefault("deep_issues", [])
        cat.setdefault("minor_issues", [])
        cat["insight"] = _normalize_insight(cat.get("insight"))

        # key_flows: 문자열 배열 보장
        cat["key_flows"] = [str(x) for x in cat["key_flows"] if isinstance(x, str)]

        # deep_issues 정리
        cleaned_deep = []
        for issue in cat["deep_issues"]:
            if not isinstance(issue, dict):
                continue
            issue.setdefault("title", "")
            issue.setdefault("context", "")
            issue.setdefault("implication", "")
            issue.setdefault("watch_points", "")
            raw_ids = issue.get("referenced_article_ids", []) or []
            issue["referenced_article_ids"] = [
                int(x)
                for x in raw_ids
                if isinstance(x, (int, float)) and int(x) in valid_ids
            ]
            # confidence는 코드가 산출한다 (LLM이 보낸 값이 있어도 덮어씀).
            # 반드시 ID 필터링 *이후* — 환각 ID가 제거된 '유효' 참조만 근거로 센다.
            body_len = sum(
                len(issue.get(k, "") or "")
                for k in ("context", "implication", "watch_points")
            )
            issue["confidence"] = compute_confidence(
                len(issue["referenced_article_ids"]), body_len
            )
            cleaned_deep.append(issue)
        cat["deep_issues"] = cleaned_deep

        # minor_issues 정리
        cleaned_minor = []
        for issue in cat["minor_issues"]:
            if not isinstance(issue, dict):
                continue
            issue.setdefault("title", "")
            issue.setdefault("summary", "")
            issue.setdefault("implication", "")
            raw_ids = issue.get("referenced_article_ids", []) or []
            issue["referenced_article_ids"] = [
                int(x)
                for x in raw_ids
                if isinstance(x, (int, float)) and int(x) in valid_ids
            ]
            cleaned_minor.append(issue)
        cat["minor_issues"] = cleaned_minor

    return data


# ----------------------------------------------------------------------
# 출처 참조 지표 (ADR-001 미해결 이슈의 측정 도구)
# ----------------------------------------------------------------------
# "장문" 판정 기준: context+implication+watch_points 합산 글자 수.
# 이 길이를 넘는 분석이 참조 기사 1개 이하에 기대고 있으면 본문이
# 출처를 벗어나 서술했을 가능성이 있는 점검 후보로 본다.
LONG_BODY_THRESHOLD = 400


def source_coverage_stats(analysis: Dict[str, Any]) -> Dict[str, Any]:
    """deep_issue의 referenced_article_ids 분포 통계.

    본문-출처 의미 일치는 자동 검증이 불가능하므로(_validate_analysis는
    valid_ids 범위만 검사), 근사 신호로 deep_issue당 참조 ID 개수를 본다.
    ID 1개 이하의 장문 분석이 잦으면 비례 임계 도입 검토 (ADR-001 측정 계획).

    Returns:
        {"deep_issue_count": int, "avg_refs": float,
         "low_ref_long": [(분야, 제목, 참조수, 본문길이), ...]}
    """
    counts: List[int] = []
    low_ref_long: List[tuple] = []
    for cat in (analysis or {}).get("categories", []):
        for issue in cat.get("deep_issues", []):
            n = len(issue.get("referenced_article_ids", []))
            counts.append(n)
            body_len = sum(
                len(issue.get(k, "") or "")
                for k in ("context", "implication", "watch_points")
            )
            if n <= 1 and body_len >= LONG_BODY_THRESHOLD:
                low_ref_long.append(
                    (cat.get("name", ""), issue.get("title", ""), n, body_len)
                )
    total = len(counts)
    return {
        "deep_issue_count": total,
        "avg_refs": (sum(counts) / total) if total else 0.0,
        "low_ref_long": low_ref_long,
    }


# insight.observation의 수치 인용 검사용. 아라비아 숫자 1개 이상이면 인용으로 본다.
_DIGIT_RE = re.compile(r"\d")


def source_concentration_stats(
    analysis: Dict[str, Any],
    id_to_article: Dict[int, Any],
) -> List[tuple]:
    """참조가 2건 이상인데 전부 한 매체인 deep_issue를 찾아 반환.

    배경(2026-07-25 운영): 사회·시사 분야 deep_issue 3건의 출처가 전부 한겨레
    한 매체였다. 매체별 분포는 데이터 프로필에 있지만 "이 이슈의 근거가 한
    매체에 쏠렸다"는 신호는 어디에도 없었다. 단일 매체만 근거로 삼으면 그
    매체의 프레임이 그대로 분석이 되므로(외부 평가 #8 언론사 편향), 관측한다.

    참조 1건은 이미 confidence "판단유보"로 표시되므로 중복 경고하지 않는다 —
    여기서 잡으려는 건 "여러 건을 봤는데 전부 같은 매체"인 경우다.

    Returns:
        [(분야명, 이슈 제목, 매체명, 참조수), ...]
    """
    flagged: List[tuple] = []
    for cat in (analysis or {}).get("categories", []):
        for issue in cat.get("deep_issues", []):
            ids = issue.get("referenced_article_ids", []) or []
            if len(ids) < 2:
                continue
            sources = {
                getattr(id_to_article.get(i), "source", None)
                for i in ids
            }
            sources.discard(None)
            if len(sources) == 1:
                flagged.append((
                    cat.get("name", ""),
                    issue.get("title", ""),
                    next(iter(sources)),
                    len(ids),
                ))
    return flagged


def _print_source_concentration(
    analysis: Dict[str, Any],
    id_to_article: Dict[int, Any],
) -> None:
    flagged = source_concentration_stats(analysis, id_to_article)
    if not flagged:
        return
    print(f"  출처 편중 지표: 단일 매체 근거 {len(flagged)}건")
    for cat_name, title, source, n in flagged:
        print(
            f"    [지표] {cat_name} — {title}: 참조 {n}건 전부 '{source}' "
            f"(단일 매체 프레임 반영 가능)"
        )


def insight_grounding_stats(analysis: Dict[str, Any]) -> Dict[str, Any]:
    """insight가 관찰(수치)·한계를 실제로 갖췄는지 집계.

    배경(2026-07-25 운영): 프롬프트로 "관찰 → 해석 → 한계"를 요구했더니
    5개 분야 모두 관찰 프레이밍은 채택했으나 **수치 인용 0건**, 한계 절은
    5개 중 1개뿐이었다. 프롬프트 요구는 지켜졌는지 검증되지 않으면 조용히
    무시되므로, 코드가 세어 로그로 드러낸다 (check_misc_ratio와 같은 관측 장치).

    Returns:
        {"total": 인사이트 있는 분야 수,
         "no_number": [수치 없는 분야명], "no_limitation": [한계 없는 분야명]}
    """
    total = 0
    no_number: List[str] = []
    no_limitation: List[str] = []
    for cat in (analysis or {}).get("categories", []):
        ins = cat.get("insight")
        if not isinstance(ins, dict):
            continue
        obs = ins.get("observation", "")
        # 인사이트가 통째로 빈 분야(데이터 부족 등)는 집계 제외
        if not obs and not ins.get("interpretation"):
            continue
        total += 1
        name = cat.get("name", "")
        if not _DIGIT_RE.search(obs):
            no_number.append(name)
        if not ins.get("limitation"):
            no_limitation.append(name)
    return {"total": total, "no_number": no_number, "no_limitation": no_limitation}


def _print_insight_grounding(analysis: Dict[str, Any]) -> None:
    stats = insight_grounding_stats(analysis)
    if stats["total"] == 0:
        return
    n = stats["total"]
    cited = n - len(stats["no_number"])
    print(f"  인사이트 근거 지표: 수치 인용 {cited}/{n}분야")
    if stats["no_number"]:
        print(
            f"    [경고] 관찰에 수치 없음: {', '.join(stats['no_number'])} "
            f"— 데이터 프로필 인용이 무시됨(프롬프트 점검 후보)"
        )
    if stats["no_limitation"]:
        print(f"    [지표] 한계 절 생략: {', '.join(stats['no_limitation'])}")


def _print_source_coverage(analysis: Dict[str, Any]) -> None:
    stats = source_coverage_stats(analysis)
    if stats["deep_issue_count"] == 0:
        return
    print(
        f"  출처 참조 지표: deep_issue {stats['deep_issue_count']}건, "
        f"평균 참조 {stats['avg_refs']:.1f}개"
    )
    for cat_name, title, n, body_len in stats["low_ref_long"]:
        print(
            f"    [지표] 참조 {n}개 장문({body_len}자): {cat_name} — {title} "
            f"(본문-출처 일치 점검 후보)"
        )


# ----------------------------------------------------------------------
# 메인 분석 함수
# ----------------------------------------------------------------------
def analyze(
    llm: LLMWithFallback,
    article_data: Dict[str, List[Article]],
    data_profile: Optional[str] = None,
) -> Dict[str, Any]:
    """LLM에 JSON 분석 요청 후 검증된 결과 반환.

    Returns:
        {
            "analysis": 검증된 분석 JSON dict,
            "id_to_article": {int: Article} 매핑 (renderer가 출처 매핑에 사용),
            "model_used": 실제 응답 모델명,
            "stats": {"total": int, "with_body": int}
        }
    """
    total = sum(len(v) for v in article_data.values())
    body_count = sum(1 for arts in article_data.values() for a in arts if a.has_body)
    print(f"  분석 대상: 총 {total}건 (본문 {body_count}, 요약만 {total - body_count})")

    if total == 0:
        return {
            "analysis": None,
            "id_to_article": {},
            "model_used": "none",
            "stats": {"total": 0, "with_body": 0},
        }

    articles_block, id_to_article = _format_articles(article_data)
    valid_ids = set(id_to_article.keys())

    # 데이터 프로필(분포·빈도·전일 대비)은 코드가 집계한 사실. 있으면 스키마 앞에
    # 주입해 인사이트가 '관찰(수치) → 해석 → 한계'를 갖추게 한다 (환각 수치 차단).
    profile_block = f"{data_profile.strip()}\n\n---\n\n" if data_profile else ""

    user_prompt = (
        f"다음은 어제 한국의 5개 분야 주요 뉴스입니다. 각 기사에 다음 태그가 붙어 있습니다:\n"
        f"- [번호]: 기사 식별자 (분석 결과의 referenced_article_ids에 사용)\n"
        f"- [본문] / [요약만]: 본문 확보 여부\n"
        f"- [경계]: 분야 경계를 넘는 사건 (분류 LLM이 판정). '오늘의 공통 패턴'의 핵심 후보. 한 분야의 deep_issues에서만 다루고 다른 분야에선 공통 패턴에 인용하거나 minor_issues로만 짧게 언급.\n"
        f"\n{articles_block}\n"
        f"\n---\n\n"
        f"{profile_block}"
        f"위 기사들을 분석하여 아래 JSON 스키마에 정확히 맞는 JSON 객체를 생성하세요.\n"
        f"마크다운, 설명, 코드블록 표시 없이 JSON만 반환합니다.\n\n"
        f"[출력 JSON 스키마]\n{OUTPUT_SCHEMA_DESCRIPTION}\n"
    )

    # 한국어 토큰 추정: 한국어는 글자당 약 1~1.5 토큰. 보수적으로 글자 수 그대로 사용.
    approx_input_tokens = len(SYSTEM_PROMPT + user_prompt)
    print(f"  입력 길이: ~{approx_input_tokens:,} 토큰 추정 (한국어 보수 추정)")

    raw = llm.generate(
        system=SYSTEM_PROMPT,
        user=user_prompt,
        max_tokens=32000,
        json_mode=True,
    )

    # JSON 파싱
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        # 일부 모델이 ``` 블록을 끼워넣는 경우 방어
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("```")[1]
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
            cleaned = cleaned.strip()
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError:
            raise AnalysisValidationError(
                f"JSON 파싱 실패: {e}\n원문 앞부분: {raw[:300]}"
            )

    validated = _validate_analysis(parsed, valid_ids)

    print(f"  ✓ 분석 완료 (사용 모델: {llm.last_used})")
    _print_source_coverage(validated)
    _print_source_concentration(validated, id_to_article)
    _print_insight_grounding(validated)

    return {
        "analysis": validated,
        "id_to_article": id_to_article,
        "model_used": llm.last_used,
        "stats": {"total": total, "with_body": body_count},
    }


# ----------------------------------------------------------------------
# 단독 실행 (디버깅용)
# ----------------------------------------------------------------------
if __name__ == "__main__":
    from collectors import collect_and_enrich
    from llm import get_llm

    print("=" * 60)
    print("분석 파이프라인 테스트 (JSON 출력)")
    print("=" * 60)

    data = collect_and_enrich()
    llm = get_llm()
    result = analyze(llm, data)

    print("\n[검증된 JSON 미리보기]")
    if result["analysis"]:
        print(json.dumps(result["analysis"], ensure_ascii=False, indent=2)[:2000])
    else:
        print("분석 결과 없음 (수집 0건)")
