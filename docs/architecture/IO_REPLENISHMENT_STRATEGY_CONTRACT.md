# IO 보충 전략 공통 계약과 주간 PSI

기준일: 2026-09-03. 계약 `io-replenishment-v1 / 1.0.0` 및 추가 입력 Binding을 지원하는 `1.1.0`, 현재 패키지 `0.6.0`.

## 1. 결정과 구현 상태

기존 수식 전용 V1 범위를 **공통 PSI + `MATHEMATICAL`, `PREDICTIVE_ML`, `DEEP_RL` 보충 전략**으로 확장한다. `BASELINE`/`RECOMMENDED`는 시나리오이며 전략 종류와 별개다. Baseline은 신규 권고 공급이 없는 기존 계산을 유지한다.

- 완료: 불변 요청/관측/제안 계약, 공통 주차 전이, 행동 검증, 주문 대기열, 로컬 Recommended PSI, 중간 Evidence 반환.
- 완료(0.4.0): [수학적 SS/ROP/목표재고 산출기](IO_MATHEMATICAL_POLICY_CONTRACT.md), 기간형 승인 Override/정책 Fallback, 독립 Golden.
- 미구현: ML·PPO 학습/모델 로더, 전략 간 자동 Fallback, 실제 Run Claim·승인 인증·Artifact 봉인·DB 게시.
- 현재 실행은 `DEVELOPMENT + LOCAL_SHADOW`만 허용한다. 세 전략의 테스트 Probe는 인터페이스를 검증하는 고정 행동이며 학습 모델이 아니다.
- 범용 Solver·생산 Capacity/BOM/BOR·Multi-Echelon·DSIM 구현은 이번 범위 밖이다.

공통 호출은 `RunRecommendedPsiUseCase(deployment).execute(request, strategy)`다. 전략은 `descriptor`와 `decide(observation) -> ReplenishmentProposal`을 제공한다. 외부 문자열로 Python 모듈을 import하거나 모델을 검색하지 않는다. 호스트가 신뢰한 Adapter를 명시적으로 주입한다.

## 2. 요청과 실행 설정

`RecommendationRequest`는 기존 `CanonicalInputRequest`를 `canonical_input`으로 감싸고 별도 `execution`을 받는다. 기존 Canonical v1의 8개 Snapshot·Schema·Baseline CLI·Golden은 변경하지 않는다.

| 필드 | 계약 |
|---|---|
| `canonical_input_hash` | 요청의 정규화된 Canonical Hash와 일치. 상태·입력·Scope 변경도 검증 |
| `configuration_revision` | Canonical Context와 일치 |
| `strategy` | 종류, `implementation_id`, `version`, 선택적 `model` |
| `model` | ML/DRL은 `model_id + version + content_hash` 필수, 수학적 전략은 null |
| `approval_reference` | 동작 설정의 승인 근거 식별자. 로컬 검사만으로 실제 승인자를 인증하지 않음 |
| `allowed_action_types` | 허용 행동 목록. `HOLD` 필수. 초기 DRL 권장값은 HOLD/ORDER_UP_TO |
| `item_controls` | 정확히 동일한 Active Item Universe, UOM, 품목별 최대 주문량·목표재고 최소/최대·발주 허용일 |
| `decision_timing` | `BUCKET_START_BEFORE_RECEIPTS` |
| `receipt_mapping` | `NEXT_BUCKET_START_ON_OR_AFTER_DUE_DATE` |
| `capacity_mode` | `CONSERVATIVE_NO_DEMAND_CREDIT` |

`execution`과 전략 제안의 구조는 [JSON Schema](../../schemas/replenishment.schema.json), 의미·수량 범위·UOM·Hash·교차 필드 검증은 Python 계약/UseCase가 담당한다. 실제 요청은 `{canonical_input, execution}` 객체다. JSON Schema는 그중 실행 설정과 제안 객체를 검증한다.

1.1.0 실행 설정은 `strategy_input_binding`(계약/Version/Snapshot ID/Hash)을 추가로 요구한다. Adapter의 `input_binding`과 일치해야 하고 관측에도 전달된다. 수학적 전략은 이를 통해 과거 이력·Profile·승인 정책을 고정한다. 기존 1.0.0과 Proposal 필드는 변경하지 않으며 결과 Version은 실행 설정과 동일하다.

설정은 한 실행 동안 불변이다. 전략·모델·행동 허용 범위 변경은 새 Configuration/논리 실행 Revision이며 기술적 Retry가 아니다. 공통 Run 서비스 연결 전에는 이 Revision의 실제 DB 존재를 확인하지 않는다. `input_content_hash`는 물리 Run ID를 제외한 Canonical Hash와 전체 실행 설정을 묶는다.

## 3. 시간과 정책 적용 규칙

1. 각 Source Calendar Bucket의 **시작일에 한 번**, 입고와 수요 처리 전에 품목별 의사결정을 요청한다. W0도 같은 규칙이다.
2. 해당 시점 상태는 가용 BOH·물리 BOH·Reserved·실제 주문 BO와 아직 입고 처리하지 않은 공급이다. 당주 확정 입고도 대기열에 있어 Inventory Position에 한 번만 포함된다.
3. `order_dates`에 없는 날짜의 양수 주문은 거부한다. 값은 Source Calendar의 Bucket 시작일이어야 한다. 빈 목록은 발주 금지다. 영업일·휴일을 엔진이 추론하지 않는다.
4. 정책은 `effective_from <= bucket.start_date < effective_to`인 행 하나를 사용한다. 주중 정책 변경은 다음 Bucket 시작부터 적용하며 일별 분할은 후속 계약이다.
5. 원래 도착일은 `decision_date + lead_time_days`다. **그 날짜 이상인 최초 Calendar Bucket 시작일**을 실제 시뮬레이션 입고 시점으로 사용한다. 주중 도착을 그 주 시작으로 당기지 않는다.
6. 0일 Lead Time은 Source가 명시한 경우 판단 경계에서 동일 Bucket 입고다. 현재 계약은 Calendar Day 0~3660일이며 ISO 주차 재계산이나 영업일 추론을 하지 않는다.
7. 원래 도착일·보수적으로 매핑한 입고일·Bucket을 모두 기록한다. Horizon 밖이거나 마지막 Bucket 중간에 도착해 다음 경계가 없으면 `ARRIVAL_OUTSIDE_PLAN_HORIZON`으로 거부한다. 이후 기간의 용량을 검증하지 않은 주문을 자동 생성하지 않는다.
8. 기존 확정 입고는 Baseline과 동일하게 원본 Due Date가 속한 주에 집계한다. 일별 출고/입고 순서를 보장하는 모델은 아니다. 신규 권고의 보수적 경계 매핑과 이 기존 집계를 혼동하지 않는다.

예: 2026-09-28 판단, Lead Time 1일이면 원래 도착일 09-29, 시뮬레이션 입고는 10-05다. 7일도 10-05, 8일은 10-12다. 모든 발주일은 계획 시뮬레이션 날짜이며 실제 ERP 주문 생성/벽시계 기준 실행 가능성 보장은 아니다.

## 4. 공통 관측과 행동

`ReplenishmentObservation`은 Engine이 구성한 불변 JSON이다. `to_dict()`는 매번 분리된 복사본을 반환한다.

- 입력 Hash, Run/Cycle/Company/Subs/Site Context, Item/UOM, 판단 ID·일자·현재 Bucket
- `state`: 가용/물리 재고, Reserved, 실제 주문 BO, Inventory Position
- `future_demand`: 현재부터 Horizon 끝까지의 확정 주문과 순 Forecast. 미래 실제 수요를 관측으로 제공하지 않음
- `pending_supply`: 공급 ID, 확정/권고 구분, 원래 Due Date, 입고 Bucket, 미도착 수량
- 현재 정책과 미래 적용 정책, 실행 제약, 수량 규칙, 전략 Binding

```text
inventory_position = available_boh + pending_supply - actual_backorder
```

관측의 Inventory Position은 Horizon 안에 포함된 미도착 공급 총량이다. 시점별 부족 판단은 반드시 대기열의 입고 Bucket을 함께 사용해야 하며 먼 미래 공급으로 현재 PSI 부족을 상쇄하지 않는다. Horizon 밖 확정 공급은 기존 입력 Evidence에 제외 사유와 보존되며 이 Position에 포함하지 않는다.

| 행동 | 의미와 기본 보충량 |
|---|---|
| `HOLD` | quantity=0 필수, 주문 없음 |
| `ORDER_QTY` | quantity가 신규 주문 후보량. 해당 행동이 실행 설정에서 허용돼야 함 |
| `ORDER_UP_TO` | quantity가 절대 목표재고. `max(0, target - inventory_position)`으로 기본 주문량 계산 |

ML/DRL의 목표 조정은 Adapter가 기준값+조정량을 절대 `ORDER_UP_TO`로 제출한다. 공통 엔진이 ML/DRL 계산을 가장하거나 전략 종류를 보고 다른 PSI 수식을 사용하지 않는다.

제안에는 현재 `decision_id`, `observation_hash`, 정확히 동일한 전략/모델 Binding, 행동/수량, nullable `calculated_policy`(안전재고·ROP·목표재고), 사유 Code가 필요하다. 모델이 계산하지 않은 정책은 null로 둔다. 이전 관측 재사용, 다른 모델, 추가 필드, 음수·비유한 수량·UOM 정밀도 위반은 거부한다.

## 5. 공통 행동 검증과 공급 대기열

- Source MOQ·발주배수·물리 용량·Lead Time은 전략이 수정할 수 없다. 실행 설정은 추가 최대 주문량·목표 범위·발주일·행동 종류를 제한한다.
- 양수 필요량만 `ceil_to_multiple(max(raw, MOQ))`를 계산한다. 0을 MOQ로 늘리지 않는다.
- 최대량/용량을 넘으면 허용 한도 이하의 최대 유효 배수로 줄인다. MOQ를 만족하는 양수가 없으면 `NO_FEASIBLE_ORDER_QUANTITY`다.
- 예: 필요 137, MOQ 100, 배수 50, 수용 가능 120 → 후보 150 → 승인 가능 100, 미충족 37. 120으로 단순 절단하지 않는다.
- 용량은 **현재 물리 BOH(Reserved 포함) + 아직 미도착인 확정/기존 권고 공급**을 대상으로 검사한다. 예상 미래 수요가 출고될 것이라고 미리 가정하지 않는다. 신규 도착 Bucket부터 Horizon 끝까지의 용량과 이미 고정된 미래 용량 감소도 확인해 가장 작은 Headroom을 적용한다.
- 이는 보수적 용량 예약이다. 예상 출고를 인정하는 방식보다 권고가 적을 수 있으며, 물리 실행 가능성 최적화라고 주장하지 않는다. 다른 방식은 별도 Versioned 계약/Golden을 거쳐야 한다.
- 기존 재고/확정 공급 자체의 초과는 `physical_capacity_excess_qty`로 진단하고, 수량을 잘라 없애지 않는다.
- 승인된 주문만 Run 로컬 대기열에 추가한다. 도착 Bucket에서 정확히 한 번 가산하고 제거한다. 반복 ORDER_UP_TO는 이미 미도착인 권고를 포함해 순 필요량을 계산한다.
- 직접 ORDER_QTY를 다른 주에 다시 제출한 것은 새 행동일 수 있어 무조건 중복으로 제거하지 않는다. 전략은 대기열을 고려할 책임이 있다. 동일 Item/Bucket은 한 번만 호출되고, 과거 Decision/관측 Hash는 거부된다.
- 미래 정책이 달라져도 이미 생성된 주문의 수량·입고일은 소급 변경하지 않는다. 주문 취소·수정은 이번 계약에 없다.
- Adapter 예외는 `STRATEGY_EXECUTION_FAILED`로 중단하며 원문 예외/Secret을 노출하지 않는다. 미지정 Fallback으로 조용히 전환하지 않는다.

## 6. 공통 PSI와 반환 Evidence

Baseline과 Recommended 모두 `advance_bucket(InventoryState, ...)`를 호출한다. 입고 → 기존 실제 BO → 고객 확정 주문 → 순 Forecast 순서, 실제 BO만 이월, Forecast Shortage는 미이월이다. 물리 EOH=가용 EOH+Reserved와 수량 보존식을 유지한다.

`RunRecommendedPsiUseCase`는 다음을 JSON으로 반환한다.

- 정규화된 모든 입력/봉인 대사, 전체 실행 설정과 전략 Binding
- 결정별 관측/Hash, 원래 제안, Source 정책, 계산 정책과 실제 사용한 목표재고
- MOQ/배수 조정 전후 수량, 용량 Headroom, 최종 승인량, 미충족량, 거부/조정 사유
- 권고 주문 생성일·원래 도착일·입고 경계·Source Policy ID와 `SIMULATED_RECEIVED` 상태
- Recommended PSI, 결정·주문·PSI별 Content Hash

`REJECTED` 행동이 있어도 PSI 자체를 계산할 수 있으며 거부를 정상 권고로 표시하지 않는다. `RECOMMENDED_PSI_COMPUTED_LOCALLY`는 **계산 완료**이지 Run 성공이나 운영 권고 승인 상태가 아니다. `run_claimed`, `database_writes`, `artifact_sealed`, `evidence_persisted`, `approval_authenticated`, `model_artifact_verified`는 모두 false다.

승인 Reference/Hash의 구조 일치는 인증을 대신하지 않는다. 운영 실행은 공통 Run/승인·모델 Artifact 검증과 실제 Evidence 저장 연결 이후에만 허용한다. Python Adapter 주입은 신뢰 경계 안의 코드이며 임의 사용자 코드의 격리 실행 기능이 아니다.

## 7. 실행과 검증

```bash
PYTHONPATH=src python3 examples/strategy_contract_probe.py --strategy MATHEMATICAL
PYTHONPATH=src python3 examples/strategy_contract_probe.py --strategy PREDICTIVE_ML
PYTHONPATH=src python3 examples/strategy_contract_probe.py --strategy DEEP_RL
PYTHONPATH=src python3 -m unittest discover -s tests/unit -v
PYTHONPATH=src python3 -m unittest discover -s tests/contract -v
PYTHONPATH=src python3 -m unittest discover -s tests/integration -p '*offline.py' -v
```

Probe는 예제 입력에 고정 목표 200을 제출하며 출력에 `probe_only_not_a_trained_strategy=true`를 남긴다. DB/학습 실행이 아니다. 세 가족의 실제 알고리즘 동등성을 검증하는 것도 아니다.

현재 관측 Evidence 누적 크기 보호를 위해 `items * buckets * (buckets+1) / 2 <= 200000`을 적용한다. 미래 Calendar/수요, 공급 대기열, 정책 및 발주일 목록이 관측마다 반복되는 규모도 실행 전에 추산해 500000행 이하로 제한한다. 이는 바이트 단위 메모리 보장이나 성능 목표가 아니라 로컬 계산의 크기 방어다. 대규모 스트리밍 저장/성능 최적화는 별도 후속 작업이다.

검증 결과는 [구현 검증 기록](IO_REPLENISHMENT_STRATEGY_VERIFICATION.md)에 기록한다. 기존 수작업 Golden과 합성 BOH Generator 승인 수식은 바꾸지 않는다.

## 8. 다음 작업

수학적 정책 산출기·Profile·독립 Golden은 0.4.0에서 완료했다.

1. [합성 기반](IO_TRAINING_EVALUATION_CONTRACT.md)과 0.6.0 [생산 수학적 전략 평가 Adapter](IO_PRODUCTION_EVALUATION_CONTRACT.md)는 완료했다. 같은 정책/관측/Guard를 호출하고 연체·미매핑 공급도 공간 예약에 포함한다. 실제 Forecast Vintage/공급 이력 연결은 별도다.
2. ML 오차 예측·DRL 목표 조정 Adapter를 각각 구현하고 동일 환경에서 비교.
3. 실제 Source, 공통 Run/승인/모델 Artifact 검증과 Artifact·TB_IO 이중 기록 연결.
4. 대상별 Migration/DB Write·공용 Runtime 배포·운영 활성화는 별도 승인.

P0-14/18/19의 보류는 유지한다. 세 전략 지원 계약은 학습·정식 회귀 Gate·Publication을 완료한 것으로 간주하지 않는다.
