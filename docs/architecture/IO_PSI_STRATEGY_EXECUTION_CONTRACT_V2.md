# PSI·전략 실행 계약 V2

## 상태

- 결정 상태: 계약 확정 및 실제 동일 Run 로컬 Orchestrator 구현 완료
- 실행 계약: `inventory-strategy-execution-plan-v1` `1.1.0`
- 결과 계약: `inventory-result-bundle-v1` `1.0.0`
- Canonical Context Binding: `inventory-canonical-context-binding-v1` `1.0.0`
- 결과 Source Key: `inventory.result_bundle`
- 운영 전략: `MATHEMATICAL`
- Shadow Challenger: 승인된 `DEEP_RL/PPO`

이 문서는 PSI 공식과 보충 전략을 분리하면서도 한 Run에서 비교 가능한 결과를 만드는
계약을 정의한다. 저장소와 무관한 실제 전략 Orchestrator, Child Artifact, 로컬 SQLite 기반
Durable Writer·Reader/Verifier·Outbox와 Offline Runtime Handler를 구현했다. 운영 Artifact
Store와 `TB_IO_*` 게시 테이블, Runtime 배포는 아직 구현 범위가 아니다. Migration 074는
개발 PostgreSQL에 적용했고 V2 Plan·Config·분류 Snapshot을 실제 Claim에 고정해
Attempt·Input Binding·Retry·CAS를 검증했다.

## 결정

PSI 재고 이동 공식은 모든 전략이 공유한다. 전략은 PSI 공식을 바꾸지 않고 신규 보충
행동만 제안한다. 한 Run은 동일한 Canonical Input, Site Binding, 품목별 Effective Policy와
Base Scenario를 사용하여 다음 결과를 만든다.

1. `BASELINE_PSI`: 신규 권고 입고 없이 계산한 비교 기준이다.
2. `RECOMMENDED_PSI + OPERATIONAL + MATHEMATICAL`: 현재 유일한 운영 결과다.
3. `RECOMMENDED_PSI + SHADOW + DEEP_RL`: 승인된 PPO 모델의 비교 결과다.
4. `STRESS_PSI + EVIDENCE_ONLY`: 입고 지연 등 봉인된 Stress Scenario의 근거 결과다.

화면에서 사용하는 `MATHEMATICAL_RECOMMENDED_PSI`,
`PPO_SHADOW_RECOMMENDED_PSI`, `STRESS_*_PSI`는 위 세 축으로부터 파생하는 표시 코드다.
별도의 업무 상태로 저장하거나 Hash에 중복 포함하지 않는다.

| 축 | 값 | 의미 |
| --- | --- | --- |
| `result_kind` | `BASELINE_PSI`, `RECOMMENDED_PSI`, `STRESS_PSI` | 무엇을 계산했는지 |
| `execution_role` | `OPERATIONAL`, `SHADOW`, `EVIDENCE_ONLY` | 결과를 어떻게 사용할지 |
| `strategy_type` | `NONE`, `MATHEMATICAL`, `DEEP_RL` | 어떤 보충 정책이 행동을 만들었는지 |
| `scenario_id` | `BASE` 또는 봉인된 Stress ID | 어떤 환경 조건에서 계산했는지 |

Baseline은 `HOLD` 전략이 아니다. Baseline에는 전략 개입 자체가 없으므로
`strategy_type=NONE`, `scenario_id=BASE`, `execution_role=EVIDENCE_ONLY`로 고정한다.

## Strategy Execution Plan

Platform은 Canonical 조립 전에 Run별 Strategy Execution Plan을 봉인한다. Plan에는 Site
Binding이나 Canonical Input Hash를 넣지 않는다. Site Binding이 Plan Hash를 포함하므로
Plan이 다시 Site Binding을 포함하면 순환 Hash가 되기 때문이다.

Plan이 봉인하는 값은 다음과 같다.

- `strategy_execution_plan_id`
- `classification_config_hash`
- `effective_policy_content_hash`
- `base_scenario_content_hash`
- `operational_strategy`
- `shadow_challenger_bindings`
- `stress_scenario_bindings`
- 예상 결과 계약 Key와 Version
- Plan `content_hash`

`operational_strategy`는 `MATHEMATICAL`만 허용하며 Implementation ID, Version,
Implementation Content Hash, 정규화한 Replenishment Config Hash와 수학 정책 입력
Snapshot Binding을 모두 고정한다. Replenishment Config Hash는 순환 결합을 피하기 위해
`canonical_input_hash`와 별도 봉인되는 `strategy_input_binding`만 제외하고 실행 설정 전체를
Hash한다. 따라서 Item Control이나 정책 History가 바뀌면 같은 Plan으로 실행할 수 없다.

Shadow Challenger는 다음 조건을 모두 만족해야 한다.

- `strategy_type=DEEP_RL`
- `algorithm=PPO`
- Challenger ID, Implementation ID·Version·Hash 존재
- Model ID·Version·Hash 존재
- 승인 Reference 존재

입력 순서는 의미가 없다. Challenger는 Challenger ID와 Model Identity 기준으로,
Stress Scenario는 Scenario ID와 Hash 기준으로 정렬한 뒤 Hash를 계산한다. 동일
Challenger ID 또는 동일한 Implementation Hash·Model Hash·승인 Reference 조합은
중복으로 거부한다.

Stress Scenario는 `scenario_contract_key`, Version, Content Hash와 실행 Runner의
Implementation ID·Version·Content Hash를 함께 봉인한다. Runtime에서 해소된 Runner는 이
세 값이 모두 정확히 같아야 한다. Scenario Content Hash는 Scenario ID·계약 Key·Version,
Payload Content Hash와 결정론적 Seed를 함께 Hash해 파생한다. 해소된 Payload JSON의 실제
Hash와 Seed도 Plan 값과 일치해야 한다. Runner는 매 Replay마다 새 Instance를 만들고 같은
입력·Payload·Seed로 두 번 실행해 동일 결과인지 확인한다.
`BASE`는 Base Scenario 예약 ID이므로 Stress ID로 사용할 수 없다.

Cost Profile은 Profile ID, 계약 Key·Version, Payload Content Hash, 파생 Profile Hash,
Source Type과 승인 Reference를 함께 봉인한다. `DEVELOPMENT_SYNTHETIC`과
`AUTHORIZED_SOURCE`를 구분하며 Runtime은 Registry가 해소한 Canonical Payload의 Hash와
Plan의 모든 식별자가 같을 때만 비용 계산을 허용한다. Profile이 없는 기존 Plan은 Hash에서
새 `null` 필드를 제외해 기존 1.1.0 Hash를 유지한다.

## Inventory Result Bundle

기존 Platform의 단일 `inventory_result_snapshot_id`와
`inventory_result_content_hash`는 각각 Result Bundle ID와 Bundle Content Hash를
의미한다. Bundle은 다음 실행 경계를 독립적으로 식별한다.

Result Bundle ID는 Handler가 임의 UUID를 발급하지 않는다. 다음 결정론적 식별자를
사용한다.

```text
result_bundle_id
= "IOB-" + SHA256({contract_id, engine_run_id, attempt_no})
```

ID 계산에는 Bundle ID나 Bundle Content Hash를 넣지 않으므로 순환 Hash가 없다. 동일
Attempt를 동일 입력으로 재실행하면 같은 Bundle ID와 Content Hash를 재생성해야 한다.
성공 Terminal Event 뒤 Publish만 실패한 경우에는 새 Bundle을 만들지 않고 동일 Terminal
Event를 재생한 뒤 Publish를 다시 시도한다.

현재 Offline Runtime에서 이 재생성 조건은 Handler의 결정론적 구현 의무다. 결정론적
Bundle ID만으로는 Child Artifact Reference 등 Bundle 본문의 동일성까지 보장할 수 없다.
운영 물리 구현에서는 검증된 Bundle ID·Hash를 Terminal Event 전에 Durable Outbox에
저장하고, Publish 재시도 시 Handler를 다시 실행하지 않고 저장된 Bundle Pointer를
재사용해야 한다. Durable Outbox는 이번 로컬 계약 구현 범위 밖의 후속 작업이다.

- Tenant, Project, Planning Cycle·Revision, Site Execution, Engine Run, Attempt
- Plan ID와 `company_cd/subs_cd/plant_cd/site_cd` Scope
- `canonical_input_hash`, `site_binding_hash`
- Strategy Execution Plan ID·Hash
- Classification Config Hash와 Effective Policy Content Hash
- Cost Profile Content Hash. 미결합 Run은 `null`, 결합 Run은 Plan의 Profile Hash와 일치
- Child Result와 비교 지표
- 자동 게시·발주 가능 여부

성공한 Child Artifact에는 Artifact Contract Key·Version, Reference, Content Hash,
Row Count가 모두 있어야 한다. 실패·건너뜀 결과도 Child로 남기며 Failure Reason을
필수로 기록한다. Action이 존재하는 성공 결과는 Raw Action, 제약 적용 후
Constrained Action, Adjustment Reasons의 Reference와 Hash를 모두 보존한다.

## 필수 결과와 Effective Pointer

Bundle에는 정확히 하나의 성공한 Baseline과 정확히 하나의 성공한 Mathematical
Recommended 결과가 있어야 한다. 두 결과 및 PPO Shadow는 모두 Plan의
`base_scenario_content_hash`를 사용한다.

`effective_child_result_id`는 Mathematical Recommended 결과만 가리킬 수 있다.
PPO와 Stress 결과는 자동 게시 또는 Effective Pointer의 대상이 될 수 없다.

Plan에 PPO Challenger가 있으면 Bundle은 각 Challenger의 성공 또는 실패 Child를
정확히 하나씩 보존한다. PPO 실패는 Mathematical 결과를 실패시키지 않는다. 즉,
PPO가 실패해도 Mathematical 결과, 입력 무결성, 품목 정책 Gate가 유효하면 Bundle은
게시 가능하다.

Plan에 Stress Scenario가 있으면 Bundle은 각 Scenario마다 성공·실패·건너뜀 Child를
정확히 하나 보존한다. Stress 결과는 항상 `MATHEMATICAL + EVIDENCE_ONLY`다.

Child 상태는 다음과 같이 닫힌 규칙으로 처리한다.

| Child | 허용 상태 | Failure Reason 규칙 |
| --- | --- | --- |
| Baseline | `SUCCEEDED`만 | 실패하면 Bundle 자체를 만들지 않음 |
| Operational Mathematical | `SUCCEEDED`만 | 실패하면 Bundle 자체를 만들지 않음 |
| PPO Shadow | `SUCCEEDED`, `FAILED` | `SKIPPED` 금지 |
| Stress Evidence | `SUCCEEDED`, `FAILED`, `SKIPPED` | `SKIPPED`는 `STRESS_SCENARIO_UNAVAILABLE`만 허용 |

Runner가 실행 도중 `STRESS_SCENARIO_UNAVAILABLE`을 예외로 반환해도 미등록 Scenario로
해석하지 않는다. 이는 `STRESS_EXECUTION_FAILED`로 기록해 Optional Child 실패 격리를
유지한다. 성공 Child는 Artifact와 Row Count가 필수이고, 실패·건너뜀 Child에는 PSI·Action
Artifact를 붙일 수 없다. `SUCCEEDED`의 `row_count`는 반드시 1 이상이어야 하며, 0행 결과는
성공으로 봉인하지 않는다. `FAILED`·`SKIPPED`는 `row_count`와 Artifact Reference를 모두
`null`로 유지한다.

## 비교 지표

Baseline과 Baseline 이외의 모든 Child 사이에 다음 Delta를 기록한다.

- Cost Delta
- Service Level Delta
- Backorder Quantity Delta

비용 Profile이 결합되지 않았다면 비용을 0으로 꾸미지 않고 다음처럼 기록한다.

```text
status      = NOT_AVAILABLE
reason_code = COST_PROFILE_NOT_BOUND
value       = null
```

Cost Profile이 Plan에 결합되면 Runtime은 PSI의 주차별 On-hand·Backorder와 공통 Guard가
확정한 발주 건수·수량으로 보유비·결품비·고정 발주비·변동 발주비를 다시 계산한다. Bundle의
`cost_profile_content_hash`는 Plan Profile Hash와 같아야 하고 성공 Child의 Cost Delta는
`AVAILABLE`이어야 한다. Profile이 없는 기존 Run은 위 `COST_PROFILE_NOT_BOUND`를 유지한다.
결과 생성 후 임의 비용 기준을 붙이거나 다른 Profile Hash로 바꾸는 것은 모두 차단한다.

성공 결과의 Backorder Delta는 항상 산출한다. 전 기간 Demand가 0이면 Service Level은
수학적으로 정의되지 않으므로 1.0으로 대체하지 않고
`NOT_AVAILABLE / NO_DEMAND_IN_HORIZON`으로 기록한다. 실패 또는 건너뜀 Child의 모든
Delta는 각각 `CANDIDATE_RESULT_FAILED` 또는 `CANDIDATE_RESULT_SKIPPED`로 남긴다.

서비스수준과 종료 수량은 외부 비교값을 받지 않고 실제 PSI Row에서 다음과 같이 산출한다.

```text
horizon_demand_qty
= SUM(confirmed_customer_order_qty + net_forecast_qty)

on_time_fulfilled_qty
= SUM(fulfilled_confirmed_customer_order_qty + fulfilled_forecast_qty)

projected_service_level
= on_time_fulfilled_qty / horizon_demand_qty

ending_backorder_qty
= 품목별 마지막 Bucket의 backorder_close_qty 합계

ending_available_inventory_qty
= 품목별 마지막 Bucket의 eoh_qty 합계

ending_on_hand_inventory_qty
= 품목별 마지막 Bucket의 on_hand_eoh_qty 합계
```

과거 Backorder를 뒤늦게 충족한 `fulfilled_backorder_qty`는 정시 서비스수준 분자에 포함하지
않는다. Bundle 1.0.0에는 종료재고 Delta 필드가 없으므로 두 종료재고 비교값은 Child 상세
비교 Evidence에 보존하고, Bundle에는 Cost·Service Level·Backorder Delta만 투영한다.
현재 V1 실행단위는 EA 단일 UOM이므로 Scalar 비교가 가능하다. 서로 다른 UOM이 섞이면
수량을 임의 합산하지 않고 Scalar Bundle 생성을 차단하며 UOM별 Child Metric은 유지한다.

## 동일 Run 로컬 Orchestrator

`RunPsiBundleUseCase`는 하나의 Canonical Input을 한 번 준비한 뒤, 준비 결과의 자체 Hash만
믿지 않고 Canonical에서 기대되는 결정론적 Projection과 전체 내용을 정확히 대조한다.
내용을 바꾼 뒤 Prepared Hash만 다시 만든 입력도 Child 실행 전에 차단한다. 검증된 불변
Prepared Input은 각 Base Child에 독립 복제본으로 전달한다. downstream의 `prepared_input`
인자는 외부 Wire가 아니라 이 검증을 통과한 내부 실행 capability다. 실행 순서는 다음과 같다.

1. Baseline PSI를 계산하고 PSI Child Artifact를 봉인한다.
2. Operational Mathematical Recommended PSI와 행동 Evidence를 계산·봉인한다.
3. Plan에 등록된 승인 PPO Challenger를 같은 Canonical Hash와 Base Scenario에서 실행한다.
4. 등록된 Stress Scenario Runner는 수요·확정 입고만 변형한 Prepared Input을 반환한다.
   Orchestrator가 그 입력으로 실제 Mathematical 전략·공통 Guard·PSI를 실행한다.
5. 실제 PSI Row에서 비교 지표를 계산한다.
6. 모든 Child, 비교값과 Effective Pointer를 Result Bundle로 조립하고 봉인한다.

Baseline 또는 Mathematical 계산 실패는 필수 결과를 만들 수 없으므로 전체 호출을 실패시킨다.
PPO 또는 Stress 실행 실패는 해당 Child만 `FAILED`로 기록하고 Mathematical 결과와 Effective
Pointer를 유지한다. Plan에 Stress가 있으나 검증된 Runner가 해소되지 않은 경우에는 Math
결과를 Stress로 재사용하지 않고 `SKIPPED / STRESS_SCENARIO_UNAVAILABLE`로 남긴다.

Stress Runner는 PSI Row, 행동, 실행 결과 또는 전략을 반환할 권한이 없다. 반환형은 정확한
`PreparedInventoryInput`이어야 하며, Context·Calendar·Master·Policy·기초재고·Cut-off
Reconciliation·Canonical Snapshot은 Base와 완전히 같아야 한다. 수요에서는 Forecast와
고객 확정주문 수량만, 입고에서는 예정일·수량과 그로부터 파생되는 주차·포함수량·제외사유만
변경할 수 있다. 수요 Netting 산식과 입고 상태별 포함 규칙은 Orchestrator가 다시 검증한다.
Scenario Payload Hash·Seed·Scenario Hash와 Runner ID·Version·Hash도 Plan Binding과 정확히
대사한다. 따라서 외부 Runner가 임의 전략의 결과를 `MATHEMATICAL`로 표시하거나 Base BOH를
바꿔 Stress 성공 Child를 만드는 것은 허용되지 않는다.

PPO 실행 전에는 Plan의 Challenger ID, Strategy·Algorithm, Implementation ID·Version·Hash,
Model ID·Version·Hash와 승인 Reference를 실제 해소 결과와 대사한다. PPO Shadow의 자동
게시·발주 Gate는 항상 `false`다. 모든 성공 Child는 동일한
`company/subs/site/item/uom/seq/yyyyww/start_date/end_date` Universe를 가져야 하며,
품목별 Bucket 날짜·1~7일 범위·연속성도 검증한다. `yyyyww`는 ISO 주차로 재계산하지 않고
봉인된 업무 Calendar 값을 유지한다. Canonical Hash는 Recommendation 실행 Hash가 아니라
최초 `CanonicalInputRequest.input_hash`를 사용한다.

PSI Artifact와 원 행동·제약 행동·조정 사유 Artifact는 정렬된 실제 Payload로 Row Count와
Content Hash를 직접 계산한다. ID와 논리 Reference는 Run·Attempt·입력·Scenario·전략·모델
Binding에서 결정론적으로 파생되므로 동일 입력 재실행 결과가 같다. 이 단계의 Reference는
아직 논리 Reference이며 실제 Object Storage 존재성이나 바이트 Hash 검증을 뜻하지 않는다.

### Action Evidence 권위와 재검증

성공한 Recommended/Stress Child의 Raw Action Artifact에는 각 판단 Row뿐 아니라 해당 판단을
재구성할 수 있는 `inventory-action-observation-source-v1`을 정확히 하나 저장한다. 이 Source는
검증된 Prepared Input의 Context·Calendar·Demand·Position·Policy·Receipt Decision·수량 규칙과
실행의 Item Control·허용 행동·Capacity Mode·Strategy·승인 Reference·Effective Policy를
포함하며 자체 Content Hash로 봉인한다. Constrained Action과 Adjustment Artifact에는 이
Source를 중복 저장하지 않고 `null`로 고정한다.

Reader/Verifier가 수행할 의미 검증은 다음과 같다.

- PSI Row와 Observation Row는 닫힌 필드 계약으로 검증하며 알 수 없는 필드를 거부한다.
- Observation은 봉인된 Source에서 품목·주차별로 다시 투영하고 전체 내용을 정확히 비교한다.
- `decision_id`는 Observation Input Hash·Item ID·`yyyyww`에서 다시 파생한다.
- Proposal을 공통 `validate_action` Guard에 다시 넣어 제약 적용 수량과 Reason Code를 재계산한다.
- 권고 입고는 PSI의 Pending Supply·도착주차와 대사하고, 각 주차의 재고·수요·Backorder
  보존식을 다시 계산한다.
- Physical Capacity 초과량은 Source Policy의 Capacity에서 다시 계산한다.
- Effective Policy Admission은 저장된 V2 정책·Config Hash·실행 목적·Strategy·Model 승인으로
  공식 `admit_effective_item_policy`를 재실행해 정확히 같은지 확인한다.

`build_psi_child_artifact`와 Source Sealer는 외부 요청을 수락하는 보안 경계가 아니라 검증된
Orchestrator 내부용 Builder다. 후속 Durable Reader는 Artifact 안의 자기서술 Source만 믿지
않고 Run Input Binding에 고정된 Prepared/Execution Artifact를 별도로 읽어 같은 Source와
Admission을 재구성해야 한다.

## Hash와 재현성

Plan과 Bundle은 자기 `content_hash`를 제외한 정규화 JSON의 SHA-256을 사용한다.
Bundle 검증기는 `result_bundle_id`가 Engine Run ID와 Attempt Number로부터 위 규칙대로
파생됐는지도 별도로 확인한다.
다음 List는 Hash 계산 전에 안정적으로 정렬한다.

- Shadow Challenger Binding
- Stress Scenario Binding
- Child Result
- Baseline 비교 결과

Bundle 검증은 Plan ID·Hash, Config Hash, Effective Policy Hash, Base/Stress Scenario
Hash, PPO Model Hash를 교차 확인한다. Content Hash만 맞고 이 연결 중 하나가 다르면
실패로 처리한다.

현재 동일성 검사는 결정론적이고 부작용 없는 Strategy/Stress Transformer라는 내부 구현 전제에서
로컬 두 번 Replay까지 확인한다. 서로 다른 Worker나 시간대의 재실행까지 ID만으로 증명하지
않는다. 영속 계층은 Semantic Artifact Reference를 Write-once Key로 사용해 같은 Reference와
같은 Hash는 멱등 성공, 같은 Reference와 다른 Hash는 충돌로 거부하고 CAS로 Outbox Pointer를
게시해야 한다.

JSON Schema는 필드·상태·필수 Artifact·자동화 Gate 같은 문서 내부 규칙을 사전 검증한다.
Canonical/Plan/Policy/PSI/Action 간 Hash와 수량 보존식처럼 여러 문서를 함께 봐야 하는 의미
검증의 권위는 Python Contract Verifier다.

## Runtime Wire

기존 V1 Claim은 Strategy Plan 없이 기존 Canonical Hash를 유지한다. Inventory Policy
2.0과 Classification Binding을 사용하는 V2 Claim은 봉인된
`strategy_execution_plan`과 `canonical_context_binding`을 필수로 포함한다. V1 Claim은
`canonical_context_binding`을 내보내지 않으며, 입력에서 필드 생략과 명시적 `null`은
동일한 기존 V1 Hash로 정규화한다.

Canonical Context Binding은 다음 값을 봉인한다.

- `plan_type`, `plan_yyyyww`, `plan_start_date`, `plan_end_date`
- `master_as_of_date`, `master_snapshot_revision`
- `business_timezone`, UTC로 정규화한 `inventory_cutoff_at`
- `inventory_source_watermark`
- 정규화·UOM 정렬된 Canonical `quantity_rules` 배열의
  `quantity_rules_content_hash`
- 위 본문 전체의 `content_hash`

`business_timezone`은 설치된 IANA ZoneInfo여야 한다. Timezone과 Watermark 문자열은
앞뒤 공백 및 C0/DEL 제어문자를 허용하지 않으며, `plan_start_date <= plan_end_date`를
강제한다. 날짜 Wire는 `YYYY-MM-DD`, Cut-off Wire는 UTC `Z` 표기로 정규화한다.

Site Binding은 기존 Scope·Source Binding·Policy Admission·Strategy Plan Hash와 함께
`canonical_context_binding_hash = canonical_context_binding.content_hash`를 포함한다.
따라서 V2 Context나 수량 규칙을 바꾸려면 Claim과 Site Binding 자체가 달라져야 한다.

V2 Claim과 Runtime은 다음을 검증한다.

- Plan의 Classification Config Hash = Claim Config Hash
- Plan의 Effective Policy Content Hash = Classification Binding Hash
- Site Binding Hash가 Plan Content Hash를 포함
- Canonical Context Binding의 자기 Hash가 본문과 일치
- Handler가 실제 사용한 `CanonicalInputRequest`의 Context 전 필드가 Binding과 일치
- Handler가 실제 사용한 정규화 `quantity_rules` 배열 Hash가 Binding과 일치

V2 Runtime Handler 결과는 실제 조립에 사용한 불변 `CanonicalInputRequest`와 봉인된 Result
Bundle 전체를 함께 반환한다. Handler가 선언한 `canonical_input_hash` Scalar는 자기증명이므로
신뢰하지 않으며 V2 결과 계약에서 사용하지 않는다. Worker는 성공 Terminal Event 또는
Publish Callback보다 먼저 Canonical 입력을 독립적으로 다시 정규화하고 다음을 검증한다.

- 모든 Snapshot의 상태가 `SEALED`인지
- `row_count`와 실제 Row 수가 같은지
- `content_hash = SHA256(snapshot_content)`인지
- Canonical Input Binding이 Snapshot ID·Hash와 같은지
- Run·Cycle·Site·Plan·Demand·Config와 Source Binding이 Platform Claim과 같은지
- Context Binding과 수량 규칙 Hash가 Platform Claim과 같은지

Worker는 이 검증을 통과한 `CanonicalInputRequest.input_hash`를 직접 산출해 Bundle을
검증한다. 이후 Run·Attempt·Cycle·Site·Plan·Config·Policy·Gate 결합을 확인하고, 기존
Result ID·Hash가 검증된 Bundle의 `result_bundle_id`·`content_hash`와 일치할 때만 Terminal
Event와 Publish를 허용한다. Bundle과 Handler Scalar를 함께 `0...0` 같은 값으로 바꾸는
방식으로는 검증을 통과할 수 없다.

검증이 완료된 V2 결과는 기존 Result ID·Hash와 함께 아래 값을 Callback에 포함한다.

```text
inventory_result_contract_key     = inventory.result_bundle
inventory_result_contract_version = 1.0.0
```

이 경우 Publish Wire의 Contract ID는 `inventory-engine-run-publish-v2`다. V1 Result는
두 필드를 보내지 않으며 기존 `inventory-engine-run-publish-v1`을 유지한다.

## Effective Policy 2.0 호환성

품목별 Effective Policy 2.0의 `effective_strategy`는 기존 Hash와 DB Projection을
깨뜨리지 않기 위해 유지한다. 다만 이 필드는 새 Run에서 Operational 전략과 Shadow
전략을 동시에 결정하는 권위 필드가 아니다.

`effective_strategy`의 새 의미는 **deprecated compatibility alias**다. 2.0 데이터에는
기존과 같이 저장·Hash하지만, Run 전략 권위는 Strategy Execution Plan의
`operational_strategy`와 `shadow_challenger_bindings`가 가진다. 다음 Major Contract에서
품목 정책의 Strategy Hint와 Run 전략 Binding을 별도 필드로 분리한다.

## 대안과 결과

단일 `effective_strategy`로 Math와 PPO 중 하나만 고르는 방식은 구조가 단순하지만,
동일 입력에서 운영 결과와 Challenger를 함께 비교할 수 없어 채택하지 않았다. Child를
각각 독립 게시하는 방식은 부분 게시와 Effective Pointer 혼동 위험이 커서 채택하지
않았다.

Result Bundle 방식은 Manifest와 검증 로직이 추가되지만 다음 이점을 갖는다.

- 동일 입력·Scenario 비교를 증명한다.
- PPO 실패를 운영 결과와 격리한다.
- Effective 결과가 Math임을 구조적으로 강제한다.
- 향후 ML Challenger와 Stress Scenario를 게시 계약 변경 없이 확장할 수 있다.

## 가정과 위험

현재 계약은 한 Engine Run이 한 Site Scope를 처리하고, 수학적 전략이 운영 기준선이라는
가정에 기반한다. PPO는 승인된 Model Binding이 있더라도 Shadow에만 머문다.

로컬 Adapter는 Artifact 존재성과 바이트 Hash, 동일 Semantic Reference의 Write-once 충돌,
Result-ready Outbox를 검증한다. 다만 SQLite는 운영 Object Storage/PostgreSQL 경계를 대신하지
않으므로 외부 저장소 간 선봉인·원자 게시·보상 복구는 후속 주입형 Adapter가 확인해야 한다.
로컬 Stress 이중 Replay는
같은 Process 안의 비결정성을 탐지하지만 외부 서비스나 다른 Worker의 결정론까지 보증하지
않으므로 운영 Runner는 순수 함수형 계약과 영속 Hash 충돌 검사가 필요하다. Stress Scenario와 Challenger 수가 늘면 Manifest와
비교 행 수도 증가하므로 현재 상한(Challenger 32, Stress 64, Child 512)을 운영 자료로
재검증해야 한다. 현재 개발 Cost Profile은 합성 단가이므로 계약·E2E 검증에는 사용할 수 있지만
운영 비용 우열의 근거로 사용할 수 없다.

Execution Plan 1.1.0은 수학 정책 입력·실행 설정, Stress Runner와 선택적 Cost Profile을 봉인한 현재
InventoryEngine 계약이다. dsai-platform Backend Model·Hash·Fixture·계약 문서와 승인 Reference
형식도 같은 1.1.0으로 정합화했고, 두 저장소의 Plan Golden Hash가 일치한다. 이 계약은 DB
Migration 074의 Planning Cycle Source 계약 Version과 별도이므로 Migration은 변경하지 않았다.

Canonical Context Binding은 전달된 Claim과 Runtime 실제 입력 사이의 변조·Drift를 막는
봉인 계약이지, 각 값의 운영 Source 권위를 새로 증명하는 계약은 아니다. 계획기간과 Master
기준은 봉인된 Planning Input에서 파생한다. 반면 `business_timezone`,
`inventory_cutoff_at`, `inventory_source_watermark`는 실제 Inventory Position Source에서,
`quantity_rules`는 권위 UOM Source에서 Platform이 독립 조회해 대사해야 한다. 이 Source
Authority 연결은 아직 후속 Platform 통합 범위다. 따라서 현재 완료 범위는 Claim 봉인과
Runtime 변조 방어이며 실제 Inventory Position/UOM 권위 Source 연결 완료로 해석하지 않는다.

## 후속 검증과 남은 범위

이번 단계에서는 Python Contract, JSON Schema, Runtime Wire, 실제 PSI Child Artifact와
동일 Run Offline Orchestrator가 같은 의미를 검증한다. 검증 결과는
[동일 Run Orchestrator 검증 기록](IO_PSI_ORCHESTRATOR_VERIFICATION.md)을 따른다. 다음 작업은
별도 승인 경계를 따른다.

1. 권위 회계 Cost Profile과 운영 승인 Stress Catalog를 개발 Registry와 교체
2. Platform의 주입형 Artifact Verifier와 운영 Artifact 저장소 연결
3. Platform에서 Inventory Position·UOM 권위 Source를 독립 조회하고 Canonical Context
   Binding의 Timezone·Cut-off·Watermark·Quantity Rules와 대사
4. `TB_IO_*` Publication/Evidence 물리 설계 확정
5. Scheduler→공용 Runtime Dispatch·Callback 배포와 Result Bundle 실제 E2E
6. V2 `REVIEW` 결과의 사람 승인·게시 상태와 CAS Workflow
