# 목표 전략·공통 Guard·C# 상태 전이 Offline 연결

기준일: 2026-10-21 · InventoryEngine `inventory_engine_dev` · 기준 HEAD `99f2f12`.

**완료 — 계산 전용 전략 실행·후보 평가·선정·발주 계획 연결. Runtime 등록·DB 연결은 미완료.**

후속 상태(2026-10-21): [계산 Binding·전체 결과의 저장·조회·복구](IO_TARGET_PIPELINE_STORAGE.md)를
별도 개발용 진입점으로 연결하고 Offline 검증했다. 아래 `RunTargetPolicyPipelineUseCase` 자체는 계속 계산 View를 반환한다.
새 저장기가 이 View와 원본 입력을 함께 보존하며, 기존 계산 진입점·ID/Hash·운영 Gate는 바꾸지 않았다.

## 1. 기존 계산을 유지한 연결 방식

[정식 DLL·Canonical Adapter](IO_CANONICAL_DLL_ADAPTER.md)의 별도 목표 전이를 공통 실행부에 연결했다.
Legacy 전체 기간 계산 후 주문표를 수정하거나, 기존 Python 결과를 C# 결과로 표시하는 방식은 쓰지 않는다.
Python 실행 Loop를 복제하면 Guard·Ledger·Gate가 서로 달라질 수 있어, **기존 실행부에 명시적 수치 전이 주입 지점만 추가**했다.
기본값은 기존 Python `advance_bucket`이며 기존 호출·CLI·Runtime은 계속 그 기본값을 사용한다.
Python 계산식, 정책식, 후보 생성·평가·선정 규칙, Guard의 제약·기간 처리와 기존 Hash 계산식은 바꾸지 않았다.

[target_execution.py](../../src/dsio_inventory_engine/recommend_replenishment/application/target_execution.py)의 명시적 개발용 진입점은 다음과 같다.

| 진입점 | 역할 | 사용하지 않는 것 |
| --- | --- | --- |
| `RunTargetRecommendedPsiUseCase` | 주어진 봉인 전략 제안→공통 Guard→C# 목표 전이 | 임의 최신 모델 탐색·기존 주문표 재사용 |
| `RunTargetMathematicalReplenishmentUseCase` | 기존 실제 Mathematical 전략·정책 Report를 그대로 사용 | 새로운 수학적 정책 계산식 |
| `RunTargetPolicyPipelineUseCase` | 기존 후보 생성→C# Simulation/평가→선정→선정 정책 재실행 | Python으로 선정한 뒤 C# 결과만 덧붙이는 방식 |

허용 범위는 `DEVELOPMENT + LOCAL_SHADOW + CALCULATION_ONLY + TARGET_GUARDED`다.
기존 계약의 LOCAL_SHADOW/SHADOW 제약도 그대로 검증한다. Profile이 Legacy이거나 Baseline 선언을 넣으면 시작 전에 거부한다.
기존 ML/PPO의 학습·승격·운영 승인·발주는 활성화하지 않았으며 실제 학습 모델 비교도 이번 범위가 아니다.

## 2. 제안에서 다음 관측까지의 실제 순서

```text
Canonical·정확한 Prepared 투영·기존 입장 검증
    ↓
기존 Effective Policy·FSN·모델 계약 확인
    ↓ 주차/품목별 반복
현재 재고 + 기존/권고 미도착 공급을 관측
    ↓
봉인 전략의 원 행동 제안
    ↓
기존 no-order Gate 위반 제안 차단 + 공통 Guard
    ↓
허용 수량·발주일·도착 Bucket만 기존 공급 Ledger에 등록
    ↓
당주 확정 입고 + 당주 허용 권고 입고를 C# 목표 전이에 전달
    ↓
과거 고객 BO → 당주 고객주문 → 순 Forecast 차감
    ↓
EOH·고객 BO·Forecast Shortage → 다음 주차 관측
```

Ledger·발주일·도착일·MOQ·배수·용량·승인 제약은 기존 공통 Python 실행과 Guard가 소유한다.
C#은 허용된 공급을 입력으로 받아 목표 수량 전이만 한다. DLL에서 주문·승인·정책을 새로 결정하지 않는다.
원 행동, Guard의 조정·거부 사유, 허용 행동과 주차 PSI를 기존 필드로 남긴다.

Guard가 137개 제안을 150개 또는 50개로 바꾸면 그 **허용 수량만** 상태에 들어간다.
검증 사례에서는 0 LT·MOQ/배수 50·용량 220 조건에서 권고 입고가 `150, 50, 0`이고 다음 관측의 가용재고는 `140`이었다.
MOQ가 100이면 용량에 맞추려고 50개를 발주하지 않으며 `150, 0, 0`이 된다.
장기 LT의 허용 주문은 미도착 공급에 남아 다음 `ORDER_UP_TO` 제안의 중복 발주를 막는다.

`REVIEW`는 계산·근거 보존을 허용하되 자동 게시·발주는 금지한다. `BLOCK`의 후보 전략은 HOLD를 유지한다.
명시적인 no-order 입장을 무시하는 전략도 공급 Ledger 등록 전에 거부한다.
기존 no-order 기준을 새로 바꾼 것이 아니라 새 수치 경계에서도 그 입장을 강제한 것이다.

DLL 오류·비지원 정밀도·결과 보존/할당 순서 불일치는 실패한다. Python으로 자동 대체하지 않는다.
실패한 호출의 부분 상태를 다음 후보에 넘기지 않는다. 실제 DB·게시가 없는 로컬 계산이며 부분 결과를 저장하지 않는다.

## 3. 동일 입력·Profile·수치 Build의 경계

기존 `CalculationBoundaryBinding / 1.0.0`을 사용한다. 새 실행 ID·Snapshot ID·결과 묶음 Hash는 추가하지 않았다.
부모 입력의 Run·Canonical Hash·8종 Snapshot·정확한 Prepared·설정·전략 입력과 설치 DLL Hash를 먼저 검증한다.
후보 실행에서는 기존 PSC/Catalog 입력 Binding과 후보 실행 Hash를 그대로 사용한다.
Baseline·후보·선정 검증·발주 계획 재계산은 모두 `inventory.psi.target_guarded.csharp / 1.0.0`과 같은 DLL Hash를 사용한다.

- 공통 공급/재고 Loop의 전이만 교체한다. 기존 Python 주차 전이를 호출하는 숨은 fallback은 없다.
- 후보의 서비스·비용 평가와 선정 검증도 실제 C# 상태 전이로 다시 확인한다.
- 최종 선정 정책은 새 전략 호출·Guard·C# 전이로 다시 실행한다. 저장된 후보 주문표를 복사하지 않는다.
- Build·Profile·입력·설정이 다르면 이전 선정을 자동 재사용하지 않는다.
- DLL Hash는 수치 라이브러리의 Build 근거다. Python 전략·Guard 전체의 배포 Build 서명이나 Runtime 등록을 대신하지 않는다.

추가된 반환 객체는 **아직 영속 Artifact가 아닌 로컬 계산 View**다.
`calculation_binding`, 단계별 `execution_bindings`, `completed_transition_calls`와
기존 `search_result/selection_result/order_plan`을 함께 반환한다.
중첩된 기존 결과의 ID·Hash는 수치/내용이 같을 때 그대로이며, 호출 수단의 근거는 바깥 Binding으로 별도 보존한다.
이 Binding을 버리고 중첩 결과만 C# 증적으로 게시해서는 안 된다. 기존 Run Claim·Result Bundle에는 아직 등록하지 않았다.

`verify_result()`는 임의 자체 Hash를 신뢰하지 않고 같은 원본 입력·Config·분류·Catalog·Build에서 전체 단계를 재계산한다.
실패/제외 후보·비용·선정·발주 계획·권한·Build/Profile을 바꾸면 거부한다.
이 검증은 빠른 조회가 아닌 전체 재계산 검증이며 현재 속도 최적화 완료를 의미하지 않는다.

## 4. 실제 검증과 한계

[실제 C# 검증 실행기](../../scripts/verify_target_execution.py)를 기존 고정 Python.NET 이미지의 Linux Arm64 컨테이너에서 실행했다.
네트워크는 껐고 저장소·DLL은 읽기 전용으로 제공했다. DB·Secret·Docker Socket은 제공하지 않았다.
DLL 재빌드나 의존성 재설치는 하지 않았다. 사용한 수치 DLL Hash는
`9bc7b1576eb3bcd00d11fb28affb77f7b875764667f120ca54ac98faa944fe7f`다.

- 기본 ABC 후보 경로와 Catalog의 ABC·XYZ 32개·LEGACY_STYLE 32개·참조 88개 후보를 모두 실행했다.
- 기존 발주 계획 Golden 8개 사례의 주문수량·발주일·도착일·EOH·조정 사유를 확인했다.
- FSN REVIEW/BLOCK의 기존 계약과 V2를 검증했다. BLOCK은 선정/주문이 없고 REVIEW 주문은 승인 필요 상태를 유지했다.
- SDE 보호기간 21일과 물리 LT 7일을 분리했고, 2품목 상태/정책/공급을 각각 유지했다.
- 실제 Mathematical 전략과 Guard 조정 후 다음 관측을 검증했다.
- Build·Profile·권한·비용·선정 정책·주문 변조 6개를 전체 재계산으로 거부했다.
- **26개 확인 항목·18개 Pipeline에서 실제 C# 전이 13,986회**를 호출했다.
  재검증 호출을 포함한 횟수이며 고유 PSI Row 수나 성능 지표가 아니다.
- 신규 Unit **28 passed**, 이를 포함한 관련 회귀 **736 passed / 158 subtests passed**(31.14초).
  Python 404파일 Ruff·Format을 확인했다. Unit의 대역 Port 검증과 실제 DLL 검증은 구분한다.

지원 범위 내에서 Python과 C#의 후보 평가·선정·발주 계획 전체 내용과 기존 결과 Hash가 같았다.
이것은 Legacy 재현 Profile과 목표 Profile이 같다는 뜻이 아니다.
장기 운영 자료·V100 실제 Run·모델 성능·속도/메모리·macOS/Windows 네이티브 CLR·전체 저장소 회귀를 검증한 것도 아니다.
자세한 Source 지문·파일 보존·실제 실행 결과는 [검증 증적](evidence/target-guarded-execution-verification-20261021.json)을 따른다.

## 5. 후속 상태와 다음 순서

**1. 계산 결과의 저장·조회·중단 복구 — 후속 완료 · InventoryEngine**

- 기존 Writer/Reader/Verifier와 ID·Hash를 재사용하면서 바깥 계산 Binding까지 함께 보존한다.
  동일 입력·Profile·Build와 전체 후보·선정·발주 계획의 누락/멱등/복구를 Offline으로 검증한다.
  후속 결과는 [저장 계약](IO_TARGET_PIPELINE_STORAGE.md)을 따른다. 전략·Guard 연결을 다시 구현할 필요는 없다.

**2. 독립 IO 결과 계약 대사 후 Platform·Demand 연결 — 다음 작업**

- 저장 결과를 기존 Result Bundle/Projection과 Offline 대사한 뒤 Platform Run과 정확한 Demand Snapshot을 연결한다.
  실제 DB Write·Migration·공용 Runtime 배포·실제 E2E Write는 별도 승인 대상이다.
  실제 발주요청 확정·ERP 전송·자원 관리 추가 고도화는 계속 후속/보류 상태다.
