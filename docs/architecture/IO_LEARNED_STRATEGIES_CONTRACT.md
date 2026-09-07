# ML/PPO 학습·추론 및 동일 조건 평가 계약

기준일: 2026-09-03. InventoryEngine 0.7.0의 **개발 로컬 구현 계약**이다. 실제 학습을 수행하지만 운영 모델 승인·DB 게시·Artifact 서비스 봉인은 하지 않는다. [기존 공통 전략 계약](IO_REPLENISHMENT_STRATEGY_CONTRACT.md)과 [생산 전략 평가 계약](IO_PRODUCTION_EVALUATION_CONTRACT.md)을 유지한다.

## 1. 현재 구현 경계

| 구분 | 구현 | 실행 시점 |
|---|---|---|
| 수학적 전략 | 기존 이력 기반 안전재고·ROP·목표재고 | 계산·평가 |
| PREDICTIVE_ML | 보호기간의 양의 누적 Forecast 오차 분위수 → 안전재고·ROP·목표재고 | 별도 CPU 학습 / JSON 추론 |
| DEEP_RL | PPO-Clip Actor–Critic → HOLD 또는 목표재고 배율 선택 | 별도 CPU 학습 / JSON 추론 |
| 공통 실행 | 기존 관측 생성·MOQ/배수/용량/발주일/납기 Guard·PSI | 모든 전략 동일 |
| 평가 World | 실제 수요·지연 입고·Backorder를 처리하는 독립 World | 로컬 합성 평가 |

학습은 `fit_replenishment`, 추론은 `learned`, 계약은 `inventory_contracts`에 둔다. `torch_models`만 PyTorch를 사용한다. Planning Cycle 추론은 Torch·NumPy·pickle·원격 모델 검색 없이 JSON 가중치로 실행한다. 학습 중에도 기존 생산 Guard가 승인한 주문만 World에 등록한다. World의 `training` 패키지에는 생산 전략 import를 추가하지 않았다.

공통 `BASELINE`/`RECOMMENDED` PSI 구분은 그대로다. 실제 수요 평가의 `NO_NEW_SUPPLY_CONTROL`은 새 주문을 내지 않는 대조군이며, 예측 기반 Baseline PSI나 연구용 `REFERENCE_R_S`와 같은 결과라고 부르지 않는다. 모델의 모델/전략 Version은 시나리오와 별개다.

## 2. 관측과 모델 고정

`io-learned-observation-v1`은 공통 생산 관측에서 다음 14개 수치만 고정된 순서로 추출한다. 수량의 분모는 `max(사전 이력 평균 주간수요, UOM 최소 단위)`다.

| 순서 | Feature | 의미 |
|---|---|---|
| 0–3 | available, backorder, pending, position / mean | 현재 가용재고·미납·미도착 공급·재고 Position |
| 4–5 | target, rop / mean | 승인 선택을 적용한 기준 목표·ROP |
| 6 | stddev / mean | 사전 이력의 표본 표준편차 |
| 7 | lead_weeks / 52 | Source Lead Time의 Calendar 주차 변환 |
| 8–9 | service_level, zero_demand_share | Source 서비스수준과 사전 이력의 무수요 비중 |
| 10–11 | remaining_weeks / 52, overdue / mean | 남은 기간과 예정일이 지난 미도착 공급 |
| 12–13 | protected_demand / mean, next_demand / mean | L+R 기간 및 이번 주의 알려진 순 Forecast+고객 확정 주문 |

- 학습 정규화 계수는 TRAIN에서만 추정하고 표준편차 하한은 `0.000001`이다. 추론은 고정 계수를 적용하며 표준화 값은 ±10으로 제한한다. 원래 Feature·정규화 값·잘린 Feature 이름을 Evidence에 남긴다.
- 미래 실제 수요, 실현 입고일, 이후 재고, 평가 정답은 Feature에 들어가지 않는다. 예정일과 현재 시점까지 확인된 지연 여부는 사용한다.
- 정책 이력은 계획 시작 이전에 고정한다. ML 학습 표본은 각 TRAIN 판단 시점 이전 Lookback만 사용한다. 모델에는 Lookback, 표본 ddof=1, 보충주기 계약도 고정하여 다른 Profile에 실수로 적용하지 못하게 한다.
- ML은 공통 벡터 중 6–9만 사용하여 불확실성을 추정한다. PPO는 14개를 모두 사용한다. 비용은 현재 보상 Profile에만 있고 관측에는 없으므로, 비용 변화에 맞춰 항상 최적 반응하는 모델이라는 주장은 하지 않는다.

`io-replenishment-model-v1 / 1.0.0`은 모델 ID·Version·Content Hash, 알고리즘/Feature Version, 단일 Company/Site/Plan Type/Configuration Scope, TRAIN 데이터 Hash, 학습 종료일·Run ID·Profile Hash·Runtime Version, 정규화 계수와 고정 Shape 가중치를 담는다. 전체 JSON의 자기 Hash를 제외한 내용을 SHA-256으로 검증한다. 별도로 전달한 `model_reference`와도 정확히 일치해야 한다.

모델 학습 종료일은 추론 계획 시작일보다 앞서야 한다. 개발 합성 주간 수요는 해당 주 마감에 확정된다고 가정한다. 실제 ERP의 거래/마감 시각 가용성은 이 날짜 단위 검증만으로 증명하지 않으며 실제 Source 연결 시 검증해야 한다. 모델 Hash 검증은 업무 승인·전자서명 인증이나 저장소 봉인을 의미하지 않는다.

[모델 Schema](../../schemas/replenishment_model.schema.json), [학습 요청 Schema](../../schemas/learned_training.schema.json), [추론 요청 Schema](../../schemas/learned_inference.schema.json)를 제공한다. 참조 URN은 인접 Schema의 로컬 Registry로 해결하며 네트워크에서 내려받지 않는다. DTO는 Schema 외에 수치 범위·Hash·시점·Scope·교차 계약을 검증한다.

## 3. ML 학습과 적용

`positive-error-quantile-linear-v1`은 Linear(4→1)+Softplus 모델을 Pinball Loss로 학습한다. 목표 분위수는 Source의 승인 서비스수준이다. 표본의 정답은 다음과 같다.

```text
H = Source Lead Time L + 보충주기 R
label = max(0, H주 실제 누적수요 − 판단 직전 이력으로 만든 고정 Forecast × H) / scale
```

정답 전체가 TRAIN 안에 존재하는 표본만 쓴다. 누락 이력을 0으로 바꾸거나 VALIDATION/TEST 정답을 포함하지 않는다. 현재 Forecast는 합성 `SYNTHETIC_FROZEN_MEAN`이며 실제 DemandEngine Forecast 발행 이력이 아니다.

추론한 분위수×scale을 UOM 단위로 올려 안전재고로 사용한다. ROP=`mean×L+SS`, 목표재고=`ROP+mean×R`이며 기존 순차 UOM 올림을 적용한다. IP≤ROP일 때 목표재고 주문을 제안한다. 이는 보호기간 오차 기반의 개발 기준 모델이며 실제 Cycle Service Level 달성 보장이 아니다. 기존 수학적 정규근사와 안전재고 산출 방식이 의도적으로 다르다.

## 4. PPO 학습과 적용

`ppo-clip-target-mlp-v1`: 14→16→16의 tanh 공통 층, 4개 행동 Actor와 스칼라 Critic이다.

| 행동 | 제안 |
|---|---|
| HOLD | 주문하지 않음 |
| TARGET_075 | 기준 목표재고×0.75, 단 ROP 미만으로 내리지 않음 |
| TARGET_100 | 기준 목표재고 유지 |
| TARGET_125 | 기준 목표재고×1.25 |

배율 행동도 IP≤ROP의 발주 조건을 따른다. 학습에서는 Categorical로 행동을 샘플링하고 추론/평가에서는 최대 확률 행동을 결정론적으로 선택한다. 동률이면 작은 인덱스를 선택한다. 샘플링한 원래 행동의 log probability를 PPO 손실에 사용하며 Guard가 조정한 최종 주문량의 확률로 바꿔치기하지 않는다.

- 실제 실행된 행동 이후 품목별 World 비용의 음수÷100을 보상으로 사용한다.
- Gamma=0.99, GAE lambda=0.95, Clip=0.2, Value 계수=0.5, Entropy 계수=0.01, Gradient norm=0.5, Adam=0.0003, Mini-batch=128, KL 조기 중단=0.03이다.
- 품목별 궤적을 분리해 GAE를 계산한다. 종료 Backorder 비용을 명시한 **유한 계획기간 문제**이므로 최종 Bootstrap은 0이다. 계속 운영하는 무한기간 문제의 시간제한 종료를 0으로 처리하는 일반 규칙이 아니다.
- 정렬된 시나리오를 순환하고 최종 TRAIN Checkpoint를 사용한다. VALIDATION/TEST로 Epoch나 Checkpoint를 선택하지 않는다.
- Seed·Profile·CPU float64·Torch Version을 고정하고 호출 전후 RNG와 Thread 설정을 복구한다. 서로 다른 Torch/장치 사이 비트 단위 재현은 주장하지 않는다.

구현의 Clip 목적함수와 on-policy 갱신은 [PPO 원 논문](https://arxiv.org/abs/1707.06347), [OpenAI Spinning Up PPO 설명](https://spinningup.openai.com/en/latest/algorithms/ppo.html)을 따른다. 운영 발주 행동 공간·제약/기간·보상 해석은 본 IO 계약의 추가 제한이다.

## 5. Source 우선순위와 실패 처리

승인 Override와 이력 부족 시 승인 Source/명시 Legacy Fallback은 두 모델보다 우선한다. 이력 부족·Fallback 없음은 계산 제외다. 모델은 PYTHON_CALCULATED로 선택된 정책에만 적용한다. Source의 MOQ·발주배수·물리 용량·발주 Calendar·승인 수량 제한·Lead Time은 기존 공통 Guard가 검증한다. 기간 밖 도착 주문은 공통 규칙대로 거부한다.

모델 손상·Version/Hash·Scope·정책 Profile 불일치, 미봉인 입력, Cut-off 오류는 Fail Closed다. 다른 모델·최신 모델·수학적 전략으로 조용히 전환하지 않는다. 운영 전략 선택/승인은 별도다.

## 6. PPO 업데이트 전 종료·장기 기간 검증

먼저 원래 TRAIN 안에서 13주/26주 및 Source 종료 Backorder 비용/0을 비교한다. 이후 TRAIN 수요만 주기적으로 반복한 52주/104주 스트레스 자료로 무지연·최대 고정 지연을 비교한다. 기존 VALIDATION/TEST 수요는 읽지 않으며, 장기 스트레스 자료를 PPO 학습에 추가하지 않는다. 임의 실측 자료를 연장한 것으로 부르지 않는다.

동일 초기재고·미도착 공급·World·Source Guard 아래 수학적 전략/무신규공급 대조군의 비용·Terminal BO/재고·잔여 공급을 기록한다. `STRUCTURAL_PREFLIGHT_PASSED`는 구조적 실행 확인이고 운영 강건성 합격이 아니다. 52/104주는 **합성 반복 스트레스**, 최종 TEST는 원래 13주 홀드아웃이다. 장기 비반복 독립 데이터와 여러 Seed의 성능 검증은 다음 작업이다.

## 7. 중간 산출물과 실행

학습 출력은 모델 JSON·Reference, 사전 점검, Label Hash·Loss, PPO 반복별 비용·Update 수·KL/Entropy/Clip 비율·가중치 변경 Hash를 포함한다. `evaluate_learned`는 입력 Snapshot·정책 보고서·모델·관측/제안/Guard·Feature·World Ledger/주문/비용을 반환한다. `compare_strategies`는 그 결과의 Hash와 지표를 요약한다. 요약 Hash만 저장했다고 원본 상세 Evidence가 영속 저장된 것은 아니다.

기본 예제는 CPU ML 300회, PPO 12개 TRAIN Episode(1,560 Item-week·최대 4 Update Epoch), Seed 20260903이다. 로컬 학습 상한을 DTO에서 제한한다. `--quick`은 20회/1개 Episode로 계약 점검용이다. 이 실행은 장기간 PPO 본학습이나 수렴 증명이 아니다.

```bash
# 선택 의존성 설치가 필요한 환경에서만 수행
python3 -m pip install -e '.[learning]'

# 기본 합성 CPU 학습 + 동일 조건 VALIDATION/TEST 비교
PYTHONPATH=src python3 examples/learned_strategies.py

# Recipe만 출력; 학습 CLI는 Torch가 있는 개발 환경에서 실행
PYTHONPATH=src python3 examples/learned_strategies.py --quick --request-only | IO_COMPANY_CD=DSE IO_ENVIRONMENT=DEVELOPMENT PYTHONPATH=src python3 -m dsio_inventory_engine train-learned --request /dev/stdin

# 저장된 개발 점검 모델의 추론: Torch 불필요
PYTHONPATH=src python3 examples/learned_inference.py --strategy DEEP_RL
PYTHONPATH=src python3 examples/learned_inference.py --request-only | IO_COMPANY_CD=DSE IO_ENVIRONMENT=DEVELOPMENT PYTHONPATH=src python3 -m dsio_inventory_engine recommend-learned --request /dev/stdin
```

결과는 stdout JSON이다. 이번 [로컬 점검 증적](evidence/learned-strategies-smoke-20260903.json)은 파일로 보존했지만 `TB_IO_*`·Artifact 서비스·공통 Run 게시가 아니다. `model_trained=true`는 학습 UseCase에서만 반환하며, 추론 시에는 재학습하지 않는다. 모든 새 실행은 DEVELOPMENT만 허용하고 `--read-postgres`를 거부한다.

## 8. 남은 순서

1. **현재 기준선 — 학습 안정성 검증 완료:** 0.8.0 [다중 Seed·선정/신규 홀드아웃](IO_LEARNING_STABILITY_CONTRACT.md)을 실행했다. 모델 성능 미달과 후속 개선은 [검증 기록](IO_LEARNING_STABILITY_VERIFICATION.md)을 따른다. 이 문서의 0.7.0 알고리즘·과거 TEST는 보존한다.
2. **외부 작업 대기 — 실제 Source:** DemandEngine Forecast 발행 당시 Snapshot/Actual, 비용·확약/지연 자료를 확보해 동일 시점 계약으로 평가한다.
3. **다음 작업 — 공통 Run/모델·Evidence 보존:** 모델 불변 Artifact/승인 Reference를 Run Binding에 연결한다. 실제 Migration·DB Write·공용 Runtime 배포는 대상별 승인 필요다. P0-14/18/19 보류와 DSIM 미구현 상태는 유지한다.
