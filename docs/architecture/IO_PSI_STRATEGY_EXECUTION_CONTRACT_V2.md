# PSI·전략 실행 계약 V2

## 상태

- 결정 상태: 계약 확정 및 로컬 검증 구현
- 실행 계약: `inventory-strategy-execution-plan-v1` `1.0.0`
- 결과 계약: `inventory-result-bundle-v1` `1.0.0`
- Canonical Context Binding: `inventory-canonical-context-binding-v1` `1.0.0`
- 결과 Source Key: `inventory.result_bundle`
- 운영 전략: `MATHEMATICAL`
- Shadow Challenger: 승인된 `DEEP_RL/PPO`

이 문서는 PSI 공식과 보충 전략을 분리하면서도 한 Run에서 비교 가능한 결과를 만드는
계약을 정의한다. 실제 전략 Orchestrator, `TB_IO_*` 게시 테이블, Migration 074 적용,
개발 DB Write와 Runtime 배포는 이 계약의 범위가 아니다.

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
Implementation Content Hash를 모두 고정한다.

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

Stress Scenario는 `scenario_contract_key`, Version, Content Hash를 함께 봉인한다.
`BASE`는 Base Scenario 예약 ID이므로 Stress ID로 사용할 수 없다.

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
- Cost Profile 결합 상태(`1.0.0`에서는 반드시 `null`)
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

Plan에 Stress Scenario가 있으면 Bundle은 각 Scenario의 성공·실패·건너뜀 Evidence를
최소 하나 보존한다. Stress 결과는 항상 `EVIDENCE_ONLY`다.

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

현재 Run Claim과 Strategy Execution Plan에는 권위 있는 Cost Profile Binding이 없다.
따라서 `inventory-result-bundle-v1` `1.0.0`은 `cost_profile_content_hash`의 non-null 값과
`AVAILABLE` Cost Delta를 모두 fail-closed로 거부한다. 임의의 비용 기준을 결과 생성 후
붙이는 것을 허용하지 않는다. 후속 Contract Revision에서 Claim에 Cost Profile ID·Version·
Content Hash를 먼저 봉인한 뒤에만 비용 비교를 활성화한다.

성공 결과의 Backorder Delta는 항상 산출한다. 전 기간 Demand가 0이면 Service Level은
수학적으로 정의되지 않으므로 1.0으로 대체하지 않고
`NOT_AVAILABLE / NO_DEMAND_IN_HORIZON`으로 기록한다. 실패 또는 건너뜀 Child의 모든
Delta는 각각 `CANDIDATE_RESULT_FAILED` 또는 `CANDIDATE_RESULT_SKIPPED`로 남긴다.

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

주요 위험은 Bundle Artifact Reader/Verifier가 아직 저장소와 연결되지 않았다는 점이다.
따라서 Wire의 ID·Hash·Contract Key가 맞더라도 실제 Artifact 존재성과 바이트 Hash는
후속 주입형 Verifier가 확인해야 한다. Stress Scenario와 Challenger 수가 늘면 Manifest와
비교 행 수도 증가하므로 현재 상한(Challenger 32, Stress 64, Child 512)을 운영 자료로
재검증해야 한다. Cost Profile이 없는 동안 비용 우열은 의사결정 근거로 사용할 수 없다.

Canonical Context Binding은 전달된 Claim과 Runtime 실제 입력 사이의 변조·Drift를 막는
봉인 계약이지, 각 값의 운영 Source 권위를 새로 증명하는 계약은 아니다. 계획기간과 Master
기준은 봉인된 Planning Input에서 파생한다. 반면 `business_timezone`,
`inventory_cutoff_at`, `inventory_source_watermark`는 실제 Inventory Position Source에서,
`quantity_rules`는 권위 UOM Source에서 Platform이 독립 조회해 대사해야 한다. 이 Source
Authority 연결은 아직 후속 Platform 통합 범위다. 따라서 현재 완료 범위는 Claim 봉인과
Runtime 변조 방어이며 실제 Inventory Position/UOM 권위 Source 연결 완료로 해석하지 않는다.

## 후속 검증과 남은 범위

이번 단계의 완료 조건은 Python Contract, JSON Schema, Runtime Wire와 Offline Test가
동일 의미를 검증하는 것이다. 다음 작업은 별도 승인 경계를 따른다.

1. Baseline·Math·PPO를 실제 동일 상태에서 실행하는 Orchestrator 구현
2. Bundle Artifact Writer와 Reader/Verifier 구현
3. Platform의 주입형 Artifact Verifier와 실제 저장소 연결
4. Platform에서 Inventory Position·UOM 권위 Source를 독립 조회하고 Canonical Context
   Binding의 Timezone·Cut-off·Watermark·Quantity Rules와 대사
5. `TB_IO_*` Publication/Evidence 물리 설계 확정
6. Migration 074 적용, 개발 DB Write, Runtime 배포와 실제 E2E
