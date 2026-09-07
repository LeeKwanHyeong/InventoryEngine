# 수학적 재고정책과 보충 권고 계약

기준일: 2026-09-03. 0.4.0 도입, 현재 패키지 `0.6.0`, 전략 `historical-normal-r-s / 1.0.0` 유지. 로컬 `DEVELOPMENT + LOCAL_SHADOW` 구현이다.

## 1. 완료한 범위와 선택한 계산 Profile

안전재고·ROP·목표재고를 실제 계산하는 `MATHEMATICAL` Adapter를 [공통 전략 계약](IO_REPLENISHMENT_STRATEGY_CONTRACT.md)에 연결했다. 고정 행동 Probe와 다르며 기존 Baseline과 ML/DRL Probe는 그대로 유지한다.

첫 Profile은 `HISTORICAL_NORMAL_R_S_V1`이다. 앞서 정리한 평균수요 기반 `(s,S)` 수식을 실행 가능한 기준선으로 고정했다. 이 Profile의 선택은 실제 운영 승인이나 통계적 적합성 검증을 의미하지 않는다.

- W0 이전 13주 또는 26주의 **검열되지 않은 수요** 평균·표준편차를 사용한다. 결품 때문에 관측된 판매량만 줄어든 `FULFILLED_SALES`는 이 Profile에서 거부한다.
- 통계는 `FROZEN_PRE_W0`로 계획 기간 전체에서 고정한다. 미래 실제 수요를 학습에 넣거나 Forecast를 과거 관측으로 추가하지 않는다.
- 미래 Forecast·확정 고객 주문은 기존 공통 PSI의 수요로 사용한다. 첫 수학적 Profile의 평균수요를 미래 Forecast로 바꾸지는 않는다. 추세/계절성·Forecast 오차 기반 정책은 후속 ML/정책 Profile 범위다.
- `stddev_ddof`는 0(모집단) 또는 1(표본)로 명시한다. 기본 예제와 Golden은 1이며 암묵적 기본값은 없다.
- Source의 `approved_service_level`은 **Cycle Service Level**이며 Fill Rate가 아니다. 이 Profile은 `0.5 <= level < 1`만 지원한다.
- Source `lead_time_days`, MOQ·배수·물리 용량, 실행 설정의 목표 범위·최대 주문량·발주일을 그대로 준수한다.

## 2. 수식과 시간·반올림

`μ`는 평균 주간수요, `σ`는 명시된 ddof의 주간 표준편차, `z`는 Source Service Level의 표준정규 분위수다.

```text
L = ceil(source_lead_time_days / 7)
R = profile.replenishment_cycle_weeks
SS_raw = z × σ × sqrt(L)
SS = ceil_to_uom(SS_raw)
ROP_raw = μ × L + SS
ROP = ceil_to_uom(ROP_raw)
TARGET_raw = ROP + μ × R
TARGET = ceil_to_uom(TARGET_raw)
```

- 완전한 7일 Bucket만 지원한다. 앞뒤 일부 주차는 임의로 환산하지 않고 `MATH_REQUIRES_FULL_WEEK_BUCKETS`로 거부한다. 다른 공통 전략의 부분 Bucket 지원 여부를 바꾸지 않는다.
- 1~7일은 1주, 8~14일은 2주다. 공통 PSI가 신규 권고를 다음 Bucket 시작으로 올려 매핑하므로 정책도 같은 노출 기간을 사용한다. 명시적 0일은 0주다.
- `R`은 1~52주의 목표 보충 주기다. **발주 가능일을 새로 생성하지 않으며** 판단 빈도와 발주일은 기존 공통 계약과 `order_dates`를 따른다.
- 각 단계의 수량을 UOM 단위로 순차 올림한다. 발주배수는 정책 수량이 아니라 실제 행동을 공통 검증할 때 적용한다. 예: EA는 정수, scale=3인 UOM은 0.001 단위다.
- Decimal 40자리·명시적 반올림 Context를 사용한다. `z`만 Python `NormalDist.inv_cdf`로 계산한 뒤 소수 12자리 HALF_EVEN으로 고정해 기록한다. Python/전략 버전 변경 시 Golden 재검증이 필요하며 모든 플랫폼에서 수학적으로 무한 정밀하다는 주장은 하지 않는다.
- 정책 중간값과 최종값, Source 일수와 환산 주수를 Evidence에 보존한다. 계산 결과가 계약 수량 한도 `10^12`를 넘으면 거부한다.

예: μ=10, σ=2, level=0.95, L=1, R=1 → z=1.644853626951 → SS=4, ROP=14, TARGET=24.

이 식은 리드타임 수요 변동의 정규근사다. 주기 검토 간격의 추가 불확실성, 가변 Lead Time, 간헐수요·계절성·수요 상관을 최적화하지 않는다. 설정한 95%를 실제로 달성한다고 보장하지 않으며 독립 평가 시나리오로 검증해야 한다. 기존 **52주 합성 BOH Generator**의 승인 수식이나 구현 상태는 변경하지 않는다.

## 3. 입력과 Hash 계약

공개 호출은 `RunMathematicalReplenishmentUseCase(deployment).execute(MathematicalPolicyRequest)`다.

```text
MathematicalPolicyRequest
  recommendation
    canonical_input             기존 8개 Snapshot 그대로
    execution / 1.1.0
      strategy_input_binding    contract/version/snapshot_id/content_hash
  policy_input                  io-mathematical-policy-input-v1 / 1.0.0
```

`policy_input`은 다음을 포함한다.

- Snapshot ID/Hash/SEALED 상태와 History Row Count
- Company/Subs/Site, Canonical 입력 Hash, Configuration Revision, W0-1 `as_of_date`, 입력이 알려진 `available_at`, 이력 Source/수량 의미
- Profile ID/Revision, Lookback, ddof, 보충 주기, Service Level 의미, Legacy Fallback 허용 여부, 승인 Reference
- W0 직전까지 연속된 13/26개 과거 Calendar 주차와 Item/UOM/주차별 수요
- 기간형 승인 Override와 Source/Legacy Fallback 레코드

추가 입력은 정규화한 후 Hash를 계산한다. 행을 정렬하고 Decimal/UTC 시각을 정규화한다. `policy_input_content()`는 Hash·상태·행 수를 제외한 내용 전체(식별자 포함)를 반환하며 상태/행 수는 별도로 검증한다. 이 함수는 **운영 봉인 서비스가 아니다**.

실행 설정 1.1.0은 `strategy_input_binding`을 필수로 받는다. Adapter의 불변 Binding과 일치해야 하며 관측에도 전달한다. 기존 1.0.0 요청과 Probe에는 새 필드를 강제하지 않는다. 모델/전략 Version에 데이터 Hash를 끼워 넣지 않는다.

이력·Profile·Override·Fallback 변경은 추가 Snapshot Hash와 전체 입력 Hash를 바꾼다. 기존처럼 물리 `engine_run_id`만 바뀐 Retry의 입력 Hash는 유지된다. 결과 계약 버전도 입력 실행 계약에 맞춰 1.1.0을 반환한다.

## 4. 품질 Gate와 정책 선택

Canonical Cut-off·Scope·Snapshot 검증과 공통 크기 Gate가 정책 계산보다 먼저 실행된다. 추가 입력도 다음을 검증한다.

- 실제 W0 직전 Calendar의 연속성·중복·계획 주차와의 중복 금지
- 고아 Item, 다른 UOM, 중복 Item/주차, 범위 밖 이력, 잘못된 정밀도 거부
- `available_at <= inventory_cutoff_at` 및 `approved_at <= available_at`. UTC 시각 객체로 비교하며 소수 초도 구분
- 과거 Calendar 13/26주는 필수. 품목별 이력 행 부족은 별도 **이력 부족** 상태이며 0으로 채우거나 짧은 평균을 계산하지 않음
- 모든 0수요 행이 실제로 존재하는 품목은 정상적인 완전 이력. 이력 자체가 없는 품목과 구분
- Override/Fallback의 필수 승인 근거, `SS <= ROP <= TARGET`, 유효기간, 같은 Item/종류의 기간 중복 금지

정책 우선순위는 다음과 같다.

1. 해당 판단일에 유효한 `APPROVED_OVERRIDE`
2. 전체 Lookback 이력이 있는 `PYTHON_CALCULATED`
3. 이력이 부족할 때 유효한 `SOURCE_FALLBACK`
4. 명시적으로 허용된 `LEGACY_FALLBACK`
5. `EXCLUDED`: 권고 없음, Baseline과 같은 재고 흐름은 계속 계산하고 이유 보존

Override/Fallback은 SS/ROP/TARGET **전체 세 값**을 함께 승인한 레코드로 받는다. 부분 필드 병합·승인 서비스·DB 존재 확인은 이번 범위가 아니다. Source의 기존 `source_rop_qty`·`source_target_inventory_qty`만 있다는 이유로 자동 Fallback하지 않는다.

승인 레코드에는 Item/UOM, 종류, 적용 기간, 값, 승인자/시각/Reference, 사유와 원천 문서 ID를 기록한다. 이 로컬 검증은 인증을 대신하지 않으며 `approval_authenticated=false`다. 유효한 Override가 있어도 Python 계산값과 원본 Source 값을 지우지 않는다.

## 5. 행동과 PSI 연결

```text
position = available BOH + 확정/기존 권고 미도착 공급 - 실제 Backorder
if effective policy exists and position <= ROP and position < TARGET:
    ORDER_UP_TO(TARGET)
else:
    HOLD(0)
```

입고·MOQ·발주배수·용량·발주일·행동 허용 범위는 공통 검증기가 최종 적용한다. 승인 Override도 이를 우회하지 못한다. Hard Constraint 충돌은 주문 조정/거부로 기록하며 이를 이유로 낮은 우선순위 정책을 자동 선택하지 않는다.

기간형 정책/Override는 각 Bucket 시작일의 `[effective_from, effective_to)`로 선택한다. 주중 변경은 다음 판단 경계부터 적용한다. 이미 주문한 권고는 소급 취소/변경하지 않는다. Horizon 밖 신규 주문 거부와 보수적 용량 예약도 기존 계약을 유지한다.

## 6. 중간 산출물과 실행

기존 관측·제안·행동 검증·주문·PSI에 더해 다음을 반환한다.

- `mathematical_policy_input`: 추가 입력 원본·Profile·승인 레코드
- `mathematical_policy_report.history_statistics`: 품목별 개수·누락 주차·평균·분산·표준편차·양수 수요 주차 수
- `policy_evidence`: 판단 ID별 Source 정책, Python 계산값, 실제 선택값/선택 Source, 적용 승인 ID, 계산·반올림 과정, Python−Source 비교
- `mathematical_policy_content_hash`, `excluded_policy_decisions`, `policy_coverage=COMPLETE/PARTIAL/NONE`

제안의 `calculated_policy`는 Python 계산 후보이고 실제 사용한 Override/Fallback과는 구분한다. 선택 정책은 위 `policy_evidence`에서 같은 `decision_id`로 확인한다. 제약 적용 후 실제 주문량은 기존 `decision_evidence.validation`이 권위 결과다.

```bash
IO_COMPANY_CD=DSE IO_ENVIRONMENT=DEVELOPMENT PYTHONPATH=src python3 -m dsio_inventory_engine recommend-mathematical --request examples/mathematical_input.json
```

예제는 수작업 합성 이력이며 운영 Snapshot이 아니다. 정책 4/14/24, 권고 주문 14·10, EOH 0→4→4를 반환한다. `--read-postgres`는 금지된다. Run Claim·영속 저장·실제 ERP 발주·ML/PPO 학습은 하지 않는다.

## 7. 다음 작업

- 완료 기준선: 수학적 계산·승인 정책 선택·공통 PSI 연결·[독립 Golden](../../tests/fixtures/GOLDEN_MATHEMATICAL.md).
- 현재 기준선(0.6.0): [독립 합성 기반](IO_TRAINING_EVALUATION_CONTRACT.md)과 [생산 전략 평가 연결](IO_PRODUCTION_EVALUATION_CONTRACT.md) 완료. 정책 준비/관측 생성기를 공유하고 실제 상태로 매주 판단한다. 수식·정책 선택·기존 PSI 결과는 유지하며 `REFERENCE_R_S`와 별도 평가한다.
- 그 이후: ML/DRL Adapter를 각각 구현하고 동일 정보·평가 구간에서 비교.
- Source/Run/Evidence 저장은 별도 구현하며 Migration·실제 DB Write·공용 Runtime 배포는 대상별 승인 필요. P0-14/18/19 보류와 Multi-Echelon/DSIM 후속 범위는 유지.

검증 범위는 [0.4.0 검증 기록](IO_MATHEMATICAL_POLICY_VERIFICATION.md)을 참조한다.
