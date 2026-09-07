# 합성 학습·평가 기반 계약

기준일: 2026-09-03. 0.5.0 도입, 현재 InventoryEngine `0.6.0`, 입력 계약 `io-training-foundation-v1 / 1.0.0` 유지.
상태: **개발 로컬 구현 완료**. DB 적재, 운영 Snapshot 봉인, ML/PPO 학습은 수행하지 않는다.

## 1. 구현 경계

| 구성 | 실제 구현 | 구분해야 할 대상 |
|---|---|---|
| `training/reference.py` | 최소 52주 독립 재고 Simulation, 주차별 정책·주문·BOH·미도착 공급 | 기존 `advance_bucket`/수학적 정책 함수를 호출하지 않음 |
| `training/dataset.py` | 과거만 사용한 Forecast Feature와 별도 미래 Label, 시간순 3개 구간 | 실제 DemandEngine Forecast 수집기나 학습된 수요 모델이 아님 |
| `training/evaluation.py` | 같은 `observe → step → result` 경로, 비용·서비스·입고지연 평가 | 실제 Planning PSI 또는 기존 전략 Protocol Adapter가 아님 |
| `training/prepare.py` | 모든 구간 초기재고, TEST 구간 HOLD/REFERENCE_R_S 비교와 Manifest | 세 운영 전략 성능 비교·PPO 학습 완료가 아님 |
| CLI `prepare-training` | 입력 Hash/Scope 검증 후 JSON 반환 | `--read-postgres`, Production 실행 금지 |

World는 공통 수량/JSON 자료형만 재사용한다. 합성 기대값을 생산 PSI로 생성하거나 생산 PSI를 합성 World로 재사용하지 않는다. 0.6.0의 [생산 전략 평가 Adapter](IO_PRODUCTION_EVALUATION_CONTRACT.md)는 `training` 밖에서 실제 정책·공통 Guard를 호출한다. 기존 Canonical·전략 1.0/1.1과 Reference 기본 결과는 유지한다.

## 2. 입력과 재현성

입력 [Schema](../../schemas/training_foundation.schema.json)는 다음을 고정한다.

- 단일 Company/Subsidiary/Site, POSM/TGSM, 설명용 Planning Cycle/Revision과 Simulation ID.
- Calendar·Demand·Policy Snapshot ID, 전체 입력 `content_hash`, 배열 내용과 Configuration Revision.
- `warmup_weeks` 52~260, `lookback_weeks` 13/26, 보충주기 1~52주.
- 품목별 UOM 최소 수량, MOQ·Lot·물리 용량, Lead Time 1~52주, Service Level과 명시적 Source/Profile 참조.
- 비용·서비스·지연 Scenario. 입력 값은 개발 설정이며 운영 승인 인증을 대신하지 않는다.

Calendar는 전달된 `yyyyww`와 연속 7일 구간을 그대로 사용한다. 실제 Calendar를 ISO 주차로 재계산하지 않는다. 예제 작성기만 명시적으로 ISO Calendar Fixture를 만든다. 입력 최대 520주·100품목·20시나리오, 주차×품목×시나리오 20,000 이하로 제한한다. CLI JSON은 8 MB 이하이다.

배열을 정규화한 전체 입력 Hash를 확인한다. 출력에 입력 원본, Snapshot별 Hash, Dataset Hash/Row Count, Version·Simulation ID를 남긴다. 난수는 사용하지 않으므로 Seed는 `null`이다. 소수 계산은 고정 Decimal Context를 쓰며 외부 Decimal 설정에 영향을 받지 않는다. 입력·출력의 물리 봉인/승인을 수행한 것으로 표시하지 않는다.

## 3. 독립 합성 BOH

[기존 승인 계약](IO_SYNTHETIC_BOH_GENERATOR_CONTRACT.md)의 수식과 순서를 유지한다.

- 기본 26주 통계 + 52주 Warm-up = W0 이전 78주. 유효 이력이 13~25주이면 13주 Fallback과 근거를 기록한다. 13주 미만은 실패한다.
- Active 시작 전 이력은 제외한다. Warm-up 이전에 적어도 13주 Active 이력이 필요하며 이후는 계속 Active인 V1 모델이다. 중간 활성/비활성 전환과 Reserved 재고 시나리오는 후속이다. 감소·단종 수요는 예제에서 실제 0 수요로 표현한다.
- 명시적 `MISSING_ACTIVE_WEEK_IS_ZERO`일 때만 W0 이전 누락을 0으로 채우고 Row별 근거를 남긴다. 기본 예제는 `REJECT`다. 학습/검증/평가의 미래 Label 누락은 항상 실패하며 0으로 추정하지 않는다.
- 표본 표준편차 `ddof=1`, 기존 `normal-service-level-v1`의 Z Allowlist를 사용한다. Service Level을 코드명이나 품목 ID에서 추론하지 않는다.
- `SS=z×σ×√L`, `ROP=μ×L+SS`, `TARGET=μ×(L+R)+SS`. **원시 수식 각각을** UOM 최소 수량으로 올림한다. 0.4.0 생산 정책의 단계별 올림/FROZEN_PRE_W0 방식과 구분한다.
- Seed BOH는 Warm-up 시작 직전 이력으로 계산한 목표재고다. 초기 BO/Open Order/Reserved는 0이다. Seed가 물리 용량을 초과하면 임의 축소하지 않고 실패한다.
- 입고 → 이전 BO/현재 실제 수요 → EOH → 주말 발주. `IP=EOH+미도착 주문−BO`, `IP<=ROP`이면 목표까지 보충한다. 필요량 0을 MOQ로 부풀리지 않는다.
- MOQ·Lot과 용량은 함께 만족시킨다. 예컨대 MOQ=100, Lot=50, 잔여 용량=120이면 100까지 가능하며 120을 허용하지 않는다.

`W0 BOH = W0-1 EOH`와 미도착 주문/Backorder를 함께 반환한다. 실제 BOH 대신 `MAX_QTY`를 사용하는 자동 Fallback은 없다. 원본 Legacy 목표와 합성 목표·시작재고는 별개 필드다.

[출력 Schema](../../schemas/synthetic_boh.schema.json)는 `positions`와 `open_orders`를 검증한다. Canonical 연결 계약 테스트는 실제 생성된 수량을 테스트용 봉인 Envelope에 넣어 기존 Cut-off/PSI를 통과시킨다. **운영 Source Adapter 또는 Artifact 봉인 구현은 아니다.** 물리 `engine_run_id` 부여·실제 입력 봉인은 이후 Run/Evidence 단계에서 한다.

## 4. 학습/검증/평가 분리

예제는 130주·5품목(정상/간헐/감소/급증/0수요)이다.

| 구간 | 주차 Index, 끝 제외 | 역할 |
|---|---|---|
| 과거 준비 | `[0,78)` | 26주 통계와 52주 Warm-up |
| TRAIN | `[78,104)` | 26주 학습 후보 |
| VALIDATION | `[104,117)` | 13주 설정 선택 후보 |
| TEST | `[117,130)` | 13주 독립 평가 후보 |

- 각 Feature는 판단 주 이전 13/26주 이력만 가진다. 해당 주 Forecast는 과거 평균의 `SYNTHETIC_CAUSAL_MEAN`으로 명시한다. 실제 DemandEngine Snapshot인 척하지 않는다.
- Label은 그 주 실제 수요이며 다음 주 시작에 알 수 있다고 가정한다. Feature와 별도 배열·Hash에 보존한다. Horizon은 1주여서 Label이 구간 경계를 넘지 않는다.
- 구간 Shuffle/전체 이력 Scaler Fit을 하지 않는다. 후속 학습기에서 TRAIN으로만 학습하고 VALIDATION으로 선택한다. TEST 성능을 보고 모델을 재선택하면 새 Holdout이 필요하다.
- 과거 수요가 다음 주 시작에 바로 확정된다는 **합성 정보 가용성 가정**이다. 실제 ERP 지연 확정·Forecast 발행 Timestamp는 별도 Source 검증이 필요하다.
- 세 구간은 각 시작 직전 52주를 독립 Simulation하여 초기화한다. 학습 중 선택한 행동/재고 상태를 평가 초기재고로 넘기지 않는다. 같은 Scenario에서 비교하는 전략은 같은 초기재고와 공급을 사용한다.

## 5. 평가 World의 시간·정보·행동

`ReferenceEpisode`는 Development 전용, 한 주 한 번 다음 순서로 진행한다.

1. `observe()`에서 주초 BOH/실제 BO, 예정 미도착 주문, 과거 이력/예측, Source 제약, 적용 Service Level·비용을 조회한다.
2. 전략이 품목별 수량을 `step({item_id: quantity_string})`으로 제출한다. 0은 HOLD다. 모든 품목을 명시해야 한다.
3. 전체 품목 수량을 먼저 검증한다. MOQ·발주배수·보수적 용량을 적용하고 조정 사유를 남긴다. 검증 실패 시 일부 품목만 반영하지 않는다.
4. 신규 주문 등록 → 실제 입고 → 이전 실제 BO → 현재 실제 수요 → EOH/BO → 비용 순으로 전이한다. 기본 Reference 경로는 Lead Time 최소 1주여서 신규 주문의 당주 입고가 없다. 생산 Adapter의 `step_admitted`는 공통 Guard가 허용한 0일/기간형 Lead Time과 도착 Bucket을 그대로 소비할 수 있다.
5. 마지막 주 이후 `result()`에서 비용·서비스·주문·잔존 재고를 조회한다.

이 World의 수요는 **실현된 전체 실제 수요**다. Forecast를 실제 수요에 더하지 않는다. 평가의 미충족 실제 수요는 BO로 이월하되, 생산 PSI의 Forecast Shortage를 BO로 바꾸는 것이 아니다.

미래 실제 수요, 실제 도착 주차, 지연 Profile은 관측에 넣지 않는다. 원래 예정일이 지났다면 Overdue만 표시한다. Python 객체는 신뢰된 로컬 연구 코드용이며 악의적인 모델 코드를 격리하는 보안 Sandbox는 아니다. 출력 전체에는 감사용 Ground Truth가 있으므로 모델 관측은 반드시 `observe()`를 사용한다.

NO_DELAY/고정 1·2주/선택 주문/Disruption Window를 지원한다. 원래 예정일을 수정하지 않고 실제 도착일을 별도로 계산한다. 선택한 주문이 실제로 발생하지 않으면 지연도 발생하지 않으므로 평가 주문 목록에서 적용 여부를 확인한다. 보유 Horizon 밖 납기는 합성 7일 Calendar 연장으로 표현하고 미도착 주문을 삭제하지 않는다. 실제 휴일/영업일 Calendar 의미로 사용하면 안 된다.

`prepare-training`은 기존 `HOLD`와 `REFERENCE_R_S`만 비교한다. 후자는 생산 `historical-normal-r-s`나 학습된 전략이 아니다. 0.6.0 `evaluate-mathematical`은 생산 관측·제안·승인 제약 Adapter를 통해 별도로 평가한다. 주문 Calendar·기간형 정책·Override는 생산 Guard가 적용하며 World가 다시 해석하지 않는다. 네트워크·확률 지연은 후속 범위다.

기존 Reference의 지연 기본값은 Warm-up을 포함한다. 새 생산 평가 실험은 `delay_scope=EPISODE_RECEIPTS_ONLY`로 구간 시작 이후가 원래 예정일인 입고에만 지연을 적용한다. 시작 연체 공급의 생산 Cut-off 거부를 우회하지 않는다. 두 초기화/종료 조건 차이를 무시한 전략 순위 비교는 금지한다.

## 6. 비용·서비스 정의

Scenario의 단위 비용은 각 Item UOM 1단위에 적용하며 같은 Scenario에서는 모든 품목에 동일한 값이다. USD/KRW/EUR 중 한 통화만 사용하고 통화 혼합/환산은 없다. 실비 추정값이 아닌 **명시적 합성 가중치**다.

```text
weekly_cost = EOH × holding_per_unit_week
            + closing_BO × backlog_per_unit_week
            + (positive_order ? fixed_per_order : 0)
            + new_order_qty × purchase_per_unit
last_week_cost += closing_BO × terminal_backlog_per_unit
reward = -weekly_cost
```

- 구매 비용은 주문 생성 시점에 부과한다. 초기 미도착 주문의 구매 비용은 비교 전략 모두의 Sunk Cost로 제외한다.
- 마지막 주 재고·미도착 공급은 수량을 별도 보존하며 잔존 자산/Salvage 가치는 주지 않는다. 기간 내 비교 기준일 뿐 실제 총수명 경제성이나 최적 전략을 증명하지 않는다. PPO 본학습 전 Terminal Value/긴 Horizon 민감도를 확인해야 한다.
- 품목별 `on_time_fill_rate = 당주 수요의 당주 충족량 / 당주 수요 합계`. 이전 BO를 늦게 해소한 양은 분자에 더하지 않는다. 분모가 0이면 `null`이다. 서로 다른 UOM을 합산한 전사 Fill Rate는 만들지 않는다.
- 당주 수요 품절 주수와 BO 잔존 주수를 분리한다. 이 값을 재고정책의 Cycle Service Level과 같다고 해석하지 않는다.
- 실제 Source 제약보다 유리한 데이터만 고르는 모델 선택은 하지 않는다. 비용만 높은 Scenario, Service Level 99%, 1·2주 지연과 특정 기간 장애를 각각 고정한다.

## 7. 실행과 남은 작업

프로젝트 루트에서 다음 예제를 실행하면 JSON 결과를 stdout으로 반환한다.

```bash
PYTHONPATH=src python3 examples/training_foundation.py
PYTHONPATH=src python3 examples/training_foundation.py --request-only
IO_COMPANY_CD=DSE IO_ENVIRONMENT=DEVELOPMENT PYTHONPATH=src python3 -m dsio_inventory_engine prepare-training --request examples/training_input.json
```

1. **현재 기준선 — InventoryEngine 평가 Adapter 완료:** 0.6.0에서 생산 수학적 정책/공통 Guard와 실제 재고 상태 피드백을 연결했다. [새 평가 계약](IO_PRODUCTION_EVALUATION_CONTRACT.md)을 따르며 Reference 성능과 구분한다.
2. **다음 작업 — ML/PPO 모듈:** 같은 고정 구간에서 학습·검증하고 정확한 모델 Version/Hash를 연결한다. 모듈은 독립 개발 가능하나 공통 계약 변경은 직렬 검토한다.
3. **외부 작업 대기 — 실제 학습 자료:** DemandEngine의 과거 발행 Forecast/Actual과 관측 공급지연·실비를 연결한다. 합성 자료로 개발을 진행할 수 있으나 운영 정확성 승인은 별도다.
4. **후속/승인 필요 — Run·Evidence·E2E:** Artifact/TB_IO 저장, 실제 DB Write, 공용 Runtime 배포는 별도 범위다. P0-14/18/19 보류는 유지한다.

수작업 근거는 [Golden](../../tests/fixtures/GOLDEN_TRAINING.md), 실제 실행 결과는 [검증 기록](IO_TRAINING_EVALUATION_VERIFICATION.md)을 참조한다.
