# ML/PPO 구현·학습·비교 검증 기록

기준일: 2026-09-03. 대상: `/Users/igwanhyeong/PycharmProjects/InventoryEngine`, 패키지 0.7.0. Python 3.12.10 / PyTorch 2.12.1 / CPU float64 / Seed 20260903.

판정: **개발 로컬 구현·계약·실제 소규모 학습 PASS**. 수렴·장기 운영 성능·모델 승인·Artifact 서비스 봉인·DB 게시 PASS가 아니다. [구현 계약](IO_LEARNED_STRATEGIES_CONTRACT.md), [독립 기대값](../../tests/fixtures/GOLDEN_LEARNED.md), [실제 실행 JSON과 모델](evidence/learned-strategies-smoke-20260903.json)을 함께 읽는다.

## 1. 검증 범위

| 검증 | 결과 |
|---|---|
| 단위 | 184건, 기존159+ML/PPO25, 실제 Torch 최적화 포함 |
| 계약 | 14건, 기존12+신규2, 로컬 Schema Registry/생산자 호환 포함 |
| 오프라인 프로세스 통합 | 17건, 기존14+신규3, 실제 학습 CLI·순수 JSON 추론·DB/Production 거부 |
| 합계 | 215건, Torch·jsonschema가 설치된 검증 Runtime에서 Skip 없음 |
| 수작업 기대값 | ML 정책·PPO 배율/HOLD·Override·Pinball·Clip·GAE 통과 |
| 누출·재현성 | VALIDATION/TEST Actual을 바꿔 재학습해도 두 모델 동일, CPU RNG/Thread 설정 복구 |
| 안전한 추론 | Torch·NumPy·pickle·asyncpg import를 막은 별도 프로세스에서 ML/PPO 추론 성공 |
| 모델 경계 | Hash·Version·Scope·학습 시점·정책 Profile·유한 수치·Shape·알 수 없는 필드 검증 |
| 기존 회귀 | Baseline·수학적 권고·학습 기반·생산 수학적 평가의 기본 CLI 전체 JSON이 0.6.0과 동일 |
| 정적 검증 | Ruff lint/format, 신규14개 Source 파일 Mypy, 신규 모듈 Bandit 통과 |
| Coverage | 신규 학습/추론/모델 계약의 분기 포함 약99%; 실제 학습 실행 포함. 논리적 전 경우 증명은 아님 |
| 패키징 | 0.7.0 Wheel 빌드; 소스/Wheel의 기본 전체 학습·비교 JSON 동일 |

전체 저장소 Mypy에는 기존 Network 타입 오류가 남아 있어 전체 타입 검사 PASS로 표기하지 않는다. 신규14개 파일을 검사하고 기존 네트워크 코드는 변경하지 않았다. 문서 검증 `oma` CLI는 미설치여서 설치하지 않고 변경 문서의 파일·명령·Schema 참조를 직접 확인했다.

임시 Stage에서 구현·검증한 뒤 변경39개 파일만 원래 InventoryEngine에 반영했다. 실제 프로젝트 경로에서 단위184·계약14·오프라인 통합17건을 재실행해 모두 통과했고 Skip은 없었다. 적용 파일의 Byte 일치와 기존108개 파일 보존, 저장된 PPO 모델의 추론 JSON 동일성, 실제 경로 Ruff lint/format도 확인했다. 기존 사용자 Notebook·다른 저장소를 변경하지 않았다. Git 미초기화 상태이며 Commit/Push·DB Write·공용 Runtime 배포는 수행하지 않았다.

## 2. 실제 학습 결과

- ML: TRAIN 안에서 완결된 보호기간 정답120건, Adam300회. Pinball Loss `0.1687593461 → 0.0111932669`.
- PPO: TRAIN26주×5품목×12 Episode=`1,560` Item-week, Optimizer96회. 6개 시나리오를 정렬 순환하며 최종 TRAIN 모델을 사용했다. 이 횟수는 수렴 판정이 아닌 학습 경로 점검이다.
- 학습 가중치 변경을 확인했으며 JSON 가중치와 Torch의 추론은 1e-10 범위/동일 선택 행동으로 일치한다.
- 학습과 비교를 포함한 실행 Hash: `6fe82036a6bbc1c62f64e828444f7faf9369881a07ce1317c9b4a23489da448f`.
- ML 모델 Hash: `7d1328aaa2daac9756497d2da7834ff076801c5f90e176679b1700ed46fdc483`.
- PPO 모델 Hash: `e8e7084ae3a2c5ac1b9ff535ad58ff39db5f6325ac3d2dd35062cff2366f191b`.

## 3. 동일 TEST 조건의 비용 비교

원래 합성 자료의 TEST13주×5품목×6시나리오다. 각 시나리오 안에서 초기재고/미도착 공급 Hash가 네 정책 모두 같고, 지연·Forecast·비용·기간 밖 주문 거부·종료 규칙도 같다. 총1,560 Item-week 비교이며 VALIDATION도 별도로 같은 규모로 실행했다.

| TEST 시나리오 | 무신규공급 | 수학적 | ML | PPO |
|---|---:|---:|---:|---:|
| BASE | 13,343 | 3,235 | 3,191 | 3,375 |
| HIGH_SHORTAGE_COST | 43,133 | 7,420 | 7,376 | 7,935 |
| DELAY_1W | 13,269 | 4,498 | 4,149 | 4,320 |
| DELAY_2W | 13,423 | 5,987 | 5,973 | 7,795 |
| DISRUPTION | 13,343 | 3,581 | 3,405 | 3,637 |
| SERVICE_99 | 12,951 | 3,395 | 3,350 | 13,103 |

단위는 합성 비용 Profile의 USD다. 실제 운영 원가가 아니다. 비용만 보고 후보를 채택하면 안 된다. BASE의 정시 충족률은 수학적66.18%, ML63.75%, PPO64.96%다. SERVICE_99에서는 수학적67.88%, ML59.85%, PPO32.12%로 **PPO가 무신규공급 대조군보다 비용도 높다**. 요청 서비스수준99% 달성 증거가 아니다.

TEST 결과를 사용한 Checkpoint/Hyperparameter 선택은 하지 않았다. 높은 서비스수준과 지연 상태에서는 정규화 범위를 벗어나 Feature가 잘린 사례도 있으며, 소비한 Feature만 해당 경고에 집계한다. 다음 안정성 검증에서는 TRAIN/VALIDATION의 범위·여러 Seed·학습량·행동 공간을 검토하고 개선 후보를 새 홀드아웃에서 평가해야 한다. 이 표는 운영 전략 순위가 아니다.

ML의 Softplus 출력은 양수이므로 무수요 품목도 UOM 올림/MOQ를 거쳐 소량 주문할 수 있다. ZERO 품목의 과잉재고, 간헐수요, 서비스 목표 변화에 대한 처리도 다음 후보 검증에 포함한다. 지금 모델을 운영 기본값으로 교체하지 않은 이유다.

## 4. PPO 업데이트 전 종료 효과와 긴 기간

원래 TRAIN의 13/26주×종료 비용2가지×6시나리오24건, TRAIN 반복 스트레스52/104주×종료 비용2가지×무지연/최대 고정지연8건, 총32건을 먼저 실행했다. 각 경우 수학적/무신규공급 정책을 공통 Guard/World로 비교했다.

BASE 예시:

| 기간 | 자료 | 수학적 비용: 종료비용 적용/0 | 무신규공급 비용: 적용/0 | 무신규공급 최종 BO |
|---|---|---:|---:|---:|
| 13주 | 원래 TRAIN | 1,783 / 1,783 | 9,792 / 6,982 | 281 |
| 26주 | 원래 TRAIN | 3,769 / 3,759 | 44,977 / 38,417 | 656 |
| 52주 | TRAIN 반복 스트레스 | 7,747 / 7,747 | 194,252 / 179,632 | 1,462 |
| 104주 | TRAIN 반복 스트레스 | 15,677 / 15,677 | 807,142 / 776,402 | 3,074 |

종료 비용을 빼면 특히 미납이 많은 정책의 총비용이 작아지고, 긴 기간에는 누적 미납 비용이 커진다. 짧은 기간의 총비용만으로 정책 우열을 판단하지 않도록 기간·Terminal BO/공급·비용 관례를 함께 기록했다. 52/104주는 새로운 독립 실측 평가가 아니며 PPO 학습에도 투입하지 않았다. 무한기간 운영에 사용할 Terminal Value·계속형 Bootstrap의 적합성은 아직 확정하지 않았다.

## 5. 다음 작업

1. **현재 기준선 — 학습 안정성 검증 완료:** 0.8.0 [새 검증 기록](IO_LEARNING_STABILITY_VERIFICATION.md)에 다중 Seed·비반복 자료·신규 홀드아웃 결과를 별도 보존했다. 이 문서의 0.7.0 결과는 과거 증적이며 모델 개선에 TEST를 재사용하지 않는다.
2. **외부 작업 대기 — 실제 Source:** DemandEngine Forecast 발행 이력, 당시 사용 가능했던 Actual·공급 확약/지연·비용을 같은 시점 계약에 연결한다.
3. **다음 작업 / 실제 적용 승인 필요 — 모델·Run·Evidence 연결:** 불변 모델 Artifact/승인을 공통 Run Binding에 연결하고 상세 중간 산출물을 저장한다. 실제 DB Migration·Write·공용 Runtime 배포는 별도 승인 대상이다.

P0-14·P0-18·P0-19 보류, 단일 Company/Site, DSIM 미구현 상태를 유지한다. 현재 `dsai-platform` 기준선은 `develop`이며 DemandEngine Runtime은 이번에 변경하지 않았다.
