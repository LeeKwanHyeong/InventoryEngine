# Inventory 7축 Segmentation 계약

기준일: 2026-09-11. 계약 Version은 `2.0.0`이다. 이 계약은 ABC·XYZ·VED·FSN·SDE·HML·PLC를
하나의 거대한 조합 Matrix로 만들지 않고, 같은 `Site + Item`에 대한 일곱 개의 독립적인
결정론적 분류 결과로 보존한다. 동일 Source Revision과 정규화된 Config Hash가 주어지면
항상 같은 `axis_results`와 표시용 Segment Code를 생성해야 한다.

## 1. 공통 결과와 상태

품목별 결과 Grain은 `classification_snapshot_id + item_id + axis`다.

`item_id`는 외부 Master가 소유하는 불투명 업무 키다. Engine은 문자를 치환하거나 별도 정규식
형식으로 재작성하지 않고 원문을 Hash와 Join에 사용한다. 다만 결정론적 비교를 위해 앞뒤 공백,
빈 값, NUL 문자와 160자 초과 값은 Source 경계에서 거부한다. Source Reader, V1 분류 정책과
Effective Policy V2는 동일한 검증 규칙을 사용한다.

| 필드 | 의미 |
| --- | --- |
| `axis` | `ABC`, `XYZ`, `VED`, `FSN`, `SDE`, `HML`, `PLC` |
| `status` | `CLASSIFIED`, `UNCLASSIFIED`, `UNVERIFIED`, `SYNTHETIC`, `NOT_APPLICABLE` |
| `class_code` | 축별 등급. 분류할 수 없으면 `NULL` |
| `application_mode` | `SHADOW` 또는 `OPERATIONAL` |
| `policy_effective` | `OPERATIONAL`이며 `CLASSIFIED`일 때만 참 |
| `source_contract_key` | 축이 요구한 Source 의미 계약 |
| `evidence` | 임계값 판정에 사용한 정규화 값 |
| `reason_code` | 미분류·미검증·미적용 사유 |

상태 의미는 다음과 같다.

- `UNCLASSIFIED`: 권위 있는 Source는 있으나 ABC/XYZ 계산 조건을 충족하지 못했다.
- `UNVERIFIED`: 필요한 Source가 없거나 완전성·시점·단위·표본 검증을 통과하지 못했다.
- `SYNTHETIC`: 개발·민감도 자료로 계산했으며 운영 정책에는 적용할 수 없다.
- `NOT_APPLICABLE`: Configuration에서 축을 사용하지 않는다.
- `SHADOW`: 분류와 Evidence를 저장하지만 유효 정책과 주문 판단을 바꾸지 않는다.
- `OPERATIONAL`: Source 상태가 `AVAILABLE` 또는 `PARTIAL`이어야 Draft 검증을 통과한다.
  `PARTIAL`이면 Source가 없는 품목은 품목 단위로 fail-closed한다.
- 현재 합성 PLC Source는 예외적으로 `SHADOW`만 허용한다. Config 2.0.0에서 PLC를
  `OPERATIONAL`로 선언하면 Platform과 InventoryEngine 양쪽 계약 검증에서 거부한다.

## 2. Source 계약과 개발 DB 조사 결과

개발 PostgreSQL `192.168.0.46:5432/dsai`를 읽기 전용으로 조사했다. 표본 Scope는
`DSE/C100/V100/V100`이며 활성 재고관리 품목은 7,000개다. DSE 전체로는 50개 Site,
350,000개 품목을 대사했다.

| 축 | Source 계약 | 개발 DB 판정 |
| --- | --- | --- |
| ABC | `CLOSED_DEMAND_VALUE_V1` | `ctl_actual_week_close`로 봉인된 `tb_dyn_demand_dtl`의 수량×단가. V2의 지원 기준은 `REVENUE`뿐이며 품목별 유효 매출 Coverage를 실제 Row에서 계산한다. |
| XYZ | `CLOSED_DEMAND_VARIABILITY_V1` | 같은 Actual Close의 주간 수요 CV². 품목별 유효 수요 Coverage를 실제 Row에서 계산한다. |
| VED | `APPROVED_VED_ASSIGNMENT_V1` | Site Scope의 승인 Assignment ID/Content Hash. 기존 승인·선택 흐름 유지 |
| FSN | `CLOSED_DEMAND_MOVEMENT_V1` | 봉인된 주간 수요 발생 여부. 무수요를 `N`으로 인정하려면 Actual Close가 해당 품목 Universe의 무이동까지 포함한다는 완전성 계약이 필요하다. |
| SDE | `SUPPLIER_LEAD_TIME_V1` | 표본 0/7,000, DSE 전체 0/350,000. 확정 발주·실입고·납기 준수 실적과 DSDM Site+Item을 잇는 봉인 Source가 없어 `UNVERIFIED` |
| HML | `INVENTORY_UNIT_COST_V1` | 표본 0/7,000, DSE 전체 0/350,000. 재고평가 단가·통화·평가기준일이 없어 `UNVERIFIED`. 판매단가나 발주단가로 대체하지 않음 |
| PLC | `SITE_PART_LIFECYCLE_V1` | 표본 7,000건과 DSE 전체 350,000건 모두 `synthetic_demand_parts` 생성 자료다. LTB 정책도 전부 `SYNTHETIC`이므로 `SYNTHETIC + SHADOW_ONLY`로 표시한다. |

표본 Actual Close는 `202601 R1 CLOSED`다. 52주 FSN Profile에서는 N 4,700개, F 1,725개,
S 575개가 관측됐다. 이 분포와 PLC Lifecycle은 업무 사실이 아니라 기본값과 실행 경로를
검토하기 위한 합성 개발 기준선이다. SDE Network Lane의 합성 P50/P90도 운송 Network
민감도 자료이므로 품목 조달 난이도의 운영 Source로 승격하지 않는다. Source 상태 API는
Close Header 존재를 100% Coverage로 치환하지 않고, 축별 Lookback의 모든 주차에 대해 최신
Actual Revision이 `CLOSED/REVISED`인지 검증한 뒤 축별 유효 품목 수를 실제 Source Row로
집계한다. 최신 정정 Revision이 `OPEN/PARTIAL`이면 과거 Terminal Revision으로 우회하지 않으며,
Authority와 Coverage는 하나의 `REPEATABLE READ READ ONLY` Snapshot에서 읽는다. Demand
Actual Close는 ABC·XYZ·FSN에만 적용한다. SDE의 입고 이력 Lookback은 Demand Close 기간을
확장하지 않으며, 권위 있는 입고 Snapshot 계약이 마련되면 별도 Revision으로 봉인한다.

`scm_part`의 PO·입고·임시 Lead Time Relation도 후보로 대사했지만 Text2SQL용 표본 자료이며,
DSDM V100 품목과 겹치는 ID가 없고 승인된 BU→Site Mapping도 없다. PO-입고 Join 누락,
조사 기준일 이후 입고일, Snapshot Revision·Hash 부재도 확인되어 운영 Source로 승격하지
않는다. 향후 SDE는 벤더 확약 납기 변경 이력과 분할 입고 Grain까지, HML은 재고평가 방식과
통화·평가기준일 및 필요 시 봉인된 FX Snapshot까지 계약에 포함해야 한다. PLC는
`lifecycle_revision_id`, 승인자·승인시각과 Content Hash가 있는 업무 승인 Revision만
Operational로 전환한다.

각 Demand 축의 `source_revision`은 단일 최신 주차 번호가 아니라 Lookback 전체의
`actual_yyyyww`, `closure_revision_no`, `publication_id`, `source_relation`, 정규화된
`source_manifest_sha256`을 주차순으로 Hash한 Window Lineage다. 과거 한 주의 정정도 Lineage를
변경한다. FSN Shadow 구간이 전부 봉인됐으면 그 구간도 Snapshot Lineage에 포함한다. 봉인이
부족한 Shadow FSN은 전체 ABC·XYZ 계산을 중단하지 않고 품목 결과를 `UNVERIFIED`로 낮추며,
Operational FSN은 같은 조건에서 전체 실행을 fail-closed한다.

실행 결과 요약은
[`evidence/seven-axis-source-readonly-20260911.json`](evidence/seven-axis-source-readonly-20260911.json)에
보존한다. Secret, 품목별 원천 Row와 개인 정보는 포함하지 않는다.

## 3. 축별 결정 규칙

### ABC·XYZ·VED

기존 1.1.0 읽기 호환은 유지한다. V2 ABC는 구현된 권위 Source가 있는 `REVENUE`만 허용하며
공헌이익과 연간 사용금액은 전용 Source 계약이 생기기 전까지 설정 단계에서 차단한다. ABC는
ABC 전용 Lookback의 매출 누적 비중, XYZ는 XYZ 전용 Lookback의 봉인된 주간 수요 CV²,
VED는 승인 Assignment를 사용한다. 한 축의 Lookback을 늘려도 다른 축의 집계값과 분모는
바뀌지 않는다. ABC 또는 XYZ가 미분류이면 운영 IO 적격이 아니다.

### FSN

- Lookback 기본값: 52주
- `N`: 마지막 양의 수요 이후 26주 이상 또는 Lookback 내 양의 수요가 없음
- `F`: N이 아니고 `positive_week_count / lookback_weeks >= 0.5`
- `S`: 나머지
- 경계 우선순위: `N` 판정 후 `F`, 그 외 `S`
- Config 2.0.0에서 FSN이 활성화되면 품목별 `fsn_source_row_count`와
  `fsn_invalid_row_count`를 반드시 제공한다. Source Row가 없거나 0건이면
  `UNVERIFIED/MOVEMENT_EVIDENCE_INCOMPLETE`, 유효하지 않은 Row가 있으면
  `UNVERIFIED/MOVEMENT_EVIDENCE_INVALID`로 분류한다.
- 완전한 Actual Close가 해당 품목의 0 movement를 증명할 때만 `N`을 인정한다. 따라서
  Operational FSN은 단순한 Source 부재를 `N`으로 추정하지 않고 Effective Policy에서
  fail-closed한다.

### SDE

- Lookback 기본값: 52주, 최소 완료 입고 표본 8건
- `S`: 공급자 수 1 이하, Lead Time 84일 이상, 납기 준수율 80% 이하 중 하나 이상
- `D`: S가 아니며 Lead Time 28일 이상 또는 납기 준수율 95% 이하
- `E`: 나머지
- 경계 우선순위: `S` 후 `D`, 그 외 `E`
- 공급자·Lead Time·납기 준수율·표본 중 하나라도 검증되지 않으면 `UNVERIFIED`

### HML

- Source: 동일 통화로 정규화된 권위 있는 Inventory Unit Cost
- Site 내 유효 단가를 오름차순 정렬하고 nearest-rank 방식으로 P50/P80을 계산
- `H`: 단가가 P80 이상, `M`: P50 이상 P80 미만, `L`: P50 미만
- 동일 단가는 같은 등급이며 품목 ID 정렬은 Hash 재현을 위한 순서에만 사용
- 단가·통화·평가기준일이 없거나 평가기준일이 판단 시점보다 미래이면 `UNVERIFIED`
- 판단 주차와 단가 평가기준일의 차이가 `max_source_age_weeks`를 초과하면 `UNVERIFIED`
- 오래된 단가는 해당 품목 판정뿐 아니라 Site P50/P80 Threshold 모집단에서도 제외한다.

### PLC

- 기본 구간: 도입 13주, 성장 52주, 생산종료 전 감소 26주
- `introduction_weeks`는 1~52, `growth_weeks`는 2~156이며 항상
  `introduction_weeks < growth_weeks`여야 한다. Platform Schema·API·화면과 Engine이 같은
  경계를 검증한다.
- 기준주차가 도입 전이면 `PRE_LAUNCH`
- 도입 후 13주 미만은 `INTRODUCTION`, 52주 미만은 `GROWTH`
- 이후 생산종료까지 26주 이내면 `DECLINE`, 아니면 `MATURE`
- 생산종료 이후 또는 승인 상태가 서비스 전용이면 `SERVICE_ONLY`
- 서비스종료 이후 또는 승인 상태가 단종이면 `DISCONTINUED`
- 날짜와 승인 상태가 충돌하면 더 보수적인 후기 단계가 우선한다.
- 현재 개발 Source는 `source_profile_hash`가 봉인된 합성 자료다. ISO 주차, 도입·생산종료·
  서비스종료 순서와 지원 상태를 검증한 뒤 `status=SYNTHETIC`으로 실제 PLC 등급과 날짜 Evidence를
  계산한다. 표시 코드에는 포함하지만 `policy_effective=false`로 두며 Effective Policy
  Projection·Hash에는 포함하지 않는다.

## 4. 정책 결합 순서

정책은 다음 순서로 합성한다.

1. ABC-XYZ Matrix가 목표 서비스수준·검토주기·전략을 제공한다.
2. VED가 목표 서비스수준 하한을 올린다.
3. SDE가 Lead Time 기준을 P50 또는 P90으로 선택한다.
4. FSN과 PLC가 주문 행동을 `ALLOW`, `REVIEW`, `BLOCK`으로 제한한다.
5. HML이 `AUTO`, `STANDARD`, `HIGH_VALUE` 승인 수준을 지정한다.
6. 주문 행동 충돌은 `BLOCK > REVIEW > ALLOW` 순으로 해소한다.
7. Source Hard Constraint와 승인 Override는 이 분류 정책보다 우선한다.

품목별 Effective Policy V2는 다음 값을 결정적으로 만든다.

| 필드 | 규칙 |
| --- | --- |
| `effective_order_action` | Operational FSN·PLC 결과를 `BLOCK > REVIEW > ALLOW`로 결합 |
| `effective_protection_lead_time_basis` | Operational SDE 등급에 매핑된 `P50` 또는 `P90` |
| `effective_protection_lead_time_days` | 선택된 검증 Lead Time 분위수. 실제 납기일을 바꾸는 값이 아니라 안전재고 보호기간 계산 입력 |
| `effective_approval_level` | Operational HML 등급에 매핑된 `AUTO`, `STANDARD`, `HIGH_VALUE` |
| `recommendation_calculation_allowed` | `ALLOW`와 `REVIEW`에서 참. 미검증 Operational 축과 `BLOCK`에서는 거짓 |
| `automatic_publish_allowed` | 주문 행동이 `ALLOW`일 때만 참 |
| `automatic_order_allowed` | 주문 행동이 `ALLOW`이고 승인 수준이 `AUTO`일 때만 참 |

`REVIEW`는 권고 후보 계산과 Evidence 저장을 허용하지만 후보를 운영 결과 포인터나 발주로
자동 게시하지 않는다. `BLOCK`은 주문 실행을 만들지 않고 차단 Evidence만 남긴다. Operational
축이 `UNVERIFIED`, `SYNTHETIC` 또는 그 밖의 미분류 상태이면 해당 품목은 추정값으로 대체하지
않고 fail-closed한다. Shadow·비활성 축은 `axis_results`와 전체 Snapshot Hash에는 남지만
Effective Policy Projection 및 Hash에는 포함하지 않는다.

각 품목의 V2 정책은 `classification_config_hash`와
`classification_config_binding_hash`로 원본 분류 설정에 결박한다. Config Hash 자체는 Shadow
설정 변경이 유효 정책 Hash를 바꾸지 않도록 `effective_policy_hash` 계산에서는 제외한다. 대신
Binding Hash가 품목 ID·Config Hash·Effective Policy Hash를 함께 봉인하며, 보충 Admission에서는
이를 재검증한 뒤 Run의 `config_hash`와 정확히 대조한다. 따라서 Config Hash 필드만 바꾸거나
Effective Policy Hash가 같은 다른 Config Revision의 정책을 재사용할 수 없다.

`display_segment_code`는 사람에게 보여주기 위한 값일 뿐 정책 Lookup Key가 아니다. ABC와 XYZ는
`AX`처럼 붙이고 이후 분류 결과만 축 순서대로 연결해 `AX-V-F-MATURE` 형태로 표시한다. 7축
조합별 Matrix는 만들지 않는다.

## 5. 현재 구현·게시 경계

- dsai-platform Config Registry는 1.1.0과 2.0.0을 함께 검증하되 Schema와 분류 모드를 정확히
  결합한다. `ABC_XYZ`는 승인된 1.1.0 Schema ID/Version/Hash만, `SEVEN_AXIS`는 2.0.0만
  허용하며 교차 조합은 Source 계산 전에 차단한다.
- Engine Studio는 신규 축의 사용 여부·모드·임계값과 Source Coverage를 표시한다.
- 합성 PLC는 화면에서 Shadow 전용으로 표시하고 Operational 선택 경로를 제공하지 않는다.
- InventoryEngine은 2.0.0 입력에서 일곱 개 `axis_results`, 표시 코드와
  `policy_effective_axes`를 결정적으로 계산한다.
- 기존 1.1.0 Snapshot은 기존 품목 필드와 Source Hash Projection을 유지한다. 축별 상태·Window
  Lineage 같은 V2 필드는 2.0.0 Snapshot에만 추가한다.
- Migration 075·076은 2026-09-11 개발 PostgreSQL에 적용했고 V2 Header·품목 정책·축 Window·
  7축 Evidence·Gate 사유의 Deferred Constraint를 Rollback Canary로 검증했다. 기존
  Snapshot 1건은 그대로 보존했으며 실제 V2 업무 Snapshot은 게시하지 않았다.
- Effective Policy V2는 FSN/PLC 주문 Gate, SDE 보호기간 Lead Time, HML 승인 수준과 Shadow
  무영향 Hash를 품목 결과에 연결한다. Platform은 승인된 V2 Snapshot ID와 전체
  `effective_policy_content_hash`를 `INVENTORY_CLASSIFICATION / 2.0.0` Run Input으로 자동
  조립하며 사용자가 Hash를 입력하지 않는다.
- Runtime은 품목별 Config Binding과 Policy Hash를 재검증하고 FSN/PLC Gate, SDE P50/P90
  Lead Time과 HML 승인 수준을 보충 계산에 적용한다. `REVIEW`는 계산과 Evidence를 남기되
  자동 Publish·발주를 차단하고, `BLOCK`과 미검증 Operational 축은 계산 전에 차단한다.
- SDE·HML은 권위 Source Coverage가 0이므로 계속 `UNVERIFIED`다. PLC 350,000건은 모두 합성
  자료이므로 `SYNTHETIC + SHADOW_ONLY`를 유지한다. 실제 Source 확보 전에는 이 상태를 운영
  분류값으로 승격하지 않는다.
- 미완료 범위는 실제 V2 업무 Config·Snapshot 영속 게시 E2E, 공통 Run Migration 074,
  공용 Runtime 배포와 운영 Source 연결이다.
