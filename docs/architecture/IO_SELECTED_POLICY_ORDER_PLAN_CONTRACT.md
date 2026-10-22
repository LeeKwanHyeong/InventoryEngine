# 선정 정책 → 보충 전략·공통 PSI → 계산용 발주 계획 계약 V1

후속 상태(2026-10-21): [Python.NET 계산 경계 계약](IO_CALCULATION_BOUNDARY_CONTRACT.md)을 확정했다.
아래 선정 정책 Adapter와 검증 이력은 기존 개선형 Python의 것이며 C#으로 변경하지 않았다.
별도 C# 재현/호출 실험과 [정식 DLL·Canonical Adapter](IO_CANONICAL_DLL_ADAPTER.md)의 목표 전이·Baseline,
[전략·Guard·후보 평가/선정·발주 계획](IO_TARGET_GUARDED_EXECUTION.md)의 Offline 연결도 완료했다.
선정 후보와 발주 계획의 계산 의미·Build가 다르면 기존 선정을 그대로 재사용하지 않는다.
후속 [로컬 저장·조회·중단 복구](IO_TARGET_PIPELINE_STORAGE.md)도 Offline 검증을 완료했다.
기존 발주 계획 원문/Hash는 바꾸지 않고 바깥 계산 Binding과 함께 보존한다. Platform Run/DB 연결은 아직 없다.
후속 [Result Bundle·Projection 연결](IO_TARGET_RESULT_LINK_CONTRACT.md)도 Offline 검증했다.
원본 Bundle은 바꾸지 않으며 선정 계획은 기존 선정 Child의 EVIDENCE_ONLY Simulation으로만 표현한다.
현재 다음 작업은 [개발 기준선](IO_DEVELOPMENT_BASELINE.md)의 로컬 Runtime·Platform 조회 조립이다.
아래 과거 C# 실행 미검증 문구는 당시 상태이며 Legacy C# 전체 선정/발주 계획과의 동등성은 여전히 미검증이다.

기준일: 2026-10-20 · 대상: InventoryEngine `inventory_engine_dev`.

상태: **연결 계약·Python 검증기·JSON Schema와 실제 선정 정책 실행 Adapter의 Offline 검증 완료**.
발주 계획의 영속 저장·조회/복구·Platform Run/API/화면 연결은 아직 없다.
2026-10-20 C# 원본 대사 이후 저장 작업은 **계산 의미·비교 Golden 정리 뒤로 보류**한다.
현재 Python 실행 계약·기존 검증 결과는 유지하며, C# 수치 동등성이 확인된 상태는 아니다.
기존 후보 생성·Simulation·품목별 선정을 다시 구현하지 않는다. 발주요청 확정·ERP 전송·DB 적용은 포함하지 않는다.
FSN 설정·운영 Gate·기존 결과와 Hash는 변경하지 않는다. 자원 관리 추가 고도화와 대규모 강건성 실험은 보류한다.

## 1. 목적과 첫 실행 범위

```text
기존 품목별 선정 결과
    ↓ 기존 Reference로 조회·검증
동일 Plan·Site·Run의 봉인 입력 + 선정 정책 모수
    ↓ RunSelectedPolicyOrderPlanUseCase
기존 보충 전략 → 공통 Guard → 공통 PSI
    ↓
Recommended PSI + 품목별 계산용 발주 계획
```

V1은 **선정에 사용한 동일 봉인 입력의 실행 연결**이다. Run·Planning Cycle·Plan·Site·W0·Config Revision·Cut-off 등
원 Context가 일치해야 하며 Canonical Hash와 수학 입력 Snapshot Hash도 같아야 한다.
Canonical Hash가 Run ID를 제외한다는 기존 의미는 유지하되, 연결 검증에서는 Context의 Run ID도 별도로 대조한다.

다른 날짜의 재고·수요, 바뀐 Forecast/Config 또는 다른 Plan에 과거 정책을 재적용하는 재계획은 이 V1에서 허용하지 않는다.
그 기능은 정책 적용기간·승인·새 입력의 허용 범위를 정의한 뒤 확장한다. 조용히 최신 Snapshot을 탐색하거나
입력 변경을 동일 실행 Retry로 취급하지 않는다. 이 제한은 첫 독립 IO 흐름을 닫기 위한 범위이며 운영 Gate 완화가 아니다.

Legacy C# PSI Simulation의 업무 역할을 현재 보충 전략·공통 PSI로 연결한다는 개발 방향이다.
2026-10-20 `NSPM 2/SupplyEngine/DSIO_postgresql_20250908/RootIO`에서 C# PSI·Lot·Optimizer 본문을 확보했다.
[C#·Python 대사 문서](IO_LEGACY_CSHARP_PYTHON_RECONCILIATION.md)에서 실제 호출 경로와 차이를 정리한다.
C#은 당주 입고·수요 처리 뒤 공통 ROP/OUTL 식으로 발주하고, 현재 Python은 주차 시작에 기존 두 후보 정책으로 판단한다.
원본의 정책 코드별 switch는 주문 분기가 아닌 ExpMOS 계산이다. LEGACY_STYLE 후보를 사용해도 C# 계산을 재현한 것은 아니다.
이번 확인은 정적 대사이며 C# 실행·배포본·수치 동등성은 미검증이다. 이 문서의 기존 Python 계약을 변경하거나
운영용 별도 계획 엔진을 추가한 것은 아니다. 비교용 원본 재현 범위는 후속 계약에서 정한다.

## 2. ID·Hash 재사용과 최소 연결 문서

Python: [SelectedPolicyOrderPlanBinding](../../src/dsio_inventory_engine/inventory_contracts/order_planning.py).
Schema: [selected_policy_order_plan_binding.schema.json](../../schemas/selected_policy_order_plan_binding.schema.json).

계약 종류는 `inventory-selected-policy-order-plan-binding / 1.0.0`이다. `contract_id`는 문서 형식의 이름이지
실행별 새 식별자가 아니다. 새 `order_plan_id`, `binding_id`, 연결 문서 자체의 `content_hash`를 만들지 않는다.

| 필드 | 의미와 기존 값 |
|---|---|
| `contract_id`, `contract_version` | 연결 문서 형식과 Version |
| `execution_mode` | `CALCULATION_ONLY`만 허용 |
| `selection_result_reference` | 기존 `PSEL-<Hash>` 또는 `OCPSEL-<Hash>` |
| `selection_result_content_hash` | 기존 선정 결과의 `content_hash`; Reference와 같은 값인지 검사 |
| `canonical_input_hash` | 선정 결과와 기존 Canonical 입력의 Hash |
| `mathematical_input_binding` | 기존 수학 입력의 `contract_id`, `contract_version`, `snapshot_id`, `content_hash` 그대로 |

회사·Subsidiary·Site·Plan·Run·W0는 기존 Canonical Context에서, 입력 8종의 Snapshot ID·Hash는 기존
`input_bindings`에서 읽는다. Config·분류 ID/Hash와 Candidate ID·선정 정책 모수는 기존 선정/원 Source에서 읽는다.
이 연결 문서에 Context·품목 목록·정책·중복 Hash를 복제하지 않는다. 사용자가 Hash를 입력하는 UI도 만들지 않는다.

최종 결과를 저장할 때는 기존 Artifact의 Reference·Content/Byte Hash·Receipt 규칙을 재사용한다.
현재 계약은 Writer·Receipt·Runtime Plan·API Registry를 변경하거나 새 계약을 자동 활성화하지 않는다.

## 3. 입력 검증과 검증 수준의 구분

1. 기존 두 선정 계약의 Python Verifier를 재사용한다. 다른 계약·Version·선정 Hash·권한 상승은 거부한다.
2. 기존 `MathematicalPolicyRequest`와 `CanonicalInputRequest`로 입력을 정규화한다.
3. 수학 입력과 Canonical 8종이 `SEALED`여야 하며 기존 `LOCAL_SHADOW` 계산 입력만 받는다.
4. 선정 Context·Canonical Hash·수학 입력 Hash가 계획 입력과 일치해야 한다.
5. 모든 활성 재고관리 품목이 선정 결과의 품목 집합과 정확히 일치해야 한다. 미선정 품목도 누락하지 않는다.
6. 선택된 정책은 현재 지원하는 `ROP_FIXED_Q`, `PERIODIC_ORDER_UP_TO`만 허용한다.
7. 기존 연결 문서 재조회 시 `verify_sources()`로 같은 원본과 대조한다. 새 Hash 계층 없이 참조 치환을 차단한다.

이 검증기는 **문서 구조·기존 Hash·Source 연결의 정합성 검증**이다. 원천 업무 자료의 정확성, 사용자 승인,
Cut-off 입장 심사 또는 실제 후보 PSI 재생 검증을 대신하지 않는다. Hash가 맞는다고 업무 승인된 정책이 되지 않는다.

실행 Adapter는 기존 `RunPolicySelectionUseCase` 또는 `RunCatalogPolicySelectionUseCase`의 원 Source 검증을 통해
정확한 Config·전체 분류·Mathematical 요청·Catalog와 선정 결과를 대사해야 한다. 그 다음 기존 입력 준비·Admission과
Guard를 그대로 수행한다. 이 계약 검증기가 `simulate()`를 호출하거나 단순 조회 때마다 PSI를 재계산하지는 않는다.

## 4. 실행 Adapter의 정책 적용 규칙

- 품목별 원 `selected_candidate_id`, `selected_candidate_result_hash`, `selected_policy`와 선정/미선정 사유를 유지한다.
  `selected_policy`는 정책 종류·RMOS·EMOS·서비스수준·점검주기 등 탐색 모수이며 실제 주문 수량과 다르다.
- 기존 후보 정책 계산·보충 행동·공통 Guard를 재사용한다. 과거 `orders` 배열을 복사해 새 발주 계획이라고 표시하지 않는다.
- 물리 Lead Time·MOQ·발주배수·용량·주문 Calendar·확정 공급·기존 권고 미도착 공급·Backorder·Forecast 소진 규칙을 유지한다.
  SDE 보호기간을 물리 납기로 바꾸지 않는다. 누락 입력을 0으로 채우지 않는다.
- FSN/PLC/HML 등 원 Effective Policy와 운영 Gate를 수정하지 않는다. `REVIEW`는 기존 허용 범위에서만 계산·증적을 남기고,
  자동 게시·발주를 허용하지 않는다. `BLOCK`·계산 제외·미선정은 사유와 Baseline을 남기며 임의 대체 정책을 선정하지 않는다.
- 전 품목 미선정도 계약상 유효하다. 후속 계획 결과는 품목별 미선정 사유를 표시하고 신규 주문 0건을 성공 발주로 위장하지 않는다.
- 기존 Baseline·Mathematical 기준선·후보 PSI·선정 문서와 Hash를 덮어쓰지 않는다.

## 5. 발주 계획 산출물은 기존 PSI·주문·행동 증적을 재사용한다

계획 Grain은 **Site + Item + 판단 시점의 행동**이다. 계산용 계획은 확정 발주요청이나 ERP 구매발주서가 아니다.
다음 필드를 기존 [공통 Recommended PSI](../../src/dsio_inventory_engine/recommend_replenishment/application/run.py)에서 소비한다.

| 산출 내용 | 기존 필드/Source |
|---|---|
| 계획 Context와 품목·단위 | Canonical Context, `item_id`, `uom` |
| 선정 정책 근거 | 선정 결과 Reference → 해당 품목의 Candidate ID·정책 모수 |
| 실제 계획 주문 | `recommended_orders`: 기존 `order_id`, `decision_id`, `quantity` |
| 발주일·납기·PSI 반영일 | `order_date`, `due_date`, `receipt_date`, `receipt_bucket`을 구분 |
| Source 정책 | 기존 `source_policy_id`; Candidate ID로 덮어쓰지 않음 |
| 원 제안·제약 적용·거절 사유 | `decision_evidence`의 proposal·validation과 기존 사유 코드 |
| 계획 재고 흐름 | `psi_rows`; BOH·수요·공급·EOH·Backorder·Shortage |
| 미선정·검토 필요 | 원 선정 사유·`source_review_required`, 기존 Gate Evidence |

양수 수락 주문만 `recommended_orders`에 존재한다. HOLD·거절·미선정·차단을 주문 배열만으로 설명하지 않고
품목별 선정 결과와 행동 증적을 함께 조회한다. 공통 PSI는 주문을 `SIMULATED_PENDING`으로 생성한 뒤
기간 내 입고를 모사한 주문을 `SIMULATED_RECEIVED`로 반환한다. 둘 다 실제 구매발주·실입고 완료 상태가 아니다.
최종 계산용 결과의 자동 게시·자동 발주·업무 정책 승인·DB Write는 모두 허용하지 않는다.
기존 결과에 자동 실행 가능 필드가 있더라도 이 계산용 계약으로 그 권한을 얻을 수 없다.

기존 `tb_io_psi_bucket`, `tb_io_replenishment_proposal`, 정책 선정·진단 Projection을 우선 재사용하는 것이 후속 방향이다.
이번에는 새로운 관계형 Table·PK/FK·Migration을 만들거나 적용하지 않는다. 발주요청 `req_no`, 확정 수량·수정 승인,
`IF_STATUS`, MAPS/DB2/AS400 전송·응답 수신은 후속 Platform/ERP Adapter 범위다.

## 6. 연결 계약 최초 검증 이력

최초 구현은 연결 문서 생성·검증, 두 기존 선정 결과의 호환성, JSON Schema와 Negative Test다.
당시 실제 실행 Adapter는 없었다. 아래 7절이 현재 완료 범위이며 이 절의 검증 수치는 이전 이력으로 보존한다.
기존 FSN·운영 Gate·DB·공용 Runtime·Commit/Push는 변경하지 않는다.

- 신규 단위 27건·계약 24건을 포함한 관련 회귀 **249 passed / 62 subtests passed**(19.21초).
  전체 회귀를 재실행한 수치는 아니다. 기존 탐색·선정·Catalog PSI·고정 Hash와 Runtime Effective Policy Gate 테스트를 포함한다.
- 기존 두 선정 계약과 세 Catalog Mode, Run/Site/Plan/W0·입력 치환, 미봉인 8종 입력, 품목 Coverage,
  참조 변조·추가 ID/Hash/권한 필드, FSN BLOCK/REVIEW 불변과 계약 검증의 PSI 무호출을 검증했다.
- Ruff·Format 신규 Python 4개, JSON/Schema 41개, 이 문서의 로컬 링크 3개·Diff Check를 확인했다.
  Coverage 수치·실제 DB·공용 Runtime·ERP 검증은 수행하지 않았다.

## 7. 2026-10-20 실제 실행 연결 — Offline 구현·검증 완료

구현: [RunSelectedPolicyOrderPlanUseCase](../../src/dsio_inventory_engine/recommend_replenishment/application/selected_order_plan.py).
외부 전략 주입·운영 승격·최신 Source 탐색 없이 원 봉인 입력만 처리하는 명시적 로컬 호출이다.

### 실행 순서와 기존 식별자 보존

1. 기존 7필드 Binding과 원 `MathematicalPolicyRequest`를 정규화하고 동일 Context·입력·선정 Hash·봉인 상태를 확인한다.
2. ABC V1은 원 요청·전체 분류·Config로 전체 Source를 재생한 후 기존 선정기를 대사한다.
   Catalog는 정확한 Catalog·Producer·원 Source를 기존 Catalog 선정 Verifier로 재생·대사한다.
   양수 주문만 검증하지 않고 실패 후보·제외·비용·Baseline·선정/검토 상태도 확인한다.
3. 기존 입력 준비에서 Cut-off·재고 이월·고아 품목·Snapshot을 검증하고 수학 입력의 이력 통계를 준비한다.
   반환하는 `prepared_input`은 원 봉인 문서이며 Effective Policy 적용은 별도의 계산용 Projection에서만 수행한다.
4. 선택된 품목만 기존 `CandidateStrategy`에 원 정책을 전달하고 공통 `validate_controls` → `simulate`를 실행한다.
   Guard·행동 검증·미도착 공급·재고 전이는 기존 구현을 그대로 사용한다.
5. 새 계산의 PSI·주문·관측·제안·제약 조정·권한이 원 선정 후보와 일치하는지 대조한다.
   불일치는 오류이며 저장된 주문으로 대체하지 않는다. 일치 확인 후 기존 정책 공식 증적만 다시 연결한다.

원 Candidate Execution Hash·Strategy Input Binding을 사용하므로 `decision_id`, `REC-<decision_id>`,
Source `policy_id`, 원 선정 Reference·Hash와 품목별 원 후보의 결과 Row/행동 Hash가 유지된다. Candidate ID를 Source 정책 ID로 덮어쓰지 않는다.
새 `order_plan_id`, `binding_id`, Root `content_hash`를 추가하지 않는다.

### 로컬 결과의 의미

현재 반환값은 **메모리 내 계산 결과 View**이며 새 영속 Artifact나 게시 가능한 업무 결과 계약으로 등록하지 않았다.

- `order_plan_binding`, `context`, 기존 `prepared_input`과 `baseline_psi_rows`를 보존한다.
- `psi_rows`, `decision_evidence`, `recommended_orders`와 기존 방식의 세 결과 Hash를 반환한다.
  주문은 실제 발주일·물리 납기·Calendar 입고 반영일·Source 정책과 Guard 조정 사유를 포함한다.
- `item_plans`는 선정 Candidate 포인터·선정/미선정 사유와 입장 증적을 보존한다.
  선정 품목은 `COMPUTED` 또는 `REVIEW_REQUIRED`, 미선정은 `NOT_SELECTED`다.
- 미선정 품목에는 대체 정책·권고 행동을 만들지 않는다. 해당 PSI는 Baseline이며
  `item_plans.psi_scenario_type=BASELINE`과 Row 자체로 구분한다. `psi_rows` 전체를 무조건 Recommended로 해석하지 않는다.
- 전 품목 미선정이면 `NO_POLICY_SELECTED`이며 주문·행동은 비어 있다. 정책을 선정했어도 재고가 충분하거나 Guard가 거절하면
  주문은 0건일 수 있다. 이는 성공 발주·서비스 목표 달성의 표시가 아니다.
- FSN BLOCK은 신규 보충을 만들지 않고 REVIEW는 계산·증적만 허용한다. 계산 결과의 자동 게시·자동 발주·정책 승인·DB Write와
  Claim·Artifact 봉인·Evidence 영속 상태는 모두 `false`다. 원 분류와 운영 Gate는 수정하지 않는다.

### 수작업 검증과 경계

[수작업 Golden](../../tests/fixtures/golden_selected_order_plan.json)은 3주·주간 수요 10EA에서
고정 수량/목표재고, MOQ·배수, 용량 감액·MOQ 미충족, 주중 납기, 2주 점검, LT=0의 8개 기대값을 직접 고정했다.
예를 들어 목표재고 23EA는 주문 13→10EA, EOH 0→3→3EA다.
MOQ 20·발주배수 6이면 원 수량 13EA를 24EA로 조정하고, 발주배수 6·용량 25이면 12EA로 제한한다.
낮은 서비스 하한을 쓰는 경계 사례는 Guard 검증용 개발 입력이며 운영 기본값이 아니다.

별도로 확정 입고 20EA·미확인 입고 100EA·Backorder 5EA·확정 고객주문 8EA를 함께 넣어
미확인 공급 제외·Forecast 소비 후 EOH 15→5→3EA, 두 번째 주 주문 8EA를 대조했다.
SDE P90=21일을 적용해도 물리 납기 7일은 유지한다. V1.1/V2와 두 품목의 상태·미도착 주문 분리도 검증한다.

- 신규 단위·Offline 통합 **51건**을 포함해 관련 회귀 **404 passed / 186 subtests passed**(25.78초)다.
  공통 PSI·수학 정책·기존 선정/탐색·Catalog·Runtime Gate·계약·CLI를 포함하며 전체 회귀 수치는 아니다.
- 재봉인한 주문·입고일·비용·실패/제외·Baseline·정책 모수·REVIEW 하향, Config·분류·입력 제어 치환을 차단했다.
  실제 재계산 결과나 조정 사유가 달라져도 원 주문을 복사해 성공시키지 않는다.
- Ruff·Format 373개 Python 파일, JSON/Schema 41개·Golden 8종·현재 두 문서의 링크 8건·Diff Check를 확인했다.
  시작 시 기존 파일 560개 중 승인된 문서 2개 외 558개는 동일하다. 기존 소스·Schema·Golden·개인 보고서를 수정하지 않았다.
- Coverage·Bandit은 사용한 Python 환경에 설치되지 않아 실행하지 않았다. 실제 DB·공용 Runtime·ERP와 독립 에이전트 검토도 수행하지 않았다.

아직 실제 V100/개발 DB Run·발주 계획 Writer·조회/복구·Platform API/화면·ERP는 검증하지 않았다.
대규모 실험·자원 관리 추가 고도화도 보류한다. 이 구현의 Source 재생은 실행 입장 검증이며 빠른 조회 경로가 아니다.

**다음 작업 — C# 재현 기준·목표 개선·비교 Golden 확정 · InventoryEngine**

- [대사 문서의 작업 순서](IO_LEGACY_CSHARP_PYTHON_RECONCILIATION.md#7-재정렬한-작업-순서)를 우선한다.
  발주 판단 시점·정책식·시간 매핑·서비스와 선정 단위를 먼저 구분하고 DB 없는 원본 비교를 준비한다.
- 기존 ID/Hash·현재 Python 검증 이력·FSN·운영 Gate는 유지한다. 실제 계산 차이가 생기면 기존 계산 Version을
  조용히 재사용하지 않는다. C# 실행 검증과 필요한 목표 규칙 정리 전에는 저장 연결을 먼저 확정하지 않는다.
- **보류 — 발주 계획 로컬 영속 저장·조회·복구.** 재개 시 기존 Artifact Writer·Reference/Hash/Receipt를 우선 재사용한다.
  동일 결과 멱등 처리·충돌·중단 복구 뒤 Platform Run/API/화면을 연결한다. DB 적용·E2E Write는 별도 승인 대상이다.
- 발주요청 확정·ERP 전송·Demand 연결은 이번 완료 범위가 아니다.
