# 실제 Source 읽기·Canonical 변환 계약

JSON Source v1 기준: InventoryEngine 0.9.1(2026-09-03), 0.10.0에서도 호환 유지. **읽기/변환 구현과 운영 Source 준비는 별개**다. 2026-09-04 추가한 Demand Export/Parquet·Canonical v2는 [별도 Handoff 계약](IO_FORECAST_HANDOFF_CONTRACT.md)을 따른다. DB·Neo4j 변경은 하지 않았다.

## 1. 구현 경계

- `SourceInputRequest`: 기존 Canonical Context·수량 규칙과 8개 Source Snapshot ID/Hash를 고정한다. 최신 Run/Snapshot 검색은 없다.
- `SourceSnapshot`: 원본 행·Scope·기준일·의미·출처·상태를 함께 Hash한다. 입력 행 순서가 달라도 Hash는 같다. 한 Dataset 최대 100,000행, 파일 최대 8MB다.
- `FileSourceSnapshotReader`: 운영자가 지정한 로컬 폴더에서 정확한 ID의 JSON만 읽는다. 경로 탈출·심볼릭 링크·다른 Snapshot ID·누락·크기 초과를 거부한다. URL/DB 주소는 Snapshot에서 받지 않는다.
- `PostgresInventorySourceReader`: `REPEATABLE READ READ ONLY`와 10초 Statement Timeout, 값 Parameter Binding, 행 상한을 사용한다. Master/Calendar 현재 조회는 준비상태 진단이며 과거 기준일 Snapshot이 아니다.
- `prepare_source`: 먼저 8개 로컬 Source 전체를 검증하고 Canonical 입력을 준비한다. `--read-postgres`인 경우에만 **검증된 메모리 Snapshot**을 `PostgresInventorySourceReader.verify_snapshot`으로 지정 Run의 DB 행과 대조한다. 원래 ID/Hash/Row Count를 유지하므로 값이 달라지면 실패한다. 현재 조회 결과로 새 Hash를 발급해 봉인을 대체하지 않는다.
- `PrepareSourceInputUseCase`: Source→Canonical 매핑 후 기존 Cut-off/Calendar/고아/수량/EOH–BOH 검증기를 호출한다. `SOURCE_INPUT_PREPARED_LOCALLY`는 Run 성공이나 저장 완료가 아니다.

이 JSON은 **새 IO Consumer 교환 계약**이다. 현재 DemandEngine이 이 형식의 Forecast Snapshot을 이미 게시한다고 주장하지 않는다. 신뢰할 수 있는 상위 수집·검증·봉인 주체가 제공해야 하며, Hash 일치 자체는 작성자 인증·업무 승인·ERP 완결성 증명이 아니다. `SEALED`를 직접 만든 테스트 패키지는 개발 Fixture일 뿐이다.

0.9.1부터 요청 Hash·Demand Run·Scope·출처·봉인·Coverage·정책·Cut-off·EOH–BOH 검증 중 하나라도 실패하면 **DB 연결과 대조 조회를 시작하지 않는다**. 검증 후 파일을 다시 읽지 않으며 DB 대조 성공 시 같은 Canonical 결과를 반환한다. 0.9.0의 내부 복합 Reader는 제거했다. CLI/교환 DTO/Hash/PSI 규칙은 바뀌지 않았다. 저수준 `read_forecast_rows`·`inspect_site`는 별도의 읽기 진단 API이므로 전체 Canonical 검증이나 봉인 완료를 의미하지 않는다.

## 2. 매핑 범위

| Adapter | 원천 → Canonical | 방어 규칙 |
|---|---|---|
| `CANONICAL_V1` | 기존 8개 Snapshot의 행 구조 | 합성 Generator·기존 Golden 포함. Metadata/계보/봉인/수량 검증을 생략하지 않는다 |
| `DSDM_MASTER_V1` | `OPER_PART_NO→item_id`, `USE_FLAG/STOCK_FLAG→active/stock_managed`, `uom=EA` | Company/Subs/Site를 보존한다. V1 수량 단위는 `EA`로 고정하며 중복·비활성 품목을 조용히 합치거나 제거하지 않는다 |
| `DSDM_FORECAST_V1` | `OPER_PART_NO`, `FCST_YYYYWW`, `FCST_QTY` → 품목·주차·예측수량 | 정확한 Run·Plan·Plant·Site·Scenario·통계량·주차 범위. 고객 주문으로 변환하지 않는다. 같은 주차 소비는 명시한 Source 의미/참조 근거에 따른다 |
| `ERP_POSITION_V1` | `boh_qty→on_hand_qty`, Reserved/Available/Backorder 명시 | BOH가 물리 On-hand임을 확인한 상위 Export만 허용. `BASE_DT/PLAN_ID`에서 날짜·Site·Reserved를 추측하지 않는다 |
| `UNVERIFIED_DUE_IN_V1` | 원본 Receipt ID·품목·수량·예정일 → `UNVERIFIED_DUE_IN` | Baseline 제외. 원본과 `VENDOR_COMMITMENT_NOT_VERIFIED` 사유 보존. 확약 상태 승격 기능 없음 |
| `SOURCE_POLICY_V1` | `MIN_PO_QTY`, `PO_LOT_QTY`, `STOCK_LT`, `SVC_LV`, `MAX_QTY`, `ROP_QTY` | Lead Time DAY/WEEK, Service Level FRACTION/PERCENT와 승인 참조를 명시. MAX_QTY는 Legacy 목표재고 비교값, 물리 한도는 별도. NULL 임의 보정 없음 |

`ERP_POSITION_V1`/`UNVERIFIED_DUE_IN_V1`/`SOURCE_POLICY_V1`은 **검증된 Export 행의 변환 계약**이다. 현재 개발 DB에 없는 Legacy 테이블을 조회하는 SQL이나 ERP Collector를 구현한 것은 아니다. V1 UOM은 Master Source의 `UNIT`을 해석하지 않고 합의된 `EA`를 사용한다. 불분명한 Source 컬럼은 상위 Export 계약이 확인되기 전에는 공급 확약/0/기본 정책으로 변환하지 않는다.

## 3. Forecast 식별과 Hash

DB 확인 경로는 `dsai.engine_runtime_runs` → `dsdm.tb_fcst_model_run` → `dsdm.tb_sum_fcst_weekly_res`다. Parent Run/Model의 성공 상태와 Scope를 확인한다. Tenant·Project는 요청에 명시되며 실행 계층에서 신뢰할 수 있게 전달해야 한다. 이 라이브러리는 사용자 인증 API가 아니다.

Forecast Selector에는 다음이 필수다.

- `tenant_id`, `project_id`, `demand_run_id`(UUID)
- `company_cd`, `subs_cd`, `plant_cd`, `site_cd`, `plan_id`
- `plan_yyyyww`, `fcst_w0_yyyyww`, `forecast_from_yyyyww`, `forecast_to_yyyyww`
- `target_cd`, `bukt_cd`, `snrio_id`, `snrio_grp`, `regul_type`, `forecast_stat_cd`

`plan_yyyyww`와 Forecast 시작 주차를 임의로 같은 의미로 해석하지 않는다. IO W0는 `fcst_w0_yyyyww`와 읽기 시작 주차에 일치해야 한다. Calendar 전체 Coverage는 기존 Admission에서 검증한다. 통계량별 행을 합산하거나 최신 모델을 골라서 누락을 채우지 않는다. DB 결과의 `input_manifest_sha256`는 Demand **입력** Hash이지 IO로 전달할 Forecast **출력 Snapshot** Hash가 아니다.

Source Hash와 Canonical Hash는 별도다. Source의 Calendar/Master/Prior 연결 Hash를 먼저 확인한 뒤, Canonical 표현의 Hash로 참조를 변환한다. `source_bindings_hash`와 `canonical_input_hash`를 모두 후속 Run Evidence에 연결해야 한다. 원본 표현·근거가 달라도 수치가 같을 수 있으므로 Canonical Hash 하나로 원본 출처까지 식별하지 않는다. 같은 Source Binding과 Context에서 기술적 Retry의 새 `engine_run_id`만 바뀌면 Canonical Hash는 유지된다.

## 4. 시점·출처·미확인 공급

- `ACTUAL_SOURCE`/`SYNTHETIC_SOURCE`/`UNKNOWN_SOURCE`/`DEVELOPMENT_FIXTURE`를 구분한다. 실제 DB에 저장됐다는 이유로 실제 운영 관측값으로 분류하지 않는다.
- `UNKNOWN_SOURCE`는 준비 진단에서 보존하지만 계산 Admission은 차단한다. Fixture/합성 Source는 개발 환경 전용이다.
- Master/정책의 `source_as_of_date`는 Planning Cycle의 `master_as_of_date`와 일치해야 한다. BOH는 Plan W0, Prior는 전기 EOH 기준일과 일치한다. 이 선언의 신뢰성은 상위 Snapshot 수집·봉인 절차에서 확보한다.
- Actual BOH는 Watermark·Cut-off·Reserved·전기 EOH·조정/이벤트를 모두 전달한다. 자료가 없으면 빈 목록이나 0을 만들지 않는다. 기존 Late Posting/EOH–BOH/고아/미봉인 차단을 그대로 적용한다.
- TGSM 재고가 없으면 기존 독립 52주 Generator의 `SYNTHETIC_BOH`와 같은 Simulation Run의 Prior/입고예정을 전달할 수 있다. `MAX_QTY`로 BOH를 대체하지 않는다. 실제 ERP Cut-off를 검증했다는 의미는 아니다.
- 미확인 Due-in은 원본 수량·예정일·제외 사유를 반환한다. DB Evidence 저장·민감도 Scenario 실행·Legacy 재분류는 이번 Reader의 역할이 아니다.

## 5. 현재 실제 Source 준비상태

[읽기 전용 증적](evidence/source-readonly-verification-20260903.json)은 **특정 Run/V100/3주/P50 진단**이다. P50를 운영 기본값으로 결정한 기록이 아니다.

| 확인 항목 | 2026-09-03 확인 결과 | 다음 연결 조건 |
|---|---|---|
| Forecast | 지정 Run의 4,270품목×3주=12,810행, 두 번 조회 Hash 일치 | 검증된 출력 Snapshot ID/Hash/봉인 Artifact·Scope를 Demand/Studio에서 전달 |
| 실제 재고·입고예정 | 조회 권한 범위에서 Legacy BOH/Due-in/BO/Plan/정책 테이블 미확인 | Source 위치·권한·Export 담당과 Cut-off/Watermark/Reserved/주문 Coverage 확정. 개발은 합성 입력 사용 가능 |
| 정책 | V100 현재 Master 7,000행에서 MOQ·발주배수·STOCK_LT·Service Level 모두 NULL | 승인된 Source 제약/목표 또는 명시적 합성 정책 필요 |
| 수량 | 진단한 Forecast 9,213행은 소수 EA | JSON v1은 정수 EA 유지. 승인된 Canonical v2에서 계획 소수와 물리 정수를 분리하며 임의 반올림하지 않음 |
| Master/Calendar | 현재 Master·`dsdm.calendar_week` 존재 | Plan 기준일 Master Revision과 공유 Calendar Snapshot 필요. Calendar에 `base_month` 없음; 임의 월 귀속 생성 금지 |
| 고아 | 진단 범위 Forecast의 현재 Master 고아 0건 | 전체 active/stock-managed Universe의 Forecast 완결성은 별도. 고아 0이 입력 준비 완료는 아님 |

## 6. 실제 재고관리 품목 범위 연결

2026-09-07에 개발 PostgreSQL과 기존 Demand Artifact를 모두 읽기 전용으로 대조했다.

- Scope: `DSE/C100/V100`
- `dsdm.tb_mst_oper_part`: 7,000개
- `USE_FLAG='Y' AND STOCK_FLAG='Y'`: 7,000개
- 기존 Demand Forecast Target: 2,579개
- 재고관리 대상이지만 Forecast가 없는 품목: 4,421개
- 재고관리 대상이 아닌 Forecast 품목: 0개
- 판정: `VERIFIED_WITH_FORECAST_GAPS`, UOM 규칙 `FIXED_EA_V1`

Demand/Studio는 이 차이를 Run-local `master-universe-evidence.json`과 Handoff 집계 필드로 보존한다. InventoryEngine은 Forecast가 없는 4,421개를 0수요로 간주하지 않고 `MISSING_FORECAST_BUCKET`으로 입장을 차단하며, 누락·비적격 품목의 개수·Hash·최대 100개 표본을 오류 Evidence에 포함한다. 계산 제외나 승인된 정책 Fallback을 허용하려면 별도 업무 결정을 거쳐야 한다.

Demand Forecast Handoff는 정확한 Run의 로컬 Parquet Artifact와 Hash Receipt까지 연결됐다. 다만 `database_published=false`, `operational_io_eligible=false`이며 실제 PostgreSQL 게시나 IO Runtime 호출을 완료한 것으로 해석하지 않는다.

## 7. 실행과 다음 순서

2026-09-07 기준 [승인 이력](IO_FORECAST_HANDOFF_PROPOSAL.md)의 POINT·계획/물리 정밀도·월요일 월 귀속을 [Parquet/v2 계약](IO_FORECAST_HANDOFF_CONTRACT.md)으로 구현했고, 실제 Demand Artifact Mapping·실행 전 Receipt·Studio 전달 계약을 연결했다. 아래 JSON v1 명령과 Hash 계약은 유지한다. 실제 DB 게시와 Runtime 배포는 완료하지 않았다.

```sh
IO_COMPANY_CD=DSE IO_ENVIRONMENT=DEVELOPMENT \
  inventory-engine prepare-source --request /absolute/source-request.json \
  --snapshot-root /absolute/trusted-source-snapshots
```

정확한 Snapshot 파일명은 `<snapshot_id>.json`이다. `--read-postgres`와 환경변수 `IO_POSTGRES_DSN`을 함께 지정하면 Forecast DB 대조만 추가한다. 명령 결과는 JSON이며 파일·Artifact·DB를 저장하지 않는다. 인증정보는 Request/로그에 넣지 않는다.

1. **다음 작업 — Forecast 미존재 품목 처리 규칙:** 4,421개를 계산 제외, 승인된 Source 정책 Fallback, 별도 수요 Source 중 어떤 방식으로 입장시킬지 정한다. 결정 전에는 조용한 0수요 간주와 운영 IO 실행을 금지한다.
2. **외부 작업 대기 — 실제 재고/공급/주문/정책 Source 제공:** 부재·NULL·미확인 의미를 해결한다. 그동안 합성 개발 패키지로 계산 테스트를 계속할 수 있다.
3. **다음 작업 — 공통 Run/Configuration/Cycle·Evidence 연결:** 이미 결정된 실행 계약을 구현하고 두 Hash와 원본/제외 근거를 저장 계약에 연결한다. Source 읽기 완료와 전체 Run 성공을 구분한다.
4. **승인 필요 — 지정 개발 환경 Migration·실제 E2E DB Write·Runtime 배포:** 별도 승인 후 수행한다. P0-14/18/19 보류는 유지한다.

ML/PPO 개선은 기존 공통 DTO를 바꾸지 않으면 병렬로 진행 가능하다. Source Export/공유 Binding을 바꾸는 작업과 통합 검증은 직렬이다. InventoryEngine은 Git 저장소가 아니므로 이번에 Commit/Push/MR은 하지 않는다.
