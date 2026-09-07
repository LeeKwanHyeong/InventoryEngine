# Demand → IO Forecast 전달 계약과 구현 범위

기준일: 2026-09-04. 사용자 구현 승인 반영. InventoryEngine **0.10.0**, DemandEngine **demand_engine_v3**. 로컬 계약·코드·오프라인 검증 범위이며 DB 게시나 운영 활성화가 아니다.

## 1. 확정한 의미

| 항목 | 적용 계약 |
|---|---|
| 통계량 | 선정 Winner의 `POINT=yhat`. 평균 또는 P50라고 재명명하지 않으며 자동 대체하지 않음 |
| 계획 수량 | Forecast·예상 PSI·파생 정책은 소수 최대 6자리. 원본 수요를 정수 반올림하지 않음 |
| 물리 수량 | EA BOH·Reserved·확정 고객 주문·확약 입고·실제 전기 EOH·Cut-off 조정은 정수, 대사 허용오차 0 |
| 보충 행동 | 최종 수락 발주·권고 입고는 물리 정밀도와 MOQ·Lot·용량 제약 준수 |
| 월 귀속 | 공유 Calendar의 주 시작 월요일 소속 월. 부분 주차도 원래 월요일 기준. Legacy 수요일은 회귀 비교용 |
| 실행 순서 | 공유 입력 고정 → Demand 실행 → 기존 Winner 선정 → 별도 Export → Receipt 검증 → IO 입력 준비 |

예: `202627`은 월요일 2026-06-29의 월인 `202606`에 속한다. 매주 `0.4 EA`인 13주 Forecast는 `5.2 EA`다. 소수 예상 EOH는 물리 ERP 재고가 아니므로 다음 실행의 실제 BOH나 Cut-off 대사 Source로 재사용하지 않는다.

## 2. 실행 전 Binding과 실행 후 Export를 분리

Demand의 [교환 DTO](../../../DemandEngine/src/demand_contracts/forecasting/export.py)는 다음 두 계약을 제공한다.

- `SharedForecastInputBinding`: Selector, 공유 Calendar/Master의 전체 봉인 내용, Cycle/Revision, Plan 기준일, Input Manifest/Source Profile Hash. **아직 존재하지 않는 Winner 선정 Hash는 요구하지 않는다.**
- `ForecastExportRequest`: 위 공유 입력과 정확한 최종 선정 근거 Hash를 함께 고정한다. Export Key에는 선정 근거가 포함되지만 실행 전 Shared Binding Hash에는 포함되지 않는다.

`SharedForecastInputGuard`를 [Composition Root](../../../DemandEngine/src/bootstrap/postgresql_full_pipeline_composition.py)의 선택 인자 `shared_input_guard`에 주입할 수 있다. Forecast 전후의 기존 Manifest 검사에서 함께 호출되며 기본값은 `None`이다. 기존 Run/Binding v1 Hash와 Forecast/Winner 계산은 바꾸지 않았다.

이 Guard의 두 Port는 후속 Studio/Runtime 연결 대상이다.

1. `load_observed`: **Forecast가 실제 사용하는** Calendar/Master Artifact를 Canonical 공유 구조로 읽어야 한다. 요청의 사본이나 현재 DB Master를 반환하면 안 된다.
2. `persist_receipt`: 실행 전에 불변 Receipt를 생성하고 같은 호출의 재검증은 동일 내용으로만 허용해야 한다. 저장 실패 시 Guard는 Forecast 진입을 막는다. Receipt는 작성자 인증 수단이 아니다.

따라서 Guard API가 있다는 이유로 기존 실행이 공유 Snapshot을 사용했다고 주장할 수 없다. 실제 Artifact Mapping·Receipt 저장 Port·Studio 기본 호출은 아직 연결하지 않았다. 과거 Run의 사후 봉인/승격도 하지 않는다.

## 3. 별도 Export의 저장·재시도 계약

Demand [Export UseCase](../../../DemandEngine/src/deliver_demand/application/export_forecast_snapshot.py)는 성공 Run, Selector, Input Manifest, 실행 전 Receipt, 품목별 선정 근거, 전체 Universe/주차를 검증한다. IO가 Winner를 다시 선정하지 않는다.

선정 근거 Hash는 `item_id/model_run_id/selection_decision_id`의 정렬된 투영에 대한 Hash다. 기존 Winner 전체 Manifest Hash를 대신 넣을 수 없다. `result_id`와 선정 ID는 모든 출력 행에 보존한다. 최종 수량은 출력 행의 별도 Hash에 포함한다.

PostgreSQL Reader는 전용 Connection의 `REPEATABLE READ READ ONLY`/30초 Timeout에서 정확한 Tenant·Project·Run·Plan·Site·Scenario·POINT만 읽는다. 선정 테이블과 결과 테이블을 같은 읽기 Transaction에서 대조하고 마지막에는 Rollback/Close한다. 신규/기존 DB 행 변경은 **0건**이다.

SQL은 기존 `engine_runtime_runs`, `tb_fcst_weekly_selection_decision`, `tb_fcst_model_run`, `tb_sum_fcst_weekly_res`를 읽는다. 이번에 실제 DB 조회·EXPLAIN·인덱스 변경은 하지 않았으므로 실 DB의 접근계획·성능·과거 Run 적합성은 미검증이다.

Parquet Writer는 private 임시 폴더에서 전체 검증이 끝나야 최종 폴더를 Atomic Rename한다. Part와 Manifest/Directory를 fsync한다. 실패한 미봉인 파일은 해당 임시 폴더에서 정리하고 기존 봉인을 덮어쓰지 않는다.

- 논리 ID: `FCST-<Export Request의 SHA-256>`.
- Content Hash: Request + 정렬된 행의 줄 단위 Canonical JSON Hash + Row Count.
- Manifest Hash: 최종 Manifest의 실제 Byte Hash.
- Part: 순서·파일명·행수·첫/마지막 품목-주차 Key·Byte Count/Hash. 모든 Column은 String이며 수량은 Decimal 문자열이다.
- 같은 Export Key와 같은 결과는 원래 Receipt를 반환한다. Part 크기가 달라도 원래 봉인을 반환한다. 같은 Key에 다른 수량/Result ID가 나오면 충돌로 차단한다.
- Export 실패는 이미 성공한 Demand Run을 실패로 변경하지 않는다. Export만 재시도한다.

반환 상태는 `ARTIFACT_SEALED`, `database_published=false`다. **DB Receipt 게시 완료, 승인 인증, IO 시작 자격 완료를 뜻하지 않는다.** Artifact 선봉인 이후 DB 원자 게시/Receipt 복구는 기존 Run·Evidence 후속 작업이다.

## 4. IO 독립 Reader와 Canonical v2

[IO Reader](../../src/dsio_inventory_engine/infrastructure/parquet/forecast_handoff.py)는 Demand 패키지를 import하지 않는다. 호출자가 지정한 Snapshot ID·Content Hash·Manifest Hash로 Part 전체를 검사한 뒤에만 행을 반환한다. 누락·변조·중복·순서 오류·경로 탈출·파일 Symlink를 차단한다. Hash 자체를 인증이나 업무 승인으로 해석하지 않는다.

[Handoff UseCase](../../src/dsio_inventory_engine/prepare_inventory/application/forecast_handoff.py)는 검증된 Forecast를 Canonical v2 템플릿에 연결하고 기존 Cut-off/Scope/Universe 검사를 호출한다. 공유 Snapshot 내용과 Cycle/Revision·W0가 정확히 같아야 한다. W0는 `fcst_w0_yyyyww`; 별도의 Demand `plan_yyyyww`는 원본 Selector에 보존한다.

Canonical Mapping은 `POINT_TO_SAME_BUCKET_V1`이다. 원본 POINT를 `SAME_BUCKET_CONSUMPTION` Forecast로 전달하며 고객 확정 주문과의 중복 소비는 기존 PSI가 담당한다. 이미 순수요로 차감된 Forecast를 이 계약으로 위장해 넣으면 안 된다.

Source Snapshot Hash와 Canonical Forecast Hash는 다르다. 반환 `handoff_evidence`는 원본 Manifest/Content Hash, Mapping Revision, 원본 Result/Model/Selection ID와 Canonical Hash를 함께 보존한다. 현재는 메모리 반환이며 `TB_IO_*`에 저장하지 않는다.

| 버전 | 수량 규칙 | 크기와 호환성 |
|---|---|---|
| Canonical v1 / Source JSON v1 | 기존 `scale`, EA=0 | 100,000행·8MB 유지, 기존 Golden/Hash 유지 |
| Canonical v2 | `planning_scale`, `physical_scale`; Export 연결 시 planning=6, EA physical=0 | Snapshot당 200,000행, 전체 요청 128MB, PSI Grid 200,000 유지 |
| Export Parquet | 계획 Decimal 원본 의미 유지 | Part 최대 20,000행, 전체 200,000행·128MB, Manifest 8MB |

v2의 128MB 한도는 Recommendation/수학적/학습 추론 Envelope에서도 유지한다. 기존 정책 이력 Snapshot v1의 100,000행 한도는 변경하지 않았다. 전체 Site 자료를 JSON v1로 재조립하거나 Horizon을 줄이지 않는다. IO 계산은 **상한이 있는 메모리 배치**이고 무제한 Streaming Solver가 아니다. 111,020행 검증은 전달·Canonical Admission 검증이며 대형 Recommended PSI/학습 성능 검증은 아니다.

ML/PPO Feature 정규화의 최소 단위는 물리 정밀도로 유지하고, 파생 정책 출력만 계획 정밀도로 확장했다. 모델 승격이나 기존 연구 Gate 판정은 바꾸지 않았다.

## 5. 호출 경계와 검증

Demand 별도 명령: `PYTHONPATH=src python -m run_demand.forecast_export_entrypoint --help`.

실제 Export에는 `--request`, `--request-file-hash`, `--shared-receipt`, `--shared-receipt-file-hash`, `--artifact-root`, `--dsn-env`가 모두 필요하다. DSN 값은 명령/JSON에 넣지 않고 기존 환경변수 이름만 전달한다. 이 문서는 실 DB 실행 승인이 아니다.

IO는 `PrepareForecastHandoffUseCase(deployment, ParquetForecastHandoffReader()).execute(template, manifest_path=..., snapshot_id=..., content_hash=..., manifest_hash=...)`로 호출한다. 기존 `prepare-source` CLI는 JSON v1용으로 유지했다. Parquet Reader는 `handoff` Optional Dependency가 필요하다.

[오프라인 검증 기록](IO_FORECAST_HANDOFF_VERIFICATION.md)을 기준으로 단위·교차 계약·기존 회귀를 구분한다. Source 없는 값은 Fixture로만 생성했으며 운영 재고 정확성이나 DB Publication을 검증했다고 해석하지 않는다.

## 6. 남은 순서

1. **다음 작업 — DemandEngine/Studio 연결:** 실제 공유 Artifact Mapping, 실행 전 Receipt 저장, 성공 후 별도 Export 호출, Cycle의 정확한 Export ID/Hash 고정을 연결한다. 계약 의존성이 있으므로 현재 세션에서 직렬 진행하는 작업이다.
2. **외부 작업 대기 — 실제 재고·공급·주문·정책:** Cut-off/Watermark/Reserved 의미와 누락 Source를 확보한다. 미확인 Due-in 제외는 유지하고 개발은 명시적 합성 BOH로 계속할 수 있다.
3. **다음 작업 — InventoryEngine/플랫폼 Run·Evidence:** Claim/Attempt/Cycle과 Canonical/원본/결과 Evidence를 연결하고 미게시 Handoff의 운영 진입을 막는다. P0-14/18/19는 보류 상태를 유지한다.
4. **승인 필요 — 지정 개발 DB·Runtime 검증:** Migration·DB Receipt 게시·실제 E2E Write·공용 Runtime 배포는 각각 대상 확정 후 별도 승인한다. 이번 작업에는 포함하지 않았다.

학습 개선은 계약을 변경하지 않는 경우 별도 작업으로 병렬 가능하다. InventoryEngine은 Git 미초기화, DemandEngine은 `demand_engine_v3`이며 이번에 Commit/Push/MR을 수행하지 않는다.
