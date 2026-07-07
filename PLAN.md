# PLAN.md 작성 계획 v4 — 실제 Notion 산출물 검증 반영

## Context

사용자 요청: 레포 상태(README/진입점/테스트·빌드/미완성)를 조사해 "완성본까지 남은 작업"을 우선순위 + 완료 판정 기준과 함께 **PLAN.md**로 작성. 적대적 재검토 2회 후, 사용자가 노션 DB(비공개 — ID는 GitHub Secret `NOTION_DATABASE_ID`로 관리)를 제공해 **실제 산출물 기반으로 재계획**.

핵심 전환: 레포 정적 분석에서는 "스텁 0건, 사실상 완성"이었으나, **실물 페이지 검증에서 독자에게 보이는 결함 4건 발견** → PLAN.md의 P0가 ADR 백로그가 아닌 실물 결함 중심으로 재편됨.

## 실물 검증에서 확인된 사실 (페이지 06-09, 06-04 + DB row 목록)

1. **메가 트렌드에 article ID 누출**: 06-09 페이지 메가 트렌드 본문에 `\[40\]`, `\[1\]`, `\[25\]`, `\[26\]` 노출. 원인 확인: renderer.py:146은 insight에만 `_clean_insight_ids` 적용, **renderer.py:152의 mega_trend는 미적용**. ADR-001 수정의 사각지대. 06-04엔 없음 → LLM 출력에 따라 간헐 발생.
2. **같은 날짜 중복 페이지**: 05-25 ×5, 06-01 ×2, 06-03 ×2. 재실행 시 기존 row 확인 없이 새 row 생성. 06-03 중복은 윈도우 고정 수정(06-02) **이후** 발생 — 윈도우 고정으로 내용은 같아져도 페이지는 계속 중복됨. ADR-015(날짜 간 중복 기사)와 별개의 미해결 문제.
3. **한겨레 RSS 0건 공급 지속**: RSS_FEEDS에 한겨레 피드 존재(collectors.py:40)하나 06-04·06-09 푸터(실수집 매체 목록) 모두에 한겨레 없음 → 피드가 죽었거나 차단. 실패가 조용히 지나감(모니터링 부재의 실증).
4. **연합뉴스는 피드에 아예 없음**: README 매체 표(연합뉴스 포함 9개 주장)와 달리 RSS_FEEDS에 연합뉴스 URL 없음(SOURCE_MAP에만 존재). README-코드 불일치 실증.
5. **제목에 보이지 않는 문자(U+FEFF) 잔존**: 두 페이지 모두 링크 텍스트 선두에 `﻿` 노출 (06-03 엔티티 디코딩 수정이 zero-width 문자는 미처리).
6. 양호한 점: 본문 확보율 100%, 기사 46~47건, 5분야 태그, 출처 링크/토글 구조 정상, init_db↔notion_writer 속성 6개 일치.

레포 정적 분석 결과(이전 확정): TODO/스텁/빈 핸들러 0건 / 테스트 5파일 중 3개는 의존성 미설치로 미실행(통과 여부 미확정) / CI는 `main.py`만 실행하고 테스트 안 돌림 + `continue-on-error: true`(daily.yml:34)로 종료코드 무력화 / 루트 `daily.yml`·`report.md.j2` 잔재(참조 0건 확인) / ADR 미해결: MISC 경보(저비용 우선 후보), ADR-015 측정 대기, OpenAI 키 미등록 등.

## 실행 단계

1. **사실 확정**: `pip install -r requirements.txt` → 5개 테스트 전부 실행 + TODO grep 재확인 + (네트워크 되면) `python rss_probe.py`로 한겨레 피드 생사 진단. 결과는 PLAN.md에 기록만 (수정 안 함).
2. **PLAN.md 작성** (레포 루트, 한국어). 머리에 "2026-06-10 스냅샷, 설계 정본은 ADR.md, 실물 검증은 Notion DB 06-04·06-09 페이지 기준" 명시. 완료 기준은 **[코드]**(레포 내 명령으로 판정) / **[운영]**(운영 환경 필요) 구분:

   **P0 — 실물에서 확인된 독자 노출 결함**
   - [x] 메가 트렌드 ID 누출 수정 (2026-06-10 완료): mega_trend(summary·key_threads)와 tldr에 `_clean_insight_ids` 적용 + '(서술, [N])' 꼴의 콤마째 제거 확장. [코드] 신규 `tests/test_megatrend_id_strip.py`(06-09 실물 누출 문자열 픽스처) 통과 + 기존 22케이스 통과
   - [~] 같은 날짜 재실행 중복 페이지 방지 — **보류 (2026-06-10 작성자 결정)**: cron은 하루 1회라 중복은 수동 재실행 시에만 발생하고, 그 경우 운영자가 인지하고 있어 수동 정리로 충분. 윈도우 고정(ADR-015) 이후엔 재실행 결과물도 동일해 코드 대응 ROI 낮음. 기존 중복(05-25 ×5 등)은 수동 정리 대상
   - [x] 한겨레 피드 진단·복구 (2026-06-10 완료): 로컬 rss_probe 실측으로 피드 정상(30건) 확인 — 원인은 URL 사망이 아니라 **RSS 엔트리에 날짜 필드 부재**(published/updated/dc:date 전무 → 전량 윈도우 필터 앞 폐기). 기사 페이지 메타에서 날짜를 추출하는 `_date_from_page` 폴백 구현(정오 KST 고정 부여로 재실행 결정론 유지, 받은 페이지는 본문으로 재사용). [코드] `tests/test_date_fallback.py` 5케이스 통과. [운영] ✓ 2026-06-11 1차 실행에서 한겨레 25건 수집 확인
   - [x] 실패·결손 가시화 (2026-06-10 완료): daily.yml을 종료코드 분기로 변경 — exit 1(수집 0건)은 green 유지, exit ≥2(분석·업로드 실패)는 red. `continue-on-error` 제거(백업은 if: always()가 보장). collectors에 매체별 수집 0건 경고 로그 추가. [운영] 실패 시 Actions red 확인

   **P1 — 품질·일관성**
   - [x] 제목 zero-width 문자(U+FEFF 등) 제거 (2026-06-10 완료): collectors에 `clean_feed_title`/`clean_feed_summary` 헬퍼 추출(U+FEFF·ZWSP 등 제거 포함), 테스트가 복제 로직 대신 실제 함수를 검증하도록 개선. [코드] `tests/test_entity_decode.py` 7케이스 통과
   - [x] MISC("기타") 비율 임계 경보 (2026-06-10 완료): `classifier.check_misc_ratio` — 기타 비율 30% 초과 시 경고 로그 (경고만, 실패 처리 안 함). [코드] `tests/test_misc_alert.py` 5케이스 통과
   - [x] README 현행화 (2026-06-10 완료): 연합뉴스 제거(피드에 없음), 분야 표→수집 매체 표(LLM 사후 분류 명시), 구성 트리 실제 구조 반영, 테스트 섹션 추가. [코드] 트리 항목 ↔ 실제 파일 대조 불일치 0건
   - [x] 테스트 일괄 실행 수단 + 테스트 CI (2026-06-10 완료): `run_tests.sh` + push 트리거 `.github/workflows/test.yml`. [코드] `bash run_tests.sh` exit 0. [운영] 푸시 후 Actions green 확인
   - [x] 루트 잔재 `daily.yml`·`report.md.j2` 제거 (2026-06-10 완료). [코드] 제거 후 전체 테스트 exit 0

   **P2 — 측정·보강 (ADR 보류 항목)**
   - [x] 날짜 간 중복 기사 측정 스크립트 (2026-06-10 완료): `dup_probe.py` — backups/ md에서 날짜별 URL 추출, 인접 날짜 재등장 빈도 보고. [코드] `tests/test_dup_probe.py` 4케이스 통과. [운영] 5일+ 백업 데이터로 `python dup_probe.py <backups 경로>` 실측 → 일평균 1건 이상이면 배제 구현 검토
   - [x] 핵심 경로 선별 테스트 (2026-06-10 완료): `tests/test_notion_blocks.py`(긴 텍스트 분할·인라인 스타일/링크·토글 children·콜아웃, 6케이스) + `tests/test_analyzer_validation.py`(검증 분기·환각 ID 필터, 8케이스) 통과
   - [x] deep_issue 출처 일치 지표 로깅 (2026-06-10 완료): `analyzer.source_coverage_stats` — deep_issue당 참조 ID 평균 + "참조≤1 장문" 점검 후보를 실행 로그에 출력. [코드] `tests/test_source_coverage.py` 3케이스 통과. [운영] 로그 관찰 후 비례 임계 도입 판단
   - [ ] **OpenAI 폴백 키(ADR-009, 사용자 결정) — 권고 격상**: 2026-06-11 Gemini 503으로 분석 실패(06-01에 이어 **2차 사고**, 재실행 유발). 키만 있었으면 GPT-5 mini 폴백으로 무중단. [운영] secrets에 `OPENAI_API_KEY` 등록 + workflow_dispatch 검증
   - [ ] Lost-in-the-Middle 측정(ADR-014). [운영 전용]
   - [x] 재실행 표본 보존 — 스냅샷 재개 (2026-06-11 완료): 수집+분류 완료 표본을 `backups/{date}_snapshot.json`으로 저장, 같은 날짜 재실행 시 분석부터 재개. daily.yml이 직전 실행 아티팩트에서 스냅샷 복원. 배경: 06-11 재실행에서 피드 top-N 갱신으로 314건→127건, 5개 매체 누락. [코드] `tests/test_snapshot.py` 5케이스 통과. [운영] 다음 재실행 상황에서 "스냅샷 재개" 로그 확인
   - [ ] 분야 간 deep_issue 중복 안전망 (2026-06-11 관찰 신규): 같은 사건(대통령 공직기강 발언)이 정치·사회 양쪽에 동일 출처로 심층 분석됨 — 프롬프트의 [경계] 지침을 LLM이 위반. 분야 간 referenced_article_ids 교집합 검출 → 경고 로그(1단계) 또는 한쪽 강등(2단계). [코드] 신규 테스트 통과
   - [x] Slack Incoming Webhook 알림 연동 (2026-06-24 완료): 브리핑 완료 시 tldr + 분야별 deep_issue 목록을 Slack으로 발송. 각 이슈 클릭 시 Notion 해당 분석 블록으로 딥링크 이동. 오류 시(종료 코드 2·3) 원인 포함 실패 알림 발송. SLACK_WEBHOOK_URL 미설정 시 자동 스킵. [코드] `slack_writer.py` 신규 + `notion_writer._fetch_issue_block_urls` + `daily.yml` 시크릿 추가

   **P3 — 장기/보류**: 임베딩 dedup, 분야별 동적 한도 고도화, 분류 경계 보정 (운영 데이터 후 ADR로 결정)

   **운영 관찰 기록 (2026-06-11 실행 로그·산출물)**
   - MISC 비율 13.7%(43/314) — 임계(30%) 미만. 머니투데이 종합 피드 교체는 보류, 계속 관찰
   - 출처 참조 지표 가동: deep_issue 15건 중 8건이 참조 1개 장문 — ADR-001 비례 임계 도입 판단 자료 누적 중
   - LLM 토큰 더듬기("영향을 미 미칠" 등 6회+) — 모델 생성 결함. 정규식 교정은 오탐 위험으로 보류, 빈도 관찰
   - 종료코드 분기(P0-4) 실전 검증: 분석 실패 → exit 2 → Actions red → 운영자 인지. 실패 알림 페이지 row는 수동 삭제 정책(P0-2 보류 결정과 일관)

## 수정 파일

- 신규: `PLAN.md` — 유일한 변경 파일. 코드 수정 없음 (P0 결함 수정은 PLAN.md 승인 후 별도 지시 대상).

## 검증

- 실행 1단계 실측(테스트/rss_probe)과 PLAN.md 본문 일치
- PLAN.md 요건: 우선순위 ✓ / 항목별 [코드]·[운영] 완료 기준 ✓ / 실물 결함은 페이지 날짜·위치 인용 ✓ / ADR 정본 선언 ✓
- push 후 원격 브랜치 반영 확인

---

# 2026-06-29 추가 계획 — 분류 부분실패 회복 (ADR-017)

## Context

적대적 재검토 + 독립 서브에이전트 대안 탐색에서 **분류 부분실패의 silent swallow + 스냅샷 박제** 확인. 배치 일부가 `[]`(실패) 반환 시 그 기사들이 genuine `기타`로 세탁되고, 스냅샷이 이를 박제해 같은 날짜 재실행이 회복 불가. 전면실패 시 빈 브리핑 영구 고착. 설계 정본·대안 비교는 **ADR-017**. 본 계획은 그 결정의 단계별 실행 항목(의존성·위험순; `미해결` 상태가 키스톤).

검증된 사실(코드 인용): 실패 배치 빈 리스트 `classifier.py:241-249` → 누락 폴백 강등 `classifier.py:181-186` → 무조건 스냅샷 저장 `main.py:88-95`(주석 `main.py:90-91`의 "전량 폴백 미저장"은 **구현 없음**) → 재실행 분류 스킵 `main.py:86`.

## 실행 단계

**Phase 0 — 키스톤 + 무위험 예방**
- [x] **0a. 미해결(unresolved) 상태 도입** [완료 2026-06-29 · tests/test_classify_unresolved.py] ★키스톤: 배치 `[]` 실패를 결정론적으로 식별해 genuine `기타`와 분리(silent swallow 제거). 동작 변화 최소(미해결도 분석 제외는 동일). [코드] 부분배치 실패 픽스처에서 미해결 N건이 `기타`와 분리 집계되는 테스트
- [x] **0b. 런 내 배치 재시도** [완료 2026-06-29 · tests/test_classify_retry.py]: 실패 배치만 1~2회 재시도 후 잔여만 미해결. [코드] 1차 실패→재시도 성공 시 미해결 0 테스트
- [x] **0c. (선택·인접) 분류 temperature 0.5→0** [완료 2026-06-29 · tests/test_classify_temperature.py]: `generate()`에 temperature 파라미터 신설. 분류만 temp=0(결정성), 분석은 기본 0.5 유지, OpenAI 폴백은 GPT-5 비기본 temperature 거부 가능성 때문에 미전달(폴백 불파손 우선). [코드] 배선 검증(분류 경로 temp 0 전달·fallback forward). 실 라벨 안정성은 [운영] 관찰.

**Phase 1 — 박제 방지 + 가시화 (저위험)**
- [x] **1a. 스냅샷 완전성 상태** [완료 2026-06-29 · tests/test_snapshot_completeness.py]: `complete: bool` + 기사별 `unresolved` 저장(`snapshot._FIELDS` 확장), 미해결 잔여 시 `complete=False`. 전면실패도 흡수(전량 미해결→재실행이 저장 기사 위에서 재분류, 재수집 불필요). 구버전 스냅샷 graceful. [코드] 미해결 포함 저장→복원 라운드트립 + `complete` 보존 테스트
- [x] **1b. 경보 격상** [완료 2026-06-29 · tests/test_slack_unresolved.py]: 미해결 존재/비율 이상 시 Slack·로그 가시화("미해결 N건 — 재실행 시 회복"), `check_misc_ratio`가 미해결 vs genuine MISC 구분 집계. [운영] 부분실패 상황에서 경보 수신 확인

**Phase 2 — 부분집합 자가치유 (Phase 1 직후, risk-gated)**
- [x] **2a. 미해결 부분집합만 재분류** [완료 2026-06-29 · tests/test_recover_unresolved.py]: 재실행 시 `complete=False` 스냅샷 로드 → **미해결만** 재분류(저장 기사 위, genuine MISC·해결분 **불가침**) → 병합 → 해결 시 `complete=True` 재저장. **Graceful(하드 요건)**: 재분류 또 실패 시 `기타` 폴백·무조건 진행. [코드] ① 미해결만 재분류·genuine MISC 불변 ② 치유 실패 graceful 진행 ③ 2회차 재실행 무변화(수렴) 테스트. [운영] 회복 로그 1회 확인

## 의존성 / 순서

```
0a 미해결 상태 ─┬─→ 0b 재시도(잔여 정의)
               ├─→ 1a 완전성 저장 ──→ 2a 부분집합 회복
               └─→ 1b 경보(미해결 구분)
```
0a 없이 1·2 불가. 2a는 유일 신규 경로라 1a 완료 + graceful 가드를 선행 조건으로 맨 뒤.

## 위험 요약

| 항목 | 위험 | 비고 |
|---|---|---|
| 0a 상태분리 | 거의 0 | 순수 타입 추가, 동작 불변 |
| 0b 재시도 | 낮음 | 그냥 재시도 |
| 0c temp=0 | 낮음 | 인접·선택 |
| 1a 완전성 저장 | 낮음 | 스키마 1필드 |
| 1b 경보 | 낮음 | 가시화만 |
| 2a 자가치유 | **중(유일)** | graceful 가드로 봉인 — 재실패 시 기타 폴백 |

## 권장 진행

- **Phase 0+1 한 PR** (키스톤+예방+가시화, 모두 저위험), **Phase 2 별도 PR**.

## 수정 파일 (예정)

- `classifier.py`(미해결 상태·배치 재시도·재분류 헬퍼), `collectors.Article`(상태 필드), `snapshot.py`(`_FIELDS`+`complete`), `main.py`(완전성 분기·런 간 회복 호출), `slack_writer.py`/`check_misc_ratio`(경보), `tests/`(각 단계 회귀). `llm.py`(분류 temp=0, 선택).