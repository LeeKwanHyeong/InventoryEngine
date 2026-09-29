# 동일 Run PSI Orchestrator 검증 기록

기준일: 2026-09-15. 판정: **로컬 구현·오프라인 회귀 PASS**.
[PSI·전략 실행 계약 V2](IO_PSI_STRATEGY_EXECUTION_CONTRACT_V2.md)의 실제 Child Artifact와
동일 Run Orchestrator와 로컬 Durable Artifact/Runtime Handler를 검증한 기록이다. 개발
Inventory Result의 PostgreSQL/Object Storage Write, 공용 Runtime 배포 또는 PPO 운영 승격의
합격 판정은 아니다. Migration 074와 실제 Claim·Attempt·Retry·CAS는 이후 개발 PostgreSQL에서
별도로 검증했다.

## 1. 구현한 범위

- 하나의 `CanonicalInputRequest`를 한 번 준비해 Baseline, Mathematical, PPO Shadow와
  Stress Child에 독립 복제본으로 전달한다.
- Prepared 문서의 자체 Hash뿐 아니라 Canonical에서 기대되는 전체 Projection과 정확히
  일치하는지 Child 실행 전에 검증한다.
- Baseline과 Recommended PSI Row 자체를 정렬·정규화하여 PSI Artifact의 Row Count와
  Content Hash를 계산한다.
- Mathematical·PPO·Stress 결과의 원 행동, 제약 적용 행동과 조정 사유를 각각 별도
  Artifact로 봉인한다.
- Raw Action Artifact에는 실제 판단에 사용한 Prepared/Execution Projection과 Effective
  Policy Admission Source를 함께 봉인한다. Constrained/Adjustment Artifact에는 Source를
  중복 저장하지 않는다.
- 실제 PSI Row에서 예상 서비스수준, 마지막 Bucket 종료재고와 Backorder를 계산한다.
- 승인된 개발 Cost Profile이 Run Plan에 봉인되면 실제 PSI·제약 적용 행동에서 보유비,
  Backorder 비용, 고정·변동 발주비를 계산한다. 미결합 Run은
  `NOT_AVAILABLE / COST_PROFILE_NOT_BOUND`로 호환한다.
- Plan에 등록된 PPO의 Implementation·Model·승인 Binding을 실행 대상과 대사한다.
- Plan 1.1.0의 수학 정책 입력 Snapshot·Replenishment Config Hash와 Stress Runner
  Implementation ID·Version·Hash를 실제 실행 대상과 대사한다.
- PPO 실패는 해당 Child만 `FAILED`로 남기고 Mathematical Effective 결과를 유지한다.
- 검증된 Stress Runner는 수요·확정 입고가 변형된 Prepared Input만 반환한다. 실제
  Mathematical 전략, 공통 Guard와 PSI 실행은 Orchestrator가 소유한다.
- Stress Payload·Seed·Scenario Hash·Runner 구현 Hash를 Plan과 대사하고 새 Runner Instance
  두 개로 같은 변형 입력이 재생되는지 확인한다.
- 개발 Registry의 수요 20% 증가와 확정 입고 7일 지연 Scenario를 실제 Stress Child로
  실행하고 Platform과 InventoryEngine의 Cost/Scenario Golden Hash가 일치하는지 확인한다.
- Context·Calendar·Master·Policy·BOH·Cut-off Evidence 변경과 Runner의 PSI·행동·전략 결과
  직접 반환을 차단한다. 수요 Netting과 입고 Calendar/상태별 포함 규칙도 다시 검증한다.

## 2. 실제 PSI 지표 검증

비교값을 테스트 Fixture 인자로 주입하지 않는다. 다음 PSI Row 필드에서 값을 다시 계산한다.

- 수요: `confirmed_customer_order_qty + net_forecast_qty`
- 정시 충족: `fulfilled_confirmed_customer_order_qty + fulfilled_forecast_qty`
- 서비스수준: 전체 정시 충족량 / 전체 수요량
- Backorder: 품목별 마지막 Bucket의 `backorder_close_qty`
- 종료 가용재고: 품목별 마지막 Bucket의 `eoh_qty`
- 종료 On-hand: 품목별 마지막 Bucket의 `on_hand_eoh_qty`

무수요 기간은 서비스수준을 1로 만들지 않고 `NO_DEMAND_IN_HORIZON`으로 기록한다.
과거 Backorder 충족량은 정시 충족 분자에 넣지 않는다. 수량 Delta의 부호는
`candidate - baseline`이다. 현재 Bundle의 Scalar 비교는 단일 UOM만 허용하고, 혼합 UOM은
조용히 합산하지 않는다.

## 3. 실행·실패 격리 검증

오프라인 통합 검증에서 다음을 확인했다.

- 같은 입력을 다시 실행하면 Bundle, Child, PSI·Action Artifact Hash가 동일하다.
- 준비 Use Case 호출 횟수는 한 Run에 정확히 한 번이다.
- Prepared의 재고 Row를 바꾸고 자체 Hash를 다시 계산해도
  `PREPARED_INPUT_PROJECTION_MISMATCH`로 Child 실행 전에 차단한다.
- Baseline·Math·PPO 성공 Child가 같은 Canonical Hash를 사용한다.
- PPO Shadow는 자동 게시와 자동 발주가 모두 `false`다.
- PPO 추론 실패 시 PPO Child만 `FAILED`가 되고 Math Child·Metric·Effective Pointer는
  성공 실행과 동일하다.
- 수요 2배 Stress Runner가 별도 `STRESS_PSI`를 만들고 실제 수요 Metric이 변한다.
- Stress Runner가 임의 실행·PSI·행동 결과를 반환하거나 Base Position을 바꾸면 Stress Child만
  실패하고 실제 Mathematical 결과와 Effective Pointer는 유지된다.
- 확정 입고는 예정일·수량 및 Calendar에서 파생되는 주차·포함수량만 변경할 수 있고, 상태나
  품목·UOM·Receipt ID를 변경할 수 없다.
- 등록된 Stress Binding에 Runner가 없으면 결과를 재사용하지 않고
  `STRESS_SCENARIO_UNAVAILABLE`로 건너뛴다.
- PSI Row, Action Payload 또는 Artifact Hash 변조는 비교 전에 차단한다.
- PSI·Observation의 닫힌 Row 계약은 알 수 없는 필드를 거부한다.
- Action Observation을 봉인된 Source에서 다시 투영하고 `decision_id`를 재파생하며, Proposal을
  공통 Guard에 다시 실행해 Constrained Action과 조정 사유를 검증한다.
- V2 Effective Policy Admission을 저장 정책·Config·실행 목적·Model 승인에서 공식 함수로
  재계산한다. PPO Admission을 `OPERATIONAL/AUTO`로 위조하면 PPO Child만 실패하고
  Mathematical Effective 결과는 유지된다.
- 물리 Capacity 초과량, 권고 입고와 Pending Supply 도착주차를 Source와 PSI에서 다시 계산한다.
- 모든 품목의 `seq/yyyyww/start_date/end_date` Bucket이 동일하며 날짜 범위가 1~7일이고
  인접 Bucket이 연속인지 검증한다. `yyyyww`는 봉인된 업무 Calendar 값을 사용하며 ISO
  주차로 다시 계산하지 않는다.
- PSI 재고·주문·Forecast 보존식, Backorder 우선 충족, 주차 Roll-forward와 Action의
  권고 입고 수량을 교차 검증한다.
- PPO는 `SKIPPED`가 될 수 없고 Stress의 `SKIPPED`는 미해소 Registry에만 허용한다. Stress
  Runner가 예약된 `STRESS_SCENARIO_UNAVAILABLE`을 예외로 던지면 실행 실패로 재분류해
  전체 Run으로 전파하지 않는다.
- 모든 `SUCCEEDED` Child는 실제 Artifact 1행 이상을 가져야 하며 `row_count=0`은 Python
  계약과 JSON Schema 양쪽에서 거부한다. `FAILED`·`SKIPPED`는 Row Count와 Artifact를
  `null`로 유지한다.
- PSI·Action·Canonical·Prepared·Execution Source와 Bundle 바이트를 SQLite 단일 Transaction에
  Append-only 저장하고 즉시 다시 읽어 Content Hash와 바이트 Hash를 검증한다.
- 동일 Semantic Reference의 정확한 Replay는 멱등 처리하고 다른 Hash·바이트는 전체 저장을
  Rollback한다. 두 Worker의 동일 저장도 하나의 원본과 하나의 Replay로 직렬화한다.
- Raw Action의 Source를 저장된 Prepared·Execution·Admission에서 다시 만들어 Artifact 내부
  Observation Source와 대사한다.
- Bundle과 같은 Transaction에서 Result-ready Outbox를 생성한다. 자동 Publish 성공 시
  `PUBLISHED`로 전환하고 실패하면 `PENDING`을 유지한다.
- Retry는 기존 Outbox와 Bundle을 재검증해 같은 Pointer를 게시하며 Command 해소와 PSI 계산을
  다시 수행하지 않는다. `REVIEW`는 `WITHHELD_FOR_REVIEW`로 저장해 자동 Publish 대상 조회에서
  제외한다.

## 4. 회귀 결과

동일 작업 트리에서 다음 전체 테스트를 실행했다.

| 구분 | 실행 결과 |
| --- | --- |
| Unit | 432 통과 |
| Contract | 26 통과, 선택 의존성 미설치 3 건너뜀 |
| Offline Integration | 65 통과, 외부 연동 선택 테스트 18 건너뜀 |
| 합계 | 523 통과, 실패 0, 건너뜀 21, Subtest 541 통과 |

변경 Python 파일 전체의 Ruff 검사와 Format 검사도 통과했다. 이 Orchestrator 테스트 자체는
DB, Network, 외부 Runtime에 접속하거나 데이터를 쓰지 않았다. 별도 승인 E2E로 V2 분류
Snapshot은 개발 PostgreSQL에 게시했다.

## 5. 남은 경계

현재 Durable 구현은 로컬 SQLite Adapter이며 운영 Artifact Store가 아니다. 운영에서는 같은
Port에 PostgreSQL Metadata/Outbox와 Object Storage 바이트 Writer를 연결하고 DB·Object 간
봉인 순서와 보상 복구를 Migration 074 및 Publication 물리 계약에서 확정해야 한다. 개발
Cost/Stress Registry는 연결됐지만 권위 회계 Cost Source와 운영 승인 Scenario Catalog는
외부 Source 대기다.

같은 Process 안에서 새 Stress Transformer Instance 두 개를 비교하는 검증은 구현 결함을 조기에
잡는 방어다. 외부 의존성을 포함한 다른 Worker 간 결정론까지 증명하지 않으므로 운영에서는
동일 Reference·동일 Hash만 멱등 허용하고 동일 Reference·다른 Hash는 충돌시키는 로컬 영속
경계까지 검증했다. 외부 Object Storage와 PostgreSQL 간 경합 검증은 아직 포함하지 않는다.

InventoryEngine의 Strategy Execution Plan은 수학 정책 입력·실행 설정, Stress Runner와 선택적 Cost Profile을
추가 봉인한 `1.1.0`이다. dsai-platform Backend Model·Hash·Fixture도 같은 `1.1.0`으로
정합화했고 교차 저장소 Golden Hash를 확인했다. Migration 074의 Planning Cycle Source 계약
Version은 별도 의미이므로 변경하지 않았다.

2026-09-15 Migration 074를 개발 PostgreSQL에 적용하고 실제 V2 Config·분류 Snapshot과
Cost/Stress Plan을 Claim에 봉인했다. 실패 Attempt 뒤 동일한 10개 Input Binding을 사용한 Retry,
Stale Attempt CAS 차단, V2 자동 게시 Gate와 V1 Effective Run CAS를 확인했다. 상세 증적은
dsai-platform의
`docs/backend/dsim/evidence/inventory-run-lifecycle-migration-074-20260915.json`을 따른다.

따라서 다음 구현 순서는 권위 Cost/Stress Source 교체와 운영 Artifact Adapter다. 실제 Result
Bundle DB/Object Storage Write와 공용 Runtime 배포는 각각 명시적 승인 이후에 수행한다.
