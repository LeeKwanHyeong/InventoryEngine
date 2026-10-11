# 품목별 최적 후보 정책 선택 계약 V1

기준일: 2026-10-06. 대상 저장소/브랜치: InventoryEngine `inventory_engine_dev`.
[정책 탐색](IO_POLICY_SEARCH_CONTRACT.md)의 실제 품목별 Simulation 결과를 소비하는
개발 Shadow 선택기다. 운영 정책 승인·자동 게시·발주계획 생성·ERP 발주 기능은 아니다.

## 1. 선택의 의미와 책임 경계

`RunPolicySelectionUseCase(deployment).execute(search_result)`가 같은 Site의 품목마다
서비스 조건을 통과한 후보 중 하나를 결정론적으로 선택한다. 그룹 평균 정책이나
회사 전체의 공동 최적 정책을 선택하는 것이 아니다.

결과의 `optimality_scope=EVALUATED_CANDIDATE_SET_ONLY`는 **등록·평가한 유한 후보 집합 안의
최상위 후보**라는 뜻이다. 탐색하지 않은 정책·모수, 다른 수요 경로, 공동 발주비,
Multi-Echelon Network에 대한 전역 최적성을 뜻하지 않는다.

순수 계산 서비스와 파일 저장 Adapter를 분리했다. 기존 고정 Mathematical/PPO 실행과
운영 유효 정책 포인터는 변경하지 않는다. `DEVELOPMENT` 환경에서만 실행한다.

## 2. 선정 자격과 서비스 조건

모든 조건을 만족해야 순위 비교에 들어간다.

- 해당 후보가 실제 Simulation `SUCCEEDED`다.
- FSN/PLC 주문 Gate가 BLOCK이 아니고 권고 계산이 허용된다.
- 현재 고객 확정 수요와 순 Forecast의 동기간 수량 충족률이 `minimum_fill_rate` 이상이다.
- 종료 시 실제 고객 Backorder가 0이다.
- 비교 비용 Profile이 명시적으로 연결됐다.
- 평가기간에 현재 수요가 존재한다.

충족률은 명목 CSL이나 확률적 서비스 보장이 아니다. 기존 Backorder 출고를 현재 수요 충족량에
섞지 않는다. Forecast 미충족은 다음 주 Backorder로 이월하지 않는 기존 PSI 규칙을 유지한다.
무수요는 100% 서비스로 위장하지 않고 미선정한다.

REVIEW는 개발 비교·선정을 허용하고 `source_review_required=true`를 보존한다.
선택됐다는 이유로 REVIEW를 ALLOW로 바꾸거나 자동 발주를 허용하지 않는다.
선정 결과 전체의 다음 값은 항상 false다.

```text
automatic_publish_allowed
automatic_order_allowed
operational_policy_approved
database_writes
```

## 3. 비교 순서와 동점 처리

서비스 충족 후보의 다음 튜플을 오름차순으로 비교한다.

```text
(총비용, 주말 평균 On-hand 재고, 제약 적용 후 발주횟수, Candidate ID)
```

가중 합이나 임의 점수·허용 오차를 사용하지 않는다. 비용이 다르면 재고나 발주횟수가
더 적어도 비용이 낮은 후보가 우선한다. 재고·발주횟수는 앞 항목이 동점일 때만 비교한다.
숫자는 소수 문자열을 Decimal로 정확하게 비교하며 마지막 Candidate ID가 완전 동점을 해소한다.
수치 비교용 문자열의 지수 표기·음수·NaN·Infinity·float를 거부한다.

품목 순서·후보 순서가 선정 결과를 바꾸지 않는다. 같은 Source Hash와 동일 선택기 Version은
같은 선택 Artifact Hash를 만든다. 원본 행 순서 등을 바꿔 **Source Hash 자체가 달라지면**
품목별 선택은 같더라도 원본 연결을 포함한 최종 Artifact Hash는 달라질 수 있다.

선정 사유는 `ONLY_ELIGIBLE_CANDIDATE`, `LOWEST_TOTAL_COST`,
`TIE_RESOLVED_BY_AVERAGE_INVENTORY`, `TIE_RESOLVED_BY_ORDER_COUNT`,
`TIE_RESOLVED_BY_CANDIDATE_ID`다. 탈락한 적격 후보에는 처음 차이가 난 기준을
`HIGHER_TOTAL_COST`, `HIGHER_AVERAGE_INVENTORY`, `MORE_ORDERS`,
`CANDIDATE_ID_TIE_BREAK`로 기록한다.

## 4. 미선정과 실패를 숨기지 않는다

| 상태·사유 | 처리 |
|---|---|
| `SEARCH_ITEM_EXCLUDED` | 이력 부족·승인 Override 고정 등 원 탐색 제외 사유를 함께 보존 |
| `NO_CANDIDATES_EVALUATED` | 해당 품목에 평가 후보가 없는 경우 선택 포인터를 만들지 않음 |
| `ALL_CANDIDATES_FAILED` | 전 후보 실패. 각 후보의 실제 `failure_reason` 보존 |
| `CANDIDATE_FAILED` | 일부 후보 실패를 격리하고 나머지 적격 후보의 선정은 허용 |
| `ORDER_GATE_BLOCKED` / `RECOMMENDATION_CALCULATION_BLOCKED` | 차단 품목은 보유재고만으로 서비스 조건을 맞춰도 미선정 |
| `NO_DEMAND_IN_HORIZON` | 미래 수요가 없는 기간을 완전 서비스로 간주하지 않음 |
| `FILL_RATE_BELOW_MINIMUM` / `ENDING_BACKORDER_REMAINS` | 비용이 낮아도 서비스 부적격 |
| `COST_PROFILE_NOT_BOUND` | 없는 비용을 0으로 채우거나 재고만으로 대체 선정하지 않음 |

여러 미선정 원인이 있으면 후보별 사유를 모두 보존한다. 품목별로는 원 탐색 제외 → 평가 없음 →
전 후보 실패를 우선 구분하고, 그 외에는 후보별 부적격 사유의 정렬된 합집합을 기록한다.
`NOT_SELECTED` 품목의 선택 ID·Hash·정책·점수·Review 값은 모두 null이다.
원본 Hash/Scope/계약·PSI의 불일치는 후보 실패로 덮지 않고 **선택 호출 자체를 거부**한다.

## 5. 원본 Simulation 재검증

저장된 `service_feasible` Boolean만 보고 선정하지 않는다.

1. 원본 계약·Parent/Child Hash, Company, 입력 Hash와 탐색 Config Hash를 검증한다.
2. 품목 Universe·제외 목록·후보의 중복/고아·후보 집합 누락을 검증한다.
3. 성공 후보의 V2 Gate·Scope와 매주 재고 상태, 관측 Hash, 주문 중복을 검증한다.
4. 공통 행동 제약 검증기를 재실행하고 수락된 주문·입고·주차 PSI를 재계산한다.
5. 실제 PSI·주문에서 서비스·비용·평균재고·발주횟수를 재계산해 저장 지표와 대조한다.

이 검증은 **로컬 Artifact의 내부 정합성 검증**이다. 파일 Hash가 맞는다는 이유로 ERP Source의
진실성·업무 승인·사용자 권한까지 검증했다고 주장하지 않는다. 원 전략을 입력에서 독립적으로
재실행하거나 권위 Master/Classification을 조회해 인증하는 운영 Verifier도 아니다.
실제 Run Claim·권위 Source 연결은 후속 Platform 연동에서 별도로 검증해야 한다.

## 6. Append-only 선택 Artifact

원본 `inventory-policy-search-result / 1.0.0`은 변경하지 않고 `selected_policies=null`을 유지한다.
별도 `inventory-policy-selection-result / 1.0.0`으로 다음을 보존한다.

- `PSR-{hash}` 원본 Reference, 원본 Content Hash와 입력·Config·분류 Snapshot Hash
- 품목별 선택 Candidate ID, 해당 후보 결과 Hash, 후보 정책 모수와 선정 점수
- 후보별 SELECTED/NOT_SELECTED/INELIGIBLE/FAILED, 순위·탈락 사유·실패 사유
- 선정/미선정 품목 수, 선택기 Version과 평가 종료 조건

`selected_policy`는 선택한 후보의 RMOS/EMOS/CSL/점검주기 등 **탐색 모수**다. 계산된
ROP·Q·S와 제약 적용 행동은 선택 후보 Hash가 가리키는 원본 Evidence에서 조회한다.
승인된 운영 Effective Policy Version을 이 필드로 대신하지 않는다.

`LocalPolicySelectionArtifactStore`는 `PSEL-{hash}`로 원자적 Append-only 저장·멱등 읽기를 제공한다.
원본 PSR이 같은 로컬 저장소에 먼저 있어야 한다. Writer와 Reader 모두 원본을 읽어 선택기를
재실행하고 선택 결과 전체를 대조한다. 선택 사유·포인터만 바꿔 새 Hash로 봉인해도 거부한다.
원본 파일이 없어지거나 변조되면 선택 결과 조회도 실패한다.
Python 계약과 `schemas/policy_selection_result.schema.json`을 함께 검증한다.
PostgreSQL 결과 게시·Result Bundle Child·Outbox·Platform 조회 API는 아직 연결하지 않았다.

```text
inventory-policy-selection --search-result /absolute/search-result.json
inventory-policy-selection --search-result /absolute/search-result.json --artifact-root /absolute/existing/directory
inventory-policy-selection --search-content-hash <system-generated-search-hash> --artifact-root /absolute/existing/directory
```

패키지를 설치하지 않은 로컬에서는 `python -m dsio_inventory_engine.entrypoints.policy_selection`을
사용한다. 위 Hash는 시스템 산출 Reference이며 사용자가 화면에서 직접 생성/입력해야 하는 값이 아니다.

## 7. 비용과 평가기간의 한계

기존 비용 계약의 보관·고객 Backorder·품목별 발주 고정비·발주 변동비만 사용한다.
명시적으로 입력한 0은 유효하지만 비용 Profile 전체가 없는 경우에는 미선정한다.
혼적 Shipment 고정비, Landed Cost·세금·환급 VAT·실제 운영 원가는 이번에 새로 연결하지 않았다.

현재 유한 Horizon·종료 잔존가치 없음·Horizon 밖 도착 주문 거부 계약을 그대로 따른다.
짧은 기간에서 발주를 늦춘 후보가 유리할 수 있다. 다음 단계의 장기 Holdout·비용 민감도와
종료 효과 검증 전에는 운영 최적 정책의 확정 또는 향후 성과 보장으로 해석하지 않는다.

검증 결과는 [품목별 선택 검증 기록](IO_POLICY_SELECTION_VERIFICATION.md)을 따른다.
