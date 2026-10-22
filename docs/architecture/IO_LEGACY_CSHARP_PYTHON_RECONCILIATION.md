# Legacy C#와 Python의 PSI·발주·정책 선정 대사

기준일: 원본 대사 2026-10-20, 재현 규칙·Golden 확정 2026-10-21 · 대상: InventoryEngine `inventory_engine_dev`, HEAD
`99f2f1286d90dfe0e26025e603786a813ea51dac`와 기존 미커밋 코드.

**상태: C# 원문 추출의 실제 참조 실행과 대응 Golden 20건 검증 완료. 전체 Legacy 엔진·배포본 동등성은 미검증.**
원본 대사에서는 문서만 변경했다. 2026-10-21에는 문서·새 Golden Fixture·테스트만 추가/정정했다.
기존 개선형 계산 코드·기존 ID/Hash·ABC/XYZ 결과·FSN 설정·운영 Gate·DB·Runtime은 변경하지 않는다.
이후 [별도 Python 재현 기준](IO_LEGACY_PYTHON_REFERENCE_CONTRACT.md)을 추가해 원문 계산 92회를 대조했다.
기존 개선형과의 차이는 그대로이며 새 경로도 운영 전략·전체 Legacy 엔진은 아니다. 성능은 미측정이다.
이 문서는 과거 문서의 ‘C# 본문 미확보’를 정정한다. 과거 Python 검증 이력을 취소하거나 Legacy 동등성 검증으로 승격하지 않는다.

### 2026-10-21 실제 C# 실행·동일 입력 비교 완료

- [DB 없는 참조기](../../tests/reference/legacy_csharp/README.md)로 원문 7파일·21개 구간을 그대로 추출해 컴파일/실행했다.
  계산식 재작성·원본 변경·Main/DB 전후처리·라이선스 진입점 호출은 하지 않았다.
- C# 대응 20개 사례를 네 정책 코드 변형 포함 29회 실행했고 수작업 Golden 불일치는 0건이다.
  .NET 8.0.32 / SDK 8.0.426, 공식 이미지의 네트워크 차단·읽기 전용 실행이며 반복 JSON 결과도 같았다.
- Python 21개 사례와 나란히 비교했다. 공통 비교 필드 일치 6·확인된 의미 차이 12·선정 적격 비교 2·비용 계약 부재 1건이다.
  전체 Optimizer Winner나 운영 성과·비용 우열을 확인한 수치는 아니다.
- [실제 출력](../../tests/fixtures/native_legacy_reference_output.json)·
  [필드별 비교](evidence/legacy-native-python-comparison-20261021.json)·
  [실행 기록](evidence/legacy-native-execution-verification-20261021.json)을 보존했다.
- 현행 Python 기초 발주·Forecast 미이월·Guard·FSN/운영 Gate는 변경하지 않았다.
  남은 작업은 확인한 차이의 목표 적용 범위와 서비스 집계/그룹→품목 전개의 확인이다.

### 2026-10-21 수작업 고정 시점의 이력

- [재현·개선 유지 계약](IO_LEGACY_REPLAY_GOLDEN_CONTRACT.md)과
  [수작업 Golden 21건](../../tests/fixtures/golden_legacy_python_semantics.json)을 고정했다.
  C# 값은 원문 수작업 기대값이지 실제 C# 실행 결과가 아니다.
- 현재 Python 계산과 FSN·운영 Gate는 그대로 둔다. 신규 검증 39건, 기존 관련 회귀를 포함해
  **362 passed / 124 subtests passed**(22.00초)이며 전체 회귀·C# 동등성 시험 수치가 아니다.
- 개별 적격의 강제 표시와 최종 선택을 구분했다. C# 최종 Winner·SVC_LV 생성식·그룹→품목 전개는 임의로 채우지 않는다.
- 다음 작업은 아래 Golden을 C#의 DB 없는 최소 참조 실행으로 확인하는 것이다. 저장 연결은 계속 보류한다.

## 1. 결론과 확인 범위

현재 Python은 **Legacy 업무 흐름을 계승한 독립 재구현**이며, 확보한 C#과 계산 방식이 다른 부분이 확인됐다.
차이는 단순한 언어·속도 문제가 아니라 발주 판단 시점, 부족 수요 이월, Lot 수량, 납기 주차, 정책 선정 목적에 영향을 준다.
따라서 ‘C#을 그대로 이식했다’거나 동일 입력이면 같은 발주 계획이 나온다고 표현하지 않는다.

- **확인 사실:** 아래의 실제 호출 경로·조건식·대입식·출력 컬럼. `SOURCE_CONFIRMED`로 구분한다.
- **현재 Python 사실:** 로컬 코드에서 확인한 동작. 기존 개발 계약이며 C# 재현을 뜻하지 않는다.
- **재현 기준:** 원문 추출 참조기에서 작은 범위를 실행했다. 제품용 재현 전략/운영 모드를 구현한 것은 아니다.
- **개선 유지:** 기존에 합의한 Python 입력 무결성·수요 구분·제약·Gate를 유지할 내용이다.
- **판단 필요:** 업무 결과를 바꾸는 선택. 이 대사만으로 운영 규칙을 변경하지 않는다.
- **실행 미검증:** 원본 전체 엔진·실제 배포본·미확인 집계/선정 흐름은 아직 확인하지 않았다. 참조 구간 실행과 구분한다.

### 원본과 실행 경로

원본 루트는 `/Users/igwanhyeong/NSPM 2/SupplyEngine/DSIO_postgresql_20250908/RootIO`다.
같은 위치의 `DSIO_postgresql_20250908.zip`과 핵심 4개 파일의 바이트가 일치한다.
이 일치는 **제공 ZIP과 추출본의 일치**이지, 실제 운영 서버 배포본과의 일치가 아니다.
경로·SHA-256·근거 위치는 [원본 대사 기록](evidence/legacy-csharp-reconciliation-20261020.json)에 보존한다.

| 구간 | 확보한 원본과 실제 호출 경로 | 확인 경계 |
|---|---|---|
| PSI | `RootIO/Program.cs`의 `#if PSI → GetDataSet → ExecutePSI` | `RootIO.csproj`는 net8.0, AssemblyVersion `1.0.0.52`, Debug/Release에 `PSI;PROD` |
| 입력 준비 | `Program.cs:149–299`, `setBufferPSIObject:669–718`, `BufferPSIObject.SetBucket` | 전처리 호출·Inbound Calendar·수요 Buffer 선택·BOH/BO/PO·ROP/MAX 수신 확인 |
| 주문·재고 | `Program.cs:771–963`, `PlanEntity.Lot.GetSize`, `BufferPSIObject.SetDuein` | 실제 PSI 본문과 Lot·도착 주차 대입 확인 |
| 최적화 | `#elif OPTIMIZER → GetDataSetOptimizer → ExecuteOptimizer → OptimizerProcess.Run` | 별도 컴파일 진입점이다. 기본 PSI 빌드에서 함께 실행된다고 해석하지 않음 |
| 선정 | `OptimizerProcess.OptimizerStep1/2` | Step1은 Segment×Scenario 집계를 소비하고 Step2는 최적화 함수 확인 결과를 생성 |
| PostgreSQL 출력 | `ResultMart`의 PostgreSQL 분기 → `ExportPostgreSQL.insertPSIResultPostgre` | `TB_PLN_SIMUL_RST`에 PSI·제안 PO 저장. ERP 발주 확정 증거는 아님 |

`ExecuteOptimization`과 `OptimizationProcess` 계열 파일이 존재한다는 사실만으로 활성 알고리즘으로 채택하지 않는다.
이번에 추적한 호출은 **`ExecuteOptimizer → OptimizerProcess`**다.
Step1이 호출하는 함수도 `SelectGroupDmRateTop`이며 `_All` 변형이 아니다.

원본 `ExecutePSI:790–791`에는 `BA59-04709A@V101` 외 품목을 반환시키는 활성 테스트 필터가 남아 있다.
이를 업무 규칙으로 복사하거나 필터를 제거해 원본 프로그램을 실행하지 않았다.
원래 진입점은 라이선스 확인·DB 전처리·후처리·결과 적재를 포함하므로 무단 실행 대상이 아니다.
최초 대사 시 Mac에는 `dotnet`이 없었다. 이후 공식 SDK 컨테이너로 계산 구간만 격리 실행했다.
원본 전체 프로그램이나 DB·라이선스 진입점을 실행한 것은 아니다.

## 2. C#의 실제 발주·재고 계산

아래는 `ExecutePSI:807–953`의 순서 요약이다. 동일 실행 결과를 재현한 테스트가 아니라 원문을 읽어 정리한 것이다.

```text
당주 BOH = 전주 EOH
당주 누적 PO = 전주 누적 PO − 당주 DueIn
당주 가용량 = BOH + DueIn
    ↓
과거 누적 BO부터 충족
    ↓
당주 Demand 충족, 부족량은 누적 BO에 추가
    ↓
Position_after = EOH + 누적 PO − 누적 BO
Position_after < ROP이면
    주문 = Lot.GetSize(OUTL − Position_after)
    납기 = 해당 Bucket.StartDate + int(StockLeadtime / 28800)일
    납기를 포함하는 Bucket의 DueIn에 추가
    ↓
LT=0 보정: 당주 재고에 가산하고 BO를 다시 충족
    ↓
EOH·누적 BO·누적 PO 이월, 할당률·예상 MOS 산출
```

**핵심 정정:** `OUTL/ROPQ/MINQ/SFST` 모두 위 발주 조건·수량식을 공유한다.
확보한 본문의 `switch(StockPolicy):906–943`는 **ExpMOS 계산 분기**다.
따라서 `ROPQ`라는 이름만 보고 현재 Python의 고정 Q 정책에 일대일 대응시키면 안 된다.
상위 SQL이 정책별로 ROP/MAX를 다르게 만들 수는 있지만, 그 사실이 C# 내부에 네 개의 별도 주문 알고리즘이 있다는 뜻은 아니다.

C#은 `SAFETY_STOCK.ROP/MAX_LV/MOS/MA_DMD_QTY`를 읽는다. 이 PSI 본문에서 안전재고·ROP·EOQ를 새로 최적 산출하지 않는다.
별도로 확보한 Rop Python/R의 수요 분포 계산을 C# 내부 계산이라고 합쳐 설명하지 않는다.

## 3. 현재 Python과의 차이 및 처리 방향

Python 근거:
[주차 실행](../../src/dsio_inventory_engine/recommend_replenishment/application/run.py),
[공통 PSI](../../src/dsio_inventory_engine/simulate_inventory/application/step.py),
[후보 전략·평가](../../src/dsio_inventory_engine/recommend_replenishment/application/policy_search.py),
[Guard](../../src/dsio_inventory_engine/recommend_replenishment/application/guard.py),
[정책 선정](../../src/dsio_inventory_engine/recommend_replenishment/application/policy_selection.py).

| 항목 | 확보한 C# | 현재 Python | 권장 분류·남은 확인 |
|---|---|---|---|
| 재고 이월 | 전주 EOH→BOH, 입고→과거 BO→당주 Demand | 같은 재고 흐름에 확정 고객주문·순 Forecast 구분 추가 | **공통 불변식 재사용.** 같은 수치라는 의미는 아님 |
| 발주 판단 시점 | 당주 입고·수요 차감 **후** EOH 기준. 납기 기준일은 Bucket 시작일 | 주차 시작, 입고·수요 차감 **전** 관측·발주 | **재현 기준 + 판단 필요.** 결과 차이의 첫 비교 대상. Python을 조용히 기말 판단으로 바꾸지 않음 |
| Trigger·정책 종류 | 공통 `Position_after < ROP`, 부족분을 OUTL까지 보충 | `ROP_FIXED_Q`: 점검일 `Position <= ROP`에 Q. `PERIODIC_ORDER_UP_TO`: 점검일 `Position < S`에 S까지 | **재현 기준.** 원본 공통 `(s,S)` 형태와 기존 두 정책을 분리. 정책명만 치환하지 않음 |
| 현재 수요 미충족 | 당주 Demand 부족분 전체를 누적 BO로 이월 | 고객주문 BO만 이월. Forecast Shortage는 별도 보존·미이월 | **의도한 개선 유지.** 원본 비교에서는 Demand 종류를 고정하고 차이를 명시 |
| 확정 주문·Forecast | PSI는 단일 `Demand`를 소비 | 동일 주차 Forecast Consumption 후 고객주문·순 Forecast 차감 | **의도한 개선 유지.** Legacy SALES_ORDER 구성 원문과 종류 매핑은 별도 확인 |
| 입고 신뢰성 | 선택된 STOCK 목록을 DueIn으로 수신 | CONFIRMED/IN_TRANSIT 포함, 미확약은 제외 증적 | **의도한 개선 유지.** C# 수신을 벤더 확약 검증으로 간주하지 않음 |
| 기초재고 누락 | 첫 과거 STOCK, 없으면 0 생성 (`InboundClass:272–289`) | 봉인 Position·Scope·Cut-off·Watermark·연속성 검증 | **개선 유지.** 누락과 실제 0재고를 구분 |
| 같은 Bucket 복수 수요·입고 | `SetBucket`은 각각 첫 일치 행만 사용 | Canonical Grain·전체 수량 검증 후 해당 주차 합산 | **재현 특성 + 개선 유지.** C# 주석은 수요 사전 총량을 가정하지만 Query.xml은 직접 조회·정렬이고 GROUP BY 없음. 상위 생성 SQL의 유일성은 미확인 |
| 대상 품목 | SALES_ORDER에 있는 Buffer 키 | 승인된 재고관리 Master·명시적 Forecast 범위 | **개선 유지.** 원본 테스트 단일 품목 필터는 운영 기능으로 복사하지 않음 |
| 월별 정책 | Calendar `BaseMonth`가 정책 기간 안에 있는 첫 행, 양끝 포함 | 판단 주차 시작일 기준 정확히 한 정책, 종료일 제외 | **재현 기준 + 판단 필요.** 월 경계·중첩·누락 Golden 필요 |
| BaseMonth 누락 | 실제 C#은 시작일 월의 1일 사용. 주석의 ‘수요일’과 다름 | 공유 Calendar/명시적 정책 기간 | **주석 아닌 코드로 재현.** 기존 Calendar SQL의 수요일 기준과 C# fallback도 분리 |
| ROP·목표량 산출 | Inbound ROP·MAX_LV 수신 | 과거 μ·σ·CSL·보호기간 + RMOS/EMOS 개발 정의 | **개선/개발 가정.** RMOS·EMOS의 원본 생성식 확인 없이 동일 EOQ라고 하지 않음 |
| Lead Time 단위 | BOR MOVE_TIME을 `int(value/28800)`으로 일수 절삭 | 명시한 정수 `lead_time_days`, SDE는 보호기간만 보정 | **재현 기준 + 단위 확인 필요.** 제공 BOR SQL은 `FN_GET_SEC_TIME_UOM` 사용; 함수 원문 없이 하루 환산 동등성 추정 금지 |
| 주중 도착 | 납기를 포함하는 Bucket에 넣음 | 납기 이상인 첫 Bucket 시작일로 올림 | **재현 기준 + 기존 개선 유지.** 현재 Bucket에 사후 기입되는 단기 LT 위험은 실제 재생으로 확인 |
| LT=0 | 당주 수요 처리 뒤 주문·재고 가산·BO 재충족 | 주문 가산 후 당주 수요 처리 | **재현 기준.** EOH뿐 아니라 DemandAllocation·Fill Rate까지 비교 |
| 기간 밖 도착 | 매칭 Bucket 없으면 DueIn 미배정. PO 이력·누적 PO는 남을 수 있음 | `ARRIVAL_OUTSIDE_PLAN_HORIZON`으로 거부 | **의도한 차이 유지.** 종료 편향 비교에서는 같은 종료 조건으로 별도 평가 |
| Lot 반올림 | Min/Max를 기준점으로 증가분 올림. Max 초과도 허용 | 절대 발주배수 올림, 승인 한도·물리 용량 안의 유효 배수로 제한 | **재현 기준 + 개선 유지.** MAX_LOT_SIZE를 물리 최대재고로 오해하지 않음 |
| 숫자·반올림 | double 중심, Lot 중간 decimal 변환, 할당률·MOS 소수 2자리 | Decimal 정밀도 40·UOM 정밀도·정규화 | **재현 허용오차 계약 필요.** 모든 소수의 바이트 동등성을 선언하지 않음 |
| 부적합 정책 | Optimizer에 강제 적격 후보 표시 존재 | 적격 후보 없으면 NOT_SELECTED, 사유 보존 | **의도한 개선 유지.** 낮은 서비스 결과를 정상 선정으로 위장하지 않음 |
| 실행·발주 안전성 | 이 소스에 현재 Python Gate와 같은 계약은 없음 | REVIEW 계산·증적만, BLOCK 주문 차단 | **기존 FSN·운영 Gate 유지.** 비교용 계산을 업무 승인으로 승격하지 않음 |

### 수량·MOS에서 특히 혼동하지 않을 것

- C# `OUTL`은 이 본문에서 **보충 목표량**이며 물리 용량 Hard Constraint가 아니다.
- `OUTL` 정책 ExpMOS는 `Round(EOH / (OUTL / MOS), 2)`, 나머지 세 정책은
  `MA_DMD_QTY>0`일 때 `Round(EOH / MA_DMD_QTY, 2)`다. 비정상 무한대/NaN은 0으로 보정한다.
- Python `observed_average_on_hand_mos`는 **전체 평가기간 EOH 산술평균 / 과거 주평균×52/12**다.
  C#의 주차별 ExpMOS나 `RMOS+EMOS`와 같은 지표가 아니다.
- C#의 Lot은 `val<Min → Min`, `Min<=val<=Max → Min+ceil((val−Min)/Step)×Step`,
  `val>Max → Max+ceil((val−Max)/Step)×Step`이다. `Step=0` 등의 잘못된 입력을 목표 엔진이 정상 정책으로 수용하지 않는다.

## 4. 정책 선정은 별도 차이가 있다

`OptimizerProcess:172–263`은 `TB_PLN_SIMUL_SUM_SEG`를 **SEGMT_ID별**로 묶고 Scenario를 비교한다.
`SelectGroupDmRateTop:335–379`는 사전순 첫 SNRIO_ID의 수요금액 `DMD_AMT` 비중을 만든다.
`ApplyWeighted:746–752`는 이를 FILL_RATE/MOS에 곱해 가중값을 만든다.

| 항목 | C# Optimizer | 현재 Python |
|---|---|---|
| 비교 Grain | Segment×Scenario 집계 | Site+Item×Candidate의 실제 PSI 재검증 |
| 목표 | OBJ01/OBJ02의 Fill Rate·MOS 가중 목표와 그룹 누적값 | 서비스 충족 후보 중 비용→평균재고→발주횟수→Candidate ID |
| 개별 조건 | FILL_RATE **>** 하한, MOS **<** 상한 | 현재 수요 Fill Rate **>=** 하한 및 종료 고객 BO=0 등 |
| 부적합 후보 | 모두 탈락 시 OBJ01/02별 강제 적격 플래그 설정 | NOT_SELECTED와 탈락·동점 사유 보존 |
| 비용 의미 | 금액 컬럼이 있으나 현재 Python 비용 목적과 같은 식이 아님 | 봉인 Cost Profile에 따른 보관·BO·발주 고정/변동비 |
| 서비스 원천 | Query.xml `SVC_LV AS FILL_RATE`를 조회 | 실제 PSI Row에서 당주 충족수량/당주 수요수량 재계산 |

**미확인:** SEGMT_ID를 품목 하나로 만드는 운영 모드, 집계 후 품목별 확정 정책 전개, `SVC_LV` 집계 생성식은 이 C#만으로 닫히지 않는다.
따라서 상무 대화의 ‘품목별 선택’ 요구와 이 소스의 ‘그룹 집계 선택’을 같다고 단정하지 않는다.
그룹을 한 품목으로 만들 수 있다는 가능성도 실제 설정·프로시저 근거와 구분한다.

`ApplyFixedCondition:570–599`의 최대재고금액·긴급비용 조건은 명칭과 달리 `FILL_RATE`를 비교한다.
이는 **확인된 구현상 의심점**이다. 의도된 식이나 운영에서 그 분기가 활성화됐다는 증거는 아니다.
재현 시험에서는 코드대로 드러내되 목표 비용 제약으로 복사하지 않는다.

C# 주차 `DemandAllocationRate=Round(DemandAllocation/Demand,2)`와 Optimizer가 읽는 `SVC_LV`는 연결 생성식이 미확인이다.
단순히 100을 곱해 Python Fill Rate·CSL과 맞췄다고 하지 않는다. **CSL은 정책 입력, Fill Rate는 실현 충족 지표**라는 구분은 유지한다.

## 5. 재현 기준과 목표 개선을 분리하는 권장 구조

| 선택안 | 장점 | 위험·판단 |
|---|---|---|
| 현재 Python을 그대로 Legacy 동일 구현이라고 표시 | 추가 구현이 적음 | 확인된 차이를 감춤. **권장하지 않음** |
| 모든 C# 동작을 즉시 운영 Python에 덮어씀 | 원문 방향으로 수렴 가능 | Forecast BO·첫 행·기간 밖 주문·강제 적격 등 기존 안전 계약 훼손. **권장하지 않음** |
| Offline 재현 기준과 현재 목표 계산을 구분 | 실제 차이를 증명하고 필요한 개선만 선택 가능 | 비교 Harness·경계 Golden이 필요. **권장안, 구현 전 계약 확정 필요** |

권장 구분은 개념상 **Legacy Replay / Python Target**이다. 이번에 새 실행 모드·Schema·ID·Hash를 추가한 것이 아니다.

1. 재현 기준은 원본 C#의 판단 순서·정책값 수신·Lot·Calendar·지표·선정 규칙을 작은 입력으로 검증한다.
   부정확해 보이는 동작도 ‘원본 재현 결과’로 표시하고, 운영 발주로 게시하지 않는다.
2. 목표 계산은 현재 확정 주문/Forecast 구분·Cut-off·공급 신뢰성·물리 제약·FSN/REVIEW/BLOCK을 유지한다.
   기존 Python 자체 테스트는 이 경로의 개발 검증 이력이다.
3. **LEGACY_STYLE은 후보 묶음·탐색 범위 Adapter**다. 이것을 사용했다고 C# PSI 또는 C# Optimizer를 재현한 것이 아니다.
   ABC/XYZ와 LEGACY_STYLE의 성과 비교도 동일한 명시적 Simulator·평가 기준 안에서 해야 한다.
4. 기존 `psi_simulator`의 implementation ID·Version Binding을 우선 활용한다.
   현재 검증기는 한 ID·Version으로 고정돼 있어 다중 구현 연결이 이미 된 상태는 아니다.
   결과 의미를 바꾸면 기존 Version을 몰래 재사용하거나 예전 Hash를 강제로 유지하지 않는다.
   과거 Artifact/Hash는 보존하고 필요한 기존 구현·Config Revision의 갱신 범위부터 결정한다.

### 후속 계약에서 정할 네 항목

- **발주 시점·정책식:** C# 공통 기말 `(s,S)`를 비교용으로 추가할 범위와 목표 운영 기본식. 기존 두 정책과 구분한다.
- **시간·정책 기간:** BaseMonth 월 정책과 주차 시작 정책, LT 일수 환산, 주중/기간 밖 도착을 각각 명시한다.
- **서비스·선정:** SVC_LV 분모·집계, 그룹 가중 목표와 품목 비용 목표의 역할, 미달 후보는 미선정으로 남긴다는 목표 기준.
- **호환 경계:** 어디까지 수치 재현하고 어떤 차이를 승인된 개선으로 허용할지, 수량·소수 허용오차와 이유 코드를 정한다.

이미 확정된 Forecast 미이월·미확약 공급 제외·FSN·운영 Gate를 다시 완화할 의사결정으로 취급하지 않는다.
원본 버그로 의심되는 부분도 원문/배포본/기대 동작을 구분한 뒤 수정 여부를 판단한다.

## 6. 수작업 Golden 고정 — 2026-10-21

아래 표는 2026-10-20의 초기 시험 명세다. 2026-10-21에는
[새 계약](IO_LEGACY_REPLAY_GOLDEN_CONTRACT.md#4-수작업-golden-21개)에 따라 21개 작은 사례를 Fixture·테스트로 고정했다.
복수 행·전체 그룹 선정 등 미포함 범위는 새 계약에서 구분한다. 모든 초기 명세가 실행 검증된 것은 아니다.
수작업 기대값의 출처는 보존하며, 그중 대응 20건은 이후 실제 C# 출력으로 검증했다.
원시 C# 출력과 최초 기대값을 구분한다. 기존 테스트·계산 코드는 변경하지 않았다.
수식 비교는 같은 ROP/S·낮은 제약·충분한 미래 Calendar를 가정하며 실제 후보의 수학 계산 효과와 분리한다.

| 사례 | 입력·확인할 기대값 | 목적 |
|---|---|---|
| 판단 시점 | BOH=10, 당주 수요=10, 미도착 PO=BO=0, ROP=10, S=23, LT=7일, Lot 1. C# 기말 PO=23, Python 주기적 S의 기초 PO=13 | 같은 목표량이어도 주문량이 달라짐을 분리 |
| Trigger 경계 | 동일 관측 Position=ROP에서 C# `<`는 미발주, Python fixed-Q `<=`는 발주 | 시점 차이를 제거한 조건식 단위 시험 |
| Forecast 부족 | BOH=0, Forecast=10, 입고 없음. C# Demand로 전달하면 누적 BO=10, Python 고객 BO=0·Forecast Shortage=10 | 수요 종류·이월 계약 |
| Lot 기준점 | Min=5, Max=100, Step=10, 필요량=16. C#=25, Python MOQ/절대배수=20 | 수량 제약의 실질 차이 |
| 복수 행 | 같은 Bucket의 수요 3+7·DueIn 4+6, BOH 복수 행 | 첫 행·순서 의존과 Canonical 총량의 차이 |
| 월 경계 | 주차 시작 월과 BASE_MONTH가 다른 주, 정책 금액·ROP 변경 | 적용 정책이 바뀌는 조건 |
| LT 경계 | LT=0/3/7일·소수 환산·기간 밖 납기 | 포함 Bucket/다음 시작일·사후 기입·미도착 PO·거부 |
| LT=0 서비스 | 수요 처리 뒤 BO 재충족 시 DemandAllocation은 다시 증가하지 않는 C# 흐름 | 종료재고만 맞고 서비스 지표가 다른 결과 탐지 |
| 적격 후보 없음 | 모든 후보가 서비스 하한 미달 | C# 강제 적격과 Python NOT_SELECTED를 구분 |
| 선정 Grain | 동일 Segment에 두 품목, 반대 성과의 두 Scenario | 그룹 공통 선택과 품목별 선택을 혼동하지 않음 |
| 미확인 비용·무수요 | Cost Profile 미봉인, 수요 0, MOS 분모 0 | 0비용·정상 서비스·무한 MOS로 위장하지 않음 |

호출 전 Scope·봉인 입력·FSN/Gate는 그대로 검증한다. 비교용 원본 수학 시험은 별도 DB 없는 Fixture에서만 수행하고
그 결과로 기존 업무 Gate·자동 게시·발주 권한을 올리지 않는다.

## 7. 재정렬한 작업 순서

**현재 기준선 — 완료 · InventoryEngine 문서**

- C# 핵심 경로·Python 동작을 정적으로 대사했다. 원본 ZIP과 추출본 핵심 4개 파일 일치를 확인했다.
- 기존 선정 정책 실행 Adapter는 Python 자체 Offline 검증 완료 상태로 보존한다.
  직전 `404 passed / 186 subtests passed`는 기존 이력이며 이번 문서 작업에서 재실행한 수치가 아니다.
- 2026-10-21 재현 규칙과 수작업 Golden 21개를 확정했다. 신규 검증 39건과 관련 회귀 362건/하위 124건을 통과했다.
- 대응 Golden 20건의 C# 실제 결과와 Python 비교는 확인했다. 운영 배포본·전체 Legacy 수치 동등성·SVC_LV 생성식은 미확인이다.

**재현할 계산 의미와 작은 기대값 고정 — 완료 · InventoryEngine 계약·테스트**

- 원본 재현/현재 Python 개선 유지와 21개 기대값을 고정했다. 계산 변경이나 운영 승인을 뜻하지 않는다.
- 이 단계는 재실행 대상으로 나열하지 않는다. 미확인 원본 지표와 일반 수치 오차 기준은 별도 남긴다.

**DB 없는 C# 참조 실행 — 완료 · C# 원본·InventoryEngine**

- 원본 계산문을 추출한 최소 Harness로 실제 결과를 얻었고 Python을 복사한 참조기를 사용하지 않았다.
- 수작업 Golden·원본은 보존했다. 비용 미확인 사례를 C# 검증 성공으로 채우지 않았다.

**작은 동일 입력의 차이 분류 — 완료 · InventoryEngine Offline 검증**

- 주차 계산·Lot·시간/정책 경계·적격 조건을 실제 출력으로 비교했다. 전체 서비스 집계와 최종 Winner는 미확인이다.
- 다음 단계는 이미 확인한 차이를 반복 측정하는 것이 아니라 목표에 적용할 유지/변경 규칙을 정하는 것이다.

**1. 확인된 차이의 목표 적용과 남은 Source를 닫는다 — 다음 작업 · InventoryEngine 계약·Legacy Source**

- 기초/기말 판단·정책식·Lot·시간 처리의 목표 적용 범위를 정한다. 계산 Version/Binding 변경이 필요하면 먼저 명시한다.
- 서비스 집계·그룹→품목·RMOS/EMOS·LT 변환 조사도 병렬 가능하다. 근거 없는 전체 Winner/경제적 EOQ를 만들어내지 않는다.

**2. 승인한 목표 규칙만 구현·회귀 검증한다 — 다음 작업 · InventoryEngine**

- 비교 후 필요한 작은 수정 또는 별도 재현 Adapter만 추가한다. 기존 분류·과거 Artifact·Hash·FSN/Gate는 보존한다.
- 완료 조건: 변경한 계산 Version/Binding이 명확하고 기존 Python Golden 및 새 비교 시험이 함께 통과한다.
  결과가 달라졌는데 같은 계산 Version·같은 결과 Hash를 유지하지 않는다.

**3. 발주 계획 저장과 독립 IO 검증을 재개한다 — 현재 보류, 원본 비교·목표 정리 이후 · InventoryEngine → dsai-platform**

- 계산 의미가 닫힌 뒤 기존 Artifact Writer·Reference/Receipt로 저장·조회·멱등 복구를 연결한다.
  기존 완료 Adapter를 새로 만들지 않는다. 독립 IO 검증 뒤 Platform Run/API/화면을 연결한다.
- API/저장 계약이 닫힌 뒤 Backend와 Frontend 구현은 병렬 가능하다. 결과 봉인·복구·통합 검증은 직렬이다.
- **승인 필요:** 실제 Migration·DB 적재/E2E Write·공용 Runtime 배포. 이번에는 수행하지 않는다.
- 발주요청 확정·ERP 전송·Demand 최종 연결, MEIO/AI 확대·추가 자원 고도화·대규모 실험은 후속 분리한다.
- Commit/Push는 이번 요청 범위가 아니다. 이후 별도 요청 시 InventoryEngine `inventory_engine_dev`에서 관련 변경만 고정한다.
  Platform 변경이 생기면 dsai-platform 대상 브랜치를 다시 확인하며 임의로 새 브랜치를 만들지 않는다.

2026-10-20 초기 의존관계는 [계획 JSON 이력](data/legacy-csharp-reconciliation-plan-v1.json)으로 보존한다.
2026-10-21 이후 현재 순서는 이 절과 [새 Golden 계약](IO_LEGACY_REPLAY_GOLDEN_CONTRACT.md#6-다음-순서와-남은-확인)을 우선한다.
이는 실행·승인 설정이 아닌 문서이며 기존 Run·정책·Artifact에 새로운 ID/Hash를 추가하지 않는다.

## 8. 2026-10-20 문서 작업의 검증 이력

- 근거 파일 19개(원본 ZIP 포함)의 지문과 코드 위치 45곳, 작업 목록 7개의 의존관계를 확인했다.
  C# 핵심 파일 4개의 ZIP 내부 원문·추출본 바이트도 대조했다.
- 이번 대상 Markdown 3개의 로컬 링크 109건과 문서 내부 절 참조를 확인했다. Diff Check도 통과했다.
- 시작 시 존재한 565개 파일 중 승인된 기존 문서 2개 외 563개는 동일하다.
  새 파일은 이 대사 문서·근거 JSON·계획 JSON 3개뿐이다. 개인 보고서·기존 코드·Schema·Golden을 변경하지 않았다.
- `oma docs` CLI는 환경에 없어 파일 존재·절 참조·지문·근거 표현을 읽기 전용 검사로 확인했다.
  CLI 실행이나 외부 URL 검증, 독립 에이전트 검토를 수행했다고 보고하지 않는다.
- C# 네이티브 시험·전체 Python 회귀·DB·Runtime 검증은 수행하지 않았다.
  이 문서의 수식 예시를 새 테스트 통과 건수나 실제 Legacy 결과로 집계하지 않는다.
