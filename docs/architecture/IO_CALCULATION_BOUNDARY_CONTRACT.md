# Python.NET 계산 역할·Canonical 대응·Guard 경계 계약 V1

기준일: 2026-10-21 · InventoryEngine `inventory_engine_dev` · HEAD `99f2f12`.

**현재 상태 — 계약·범용 DLL·Legacy 변환·목표 전략/Guard/후보/발주 계획 Offline 완료 / 저장·Runtime 연결 미구현**

Python.NET을 C# 계산부의 우선 연결 방식으로 채택한다. 별도 C# 프로세스 방식이나 Native AOT 비교는 보류한다.
기존 Python 계산·Canonical·전략·FSN·운영 Gate·기존 Plan 검증기를 변경하지 않는다.
계약 형식은 추가하지만 기존 Run Claim·Result Bundle·개발 계산 API에 자동 등록하지 않는다.
기존 실험 DLL은 16품목·32후보·52주 입력 그대로 보존한다.
후속 [정식 DLL·Canonical Adapter](IO_CANONICAL_DLL_ADAPTER.md)는 범용 Legacy 입력과 목표 Baseline을 별도로 지원한다.
목표 Recommended의 전략·공통 Guard·허용 행동 Ledger는 후속 [Offline 실행 연결](IO_TARGET_GUARDED_EXECUTION.md)에서 구현·검증했다.
기존 Python 기본 경로는 유지하고 Runtime/Run Plan에 새 Backend를 등록하지 않았다.

## 1. 결정한 역할

| 책임 | 소유자 | 경계 |
| --- | --- | --- |
| 원천·Scope·Snapshot·Cut-off·BOH 연속성 검증 | 기존 Python Prepare/검증기 | DLL 호출 전에 유지; C# 성공을 데이터 승인으로 해석하지 않음 |
| Forecast Consumption·입고 신뢰성 판정 | 기존 Python Prepare | 이미 계산된 순 Forecast를 다시 Consumption하지 않음; 제외 공급은 증적에 보존 |
| ABC/XYZ·LEGACY_STYLE·7축·후보 생성 | 기존 Python 분류/후보 Adapter | 분류 방식·전략 종류·계산 Backend는 별도 차원 |
| 안전재고·ROP·목표재고·ML/PPO 제안 | 기존 Python 전략 경계 | C# 원문 PSI가 안전재고·EOQ를 최적 산출한다고 하지 않음 |
| 재현 PSI·내부 발주 | 원본 C# + Python.NET, 별도 Python 참조 | `LEGACY_REPLAY` 진단 전용; 목표 발주 계획으로 게시하지 않음 |
| 목표 PSI 수치 전이 | 현재 개선형 Python; C# 확장은 후속 | `TARGET_GUARDED`의 고객주문/Forecast·Reserved·Guard 의미를 유지해야 함 |
| 제약·FSN·승인/게시 통제 | 기존 Python 공통 Guard·Effective Policy·Runtime | C#에서 승인 상태나 Gate를 새로 계산하거나 완화하지 않음 |
| 서비스·비용 평가·품목별 선정·Artifact | 기존 Python 평가/선정/Writer | Legacy 주차 할당률을 목표 Fill Rate/CSL로 재명명하지 않음 |

Python.NET은 호출 수단이다. `MATHEMATICAL/PREDICTIVE_ML/DEEP_RL`에 추가되는 네 번째 전략이 아니다.
`LEGACY_STYLE`은 후보 범위 생성 방식이며 Legacy PSI를 선택했다는 뜻이 아니다.
초기 실행 연결은 수학적 전략을 우선하고, 기존 ML/PPO의 Shadow·승인 계약은 그대로 둔다.

## 2. 계산 의미와 실행 Backend를 분리한다

| 의미 프로필 | 허용 Backend 선언 | 발주·수요·수치 의미 | 현재 구현 상태 |
| --- | --- | --- | --- |
| `LEGACY_REPLAY` | `CSHARP_PYTHONNET`, `PYTHON_REFERENCE` | 기말 Position의 엄격한 `< ROP`, OUTL/Lot, 단일 Demand 부족의 BO 이월, 원본 double/decimal 변환 | 원문 재현·실제 출력과 Canonical 진단 투영 비교 완료. 원본 SQL 전체 E2E는 아님 |
| `TARGET_GUARDED` | `PYTHON_EXISTING` | 주초 관측·외부 전략·Guard, 고객주문 BO와 Forecast Shortage 분리, 기존 UOM/Decimal 규칙 | 기존 Python 경로 유지. 새 Binding을 소비하도록 연결한 것은 아님 |
| `TARGET_GUARDED` | `CSHARP_PYTHONNET` | 동일 목표 규칙과 승인 행동만 상태에 반영 | 별도 Baseline·Recommended/Guard·후보 평가/선정·발주 계획 Offline 비교 완료. 저장·Runtime은 미연결 |

`PYTHON_REFERENCE`는 `run_legacy_reference`, `PYTHON_EXISTING`은 기존 개선형 Baseline/Recommended 경로를 뜻한다.
후자를 C# 재현 구현이라고 부르지 않는다. C# 목표 확장은 단순 Wrapper 추가와 구분한다.

- `LEGACY_REPLAY`에서 Baseline은 허용하지 않는다. 원문 DLL이 내부 발주를 생성하므로 ROP를 0으로 넣어도
  발주 금지가 증명되지 않는다. 목표 Baseline은 새 주문을 생성하지 않는 별도 규칙이다.
- `LEGACY_REPLAY`는 `RECOMMENDED` 계산 형식을 쓰더라도 원본 권고의 진단 출력일 뿐이다.
  내부 주문·주차 PSI를 그대로 보존하고 제약 위반 여부는 비변경 진단으로만 연결한다.
- 목표 계산은 원문 결과의 수량·납기·BO·서비스 지표를 사후에 바꿔서 만들지 않는다.
  원문 재현 결과와 목표 개선 결과는 같은 입력·조건일 때 차이를 설명하는 별도 결과다.
- 과거 Artifact와 Hash는 보존한다. 새 계산 의미/Build를 예전 구현 Version에 숨기거나 이전 Hash를 강제로 유지하지 않는다.

## 3. Canonical → 계산 입력 대응

먼저 기존 `CanonicalInputRequest`와 `PrepareInventoryInputUseCase`를 사용한다.
실행 시 `validate_prepared_projection`으로 정확한 Canonical 투영인지 확인해야 한다.
Prepared의 자기 Hash만 맞는 것으로 원천 정합성·Scope·Cut-off 입장을 대신하지 않는다.

아래는 **변환 계약**이다. 실제 구현·지원 범위·정밀도 제한은 [Adapter 보고서](IO_CANONICAL_DLL_ADAPTER.md)를 따른다.
Canonical을 투영한 Legacy 진단은 원본 Legacy SQL 입력을 그대로 재생한 것과도 구분한다.

| Canonical/준비 입력 | C# 원문 DTO·계산 입력 | 목표 계산·변환 규칙 |
| --- | --- | --- |
| `context`·`input_bindings` 8종 | 계산 숫자에 대응하지 않음 | Python이 회사·Subsidiary·Site·Plan·Run·W0·Demand/Calendar/Master·Config를 검증하고 결과와 다시 연결 |
| `snapshots.master.rows` | `ItemID/BufferID`의 외부 Index | 승인된 Universe만 사용; 품목·Site·후보의 역매핑을 보존. Buffer 문자열 파싱으로 Scope를 추정하지 않음 |
| `calendar.yyyyww/seq/start_date/end_date/base_month` | `BucketID/BucketIndex/StartDate/EndDate/BaseMonth` | 공유 Calendar의 명시 값을 전달. 현재 실험 Facade가 BaseMonth를 월초로 재생성하는 동작은 Canonical Adapter로 복사하지 않음 |
| `positions.available_qty` | `BufferPSIObject.BOH`의 진단 투영 후보 | `on_hand_qty`를 넣어 Reserved까지 사용하지 않음. 목표 전이는 Reserved를 별도 상태로 보존 |
| `positions.reserved_qty/on_hand_qty`·Reserved 처리 Metadata | 원본 커널에 별도 동등 필드 없음 | Legacy 투영에서 제외·보존 의미를 명시. 목표 C# 상태/결과는 두 수량을 별도 지원해야 함 |
| `positions.backorder_qty` | 초기 `AccBO` | 목표에서는 확정 고객주문 BO만 이월. 원문 실행 이후 BO는 단일 Demand의 부족도 포함하므로 목표 고객 BO로 재명명하지 않음 |
| `prepared.demands.net_forecast_qty` + `confirmed_customer_order_qty` | 원본 `SALES_ORDER.QTY → PlanBucket.Demand` | 목표에서는 두 수요를 끝까지 구분. Legacy 진단용 합산은 명시적 투영이며 원 수량·Consumption 근거를 함께 보존 |
| Forecast Snapshot의 `forecast_netting_mode` | 원문에 동등 필드 없음 | 기존 `SAME_BUCKET_CONSUMPTION/UPSTREAM_NETTED` 판정은 Python이 수행. C#이 순 Forecast를 재차 차감하지 않음 |
| `prepared.receipt_decisions` | `STOCK.USABLE_DTTM/QTY → DueIn` | 목표는 `included_qty`만 공급으로 사용하고 원본·제외 사유를 보존. 복수 입고를 C# 첫 일치 행에 맡겨 유실하지 않음 |
| 시작 시 포함된 확정 공급 Ledger | 초기 `AccPO`의 투영 근거 | 포함 공급의 Grain·합계와 DueIn 차감을 일관되게 정의. 초기 PO를 임의 0으로 넣거나 같은 입고를 두 번 가산하지 않음. 기간 밖/제외 공급의 처리도 별도 표시 |
| `policies.lead_time_days` | `StockLeadtime`은 직접 동등하지 않음 | 원문은 `int(raw / 28800)`을 사용. Canonical 일수의 진단 투영에 이 단위를 쓰면 명시적으로 `days × 28800`임을 기록하고 원본 BOR/SQL 단위 재현이라고 하지 않음 |
| SDE 보호기간 보정 | 원문 물리 `StockLeadtime`의 대체값 아님 | 실제 주문 도착은 물리 Lead Time으로 계산. 보호기간을 운송기간으로 덮어쓰지 않음 |
| `policies.moq/order_multiple` | `Lot.MinLotSize/IncreaseLotSize`와 의미가 다름 | 목표 Guard의 절대 MOQ·배수 규칙 유지. Legacy Lot 재현에는 원 Min/Max/Step 근거가 필요 |
| `policies.physical_max_capacity` | `Lot.MaxLotSize` 또는 `MAX_LV`가 아님 | 물리 Hard Constraint는 Guard에 유지. Legacy Max Lot은 상한이 아니라 반올림 기준점이며 초과 수량도 나올 수 있음 |
| `source_rop_qty/source_target_inventory_qty`·후보/선정 정책 | `SAFETY_STOCK.ROP/MAX_LV` | Python 파생값·Source 제약·Override 우선순위를 유지. Legacy 투영에서는 어떤 원 정책을 사용했는지 별도 봉인 |
| `effective_from/effective_to` | C#은 BaseMonth에서 기간 양끝 포함 | 목표는 판단일 기준 `[from,to)`. 종료일을 그대로 inclusive로 복사하지 않음. 원문 기간/순서가 필요하면 별도 Legacy 근거를 사용 |
| Legacy `MOS/MA_DMD_QTY/StockPolicy` | 주차 ExpMOS 계산 인자 | Canonical에 직접 대응하는 값이 없으면 별도 원문 인자 근거 필요. Legacy `OUTL/ROPQ/MINQ/SFST`를 현재 정책 종류에 이름만으로 대응시키지 않음 |
| Canonical decimal 문자열·UOM 규칙 | 원문 binary64, Lot 중간 decimal | Legacy는 원문 수치 경로 보존. 목표는 기존 planning/physical scale와 계산 의미를 유지하고, C# 지원 범위를 넘으면 오류. 무조건 double 변환·임의 반올림 금지 |
| `prior_inventory`·이벤트·마감 증적 | 원문 계산 숫자의 직접 입력 아님 | Python에서 BOH 연속성·조정·마감 검증에 사용. DLL에 넘기지 않는다고 검증이나 Evidence에서 버리지 않음 |

Target의 기존 입고 포함 상태는 `CONFIRMED/IN_TRANSIT`이다.
`ORDERED/UNVERIFIED_DUE_IN`과 `LEGACY_ASSUMED_CONFIRMED`는 기존 목표 PSI 제외 규칙을 유지한다.
Legacy에 대한 과거 가정 공급 비교를 추가하더라도 목표 Baseline에 섞지 않는다.

### 별도 근거가 필요한 Legacy 인자

Legacy 진단의 `legacy_parameters_binding`은 이미 있는 원문 입력/정책 Artifact의 Reference·Hash를 사용한다.
임의 Snapshot·Run ID를 새로 만들기 위한 필드가 아니다. 해당 인자가 없다면 `LEGACY_PARAMETERS_REQUIRED`로 거부한다.
후속 Adapter는 봉인된 인자의 품목·후보 중복·전체 품목·BaseMonth 기간·정밀도를 확인한다.
실제 Source Producer/Catalog와 전체 후보를 대사하는 연결은 후속이다. Reference 존재만으로 준비 완료라고 하지 않는다.

- `StockPolicy`, raw `StockLeadtime` 또는 명시적 Canonical 일수 투영 근거
- `MinLotSize/MaxLotSize/IncreaseLotSize`
- `ROP/MAX_LV/MOS/MA_DMD_QTY`, 원 정책 기간과 입력 순서

현재 LEGACY_STYLE Feature Producer의 Lead Time·이력·단가가 있다는 사실만으로 이 원문 인자가 모두 확보됐다고 판단하지 않는다.
확인되지 않은 값의 0 대체·최근 정책 자동 선택·고정 Q와 ROPQ의 자동 치환은 금지한다.
개발 검증에는 합성 인자 Fixture를 명시적으로 봉인해 사용할 수 있다. 이를 실제 Source나 원본 SQL 입력과 동일하다고 표시하지 않는다.

## 4. 발주·Guard·PSI 순서

### 목표 Recommended PSI — `VALIDATE_BEFORE_STATE_COMMIT`

```text
기존 입력 봉인·Scope·Cut-off 검증
    ↓
기존 Effective Policy·FSN·모델 승인 입장 확인
    ↓ 주차/품목별 반복
현재 상태·기존 미도착 공급 관측 → 봉인 전략의 원 행동 제안
    ↓ 상태나 입고 Ledger를 아직 변경하지 않음
기존 공통 Guard: 발주일·물리 LT/도착·MOQ·배수·용량·승인 제약
    ↓
허용 수량·납기만 미도착 공급에 등록
    ↓
당주 확정/권고 입고 → 과거 고객 BO → 당주 고객주문 → 순 Forecast
    ↓
EOH·고객 BO·Forecast Shortage 기록 → 다음 주차 상태
```

원 행동·Guard 결과·조정/거부 사유를 모두 보존한다. `REVIEW`의 계산·증적은 허용하되 자동 게시·발주는 금지하고,
`BLOCK`은 기존 계약대로 주문을 차단한다. 새 Binding의 통과는 FSN 입장이나 사용자 승인을 의미하지 않는다.

Guard가 수량·납기를 바꾸면 다음 전략 관측과 후속 PSI도 변경된 공급을 사용해야 한다.
원문 커널로 전체 기간을 끝낸 후 주문표만 자르거나, 같은 원 행동을 고정해 마지막 PSI만 다시 계산하는 것은 허용하지 않는다.
후속 판단이 이전 상태에 의존하므로 동일 봉인 입력과 승인된 행동으로 순차 상태를 다시 구성해야 한다.

현 C# 전체 기간 커널에는 이 Guard 개입점·분리 수요·Reserved 상태 계약이 없다.
후속 구현에서 원문 커널과 분리한 **목표 수치 전이와 Baseline 인터페이스**를 추가·검증했다.
외부 전략→Guard→허용 행동 Ledger의 Offline 연결도 완료했다. 이 결과로 운영 실행/게시 승격을 허용하지 않는다.
Python Guard를 C#으로 복제하거나 주차마다 Python 콜백을 숨겨 넣는 것을 이 계약의 완료로 간주하지 않는다.

### 목표 Baseline — `NO_NEW_ACTIONS`

새 전략 행동·권고 입고는 없다. 시작 상태와 포함된 기존 확정 공급만 사용한다.
기존 Python의 수요 차감·EOH 이월을 유지하고, 미래 C#에서도 내부 자동 발주가 꺼졌음을 검증해야 한다.
모든 Target Baseline/Recommended 결과는 기존 공개 필드의 고객 BO·Forecast Shortage 의미를 유지한다.

### Legacy 진단 — `DIAGNOSTIC_POSTCHECK_NO_MUTATION`

원문 순서인 입고 → 과거 BO → 단일 Demand → 기말 ROP/OUTL/Lot 발주 → LT0 보정 → 이월을 바꾸지 않는다.
현재 DLL 그대로의 결과는 진단 원문이며 Target Guard의 accepted action이 아니다.
사후 제약 진단이 있더라도 원문 수량을 바꾸거나 `approved/operational` 결과로 게시하지 않는다.
원문 수치 동등성과 업무 적합성을 서로 다른 검증 결과로 남긴다.

## 5. 참조 Binding과 구조 검증

Python: [CalculationBoundaryBinding](../../src/dsio_inventory_engine/inventory_contracts/calculation_boundary.py)
· Schema: [inventory_calculation_boundary_binding.schema.json](../../schemas/inventory_calculation_boundary_binding.schema.json).

계약 종류는 `inventory-calculation-boundary-binding / 1.0.0`이다. 계약명은 실행별 ID가 아니다.
이 문서 자체의 `binding_id/content_hash`나 새로운 Run/Snapshot ID를 생성하지 않는다.

| 필드 | 계약 |
| --- | --- |
| `execution_mode` | `CALCULATION_ONLY`만 허용; 게시·발주 권한 필드는 허용하지 않음 |
| `calculation_backend`·`semantics_profile`·`psi_scenario_type` | 호출 수단·계산 의미·Baseline/Recommended를 각각 구분 |
| `engine_run_id` | Canonical Context의 기존 값. Canonical Hash가 Run ID를 제외하므로 별도로 일치 검사 |
| `canonical_input_hash`·`prepared_content_hash` | 기존 Canonical/Prepared Hash 그대로 참조 |
| `input_bindings` | 기존 8종 Snapshot ID·Hash 그대로. 회사/Site/Plan/W0/Config는 Canonical 원본을 통해 검증 |
| `psi_simulator` | 기존 형식인 `implementation_id/version/implementation_content_hash` 사용. 사용자 입력/새 인증 수단 아님 |
| `guard_order` | 위 세 순서 중 Profile/Scenario에 맞는 값만 허용 |
| `replenishment_execution_binding` | 목표 Recommended에서 기존 Strategy Descriptor·Strategy Input Binding·Replenishment Config Hash 참조. Baseline/Legacy는 null |
| `legacy_parameters_binding` | Legacy 진단에서 기존 원 인자 Artifact Reference·Hash 필수. Target은 null |

기존 `inventory.psi.weekly_roll_forward / 1.0.0`은 기존 Python용이다. 다른 C# 구현을 이 Version에 위장시키지 않는다.
새 Legacy 진단의 논리 구현명은 `inventory.psi.legacy_replay`로 구분한다.
이는 새로 발급하는 실행 ID가 아니다. 별도 DLL의 실제 Build Hash·입출력 대조는 후속 보고서에 기록하며 Registry 연결은 아직 없다.
목표 C# 확장도 원문 Legacy 구현명으로 표시하지 않는다.

`from_sources()`는 8종 `SEALED`·건수·Source Hash·Snapshot Binding과 Canonical/Config 참조를 확인한다.
`verify_sources()`는 신뢰한 호출자가 전달한 원본·Prepared Hash·Build·전략·Config와 다시 대조한다.
Hash의 출처를 인증하거나 Cut-off·Source Projection·Model 승인·기존 Guard 실행을 대신하지 않는다.
특히 동일하게 조작한 Prepared와 자기 Hash를 넣어도 안전하다고 주장하지 않는다. 실행 연결 시 기존 투영 검증이 필수다.

현재 Plan의 `_psi_simulator`는 기존 Python ID/Version 하나로 고정돼 있다.
새 Binding을 선언할 수 있다는 이유로 기존 Run Plan이 새 구현을 받을 수 있다고 해석하지 않는다.
기존 Canonical·Replenishment·Result Bundle Schema/DTO의 Version이나 필드 집합도 이번에 바꾸지 않았다.

## 6. 출력 의미와 검증 기준

- 원문 `Demand/BackOrder/AccBackOrder`는 목표의 확정 고객주문·Forecast Shortage 구분을 대신하지 않는다.
  원문 주차 `DemandAllocationRate/ExpMOS`는 목표 Fill Rate·CSL·평균 MOS와 같은 지표가 아니다.
- 원문 DueIn에는 계산된 뒤의 이전/현재 Bucket 수정이 포함될 수 있다. 목표 입력 확정 공급이나 권고 입고로
  무조건 복사하지 않는다. Target은 approved supply ledger와 주차 상태의 실제 결과로 행을 만든다.
- 품목·후보·주차 Coverage, 주문·입고 합계, 재고/고객주문 보존, 원 행동·허용 행동·상태의 일관성을 검증한다.
  C# Target에서 지원하지 않는 필드/정밀도/기간은 명시적으로 실패하며 Legacy 결과나 기존 Python으로 자동 대체하지 않는다.
- 같은 Legacy 입력은 원문 C#과 별도 Python 참조로 기존 숫자·날짜·순서를 대조한다.
  Target 구현은 기존 목표 Golden과 대조한다. 두 Profile 사이의 의도된 차이를 불일치 0건으로 숨기지 않는다.
- 선택된 후보의 Simulation과 발주 계획은 같은 입력·Build·Profile·행동 규칙을 사용해야 한다.
  기존 선정 결과를 다른 계산 의미에 재적용하지 않는다. Build와 의미가 바뀌면 재계산·재평가가 필요하다.
- 공통 결과 Artifact에는 기존 Reference/Hash/Receipt 방식을 재사용한다. 새 Child 종류·Writer·DB 테이블을 이번에 추가하지 않는다.

## 7. 검증·보존 범위와 다음 순서

이 절의 81/610/48건은 경계 계약을 처음 고정한 검증 이력이다.
그 이후 정식 DLL·Adapter 완료 범위와 현재 다음 작업은 [Adapter 보고서](IO_CANONICAL_DLL_ADAPTER.md) 및 [개발 기준선](IO_DEVELOPMENT_BASELINE.md)을 우선한다.

신규 구조·출처 참조 테스트는 [계약 테스트](../../tests/contract/test_calculation_boundary_contract.py)에 있다.
선택된 프로필 조합, 권한 필드 차단, Legacy Baseline/전략 혼합 거부, Guard 순서·Build Version 재사용 거부,
Run/Plan/Site/W0/Config·각 Snapshot 변조와 동일 Source의 결정론적 직렬화를 확인한다.
이는 순서 규칙이 실제 C# 상태 전이에 구현됐다는 테스트가 아니다. 기존 계산 회귀와 원문 출력은 별도로 보존한다.

신규 계약 검증 **81 passed**, 관련 Unit·Contract·Offline Integration **610 passed / 102 subtests passed**(27.10초)다.
추가 Baseline·Canonical V2·CLI·Context Binding 묶음은 별도로 **48 passed / 56 subtests passed**(1.00초)다.
Python 394파일 Ruff·Format과 `git diff --check`를 확인했다. 전체 저장소 테스트, 새 C# 실행·성능 측정·Coverage·독립 검토는 하지 않았다.

**1. 정식 DLL·Canonical Adapter 확장 — 후속 구현 완료 · InventoryEngine**

- 이번 계약에 따라 Source와 원 인자 Coverage를 확인하고 실제 입력·출력 변환기를 구현한다.
  원문 재현과 목표 전이 인터페이스를 별도로 두고, 누락 인자·형 변환·첫 일치/합산·기간 경계를 검증한다.
- 계약 확정 후 C# 라이브러리와 Python Adapter 준비는 병렬 가능하다. 공통 입력/동등성 검증은 직렬로 합친다.

**2. 실제 Guard·후보·발주 계획 실행 연결 — 후속 Offline 구현 완료 · InventoryEngine**

- 기존 Python 전략·Guard를 유지하며 C# 목표 전이가 승인 행동만 소비하는지 검증한다.
  전체 후보 평가·선정·발주 계획을 같은 Profile/Build로 닫는다. 기존 구현을 다시 만드는 작업은 하지 않는다.

**3. Artifact 저장·복구와 독립 IO 검증 — 현재 다음 작업 · InventoryEngine**

- 기존 Writer/Reader/Verifier에서 중단·정합성·재계산을 확인한다. 계산 의미가 다른 과거 결과를 재사용하지 않는다.
  이후에만 Platform/Demand 연결 검증으로 넘어간다. 자원 관리 추가 고도화는 계속 보류한다.

**승인 필요 — 실제 DB Write·Migration·공용 Runtime 배포**

- 이번 작업에는 DB·Runtime·Forecast 재실행·실제 발주·Commit/Push가 없다.
  검증 결과와 파일 보존 확인은 [검증 증적](evidence/calculation-boundary-contract-verification-20261021.json)에 기록한다.
