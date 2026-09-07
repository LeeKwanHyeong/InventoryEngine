# InventoryEngine 개발 기준선과 작업 순서

기준일: 2026-09-07, 로컬 패키지 0.11.0. 이 문서는 **목표 계약 확정**, **코드 구현**, **개발 DB 검증**, **운영 사용**을 구분한다. 목표 아키텍처의 전체 파일 트리가 이미 구현돼 있다고 해석하지 않는다.

동일 순서의 [의존관계 계획 JSON](IO_DEVELOPMENT_PLAN.json), [Network 검증 요약](evidence/network-input-validation-20260903.json), [Canonical·PSI 검증 기록](IO_CANONICAL_PSI_VERIFICATION.md)을 함께 관리한다. 계획 파일은 향후 작업 목록이며 자동 실행 지시나 DB 변경 승인이 아니다.

## 1. 현재 기준선 — 완료한 범위

| 대상 | 실제 상태 | 아직 하지 않은 것 |
|---|---|---|
| InventoryEngine 프로젝트 | Python 3.12 `pyproject.toml`, `src/dsio_inventory_engine`, CLI·테스트 기반 추가 | 전체 Runner, HTTP/Worker, 배포, Git 초기화 |
| Network 입력 | 요청 ID/Hash·승인·Scope·Plan 기준일 검증, 불변 Network Snapshot·Manifest와 Site 후보 경로 생성 | Artifact 봉인·TB_IO 저장, Cycle/Run FK·Claim, 운송수단 선택·계산 적용 |
| Canonical 입력·Golden | 8개 Snapshot 구조·JSON Schema, 요청 Binding·Hash 검증, 독립 Golden 14개/45 PSI Row | 실제 Legacy SQL Adapter, 운영 Source 의미·정확성 검증 |
| 0.9.1 Source 읽기 호환 | 전체 로컬 검증 후 DB 연결과 기존 JSON v1 DTO·Hash 유지. 실제 DB 2건은 0.9.0 당시 기록 | 실제 재고/주문/정책 Collector와 운영 공유 Snapshot 미준비 |
| 0.10.0 Forecast 전달 | Demand POINT opt-in Export/Guard·분할 Parquet, IO 독립 Reader·Canonical v2 계획/물리 정밀도 분리. 111,020행 전달·입력 준비 검증 | 실제 Artifact Mapping/실행 전 Receipt 저장/Studio 연결·DB Publication·대형 Recommended PSI 성능 |
| ABC-XYZ 분류 Snapshot | 정확한 Site Config와 Actual Close로 분류하고 기본 no-write, append-only 게시와 exact replay를 지원. Claim된 Run의 분류 단계 Event Adapter 구현 | 공통 Run Migration 적용·실제 Claim·Scheduler·공용 Runtime 배포 |
| Cut-off·단일 Site PSI | 전기 EOH–BOH·Watermark·Late Posting·봉인/고아 검증, 공통 주차 전이·Baseline/로컬 Recommended PSI와 Evidence JSON | ERP 신규 거래 수집/실제 봉인 저장·DB Evidence 적재 |
| 세 전략 공통 기반 | MATHEMATICAL/PREDICTIVE_ML/DEEP_RL 계약, 행동/납기/용량 검증, 공급 대기열 | 전략 간 자동 Fallback·운영 승인 인증 |
| 수학적 정책 | 13/26주 이력·SS/ROP/목표재고·승인 Override/Fallback·권고 Golden 14개/42 PSI Row | 실제 Source 적합성/서비스수준 달성 검증 |
| 학습·평가 기반 | 독립 52주 Generator, 시간순 Feature/Label, 비용·서비스·입고지연 Reference World, 130주·5품목·6시나리오 | 실제 Forecast Vintage/공급 이력 수집·운영 봉인 |
| 생산 수학적 전략 평가 | 기존 정책/공통 Guard 호출, 매주 실제 재고 상태 반영, 기간형 정책·Override·소수 UOM·지연 Golden | 운영 Source 성능 합격·실제 Source 전략 우월성 |
| 0.7.0 ML/PPO 및 비교 | 공통 14 Feature·Model Version/Hash, 실제 CPU ML/PPO 학습·JSON 추론, 동일 조건 VALIDATION/TEST 및 종료 점검 | 운영 모델 승인·봉인·배포 |
| 0.8.0 학습 안정성 | 학습 Seed3개×후보2개, 비반복 TRAIN52/VALIDATION52, 선정 Hash 고정 후 새 TEST Seed3개×104주·종료 민감도 실행 완료 | ML/PPO 모두 연구용 성능 기준 미달. 실제 Source 적합성·승격/운영 승인 없음 |
| dsai-platform Network Master | 이전 승인 작업에서 개발 PostgreSQL의 `dsim` Master 3개+Outbox 1개, 10/50/80건 검증 완료 | 이번 작업에서 새 Migration/DB Write 없음 |
| Network Graph | 이전 작업에서 DSDM 개발 Neo4j에 Inventory 전용 190 Node/340 Relationship, Outbox 전부 PUBLISHED | IO는 Graph를 입력 Source로 사용하지 않음. 상시 Worker 활성화 없음 |
| DemandEngine | 기존 `src/run_demand/full_pipeline.py` 등을 설계 참조로 사용 | 이번에 Demand Runtime 변경·재배포·전체 상태 재검증하지 않음 |
| DSIM | 미래 Consumer의 Read Contract만 정의 | Agent·조회 API·서비스 미구현. 현재 연동할 DSIM은 없음 |

InventoryEngine 작업 경로는 `/Users/igwanhyeong/PycharmProjects/InventoryEngine`이며 아직 독립 Git 저장소가 아니다. `main.py`와 `sample_jupyter/io_proximal_policy_optimization.ipynb`는 기존 사용자 자료로 보존한다. 연구 Notebook은 재고정책·Solver 구현 기준선으로 자동 채택하지 않는다.

dsai-platform은 `dsdm_engine_studio_dev` 브랜치에서 기존 Demand 업무 구현을 유지한다. `planning_cycle_revisions`, `planning_cycle_site_executions`, `engine_run_input_bindings`와 IO 실행 연결은 이번 Source 확인 범위에서 구현을 찾지 못했다. 계약 확정과 공통 Runtime Migration 구현을 분리해 관리한다.

## 2. 다시 결정하지 않는 업무 기준선

- POSM/TGSM 동시 지원, 단일 Company, 한 Run 한 Site.
- 목표 주차는 `YYYYWW`, `YEARWEEK=YEARPWEEK` 가정. Demand와 IO의 Calendar/Master Snapshot을 고정한다.
- Demand를 먼저 실행해 검증된 Forecast Snapshot ID/Hash를 전달한다. IO의 최신 Forecast/Network 탐색은 금지한다.
- Master 기준일은 Planning Cycle의 Plan Version 기준일이다. 실행일은 Legacy 회귀용으로만 사용한다.
- 입력 순서는 Forecast + BOH/입고예정 + 정책 → Baseline PSI/품절 → 정책 기반 보충 권고/Recommended PSI다.
- 고객 확정 주문과 순 Forecast를 구분한다. 실제 주문 미충족만 Backorder로 이월하고 Forecast Shortage는 이월하지 않는다.
- 미확약 ORDERED/UNVERIFIED_DUE_IN은 Baseline에서 제외하고 원본·제외 사유를 Evidence에 남긴다. 민감도·Legacy 시나리오는 분리한다.
- Python 안전재고/ROP/목표재고/권고량을 기본으로 하고 Source의 MOQ·발주배수·물리 한도·Lead Time·승인 목표를 준수한다. Override도 Hard Constraint를 위반할 수 없다.
- TGSM MAX_QTY는 목표재고다. Legacy 비교는 POLICY_PROXY, 목표 개발 검증은 독립 SYNTHETIC_BOH Generator를 사용한다.
- Cut-off/Watermark와 전기 EOH–당기 BOH를 검증한 SEALED 재고 Snapshot만 PSI 입력으로 사용한다.
- Run별 입력·중간·최종 산출물은 Artifact와 TB_IO Evidence에 보존한다. 이번 Network DTO는 그 저장 단계의 입력이며 저장 완료를 대신하지 않는다.
- 수식 전용 V1 결정은 공통 PSI·제약 검증과 수학적/예측형 ML/심층 강화학습 전략 지원으로 확장됐다. ML/PPO는 개발 로컬 학습·추론까지 구현했으며 생산 Capacity, BOM/BOR, Site 간 수송 최적화·Multi-Echelon Solver는 별도 범위다.

## 3. 완료한 구현과 정확한 검증 범위

[Network 입력 계약](IO_NETWORK_INPUT_CONTRACT.md)의 `PrepareNetworkInputUseCase`를 실제 개발 PostgreSQL에 연결해 10개 Network/50개 Site를 읽기 전용으로 검증했다. Unit 27건, Producer 호환 1건, 통합 5건을 통과했다.

이 결과는 `NETWORK_INPUT_PREPARED`이며 `run_claimed=false`, `psi_computed=false`, `persistence_status=PREPARED_IN_MEMORY`다. 예제 Cycle/Run/Master Revision ID는 실제 DB Row가 아니며 통합 테스트도 Run을 만들지 않는다. 실제 Pipeline에 이 UseCase를 연결하는 것은 아래 공통 실행 작업에 포함한다.

[Canonical·Cut-off·PSI 계약](IO_CANONICAL_PSI_CONTRACT.md)의 입력 준비와 `RunPsiSimulationUseCase`도 구현했다. 14개 독립 Golden의 45개 PSI Row와 수량 보존식이 일치하며, 잘못된 입력은 PSI 진입 전에 거부한다. 단위 58건(기존 Network 27건 포함), 계약 3건, 오프라인 CLI 통합 3건이 통과했다. 이 작업에서 실제 DB에 접속하지 않았고 기존 Network 읽기 전용 검증 5건은 이전 작업의 기준선이다.

0.2.0 Baseline은 그대로 유지한다. 0.3.0에서 [세 전략 계약](IO_REPLENISHMENT_STRATEGY_CONTRACT.md), `advance_bucket`, `RunRecommendedPsiUseCase`를 추가했다. 원본 Snapshot, 관측/제안/행동 조정·주문·PSI를 JSON으로 반환하지만 Artifact/DB에 저장하지 않는다. `RECOMMENDED_PSI_COMPUTED_LOCALLY`는 계산 완료이며 운영 승인/Run 성공이 아니다. Probe는 학습 모델이 아니고 TGSM Fixture는 52주 Generator가 아니다.

0.3.0 검증은 단위 86건, 계약 5건(Producer 호환 포함), 오프라인 프로세스 통합 4건이며 모두 통과했다. 기존 14개 Golden은 Baseline과 HOLD 전략 양쪽에서 일치한다. [새 검증 기록](IO_REPLENISHMENT_STRATEGY_VERIFICATION.md)을 참조한다. 이번 작업에서도 실제 DB에 접속하지 않았다.

0.4.0에서 [수학적 정책 산출기](IO_MATHEMATICAL_POLICY_CONTRACT.md)를 구현했다. 과거 이력·Profile·승인 정책을 추가 Binding에 고정하고 Source/Python/Effective 정책을 분리한다. 독립 Golden 14개/42개 PSI Row가 통과했다. 당시 단위 111건·계약 7건·오프라인 통합 7건의 [검증 기록](IO_MATHEMATICAL_POLICY_VERIFICATION.md)을 보존한다.

0.5.0에서 [합성 학습·평가 기반](IO_TRAINING_EVALUATION_CONTRACT.md)을 구현했다. 최소 52주 독립 BOH·미도착 공급, 과거만 사용한 Feature/별도 Label, 시간순 구간과 비용·서비스·지연 시나리오가 실행된다. 실제 생성 BOH의 Canonical/PSI 호환 테스트도 통과했다. 최신 실행 수치와 한계는 [검증 기록](IO_TRAINING_EVALUATION_VERIFICATION.md)을 따른다. DB Write·배포·모델 학습은 수행하지 않았다.

0.6.0에서 [생산 전략 평가 Adapter](IO_PRODUCTION_EVALUATION_CONTRACT.md)를 완료했다. 기존 수학적 정책/공통 Guard를 실제로 호출하고 독립 World의 매주 실제 BOH·BO·입고를 다시 관측한다. 합계185개 테스트(단위159·계약12·오프라인 통합14), 3개 구간×6시나리오를 검증했다. [검증 기록](IO_PRODUCTION_EVALUATION_VERIFICATION.md)의 합성 조건을 따르며 운영 성능/배포/저장 완료가 아니다.

0.7.0에서 [ML/PPO 학습·추론·평가](IO_LEARNED_STRATEGIES_CONTRACT.md)를 완료했다. 실제 CPU 학습, JSON 모델 검증·추론, 동일 초기재고/지연/종료 조건의 네 정책 비교를 실행했다. 단위184·계약14·오프라인 통합17의 로컬 검증과 한계는 [최신 검증 기록](IO_LEARNED_STRATEGIES_VERIFICATION.md)을 따른다. ML/PPO가 모든 시나리오에서 수학적 전략보다 우수하지 않으며 운영 승인은 하지 않았다.

## 4. 남은 개발 작업 순서

0.8.0 [학습 안정성 계약](IO_LEARNING_STABILITY_CONTRACT.md)과 [검증 기록](IO_LEARNING_STABILITY_VERIFICATION.md)의 실험은 완료했다. 학습 모델12개, 새 홀드아웃·민감도128회/62,400 Item-week를 평가했지만 ML/PPO 모두 연구용 비용·서비스 Gate를 통과하지 못했다. 모델 승격은 하지 않았다.

[Inventory 분류 Snapshot Lifecycle](IO_CLASSIFICATION_SNAPSHOT_LIFECYCLE.md)은 승인된 Config와 Actual Close를 사용한 계산, 집계 Receipt, append-only 게시와 exact replay를 구현했다. 이미 Claim된 Run에 분류 단계 Event와 Snapshot 집계 증적을 연결하는 Adapter도 추가했다. 공통 Run Claim DTO와 Platform 호환 Migration 074는 개발 초안이며 실제 Migration 적용·Claim·전체 실행 Evidence 저장은 아직 수행하지 않았다.

### 1. 실제 공유 Artifact·Receipt·Studio 연결 — 다음 작업 / 외부 작업 대기

대상: DemandEngine/Studio의 Export 접점과 InventoryEngine Source Reader. [Source 계약](IO_SOURCE_READ_CONTRACT.md)과 [검증 기록](IO_SOURCE_READ_VERIFICATION.md)의 읽기/매핑 및 DB 연결 전 방어 구현은 완료했다. 실제 운영 Source가 준비됐다는 의미는 아니다.

- [승인된 Handoff 계약](IO_FORECAST_HANDOFF_CONTRACT.md)의 로컬 구현은 완료했다. POINT·계획 소수/물리 정수·월요일 월 귀속은 더 이상 미결 결정이 아니다. 기존 JSON v1을 유지하고 v2의 200,000행·128MB 경계를 명시했다.
- 다음은 Demand 실제 사용 파일을 공유 Snapshot으로 읽는 Mapping과 실행 전 Receipt 저장, Studio의 성공 후 별도 Export/정확한 Cycle Binding 연결이다. Guard Hook이 기본 경로에서 자동 활성화됐거나 기존 Run이 소급 봉인됐다고 해석하지 않는다.

- 성공 Demand Run의 Artifact Seal 이후 DB Receipt 게시·검증 상태를 연결한다. 현재 DB의 실적/성능 Snapshot을 Forecast 출력 봉인으로 대신 쓰지 않는다. 기존 성공 Run과 Export의 실패/재시도를 분리한다.
- 고객 확정 주문·공급 확약·Reserved와 `PLAN_ID/PLAN_STRT_DT/BASE_DT`의 실제 Source를 확인한다. 현재 개발 DB에서 관련 Legacy 테이블 미확인, V100 정책 7,000행 NULL을 기록했다. 미확인 Source를 0 또는 CONFIRMED로 대체하지 않는다.
- Cut-off에 고정된 Watermark로 원천을 수집하고 실제 봉인 Artifact/Receipt를 생성한다. 현재 검증기는 이미 전달된 SEALED 입력을 검증하는 역할이다.
- 운영 Source 준비 전에는 독립 합성 Generator/Golden으로 구현을 검증한다. 사용자 데이터 변경 없이 Adapter 계약 테스트부터 진행한다.
- 완료 조건: 동일 Source Binding이 동일 Canonical Hash를 만들고 실제 누락·Late Posting을 Source 계약에 따라 탐지한다. DB Write가 필요하면 별도 승인받는다.

### 2. 미달한 ML/PPO 개선 실험 — 다음 작업

대상: InventoryEngine ML/PPO 학습·평가 모듈. 완료한 [학습 안정성 실험](IO_LEARNING_STABILITY_VERIFICATION.md)을 재구현하지 않고 미달 원인을 분리한다.

- 수학적 전략을 기준선으로 유지하고 TRAIN/VALIDATION에서 정규화·무수요 품목·행동/보상·학습량 영향을 검증한다.
- 이번에 평가한 TEST에 맞춰 후보를 튜닝하지 않는다. 개선 후보는 새 Revision과 새 홀드아웃으로 평가한다.
- 완료 조건: 비용과 서비스 기준을 함께 충족하는 재현 근거 또는 명확한 거부 이유. 자동 운영 전환·공용 Runtime 배포는 하지 않는다.

### 3. 공통 Run·Configuration·Planning Cycle 연결 — 다음 작업

대상: dsai-platform Backend와 InventoryEngine `platform_contracts`, `run_inventory`, Bootstrap.

- 이미 확정한 공통 Run 소유권과 최소 Cycle Table 원칙을 실제 호환 Migration·API 계약으로 구현한다. Demand 기존 API/Run을 깨지 않는다.
- Plan Claim, Run ID, Attempt, 부분 성공, 같은 입력 Retry/새 Revision 구분을 구현한다.
- Network UseCase에는 Claim 이후 Run Context와 Cycle에 고정된 Network ID/Hash를 전달한다. 이 단계에서 Cycle/Run Row 존재와 전체 입력 Binding을 검증한다.
- 완료 조건: 같은 입력의 기술적 Retry는 새 Run만 만들고 입력 Hash를 유지한다. 변경된 입력은 새 Cycle Revision으로 분리된다.

### 4. Artifact·TB_IO 중간 산출물 저장 — 다음 작업

대상: InventoryEngine `inventory_evidence`·저장 Adapter, dsai-platform의 `dsim` Migration 초안.

- P0-13에서 Manifest·Cut-off 대사·Canonical 입력·PSI·정책 근거의 물리 Grain/PK/FK/Precision을 확정한다.
- 모든 단계의 불변 DTO를 Run-scoped Artifact에 봉인하고 DB와 Row Count/Hash를 대사한다. Network 전체 Snapshot과 Site Context도 포함한다.
- 계산은 DB와 분리해 구현할 수 있으나, Evidence가 영속 저장되지 않은 상태를 전체 Run 성공으로 표시하지 않는다.
- 완료 조건: 실행별 입력/중간/결과 계보를 재구성할 수 있다. 실제 Migration/DB Write는 해당 대상별 승인 후 수행한다.

### 5. Publication과 실제 개발 E2E — 승인 필요

대상: InventoryEngine `deliver_inventory`, dsai-platform 공통 Runtime, 지정 개발 PostgreSQL/Artifact 저장소.

- Publication 구현 전에 보류한 P0-18을 재개해 게시 Table·멱등 Key·부분 실패 재발행·Effective Run CAS를 확정한다.
- Demand → IO 순차 실행, 실패 Site Retry, Artifact/DB 불일치·복구를 실제 Run으로 검증한다.
- 완료 조건: 성공 Run의 Evidence/Receipt와 유효 결과 포인터가 일치하고 실패 Run은 정상 결과로 노출되지 않는다.
- 신규 DB Write·Migration·공용 Runtime 배포 범위는 별도 승인받는다. 이전 Network Master 적용 승인을 전체 IO E2E 승인으로 확대하지 않는다.

### 6. 운영 전환과 후속 Capability — 외부 작업 대기 / 승인 필요

대상: 실제 운영 Source·배포 환경, 향후 Multi-Echelon 및 DSIM 개발.

- 실제 재고·주문·공급 확약 Source, 물류 계약을 검증하고 P0-14 보존/복구와 P0-19 정식 회귀 Gate를 운영 전환 전에 확정한다.
- Multi-Echelon은 단일 Site Core 검증 이후 별도 계산 범위로 착수한다.
- DSIM은 아직 미구현이며 IO 개발의 선행 의존성이 아니다. DSIM을 개발하는 시점에 Evidence Read Contract를 소비하도록 설계한다.

## 5. 병렬 가능성과 승인 경계

공통 전략/PSI·수학적 기준·합성 기반·생산 평가·ML/PPO 및 0.8.0 학습 안정성 실험은 완료됐다. 모델 성능 합격은 아니다. 1번 실제 Source Adapter, 2번 모델 개선 실험, 3번 Platform 실행 연결은 기존 계약을 보존하면 독립 진행 가능하다. 같은 Canonical DTO/API·모델 Binding과 E2E 기준선 변경은 직렬로 합의한다.

4번 물리 Schema는 공통 Run ID 계약과 맞춘 뒤 작성한다. 실제 Migration → Source 적재 → 실행 → Publication/E2E는 직렬이며 대상별 승인이 필요하다. 이번 작업에서 다른 세션이나 Agent를 자동 실행하지 않았다.

기존 아키텍처·PM 기준선에 이번 Backend·QA 검증을 반영했다. 보호된 `.agents/` 대신 이 `docs/architecture`에서 기준선과 계획을 관리한다.

InventoryEngine은 Git 미초기화이므로 Commit/Push/MR의 대상 Branch와 Remote는 아직 없다. 버전관리 착수 시 이를 먼저 확정한다. dsai-platform의 후속 변경은 `dsdm_engine_studio_dev`를 기준으로 독립 검토하며, 이번에 Commit/Push/배포하지 않았다.

## 6. 의사결정과 검증을 분리한 목록

| 구분 | 처리 |
|---|---|
| 이미 결정됨 | POSM/TGSM, 단일 Site/Company, 실행 소유권, Calendar·Master 기준일, 세 보충 전략·공통 PSI, Source 제약 우선, Cut-off·중간 Evidence, Network Master 소유권 |
| 구현·근거 검증 필요 | P0-1 Legacy 외곽선, P0-10 운영 Artifact 봉인·업무 검토(독립 합성 Generator/Golden 로컬 완료), P0-11 공통 실행, P0-15 실제 Source/운영 검증(로컬 PSI Golden 완료), P0-16 실제 Column 의미·정규근사 정책의 운영 적합성(수학적 계산/정책 선택 Golden 완료) |
| 다음 물리 설계 | P0-13 TB_IO Grain·필드·PK/FK·Hash·Receipt |
| 보류 유지 | P0-14 보존·복구, P0-18 Publication 상세, P0-19 정식 Legacy 합격 Gate. 각 착수 경계에서만 재개 |
| 향후 범위 | Multi-Echelon 계산·실제 물류 Source, DSIM Agent/조회 API |

Source 읽기·변환과 DB 연결 전 방어는 [0.9.1 검증 기록](IO_SOURCE_READ_VERIFICATION.md)의 범위에서 완료했다. 바로 다음은 **Demand/Studio의 실제 봉인 Export 접점·대표 통계량·소수 EA 처리 기준 연결**이다. 재고/주문/정책 Source는 외부 준비 대기이며 합성 개발 경로를 유지한다. ML/PPO 개선·공통 Run 구현은 기존 계약을 보존하면 독립 진행 가능하다. Multi-Echelon Solver와 DSIM은 별도 후속 범위다.
