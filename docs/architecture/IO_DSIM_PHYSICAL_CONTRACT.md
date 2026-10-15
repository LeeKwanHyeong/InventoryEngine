# DSIM 관계형 Evidence 물리 계약 초안

상태: DRAFT_NOT_APPLIED · 기준일: 2026-10-15 · 계약: inventory.dsim.evidence-physical-draft / 1.0.0

대상: InventoryEngine `inventory_engine_dev` · dsai-platform `codex/inventory-psi-strategy-v2`.

## 최신 구현 상태 — 2026-10-15

[Projection Writer·Verifier](IO_DSIM_PROJECTION_WRITER_VERIFIER.md)는 로컬 구현·Offline 검증을 완료했다.
실제 Artifact의 NO_ORDER, 미제공 입고일/봉인 시각, Site 공통 Cut-off, HML 승인 수준과 별도 주문 Gate,
장기 평가 기간을 반영해 미적용 초안을 21개 Table·277개 컬럼으로 보정했다. 아래 향후 Writer 항목은
새 구현 계약으로 대체하며 실제 PostgreSQL 적용·FK/Trigger/동시성 검증과 기본 Runtime 활성화는 미수행이다.

## 현재 기준선과 목적

[Modified Table 매핑](https://app.notion.com/p/3fabbe40561380918894f8b899aefa79)의 24개 후보 역할을 Platform의 기존 040·072·074~078 Migration과 InventoryEngine Canonical·PSI·정책 탐색·Result Bundle 코드에 대조했다. 이번 산출물은 **컬럼·Grain·PK/FK·무결성·트랜잭션의 로컬 설계 초안**이다. Runtime 입출력 계약 Version은 바꾸지 않았다.

신규 관계형 Table은 21개 후보로 정리하고, 정책 승인/배정 Master 2개와 DB Publication Outbox는 보류한다. 실제 DB 접속·적용·데이터 적재·Runtime 배포·Commit·Push는 하지 않는다. 기존 079 판단 주차 제약 초안 및 FSN/운영 Gate는 유지한다. Notion 매핑 원문도 변경하지 않는다.

## 기존 구조 재사용

- Run·Attempt·입력 Binding·Cycle/Site·Effective Run은 기존 `dsai` 원장이 권위 Source다. `plan_context`는 날짜·Cut-off·Canonical/Prepared Hash를 추가하는 Run Evidence 확장이다.
- 승인 Site 입력 묶음 `dsai.inventory_site_input_sets`, Config Revision, 7축 분류/유효 정책 Projection을 재사용한다. `snapshot_manifest`는 승인 원장이 아니라 실제 소비한 Run Binding과 봉인 Artifact의 조회 사본이다.
- `dsdm`의 Item/Site/Calendar를 Source로 읽고, Run별 frozen Item/Location/Calendar를 저장한다. 변경 가능한 현행 Master를 과거 결과의 FK로 사용하지 않는다.
- Network 3개 Master와 Migration 077의 Trade/비용 13개 Table은 그대로 사용한다. Landed Cost는 기존 Assessment/Component를 중복 생성하지 않는다.
- 현재 파일 기반 Durable Outbox·Artifact Writer/Reader/Verifier는 유지한다. 신규 DB Writer/Reader·DB 업무 게시 Outbox 구현 완료로 간주하지 않는다.

## 신규 Table 후보와 저장 단위

| 신규 Table (`dsim.` 생략) | 저장 단위 (Grain) | PK |
| --- | --- | --- |
| `tb_io_plan_context` | 한 Inventory engine_run_id | `engine_run_id` |
| `tb_io_snapshot_manifest` | Run + input_type | `engine_run_id` + `input_type` |
| `tb_io_calendar_bucket` | Run + yyyyww | `engine_run_id` + `yyyyww` |
| `tb_io_location` | Run + location_id | `engine_run_id` + `location_id` |
| `tb_io_item` | Run + item_id | `engine_run_id` + `item_id` |
| `tb_io_buffer` | Run + buffer_id; Run + item_id도 유일 | `engine_run_id` + `buffer_id` |
| `tb_io_demand` | Run + Buffer + Bucket + demand_type | `engine_run_id` + `buffer_id` + `yyyyww` + `demand_type` |
| `tb_io_inventory_position` | Run + Buffer + position_type | `engine_run_id` + `buffer_id` + `position_type` |
| `tb_io_scheduled_receipt` | Run + receipt_id | `engine_run_id` + `receipt_id` |
| `tb_io_inventory_policy` | Run + Buffer + policy_id | `engine_run_id` + `buffer_id` + `policy_id` |
| `tb_io_inventory_reconciliation` | Run + reconciliation_id | `engine_run_id` + `reconciliation_id` |
| `tb_io_result_bundle` | Run당 결과 묶음 1건 | `engine_run_id` |
| `tb_io_result_child` | Run + child_result_id | `engine_run_id` + `child_result_id` |
| `tb_io_policy_candidate` | Run + search Child + Buffer + candidate_id | `engine_run_id` + `child_result_id` + `buffer_id` + `candidate_id` |
| `tb_io_psi_bucket` | Run + Child + Buffer + simulation_id + 주차 | `engine_run_id` + `child_result_id` + `buffer_id` + `simulation_id` + `yyyyww` |
| `tb_io_policy_result` | Run + Child + Buffer + simulation_id + 판단주차 | `engine_run_id` + `child_result_id` + `buffer_id` + `simulation_id` + `yyyyww` |
| `tb_io_replenishment_proposal` | Run + Child + proposal_id | `engine_run_id` + `child_result_id` + `proposal_id` |
| `tb_io_replenishment_diagnostic` | Run + diagnostic_id | `engine_run_id` + `diagnostic_id` |
| `tb_io_policy_evaluation` | Run + evaluation_id; ID는 Child·탐색·Buffer·후보·Context의 결정론적 Hash | `engine_run_id` + `evaluation_id` |
| `tb_io_policy_selection` | Run + selection Child + Buffer | `engine_run_id` + `child_result_id` + `buffer_id` |
| `tb_io_publication_receipt` | Run당 VERIFIED Evidence Receipt 1건 | `engine_run_id` |

전체 컬럼 형식·NULL·PK·Unique·FK·Index는 이 저장소의 [기계 판독 Catalog](../../schemas/inventory-dsim-physical-contract-v1.json)에 있다. Platform 원본은 `infra/contracts/inventory-dsim-physical-contract-v1.json`, 전체 설명은 Platform `docs/backend/dsim/inventory-evidence-physical-contract-v1.md`에 있다. 두 Catalog는 동일 byte로 관리하고 변경 시 함께 대사한다. InventoryEngine 테스트가 다른 저장소 checkout을 요구하지는 않는다.

## Canonical·계산 의미 매핑

- `demand`는 `FORECAST`와 `CUSTOMER_ORDER`를 분리한다. Legacy `TB_ENG_SALES_ORDER.SO`가 Forecast에서 나왔다는 이유로 고객 확정주문으로 변환하지 않는다. Backorder는 Position/PSI에서 별도 관리한다.
- `inventory_position`은 CURRENT/PRIOR를 구분한다. CURRENT의 on-hand는 available + reserved이고, W0 PSI BOH는 available이다. 합성 시작재고는 `SYNTHETIC_BOH`와 Simulation ID/Generator Version을 보존한다. `POLICY_PROXY` 표기만으로 운영 입장 자격을 얻지 않는다.
- `scheduled_receipt`에는 원 수량·예정일·상태·제외 사유를 보존한다. Baseline 가산은 CONFIRMED/IN_TRANSIT만 허용하고 ORDERED/UNVERIFIED/Legacy 가정은 제외한다.
- `inventory_policy`는 Source 제약·승인 서비스 입력·비교용 Legacy 목표/ROP를, `policy_result`는 Python 안전재고·ROP·목표재고를 기록한다. TGSM MAX_QTY는 Source 목표재고로 정규화하며 실제 BOH나 물리 Capacity로 추정하지 않는다.
- PSI는 공통 결정론적 Simulator Row를 그대로 투영한다. 이전 BOH/EOH·현재주문/순 Forecast/Backorder 이월을 구분한다. FSN/PLC/SDE/HML 영향은 기존 유효 정책 입력을 통해 들어오며 이 SQL이 정책을 다시 계산하지 않는다.
- 일반 PSI Child는 simulation_id=BASE, 탐색 후보 PSI는 simulation_id=candidate_id다. 후보마다 새로운 wire PSI Child를 발명하지 않고 기존 탐색 Artifact 안의 Row를 별도 키로 보존한다. 감도 평가 전체 PSI는 원 Artifact가 권위 Source이며 DB에는 Context별 비교값부터 투영한다.
- Candidate는 수학적 후보, Evaluation은 기간/비용/Stress/종료 조건별 평가, Selection은 품목별 최종 선택 증적이다. 선정은 운영 정책 승인이 아니다. 무수요 fill rate와 미가용 비용은 null로 보존한다.
- Registry의 `POLICY_SEARCH/POLICY_SELECTION/POLICY_SENSITIVITY/LANDED_COST`는 추가 조회 분류다. 기존 Result Bundle wire의 `BASELINE_PSI/RECOMMENDED_PSI/STRESS_PSI` 종류를 확장하거나 기존 Version을 바꾸지 않는다.

## 수치·Hash·키 규칙

- 단위 EA, 유한 NUMERIC. 계산 결과를 소수 6자리로 잘라내지 않는다. Source 입력의 기존 소수 6자리·크기 제한은 Canonical에서 검증한다. 대사 차이만 음수를 허용한다.
- 주차는 공유 Calendar Key와 seq를 사용한다. 포함형 Source 정책 기간을 SQL daterange의 닫힌 경계로 비교해 겹침을 막는다.
- FK는 같은 Run의 Snapshot/Item/Buffer/PSI/후보를 참조한다. Root만 기존 dsai Run의 Tenant·Project Scope를 참조한다. V2 분류의 ID·Hash·Scope·Config·판단 주차는 함께 검사한다.
- Hash 정규화는 기존 `values.canonical_json/digest/quantity_text`와 맞춘다. Projection Hash는 Receipt를 제외한 20개 Table을 Table 이름/PK 순으로 정규화하며 created_at은 제외한다. DB는 Hash 형식/바인딩만 검사하고 실제 재계산은 후속 Writer/Verifier가 수행한다.
- 평가 ID는 Run·평가 Child·원 탐색 Child·Buffer·후보·Context의 Hash다. 길이가 큰 복합 자연키를 그대로 B-tree PK에 쌓지 않는다. 원 Artifact ID나 Content Hash를 바꾸지는 않는다.

## 저장·복구 경계

Artifact 선봉인 → Writer 검증 → READ COMMITTED + Run별 Advisory Lock → 전체 Projection → 검증 Receipt → commit 순서다. 같은 Receipt/Hash면 반환, 다른 Hash면 충돌한다. Row Count·PSI 이월 검사는 SQL 초안에 있고, 전체 품목/후보 집합·정책 Coverage·원문/모델/Scenario Hash 검사는 후속 Writer에 남긴다.

Receipt 없는 Context commit, Receipt 이후 INSERT, 기존 Row UPDATE/DELETE/TRUNCATE는 차단하는 초안이다. 실패하면 DB Projection 전체를 Rollback하고 기존 봉인 Artifact는 보존한다. 실패/차단 증적은 기존 Run Event와 Artifact를 사용하며 불완전한 PSI를 성공 Table로 게시하지 않는다.

`CALCULATION_ONLY`는 정식 UUID Run Claim이 아니므로 이 Run FK에 넣지 않는다. 기존 영속 Artifact 경로로 유지한다. 현재 V100 계산 전용 결과가 새 테이블에 적재됐다는 의미가 아니다.

`publication_receipt` 이름은 매핑을 유지했지만 의미는 **DB Evidence Projection 검증 완료**다. 업무 게시·발주 Receipt가 아니다. 기존 Ledger Evidence Status·Effective Run CAS·Cycle 집계·자동 주문을 변경하지 않는다. REVIEW/SHADOW는 권한 상승이 불가능하며 원 입력/Claim Gate가 거짓이면 참으로 만들 수 없다.

## 검증 결과와 미완료 범위

Catalog 구조·PK/FK 대상, Python/SQL 생성물 정합성, NULL·0행·비용 미가용·무수요·Review·미확인 입고·PSI 보존식 Negative Test를 Offline으로 검증한다. pglast 구문 분석과 SQLite CHECK 평가를 실제 PostgreSQL Trigger·FK·동시성 Canary로 부르지 않는다. 실제 DB 검증은 별도 승인이 필요하다.

신규 SQL은 Platform `infra/migrations/drafts/UNASSIGNED_dsim_inventory_evidence_v1.sql`이다. 번호/적용 Ledger가 없고 Review Flag가 없으면 중단하며 마지막은 항상 ROLLBACK이다. 이번에는 Migration 074~078 재실행이나 079 적용을 하지 않는다.

이번 로컬 결과: InventoryEngine 전체 Unit·Contract·Offline Integration **835 passed / 9 skipped / 669 subtests passed**, 기존 Framework 경고 3건. Platform 관련 Migration **114 passed**(신규 물리 초안 57건 포함), Catalog byte·SQL 생성물 대사·Ruff/Format·pglast 구문 검증 통과. 실제 PostgreSQL 실행은 미수행이다.

## 남은 작업 순서

**다음 작업 — InventoryEngine·Platform Backend의 Projection Writer/Verifier를 Offline으로 구현한다.**

- Catalog Serializer·동일/다른 Hash 멱등 사례·Chunk 누락/중복·Read-back Golden을 만든다. Run/입력/후보 완전성과 원 Artifact 재검증이 완료 조건이다. Reader 권한 계약은 공통 계약 고정 후 병렬 준비할 수 있다.

**승인 필요 — 개발 PostgreSQL의 Rollback Canary·동시성·권한을 검증한다.**

- 실제 대상 Schema/Unique/FK/Index/Role을 읽기 대사하고 적용 후보를 번호가 있는 Migration으로 정리한다. DB 실행은 별도 승인 후 진행한다.

**후속 작업 — 필요한 Table만 적용하고 기존 Effective Run 조회와 통합한다.**

- 정책 승인/배정 Master와 DB 업무 게시 Outbox는 별도 계약으로 남긴다. 보존·복구 기간 및 Legacy 정량 합격 기준도 이번 초안에서 임의 확정하지 않는다.
