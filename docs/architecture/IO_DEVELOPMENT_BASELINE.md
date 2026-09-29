# InventoryEngine 개발 기준선과 작업 순서

기준일: 2026-09-29, 로컬 패키지 0.15.0. 이 문서는 **목표 계약 확정**, **코드 구현**, **개발 DB 검증**, **운영 사용**을 구분한다. 목표 아키텍처의 전체 파일 트리가 이미 구현돼 있다고 해석하지 않는다.

동일 순서의 [의존관계 계획 JSON](IO_DEVELOPMENT_PLAN.json), [Network 검증 요약](evidence/network-input-validation-20260903.json), [Canonical·PSI 검증 기록](IO_CANONICAL_PSI_VERIFICATION.md)을 함께 관리한다. 계획 파일은 향후 작업 목록이며 자동 실행 지시나 DB 변경 승인이 아니다.

## 1. 현재 기준선 — 완료한 범위

| 대상 | 실제 상태 | 아직 하지 않은 것 |
|---|---|---|
| InventoryEngine 프로젝트 | Python 3.12 패키지, 독립 Git 저장소, Runtime HTTP 접수·Platform Callback·Worker Orchestration, 전체 PSI Handler와 로컬 영속 Evidence Adapter | 운영 PostgreSQL/Object Storage Adapter, 영속 Queue Worker, 배포 |
| Network 입력 | 요청 ID/Hash·승인·Scope·Plan 기준일 검증, 불변 Network Snapshot·Manifest와 Site 후보 경로 생성 | Artifact 봉인·TB_IO 저장, Cycle/Run FK·Claim, 운송수단 선택·계산 적용 |
| Canonical 입력·Golden | 8개 Snapshot 구조·JSON Schema, 요청 Binding·Hash 검증, 독립 Golden 14개/45 PSI Row | 실제 Legacy SQL Adapter, 운영 Source 의미·정확성 검증 |
| 0.9.1 Source 읽기 호환 | 전체 로컬 검증 후 DB 연결과 기존 JSON v1 DTO·Hash 유지. 실제 DB 2건은 0.9.0 당시 기록 | 실제 재고/주문/정책 Collector와 운영 공유 Snapshot 미준비 |
| 0.10.0 Forecast 전달 | Demand POINT opt-in Export/Guard·분할 Parquet, IO 독립 Reader·Canonical v2 계획/물리 정밀도 분리. 111,020행 전달·입력 준비 검증 | 실제 Artifact Mapping/실행 전 Receipt 저장/Studio 연결·DB Publication·대형 Recommended PSI 성능 |
| 7축 분류·Effective Policy V2 | Config 1.1.0 호환과 2.0.0 7축 결과를 결정론적으로 생성한다. 개발 PostgreSQL DSE/C100/V100에 Config Revision 2와 V2 Snapshot Revision 2를 게시했고 7,000품목·49,000축 Row·26주 Actual Close·Exact Replay를 검증했다. Migration 074와 실제 Claim·Attempt·Retry·CAS도 개발 DB에서 검증했다. | Scheduler·공용 Runtime 배포. SDE/HML 운영 Source와 PLC 권위 Source는 아직 준비되지 않음 |
| Cut-off·단일 Site PSI | 전기 EOH–BOH·Watermark·Late Posting·봉인/고아 검증, 공통 주차 전이·Baseline/로컬 Recommended PSI와 Evidence JSON | ERP 신규 거래 수집/실제 봉인 저장·DB Evidence 적재 |
| 동일 Run PSI Result Bundle | Canonical 입력을 한 번 준비하고 실제 Baseline·Math·PPO Shadow·등록 Stress PSI를 계산한다. 승인된 개발 Cost Profile과 수요 급증·확정 입고 7일 지연 Registry를 Plan에 Hash로 봉인하고 실제 PSI Row에서 비용 Delta를 산출한다. 무비용 V1 경로는 `COST_PROFILE_NOT_BOUND`로 호환된다. | 권위 비용 Source와 운영 Stress Registry, PostgreSQL/Object Storage Adapter, 공용 Runtime 배포·DB 게시 |
| Trade Cost·Landed Cost V1 | C100 V100→V101~V104의 개발 Fixture Catalog, Pure Calculator, Migration 077, 승인·Supersede CAS API, Planning Cycle Run Binding과 Sealed Revision Set 조회를 구현했다. 봉인 Shipment·Projection의 Child 계산, Append-only 원자 저장과 독립 Replay도 Offline 완료했다. | Child의 실제 Claim/Runtime·Parent Bundle 연결과 개발 Simulation·MEIO 비용 소비. 운영 전환 시 권위 Source와 국가별 공식 Rule Adapter 필요 |
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

InventoryEngine 작업 경로는 `/Users/igwanhyeong/PycharmProjects/InventoryEngine`이며 독립 Git 저장소와 `origin`이 구성돼 있다. `main.py`와 `sample_jupyter/io_proximal_policy_optimization.ipynb`는 기존 사용자 자료로 보존한다. 연구 Notebook은 재고정책·Solver 구현 기준선으로 자동 채택하지 않는다.

2026-09-29 선언 범위의 격리 환경 재검증에서 InventoryEngine Unit 459건, Contract 52건,
Offline Integration 85건이 통과했다. 합계 596건 통과·실패 0·Skip 7·Subtest 623건이다. Skip은 명시적으로
비활성화한 PostgreSQL 읽기 전용 테스트이며, DemandEngine 교차 Handoff와 111,020행 전달
검증은 Python 3.12 환경에서 포함했다. Ruff, Format과 JSON Schema 21개 검증도 통과했다.
dsai-platform은 최신 `origin/develop`의 `abe71826` 기준 Inventory 관련 회귀 248건이 통과했다.
`main.py`의 기존 import 위치·전체 서식 지적은 기준 커밋에도 있어 전면 재작성하지 않았다.
새 Inventory Router import는 같은 초기화 순서를 따르며, 그 외 후보 Python 파일의 Ruff와
Format을 검증했다. Frontend와 Platform 전체 기능 테스트는 이번에 재실행하지 않았다.

기존 환경의 Polars 1.31.0·Torch 2.14.0과 의존성 선언은 변경하지 않았다. 임시 환경 두 개에서
모든 선언 Extra를 함께 설치해 Polars 1.41.0/Torch 2.12.0 및 Polars 1.44.2/Torch 2.12.1을
검증했고 `pip check`도 통과했다. 하한 환경은 Inventory 자체 587건, 최신 허용 환경은
Demand 교차 Handoff 포함 596건 통과다. Handoff의 Demand 모듈 로딩에는 NumPy·pandas·
PyArrow·psycopg2-binary·python-dotenv·tqdm을 테스트 보조 의존성으로 추가했다.
이 결과는 macOS arm64/Python 3.12.10의 CPU·Offline 검증이며 Linux/CUDA·공용 Runtime
배포 검증을 뜻하지 않는다. [격리 의존성·Artifact 증적](evidence/landed-cost-artifact-validation-20260929.json)을 따른다.

Migration 074·075·076·077의 개발 적용, Config·분류 Snapshot 게시와 Claim·Retry·CAS는
이전 승인 작업의 증적이다. 이번 Git 기준선 검증에서는 DB 접속·Migration 적용·DB Write와
공용 Runtime 배포를 수행하지 않았다. 세부 검증과 Commit 경계는
[2026-09-29 Git 기준선 기록](evidence/git-baseline-validation-20260929.json)을 따른다.

dsai-platform의 관련 변경은 `origin/develop`에서 분리한 로컬 `codex/inventory-psi-strategy-v2`에서
Run/Plan 기준선 `b0df9b4c`와 Trade Cost 기준선 `c6c38373` 두 Commit으로 고정했다. Push와
`develop` 병합은 하지 않았다. `planning_cycle_revisions`, `planning_cycle_site_executions`,
`engine_run_input_bindings`의 Migration과 Repository/API, Demand Handoff Input Binding,
Runtime Dispatch가 구현돼 있다. Migration 074는 이전 개발 DB 작업에서 적용했고 실제 V2
Claim의 Attempt·Input Binding·Retry CAS와 V1 Effective Run CAS를 검증했다.
Scheduler→공용 Runtime E2E는 아직 수행하지 않았다.
InventoryEngine과 dsai-platform Backend의 Strategy Execution Plan은 모두 `1.1.0`이며,
수학 정책 입력·Replenishment Config·PSI Simulator·Stress Payload/Seed/Runner Binding과
승인 Reference 규칙 및 교차 저장소 Golden Hash가 일치한다. Migration 074의 Planning Cycle
Source 계약 Version은 별도 의미이므로 변경하지 않았다.

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

[Inventory 분류 Snapshot Lifecycle](IO_CLASSIFICATION_SNAPSHOT_LIFECYCLE.md)은 승인된 Config와 Actual Close를 사용한 결정론적 7축 분류, 품목별 유효 정책 Hash, 집계 Receipt, append-only 게시와 exact replay를 구현했다. 개발 PostgreSQL의 DSE/C100/V100에 Config Hash `48ad9893...e77e`와 Snapshot `e54e5649-ad2b-578c-b1fa-2e5603446895`를 게시했다. 7,000개 중 2,300개를 분류했고 4,700개는 `INSUFFICIENT_DEMAND_HISTORY`로 명시적 차단했다. SDE/HML은 비활성, PLC는 `SYNTHETIC + SHADOW_ONLY`다. 이 Snapshot을 Migration 074의 실제 V2 Claim에 고정했으며 자동 게시 불가 Gate도 개발 DB에서 확인했다. 공용 Runtime 배포는 아직 수행하지 않았다.

[PSI·전략 실행 계약 V2](IO_PSI_STRATEGY_EXECUTION_CONTRACT_V2.md)의 저장소 독립 Orchestrator와
실제 Child Artifact를 구현했다. 하나의 Canonical 입력을 한 번 준비해 Baseline, Operational
Mathematical, 승인 PPO Shadow와 등록 Stress Scenario를 실행하고 실제 PSI Row로 비교값을
만든다. PPO/Stress 실패는 선택 Child에 격리하며 Mathematical 결과만 Effective Pointer가
된다. Stress Runner는 미래 수요·확정 입고 변형만 반환하며 Context·Master·Policy·BOH 변경과
임의 전략·PSI 결과 반환을 차단한다. 변형 입력의 실제 Mathematical 전략·공통 Guard·PSI는
Orchestrator가 수행한다. Raw Action에는 판단 당시의 Prepared/Execution Source를 봉인하고 Observation 재투영,
공통 Guard·Effective Policy Admission 재실행, PSI·입고·Capacity 대사까지 수행한다. 상세
검증 범위는 [동일 Run Orchestrator 검증 기록](IO_PSI_ORCHESTRATOR_VERIFICATION.md)을
따른다. 이 Artifact는 아래 로컬 영속 Adapter와 Runtime Handler에서 다시 검증한다.

### Platform Strategy Plan 1.1.0 정합화 — 완료(로컬)

대상: dsai-platform Backend `inventory_engine_run_contract`, 계약 Fixture와 문서.

- 수학 정책 입력 Snapshot, Replenishment Config Hash, PSI Simulator와 Stress Payload·Seed·Runner
  Binding을 Platform Pydantic Model과 Hash 계약에 반영했다.
- 승인 Reference를 InventoryEngine의 닫힌 식별자 규칙과 맞추고 두 저장소의 Golden Hash를
  일치시켰다. Migration 074와 개발 DB Claim 검증은 아래 단계에서 완료했으며 배포는 수행하지
  않았다.

### Result Bundle Artifact 저장·재검증 경계 구현 — 완료(로컬)

대상: InventoryEngine `inventory_evidence`와 저장 Port, dsai-platform Artifact Interface.

- Storage-neutral Port와 로컬 SQLite Adapter를 구현했다. PSI, 원 행동, 제약 행동, 조정 사유,
  Canonical·Prepared·Execution Source와 Bundle의 Canonical JSON 바이트를 한 Transaction에서
  Append-only 저장한다.
- Reader는 바이트 Hash·문서 Content Hash·Reference·Row Count·Child 연결을 다시 검증하고,
  Prepared/Execution Source와 품목별 Admission에서 Action Observation Source를 재구성한다.
- 동일 Reference·동일 바이트는 멱등 성공하고 다른 바이트·Hash는 전체 Transaction을
  Rollback한다. 동시 동일 저장도 하나의 원본과 Exact Replay 하나로 직렬화한다.
- Result-ready Outbox는 Bundle과 같은 Transaction에서 `PENDING`으로 생성되고 성공 게시 후
  `PUBLISHED`로 CAS 전환한다. 실제 PostgreSQL/Object Storage Adapter는 아직 없다.

### 전체 Runtime Pipeline Handler 연결 — 완료(로컬)

대상: InventoryEngine Runtime, dsai-platform Backend 공통 Runtime.

- Plan 1.1.0 Claim을 Command Resolver와 동일 Run Orchestrator에 전달해 입력 해소, PSI 실행,
  Artifact 저장·재검증, 단계 Event, Terminal Event와 Platform Publish를 연결했다.
- Mathematical Bundle Gate만 Runtime 결과로 사용한다. `REVIEW`는 Evidence와
  `WITHHELD_FOR_REVIEW` Outbox를 남기지만 자동 Publish 대상 조회와 Publish를 모두 차단한다.
- Platform 게시 실패 시 Outbox를 유지한다. Retry는 Command 해소와 PSI 계산을 건너뛰고 저장된
  Bundle을 다시 검증해 동일 Pointer를 게시하며, 성공 후 Outbox를 `PUBLISHED`로 전환한다.
- 공용 Runtime 배포와 실제 E2E Write는 별도 승인 대상이다.

### 개발 Cost Profile과 Stress Registry 연결 — 완료(로컬)

대상: InventoryEngine 계약·평가 모듈, 권위 비용·공급 Source.

- 개발 Cost Profile `DEV-KRW-COST-BASELINE/1.0.0`과 수요 20% 증가·확정 입고 7일 지연
  Scenario를 승인 Reference, Payload Hash, Runner Hash와 함께 Strategy Execution Plan에 봉인한다.
- Runtime은 Registry가 해소한 동일 Payload만 허용하고 실제 PSI·제약 적용 행동에서 보유비,
  Backorder 비용, 고정·변동 발주비를 다시 계산한다.
- 이는 개발 E2E용 합성 비용이다. 권위 회계 Cost Profile과 운영 승인 Stress Catalog가 준비될
  때까지 운영 경제성 판정에는 사용하지 않는다. Profile 미결합 Run은 계속
  `COST_PROFILE_NOT_BOUND`로 호환한다.

### Trade Cost Source·Landed Cost V1 실행 기준선 — 완료(개발 환경)

대상: InventoryEngine 계약·Schema, dsai-platform 문서, 개발 PostgreSQL 읽기 영역.

- Source Owner·수집 Manifest를 정의했다. 개발 Fixture는 개발 환경에서 승인할 수 있고
  `development_eligible=true`, `operational_eligible=false`로 해석한다.
- Source Domain별 Versioned Revision과 승인 Revision Set, 실행별 `TB_IO_*` Evidence를
  Migration 077로 설계하고 개발 PostgreSQL에 적용했다.
- Decimal 기반 종가·종량·복합세·Floor/Cap과 고정비 배부기를 구현하고 일본·중국·사우디·UAE
  합성 Golden 4건을 고정했다. 합성 Rate는 법정·운영 세율이 아니다.

- [Trade Cost·Landed Cost 계약](IO_TRADE_COST_LANDED_COST_CONTRACT.md)에서 V1을
  `C100: V100→V101~V104`로 제한하고, 물리 Grain을 Shipment Line+Lane+Item+적용일로 고정했다.
- 개발 PostgreSQL의 C100 35,000개 Site-Item과 승인 Network 8개 Lane을 읽기 전용으로 조사했다.
  `EA`와 Site/국가 관계 외의 HS·원산지·구매가격·Incoterms·운송비·VAT/FTA Source는 운영 계산에
  사용할 수 없으며, 과거 판매가격·환율은 `UNVERIFIED`로 분류했다.
- Source Catalog와 Landed Cost Assessment DTO/JSON Schema를 구현했다. 미확인 값을 0으로
  치환하지 않고 Component 합계, 회수 가능 VAT, FTA Evidence, Actual Replay와
  `operational_eligible`를 Fail Closed로 검증한다.
- Source Revision/Revision Set 승인·Supersede CAS API와 Backend-resolved Run Binding을 구현했다.
- Sealed Projection API는 Run에 고정된 Set ID·Hash를 받아 Repeatable Read로 Source 27개와
  Document·구조화 Record를 읽고 Source·Set Hash를 재계산한다. InventoryEngine Runtime Resolver는
  Projection Hash, Scope, 적용일과 개발/운영 환경을 다시 검증한다.
- 국가별 공식 Source 후보는 운영 전환 자료이며 개발 선행조건이 아니다. Landed Cost Child
  Artifact와 개발 Simulation·MEIO 비용 연결은 아직 수행하지 않았다.

### 실제 V2 Config·분류 Snapshot 게시 검증 — 완료(개발 DB)

대상: dsai-platform Backend, InventoryEngine `classify_inventory`, 개발 PostgreSQL.

- 프로젝트 `9ed62f41`, DSE/C100/V100에 프로젝트 전용 VED 승인본 7,000건과 Config 2.0.0
  Revision 2를 게시·활성화했다.
- 최근 봉인 Actual Close 26주(`202528`~`202601`)로 Snapshot Revision 2를 게시했다. Header
  1건, Item 7,000건, Axis 49,000건, Window 3건, Policy Reason 26,610건을 대사했다.
- 자동 실행 Gate는 `ALLOW` 2,296건, `REVIEW` 4건, `BLOCK` 4,700건이다. 동일 입력 재실행은
  같은 Snapshot ID/Hash의 `exact_replay`이고 추가 DB Write가 없음을 확인했다.
- 상세 식별자와 Hash는 [개발 V2 게시 증적](evidence/inventory-v2-publication-20260915.json)에
  기록한다. 실제 운영 중요도를 뜻하지 않는 개발 VED와 합성 PLC라는 한계는 유지한다.

### 3. Planning Cycle Migration 074와 실제 Claim 검증 — 완료(개발 DB)

대상: dsai-platform Backend, 개발 PostgreSQL.

- Migration 074 Rollback Canary를 통과한 뒤 개발 PostgreSQL에 적용하고 Migration Ledger와
  Table·Column·Constraint·Function·Trigger Signature를 대사했다. 동일 파일 재적용은 DB Write
  없는 Exact Replay였다.
- 실제 V2 Config Revision `d4be4ad6-...`, 분류 Snapshot
  `e54e5649-ad2b-578c-b1fa-2e5603446895`, Effective Policy Hash, 개발 Cost Profile과 Stress
  Scenario Hash를 Strategy Plan 1.1.0과 Claim에 고정했다.
- Attempt 1 실패 후 Attempt 2를 새 `engine_run_id`로 재시도했고 두 Attempt의 10개 Input
  Binding이 동일함을 확인했다. Stale Attempt CAS는 차단됐다.
- V2 집계 Gate가 자동 게시 불가이므로 `effective_run_id` 승격은
  `inventory_effective_run_review_required`로 차단했다. 별도 V1 호환 Cycle에서 성공·검증 Run의
  Effective Pointer CAS와 Exact Replay를 확인했다.
- 상세 식별자와 한계는 dsai-platform의
  `docs/backend/dsim/evidence/inventory-run-lifecycle-migration-074-20260915.json`을 따른다.
  Scheduler→공용 Runtime과 운영 Artifact Publication은 아직 범위 밖이다.

### 4. Landed Cost Child Artifact와 개발 Simulation 비용 연결 — 다음 작업

대상: InventoryEngine `run_inventory`·`calculate_landed_cost`, dsai-platform Run 결과 계약.

- 현재 기준선: [Child Artifact 계약](IO_LANDED_COST_CHILD_ARTIFACT_CONTRACT.md)의 봉인 입력,
  Source 해소, Component/총액 저장과 원본 기반 Replay는 Offline 완료했다. 별도 비용 Child이며
  기존 PSI `result_children`에 비용 Row를 추가하지 않고 Publication Outbox도 만들지 않는다.
- 다음 작업: 실제 Claim으로 Shipment를 해소하고 비용 Child 포인터를 Runtime·Parent 결과에
  연결한다. Parent 계약 변경은 InventoryEngine·Platform 사이에서 먼저 정합화한다.
- 그 후 개발 Simulation 평가에서 보유비·Backorder·발주비와 운송·관세·세금·구매비의 중복
  계산을 차단한다. 실제 Multi-Echelon 최적화 Solver 구현과는 별도 단계다.
- 완료 조건: 같은 Run의 봉인 비용 입력을 동일하게 복구하고 `CALCULABLE`인 개발 결과만
  비용 평가에 사용한다. 미확인 비용은 0으로 대체하지 않는다. Offline 개발에 운영 Owner
  승인은 필요 없고, DB Write·공용 Runtime 배포는 별도 승인 대상이다.

### 5. 실제 Demand·Inventory Source 연결 — 운영 전환 시 외부 작업 대기

대상: DemandEngine/Studio Export, InventoryEngine Source Reader, 운영 Source 소유 부서.

- Demand 성공 후 봉인 Forecast Artifact와 Receipt를 정확한 Cycle Binding으로 전달하고,
  고객 주문·재고·확정 공급·Reserved를 동일 Cut-off와 Watermark로 수집한다.
- SDE는 확정 발주·실입고 이력, HML은 평가 단가·통화·기준일, PLC는 승인 Lifecycle Revision이
  필요하다. 준비 전에는 SDE/HML `UNVERIFIED`, PLC `SYNTHETIC + SHADOW_ONLY`를 유지한다.
- 완료 조건: 동일 Source Binding이 동일 Canonical Hash를 만들고 누락·Late Posting을 차단한다.
  Source 게시나 DB Write가 필요하면 별도 승인받는다.

### 6. PPO 개선 실험 — 다음 작업, 병렬 가능

대상: InventoryEngine ML/PPO 학습·평가 모듈.

- 수학 전략을 운영 기준선으로 유지하고 TRAIN/VALIDATION에서 상태·행동 Mask·보상·종료 효과와
  무수요/간헐수요 표본을 개선한다. 이미 본 TEST에 맞춰 튜닝하지 않는다.
- 완료 조건: 새 Revision과 새 Holdout에서 비용과 서비스 Gate를 함께 충족하거나 명확한
  거부 근거를 남긴다. 자동 운영 전환이나 공용 Runtime 배포는 하지 않는다.

### 7. Publication 물리 계약과 실제 개발 E2E — 승인 필요

대상: InventoryEngine `deliver_inventory`, dsai-platform Runtime, 개발 PostgreSQL과 Artifact 저장소.

- 보류한 P0-13/P0-18을 재개해 `TB_IO_*` Grain·PK/FK·Hash·Receipt, 게시 멱등 Key,
  부분 실패 재발행과 Effective Run CAS를 확정한다.
- Demand→IO 순차 실행과 실패 Site Retry, Artifact/DB 불일치 복구를 실제 Run으로 검증한다.
- 완료 조건: 성공 Run의 Evidence와 Effective Pointer가 일치하고 실패 Run은 정상 결과로
  노출되지 않는다. Migration·DB Write·공용 Runtime 배포는 모두 별도 승인 대상이다.

### 8. 운영 전환과 후속 Capability — 외부 작업 대기 / 승인 필요

대상: 실제 운영 Source·배포 환경, 향후 Multi-Echelon 및 DSIM.

- 운영 전환 전에 실제 Source 정확성, P0-14 보존/복구, P0-19 회귀 합격 Gate를 확정한다.
- 단일 Site Core가 운영 검증된 뒤 Hub–Spoke Multi-Echelon Solver를 별도 범위로 착수한다.
- DSIM은 현재 미구현이며 선행 의존성이 아니다. 구현 시 봉인된 Evidence Read Contract를
  소비하게 한다.

## 5. 병렬 가능성과 승인 경계

공통 전략/PSI·수학적 기준·합성 기반·생산 평가·ML/PPO, V2 Runtime Binding과 개발 DB
Projection은 완료됐다. 모델 성능 합격이나 운영 활성화를 뜻하지 않는다. Artifact 저장
경계와 Runtime Handler 연결도 로컬 완료됐다.
완료된 Landed Cost Artifact와 PPO 연구를 사용하는 평가 구현은 공통 PSI·Result Bundle 계약을 바꾸지 않는 범위에서
병렬 진행할 수 있다. 공통 Artifact 계약 변경·DB Migration·실제 E2E는 직렬 작업이다.
운영 Source 연결은 운영 전환 시 외부 준비가 필요하며 현재 개발 Fixture 흐름을 차단하지 않는다.

실제 V2 Snapshot과 Migration 074·Claim 검증은 완료했다. Publication/E2E는 직렬 적용 전에
별도 승인이 필요하다.
`TB_IO_*` 물리 Schema는 Plan 1.1.0과 공통 Run ID 계약을 맞춘 뒤 작성한다.

기존 아키텍처·PM 기준선에 이번 Backend·QA 검증을 반영했다. 보호된 `.agents/` 대신 이 `docs/architecture`에서 기준선과 계획을 관리한다.

InventoryEngine은 `inventory_engine_dev`와 GitHub `origin/inventory_engine_dev`를 개발 기준 브랜치로 사용한다.
2026-09-29 사용자 결정에 따라 별도 `codex/` 브랜치를 만들지 않고 이 브랜치에서 직접 작업한다.
기존 `codex/inventory-result-orchestrator`는 동일 커밋의 병합 상태를 확인한 후 로컬·원격에서 삭제했고,
기존 미커밋 변경은 `inventory_engine_dev` 작업 트리에 그대로 보존했다.
dsai-platform의 후속 변경은 `origin/develop`에서 분리한 Inventory 전용 브랜치를 기준으로
독립 검토한다. 승인돼 완료된 개발 Migration 074·075·076·077 이외의 추가 Migration, 실제 업무 DB
Write와 Runtime 배포는 별도 승인 전 수행하지 않는다.

InventoryEngine은 PSI Orchestrator·영속 Artifact/Outbox·Runtime와 Trade Cost·Sealed Projection을
기능별 Commit으로 분리하고 이 문서·계획·브랜치 지침을 별도 기준선 Commit으로 관리한다.
Push 대상은 `origin/inventory_engine_dev`이며 Platform과 DemandEngine을 함께 Push하지 않는다.
사용자 원본 `main.py`, 연구 Notebook, 환경 파일, IDE 설정과 생성 Output은 제외한다.
별도 SCM 전문가 보고서의 사용자 수정도 Commit에 포함하지 않고 그대로 보존한다.

## 6. 의사결정과 검증을 분리한 목록

| 구분 | 처리 |
|---|---|
| 이미 결정됨 | POSM/TGSM, 단일 Site/Company, 실행 소유권, Calendar·Master 기준일, 세 보충 전략·공통 PSI, Source 제약 우선, Cut-off·중간 Evidence, Network Master 소유권 |
| 구현·근거 검증 필요 | P0-1 Legacy 외곽선, P0-10 운영 Artifact 봉인·업무 검토(독립 합성 Generator/Golden 로컬 완료), P0-11 공통 실행, P0-15 실제 Source/운영 검증(로컬 PSI Golden 완료), P0-16 실제 Column 의미·정규근사 정책의 운영 적합성(수학적 계산/정책 선택 Golden 완료) |
| 다음 물리 설계 | P0-13 TB_IO Grain·필드·PK/FK·Hash·Receipt |
| 보류 유지 | P0-14 보존·복구, P0-18 Publication 상세, P0-19 정식 Legacy 합격 Gate. 각 착수 경계에서만 재개 |
| 향후 범위 | Multi-Echelon 계산·실제 물류 Source, DSIM Agent/조회 API |

Source 읽기·변환, V2 Runtime Binding, 개발 DB V2 Config·분류 Snapshot 게시와 동일 Run 로컬
PSI Orchestrator는 완료했다. dsai-platform Strategy Plan 1.1.0 정합화, 개발 Cost/Stress
Registry, Result Bundle 영속 Adapter와 Runtime Handler 연결도 로컬 완료했다. Migration 074와
실제 Claim·Retry·CAS 검증 역시 완료했으므로 다시 실행 목록에 넣지 않는다. 다음 직렬 작업은
완료된 Landed Cost Child를 실제 Claim·Runtime·Parent 결과와 개발 Simulation 비용에 연결하는 것이다. 실제 Publication/E2E·공용 Runtime
배포는 별도 승인 대상이며, 권위 SDE/HML/PLC·Trade Source는 운영 전환 때 준비한다.
Multi-Echelon Solver와 DSIM은 별도 후속 범위다.
