"""Daily News Briefing - 전체 파이프라인 오케스트레이션.

실행 흐름:
  1. RSS 수집 (본문 크롤링 없음 — ADR-018)
  2. LLM 분야 분류
  3. 분야별 dedup + 후보 본문 크롤링(robots.txt 준수) + 선별
  4. LLM 분석 (Gemini → 폴백 GPT-5 mini)
  5. JSON → 마크다운 렌더링
  6. Notion 업로드 (실패 시 로컬 백업)

종료 코드:
  0: 정상
  1: 수집 0건 (RSS 피드 점검 필요)
  2: LLM 분석 실패 (양쪽 다 실패)
  3: Notion 업로드 실패 (로컬 백업은 보존)
"""

from __future__ import annotations

import os
import sys
import traceback

from timewindow import target_date_str
from collectors import (
    collect_articles,
    dedup_similar,
    enrich_with_bodies,
    select_for_analysis,
)
from analyzer import analyze, AnalysisValidationError
from classifier import classify, VALID_CATEGORIES, FALLBACK_CATEGORY
from llm import get_llm
from renderer import render_report
from data_profile import build_data_profile
from notion_writer import upload_to_notion, save_local_backup
from slack_writer import send_slack_briefing, send_slack_error
from snapshot import save_snapshot, load_snapshot


# 분석 대상 분야 (FALLBACK_CATEGORY="기타"는 분석에서 제외)
ANALYSIS_CATEGORIES = [c for c in VALID_CATEGORIES if c != FALLBACK_CATEGORY]


def recover_unresolved(llm, all_articles) -> int:
    """스냅샷 재개 시 '미해결' 부분집합만 회복 재분류한다 (ADR-017 2a).

    genuine '기타'·정상 분류분은 건드리지 않는다 — 분류는 temperature>0이라
    재굴림하면 라벨이 바뀌어 결정론(ADR-015)이 깨지기 때문. 미해결만 다시 돌려
    수렴시킨다(해결되면 classifier가 플래그를 해제). 회복이 또 실패해도 예외를
    삼켜 파이프라인을 막지 않는다 (스냅샷=막지 않는 최적화 원칙).

    Returns:
        회복을 시도한 미해결 기사 수 (0이면 완전한 스냅샷이라 LLM 미호출).
    """
    unresolved = [a for a in all_articles if a.classification_unresolved]
    if not unresolved:
        return 0
    try:
        classify(llm, unresolved)  # in-place; 해결분은 플래그 해제, 잔여는 미해결 유지
    except Exception as e:
        print(f"  [경고] 회복 재분류 실패 (무시하고 진행): {e}")
    return len(unresolved)


def main() -> int:
    print("=" * 60)
    print("Daily News Briefing")
    print("=" * 60)

    # 대상 날짜 (D = 어제, cron 예정 시각 07:00 KST 기준 고정)
    # 실행 시각이 아니라 예정 시각 기준이라, 재실행/지연에도 동일.
    date_str = target_date_str()
    print(f"대상 날짜: {date_str}")

    # ------------------------------------------------------------------
    # 1) 수집 (같은 날짜 스냅샷이 있으면 재사용)
    # ------------------------------------------------------------------
    # 본문 크롤링은 여기서 하지 않는다 — 분류·dedup 후 분야별 선별 후보에만
    # 수행 (ADR-018, step 3). 매체 서버 요청 수와 저작물 복제 범위 최소화.
    #
    # 재실행 시 RSS 피드 top-N이 갱신되어 1차 실행의 표본을 잃는 문제 대응
    # (2026-06-11 관찰: 재실행에서 314건→127건, 5개 매체 누락). 스냅샷이
    # 있으면 수집·분류를 건너뛰고 동일 표본으로 분석부터 재개한다.
    all_articles = load_snapshot(date_str)
    resumed = all_articles is not None
    if resumed:
        print(f"\n[1] 스냅샷 재개: 같은 날짜 1차 실행의 수집·분류 결과 재사용 ({len(all_articles)}건)")
    else:
        try:
            print("\n[1] RSS 수집")
            all_articles = collect_articles()
        except Exception as e:
            print(f"\n[치명 오류] 수집 단계 실패: {e}")
            traceback.print_exc()
            return 1

    if not all_articles:
        print("\n[종료] 수집된 기사 0건. 알림 페이지만 생성.")
        empty_md = render_report(date_str, None, {})
        save_local_backup(empty_md, date_str, suffix="empty")
        try:
            upload_to_notion(
                markdown=empty_md,
                date_str=date_str,
                model="none",
                body_ratio_pct=0,
                category_tags=[],
                article_count=0,
            )
        except Exception:
            pass
        return 1

    # ------------------------------------------------------------------
    # 2) LLM 분야 분류 (제목 기반)
    #    - 신규 수집: 전량 분류
    #    - 스냅샷 재개: 미해결 부분집합만 회복 재분류 (ADR-017 2a)
    # ------------------------------------------------------------------
    llm = get_llm()
    if not resumed:
        print("\n[2] LLM 분야 분류")
        try:
            classify(llm, all_articles)
        except Exception as e:
            # classifier.classify는 내부 폴백을 가지지만 만약을 대비.
            # 전량 '기타' 세탁 대신 '미해결'로 (ADR-017) — 재실행 회복 대상.
            print(f"  [경고] 분류 단계 예외: {e}. 모든 기사 '미해결'로 폴백.")
            for a in all_articles:
                a.category = None
                a.is_cross_category = False
                a.classification_unresolved = True
        # 수집·분류 표본을 스냅샷으로 저장. 미해결이 남아도 저장한다 —
        # complete=False로 기록돼 재실행이 그 표본 위에서 회복(2a)한다.
        # 저장 보류는 재수집을 강제해 표본 드리프트(ADR-015 위반)를 부르므로 안 함.
        try:
            save_snapshot(date_str, all_articles)
        except Exception as e:
            print(f"  [경고] 스냅샷 저장 실패 (무시하고 진행): {e}")
    else:
        unresolved_n = sum(1 for a in all_articles if a.classification_unresolved)
        if unresolved_n:
            print(f"\n[2] 미해결 {unresolved_n}건 회복 재분류 (부분집합)")
            recover_unresolved(llm, all_articles)
            try:
                save_snapshot(date_str, all_articles)  # 해결분 반영해 재저장
            except Exception as e:
                print(f"  [경고] 회복 스냅샷 재저장 실패 (무시): {e}")
        else:
            print("\n[2] 스냅샷 완전(complete) — 회복 재분류 불필요")

    # ------------------------------------------------------------------
    # 3) 분야별 분배 + dedup + 선별 ("기타"는 제외)
    # ------------------------------------------------------------------
    # 분야별 선별 한도 동적 계산: 풀 크기에 비례.
    # 큰 분야(예: 정치 95건)는 더 많이 추출, 작은 분야는 최소값 보장.
    # MAX=20은 급박한 특보 상황(한 분야 폭주) 안전망. 평시엔 ratio가 작동해 12~15건 수준.
    SELECT_RATIO = 0.15
    SELECT_MIN = 8
    SELECT_MAX = 20

    print("\n[3] 분야별 dedup + 후보 본문 크롤링 + 선별")
    article_data: dict = {}
    for cat in ANALYSIS_CATEGORIES:
        cat_articles = [a for a in all_articles if a.category == cat]
        if not cat_articles:
            article_data[cat] = []
            print(f"  {cat}: 0건")
            continue
        deduped = dedup_similar(cat_articles)
        # 풀 크기(dedup 후 기준)에 비례한 max_total. 8~15 범위.
        target = max(SELECT_MIN, min(SELECT_MAX, int(len(deduped) * SELECT_RATIO)))
        # 본문 크롤링은 이 분야의 선별 후보에만, 목표 도달 시 중단 (ADR-018).
        # robots.txt 차단 URL은 fetch_body가 요청 없이 건너뛴다.
        enrich_with_bodies(deduped, target=target)
        selected = select_for_analysis(deduped, max_total=target)
        article_data[cat] = selected
        body_count = sum(1 for a in selected if a.has_body)
        print(f"  {cat}: 분류 {len(cat_articles)} → dedup {len(deduped)} → 목표 {target} → 선택 {len(selected)} (본문 {body_count})")

    # 스냅샷은 본문을 저장하지 않으므로(ADR-019) 여기서 재저장하지 않는다.
    # 재실행(스냅샷 재개)도 이 단계를 다시 지나며 후보만 재크롤링한다.

    excluded = sum(1 for a in all_articles if a.category == FALLBACK_CATEGORY)
    # 분류 미해결(ADR-017): 배치 실패로 LLM 판정을 못 받아 분석에서 잠정 제외된 기사.
    # genuine '기타'와 구분해 가시화 — 재실행 시 회복(2a) 대상.
    unresolved_count = sum(1 for a in all_articles if a.classification_unresolved)
    print(f"  (기타 분야 제외: {excluded}건, 분류 미해결: {unresolved_count}건)")
    if unresolved_count:
        print(
            f"  [경고] 분류 미해결 {unresolved_count}건 — 이번 브리핑은 일부 분야가 "
            f"누락됐을 수 있음. 재실행 시 회복됩니다 (ADR-017)."
        )

    total = sum(len(v) for v in article_data.values())
    if total == 0:
        print("\n[종료] 분류 후 분석 대상 0건. 알림 페이지만 생성.")
        empty_md = render_report(date_str, None, {})
        save_local_backup(empty_md, date_str, suffix="empty")
        try:
            upload_to_notion(
                markdown=empty_md,
                date_str=date_str,
                model="none",
                body_ratio_pct=0,
                category_tags=[],
                article_count=0,
            )
        except Exception:
            pass
        return 1

    # ------------------------------------------------------------------
    # 4) LLM 분석
    # ------------------------------------------------------------------
    print("\n[4] LLM 분석")
    # 데이터 프로필: 선별 전 전체 분류 풀(all_articles) 기준 분포·빈도를 집계해
    # 프롬프트에 주입. LLM은 이 수치를 인용만 하고 새 숫자는 만들지 않는다.
    data_profile = build_data_profile(all_articles)
    try:
        # llm은 [2] 단계에서 이미 생성됨
        result = analyze(llm, article_data, data_profile=data_profile)
    except AnalysisValidationError as e:
        print(f"\n[치명 오류] LLM 출력 검증 실패: {e}")
        traceback.print_exc()
        # 폴백: 분석 없는 페이지라도 만들기
        result = {
            "analysis": None,
            "id_to_article": {},
            "model_used": "none",
            "stats": {"total": total, "with_body": 0},
        }
    except Exception as e:
        print(f"\n[치명 오류] LLM 호출 단계 전체 실패: {e}")
        traceback.print_exc()
        result = {
            "analysis": None,
            "id_to_article": {},
            "model_used": "none",
            "stats": {"total": total, "with_body": 0},
        }

    analysis = result["analysis"]
    id_to_article = result["id_to_article"]
    model_used = result["model_used"]
    stats = result["stats"]

    # ------------------------------------------------------------------
    # 5) 마크다운 렌더링
    # ------------------------------------------------------------------
    print("\n[5] 마크다운 렌더링")
    try:
        markdown = render_report(date_str, analysis, id_to_article)
        print(f"  ✓ 렌더링 완료 ({len(markdown):,}자)")
    except Exception as e:
        print(f"\n[치명 오류] 렌더링 실패: {e}")
        traceback.print_exc()
        markdown = (
            f"# 📰 {date_str} 데일리 브리핑\n\n"
            f"> ⚠️ **렌더링 실패**\n"
            f"> 분석은 완료되었으나 마크다운 변환 단계에서 오류가 발생했습니다.\n"
        )
        # 디버깅용으로 raw JSON 저장
        if analysis:
            import json
            save_local_backup(
                json.dumps(analysis, ensure_ascii=False, indent=2),
                date_str,
                suffix="raw_analysis",
            )

    # ------------------------------------------------------------------
    # 6) Notion 업로드 (실패해도 로컬 백업 보존)
    # ------------------------------------------------------------------
    print("\n[6] Notion 업로드")

    # 본문 확보율
    body_ratio = (
        int(stats["with_body"] / stats["total"] * 100)
        if stats["total"] > 0 else 0
    )

    # 분야 태그: 데이터 있는 분야(기사 1건 이상)만 (사용자 결정사항)
    category_tags = [
        cat for cat, arts in article_data.items() if arts
    ]

    success, ref, issue_block_map = upload_to_notion(
        markdown=markdown,
        date_str=date_str,
        model=model_used,
        body_ratio_pct=body_ratio,
        category_tags=category_tags,
        article_count=stats["total"],
    )

    # ------------------------------------------------------------------
    # 7) Slack 알림 (SLACK_WEBHOOK_URL 미설정 시 스킵)
    # ------------------------------------------------------------------
    slack_url = os.environ.get("SLACK_WEBHOOK_URL")
    if slack_url:
        print("\n[7] Slack 알림")
        repo = os.environ.get("GITHUB_REPOSITORY", "")
        if analysis is None:
            send_slack_error(slack_url, date_str, exit_code=2, repo=repo)
        elif not success:
            send_slack_error(slack_url, date_str, exit_code=3, error_detail=ref, repo=repo)
        else:
            send_slack_briefing(
                webhook_url=slack_url,
                date_str=date_str,
                tldr=analysis.get("tldr", ""),
                analysis=analysis,
                page_id=ref,
                issue_block_map=issue_block_map,
                stats={
                    "article_count": stats["total"],
                    "body_ratio_pct": body_ratio,
                    "model_used": model_used,
                    "unresolved": unresolved_count,
                },
            )

    # ------------------------------------------------------------------
    # 결과 출력
    # ------------------------------------------------------------------
    print("\n" + "=" * 60)
    if analysis is None:
        print(f"⚠️  분석 실패. Notion {'업로드 됨' if success else '업로드 실패'}.")
        return 2
    if not success:
        print(f"⚠️  Notion 업로드 실패. 로컬 백업: {ref}")
        return 3

    print(f"✓ 완료. 페이지 ID: {ref}")
    print(f"  - 분야: {len(category_tags)}개 ({', '.join(category_tags)})")
    print(f"  - 분석 기사: {stats['total']}건 (본문 {stats['with_body']}건, {body_ratio}%)")
    print(f"  - 모델: {model_used}")
    return 0


if __name__ == "__main__":
    sys.exit(main())