# 생산 수학적 전략의 독립 평가 계약

기준일: 2026-09-03. InventoryEngine `0.6.0`, `io-production-evaluation-v1 / 1.0.0`.
상태: **개발 로컬 구현 완료**. 여기서 생산 전략은 실제 구현한 `historical-normal-r-s / 1.0.0` 코드를 뜻하며 운영 배포·활성화를 뜻하지 않는다.

## 1. 연구 Benchmark와 생산 전략을 구분한다

| 항목 | 연구용 `REFERENCE_R_S` | 새 `PRODUCTION_MATHEMATICAL` 평가 |
|---|---|---|
| 호출 | `prepare-training`의 기존 Benchmark | `evaluate-mathematical` |
| 정책 | 독립 Reference 수식, 매주 과거 이력 갱신, Allowlist Z, 원시 수식별 올림 | 기존 생산 정책 산출기, 평가 시작 전 이력 고정, 실제 Z·단계별 올림 |
| Source·승인 | 연구 Profile의 고정 제약 | 기간형 Source 정책, Override/Fallback, 발주일·승인 행동 범위 |
| 행동 검증 | Reference의 수량 제약 | 기존 생산 `validate_action` 그대로 호출 |
| 신규 주문 | Horizon 밖 예정일도 허용 | 기존 생산 규칙대로 Horizon 밖 신규 주문 거부 |
| 지연 적용 | 기존 기본값은 Warm-up부터 적용 | 평가 시작 이후가 원래 예정일인 입고에만 적용 |
| 결과 구분 | `production_strategy_executed=false`, `benchmark=REFERENCE_R_S` | `production_strategy_executed=true`, `evaluation_kind=PRODUCTION_MATHEMATICAL` |

재고·Backorder·비용의 전이는 양쪽 모두 독립 `ReferenceEpisode`가 계산한다. **Reference 수식을 생산 전략으로 이름만 바꾸거나, 생산 PSI를 정답 생성기로 재사용하지 않는다.** 기존 `prepare-training` 출력과 Golden은 유지한다. 위 조건이 다르므로 두 비용의 단순 차이를 알고리즘 우열이나 운영 절감액으로 해석하지 않는다.

## 2. 실제 연결 경로

```text
Canonical + 정책/이력 Snapshot → 기존 생산 입력 검증·수학적 정책 산출(1회)
    ↓
매주 실제 BOH·BO·예정 공급 → 공통 Observation → MathematicalStrategy.decide
    ↓
공통 validate_action → 승인된 수량·원래 납기·Calendar 도착 Bucket
    ↓
독립 World: 신규 주문 → 실현된 입고 → 실제 수요/BO → EOH·비용
    └→ 다음 주 실제 상태로 다시 판단
```

- 생산 PSI와 평가 Adapter가 같은 `build_observation`을 사용한다. 정책·승인·수량 규칙·입력 Binding을 동일하게 전달한다.
- 정책 통계는 `FROZEN_PRE_EPISODE`다. 관측 재고/BO는 매주 갱신하지만 정책을 미래 실제 수요로 재학습하지 않는다.
- 기간형 Source 정책·Override는 원래 생산 구현이 판단일 기준으로 선택한다. 기존 주문에 새 Lead Time을 소급 적용하지 않는다.
- `step_admitted`는 생산 검증을 통과한 주문을 World에 전달하는 **신뢰된 로컬 코드 경계**다. 수량·UOM·주문 ID·Calendar Mapping·관측 Hash를 다시 검사하되 Reference MOQ/Lot/Lead Time으로 재계산하지 않는다. 모든 품목을 검사한 뒤 상태를 변경한다.
- 주문 ID는 생산 `decision_id`에서 생성한다. 지연 시나리오는 원래 예정일과 실제 도착일을 분리한다. 선택 주문 지연은 실제 생산 주문 ID를 지정해야 하며, 발생하지 않은 주문 ID는 영향이 없다.
- 연체 공급은 원래 예정일을 그대로 관측에 남긴다. 도착한 것으로 삭제하거나 실제 도착일을 미리 알려주지 않는다. 공통 용량 검증기는 연체/미매핑 공급도 보수적으로 공간 예약에 포함한다.
- 실제 입고와 정책 한도 변경 때문에 용량 초과가 생기면 판단별 `physical_capacity_excess_qty`에 보존한다. Source 제약 때문에 거부된 권고와 외생적 재고 초과를 구분한다.

## 3. 입력과 실험 범위

`EvaluateMathematicalStrategyUseCase(deployment).execute(ProductionEvaluationRequest)`를 호출한다. [입력 Schema](../../schemas/production_evaluation.schema.json)는 기존 Training·Canonical·실행 1.1·정책 Schema를 참조한다. URN은 인접 파일로 로컬 등록하며 인터넷으로 Schema를 가져오지 않는다.

- Company/Subs/Site·Plan Type·Planning Cycle/Revision·Configuration, 선택 구간의 Calendar와 품목/UOM Universe를 대조한다.
- 독립 Warm-up의 BOH·BO·미도착 공급과 Canonical 입력이 정확히 일치해야 한다. 원래 Cut-off/EOH–BOH·봉인·고아 검증도 그대로 통과해야 한다.
- 시작 정책의 MOQ·Lot·용량·Lead Time·Service Level은 해당 합성 초기화 Profile과 일치해야 한다. 이후 승인된 기간형 정책 변경은 별도 입력으로 허용하고 Evidence에 남긴다.
- 정책 이력은 평가 시작 **이전** 동일 실제 수요와 일치해야 한다. 생산 입력에서 이력을 누락했다면 자동 보충하지 않고 기존 승인 Fallback 또는 계산 제외를 검증한다.
- V1 Forecast는 `SYNTHETIC_FROZEN_MEAN`: 평가 시작 전 13/26주 평균을 UOM 단위 `ROUND_HALF_UP`으로 반올림하고 전체 평가 기간에 고정한다. 품목별 Canonical Forecast 값도 이 규칙과 대조한다. 실제 DemandEngine Forecast Vintage를 검증한 것으로 표시하지 않는다.
- 고객 확정 주문 0, Reserved 0, 전체 실현 수요의 미충족을 BO로 이월하는 평가다. 실제 수요에 Forecast를 다시 더하지 않는다. **운영 PSI의 Forecast Shortage 비이월 규칙은 변경하지 않는다.**
- UOM 단위는 Canonical scale에 맞는 `1`, `0.1`, …, `0.000001`만 지원한다. `0.5` 같은 별도 재고 최소 단위는 이 Adapter에서 거부한다. 발주배수는 별도로 지원한다.
- 시작 시점의 모든 미도착 공급이 계획 안에 있어야 한다. 지나치게 짧은 구간으로 초기 공급이 Horizon 밖에 있으면 거부한다. 기간 중 지연으로 종료 뒤 남은 공급은 삭제하지 않고 보존한다.
- `EPISODE_RECEIPTS_ONLY`는 원래 입고 예정 Index가 구간 시작 이상인 주문에만 지연을 적용한다. Warm-up 중 이미 도착했어야 할 주문을 연체 상태로 시작시키지 않는다. 기존 생산의 연체 확정 입고 거부를 우회하거나 새 확약 납기를 가정하지 않기 위한 실험 경계다.
- 입력의 Scenario·Source 정책은 합성 설정이며 운영 승인 인증이 아니다. `DEVELOPMENT`만 허용하고 DB 접속·모델 로딩/학습을 하지 않는다.

## 4. 중간 산출물과 재현성

- 원본 `input_snapshot`, 전체 평가 입력 Hash, 생산 `recommendation_input_hash`, 기존 `prepared_input`.
- 기존 `mathematical_policy_report`: 통계, Source/Python/Effective 정책, 승인/Fallback·제외·반올림 근거.
- 매주 `decision_evidence`: 생산 관측과 Hash → 제안 → 검증 → World에 실제 전달한 주문 → 독립 Ledger Row, 실제 용량 초과 진단.
- `world_result`: 초기 52주 Ledger, 주문의 원래/실제 납기, 실제 재고 흐름, 비용·서비스·종료 미도착 공급. 비용·서비스 수식은 [독립 World 계약](IO_TRAINING_EVALUATION_CONTRACT.md)을 그대로 따른다.
- 전체 출력에 `content_hash`, `performance_superiority_claimed=false`를 기록한다. `run_claimed`, `database_writes`, `artifact_sealed`, `evidence_persisted`, `approval_authenticated`, `model_trained`는 false다.

전체 반환값은 감사용 미래 Ground Truth도 포함한다. 전략에는 `decision_evidence.observation`으로 구성한 값만 전달하며 입력/결과 전체를 전달하지 않는다. Python 객체의 내부 속성 접근을 막는 Sandbox는 아니다.

## 5. 실행과 남은 순서

InventoryEngine 루트에서:

```bash
PYTHONPATH=src python3 examples/production_evaluation.py
PYTHONPATH=src python3 examples/production_evaluation.py --scenario DELAY_1W
PYTHONPATH=src python3 examples/production_evaluation.py --request-only | IO_COMPANY_CD=DSE IO_ENVIRONMENT=DEVELOPMENT PYTHONPATH=src python3 -m dsio_inventory_engine evaluate-mathematical --request /dev/stdin
```

`--split TRAIN|VALIDATION|TEST`를 선택할 수 있다. 개발 예제는 130주·5품목·6시나리오다. JSON은 stdout으로만 반환하며 실제 Snapshot 봉인이나 ERP 발주가 아니다.

1. **현재 기준선 — 완료:** 생산 수학적 정책/공통 Guard와 독립 World 연결. [수작업 Golden](../../tests/fixtures/GOLDEN_PRODUCTION_EVALUATION.md)과 [검증 기록](IO_PRODUCTION_EVALUATION_VERIFICATION.md)을 기준으로 유지한다.
2. **현재 기준선 — 완료:** 0.7.0 [ML/PPO Adapter](IO_LEARNED_STRATEGIES_CONTRACT.md)의 실제 CPU 학습·JSON 추론과 동일 조건 비교를 완료했다. PPO 업데이트 전 13/26주 TRAIN 및 52/104주 TRAIN 반복 스트레스의 종료 민감도를 점검했다.
3. **다음 작업 — 학습 안정성:** 여러 Seed·비반복 장기 자료와 새 홀드아웃으로 후보를 검증한다. 현재 합성 결과를 운영 전략 순위나 수렴 증명으로 재사용하지 않는다.
4. **외부 작업 대기/승인 필요:** 실제 Forecast Vintage·공급/비용 Source, Run·Evidence 영속 저장, 실제 DB Write/Runtime 배포. P0-14/18/19 보류는 유지한다.
