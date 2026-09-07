# 다중 Seed 학습 안정성·후보 선정 계약

기준일: 2026-09-03. InventoryEngine 0.8.0의 개발 로컬 실험 계약이다. **검증 기능 완료와 학습 모델 성능 합격은 다르다.** DB·배포·운영 모델 승인·Run 등록은 하지 않는다. 기존 [0.7.0 ML/PPO 계약](IO_LEARNED_STRATEGIES_CONTRACT.md)의 알고리즘·Feature·PSI·공통 Guard는 변경하지 않았다.

## 1. 실행 순서와 정보 경계

1. **실험 정의 고정:** `StabilityRequest`에 Generator Version, 개발자료 Seed, 학습 Seed, 새 홀드아웃 Seed, 후보별 학습 횟수와 선택 규칙을 기록한다. 요청은 [Schema](../../schemas/learning_stability.schema.json)와 DTO로 검증한다.
2. **TRAIN/VALIDATION 실행:** TRAIN에서만 모델·정규화 통계를 학습한다. VALIDATION에서 후보별 모든 Seed의 비용·서비스 지표를 평가한다. 이 단계는 홀드아웃 수요를 생성하지 않는다.
3. **후보 선정 결과 고정:** 후보 점수, 선택·거부 이유, 모든 학습 Run/모델 Version·Hash, 구간 Hash를 `io-stability-selection-v1` JSON에 기록한다. 프로세스를 종료하고 파일의 `content_hash`를 별도로 고정한다.
4. **새 홀드아웃 평가:** 다른 실행에서 선택 파일과 `--selection-hash`를 전달한다. Hash, 후보 점수 재계산, Run·모델 연결 검증 이후에만 새 TEST 수요를 생성한다. 선택 후보의 Seed 3개를 전부 평가한다.
5. **결과 보존:** 새 결과는 `io-stability-holdout-v1`로 저장한다. 홀드아웃 결과로 후보나 학습 횟수를 바꾸지 않는다. 다음 개선 실험에는 별도 Revision과 새 홀드아웃이 필요하다.

이 Hash 고정은 로컬 재현성 계약이다. 외부 승인자의 서명·신뢰 저장소·일회성 Holdout 접근 통제·Artifact 서비스 봉인을 대신하지 않는다. 같은 고정 파일로 재현 검증은 가능하다. CLI는 기존 파일을 덮어쓰지 않으며, 실제 Source/Run 서비스와 연결하는 단계에서 접근 이력과 승인 경계를 추가해야 한다.

## 2. 비반복 자료와 여러 Seed

| 구분 | 기본값 | 용도 |
|---|---|---|
| 합성 이력 | 78주 | 26주 정책 이력 + 최소 52주 독립 재고 Warm-up |
| TRAIN | 52주, 2027-07-05~2028-07-02 | 모델·정규화 통계 학습 |
| VALIDATION | 52주, 2028-07-03~2029-07-01 | 후보 선정만 수행 |
| 새 TEST | 104주, 2029-07-02~2031-06-29 | 선정 이후 평가만 수행 |
| 개발자료 Seed | 17011 | 동일 TRAIN/VALIDATION을 모든 후보에 제공 |
| 학습 Seed | 101, 202, 303 | 초기 가중치·PPO 행동 샘플링 차이 |
| TEST 자료 Seed | 81001, 81002, 81003 | 서로 다른 새 수요 실현값; 학습 Seed와 교차 평가 |
| 후보 1 | `BUDGET_06`: ML150 Epoch, PPO6 Episode×4 Epoch | 짧은 학습 예산 |
| 후보 2 | `BUDGET_18`: ML300 Epoch, PPO18 Episode×4 Epoch | 긴 학습 예산 |

`nonrepeating-hash-demand-v1`은 Seed·Item·주차·Noise/Regime/Arrival/Size/Shock 채널의 SHA-256으로 결정론적 수요를 만든다. 정상·간헐·감소·급증·무수요 품목을 포함하며 계절 평균과 17주 단위 수준 변화를 가진다. 과거 26/52주 배열을 복사하지 않는다. `ZERO`는 의도적으로 항상 0이며 비반복 검사에서 제외한다. 미래 연도는 합성 Calendar 표기이지 실제 관측 자료가 아니다.

TRAIN/VALIDATION 단계에서 DTO가 요구하는 TEST 행은 **미관측 0 Placeholder**다. 실제 0수요 관측값으로 학습·선정하지 않으며, TEST 구간 Hash도 선택 증적에 넣지 않는다. 홀드아웃 개방 시 등록된 별도 Seed로만 치환한다. 세 TEST는 TRAIN/VALIDATION 및 시작 상태를 공유한다. 독립 학습자료 Seed까지 바꾼 다중 데이터셋 학습 검증은 아직 아니다.

0.7.0의 130주 자료와 TEST는 과거 증적으로 보존한다. 기존 학습기의 104주 TRAIN 반복 Preflight도 그대로 수행하지만 `SYNTHETIC_TRAIN_REPLAY_STRESS`로 표시하며, 이번 비반복 홀드아웃과 섞지 않는다.

## 3. 비용·서비스 동시 선택 기준

`cost-service-guardrails-v1`은 **로컬 연구 기준**이다. 사용자가 보류한 P0-19 운영/Legacy 합격 수치를 확정하는 것이 아니다.

| 항목 | 기준 |
|---|---|
| 평균 비용 비율 | 동일 조건 수학적 전략 대비 ≤1.00 |
| 최악 Seed×시나리오 비용 비율 | ≤1.10 |
| 전체 수량 가중 정시 충족률 차이 | 모든 Seed×시나리오에서 ≥−2%p |
| 품목별 정시 충족률 차이 | 수요가 있는 모든 품목에서 ≥−5%p |
| 물리 용량 초과 | 0 |

비용 비율은 각 Seed×시나리오의 `learned_cost / mathematical_cost`를 동일 가중 평균한다. 서로 다른 비용 시나리오 금액을 합산한 비율이 아니다. 서비스는 `on_time_fulfilled_qty / demand_qty`이며 주기 서비스수준(Cycle Service Level)이나 Source의 SL95/SL99 목표 달성을 의미하지 않는다. 무수요 품목의 Fill Rate는 NULL로 유지한다.

후보별 학습 Seed 3개×시나리오 4개가 모두 있어야 선정한다. 기준 통과 여부 → 위반 Cell 수 → 평균 비용 비율 → 평균 서비스 차이 → 후보 ID 순으로 정렬한다. 가족별 후보를 하나 지명하되 **가장 좋은 Seed를 뽑지 않고 3개 모델 모두 유지**한다. 통과 후보가 없으면 연구 비교 후보만 지명하고 `effective_strategy=MATHEMATICAL`로 보고한다. 이것은 운영 Runtime의 자동 Fallback 구현이 아니다.

평균·표본 표준편차·최소·최대와 Seed별 시나리오 평균을 기록한다. 세 Seed와 상관된 시나리오를 IID 반복 표본처럼 취급하지 않고 신뢰구간·통계적 우월성을 주장하지 않는다. 소수 Run의 점 추정치만으로 결론을 내리지 않는다는 방향은 [Agarwal 등의 RL 평가 연구](https://arxiv.org/abs/2108.13264)를 참고했다. 이번 구현은 해당 논문의 Bootstrap/IQM 도구 전체를 구현한 것은 아니다.

## 4. 같은 조건의 평가와 종료 민감도

- 모든 전략은 `EPISODE_RECEIPTS_ONLY`, `SYNTHETIC_FROZEN_MEAN` 및 동일 독립 World를 사용한다. 각 비교에서 초기 상태 Hash·통화·기간·비용 규칙이 일치해야 한다.
- `BASE`, `HIGH_SHORTAGE_COST`, `SERVICE_99`, `DELAY_2W`를 비교한다. 무신규공급 대조군, 수학적 전략과 선정된 ML/PPO의 모든 Seed를 보존한다.
- 기본은 104주와 Source 종료 BO 비용이다. 첫 TEST Seed의 BASE/DELAY_2W에서 52주 Source 비용, 104주 종료 BO 비용 0도 비교한다. 전체 비용은 기간 길이에 따라 변하므로 단순 금액 크기로 기간 우열을 판정하지 않는다.
- 모든 권고는 기존 공통 Guard를 거쳐 MOQ·Lot·납기·정책 우선순위에 따라 적용한다. 이번 합성 Profile에는 물리 용량 상한이 없으므로 용량 초과 0을 실제 창고 용량 적합성 증거로 해석하지 않는다. 용량 동작 자체는 기존 Golden에서 별도 검증한다.
- World는 실현 수요 미충족을 BO로 이월한다. 운영 Canonical PSI의 Forecast Shortage와 고객 확정 주문 BO 구분 계약을 바꾸지 않는다.
- 종료 BO 비용은 마지막에 부과하고 재고 Salvage는 없다. 정상화한 주당 비용·Fill Rate·종료 BO/재고/Pipeline을 함께 검토한다.

## 5. Evidence 범위와 후속 Source 연결

현재 로컬 파일은 전체 후보 모델의 안전한 JSON 가중치, Model Version/Hash, 학습 Run ID, 입력/학습 Profile Hash, Optimizer Log, TRAIN Preflight, VALIDATION·TEST 지표 및 World/Decision Hash를 보존한다. 기본 증적은 **요약형**이고 매주 원장/모든 관측 원문을 저장하지 않는다. 같은 Recipe와 모델로 상세 평가 API를 다시 실행할 수 있다.

실제 `engine_run_id` DB Row, Demand Run·Forecast 발행 시점, 승인 Model Registry, Artifact Receipt, `TB_IO_*` 행과의 연결은 미구현이다. 합성 Run ID나 `database_writes=false`를 실제 이중 기록 완료로 해석하지 않는다. 후속 작업에서 Source Revision·Snapshot Hash, 공통 Run Input Binding, 모델 승인/봉인과 Evidence Receipt를 연결한다. DB 변경·배포·실제 E2E Write는 별도 승인 대상이다.

## 6. 실행 명령

프로젝트 루트와 `learning` 선택 의존성이 설치된 Python을 사용한다. 출력 경로는 새 파일이어야 한다.

```bash
PYTHONPATH=src python3 examples/learning_stability.py request
PYTHONPATH=src python3 examples/learning_stability.py select --output /tmp/io-selection-new.json
```

위 실행이 반환한 `content_hash`를 별도 기록한 다음 `holdout --selection <선택 파일> --selection-hash <기록한 Hash> --output <새 결과 파일>`로 진행한다. 기존 증적의 검증 명령은 다음과 같다. 검증은 학습·홀드아웃 실행이 아니다.

```bash
PYTHONPATH=src python3 examples/learning_stability.py verify-selection --selection docs/architecture/evidence/learning-stability-selection-20260903.json --selection-hash db4d8f24a85e65876b7eba60811208900bbebf3b714f883c7a787b1cb3bbaa76
```

구현: [요청 계약](../../src/dsio_inventory_engine/inventory_contracts/stability.py), [실행기](../../src/dsio_inventory_engine/stability/application.py), [자료 생성](../../src/dsio_inventory_engine/stability/data.py), [선정 규칙](../../src/dsio_inventory_engine/stability/selection.py), [검증 결과](IO_LEARNING_STABILITY_VERIFICATION.md).
