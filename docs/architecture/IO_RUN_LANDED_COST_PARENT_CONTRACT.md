# Runtime Parent와 개발 Simulation 비용 연결 계약

기준일: 2026-09-29. InventoryEngine `inventory_engine_dev`와 dsai-platform
`codex/inventory-psi-strategy-v2`의 미커밋 로컬 구현 기준이다.

## 현재 기준선 — 완료(Offline)

Platform의 실제 Claim/Dispatch 계약을 사용해 PSI 결과와 Landed Cost Child를 하나의
Run Result Manifest로 연결했다. 원본 저장·재계산·게시 재시도와 Platform HTTP 조회를
Offline으로 검증했다. 실제 개발 PostgreSQL 쓰기, Migration, 공용 Runtime 배포는 하지 않았다.

이는 **개발용 비용 비교**이며 MEIO 경로 최적화나 운영 원가 확정이 아니다. 기존
[독립 Landed Cost Child 계약](IO_LANDED_COST_CHILD_ARTIFACT_CONTRACT.md)의 후속 구현으로,
이번 문서는 그 문서에 남아 있는 Runtime/Simulation 연결의 완료 범위를 별도로 기록한다.
기존 기준선·작업 계획 문서는 자동 갱신하지 않았다.

## 1. 입력과 Version 경계

- Strategy Execution Plan `1.2.0`은 `landed_cost_binding`과 `cost_profile_binding`을 필수로 한다.
  기존 `1.1.0`에는 새 필드를 출력하지 않아 기존 정규화 JSON과 Hash를 유지한다.
- `inventory.landed_cost.run_input` / `1.0.0`은 Backend/Adapter가 봉인하는 비용 입력이다.
  Snapshot ID/Hash, Tenant/Project, Company/Subsidiary, Origin/Destination, Network Revision,
  Trade Cost Revision Set ID/Hash, 적용일·판단 시점, 통화, 품목별 가격 Template를 포함한다.
- Scope는 C100의 **V100 → V101/JP·V102/CN·V103/SA·V104/AE**다. IO Claim의 Site는
  목적 거점이고 비용 Source의 Origin은 V100이다. 이전 Plan의 기존 Scope 규칙은 유지한다.
- 적용일은 Claim의 `master_as_of_date`와 같아야 한다. Trade Cost Authority는 Plan에 지정한
  승인된 개발 Revision Set을 정확한 ID/Hash로 조회한다. 최신 Set이나 목적 Site의 Set을
  대신 선택하지 않는다. Revision Set Binding 없이 Repository가 Attempt를 생성할 수 없다.
- 품목 Template는 EA 1개 기준이며 각 주문의 승인 수량으로 확대한다. Run ID·Attempt·Canonical
  Input Hash는 Runtime Claim으로 다시 고정한다. 사용자가 Hash를 직접 입력하는 UI는 없다.
- `FileRunCostInputReader`는 정확한 파일 ID/Hash/Scope를 확인한다. 경로·심볼릭 링크·중복 JSON
  Key·8MB 초과 입력을 거부한다. 공유 Runtime용 Source Publisher/Gateway는 아직 미구현이다.

계약 Schema: [Run 비용 입력](../../schemas/inventory_run_cost_input.schema.json),
[Run Result Manifest](../../schemas/inventory_run_result_manifest.schema.json),
[PSI Bundle와 Plan](../../schemas/inventory_result_bundle.schema.json).

## 2. Parent 구조와 저장·복구

`inventory.run_result_manifest` / `1.0.0`은 Evidence 전용 Parent다. 기존 PSI Result Bundle
`1.0.0`과 `result_children`에는 비용 Row를 넣지 않는다.

```text
Run + Attempt의 Run Result Manifest
├─ 기존 PSI Bundle: Baseline / Mathematical / PPO Shadow / Stress
├─ 봉인 비용 입력 + Trade Cost Projection + 기존 Cost Profile
├─ PSI 후보별 승인 주문 → Shipment / Projection / Landed Cost Child
└─ 후보별 운영비·Net Landed Cost·총비용·Baseline 대비 차이
```

Parent는 Runtime Request·Canonical Input·Site Binding·Plan Hash와 PSI ID/Hash를 고정한다.
비용 Child에는 PSI Child ID, Item, 판단일, Decision ID, 승인 수량과 Reference/Hash를 기록한다.

`InventoryResultPipelineHandler`는 Prepare → PSI → 비용 계산 → 증적 봉인을 수행한다.
저장 전에 원본으로 비용과 Parent를 다시 계산한다. Parent·비용 원본·Child·PSI 원본과 기존
Run/Attempt의 단일 Publication Outbox를 **같은 SQLite Transaction**으로 저장한다.
다른 Hash의 마지막 Parent 충돌도 앞선 INSERT와 Outbox를 함께 Rollback한다.

게시 실패 시 같은 Attempt를 재개하면 이미 봉인된 Outbox와 원본으로 복구한다. 외부 비용
Source를 다시 조회하거나 새로 계산한 최신 입력으로 교체하지 않는다. 동일 Reference/Hash는
멱등 재사용하고 다른 내용은 충돌한다. Worker는 비용 Plan에 `run_result_verifier`를 필수로
요구해 저장 원본 재검증이 생략되지 않게 한다. 바뀐 총액에 새 Hash를 붙여도 통과하지 않는다.

## 3. Platform Parent 포인터와 조회

Runtime의 완료 Event·REVIEW Event·Publish 요청은 다음 네 필드를 함께 전달한다.

```text
run_result_manifest_reference
run_result_manifest_content_hash
run_result_manifest_contract_key = inventory.run_result_manifest
run_result_manifest_contract_version = 1.0.0
```

Reference는 `artifact:inventory:<engine_run_id>:<attempt_no>:run-result-manifest`다.
Platform은 기존 `dsai.engine_runtime_runs.metadata_redacted` JSONB에 Plan Version,
비용 입력 ID/Hash, Parent 필수 여부와 포인터를 기록한다. **신규 테이블·DDL은 없다.**
한 Run의 포인터는 최초 봉인 후 바꿀 수 없다. Version/필수 Marker 불일치, 일부 필드 누락,
다른 Run/Attempt, 포인터 교체를 거부한다. Site의 기존 유효 결과 포인터는 PSI Bundle을
계속 가리킨다. Parent는 이를 대체하지 않는다.

```text
GET /api/v1/engine-studio/inventory/runs/<engine_run_id>/result-reference?project_id=<project_id>
```

조회는 JWT의 Tenant, Project와 Run Scope를 검증하고 `engine_report.read` 권한을 요구한다.
API는 Parent의 Reference/Hash를 반환하며 임의 파일 경로나 Parent 본문을 읽어 주지 않는다.
실제 공유 Artifact Gateway와 Platform Callback Verifier의 배포 연결은 후속 작업이다.
현재 기본 Callback Verifier는 계속 차단 상태이고, Offline HTTP 테스트에서는 저장 원본을
재검증하는 구현을 주입했다. `REVIEW`의 근거와 Parent는 저장·조회되지만 자동 게시되지 않는다.

## 4. 개발 Simulation 비용 반영 규칙

모드는 `DEVELOPMENT_LANE_PRICING_PROXY`다. 비용은 전략의 원 행동이 아니라 공통 Guard를
통과한 **accepted_order_qty > 0**인 주문에만 계산한다. 주문이 없으면 Net Landed Cost 0은
정상 결과다. 부족한 Source를 0으로 간주하는 것과는 다르다.
가격을 붙이는 범위는 **새 권고 주문의 증분 비용**이다. 기초재고 취득원가와 기존 확정 입고의
구매비를 모두 재평가한 회계 총원가가 아니며, 기존 입고의 금액 평가 계약은 별도 범위다.

| 기존 Cost Profile | 이번 합산에서 허용하는 의미 |
|---|---|
| holding_cost_per_unit_week | 재고 보유비 |
| backorder_cost_per_unit_week | Backorder 비용 |
| fixed_order_cost | 발주 행정비 |
| variable_order_cost_per_unit | 주문 처리비만; 구매·운송·관세·세금 제외 |

입력에 위 성격을 명시해 봉인한다. 겹치는 비용 성격이나 미확인 성격은 거부한다.
Shipment 거래금액은 기존 계약대로 GOODS ONLY이며 직접 Charge와 Lane Charge의 중복도
기존 계산기가 차단한다. 환급 가능한 VAT를 뺀 `net_landed_cost_amount`에는 구매·운송·관세·
비환급 세금이 이미 들어 있다. 이를 Component별로 다시 더하지 않는다.

```text
후보별 총비용 = 기존 운영비 + 검증된 Net Landed Cost 합계
```

- 고정 Lane 비용은 **승인된 품목 주문 1건마다** 부과한다. Shipment 통합·묶음 발주는 아직 없다.
- 비용은 주문 승인 시점에 인식하며 입고일이 Horizon 밖이어도 제외하지 않는다.
- 비용 Child가 `CALCULABLE`이고 원본 재계산이 일치할 때만 총액에 사용한다.
  가격 Template 누락은 품목·판단일·수량·사유를 남긴다. 환율·관세·Lane 등 필수 Source 누락은
  진단 Child를 보존한다. 해당 후보 총액·비교 차이는 `null / NOT_AVAILABLE`로 둔다.
- Baseline·Math·PPO·Stress는 각각 별도 후보다. 후보 비용을 전부 합산해 회사 총비용으로
  해석하지 않는다. 비교에는 같은 통화와 봉인된 비용 입력을 사용한다.
- 기존 PSI Bundle의 비용 지표는 **기존 운영비만**이다. All-in 비용은 새 Parent의
  `simulation_metrics.total_cost`와 `simulation_comparisons.total_cost_delta`에 있다.
- 비용 계산은 PSI·행동·수학적 정책·PPO 추론이나 학습 보상을 변경하지 않는다.
  `operational_cost_eligible=false`를 강제한다.
- 한 Run의 전체 후보를 합쳐 승인 주문 10,000건, 비용 증적 64MiB를 초과하면 저장 전에
  차단한다. 현재 Projection 중복 저장 방식은 대규모 Full Load용 구현이 아니다.

개발 Golden에서 Math 승인 주문 25 EA는 구매 375,000 + 보험 37,500 + 처리비 75,000 +
운송 150,000 + 관세 28,125 = Net Landed Cost **665,625 KRW**다. 환급 VAT는 Net에서
제외하며 기존 운영비 1,250을 한 번 더해 **666,875 KRW**로 계산한다.
이는 합성 가격·세율이며 실제 국가 세율이나 운영 원가 주장이 아니다.

## 5. 검증 증적과 남은 순서

[검증 증적](evidence/run-cost-parent-offline-20260929.json)에 실행 환경과 결과를 기록한다.
기존 개발 환경은 유지하고 격리 환경에서 검증했다. 기존 개인 보고서, Platform 산출물과
DemandEngine의 환경·IDE 변경은 보존했다. 2026-09-29 당시 Commit·Push는 하지 않았다.

2026-10-01 InventoryEngine 전체 Offline 회귀를 다시 실행해 **622 passed, 7 skipped,
653 subtests passed**를 확인했다. 스킵 7건은 명시적으로 비활성화한 Network/Source
읽기 전용 외부 연동 테스트다. Ruff Check, Ruff Format Check(197개 Python 파일),
23개 JSON Schema 구조 검사와 `git diff --check`도 통과했다. 이 검증은 로컬
Unit·Contract·Offline Integration 기준이며, 개발 PostgreSQL E2E Write나 공용
Runtime 배포를 검증한 것으로 해석하지 않는다.

**개발 입력 Publisher와 검증 Gateway 연결 — 다음 작업, 직렬**

- dsai-platform Backend/InventoryEngine에서 봉인 가격 입력을 생성·저장하고 Planning Cycle
  Claim에 서버가 ID/Hash를 전달하는 경로를 연결한다. UI에는 Hash 입력을 요구하지 않는다.
- 공유 Artifact Reader와 Platform Callback Verifier를 연결해 실제 저장소의 원본 재검증이
  완료된 Run만 게시되도록 한다. PostgreSQL이나 공유 Runtime은 이 로컬 단계에서 변경하지 않는다.
- 비용 입력 Producer 계약과 원본 보관 위치에 의존하므로 현재 세션에서 직렬로 진행한다.

**Platform 변경과 기존 문서 기준선 정합화 — 후속 작업**

- dsai-platform `codex/inventory-psi-strategy-v2`의 Producer·Callback 변경은
  InventoryEngine 기준 Commit과 최신 `origin/develop`을 대조한 후 별도로 검증·Commit한다.
- 기존 아키텍처·작업 계획의 완료 상태는 공유 Artifact Gateway와 개발 입력 Publisher의
  실제 구현 여부를 확인하면서 갱신한다. 로컬 Offline 완료를 공용 Runtime 완료로 표시하지 않는다.

**실제 개발 통합 검증과 배포 — 승인 필요**

- 대상 개발 PostgreSQL과 공용 Inventory Runtime을 확정하고 실제 Claim·Event·Parent 조회·
  Publish/REVIEW·재시도를 검증한다. 실제 E2E DB Write와 공용 Runtime 배포는 별도 승인한다.
- 운영 원가 Source, Incoterms/FTA/신고 Replay, 과거 Calculator Hash별 Version Registry,
  비용을 사용하는 PPO 보상·MEIO Solver는 이번 완료 범위에 포함하지 않는다.
