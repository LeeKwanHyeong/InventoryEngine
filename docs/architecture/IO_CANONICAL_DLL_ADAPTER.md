# 정식 계산 DLL과 Canonical Adapter 구현

기준일: 2026-10-21 · InventoryEngine `inventory_engine_dev` · 기준 HEAD `99f2f12`.

후속 상태: [목표 전략·Guard·C# 전이 실행 연결](IO_TARGET_GUARDED_EXECUTION.md)도 Offline 구현·검증을 완료했다.
아래는 DLL·변환기 단계의 범위와 검증 이력이며, 현재 다음 작업은 저장·조회·복구다.
후속 연결은 기존 Python 기본값을 유지하는 선택적 수치 주입 방식이며 원문/목표 C# 계산문은 다시 변경하지 않았다.

## 완료 범위와 사용 경계

Python.NET으로 호출하는 `Inventory.Calculation.dll`과 명시적 Canonical Adapter를 추가했다.
기존 Python 계산·Canonical/Run 계약·ID·Hash·분류·FSN·운영 Gate는 교체하지 않았다.
실제 운영 Source 승인·Run Claim·Runtime Registry·게시·DB에는 연결하지 않았다.

| 경로 | 이번 구현 | 하지 않은 것 |
| --- | --- | --- |
| Legacy 원문 재현 | 지문 검증한 원본 `NativeKernel/SetBucket/Lot` + 범용 입출력 Facade | 원문 계산 수정, 원본 SQL 전체 입력 재현 주장 |
| Canonical의 Legacy 진단 투영 | Calendar·재고·순 Forecast/고객주문·포함 입고와 봉인된 별도 Legacy 인자 변환 | 목표 ROP/MOQ에서 원문 Lot/MOS를 추정, 누락을 0으로 대체 |
| 목표 Baseline | 별도 C# 정확 수량 전이로 전체 품목·주차 계산; 기존 PSI Row/Hash 형식 유지 | 내부 자동 발주, 새 공급 생성 |
| 목표 Recommended | 외부의 검증된 입고를 받을 수 있는 수치 전이 Primitive | 전략 제안→공통 Guard→허용 행동 등록 연결, 운영 승격 |

기존 [계산 경계 계약](IO_CALCULATION_BOUNDARY_CONTRACT.md)은 유지한다.
현재 `TARGET_GUARDED + CSHARP_PYTHONNET`은 **Baseline과 개별 상태 전이만** 검증됐다.
Recommended 선언을 Baseline 실행에 넣으면 거부하며, Guard 없이 Legacy 주문을 목표 권고로 바꾸는 API는 없다.

## 코드와 호출 책임

- [DLL 프로젝트](../../native/Inventory.Calculation/Inventory.Calculation.csproj): .NET 8 Release 라이브러리.
  범용 `RunLegacy`와 별도 `AdvanceTarget` 진입점이 있다. DB·라이선스 Main·전체 Optimizer는 포함하지 않는다.
- [Python.NET 전송기](../../src/dsio_inventory_engine/infrastructure/calculation_pythonnet.py): Build Hash와 런타임 설정을 확인하고
  UTF-8 JSON을 한 번에 복사한다. CLR 지연 로딩이며 다른 Build가 이미 같은 Assembly 이름으로 로드됐다면 거부한다.
- [Canonical Adapter](../../src/dsio_inventory_engine/prepare_inventory/application/calculation_adapter.py): 기존 입력 검증과
  `validate_prepared_projection`을 먼저 수행하고 Run·8종 Snapshot·Prepared·DLL Build·별도 인자 참조를 대조한다.
  결과의 품목·후보·주차 전체 순서/건수·수량·발주/도착일을 확인한다.
- [빌드 준비기](../../scripts/prepare_calculation_dll.py): 원본 7개 파일과 21개 구간을 기존 추출기로 검증한 뒤 새 외부 폴더에 준비한다.
  [기존 추출기](../../tests/reference/legacy_csharp/prepare_legacy_reference.py)와 원문 계산·실험 Adapter는 바꾸지 않는다.

`CanonicalCalculationAdapter.run_legacy()`는 진단 전용이다.
`run_target_baseline()`은 기존 Baseline과 같은 `psi_rows/psi_content_hash`를 반환하지만 운영 게시 권한을 부여하지 않는다.
`AdvanceTarget` 자체는 수치 Primitive이며 호출자가 넣은 `recommended_receipt_qty`의 승인 여부를 판정하지 않는다.
따라서 Primitive 직접 호출 결과를 자동 발주·승인 결과로 쓰면 안 된다. 그 경계의 실제 연결은 다음 작업이다.

## Canonical 변환 규칙

### Calendar와 수요

Calendar의 `yyyyww/seq/start_date/end_date/base_month`를 그대로 전달한다.
16품목·32후보·52주 실험 제한과 2026/2027년 고정 정책, 시작일에서 BaseMonth를 재생성하던 실험용 가정을 제거했다.
전송 한도는 입력 8MB·결과 128MB·전체 품목×후보×주차 200,000행, Calendar 1~520행이다.
이 한도는 전역 자원 관리 고도화나 실제 대용량 성능 합격을 뜻하지 않는다.

Legacy 진단에서는 `net_forecast_qty + confirmed_customer_order_qty`를 주차별 단일 Demand로 투영한다.
Forecast Consumption은 이미 기존 Prepare에서 끝난 값이다. 원 Forecast·고객주문·소비 근거는 기존 Prepared에 남기며,
이 투영으로 원문 SQL과 완전히 같은 입력이라고 주장하지 않는다.
목표 계산은 두 수요를 분리하고 고객주문 부족만 Backorder로 이월한다.

### 재고와 공급

- 원문 BOH에는 `available_qty`를 사용한다. Reserved를 다시 가용재고에 더하지 않는다.
  Reserved·On-hand·Source·마감 근거는 기존 Prepared에 보존하며 목표 전이에도 Reserved를 별도로 유지한다.
- 동일 주차의 포함 확정 공급을 먼저 합산해 하나의 DueIn으로 보낸다.
  원문 `SetBucket`의 첫 일치 행 선택 때문에 두 번째 입고가 사라지는 일을 방지한다.
- Legacy 시작 `AccPO`는 이번 투영에 포함된 확정 입고 합계다. 초기값을 임의 0으로 두지 않으며,
  주차 입고 시 원문이 차감하는 수량과 일치시킨다.
- `ORDERED/UNVERIFIED_DUE_IN`·취소·이미 BOH에 반영된 입고·기간 밖 공급은 기존 판정대로 제외한다.
  제외 수량·날짜·사유는 `receipt_decisions`에 남는다. 과거 Legacy 가정 공급을 목표 입력에 가산하지 않는다.
- 공급 합계는 한 번 Index를 만들어 재사용한다. 후보마다 원본 입고 전체를 다시 검색하지 않는다.

### 별도 Legacy 인자 Artifact

기존 `legacy_parameters_binding.reference/content_hash`가 가리키는 Artifact 본문은 다음 닫힌 구조다.
새 실행 ID·Snapshot ID·별도 Binding Hash는 만들지 않는다.

```json
{
  "canonical_input_hash": "기존 Canonical Hash",
  "prepared_content_hash": "기존 Prepared Hash",
  "source_type": "LEGACY_SOURCE 또는 SYNTHETIC_DEVELOPMENT",
  "rows": [{
    "item_id": "기존 품목 키",
    "candidate_id": "기존 후보 키",
    "stock_policy": "OUTL",
    "internal_lead_time": "201600",
    "lot": {"minimum": "0", "maximum": "1000000", "step": "1"},
    "policies": [{"start": "2026-01-01", "end": "2026-12-31",
      "rop": "10", "target": "23", "mos": "1", "ma_demand": "10"}]
  }]
}
```

위 수치는 구조 설명용 개발 예시이며 기본 정책이 아니다. 사용자가 Hash를 입력하는 화면도 만들지 않는다.
실제 Producer·Catalog의 승인 원본을 이 인자 구조로 만드는 연결은 후속이다.

품목별로 후보 한 개 이상이 있어야 하고 같은 품목·후보 중복과 고아 품목은 거부한다.
기대 후보 목록 전체성은 이 Artifact의 봉인 목록을 기준으로 한다. 외부 Catalog와의 전체 후보 대사는 다음 실행 연결 단계다.
모든 Calendar BaseMonth에 적용되는 원문 정책이 있어야 한다. 원문 정책 순서와 양끝 포함 기간은 유지하며
기간이 겹치면 원문대로 첫 일치를 사용한다. 목표 정책의 `[from,to)`를 이 기간으로 자동 변환하지 않는다.

`internal_lead_time`은 원문 단위로 명시한 값이다. `/28800` 절삭을 그대로 사용한다.
Canonical `lead_time_days`·SDE 보정·원문 LT를 자동 치환하지 않는다.
Lot Min/Max/Step도 원문 인자가 필요하며, MOQ·배수·물리 최대재고를 이름만으로 대입하지 않는다.
`SYNTHETIC_DEVELOPMENT`는 개발 범위에서만 허용하고 운영 권위 자료라고 표시하지 않는다.

## 정밀도와 결과 검증

Legacy는 원문 binary64와 Lot의 중간 decimal 경로를 유지한다.
`0.1`의 binary64 의미는 Legacy 재현에서 의도된 것이지만, `9007199254740993`처럼
문자열→binary64→최단 십진 문자열 과정에서 값이 바뀌는 입력과 비유한 값은 거부한다.
중간 Lot 계산의 .NET 범위 초과도 실패하며 Python으로 자동 대체하지 않는다.
기존 C# 캡처와는 허용 오차가 아니라 `float.hex()` 수준의 값·날짜·순서를 대조한다.

목표 전이는 `double`이나 .NET decimal의 28~29자리 제한을 사용하지 않는다.
소수 6자리 고정 Scale의 `BigInteger`로 덧셈·차감·Min을 수행한다.
지원 범위는 소수 6자리 이하, 정수부 34자리 이하이고 **모든 중간 합계**도 이 범위에 있어야 한다.
기존 Python의 40자리 Decimal 문맥 안에서 정확히 표현되는 부분집합이다. 더 큰 값은 임의 반올림하지 않고 실패한다.
기존 Python의 지원 범위를 줄이거나 기존 입력 계약을 바꾼 것은 아니다. 새 DLL 경로의 제한이다.

목표 결과는 입력 Echo·Reserved·고객주문/재고 보존·BO→당주 고객주문→순 Forecast 할당 순서를 검증한다.
Legacy `AccPO`가 음수가 되는 원문 특성은 진단으로 보존하며 이를 목표 공급 Ledger로 재명명하지 않는다.
결과 형식/건수/순서/기존 참조 검증은 실제 재계산 동등성 검사와 구분한다.

## 검증과 남은 작업

실제 DLL 검증은 [전용 실행기](../../scripts/verify_calculation_dll.py)로 기존 로컬 격리 이미지에서 수행했다.
네트워크·DB·Secret·Docker Socket은 컨테이너에 제공하지 않았고 저장소·DLL은 읽기 전용 Mount했다.
검증 환경은 Linux Arm64 컨테이너다. macOS/Windows 네이티브 CLR 환경의 호환성을 이번 결과로 주장하지 않는다.
원문 nullable 경고 CS8632 한 건은 보존했으며 Release 빌드 오류는 없다.

- 실제 기존 C# 캡처: 27사례·81행의 14개 수치·주차·발주/도착일 일치.
- Canonical→Legacy: 4개 정책 코드×4개 LT·33개 후보와 별도 BaseMonth/기간·17품목을 포함한 1,638행 대조.
- Canonical→목표 Baseline: V1/V2/V2.1, 분수 수요, Reserved/BO·복수/미확인 입고·17품목의 72행과 기존 결과 Hash 일치.
- 별도 목표 상태 전이: 125입력의 실제 C# 결과와 기존 Python 결과 완전 일치.
  30자리 정수부·소수 6자리 예시를 포함한다. 학습이나 성능 본실험은 아니다.
- 추가 1/53주 가변 기간, 15개 실제 오류·취소 거부, 취소 후 같은 결과를 확인했다.
- 신규 Offline 단위 테스트와 관련 회귀·정적 검사 결과는 [검증 기록](evidence/canonical-dll-adapter-verification-20261021.json)에 남긴다.

실험 이미지와 이전 속도 수치를 이 새 JSON Adapter의 성능으로 인용하지 않는다.
목표 Baseline은 현재 주차마다 DLL 경계를 호출한다. 다음 Recommended 연결에서 공통 Guard의 순서를 먼저 검증하고,
별도 승인된 성능 비교가 필요하면 그때 같은 입력으로 측정한다. 추가 자원 관리 고도화는 계속 보류한다.

**1. 목표 보충 전략·공통 Guard 실행 연결 — 후속 Offline 완료 · InventoryEngine**

- 기존 전략의 제안→Guard→허용 행동→공급 Ledger→목표 전이를 연결한다.
  실제 후보 목록·선정 정책·발주 계획을 같은 입력·Profile·Build로 실행하고 이후 관측에 반영되는지 검증한다.
  계약·DLL 준비가 완료됐으므로 이 공통 실행 연결은 직렬로 진행한다.

**2. Artifact 저장·조회·중단 복구 — 현재 다음 작업 · InventoryEngine**

- 기존 Writer/Reader/Verifier와 ID·Hash를 재사용해 독립 IO를 검증한다.
  그 후 Platform/Demand 연결 테스트로 넘어간다. 실제 DB Write·Migration·공용 배포는 별도 승인 대상이다.
