# ML/PPO 독립 기대값

학습된 모델의 점수를 정답으로 고정하지 않는다. 다음은 분석적으로 정한 가중치·손실·정책의 기대값이다. 실제 CPU 최적화·동일 조건 비교는 별도 테스트와 [검증 기록](../../docs/architecture/IO_LEARNED_STRATEGIES_VERIFICATION.md)을 따른다.

| 대상 | 직접 계산한 기대값 |
|---|---|
| ML Softplus | Weight=0, Bias=log(exp(0.5)−1) → 0.5 |
| ML 정책 | 평균10, L=1, R=1, 예측0.5 → 안전재고5, ROP15, 목표25 |
| PPO 배율 | 수학적 목표24, ROP14 → 0.75배18 / 1배24 / 1.25배30 |
| PPO HOLD | 모델 Actor의 HOLD Logit만1 → 신규 주문0 |
| Override | 승인 목표40은 ML 예측·PPO HOLD보다 우선, 만료 후 모델 적용 |
| Pinball | q=0.9, 예측1: 정답2이면0.9, 정답0이면0.1 |
| Clip 목적함수 | ratio=[1.5,0.5,1.5,0.5], A=[1,1,−1,−1], epsilon=0.2 → [1.2,0.5,−1.5,−0.8] |
| 유한기간 GAE | reward=[1,2], V=[0.5,0.25], gamma=lambda=1, 최종V=0 → A=[2.5,1.75], Return=[3,2] |

`tests/support/learned_fixtures.py`는 이 가중치만 패키징한다. Torch로 기대값을 다시 학습하거나 생산 전략으로 정답을 생성하지 않는다. 추론은 UOM 수량에 대해 정확한 문자열을 비교하며 신경망 Float는 CPU/Torch와 순수 JSON 계산의 1e-10 오차 범위에서 비교한다.

학습 통합 테스트는 Weight 변경·유한 Loss·Seed 재현·TRAIN 밖 Actual 변경 불변성을 검사한다. 품질 합격 수치나 모델 우월성, P0-19의 운영 회귀 합격 기준을 대신하지 않는다.
