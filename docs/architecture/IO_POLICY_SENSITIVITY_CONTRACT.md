# 품목별 정책의 장기 평가·비용 민감도 계약

기준일: 2026-10-06. InventoryEngine `inventory_engine_dev`의 로컬 개발 검증이다.
정책 탐색·선택 `1.0.0`은 유지하고 별도 Sensitivity Request/Result `1.0.0`을 추가한다.
운영 최적 정책 승인, PPO 승격, Platform Run 게시를 의미하지 않는다.

## 1. 비교하는 세 가지 질문

1. **기간을 늘리면 같은 후보가 계속 선정되는가?**
   각 기간에 대해 실제 `RunPolicySearchUseCase`로 공통 Guard와 PSI를 다시 실행한다.
   기간별 Source Hash는 다르며 짧은 결과를 복사하거나 같은 Hash로 위장하지 않는다.
2. **비용 가정을 바꾸면 선정이 바뀌는가?**
   같은 PSI·행동·주문에 보관비, 고객 Backorder 비용, 품목별 고정·변동 발주비를 다시 적용한다.
   비용 변화는 후보 행동을 재학습·변경시키지 않는다. 원본 PSR과 Config를 수정하지 않는다.
3. **짧은 자료에서 선택한 정책이 새로운 장기 자료에서도 서비스 조건을 만족하는가?**
   명시된 Validation Case와 비용 Profile에서 한 번 선택한 Candidate ID를 Holdout에 고정한다.
   Holdout에는 순위·새 선정 결과를 만들지 않는다. Source 검증도 선택 없이 Guard·PSI·지표만 재검증한다.

평가기는 봉인된 PSR을 소비하는 순수 계산이다. 실제 기간별 Simulation 생성은 기존 탐색기 책임이다.
CLI가 Source를 임의로 늘리거나 미래수요를 자동 조회하지 않는다.

## 2. 입력과 비교 가능 조건

- Request에는 `evaluation_id`, 개발 합성 입력 표시, `reference_case_id`,
  `reference_cost_profile_id`, Case 목록, 비용 Profile, 종료 비교 Pair가 들어간다.
- Case는 `case_id`, `trace_id`, `split=VALIDATION|HOLDOUT`, Seed와 PSR 원본을 가진다.
- Case 24개, 비용 Profile 12개, Pair 24개, 전체 JSON 128MB 이내다.
  후보×Case×비용 Profile 평가량도 제한한다.
- Scope·W0·Cut-off·Master·Config·분류 Hash·품목 Universe·기초재고·예약·기존 BO·후보 집합이 같아야 한다.
  비교용 정책·제약·계산값은 고정한다. 이 버전은 기간 중 정책 전환을 허용하지 않는다.
- 같은 Trace의 기간별 Calendar·고객주문·순 Forecast·확정 입고는 동일 Prefix여야 한다.
  동일 Trace에서 Split이나 Seed를 바꿔 Holdout으로 위장할 수 없다.
- Holdout은 Validation과 다른 Seed·수요/공급 Path를 요구한다.
  Seed 표시는 암호학적 독립성 보증이 아니며 실제 업무 Source 권위를 증명하지 않는다.
- 서로 다른 통화 비용을 직접 비교하지 않는다. 통화 환산 기능은 이 범위에 없다.
- 비용이 없으면 `COST_PROFILE_NOT_BOUND`와 `total_cost=null`이며 최저비용 후보를 선정하지 않는다.
  무수요 기간은 `NO_DEMAND_IN_HORIZON`, 수요 단위당 비용은 `null`이다.

Hash는 정규화한 전체 실험 사양과 PSR Reference/Hash를 포함한다. 사용자에게 Hash를 작성하게 하지 않는다.

## 3. 서비스·비용 평가 의미

선정 규칙은 기존 [품목별 선택 계약](IO_POLICY_SELECTION_CONTRACT.md)을 재사용한다.
서비스 조건 → 총비용 → 주말 평균재고 → 발주횟수 → Candidate ID 순서다.
`CYCLE_SERVICE_LEVEL` 정책 입력과 실현 수요 수량의 Fill Rate는 다른 지표다.

서로 길이가 다른 기간의 누적 비용을 직접 비교하지 않도록 `cost_per_week`와
`cost_per_current_demand_unit`을 함께 보존한다. 정책 선정은 같은 기간의 후보끼리만 비교한다.
비용 가정만 달라진 경우 수요 충족률·주문·재고 흐름은 변하지 않아야 한다.

현재 비용 범위는 주차말 On-hand 보관비, 고객 BO 누적 비용, 품목별 고정 발주비, 발주량 변동비다.
Forecast Shortage는 고객 Backorder로 자동 이월되지 않는다. 따라서 고객주문이 없는 사례에서는
Backorder 단가만 높여도 결과가 변하지 않는다. Lost Sales 비용, 공동 Shipment 비용 배부,
Landed Cost, 할인율·잔존가치는 이번에 추가하지 않았다. 발주비는 기존 계약대로 주문 시점에 계상한다.

## 4. 종료 효과 진단

기존 PSI의 `FINITE_HORIZON_NO_TERMINAL_VALUE_ARRIVALS_OUTSIDE_HORIZON_REJECTED`를 변경하지 않는다.
짧은 Case와 동일 Trace의 연장 Case에서 다음을 비교한다.

- 짧은 기간과 같은 첫 N주의 원 행동·수량, Guard 적용 수량, 납기, 조정/거부 사유
- 같은 N주의 서비스·비용·EOH·BO
- N주 안에 주문했으나 N주 이후 입고되는 권고 미도착 수량
- 연장 Case의 전체 결과(후속 주문을 계속 수행한 결과)

연장 Case가 주문을 계속 수행하므로 이는 주문을 전부 중지한 Drain-down 실험이 아니다.
잔존가치나 미래 비용을 임의로 붙이지 않으며, 종료 편향을 완전히 제거했다고 주장하지 않는다.
장기 순위 변경과 마지막 주의 Horizon-dependent Guard 차이도 서로 다른 진단으로 보존한다.

## 5. 별도 Evidence와 복구

- 실험 결과는 `PSENS-<content_hash>`로 별도 저장한다. 원본 PSR과 기존 PSEL은 덮어쓰지 않는다.
- 저장·조회 전에 모든 PSR 부모가 존재해야 하며 Source Guard/PSI/지표와 실험 전체를 재계산한다.
- 동일 Reference·동일 내용은 멱등 처리한다. 결과 수치를 수정하고 Hash만 다시 계산해도 Replay에서 차단한다.
- Request/Result JSON Schema는 구조 검증이다. 수치 의미·부모 참조·Holdout 규칙은 Python 계약과
  Artifact Replay에서 추가 검증한다. 자체 Hash는 ERP 원천 또는 업무 승인 진위를 증명하지 않는다.
  비교기는 PSR에 보존된 초기 상태·평균수요·계산 정책·제약의 일관성을 검사한다.
  원본 수요 이력 전체가 동일한지까지 PSR만으로 증명하지 않는다. 개발 재현 도구는 동일 사전 이력을 사용한다.
- `automatic_publish_allowed`, `automatic_order_allowed`, `operational_policy_approved`,
  `database_writes`는 모두 `false`다. REVIEW/BLOCK를 운영 승인으로 바꾸지 않는다.

CLI: `inventory-policy-sensitivity --request <sealed-experiment.json> --artifact-root <existing-directory>`.
로컬 Append-only JSON Writer이며 DB·Runtime 배포 권한을 갖지 않는다.

## 6. 개발 재현과 남은 범위

`scripts/verify_policy_sensitivity.py`는 저장소 내부 Test Fixture를 사용하는 개발 검증 도구다.
설치된 제품의 ERP 수집기나 운영 데이터 생성기가 아니다.

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src:tests:tests/support \
  python scripts/verify_policy_sensitivity.py --artifact-root <existing-directory>
```

1개 품목, 13·26·52·104주와 108주 연장, Validation Seed 7 및 Holdout Seed 17/29,
6개 비용 Profile을 평가한다. 역사 평균수요 10 EA, BOH 30 EA, Lead Time 7일,
MOQ 0/배수 1, 4개 후보를 고정한다. 미래 경로는 단순 반복 배열이 아닌 Seed별 생성 자료다.
기초재고 30은 명시적 개발 Fixture이며 새로 52주 Warm-up한 운영재고라고 표현하지 않는다.

실제 SKU 대표성, 시계열 순차 Backtest, 재학습/정책 갱신, 통계적 우월성, PPO 비교·운영 승격은
별도 범위다. 이번 결과는 새 Seed의 계획 입력 경로에 대한 고정 정책 검증이다.
실측 재고·수요로 검증했다고 해석하지 않는다.

세부 실행 결과와 한계는 [검증 기록](IO_POLICY_SENSITIVITY_VERIFICATION.md)을 따른다.
