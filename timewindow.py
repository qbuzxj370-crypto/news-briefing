"""실행 예정 시각(cron) 기준으로 대상 날짜와 수집 윈도우를 계산.

배경 (2026-06-02 결정):
기존에는 main.py가 `now - 1일`로 대상 날짜를, collectors.py가 `now - 48h`로
수집 윈도우를 각각 실행 시각(now) 기준으로 계산했다. 그 결과:
- 재실행/Actions 지연 시 윈도우가 실행 시각만큼 평행이동 (표본이 매번 달라짐)
- 같은 "06-01 브리핑"이 08:27 실행과 13:25 실행에서 290건 vs 277건으로 갈림

해결: 실행 시각이 아니라 **cron 예정 시각(매일 07:00 KST)**을 단일 기준점으로
삼는다. GitHub Actions schedule은 예정 시각보다 일찍 트리거되지 않고 지연만
발생하므로(공식 동작), 예정 시각을 윈도우 끝으로 박아도 미래 기사를 긁을
위험이 없다. 언제 실행하든(정시/지연/수동 재실행) 같은 표본을 보장한다.

용어:
- D = 대상 날짜 (브리핑이 다루는 날 = 어제). 페이지 제목 "D 데일리 브리핑".
- 실행 예정 시각 = D+1 07:00 KST (= 오늘 아침, 사용자가 읽는 시점)
- 수집 윈도우 = [D-1 07:00, D+1 07:00) = 48시간
  · 끝(D+1 07:00): 새벽 급보까지 포함 (사용자가 아침에 읽을 때 최신 반영)
  · 폭 48h: RSS 반영 지연·매체 타임존 오차 보상 (날짜 간 중복은 dedup이 처리)
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

# cron 예정 시각 (KST). GitHub Actions cron과 반드시 수동 동기화할 것.
#   .github/workflows/*.yml 의 cron: '0 22 * * *' (UTC 22:00 = KST 07:00)
# 이 값을 바꾸면 cron도 같이 바꿔야 한다.
SCHEDULED_HOUR_KST = 7

# 수집 윈도우 폭 (시간). 반영 지연·타임존 오차 보상용 48h.
COLLECT_WINDOW_HOURS = 48

KST = timezone(timedelta(hours=9))


def _scheduled_run_dt(now_kst: datetime) -> datetime:
    """이번 실행이 대응하는 'cron 예정 시각'을 구한다.

    실행 시각(now)이 오늘 07:00 이후면 → 오늘 07:00이 예정 시각.
    만약 07:00 이전에 실행됐다면(수동 새벽 실행 등) → 어제 07:00이 예정 시각.
    (정상 cron은 07:00 이전에 안 돌지만, 수동 실행 방어)
    """
    today_scheduled = now_kst.replace(
        hour=SCHEDULED_HOUR_KST, minute=0, second=0, microsecond=0
    )
    if now_kst >= today_scheduled:
        return today_scheduled
    # 예정 시각 전에 실행된 경우(수동) → 직전 예정 시각(어제 07:00)
    return today_scheduled - timedelta(days=1)


def target_date_str(now_kst: datetime | None = None) -> str:
    """브리핑 대상 날짜(D = 어제) 문자열 'YYYY-MM-DD'.

    예정 시각(D+1 07:00)의 전날이 D.
    """
    if now_kst is None:
        now_kst = datetime.now(KST)
    scheduled = _scheduled_run_dt(now_kst)
    target = scheduled - timedelta(days=1)
    return target.strftime("%Y-%m-%d")


def collect_window(now_kst: datetime | None = None) -> tuple[datetime, datetime]:
    """수집 윈도우 [start, end) 반환. 실행 시각과 무관하게 예정 시각 기준 고정.

    end   = D+1 07:00 (예정 시각)
    start = end - 48h  = D-1 07:00
    """
    if now_kst is None:
        now_kst = datetime.now(KST)
    end = _scheduled_run_dt(now_kst)
    start = end - timedelta(hours=COLLECT_WINDOW_HOURS)
    return start, end