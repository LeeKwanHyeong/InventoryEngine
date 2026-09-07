# IO Synthetic BOH Generator 계약

## 1. 문서 목적과 상태

본 문서는 운영 실제재고 Source가 준비되기 전에 TGSM의 목표 Python 계약과 Demand → IO E2E를 검증하기 위한 `SYNTHETIC_BOH` 생성 계약을 정의한다.

| 항목 | 값 |
|---|---|
| 계약 ID | `io-synthetic-boh-generator-v1` |
| 상태 | `IMPLEMENTED_LOCALLY` (0.5.0); 운영 Artifact 봉인·업무 승인 후속 |
| 관련 의사결정 | `IO_ENGINE_TARGET_ARCHITECTURE.md`의 `P0-10` |
| 적용 범위 | 개발, Contract Test, Golden Test, E2E |
| Production 실제재고 사용 | 금지 |

이 계약의 결과는 운영 실제 BOH가 아니다. 모든 시작 Position에는 `POSITION_SOURCE_TYPE='SYNTHETIC_BOH'`를 기록한다. Legacy 회귀에서 `MAX_QTY`를 시작 Position으로 사용하는 `POLICY_PROXY` Mode와도 혼합하지 않는다.

---

## 2. 기본 결정

| 항목 | V1 계약 |
|---|---|
| Warm-up | 최소 52주 |
| 기본 Policy Lookback | 26주 |
| Lookback Fallback | 유효한 이전 이력이 13~25주이면 13주 |
| 13주 미만 이력 | `INSUFFICIENT_HISTORY`로 실패 |
| 표준편차 | 표본 표준편차, `ddof=1` |
| 기본 Service Level | 95%, `Z=1.6449` |
| 기본 Synthetic Lead Time | 2주 |
| 기본 보충주기 | 1주 |
| Base 입고 지연 | 없음 |
| 주차 내 처리 | 입고 → Backorder/수요 충족 → EOH → 발주 판단 |
| Warm-up Seed | Warm-up 시작 주차의 목표재고, Backorder/Open Order 0 |
| W0 BOH | W0-1의 EOH |
| 결정성 | 동일 입력·설정·Generator Version이면 동일 결과와 Hash |

기본 26주 Lookback과 52주 Warm-up에는 W0 이전 최소 78주 Demand History가 필요하다. 13주 Fallback이면 최소 65주가 필요하다.

---

## 3. 입력 계약

### 3.1 필수 입력

- Planning Cycle 및 Site Scope
  - `PLANNING_CYCLE_ID`
  - `PLANNING_CYCLE_REVISION_ID`
  - `CYCLE_SITE_EXECUTION_ID`
  - `COMPANY_CD`
  - `SUBS_CD`
  - `SITE_CD`
  - `PLAN_TYPE='TGSM'`
  - `PLAN_YYYYWW`
  - `W0_START_DATE`
- Demand Snapshot
  - `SITE_CD + ITEM_ID + YYYYWW`
  - `ACTUAL_DEMAND_QTY`
  - `DEMAND_SNAPSHOT_ID`
  - `DEMAND_CONTENT_HASH`
- Item 및 Policy Snapshot
  - Active/Stock 대상 여부
  - Lead Time
  - Service Level 또는 Synthetic Profile
  - MOQ
  - Lot Multiple
  - Legacy `MAX_QTY`
  - Policy Snapshot ID와 Hash
- Calendar Snapshot
  - 주차 ID, 시작일, 종료일과 순번

### 3.2 Demand History 정규화

- 각 Simulation 주차는 해당 주차 이전 Demand만 Policy 계산에 사용한다.
- Active 기간 안에 Calendar 주차는 존재하지만 Demand Row가 없으면 수요 0으로 채운다.
- Active 기간 밖의 주차는 Lookback에서 제외한다.
- `NULL`, 음수 또는 중복 Demand는 0으로 보정하지 않고 데이터 품질 오류로 처리한다.
- Demand History와 Item은 같은 `COMPANY_CD + SUBS_CD + SITE_CD + ITEM_ID` Scope를 통과해야 한다.

### 3.3 Lookback 선택

각 Simulation 주차 `t`에 대해 `t` 이전의 유효 이력만 사용한다.

```text
이전 유효 이력 >= 26주
    -> 직전 26주 사용

이전 유효 이력 13~25주
    -> 직전 13주 사용

이전 유효 이력 < 13주
    -> INSUFFICIENT_HISTORY
```

수요 패턴이나 변동성에 따라 13/26주를 자동 선택하지 않는다. 13주 고정 Scenario가 필요하면 실행 설정에 `LOOKBACK_WEEKS=13`과 사유 Code를 명시한다.

---

## 4. 정책 통계 계약

### 4.1 평균과 표준편차

```text
AVERAGE_WEEKLY_DEMAND = mean(demand[t-lookback : t-1])

DEMAND_STDDEV
    = sqrt(sum((x_i - mean)^2) / (n - 1))
```

- `DEMAND_STDDEV_METHOD='SAMPLE_DDOF_1'`을 기록한다.
- 모든 수요가 같으면 표준편차는 0이다.
- 계산 중간값은 고정 소수 정밀도를 유지하고 최종 수량만 UOM 규칙으로 올림한다.

### 4.2 Service Level과 Z-value

V1은 다음 Versioned Allowlist를 사용한다.

| Service Level | Code | Z-value |
|---:|---|---:|
| 90% | `SL_90` | 1.2816 |
| 95% | `SL_95` | 1.6449 |
| 97.5% | `SL_975` | 1.9600 |
| 98% | `SL_98` | 2.0537 |
| 99% | `SL_99` | 2.3263 |

- 기본값은 `SL_95`다.
- `Z_VALUE_MAPPING_VERSION='normal-service-level-v1'`을 Manifest에 기록한다.
- 품목별 등급을 Generator가 추론해 Service Level을 바꾸지 않는다. 다른 값은 Versioned Synthetic Policy Profile에서 명시한다.

### 4.3 Lead Time Profile

Lead Time은 Demand에서 추정하지 않는다. Source 우선순위는 다음과 같다.

1. Item Policy/Master의 유효한 `LEAD_TIME_WEEKS`
2. Part Family에 명시적으로 연결된 Versioned Synthetic Profile
3. 기본 `STANDARD=2주`

| Profile | Lead Time |
|---|---:|
| `SHORT` | 1주 |
| `STANDARD` | 2주 |
| `LONG` | 4주 |
| `EXTENDED` | 8주 |

V1은 `1 <= LEAD_TIME_WEEKS <= 52`만 허용한다. 품목 ID Hash를 이용한 무작위 Lead Time 배분은 기본 계약에서 사용하지 않는다.

---

## 5. 재고정책 수식

주간 평균수요를 `mu`, 표준편차를 `sigma`, Lead Time을 `L`, 보충주기를 `R=1`, Z-value를 `z`로 정의한다.

```text
SAFETY_STOCK
    = z * sigma * sqrt(L)

ROP
    = mu * L + SAFETY_STOCK

ORDER_UP_TO_QTY
    = mu * (L + R) + SAFETY_STOCK
```

EA 단위의 최종 정책 수량은 올림한다.

```text
SAFETY_STOCK_QTY    = ceil(SAFETY_STOCK)
ROP_QTY             = ceil(ROP)
ORDER_UP_TO_QTY     = ceil(ORDER_UP_TO_QTY)
```

주차 종료 시 Inventory Position은 다음과 같다.

```text
INVENTORY_POSITION
    = EOH_QTY + OPEN_ORDER_QTY - BACKORDER_CLOSE_QTY
```

발주 조건과 수량은 다음과 같다.

```text
if INVENTORY_POSITION > ROP_QTY:
    ORDER_QTY = 0
else:
    RAW_ORDER_QTY = max(0, ORDER_UP_TO_QTY - INVENTORY_POSITION)
    ORDER_QTY = 0                               if RAW_ORDER_QTY == 0
                ceil_to_lot(max(MOQ, RAW_ORDER_QTY), LOT_MULTIPLE) otherwise
```

- `MOQ`가 없으면 0으로 본다.
- `LOT_MULTIPLE`이 없으면 수량 UOM의 최소 단위 1을 사용한다.
- `MOQ`, Lot 반올림 전후 수량과 적용 근거를 Ledger에 보존한다.

---

## 6. Warm-up과 주차 처리 순서

### 6.1 Warm-up Seed

Warm-up 시작 주차 `W0-52`의 직전 Demand만 사용해 해당 시점의 정책을 계산한다.

```text
SIMULATION_START_BOH = 해당 시점 ORDER_UP_TO_QTY
BACKORDER_OPEN        = 0
OPEN_ORDER_QTY        = 0
RESERVED_QTY          = 0
```

난수는 사용하지 않는다. Reserved 재고는 Base Scenario에서 0이며 별도 Scenario에서만 명시한다.

### 6.2 주차 내 처리 순서

각 주차 `t`는 다음 순서로 처리한다.

1. `BOH_t` 확정
2. `ACTUAL_DUE_WEEK=t`인 입고 반영
3. 기존 Backorder와 현재 Demand를 합산
4. 가용재고 범위에서 출고
5. 미충족 수요를 Backorder로 이월
6. `EOH_t` 계산
7. 미도착 주문을 포함한 Inventory Position 계산
8. 해당 주차 이전 Demand만 사용해 Policy 갱신
9. Inventory Position이 ROP 이하이면 신규 주문 생성

```text
AVAILABLE_t
    = BOH_t + RECEIPT_t

TOTAL_DEMAND_t
    = BACKORDER_OPEN_t + DEMAND_t

FULFILLED_DEMAND_t
    = min(AVAILABLE_t, TOTAL_DEMAND_t)

EOH_t
    = AVAILABLE_t - FULFILLED_DEMAND_t

BACKORDER_CLOSE_t
    = TOTAL_DEMAND_t - FULFILLED_DEMAND_t

BOH_t+1
    = EOH_t
```

Lead Time이 1주라면 W0 종료 시 생성한 주문은 W1 시작 시 입고된다. 같은 주에 생성한 신규 주문으로 같은 주 수요를 충족시키지 않는다.

```text
PLANNED_DUE_WEEK_INDEX
    = ORDER_WEEK_INDEX + LEAD_TIME_WEEKS
```

Warm-up이 끝나면 다음을 봉인한다.

```text
SYNTHETIC_W0_BOH = EOH of W0-1
W0_OPEN_ORDER    = W0 시점 미도착 주문
```

---

## 7. Legacy MAX_QTY와 Synthetic Policy

`TGSM MAX_QTY`와 Synthetic 목표재고는 값의 크기로 우선순위를 결정하지 않는다. 실행 Mode가 사용할 의미를 결정한다.

### 7.1 Legacy 회귀 Mode

```text
INVENTORY_POSITION_MODE = POLICY_PROXY
START_POSITION          = LEGACY MAX_QTY
POSITION_SOURCE_TYPE    = POLICY_PROXY
```

### 7.2 목표 개발 Mode

```text
INVENTORY_POSITION_MODE = SYNTHETIC_BOH
START_POSITION          = W0-1 EOH
POSITION_SOURCE_TYPE    = SYNTHETIC_BOH
POLICY_SOURCE_TYPE      = SYNTHETIC_POLICY
```

- `SYNTHETIC_BOH` Mode에서 Synthetic Policy가 없으면 실패한다. `MAX_QTY`로 자동 Fallback하지 않는다.
- 원본 `MAX_QTY`는 `LEGACY_TARGET_INVENTORY_QTY`로 보존한다.
- 비교 Evidence는 다음을 포함한다.

```text
LEGACY_TARGET_INVENTORY_QTY
SYNTHETIC_ORDER_UP_TO_QTY
SYNTHETIC_W0_BOH
TARGET_QTY_DELTA
TARGET_QTY_RATIO
```

---

## 8. 입고 지연 Scenario

주문 원본을 수정하지 않고 예정 입고와 실제 입고를 분리한다.

```text
ORDER_WEEK
PLANNED_DUE_WEEK
ACTUAL_DUE_WEEK
DELAY_WEEKS
DELAY_SCENARIO_CODE
```

| Scenario | 계약 |
|---|---|
| `NO_DELAY` | `ACTUAL_DUE_WEEK = PLANNED_DUE_WEEK` |
| `FIXED_1W_DELAY` | 대상 주문을 1주 지연 |
| `FIXED_2W_DELAY` | 대상 주문을 2주 지연 |
| `SELECTED_ORDER_DELAY` | 명시한 주문만 지연 |
| `DISRUPTION_WINDOW` | 지정 기간에 도착 예정인 주문을 명시 주수만큼 지연 |

Golden Test에는 확률 지연을 사용하지 않는다. 확률 Scenario가 필요하면 Generator Seed와 확률 계약을 별도 Version으로 추가한다.

---

## 9. 출력 Dataset과 계보

Generator는 최소 다음 Artifact를 생성한다.

| Dataset | Grain | 용도 |
|---|---|---|
| `SYNTHETIC_INVENTORY_POLICY` | Simulation Run + Site + Item + Effective Week | 주차별 정책과 통계 근거 |
| `SYNTHETIC_INVENTORY_LEDGER` | Simulation Run + Site + Item + Week | BOH, 입고, 수요, 출고, Backorder, EOH, 주문 |
| `SYNTHETIC_PURCHASE_ORDER` | Simulation Run + Order ID | 주문, 예정·실제 입고주차와 지연 근거 |
| `SYNTHETIC_BOH_SNAPSHOT` | Simulation Run + Site + Item + W0 | IO Engine에 전달할 시작 Position |
| `GENERATOR_MANIFEST` | Simulation Run | 입력·설정·출력 Hash와 검증 상태 |

`SYNTHETIC_BOH_SNAPSHOT`은 다음 Canonical 필드를 포함한다.

```text
SIMULATION_RUN_ID
GENERATOR_VERSION
PLANNING_CYCLE_ID
PLANNING_CYCLE_REVISION_ID
CYCLE_SITE_EXECUTION_ID
COMPANY_CD
SUBS_CD
SITE_CD
ITEM_ID
POSITION_DATE
ON_HAND_QTY
RESERVED_QTY
AVAILABLE_QTY
POSITION_SOURCE_TYPE
DEMAND_SNAPSHOT_ID
POLICY_SNAPSHOT_ID
CONTENT_HASH
```

IO Engine은 이를 `TB_IO_INVENTORY_POSITION`에 `POSITION_TYPE='BOH'`, `POSITION_SOURCE_TYPE='SYNTHETIC_BOH'`로 Materialize한다.

미도착 주문은 `SITE_CD + ITEM_ID + DUE_WEEK + ORDER_ID` Grain으로 전달하고 `SUPPLY_TYPE='SYNTHETIC_PURCHASE_ORDER'`를 기록한다.

### 9.1 Manifest 필수 항목

```text
GENERATOR_CONTRACT_ID
GENERATOR_CONTRACT_VERSION
SIMULATION_RUN_ID
PLANNING_CYCLE_ID
PLANNING_CYCLE_REVISION_ID
CYCLE_SITE_EXECUTION_ID
DEMAND_SNAPSHOT_ID
DEMAND_CONTENT_HASH
POLICY_SNAPSHOT_ID
POLICY_CONTENT_HASH
CALENDAR_SNAPSHOT_ID
WARMUP_WEEKS
POLICY_LOOKBACK_WEEKS
STDDEV_METHOD
SERVICE_LEVEL_CODE
Z_VALUE_MAPPING_VERSION
LEAD_TIME_PROFILE_VERSION
REPLENISHMENT_CYCLE_WEEKS
ORDERING_RULE_VERSION
DELAY_SCENARIO_CODE
RANDOM_SEED
DATASET_ROW_COUNTS
DATASET_CONTENT_HASHES
VERIFICATION_STATUS
```

Base V1은 난수를 사용하지 않으므로 `RANDOM_SEED`는 `NULL`이다.

---

## 10. 불변조건

모든 주차와 품목에서 다음을 검증한다.

```text
BOH + RECEIPT - FULFILLED_DEMAND = EOH
BACKORDER_OPEN + DEMAND - FULFILLED_DEMAND = BACKORDER_CLOSE
EOH >= 0
BACKORDER_CLOSE >= 0
ORDER_QTY >= 0
W0 BOH = W0-1 EOH
ACTUAL_DUE_WEEK >= PLANNED_DUE_WEEK
PLANNED_DUE_WEEK_INDEX = ORDER_WEEK_INDEX + LEAD_TIME_WEEKS
```

추가 검증은 다음과 같다.

- 동일 입력과 Generator Version은 동일 Logical Content Hash를 생성한다.
- 어떤 Policy 계산도 해당 주차 또는 미래 Demand를 참조하지 않는다.
- `POLICY_PROXY`와 `SYNTHETIC_BOH` Row는 같은 Source Type으로 저장되지 않는다.
- Site, Item과 Calendar 고아 Row가 하나라도 있으면 Fail Closed한다.
- 실패 Artifact는 `VERIFIED` 상태로 노출하지 않는다.

---

## 11. Golden Scenario

| ID | Scenario | 핵심 검증 |
|---|---|---|
| `G01` | 일정 수요, 지연 없음 | 기본 보존식과 주기적 보충 |
| `G02` | 수요 급증 | ROP 도달과 보충 주문 |
| `G03` | BOH 0 | 품절과 Backorder 이월 |
| `G04` | MOQ 적용 | 최소 주문량 |
| `G05` | Lot 반올림 | 주문량 배수 올림 |
| `G06` | Lead Time 2주 | 주문·입고 주차 관계 |
| `G07` | 입고 1주 지연 | 예정/실제 입고 분리와 추가 품절 |
| `G08` | 간헐수요 | 0 수요 주차와 변동성 |
| `G09` | Legacy MAX_QTY 차이 | Policy Proxy와 Synthetic Policy 분리 |
| `G10` | 13주 Fallback | 짧은 이력의 명시적 처리 |
| `G11` | 13주 미만 | `INSUFFICIENT_HISTORY` |
| `G12` | 비활성 기간 | Active 기간 밖 Demand 제외 |
| `G13` | 정책 누락 | Fail Closed와 Data Quality Issue |
| `G14` | 고아 품목 | Site Item Master 무결성 오류 |

각 Scenario는 다음 주차별 기대값을 사람이 검토한 불변 Artifact로 보존한다.

```text
BOH
RECEIPT
DEMAND
BACKORDER_OPEN
FULFILLED_DEMAND
BACKORDER_CLOSE
EOH
OPEN_ORDER_QTY
INVENTORY_POSITION
ROP_QTY
ORDER_UP_TO_QTY
RAW_ORDER_QTY
ORDER_QTY
PLANNED_DUE_WEEK
ACTUAL_DUE_WEEK
```

### 11.1 대표 수작업 결과

설정:

```text
ROP_QTY             = 15
ORDER_UP_TO_QTY     = 30
MOQ                 = 10
LOT_MULTIPLE        = 10
LEAD_TIME_WEEKS     = 1
```

W0:

```text
BOH                 = 20
RECEIPT             = 0
DEMAND              = 8
EOH                 = 12
INVENTORY_POSITION  = 12
RAW_ORDER_QTY       = 18
ORDER_QTY           = 20
PLANNED_DUE_WEEK    = W1
```

W1:

```text
BOH                 = 12
RECEIPT             = 20
DEMAND              = 8
EOH                 = 24
INVENTORY_POSITION  = 24
ORDER_QTY           = 0
```

이 결과는 Golden Artifact, Generator 출력, IO 입력 Materialization과 `TB_IO_*` Evidence에서 동일해야 한다.

---

## 12. 구현 독립성과 승인 경계

- 합성 Generator는 IO Engine PSI 구현과 별도 모듈·Reference 수식으로 구성한다.
- Generator가 만든 기대값으로 같은 구현을 검증하는 자기검증을 금지한다.
- Golden Scenario의 입력과 기대 결과는 수작업 검토 후 별도 불변 Artifact로 봉인한다.
- 실제 DB Migration, Full Load, E2E DB Write와 공용 Runtime 배포는 별도 승인을 받는다.
- Production에서는 `SYNTHETIC_BOH`와 `POLICY_PROXY`를 실제재고로 게시하지 않는다.

---

## 13. 완료 조건

2026-09-03 현재 독립 생성기·입출력 Schema·수작업 Golden 대응·Canonical/PSI 호환은 로컬 구현/검증했다. [학습·평가 계약](IO_TRAINING_EVALUATION_CONTRACT.md)과 [검증 기록](IO_TRAINING_EVALUATION_VERIFICATION.md)을 따른다. 승인 계약의 기본 수식/주말 발주 순서는 유지한다. 명시적 입력 Profile로 Source 기본값을 고정하며, 미래 Label은 누락 시 항상 실패한다. 실제 Artifact 봉인, 사용자 업무 검토, DSIM 구현까지 완료했다는 뜻은 아니다.

P0-10은 다음 조건을 모두 충족하면 완료한다.

- Schema, Formula, Event Ordering과 Manifest가 승인됨
- 같은 입력·설정·Generator Version이 항상 같은 BOH, Open Order와 Hash를 생성함
- 모든 주차의 재고·Backorder 보존식이 성립함
- 주문과 예정·실제 입고주차의 관계가 성립함
- 14개 Golden Scenario의 수작업 결과와 Generator 결과가 일치함
- 원본 `MAX_QTY`, Synthetic 목표재고와 W0 BOH가 서로 다른 의미로 보존됨
- `POLICY_PROXY`, `SYNTHETIC_BOH`, Production `ACTUAL_BOH`가 Source Type으로 구분됨
- DSIM이 Simulation Run, 입력 Snapshot, 정책과 계산 근거를 추적할 수 있음
