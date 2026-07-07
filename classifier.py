"""LLM 기반 분야 분류 모듈.

한국 매체의 RSS 피드는 대부분 종합 피드(분야 누수 심함)라
RSS 출처로 분야를 추정할 수 없다. 이 모듈은 기사 제목을 LLM에 보내
분야를 사후 판정한다.

원칙 (ADR-001):
- LLM은 판단(분류)을 수행
- 코드는 분야 집합 검증, 알 수 없는 분야는 "기타"로 강제
- LLM이 만들어낼 수 있는 위험은 검증 가능한 형식(고정 분야 집합)으로 차단

다분야 사건 정책:
- 주분야 1개 선택
- 분야 경계를 넘는 사건은 is_cross_category=True 마크
- 메가 트렌드 작성 시 이 플래그를 우선 활용
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Set

from collectors import Article
from llm import LLMWithFallback


# ----------------------------------------------------------------------
# 분야 집합
# ----------------------------------------------------------------------
VALID_CATEGORIES: List[str] = [
    "IT·테크·AI",
    "경제·금융·증시",
    "정치",
    "사회·시사",
    "산업",
    "기타",
]
FALLBACK_CATEGORY = "기타"
_VALID_SET: Set[str] = set(VALID_CATEGORIES)

# LLM 출력 토큰 절약을 위한 단축 코드 매핑.
# 한국어 분야명(예: "IT·테크·AI")은 토큰 효율이 매우 나빠 응답 잘림 빈발.
# LLM은 단축 코드로 응답, 코드가 전체 분야명으로 변환.
CODE_TO_CATEGORY: Dict[str, str] = {
    "IT": "IT·테크·AI",
    "ECO": "경제·금융·증시",
    "POL": "정치",
    "SOC": "사회·시사",
    "IND": "산업",
    "MISC": "기타",
}
FALLBACK_CODE = "MISC"


# ----------------------------------------------------------------------
# 프롬프트
# ----------------------------------------------------------------------
SYSTEM_PROMPT = """당신은 한국 뉴스 분류 전문가입니다. 기사 제목을 보고 6개 분야 코드 중 가장 적합한 1개를 판정합니다.

[분야 정의]
- IT: 정보기술, 인공지능, 반도체, 스타트업, 플랫폼 기업(네이버·카카오 등), 통신, 사이버보안, 게임. 단순 IT 회사 인사·실적이 아닌 기술/제품/시장 관련.
- ECO: 경제·금융·증시. 거시경제, 금리, 환율, 물가, 주식, 채권, 부동산 시장 동향, 금융정책, 가계 경제. 산업과 구분: 시장·자본 흐름에 초점.
- POL: 정치. 대통령·정부 정책 결정, 국회·정당, 선거, 정치인 발언/논쟁, 정치 사건(특검·수사 포함).
- SOC: 사회·시사. 사회 현상, 사건사고, 범죄, 교육, 환경, 보건, 노동, 시민운동, 문화 논쟁. 정치보다 시민 생활 중심.
- IND: 산업. 제조업·서비스업 기업 활동, 노사 관계, 산업 정책, 기업 인수합병, 신제품 출시. IT 외 분야 (자동차, 조선, 철강, 유통, 식품 등).
- MISC: 기타. 위 5개 분야에 명확히 속하지 않음 (스포츠, 연예, 단순 인사 동정, 헤드라인만으로 판정 어려운 것).

[다분야 사건 판정]
- 한 사건이 여러 분야에 동시에 속하는 경우 (예: "이재명 정부의 플랫폼 규제 입법") 가장 핵심적인 1개 분야를 cat으로 고르고, cross=true로 마크.
- 명확히 한 분야에만 속하면 cross=false.
- MISC는 cross 의미 없음. 항상 false.

[출력]
반드시 JSON 객체만 응답. 마크다운, 설명, 코드블록 표시(```) 금지."""


OUTPUT_SCHEMA = """{
  "items": [
    {"id": 1, "cat": "IT", "cross": false},
    {"id": 2, "cat": "SOC", "cross": true}
  ]
}

[필드 규칙]
- id: 입력에 표시된 기사 번호 정수 그대로
- cat: 정확히 다음 6개 코드 중 하나 - "IT", "ECO", "POL", "SOC", "IND", "MISC"
- cross: true 또는 false
- 모든 입력 기사를 빠짐없이 분류. 누락 금지."""


# ----------------------------------------------------------------------
# 예외
# ----------------------------------------------------------------------
class ClassificationError(Exception):
    """분류 실패 (파싱 실패 또는 LLM 호출 실패)."""


# ----------------------------------------------------------------------
# 입력 포맷팅
# ----------------------------------------------------------------------
def _format_titles(articles: List[Article]) -> str:
    """기사 리스트를 LLM 입력용 번호 매긴 제목 목록으로."""
    lines = []
    for i, a in enumerate(articles, start=1):
        # 제목만 사용. 매체명도 힌트로 같이 (다만 분류 주체는 제목)
        lines.append(f"[{i}] ({a.source}) {a.title}")
    return "\n".join(lines)


# ----------------------------------------------------------------------
# 응답 파싱 및 검증
# ----------------------------------------------------------------------
def _parse_response(raw: str) -> List[Dict[str, Any]]:
    """LLM JSON 응답 파싱. ``` 펜스 방어 포함."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("```")[1]
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
            cleaned = cleaned.strip()
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as e:
            raise ClassificationError(f"JSON 파싱 실패: {e}\n원문 앞부분: {raw[:300]}")

    if not isinstance(data, dict):
        raise ClassificationError("최상위가 객체가 아님")
    items = data.get("items")
    if not isinstance(items, list):
        raise ClassificationError("items 누락 또는 배열 아님")
    return items


def _apply_classifications(
    articles: List[Article],
    classifications: List[Dict[str, Any]],
) -> Dict[str, int]:
    """분류 결과를 Article에 in-place로 적용. 통계 dict 반환.
    
    검증:
    - 유효한 분야가 아니면 "기타"
    - id가 articles 범위 밖이면 무시
    - 누락된 id의 Article은 "기타"로 폴백
    """
    classified_ids = set()
    stats = {cat: 0 for cat in VALID_CATEGORIES}
    cross_count = 0

    for item in classifications:
        if not isinstance(item, dict):
            continue
        raw_id = item.get("id")
        if not isinstance(raw_id, (int, float)):
            continue
        idx = int(raw_id) - 1  # 1-indexed → 0-indexed
        if idx < 0 or idx >= len(articles):
            continue

        # 단축 코드 → 분야명 매핑
        code = item.get("cat", FALLBACK_CODE)
        cat = CODE_TO_CATEGORY.get(code, FALLBACK_CATEGORY)
        if cat not in _VALID_SET:
            cat = FALLBACK_CATEGORY

        is_cross = bool(item.get("cross", False))
        # "기타"는 cross_category 의미 없음
        if cat == FALLBACK_CATEGORY:
            is_cross = False

        articles[idx].category = cat
        articles[idx].is_cross_category = is_cross
        # 정상 분류분은 미해결 플래그 해제 (직전 런에서 미해결이던 기사의 회복 포함)
        articles[idx].classification_unresolved = False
        classified_ids.add(idx)
        stats[cat] = stats.get(cat, 0) + 1
        if is_cross:
            cross_count += 1

    # LLM 판정을 못 받은 기사 = '미해결'(unresolved). genuine '기타'와 구분한다
    # (ADR-017): 배치 실패·응답 누락으로 분류가 안 된 것이라 회복(재분류) 대상.
    # category=None으로 두어 분석에서 자연 제외되고 '기타' 카운트를 오염시키지 않는다.
    unresolved = 0
    for i, art in enumerate(articles):
        if i not in classified_ids:
            art.category = None
            art.is_cross_category = False
            art.classification_unresolved = True
            unresolved += 1

    stats["_cross"] = cross_count
    stats["_unresolved"] = unresolved
    return stats


# ----------------------------------------------------------------------
# 폴백
# ----------------------------------------------------------------------
def _apply_fallback(articles: List[Article]) -> None:
    """LLM 호출이 전면 실패한 경우. 모든 기사를 '미해결'로 (ADR-017).

    genuine '기타'가 아니라 회복 대상 — 재실행 시 재분류된다. category=None이라
    이번 런 분석에선 제외되지만(전량 미해결이면 total=0 → 빈 페이지), 스냅샷
    완전성(Phase 1a)이 이를 complete=False로 기록해 재실행이 복구할 수 있게 한다.
    """
    for a in articles:
        a.category = None
        a.is_cross_category = False
        a.classification_unresolved = True


# ----------------------------------------------------------------------
# 메인 엔트리포인트
# ----------------------------------------------------------------------
# 한 번에 분류할 최대 기사 수.
# Gemini 무료 티어의 실효 출력 한도(~8000 토큰)에 맞춰 안전하게 설정.
# 단축 코드 1개 객체 ≈ 35자 ≈ 50 토큰 → 100건 = 약 5000 토큰 (한도 내)
CLASSIFY_BATCH_SIZE = 100

# 실패 배치 재시도 (ADR-017 0b). _classify_batch는 호출/파싱 실패를 빈 리스트로
# 흡수하므로, 빈 결과를 실패 신호로 보고 재시도해 일시적 5xx·간헐 파싱 실패를
# 근원에서 만회한다 (llm 레벨 5xx 백오프와 별개 — 파싱 실패도 재호출 시 temp
# 샘플링이 달라져 풀릴 수 있음). 미해결로 떨어지기 전 1차 방어선.
BATCH_RETRY = 2
BATCH_RETRY_SLEEP = 1.5  # 재시도 간 대기(초) — 일시적 과부하 완화


def _classify_batch(
    llm: LLMWithFallback,
    articles: List[Article],
    offset: int,
) -> List[Dict[str, Any]]:
    """기사 배치 하나를 분류. offset만큼 ID에 더해서 입력 표시.
    
    Returns:
        classifications 리스트. 실패 시 빈 리스트 반환 (호출자가 폴백 처리).
    """
    # 입력 ID는 1-indexed로 표시 (offset+1부터)
    lines = []
    for i, a in enumerate(articles):
        lines.append(f"[{offset + i + 1}] ({a.source}) {a.title}")
    titles_block = "\n".join(lines)

    user_prompt = (
        f"다음 {len(articles)}개 기사를 분류하세요. JSON 스키마에 정확히 맞춰 응답합니다.\n\n"
        f"{titles_block}\n\n"
        f"---\n\n"
        f"[출력 JSON 스키마]\n{OUTPUT_SCHEMA}\n"
    )

    try:
        raw = llm.generate(
            system=SYSTEM_PROMPT,
            user=user_prompt,
            max_tokens=8000,
            json_mode=True,
            thinking_budget=0,  # 분류는 추론 불필요. thinking 끄고 모든 토큰을 응답에 사용
            temperature=0,      # 라벨링 작업 — 결정성 위해 temp 0 (ADR-017 0c)
        )
    except Exception as e:
        print(f"    [경고] 배치 LLM 호출 실패 (offset={offset}): {e}")
        return []

    try:
        return _parse_response(raw)
    except ClassificationError as e:
        print(f"    [경고] 배치 응답 파싱 실패 (offset={offset}): {e}")
        return []


# "기타" 비율 경고 임계 (ADR-012/013 미해결 이슈 — silent drop 감지).
# "기타"는 분석에서 제외되므로 비율이 비정상적으로 높으면 분류 실패가
# 조용히 기사를 잠식하는 신호다 (응답 잘림, 프롬프트 회귀, 모델 변경 등).
# 평시 기타 비율은 10~20% 수준 — 30% 초과를 이상 징후로 본다.
MISC_RATIO_WARN_THRESHOLD = 0.30


def check_misc_ratio(
    stats: Dict[str, int],
    total: int,
    threshold: float = MISC_RATIO_WARN_THRESHOLD,
) -> float:
    """'기타' 분류 비율을 계산하고 임계 초과 시 경고 출력. 비율 반환.

    경고만 하고 실패 처리하지 않는다 — 분석 자체는 정상 분류된 기사로
    계속 진행하는 것이 옳고, 이 경보의 목적은 로그 가시화다.
    """
    if total <= 0:
        return 0.0
    misc = stats.get(FALLBACK_CATEGORY, 0)
    ratio = misc / total
    if ratio > threshold:
        print(
            f"  [경고] '기타' 분류 비율 {ratio:.0%} ({misc}/{total}건) — "
            f"임계 {threshold:.0%} 초과. 분류 품질 점검 필요 "
            f"(응답 잘림·프롬프트 회귀·모델 변경 의심)."
        )
    return ratio


def classify(llm: LLMWithFallback, articles: List[Article]) -> Dict[str, int]:
    """기사 리스트를 LLM으로 분류. in-place로 category, is_cross_category 채움.
    
    배치 분할 처리: 한 번에 CLASSIFY_BATCH_SIZE건씩 호출.
    
    Returns:
        분류 통계 dict. 예: {"IT·테크·AI": 12, ..., "_cross": 5}
        모든 배치 실패 시 모든 기사 "기타"로 폴백.
    """
    if not articles:
        return {cat: 0 for cat in VALID_CATEGORIES}

    total = len(articles)
    num_batches = (total + CLASSIFY_BATCH_SIZE - 1) // CLASSIFY_BATCH_SIZE
    print(f"  분류 대상: {total}건, 배치 {num_batches}개 (배치당 {CLASSIFY_BATCH_SIZE}건)")

    # 모든 배치 결과를 모은 classifications (id는 1-indexed 전체 기준)
    all_classifications: List[Dict[str, Any]] = []
    for batch_idx in range(num_batches):
        start = batch_idx * CLASSIFY_BATCH_SIZE
        end = min(start + CLASSIFY_BATCH_SIZE, total)
        batch = articles[start:end]
        print(f"    배치 {batch_idx + 1}/{num_batches}: 기사 {start + 1}~{end}")
        batch_results = _classify_batch(llm, batch, offset=start)
        # 0b: 배치 실패(빈 결과)면 재시도. 잔여 실패만 미해결(0a)로 떨어진다.
        attempt = 0
        while not batch_results and attempt < BATCH_RETRY:
            attempt += 1
            print(f"      [재시도 {attempt}/{BATCH_RETRY}] 배치 {batch_idx + 1} 재분류")
            if BATCH_RETRY_SLEEP:
                time.sleep(BATCH_RETRY_SLEEP)
            batch_results = _classify_batch(llm, batch, offset=start)
        all_classifications.extend(batch_results)

    # 모든 배치가 실패한 경우 (결과 0건)
    if not all_classifications:
        print(f"  [경고] 모든 배치 분류 실패. 폴백: 모든 기사 '미해결'(재실행 시 회복 대상).")
        _apply_fallback(articles)
        return {**{cat: 0 for cat in VALID_CATEGORIES}, "_unresolved": len(articles), "_cross": 0}

    stats = _apply_classifications(articles, all_classifications)
    print(f"  ✓ 분류 완료 (모델: {llm.last_used})")
    for cat in VALID_CATEGORIES:
        cnt = stats.get(cat, 0)
        if cnt > 0:
            print(f"    {cat}: {cnt}건")
    print(f"    (분야 경계 사건: {stats.get('_cross', 0)}건)")

    # 미해결(분류 실패) 가시화 — 배치 실패가 '기타'로 세탁되던 silent drop 차단.
    # 0a에선 로그 경고까지. Slack/exit 격상은 Phase 1b.
    unresolved = stats.get("_unresolved", 0)
    if unresolved:
        print(
            f"  [경고] 미해결(분류 실패) {unresolved}/{total}건 — LLM 판정 없음, "
            f"분석 잠정 제외. 재실행 시 회복 대상 (ADR-017)."
        )

    check_misc_ratio(stats, total)

    return stats


# ----------------------------------------------------------------------
# 단독 실행 (디버깅)
# ----------------------------------------------------------------------
if __name__ == "__main__":
    from collectors import collect_and_enrich
    from llm import get_llm

    print("=" * 60)
    print("Classifier 테스트")
    print("=" * 60)

    articles = collect_and_enrich()
    if not articles:
        print("수집 0건. 종료.")
        exit(0)

    llm = get_llm()
    stats = classify(llm, articles)

    # 분야별 샘플 출력
    print("\n[분야별 샘플]")
    by_cat: Dict[str, List[Article]] = {}
    for a in articles:
        by_cat.setdefault(a.category or "?", []).append(a)
    for cat, arts in by_cat.items():
        print(f"\n=== {cat} ({len(arts)}건) ===")
        for a in arts[:3]:
            cross_marker = " [경계]" if a.is_cross_category else ""
            print(f"  ({a.source}){cross_marker} {a.title[:60]}")