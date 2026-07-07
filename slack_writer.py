"""Slack Incoming Webhook 알림 발송.

성공 시: tldr + 분야별 deep_issue 목록 (Notion 블록 딥링크 포함)
실패 시: 오류 종류 + 상세 정보
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

import requests


_SECTION_LIMIT = 2800  # Slack section 블록 텍스트 한도 (공식 3000자, 여유 포함)

_CATEGORY_EMOJI: Dict[str, str] = {
    "IT·테크·AI": "💻",
    "경제·금융·증시": "💰",
    "정치": "🏛️",
    "사회·시사": "🗞️",
    "산업": "🏭",
}

_ERROR_REASONS: Dict[int, str] = {
    1: "수집된 기사 없음 (RSS 피드 점검 필요)",
    2: "LLM 분석 실패 (Gemini + GPT-5 mini 모두 실패)",
    3: "Notion 업로드 실패 (로컬 백업 보존됨)",
}


# ----------------------------------------------------------------------
# Block Kit 빌더
# ----------------------------------------------------------------------
def _briefing_blocks(
    date_str: str,
    tldr: str,
    analysis: Dict[str, Any],
    page_id: str,
    issue_block_map: Dict[str, str],
    stats: Dict[str, Any] | None = None,
) -> List[Dict[str, Any]]:
    blocks: List[Dict[str, Any]] = []

    blocks.append({
        "type": "header",
        "text": {"type": "plain_text", "text": f"📰 {date_str} 데일리 브리핑", "emoji": True},
    })

    if tldr:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"💡 *오늘의 핵심*\n{tldr.strip()}"},
        })

    key_threads = analysis.get("mega_trend", {}).get("key_threads", [])
    if key_threads:
        threads_text = "🌐 *오늘의 흐름*\n" + "\n".join(f"• {t}" for t in key_threads)
        if len(threads_text) > _SECTION_LIMIT:
            threads_text = threads_text[:_SECTION_LIMIT] + "…"
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": threads_text},
        })

    blocks.append({"type": "divider"})

    for cat in analysis.get("categories", []):
        deep_issues = cat.get("deep_issues", [])
        if not deep_issues:
            continue

        cat_name = cat.get("name", "")
        emoji = _CATEGORY_EMOJI.get(cat_name, "📂")
        lines = [f"*{emoji} {cat_name}*"]

        for issue in deep_issues:
            title = issue.get("title", "").strip()
            url = issue_block_map.get(title)
            if url:
                lines.append(f"  • <{url}|📊 {title}>")
            else:
                lines.append(f"  • 📊 {title}")

        text = "\n".join(lines)
        if len(text) > _SECTION_LIMIT:
            text = text[:_SECTION_LIMIT] + "…"

        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": text},
        })

    # 분류 미해결 경고 (ADR-017 1b): 부분실패로 일부 분야가 누락된 degraded
    # 브리핑임을 운영자에게 알림. 재실행 시 회복(2a) 가능.
    unresolved = (stats or {}).get("unresolved", 0)
    if unresolved:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": (
                f"⚠️ *분류 미해결 {unresolved}건* — 일부 분야가 누락됐을 수 있습니다. "
                f"재실행하면 회복됩니다."
            )},
        })

    blocks.append({"type": "divider"})

    page_url = f"https://www.notion.so/{page_id.replace('-', '')}"
    blocks.append({
        "type": "actions",
        "elements": [{
            "type": "button",
            "text": {"type": "plain_text", "text": "노션에서 전문 보기 →", "emoji": False},
            "url": page_url,
        }],
    })

    if stats:
        total_issues = sum(len(cat.get("deep_issues", [])) for cat in analysis.get("categories", []))
        deeplink_count = len(issue_block_map)
        parts = [f"기사 {stats.get('article_count', '?')}건"]
        if "body_ratio_pct" in stats:
            parts.append(f"본문 확보 {stats['body_ratio_pct']}%")
        model = stats.get("model_used", "")
        if model and model != "none":
            parts.append(model)
        if total_issues > 0:
            parts.append(f"딥링크 {deeplink_count}/{total_issues}")
        if unresolved:
            parts.append(f"⚠️ 미해결 {unresolved}")
        blocks.append({
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": " · ".join(parts)}],
        })

    return blocks


def _error_blocks(
    date_str: str,
    exit_code: int,
    error_detail: str,
    repo: str = "",
) -> List[Dict[str, Any]]:
    reason = _ERROR_REASONS.get(exit_code, f"알 수 없는 오류 (종료 코드 {exit_code})")
    text = f"*원인:* {reason}"
    if error_detail:
        text += f"\n*상세:* {error_detail}"

    blocks: List[Dict[str, Any]] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"⚠️ {date_str} 브리핑 생성 실패", "emoji": True},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": text},
        },
    ]

    if repo:
        workflow_url = f"https://github.com/{repo}/actions/workflows/daily.yml"
        blocks.append({
            "type": "actions",
            "elements": [{
                "type": "button",
                "text": {"type": "plain_text", "text": "Actions에서 수동 재실행 →", "emoji": False},
                "url": workflow_url,
            }],
        })

    return blocks


# ----------------------------------------------------------------------
# 발송 함수
# ----------------------------------------------------------------------
def send_slack_briefing(
    webhook_url: str,
    date_str: str,
    tldr: str,
    analysis: Dict[str, Any],
    page_id: str,
    issue_block_map: Dict[str, str],
    stats: Dict[str, Any] | None = None,
) -> bool:
    """브리핑 완료 알림 발송. 성공 여부 반환."""
    blocks = _briefing_blocks(date_str, tldr, analysis, page_id, issue_block_map, stats)
    return _post(webhook_url, {"blocks": blocks})


def send_slack_error(
    webhook_url: str,
    date_str: str,
    exit_code: int,
    error_detail: str = "",
    repo: str = "",
) -> bool:
    """오류 알림 발송. 성공 여부 반환."""
    blocks = _error_blocks(date_str, exit_code, error_detail, repo)
    return _post(webhook_url, {"blocks": blocks})


def _post(webhook_url: str, payload: Dict[str, Any]) -> bool:
    try:
        resp = requests.post(
            webhook_url,
            data=json.dumps(payload),
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        if resp.status_code == 200 and resp.text == "ok":
            print("  ✓ Slack 알림 발송 완료")
            return True
        print(f"  [경고] Slack 응답: {resp.status_code} {resp.text}")
        return False
    except Exception as e:
        print(f"  [경고] Slack 발송 실패: {e}")
        return False
