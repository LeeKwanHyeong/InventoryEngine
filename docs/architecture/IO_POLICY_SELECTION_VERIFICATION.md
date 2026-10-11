# 품목별 정책 선택 로컬 검증 기록

기준일: 2026-10-06. [선택 계약](IO_POLICY_SELECTION_CONTRACT.md)의 구현·검증 기록이다.
정책 탐색의 [직전 검증 기록](IO_POLICY_SEARCH_VERIFICATION.md)은 당시 수치로 그대로 보존한다.

## 1. 기준선과 변경 범위

- 저장소: `/Users/igwanhyeong/PycharmProjects/InventoryEngine`.
- 브랜치: `inventory_engine_dev`, 변경 전/현재 HEAD `53869a9a05d04d5ca7174e998f75e54a9df63a50`.
- 변경 상태: 로컬 미커밋. 기존 정책 탐색·Source Producer·개인 보고서 변경을 보존했다.
- 이번 추가: 독립 선택 UseCase, 선택 계약·JSON Schema, Append-only 선택 저장·Replay, Offline CLI와 테스트.
- 이번에 dsai-platform/DemandEngine 코드를 변경하지 않았다. Migration·DB 접속/Write·공용 Runtime
  배포·Commit·Push도 수행하지 않았다. 새 테이블은 추가하지 않았다.

기존 탐색의 Hash Verifier를 공통 계약 모듈로 이동하고, 로컬 Append-only Writer를 검색/선택
Artifact가 공유하도록 분리했다. 기존 PSR 계약 Version·Hash·`selected_policies=null`은 유지한다.
이는 저장과 순수 계산의 책임을 분리하기 위한 변경이며 Platform Repository를 import하지 않는다.

## 2. 수작업 선정 Golden

과거 13주 수요 10 EA/주, BOH 10 EA, Lead Time 7일, 미래 수요 10/10/10 EA,
RMOS=0·EMOS=0.3, 보관비 1·발주 고정비 5·발주 변동비 2의 기존 개발 가정을 사용한다.

| 조건 | 실제 선정 | 검증 내용 |
|---|---|---|
| 최소 충족률 0.95 | PERIODIC_ORDER_UP_TO | 고정 Q 비용 34가 더 싸도 23/30 충족으로 탈락. 주기 정책은 30/30·비용 62·종료 BO 0 |
| 최소 충족률 0.7 | ROP_FIXED_Q | 두 후보 서비스 통과 후 34 < 62로 선정 |
| 비용 가정을 모두 명시적 0, 최소 0.7 | ROP_FIXED_Q | 평균 주말 재고 1 < 2로 비용 동점 해소 |
| 미래 수요 10/10/13, 비용 모두 0, 최소 0.5 | ROP_FIXED_Q | 두 후보 평균재고 1·비용 0으로 같을 때 주문 1건 < 2건으로 선정 |
| BOH 100, 미래 10/10/10 | Candidate ID가 작은 후보 | 모든 지표 동점. 입력 후보 순서를 바꿔도 품목별 선정 동일 |

추가로 비용 소수 25자리 차이를 그대로 비교하고, 호출자의 Decimal precision/rounding/trap이
결과를 바꾸지 않는지 검증했다. 비용 우선순위를 가중 합·epsilon으로 대체하지 않았다.

기본 Golden의 Source와 선택 결과 Hash:

```text
PSR-5e1808cb4bde77ee48feb065679065614f3513bcb72326a3882d3d59cd45fc75
PSEL-1dc99a92df87938682b845042f4d48f9a23ae731bf90f7ba97583736b4a9ae17
```

선정 Candidate ID:
`PC-5103987b40d6a0bd8a104817b81d57826fd0fb822a905cacdb3a965e7b143799`.
Source REVIEW를 보존하며 자동 게시·발주·운영 승인·DB Write는 모두 false다.
이 작은 Golden의 선정은 장기 최적성이나 실제 서비스 달성의 증명이 아니다.

## 3. 품목별 미선정·실패 격리

- 두 품목이 같은 Source/Config를 공유해도 각자의 재고·후보·선정 포인터를 사용한다.
- 한 품목의 미래 수요가 0이면 그 품목만 `NO_DEMAND_IN_HORIZON`으로 미선정하고 다른 품목은 유지한다.
- 비용 미연결은 null/미선정이며 명시적 0 비용과 구분한다.
- 실제 고객 주문의 종료 Backorder가 남으면 최소 충족률이 0이어도 미선정한다.
- FSN BLOCK은 재고가 충분해도 미선정한다. REVIEW는 개발 비교만 허용한다.
- 일부 후보의 목표 상한 실패가 적격 후보의 선정을 방해하지 않는다. 전 후보 실패는 별도 사유를 보존한다.
- 이력 부족·승인 Override 고정은 원 탐색 제외 사유를 유지한다. 평가 없음에도 후보를 새로 만들어 선택하지 않는다.

## 4. Artifact·계약 방어

- Hash만 새로 계산한 허위 서비스/비용 지표, PSI EOH 변경, 중복/고아/부분 후보 집합,
  V2 Gate Version 변경을 선택 진입에서 거부한다.
- Python 계약과 JSON Schema 양쪽에서 빈 품목 결과, 잘못된 선택/null/순위 상태,
  추가 Key, Review 누락, 운영 게시/승인 true를 거부한다. bool을 품목 수 integer로 인정하지 않는다.
- Source가 먼저 없으면 선택 Writer가 거부한다. 동일 Hash 저장은 멱등이며 기존 파일을 덮어쓰지 않는다.
- 선택 사유 변경 후 재봉인한 파일은 Reader/Writer의 원본 선택 Replay에서 거부한다.
- 더 비싼 후보로 ID·Hash·정책·점수·순위를 일관되게 바꾸고 재봉인해도 원본 Replay로 거부한다.
  구조/Hash 검증만으로 최적성을 판단하지 않는다.
- 원본 PSR 파일 변조는 PSEL 읽기에도 실패를 전파한다. CLI 파일 입력과 원본 Hash 입력이
  같은 저장 결과를 만든다. DB 없이 개발 Company Scope와 Reference 필수 조건을 확인했다.

이는 내부 정합성 검증이며 ERP 원천의 정확성·Source 승인 권한을 검증한 것은 아니다.
실제 Run Claim·DB Receipt·권위 Source 인증은 아직 연결하지 않았다.

## 5. 재현 방법과 환경

| 검증 | 결과 | 범위 |
|---|---|---|
| 전체 Unit·Contract·Integration | 679 passed, 22 skipped, 631 subtests passed | 최종 재실행 95.92초. 실제 DB E2E 아님 |
| 신규 선택기 집중 회귀 | 43 passed | 품목별 선정·동점·미선정·원본 Replay·CLI·Schema |
| Ruff | 통과 | `src`, `tests` 전체 |
| Format | 통과, 213개 파일 | 기존 무관한 파일을 자동 재작성하지 않음 |
| JSON Schema | 26개 유효 | 개발 계획 JSON 구문과 성공/미선정 결과 검증 포함 |
| Diff whitespace·관련 문서 Link | 통과 | Commit 수행을 뜻하지 않음 |

실행 환경은 기존 dsai-platform `backend/.venv` Python 3.12.10이다. 설치 의존성과
기존 환경을 변경하지 않았다. Optional Source/DB 조건부 테스트의 Skip은 실제 통합 통과를 뜻하지 않는다.

```text
# cwd: /Users/igwanhyeong/PycharmProjects/InventoryEngine
# PYTHONDONTWRITEBYTECODE=1, PYTHONPATH=.:src:tests:tests/support
pytest -p no:cacheprovider -q tests/unit tests/contract tests/integration
pytest -p no:cacheprovider -q tests/unit/test_policy_selection.py tests/integration/test_policy_selection_offline.py
ruff check --no-cache src tests
ruff format --check --no-cache src tests
git diff --check
```

## 6. 후속 작업과 승인 경계

1. **장기 평가·비용 민감도 — 다음 작업 · InventoryEngine**
   짧은 Horizon·종료 잔존가치 없음의 영향을 검증하고 서비스·정책 순위·발주 지연 변화를 기록한다.
2. **Run/결과 조회 연결 — 다음 작업 · dsai-platform Backend/Frontend, InventoryEngine**
   Config 2.1.0·분류·탐색·선택 Hash를 실제 Run에 고정하고 품목별 선정/미선정 근거를 조회한다.
   평가와 API/화면 설계는 병렬 가능하지만 Run 통합은 계약 테스트 이후 직렬 진행한다.
3. **개발 배포·실제 DB E2E — 승인 필요 · 개발 PostgreSQL·dsai-platform 개발 서버·공용 Inventory Runtime**
   대상과 Write 범위를 별도로 승인받는다. 기존 Migration 074~077을 재적용 대상으로 삼지 않는다.
4. **변경 고정 — 다음 작업 · InventoryEngine `inventory_engine_dev`, dsai-platform `codex/inventory-psi-strategy-v2`**
   기존 다른 작업·개인 산출물과 구분한 뒤 독립 Commit·Push 범위를 정한다. 이번 요청에서 실행하지 않았다.
