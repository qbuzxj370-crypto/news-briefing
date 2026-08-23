"""A/B 하네스 — 같은 입력으로 두 조건(모델·프롬프트)의 분석 결과를 비교.

배경: 매일 기사가 달라지므로 "바꾼 게 효과가 있었나"를 산출물 비교로 답할 수 없다.
어제보다 좋아 보여도 그게 변경 덕인지 그날 기사가 좋아서인지 구분되지 않는다.
스냅샷(ADR-015/019)으로 기사 집합을 고정하면 입력을 통제한 비교가 가능해진다.

핵심 설계 — 입력을 한 번만 만들어 모든 조건이 공유한다:

    prepare : 스냅샷 → dedup·선별·본문 크롤링·데이터 프로필까지 1회 수행해 캐시
    run     : 캐시된 입력으로 LLM 분석 실행, 결과·지표 저장 (조건마다 반복)
    compare : 저장된 결과들의 지표를 표로 대조

prepare를 분리한 이유는 본문 크롤링이 조건마다 달라지면 안 되기 때문이다. 매번
크롤링하면 네트워크·robots·기사 삭제로 본문 확보가 달라져 입력이 흔들리고, 그러면
결과 차이가 조건 때문인지 입력 때문인지 알 수 없다.

프롬프트 A/B도 같은 캐시로 한다: prepare 1회 → run(label=before) → 프롬프트 수정
→ run(label=after) → compare. 코드를 바꿔도 입력은 그대로다.

주의 — 단일 표본으로 결론 내지 말 것:
분석 호출은 temperature 0.5라 같은 조건을 두 번 돌려도 출력이 다르다. 1회씩 비교해
"B가 낫다"고 판단하면 조건 차이가 아니라 샘플링 노이즈를 보는 것일 수 있다.
--runs로 조건당 2~3회 실행하고, compare가 출력하는 회차별 편차를 함께 본다.

폴백을 쓰지 않는다: 조건 A가 실패해 OpenAI로 넘어가면 비교가 오염된다. 실패는
숨기지 않고 그대로 드러낸다.

Gemini 3.x 주의: 분류(classifier)는 thinking_budget=0을 쓰는데 3.x가 이를 거부한다.
이 하네스는 이미 분류된 스냅샷에서 시작하고 분석만 호출하므로(thinking_budget 미지정)
3.x 모델도 그대로 시험할 수 있다. 3.x를 운영에 올리려면 분류 경로를 먼저 손봐야 한다.

캐시·결과는 ab_runs/에 저장하며 .gitignore 대상이다. 입력 캐시에는 기사 본문이
담기므로(ADR-019 — 공개 아티팩트에 본문을 싣지 않는다는 결정) 커밋·업로드 금지.

사용:
    python ab_probe.py prepare backups/2026-07-25_snapshot.json
    python ab_probe.py run ab_runs/input_2026-07-25.json --label base --runs 2
    python ab_probe.py run ab_runs/input_2026-07-25.json --label v36 --runs 2 --model gemini-3.6-flash
    python ab_probe.py compare base v36
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

AB_DIR = Path(__file__).parent / "ab_runs"


# ----------------------------------------------------------------------
# prepare — 입력 1회 생성 (모든 조건이 공유)
# ----------------------------------------------------------------------
def prepare(snapshot_path: Path, out_path: Optional[Path] = None) -> Path:
    """스냅샷 → 분석 직전 입력(선별된 기사 + 데이터 프로필)을 만들어 캐시.

    main.py step 3과 같은 순서로 dedup → 본문 크롤링 → 선별을 수행한다.
    파이프라인이 바뀌면 이 함수도 따라가야 한다.
    """
    from collectors import dedup_similar, enrich_with_bodies, select_for_analysis
    from classifier import VALID_CATEGORIES, FALLBACK_CATEGORY
    from data_profile import build_data_profile
    from snapshot import load_snapshot

    date_str = snapshot_path.name.split("_")[0]
    articles = load_snapshot(date_str, backup_dir=snapshot_path.parent)
    if not articles:
        raise SystemExit(f"스냅샷을 읽지 못했습니다: {snapshot_path}")
    print(f"스냅샷 로드: {date_str} — {len(articles)}건")

    # main.py step 3과 동일한 상수·순서
    SELECT_RATIO, SELECT_MIN, SELECT_MAX = 0.15, 8, 20
    categories = [c for c in VALID_CATEGORIES if c != FALLBACK_CATEGORY]

    article_data: Dict[str, List[Any]] = {}
    for cat in categories:
        cat_articles = [a for a in articles if a.category == cat]
        if not cat_articles:
            article_data[cat] = []
            continue
        deduped = dedup_similar(cat_articles)
        target = max(SELECT_MIN, min(SELECT_MAX, int(len(deduped) * SELECT_RATIO)))
        enrich_with_bodies(deduped, target=target)
        selected = select_for_analysis(deduped, max_total=target)
        article_data[cat] = selected
        body_n = sum(1 for a in selected if a.has_body)
        print(f"  {cat}: {len(cat_articles)} → 선택 {len(selected)} (본문 {body_n})")

    profile = build_data_profile(articles)

    payload = {
        "date_str": date_str,
        "data_profile": profile,
        "article_data": {
            cat: [
                {
                    "title": a.title, "summary": a.summary, "link": a.link,
                    "source": a.source, "category": a.category,
                    "is_cross_category": a.is_cross_category,
                    "published": a.published.isoformat(),
                    "body": a.body,  # 본문 포함 — 로컬 전용, 커밋 금지
                }
                for a in arts
            ]
            for cat, arts in article_data.items()
        },
    }
    AB_DIR.mkdir(exist_ok=True)
    out = out_path or AB_DIR / f"input_{date_str}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    total = sum(len(v) for v in article_data.values())
    print(f"\n입력 캐시 저장: {out} (분석 대상 {total}건)")
    print("  ※ 본문 포함 — 로컬 전용, 커밋·업로드 금지 (ADR-019)")
    return out


def _load_input(path: Path):
    """prepare가 만든 캐시를 Article 객체로 복원."""
    from datetime import datetime
    from collectors import Article

    payload = json.loads(path.read_text(encoding="utf-8"))
    article_data = {
        cat: [
            Article(
                title=d["title"], summary=d["summary"], link=d["link"],
                source=d["source"], category=d["category"],
                is_cross_category=d["is_cross_category"],
                published=datetime.fromisoformat(d["published"]),
                body=d.get("body"),
            )
            for d in arts
        ]
        for cat, arts in payload["article_data"].items()
    }
    return payload["date_str"], payload.get("data_profile", ""), article_data


# ----------------------------------------------------------------------
# run — 한 조건으로 N회 분석
# ----------------------------------------------------------------------
def _metrics(analysis: Dict[str, Any], id_to_article: Dict[int, Any]) -> Dict[str, Any]:
    """비교에 쓸 지표를 실행 시점에 계산해 저장한다.

    compare가 id_to_article 없이도 표를 그릴 수 있게, 결과 JSON에 지표를 박아둔다.
    """
    from analyzer import (
        insight_grounding_stats, source_coverage_stats, source_concentration_stats,
    )

    ground = insight_grounding_stats(analysis)
    cover = source_coverage_stats(analysis)
    concentration = source_concentration_stats(analysis, id_to_article)

    conf: Dict[str, int] = {"높음": 0, "중간": 0, "판단유보": 0}
    for cat in analysis.get("categories", []):
        for issue in cat.get("deep_issues", []):
            c = issue.get("confidence")
            if c in conf:
                conf[c] += 1

    return {
        "insight_total": ground["total"],
        "insight_with_number": ground["total"] - len(ground["no_number"]),
        "insight_with_limitation": ground["total"] - len(ground["no_limitation"]),
        "deep_issue_count": cover["deep_issue_count"],
        "avg_refs": round(cover["avg_refs"], 2),
        "low_ref_long": len(cover["low_ref_long"]),
        "single_source": len(concentration),
        "confidence": conf,
    }


def run(input_path: Path, label: str, runs: int, model: Optional[str]) -> None:
    from analyzer import analyze
    from llm import GeminiProvider, LLMWithFallback, GEMINI_MODEL

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise SystemExit("GEMINI_API_KEY가 필요합니다")

    date_str, profile, article_data = _load_input(input_path)
    used_model = model or GEMINI_MODEL
    total = sum(len(v) for v in article_data.values())
    print(f"조건 '{label}' — 모델 {used_model} / 입력 {total}건 / {runs}회 실행")
    if not profile:
        print("  [경고] 입력 캐시에 데이터 프로필이 없습니다 (구 버전 캐시?)")

    AB_DIR.mkdir(exist_ok=True)
    for i in range(1, runs + 1):
        print(f"\n--- {label} 회차 {i}/{runs}")
        # 폴백 없이 단일 모델 — 실패가 다른 모델로 가려지면 비교가 오염된다
        llm = LLMWithFallback(GeminiProvider(api_key, model=used_model), None)
        try:
            result = analyze(llm, article_data, data_profile=profile)
        except Exception as e:
            print(f"  [실패] {type(e).__name__}: {e}")
            continue
        analysis = result["analysis"]
        if analysis is None:
            print("  [실패] 분석 결과 없음")
            continue
        m = _metrics(analysis, result["id_to_article"])
        out = AB_DIR / f"{date_str}_{label}_{i}.json"
        out.write_text(json.dumps({
            "label": label, "run": i, "date_str": date_str,
            "model": used_model, "model_used": result["model_used"],
            "metrics": m, "analysis": analysis,
        }, ensure_ascii=False), encoding="utf-8")
        print(f"  저장: {out.name} — 수치인용 {m['insight_with_number']}/{m['insight_total']}, "
              f"신뢰도 {m['confidence']}")


# ----------------------------------------------------------------------
# compare — 저장된 결과 대조
# ----------------------------------------------------------------------
def _collect(label: str) -> List[Dict[str, Any]]:
    return [
        json.loads(p.read_text(encoding="utf-8"))
        for p in sorted(AB_DIR.glob(f"*_{label}_*.json"))
    ]


def _agg(runs: List[Dict[str, Any]], key: str) -> str:
    """회차별 값을 '평균 (범위)'로. 편차가 크면 단일 표본 결론을 피하라는 신호."""
    vals = [r["metrics"][key] for r in runs]
    if not vals:
        return "-"
    if len(vals) == 1:
        return f"{vals[0]}"
    lo, hi = min(vals), max(vals)
    mean = statistics.mean(vals)
    spread = f" ({lo}~{hi})" if lo != hi else ""
    return f"{mean:.1f}{spread}"


def compare(labels: List[str], show_insight: bool) -> None:
    data = {lb: _collect(lb) for lb in labels}
    for lb, runs in data.items():
        if not runs:
            raise SystemExit(f"'{lb}' 결과가 없습니다. run을 먼저 실행하세요.")
        print(f"{lb}: {len(runs)}회 — 모델 {runs[0]['model']}")

    rows = [
        ("수치 인용 분야", "insight_with_number"),
        ("한계 절 분야", "insight_with_limitation"),
        ("deep_issue 수", "deep_issue_count"),
        ("평균 참조 수", "avg_refs"),
        ("참조≤1 장문", "low_ref_long"),
        ("단일 매체 편중", "single_source"),
    ]
    width = max(len(n) for n, _ in rows) + 2
    print("\n" + "지표".ljust(width) + "".join(lb.ljust(18) for lb in labels))
    print("-" * (width + 18 * len(labels)))
    for name, key in rows:
        line = name.ljust(width)
        for lb in labels:
            line += _agg(data[lb], key).ljust(18)
        print(line)

    line = "신뢰도 분포".ljust(width)
    for lb in labels:
        c: Dict[str, int] = {"높음": 0, "중간": 0, "판단유보": 0}
        for r in data[lb]:
            for k, v in r["metrics"]["confidence"].items():
                c[k] += v
        n = len(data[lb])
        line += f"높{c['높음']/n:.0f}/중{c['중간']/n:.0f}/유{c['판단유보']/n:.0f}".ljust(18)
    print(line)

    print("\n※ 괄호는 회차별 최소~최대. 폭이 넓으면 조건 차이가 아니라 샘플링 편차일 수 있다.")
    print("※ 해석이 관찰에서 도출되는지는 자동 판정이 불가능하다 — 아래 인사이트를 직접 대조할 것.")

    if show_insight:
        for lb in labels:
            first = data[lb][0]["analysis"]
            print(f"\n{'=' * 20} {lb} (회차 1) 인사이트")
            for cat in first.get("categories", []):
                ins = cat.get("insight") or {}
                if not isinstance(ins, dict):
                    continue
                print(f"\n[{cat.get('name', '')}]")
                for k, ko in (("observation", "관찰"), ("interpretation", "해석"), ("limitation", "한계")):
                    if ins.get(k):
                        print(f"  {ko}: {ins[k]}")


def main() -> int:
    ap = argparse.ArgumentParser(description="같은 입력으로 두 조건의 분석을 비교")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_prep = sub.add_parser("prepare", help="스냅샷 → 분석 입력 캐시 (본문 크롤링 1회)")
    p_prep.add_argument("snapshot", type=Path)

    p_run = sub.add_parser("run", help="캐시된 입력으로 한 조건을 N회 실행")
    p_run.add_argument("input", type=Path)
    p_run.add_argument("--label", required=True, help="조건 이름 (예: base, v36)")
    p_run.add_argument("--runs", type=int, default=2, help="반복 횟수 (기본 2)")
    p_run.add_argument("--model", default=None, help="Gemini 모델 (미지정 시 운영 모델)")

    p_cmp = sub.add_parser("compare", help="저장된 조건들의 지표 대조")
    p_cmp.add_argument("labels", nargs="+")
    p_cmp.add_argument("--insight", action="store_true", help="인사이트 본문도 출력")

    args = ap.parse_args()
    if args.cmd == "prepare":
        prepare(args.snapshot)
    elif args.cmd == "run":
        if args.runs < 1:
            raise SystemExit("--runs는 1 이상")
        run(args.input, args.label, args.runs, args.model)
    else:
        compare(args.labels, args.insight)
    return 0


if __name__ == "__main__":
    sys.exit(main())
