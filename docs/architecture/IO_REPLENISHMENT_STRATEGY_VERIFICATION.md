# 세 전략 공통 계약·주간 PSI 구현 검증

기준일: 2026-09-03. 대상: `/Users/igwanhyeong/PycharmProjects/InventoryEngine`, 패키지 `0.3.0`, Python `3.12.10`.

판정: **요청한 공통 계약·주차별 PSI·행동 검증기의 로컬 구현 PASS**. 수학적 정책 산출기, 학습된 ML/PPO, 운영 승인, DB/Artifact 저장이나 실제 E2E의 완료 판정이 아니다.

이 문서는 0.3.0 검증 당시 상태다. 실제 수학적 정책 산출기가 추가된 최신 기록은 [0.4.0 검증 기록](IO_MATHEMATICAL_POLICY_VERIFICATION.md)을 참조한다.

## 1. 검증 결과

| 검증 | 관측 결과 |
|---|---|
| Unit | 86건 통과: 기존 58건 + 전략/공통 PSI 28건 |
| Contract | 5건 통과, Skip 없음. 기존 Producer/Canonical 3건 + 새 Schema/DTO 2건 |
| Offline Integration | 4건 통과. 기존 CLI 3건 + 세 전략 Probe의 실제 프로세스 실행 1건 |
| Baseline 회귀 | 기존 독립 Golden 14개/45개 PSI Row 유지. HOLD 전략도 같은 수량 결과 |
| Coverage | Unit+Contract 기준 전체 97%(Statement/Branch 합산). 새 계약·행동 검증·권고 실행·공통 전이 모듈은 측정된 Line/Branch 100% |
| Ruff | `0.15.22`, Source/Test/Probe Lint와 Format 통과, 41개 파일 |
| Mypy | `2.3.1`, 변경 핵심 7개 Source 파일 통과, `--follow-imports=silent` |
| Bandit | `1.9.4`, 새 계약·권고 모듈·PSI Application 검사 Finding 없음 |
| Wheel | `0.3.0` 빌드 성공. 소스 대신 Wheel에서 Import한 세 Probe의 전체 응답이 소스 실행과 동일 |

기존 Network 읽기 전용 DB 검증 5건은 이전 기준선이며 이번 95건에 합산하지 않는다. 이번에는 DB에 접속하지 않았다. Coverage는 모든 입력 조합·업무 정확성·성능을 보장하지 않는다. 특히 공통 `require()` 조건은 별도 거부 테스트로 검증하며 Line/Branch 수치만으로 오류 경로의 완전성을 주장하지 않는다.

전체 기존 Network SQL의 Bandit 판정은 [0.2.0 기록](IO_CANONICAL_PSI_VERIFICATION.md)의 설명을 유지한다. 이번 새 모듈 검사 통과를 전체 Source의 Finding 부재로 확대하지 않는다.

## 2. 수작업 기대값과 오류 주입

| 사례 | 기대값 / 결과 |
|---|---|
| BOH 10, 목표 100, 수요 0, Lead Time 14일 | 주문 90 한 번. EOH 10→10→100. 다음 주 Position 100으로 중복 보충 없음 |
| Lead Time 0/1/7/8일 | 각각 당주/다음 주/다음 주/다다음 주 경계에 도착. 원래 Due Date 보존 |
| Horizon 밖 또는 마지막 주 중간 도착 | `ARRIVAL_OUTSIDE_PLAN_HORIZON`, 대기열 추가 없음 |
| 필요 137, MOQ 100, 배수 50, 수용 가능 120 | 배수 후보 150→유효 수량 100, 미충족 37. 잘못된 120 주문 없음 |
| 용량이 MOQ 미만 | `NO_FEASIBLE_ORDER_QUANTITY`, 기존 재고 수량은 보존 |
| Reserved/미도착 확정 공급/미래 용량 감소 | 모두 용량 예약에 반영. 미래 Forecast 출고로 용량을 미리 확보하지 않음 |
| Source 재고/확정 공급이 이미 용량 초과 | 초과량 진단, 원본 재고를 임의로 절단하지 않음 |
| 발주일·목표 범위·허용 행동 위반 | 주문 거부 및 이유 보존 |
| HOLD/순 필요량 0 | MOQ를 적용해 불필요한 주문을 만들지 않음 |
| 실제 BO/Forecast Shortage | 실제 주문 미충족만 이월. 기존 Cut-off·수량 보존식 유지 |
| 기존 권고 도착 | 해당 Bucket에 한 번 가산 후 대기열에서 제거 |
| 다른 Company/입력 Hash/Configuration/모델 Binding | 실행 거부 |
| 미봉인 Snapshot/잘못된 Control | 전략 Callback 전에 거부 |
| 과거 Decision/관측 Hash, 음수/NaN/정밀도 위반 | 제안 거부. 직접 DTO 생성으로 우회 불가 |
| Adapter 예외 | `STRATEGY_EXECUTION_FAILED`, 예외 원문/Secret 미노출, 임의 Fallback 없음 |
| 재시도/연속 호출 | 입력 Hash와 주문/PSI Hash 동일, 호출 간 대기열 상태 누출 없음 |
| 관측 Evidence 과도한 확장 | 크기 계산 Gate에서 중단 |

숫자 기대값은 [단위 테스트](../../tests/unit/test_replenishment.py)에 독립 리터럴로 작성했다. 테스트 Probe는 고정 행동을 제출할 뿐 정책 공식이나 학습 모델을 가장하지 않는다.

## 3. 실제 프로세스와 패키지 검증

[예제 Probe](../../examples/strategy_contract_probe.py)는 기존 `baseline_input.json`에 고정 목표재고 200을 제출한다. 세 전략 종류로 각각 실행했을 때 모두 다음 수량을 관측했다.

- EOH: `30 → 70 → 180`
- 신규 권고 입고: `0 → 60 → 110`
- 권고 주문 2건, 결정 Evidence 3건
- `probe_only_not_a_trained_strategy=true`
- `database_writes=evidence_persisted=approval_authenticated=model_artifact_verified=false`

전략 종류가 PSI Row에 기록되므로 수량이 같아도 가족별 Content Hash는 다르다. 각 가족의 소스 실행과 Wheel 실행 사이에서는 전체 응답이 일치했다. 이는 패키징과 공통 호출의 검증이며 실제 세 알고리즘의 성능 동등성 주장이 아니다.

## 4. 재검증과 환경

```bash
PYTHONPATH=src python3 -m unittest discover -s tests/unit -v
DSAI_PLATFORM_ROOT=/Users/igwanhyeong/PycharmProjects/dsai-platform PYTHONPATH=src python3 -m unittest discover -s tests/contract -v
PYTHONPATH=src python3 -m unittest discover -s tests/integration -p '*offline.py' -v
PYTHONPATH=src python3 examples/strategy_contract_probe.py --strategy DEEP_RL
```

계약 테스트는 `jsonschema`와 Producer 저장소가 필요하다. 이번 실행에서는 둘 다 제공해 Skip 없이 검증했다. Ruff·Mypy·Coverage·Bandit·Wheel 빌드는 임시 도구 환경에서 실행했고 사용자 Runtime 의존성을 변경하지 않았다. 기본 검증 Python에 pip/build가 없어 Wheel은 격리된 `uv build`로 검증했다.

`oma-backend`의 계약/UseCase 분리와 `oma-qa`의 독립 기대값·실패 주입·프로세스 검증을 적용했다. `oma-docs`의 구현과 문서 대조도 수행했다. `oma` CLI는 설치돼 있지 않아 변경 문서의 파일 링크·명령·상태를 직접 검증했으며 보호된 `.agents/`는 수정하지 않았다.

## 5. 다음 작업과 승인 경계

1. **다음 작업 — InventoryEngine 수학적 정책 산출기:** Lookback·Service Level·보충주기 입력과 안전재고/ROP/목표재고를 구현하고 독립 Golden으로 검증한다. 완료된 공통 PSI/행동 검증은 재사용한다.
2. **다음 작업 — InventoryEngine 학습/평가 환경:** 독립 합성 BOH·수요 이력·비용/평가 계약을 준비한다. 계약이 고정된 후 ML/DRL은 독립 모듈로 병렬 구현할 수 있다.
3. **다음 작업 / 승인 필요 — 실제 Source·공통 Run·Evidence 저장:** 구현과 오프라인 계약 검증은 진행할 수 있으나 Migration/실제 DB Write·공용 Runtime 배포는 대상별 승인 후 수행한다.

InventoryEngine은 아직 Git 저장소가 아니므로 Commit/Push/MR을 수행하지 않았다. dsai-platform의 기존 작업과 사용자 Notebook도 변경하지 않았다. P0-14/18/19 보류와 Multi-Echelon/DSIM 후속 범위는 유지한다.
