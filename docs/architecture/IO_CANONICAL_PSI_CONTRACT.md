# Canonical 입력·Cut-off·단일 Site Baseline PSI v1

> 0.3.0 확장: Canonical v1과 Baseline 수식·Golden은 유지한다. 공통 주차 전이와 세 전략/Recommended PSI는 [보충 전략 계약](IO_REPLENISHMENT_STRATEGY_CONTRACT.md)을 따른다. 실제 정책 산출기·ML/PPO 학습은 후속 작업이다.

기준일: 2026-09-03. 상태: **로컬 코드·Golden·CLI 검증 완료**. 실제 ERP Adapter, Source 수집/봉인 저장, 공통 Run/Planning Cycle 연결, Artifact·TB_IO 영속화와 Production 활성화는 미구현이다.

## 1. 구현한 경계

`CanonicalInputRequest → PrepareInventoryInputUseCase → RunPsiSimulationUseCase` 순서다. PSI 공개 진입점은 항상 입력 준비를 먼저 호출한다. 데이터가 잘못되면 수량 계산 전에 `InventoryInputError`와 원인 Code/대사 Evidence를 반환한다.

- 입력: [Canonical JSON Schema](../../schemas/canonical_input.schema.json), [실행 예제](../../examples/baseline_input.json).
- 구현: `inventory_contracts/canonical.py`, `prepare_inventory/application/{validation,cutoff,inventory_input}.py`, `simulate_inventory/application/baseline.py`.
- 실행: `simulate-baseline --request ...`. 단일 Company/한 Site, POSM/TGSM 공통 Canonical 계산이다. 기존 `prepare-network`는 그대로 유지한다.
- 각 Legacy Source를 실제 조회하는 SQL Adapter는 구현하지 않았다. `PLAN_ID/PLAN_STRT_DT/BASE_DT` 및 정책 Column의 미확인 의미를 추정하지 않는다.
- 승인 Network 입력과 PSI는 각각 독립 Slice다. 전체 Runner가 두 입력을 동일 Cycle/Scope로 합성하는 작업은 후속 범위다.

## 2. Snapshot 구조

모든 Snapshot은 `snapshot_id`, `content_hash`, `status`, `row_count`, `metadata`, `rows`를 가진다. 실행 요청의 `input_bindings`에도 각 ID/Hash를 명시한다. 누락 ID를 최신값 조회로 보완하지 않는다.

| Snapshot | Row Grain / 필수 의미 |
|---|---|
| `calendar` | 주차당 1행. `yyyyww`, `seq`, 실제 시작/종료 Date, 기준 월 |
| `master` | 선택 Site의 Item당 1행. UOM·Active·Stock Managed 여부 |
| `forecast` | 유효 Item × Calendar Bucket당 1행. 0수요도 명시하고 누락은 오류 |
| `customer_orders` | Item × Bucket의 집계 확정 주문. `coverage=COMPLETE`일 때 없는 행만 0으로 해석 |
| `inventory` | Item당 W0 Position 1행. 물리 On-hand·Reserved·Available·실제 주문 Backorder |
| `prior_inventory` | Item당 W0 전일 마감 물리 EOH 1행. 별도 봉인 Snapshot과 ID/Hash 검증 |
| `receipts` | 유일한 `receipt_id`당 1행. Item·UOM·예정일·수량·상태·Supply Type |
| `policies` | Item·Policy·유효기간. MOQ·배수·Lead Time·승인 Service Level·물리 한도·Source 목표/ROP |

`prior_inventory`는 전기 Inventory Snapshot의 참조 역할이며 새 DB Table 추가를 결정한 것이 아니다. 정책은 유효기간이 중복되지 않고 각 Bucket 시작일에 적용 가능한 1행이 있어야 한다. 실제 정책 계산/월 경계 적용과 Source Mapping은 권고 단계에서 이 명시적 기간 계약에 맞춰 구현한다.

입력의 Company/Subs/Site는 Snapshot Metadata와 모든 Item Fact Row에서 검증한다. 유효 Master Item만 Buffer Universe에 포함하며, 고아·중복·잘못된 UOM·Policy/Position/Forecast 누락은 전체 입력을 거부한다. 주문/입고 Source가 `UNAVAILABLE`이면 0으로 대체하지 않는다.

## 3. 봉인·계보·Hash

- 8개 Snapshot 모두 `SEALED`여야 한다. Forecast에는 `demand_run_id`, `SUCCEEDED`, `VERIFIED`와 동일 Calendar/Master ID·Hash/Revision이 필요하다.
- Content Hash는 `snapshot_type + snapshot_id + metadata + rows`의 UTF-8 정렬 JSON SHA-256이다. Row는 Canonical JSON 순으로 정렬하고 숫자는 지수 없는 Decimal 문자열, Timestamp는 UTC로 정규화한다.
- Inventory의 Adjustment/Source Event 배열도 정렬한다. 모든 원본 Canonical Row와 제외 Row를 출력에 보존한다.
- `row_count`와 상태는 별도로 검증하고 실제 내용 Hash·요청 Binding과 대조한다. 상태 문자열만 `SEALED`로 바꾸는 것으로 통과하지 않는다.
- 전체 `input_content_hash`에는 Snapshot, Binding, Configuration/Quantity Rule, Plan·Site Context가 포함된다. `engine_run_id`만 제외하여 동일 입력 Retry의 새 Run ID가 계산 입력 변경으로 오인되지 않는다.
- Hash는 내용 무결성 검증이지 Source의 서명·업무 승인을 대신하지 않는다. 실제 실행에서는 신뢰된 Source/Artifact Reader와 공통 Cycle Binding이 요청 ID/Hash·승인 근거를 제공해야 한다. 현재 CLI는 로컬 신뢰 경계이며 인증/Run FK를 구현하지 않는다.

## 4. Cut-off 방어

실제 재고의 대사식은 `reported_on_hand_boh = prior_sealed_eoh + approved_adjustment`다. 전기 Snapshot은 W0 전일이어야 하고 Inventory에 기재된 이전 ID/Hash가 실제 전달된 Snapshot과 일치해야 한다.

- Plan에 고정된 Timezone, Cut-off와 Source Watermark Token이 Snapshot과 일치해야 한다.
- `cutoff <= complete_through <= extracted_at <= sealed_at`이고 Cut-off는 Site의 W0 시작 이후일 수 없다. `complete_through`는 Source가 해당 시점까지의 추출 완결성을 보장하는 경계다.
- Adjustment는 원인·승인자·승인시각·원천 문서·Event Hash가 있어야 한다. 승인시각은 봉인 이후일 수 없고 중복 Adjustment ID를 거부한다.
- 전기 EOH와 보고 BOH 차이를 `variance_qty`로 기록한다. EA는 정수/허용오차 0, 소수 UOM은 명시적으로 승인된 Scale·Tolerance를 사용한다. 허용오차 안의 차이도 숨기거나 BOH를 고쳐 쓰지 않는다.
- 실제 발생시각이 Cut-off 이전인데 ERP 확정시각이 이후인 Event는 `LATE_POSTING_DETECTED`로 거부한다. Event 시각 순서·Hash와 수집 경계도 검증한다. 동일 Run 재시도가 아니라 새 Snapshot/Cycle Revision이 필요하다는 Evidence를 남긴다.
- 발견된 Event를 자동으로 BOH에 다시 가산/차감하지 않는다. 이미 Snapshot에 반영된 Movement를 두 번 계산하는 것을 막는다.
- 현재 검증은 **제공된 Source Event와 완결성 Metadata**를 대상으로 한다. 아직 수집되지 않은 ERP 거래를 스스로 발견하거나 실제 ERP 누락이 없음을 입증하는 기능은 아니다.
- SYNTHETIC_BOH에는 ERP Event를 적용하지 않는다. 대신 Generator 완료 Watermark·Simulation ID/Version·전기 EOH 연속성을 검증하며 DEVELOPMENT에서만 허용한다. 실제 52주 Generator 구현은 별도다.

## 5. PSI 수량과 시간 의미

주차 내 순서는 확정 입고 → 기존 주문 Backorder → 현재 고객 확정 주문 → 순 Forecast다. 각 수요는 남은 가용재고만큼 부분 충족한다.

```text
net_forecast = max(gross_forecast - confirmed_customer_order, 0)
available = boh + confirmed_supplier_receipt
eoh = available - fulfilled_backorder - fulfilled_customer_order - fulfilled_forecast
backorder_close = backorder_open + confirmed_customer_order - fulfilled_backorder - fulfilled_customer_order
forecast_shortage = net_forecast - fulfilled_forecast
next_boh = current_eoh
next_backorder_open = current_backorder_close
```

- SAME_BUCKET_CONSUMPTION은 동일 Item/Week에서만 소비한다. UPSTREAM_NETTED는 받은 순 수요를 그대로 사용하고 다시 주문을 차감하지 않는다. 원래 Gross/Consumed가 없으면 NULL로 보존하며 임의 복원하지 않는다.
- 물리 재고는 `on_hand_qty`, 제외된 비가용 예약분은 `reserved_qty`, 계산 시작잔고는 `available_qty=on_hand_qty-reserved_qty`다. PSI `boh_qty/eoh_qty`는 **계획 가용재고**이며 `on_hand_boh_qty/on_hand_eoh_qty`를 별도 제공한다.
- Reserved가 0이 아니면 `UNAVAILABLE_EXCLUDED`로 명시된 비가용 보류 재고만 지원한다. 같은 고객 주문에 배정된 예약분을 다시 차감하지 않도록 `CUSTOMER_ORDER_ALLOCATED/UNKNOWN`은 거부한다. 예약 해제/배정 이벤트 모델은 후속 계약이다. 현재 지원 모드에서는 Reserved를 Horizon 동안 고정한다.
- CONFIRMED/IN_TRANSIT만 예정 입고에 가산한다. ORDERED/UNVERIFIED는 `VENDOR_COMMITMENT_NOT_VERIFIED`, RECEIVED는 `ALREADY_INCLUDED_IN_BOH`, PLANNED/CANCELLED/Legacy 공급도 제외 사유를 남긴다.
- Calendar 종료 이후 입고는 제외 Evidence로 보존한다. W0 이전의 아직 미입고 확약 공급은 자동으로 W0에 몰아넣지 않고 재확약된 예정일이 필요하다는 오류를 반환한다.
- 주차 시작/종료 Date는 포함 경계이며 연속성을 검증한다. YYYYWW의 숫자에 1을 더하거나 임의 ISO Calendar로 다시 만들지 않는다. 기간 양끝 부분 주차도 1~7일 Bucket이면 지원한다.
- 주간 집계 모델이므로 같은 주의 입고는 그 주의 수요 충족에 사용한다. 일중/주중의 상세 입출고 순서나 정확한 품절 시각을 보장하지 않는다.
- 권고 입고는 항상 0이며 이번 Slice는 BASELINE만 계산한다. RECOMMENDED, 미확약 공급 민감도, Legacy 비교 실행은 후속 범위다.

## 6. 중간 산출물과 Golden

반환 JSON에는 Canonical Snapshot, Input Manifest/Hash, EOH–BOH 대사, Forecast Consumption, Receipt 원본·포함 수량·제외 사유, PSI Bucket과 결과 Hash가 들어 있다. 오류에서도 EOH–BOH/Late Posting Evidence를 반환한다.

`BASELINE_PSI_COMPUTED_LOCALLY`는 로컬 계산 상태다. `run_claimed=false`, `database_writes=false`, `artifact_sealed=false`, `evidence_persisted=false`를 명시한다. Snapshot을 새로 봉인하거나 저장 완료 Receipt를 위조하지 않는다. 지속 보존은 `inventory_evidence` 후속 작업에서 연결한다.

14개 독립 [Golden 사례](../../tests/fixtures/GOLDEN_BASELINE.md)를 작성했다. `tests/support/fixtures.py`는 입력 포장/Hash만 수행하고 Engine을 import하거나 기대값을 계산하지 않는다. Golden JSON은 Hash를 계약 테스트에 고정했으며 정답 변경은 계산 근거·Golden Version·Hash를 함께 검토해야 한다.

## 7. 남은 작업

1. **정책 계산·보충 권고 — 다음 작업**: InventoryEngine에서 Source/Python/Effective 정책과 Hard Constraint를 구현한다. Baseline Golden을 재사용하되 권고 기대값을 독립 추가한다.
2. **실제 Source Adapter와 봉인 저장 — 다음 작업 / 일부 외부 작업 대기**: 확인된 Source만 POSM/TGSM Canonical로 변환하고 실제 재고·확약 상태·Reserved 의미를 검증한다. 고객 주문 Source 미확인은 0으로 대체하지 않는다.
3. **공통 Runner와 Evidence 영속화 — 다음 작업**: dsai-platform 공통 Cycle/Run Binding을 연결하고 Artifact·TB_IO에 모든 중간 산출물을 보존한다. 실제 Migration/DB Write·공용 Runtime 배포는 별도 승인이다.

P0-14/P0-18/P0-19의 보류는 유지한다. DSIM·Multi-Echelon은 현재 개발의 선행 조건이 아니다.
