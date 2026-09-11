# Demand → IO Forecast 전달 계약 — 제안과 승인 이력

기준일: 2026-09-04. 상태: **ACCEPTED / 사용자 구현 승인 반영**.

사용자가 아래 제안의 구현을 승인했다. 현재 적용 계약은 [Handoff 구현 계약](IO_FORECAST_HANDOFF_CONTRACT.md), 결과는 [검증 기록](IO_FORECAST_HANDOFF_VERIFICATION.md)을 따른다. InventoryEngine 0.10.0과 Demand opt-in Export를 구현했으며 DB 게시·Runtime 배포는 하지 않았다.

**아래 1~8절은 0.9.1 기준으로 작성한 승인 전 제안의 원문 이력이다.** 원문의 “승인 대기/제안”은 당시 상태이며 현재 미결 항목으로 재해석하지 않는다. 실제 공유 Artifact/Receipt Port·Studio 운영 연결은 후속으로 남아 있다.

## 1. 현재 기준선 — 확인 완료

| 항목 | 코드로 확인한 사실 | 연결 시 주의점 |
|---|---|---|
| 대표 통계량 | Demand `STATISTIC_SOURCE_COLUMNS`: `POINT=yhat`, `P50=yhat_q50`, P10/P90 별도 | POINT를 평균 또는 P50로 재명명할 수 없다 |
| 모델 선정 | Demand의 현재 Winner 후보 조회와 Studio Report의 기본 결과 조회는 POINT 기준 | IO가 후보 모델을 다시 선정하거나 통계량을 합산하지 않는다 |
| Forecast Handoff | 현재 `ForecastArtifactHandoff`는 Forecast → Postprocess의 논리 Artifact 참조·Runtime Binding | IO에 전달할 최종 출력 행의 봉인 계약과 다르다 |
| 선정 근거 | `WeeklyWinnerSelectionBatch`는 선정 행과 비교 행의 논리 Hash를 제공 | 이를 참조하되 선정 Manifest를 Forecast 출력 Hash로 대신 쓰지 않는다 |
| 수량 | IO 0.9.1은 EA `scale=0`, `tolerance=0`; Forecast도 같은 규칙 적용 | 실제 코드에 소수 Forecast를 넣으면 `UOM_QUANTITY_PRECISION` 발생 |
| 월 귀속 | Demand `week_to_month`는 ISO 주의 월요일 소속 월 | Legacy IO의 수요일 소속 월과 월 경계에서 다르다 |
| 크기 | 현재 IO Snapshot당 최대 100,000행·파일 8MB, 전체 JSON 파싱에도 8MB 제한 | 전체 Site·장기 Horizon을 작은 JSON 예제와 동일하게 처리할 수 없다 |

관찰 근거:

- [Demand 통계량 매핑](../../../DemandEngine/src/forecast_demand/application/use_cases/weekly_candidate_normalization.py)
- [Demand POINT 후보 조회](../../../DemandEngine/src/forecast_demand/adapters/postgresql/weekly_performance_runtime.py)
- [Demand Winner 선정·Hash](../../../DemandEngine/src/forecast_demand/application/use_cases/weekly_winner_selection.py)
- [현재 내부 Handoff](../../../DemandEngine/src/run_demand/artifact_handoff.py)
- [Demand 월 귀속](../../../DemandEngine/src/forecast_demand/domain/weekly_forecast_contracts.py)
- [Studio 결과 조회](../../../dsai-platform/backend/platform_api/demand_engine_report_repository.py)
- [IO 수량 검증](../../src/dsio_inventory_engine/prepare_inventory/application/validation.py)
- [IO 현재 Source 계약](IO_SOURCE_READ_CONTRACT.md)

2026-09-03의 실제 DB 진단은 P50 4,270품목·3주에 한정된다. 이번에 POINT의 실제 행 수·Coverage를 DB에서 확인한 것은 아니다. 과거 Master와 현재 Master의 동일성을 주장하지 않는다.

## 2. 제안 방향과 대안

### Goal — 검증된 Forecast를 같은 Calendar/Master와 함께 전달

성공한 Demand Run의 최종 선정 결과를 원본 의미 그대로 봉인하고, Studio가 정확한 ID/Hash를 IO에 전달한다. 실제 주문·재고·공급의 정수성과 Forecast의 소수 계획값을 구분한다.

### Strategy — 소유권을 유지한 별도 Export 경계

**권장안 A:** Demand 소유의 명시적 Forecast Export UseCase를 추가한다. Studio는 Export 완료 Receipt와 Cycle Binding을 관리하고 IO는 불변 교환 계약만 소비한다. 초기에는 명시적 opt-in 호출로 연결하며 기존 Demand 계산·Winner 선정·성공 판정 API는 자동으로 바꾸지 않는다.

**대안 B:** IO가 성공 Run DB를 읽어 자체 Snapshot을 만들어 사용한다. 구현 경로는 짧지만 Source 승인·선정·봉인의 소유권이 IO로 이동하고 사후 Master 재구성 문제가 생긴다. 이번 요구의 상위 봉인·공유 Snapshot 원칙에 맞지 않아 권장하지 않는다.

### Impact — 실제로 바뀌는 경계

- DemandEngine `demand_engine_v3`: 기존 Forecast/선정 로직은 유지하고, 선정 결과·근거를 받아 불변 Export를 만드는 UseCase/Port/Adapter 및 테스트 추가가 필요하다.
- dsai-platform `develop`: 성공 Run과 검증된 Export Receipt를 Cycle에 고정하는 연결이 필요하다. 기존 Report의 최신 결과 비교 로직을 IO 입력 선택에 재사용하지 않는다.
- InventoryEngine: 계획 수량과 물리 수량을 구분하는 **버전이 명시된 계약 확장**이 필요하다. 기존 v1 정수 EA 계약과 Golden을 덮어쓰지 않는다.
- 대형 Snapshot: 논리 Snapshot과 운반 파일을 분리하는 Manifest/Parquet Part 계약 및 Reader가 필요하다. 단순히 파일만 분할하고 전체를 8MB JSON으로 다시 합치는 방식은 해결책이 아니다.

### Trade-offs — 보존하는 의미와 늘어나는 구현

원본 소수를 유지하면 수요 총량과 시점이 보존되지만 예상 PSI 잔량이 소수가 될 수 있다. 따라서 예상 잔량을 ERP의 물리 재고로 표시하면 안 된다. 정수화하면 현재 IO 계약은 유지하기 쉽지만 수요가 사라지거나 과대 계상될 수 있다. 별도 Export·Receipt는 구현량을 늘리지만 Source 소유권과 재시도 재현성을 명확하게 한다.

## 3. 대표 통계량 — POINT 권장, 자동 대체 금지

- V1 기본 전달은 **Demand가 선정한 Winner의 `forecast_stat_cd=POINT`**로 제안한다. 모델 고유의 point forecast이며 기대평균이라는 보장은 별도로 하지 않는다.
- `selection_decision_id`, 선정 근거 Hash, 선택된 `model_run_id`, 원본 `result_id`, Demand Run ID를 보존한다. 기존 선정 결과를 따르고 IO에서 재순위화하지 않는다.
- P50/P10/P90은 같은 선정 모델·Run의 별도 분석/민감도 입력으로 구분한다. POINT가 없다고 P50나 최신 Run으로 자동 대체하지 않는다.
- 품목×주차×Scope×Scenario별 중복·누락을 검사한다. 서로 다른 모델이나 통계량의 행을 합산하지 않는다.
- 전달 Master의 active/stock-managed Universe에 Forecast가 부족하면 차단한다. Forecast에 맞춰 Master를 조용히 줄이거나 누락을 0으로 생성하지 않는다. 업무적으로 승인된 Universe 변경은 새 Revision으로 처리한다.

## 4. 소수 EA — 계획값 보존, 물리량 정수성 분리 권장

| 수량 구분 | 제안 검증 |
|---|---|
| 원본 Forecast | Source Decimal 문자열·통계량 보존. 무조건 올림/반올림/절사 금지 |
| 계획 Forecast·예상 PSI | 최대 소수 6자리의 명시적 계획 정밀도. 초과 정밀도는 자동 절사하지 않고 계약 오류 |
| 실제 BOH·Reserved·확정 주문·확약 입고 | EA는 정수·대사 허용오차 0 유지 |
| 파생 SS·ROP·목표/필요 보충량 | 계획 정밀도로 계산; 물리 발주량과 분리 |
| 최종 권고 발주·신규 권고 입고 | EA 정수, MOQ·발주배수·Hard Constraint를 공통 행동 검증기에서 만족해야 수락 |

UOM을 `EA_PLAN` 등으로 임의 변경해 기존 검증을 우회하지 않는다. 계약에서 `planning_scale`과 `physical_scale`/실행 단위를 분리하는 방식이 필요하다. 필드명·Schema 버전은 구현 승인 후 확정한다. 승인 전에는 기존 EA 제약을 그대로 유지한다.

수작업 반례: 주간 Forecast `0.4 EA`가 13주이면 원본 합계는 `5.2 EA`다. 주별 반올림은 0, 주별 올림은 13이다. 합계 보존 배분도 개별 품절 주차를 이동시킬 수 있어 기본값으로 제안하지 않는다. 소수 예상 잔량·Forecast Shortage는 계획 결과이며, 실제 Cut-off EOH–BOH 대사에는 사용하지 않는다.

## 5. 공유 Calendar/Master — 시작 전에 고정

1. Studio가 Plan Version의 기준일·기간·Site Universe를 확정한다.
2. 동일 Cycle의 Calendar/Master Snapshot을 **Demand 실행 전에** 봉인한다. Demand와 IO는 같은 Cycle/Site 투영의 ID/Hash를 사용한다. 회사 전체 Master를 Site별로 나누면 부모 Revision과 투영 Hash를 모두 추적한다.
3. Demand 실행 후 다른 시점의 현재 Master를 조회해 과거 `master_as_of_date`를 붙이지 않는다. 공유 Binding이 없는 과거 Run은 IO 운영 입력으로 자동 승격하지 않고 회귀/개발 진단으로 남긴다.
4. IO W0는 전달 Calendar의 `fcst_w0_yyyyww`에 맞춘다. Demand의 `plan_yyyyww`가 계획 버전 주차로 다르게 쓰인 경우 원본 필드로 보존하고 IO W0와 혼동하지 않는다.
5. Calendar 날짜·주차는 Source 행을 사용한다. IO가 YYYYWW 문자열만으로 다시 계산하지 않는다.

월 귀속은 **현재 Demand와 일치하는 원본 주 시작일(월요일) 소속 월**을 목표 기본값으로 제안한다. Snapshot에 `base_month`와 월 귀속 규칙 Revision을 포함하고 Plan 경계에서 Bucket 날짜가 잘려도 원본 주 기준을 보존한다. Legacy 수요일 귀속은 회귀 Adapter에서만 비교한다. 예를 들어 `202627`은 Demand 월요일 기준 `202606`, Legacy 수요일 기준 `202607`이다. 이는 아직 승인되지 않은 차이이며 소리 없이 적용하지 않는다.

## 6. 출력 봉인과 전달 완료

권장 흐름은 다음과 같다. 아래 상태명은 설계 설명이며 기존 API에 추가된 상태가 아니다.

```text
Shared Calendar/Master sealed before Demand
    -> Demand succeeds with verified selection evidence
    -> Explicit export of pinned Winner POINT rows
    -> Validate scope, coverage, quantities and row hashes
    -> Write immutable artifact parts and seal manifest
    -> Verify artifact receipt and publish handoff binding
    -> Studio marks handoff eligible and pins Cycle/Site reference
    -> IO consumes exact Run/Snapshot ID/Hash
```

- 기존 Demand 성공 상태만으로 IO를 시작하지 않는다. 별도 Export/Receipt가 실패하면 Handoff는 미완료이고 IO는 시작하지 않는다. 기존 성공 Demand Run을 임의로 실패 상태로 바꾸지 않는다.
- 원본 Snapshot Hash, Manifest Hash, 파일별 Byte Hash, Canonical Mapping/수량 규칙 Revision을 구분한다. 선정 Hash나 `input_manifest_sha256`는 출력 수량 Hash의 대체물이 아니다.
- Export 멱등 키는 Demand Run·정확한 Selector·선정 근거 Hash·Calendar/Master Binding·Export 규칙 Revision을 포함한다. 동일 키 재시도는 같은 봉인 결과를 반환하며 다른 내용으로 덮어쓰지 않는다. 새 기준일·수량 규칙은 새 Revision이다.
- Artifact 선봉인 후 짧은 DB Transaction으로 Binding/Receipt를 게시한다. 게시 실패 시 Artifact는 유지하고 같은 키로 복구한다. Hash 자체를 작성자 인증·승인 권한으로 해석하지 않는다.
- Part Manifest는 파일별 Schema·정렬/품목 범위·행수·Byte Hash와 전체 논리 Content Hash를 포함한다. Part 누락·중복·순서 의존성·범위 겹침을 검증한다. Parquet Byte Hash와 정규화 행 Hash는 다를 수 있다.
- 기존 IO `SourceSnapshot` 100,000행·8MB 한도는 유지한다. 4,270품목×26주만 해도 111,020행이므로 실 Site 전체 연결 완료 조건에는 대형 Snapshot Reader/계약 검증을 포함해야 한다. 행 자르기·Horizon 축소로 통과시키지 않는다.

구체적인 새 DB Table/DDL은 이 문서에서 추가 결정하지 않는다. 기존 Run Artifact/Binding 재사용 가능성을 확인하고, 실제 Migration·게시·E2E DB Write는 별도 승인받는다. 보류된 P0-14/18/19를 이번 계약 제안으로 확정하지 않는다.

## 7. 검증한 것과 구현 후 합격 조건

현재 코드 관찰은 [검증 요약](evidence/forecast-handoff-characterization-20260904.json)에 남겼다. POINT/P50 매핑, 현재 EA 소수 거부, 월 경계 차이를 로컬 Python으로 확인했다. 반올림 수량과 26주 행 수는 수작업 예시 계산이며 실제 운영 측정이 아니다. 생산 코드·DB·배포는 변경하지 않았다.

구현 승인 후 검증할 항목:

- POINT 누락/P50 존재에도 자동 대체하지 않음, 정확한 Winner/Scope/Run만 포함
- 원본 소수·총량 보존, 계획 PSI 소수 계산, 물리 EA/권고 Lot 위반 거부
- 월/연 경계·부분 주차에서 공유 Calendar와 동일한 매핑
- 요청 Hash·Master/Calendar Revision·선정 근거 불일치 차단
- Part 누락/변조/중복·100,000행 초과 정상 Dataset·동일 입력 결정론성
- 봉인/게시 사이 실패·멱등 복구·미완료 Handoff의 IO 시작 차단
- 기존 정수 Canonical Golden 및 모든 전략의 공통 행동 계약 유지

## 8. 작업 순서와 승인 대상

**현재 기준선 — 완료**

- InventoryEngine 0.9.1 Reader는 유지했다. DemandEngine `demand_engine_v3`와 Studio `develop`의 관련 코드 및 위 의미 차이를 확인했다.

**제안 승인 — 승인 필요**

- Demand 소유 Export/Studio Binding, 기본 POINT, 소수 계획값과 물리 정수 분리, 월요일 월 귀속을 묶어 승인받는다. 특히 EA 규칙은 IO 현행 계약 변경이다.

**계약·오프라인 구현 — 승인 후 다음 작업**

- 먼저 Versioned Manifest/수량/공유 Binding 계약을 고정한다. 그다음 Demand Export와 IO Reader/계획 정밀도 경계를 구현하고 독립 Fixture로 검증한다.
- 계약이 고정되면 Producer/Consumer의 별도 파일 구현은 병렬 가능하다. 공통 계약 변경과 동일 Handoff Golden 검증은 직렬이다. 이번에 다른 세션/Agent를 실행하지 않았다.

**Studio 연결·실제 통합 — 다음 작업 / 승인 필요**

- 독립 테스트 이후 Studio의 실행 연결과 실제 Source 준비 여부를 검증한다. 기존 Source/권한으로 읽기 대조만 가능한 범위를 우선 수행한다.
- 지정 PostgreSQL Migration·Artifact/DB 게시·실제 E2E Write·공용 Runtime 배포는 대상별 승인이 필요하다. 이전 Network 승인으로 확대하지 않는다.
- InventoryEngine은 Git 미초기화다. DemandEngine/플랫폼의 기존 변경은 보존하고 이번에는 Commit/Push/MR을 수행하지 않는다.
