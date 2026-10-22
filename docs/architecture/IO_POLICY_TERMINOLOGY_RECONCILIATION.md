# MOS·RMOS·EMOS·ROP·Q와 서비스 지표 대사

기준일: 2026-10-19. 대상: InventoryEngine `inventory_engine_dev`, 확인 시 HEAD
`99f2f1286d90dfe0e26025e603786a813ea51dac`와 기존 미커밋 후보 계산 코드다.
**용어·문서 대사는 완료했으며 Legacy 수치 동등성은 확인하지 않았다.**
이번 작업은 로컬 원본과 계약의 정적 대사다. 계산 코드·Schema·설정·Hash 계약을 변경하지 않는다.
FSN·운영 Gate·DB·Runtime·실제 발주를 변경하지 않으며, Legacy 프로그램도 실행하지 않는다.

## 1. 확인 사실과 개발 정의를 구분한다

| 표시 | 뜻 |
|---|---|
| `CODE_CONFIRMED` | 확보한 Legacy 파일 또는 제공 SQL 본문에서 확인. 현재 배포·실행 성공의 증거는 아님 |
| `DDL_CONFIRMED` | 보존 DDL의 컬럼·타입·주석만 확인. 생성식·단위까지 증명하지 않음 |
| `BUSINESS_DESCRIBED` | 사용자가 제공한 상무 대화·화면의 업무 설명. 녹음 원본을 재청취한 결과는 아님 |
| `PYTHON_CODE_CONFIRMED` | 현재 로컬 Python의 계산·지표를 확인. Legacy와의 동등성을 뜻하지 않음 |
| `DEVELOPMENT_ASSUMPTION` | Python 개발 프로필에서 선택한 환산·수식·후보·평가 기준. 원본 Legacy 규칙으로 승격하지 않음 |
| `UNVERIFIED` | 생성 프로시저·설정·서비스 계산 원문·배포 기준이 없어 확정할 수 없음 |

Python 코드에서 확인한 구현도 그 수식·평가 기준을 선택한 근거는 개발 가정일 수 있다.
파일 SHA-256·위치·표현과 미확인 목록은 [근거 Register](evidence/policy-terminology-reconciliation-20261019.json)에 기록한다.
상세 Legacy 조사 범위는 [기존 원본 대사](IO_LEGACY_SEGMENTATION_RULE_RECONCILIATION.md)를 유지한다.

## 2. Legacy에서 실제로 확인한 범위

| 용어·Source | 확인한 내용 | 확인하지 못한 내용 |
|---|---|---|
| `TB_ENG_SAFETY_STOCK.MOS` | 대화에 제공된 SQL은 `COALESCE(RMOS,0)+COALESCE(EMOS,0)`를 적재 | 각 항의 생성식·월수 환산·정책별 의미. 이 SQL은 첨부 파일이 아닌 대화 원문이므로 파일 Hash를 부여하지 않음 |
| `RMOS`, `EMOS`, `EOQ_QTY`, `APPY_*` | 보존 Master DDL에 컬럼·이름 존재 | RMOS=ROP/Dm, EMOS=Q/Dm 또는 Q/(2Dm) 중 무엇인지. 이름·주석만으로 선택할 수 없음 |
| `ROP_QTY`, `LT_QTY`, `SFST_QTY` | Rop Python/R은 일 수요·고정 `STOCK_LT`로 10,000회 분포 생성. ROP는 서비스 분위수, LT는 **중앙값**, 안전재고는 두 값의 차이 | 실제 활성 서비스 코드·배포 언어·후속 월별 변환·동일 실행 경로. LT 중앙값을 평균수요라고 부르지 않음 |
| `Q`·Lot | BUFFER SQL의 `MIN_PO_QTY`, `PO_LOT_QTY`가 최소량·배수·Lot으로 전달 | Lot이 경제적 최적 EOQ라는 근거, 원본 주문 분기·EOQ 산식·공동 발주비 배부 |
| 월평균 수요 | BUFFER의 `ANNUAL_AVG_QTY` 식은 `ROUND(M24_DMD_QTY / 24, 0)` | 이름만 보고 연평균이라고 해석하거나, MA03/현재 Python Dm과 동일시할 수 없음 |
| MAX·시작재고 | Safety Stock SQL은 `LOT_MAX_QTY → MAX_LV`; TGSM STOCK SQL은 `MAX_QTY → 시작 Position` | 전자가 물리 용량인지 계산 목표재고인지 생성식으로 확인하지 못함. 후자는 실제 BOH가 아닌 Legacy 정책 Proxy로 구분 |
| 서비스수준 | Rop은 `SERVICE_LEVEL.CODE_CD`를 정수화해 100으로 나눠 분위수에 사용 | 실제 코드 목록·최종 Simulation 서비스 KPI·합격 기준. 95% 고정으로 추정하지 않음 |

상무 대화의 핵심은 그룹이 **정책 후보의 탐색 범위**를 만들고, Simulation·선정은 **품목별**로 한다는 방향이다.
`최소 ROP / 최대 ROP+Q / 중간 ROP+Q/2`는 수량 관계 설명으로 보존하되 Legacy 컬럼 생성식의 증거로 쓰지 않는다.
`ROP+Q/2`를 그대로 MOS라고 쓰면 수량과 월수 단위가 섞인다. 월수로 표시하려면 명시한 월평균 수요로 나눠야 한다.

확보한 Rop은 PO별 납기 편차를 표본추출하지 않고 고정 `STOCK_LT`를 소비한다.
Python의 난수 Seed 미고정과 R의 Seed·표준편차·반올림·음수 보정 차이도 있어 두 원본의 수치 동등성을 주장하지 않는다.
“실적 축적 후 PO 편차 반영”은 업무 요구로 남기고 현재 구현 완료로 표기하지 않는다.

## 3. 현재 Python 용어와 단위

아래는 `MONTHLY_COVER_SEARCH_V1_NOT_LEGACY_EOQ`의 **구현된 개발 정의**다.
품목별 수량은 해당 Canonical UOM, 커버리지는 환산 월수, 점검주기는 주, 물리 납기는 일이다.
계획 수량 정밀도와 실제 주문 수량 정밀도는 Quantity Rule을 따르며 무조건 정수 EA로 재해석하지 않는다.

| 필드·기호 | 현재 정의 | 혼동하면 안 되는 것 |
|---|---|---|
| `Dm` / `monthly_mean_demand_qty` | W0 이전 주평균 수요 μ × 52 ÷ 12 | 실제 Calendar 월별 수요·미래 Forecast 월평균이 아님 |
| `requested_rmos` | `Dm × RMOS`를 ROP 하한으로 요청 | 최종 ROP/Dm과 항상 같지 않음. 수학적 ROP·수량 올림이 우선할 수 있음 |
| `requested_emos` | 기본 Q=`ceil_UOM(Dm × EMOS)`를 만드는 커버리지 | Q/2의 커버리지, 실제 주문량의 커버리지가 아님 |
| `rop_qty` / ROP | 수학적 ROP와 요청 RMOS 하한 중 큰 수량 | 월수·점검주기가 아님. 주기적 S 정책에서는 직접 주문 Trigger도 아님 |
| 기본 Q | 요청 EMOS로 산출한 기본 발주 제안 수량 | 승인·제약 적용 후 실제 주문량, 경제적 최적 EOQ와 다름 |
| S / `target_inventory_qty` | 기본 ROP+Q. 주기적 정책은 보호기간 하한으로 더 커질 수 있음 | 매번 주문할 수량·물리 최대 용량이 아님 |
| `effective_rmos` | 최종 ROP ÷ Dm | 요청 RMOS를 그대로 복사한 값이 아님 |
| `effective_emos` | 최종 `(S−ROP) ÷ Dm` | 주기적 정책에서는 기본 Q/Dm보다 커질 수 있음. 실제 주문량의 비율도 아님 |
| `policy_max_position_mos` | 최종 S ÷ Dm | 관측 최대 On-hand MOS를 보장하는 상한이 아님 |
| `policy_midpoint_position_mos` | `(ROP+(S−ROP)/2) ÷ Dm` | 정책 Position의 산술 중간점. PSI 평균 On-hand MOS가 아님 |
| `observed_average_on_hand_mos` | PSI의 주차말 `on_hand_eoh_qty` 산술평균 ÷ Dm | 예약재고 포함. 가용재고만의 평균·일별/시간가중 평균이 아님 |
| `accepted_order_qty` | 공통 Guard가 MOQ·배수·용량·승인 범위를 적용한 실제 수락 수량 | Q 또는 S를 그대로 복사하지 않음. 조정·거부 사유를 함께 보존 |

현재 관측 Position은 **가용재고 + 미도착 공급 − 고객 Backorder**다. 예약재고는 Position에서 제외하지만,
보관비·물리 용량·On-hand 지표에는 포함한다. 품목별 판단 시점은 해당 주차 시작, 그 주차의 입고·출고 반영 전이다.
미도착 공급에는 실행기가 전달한 확정 공급과 앞선 권고 주문이 포함되므로 이미 낸 주문을 무시하고 다시 주문하지 않는다.
미확약 입고를 확정 공급으로 바꾸는 결정은 이번 대사에 없다.

### 수식과 반올림 순서

```text
L       = ceil(보호 Lead Time 일수 / 7)
SS(L)   = ceil_UOM(z(CSL) × 표준편차 σ × √L)
ROP0    = ceil_UOM(μ × L + SS(L))
ROP     = max(ROP0, ceil_UOM(Dm × requested_rmos))
Q       = ceil_UOM(Dm × requested_emos)
S0      = ROP + Q

ROP_FIXED_Q:
  점검일에 Position <= ROP이면 ORDER_QTY(Q) 제안

PERIODIC_ORDER_UP_TO:
  H = ceil((보호 Lead Time 일수 + 7 × review_cycle_weeks) / 7)
  SH = ceil_UOM(μ × H + SS(H))
  S = max(S0, SH)
  점검일에 Position < S이면 ORDER_UP_TO(S) 제안
  Guard의 원 주문 필요량 = max(0, S − Position)
```

안전재고 → ROP → 목표량 순서로 수량을 올림한다. `ceil_UOM`은 계획 정밀도 기준이며 발주배수 반올림과 다르다.
CSL의 z값은 정규분포 역함수 결과를 소수 12자리로 HALF_EVEN 반올림한다.
후보 CSL은 활성 VED 하한과 비교해 큰 값을 사용한다. 개발 탐색이 기존 운영 CSL·Gate를 변경하는 것은 아니다.
SDE는 보호기간만 바꾸며 실제 납기는 Source의 **물리 Lead Time**으로 결정한다.
현재 일→주 올림과 미래 주차 경계 매핑은 개발 계약이지 실제 Calendar 월 단위 재현이 아니다.

예를 들어 Dm=100, 최종 ROP=100, 기본 Q=50, S=150이면 유효 RMOS=1, 유효 EMOS=0.5,
정책 최대 MOS=1.5, 정책 중간점 MOS=1.25다. **RMOS+EMOS=1.5를 평균 MOS=1.25로 바꾸어 부를 수 없다.**
이 예는 단위 관계 설명이며 Legacy Golden이나 관측 평균재고의 증거가 아니다.
주기적 보호 하한으로 S가 180이 되면 유효 EMOS는 0.8이고 기본 Q는 여전히 50일 수 있다.

누락 이력은 0으로 만들지 않는다. 이력이 부족하면 해당 탐색 품목을 제외하며,
과거 평균 0이면 `ZERO_HISTORICAL_MEAN_DEMAND`로 후보 탐색에서 제외한다.
평가 지표의 Dm이 없거나 0이면 관측 MOS는 `null`이다. 임의로 0개월·무한대·정상 정책으로 채우지 않는다.

## 4. 서비스 지표는 분모와 시간을 함께 명시한다

| 필드·용어 | 분자 / 분모·판정 | 의미·경계 |
|---|---|---|
| Legacy `SVC_LV` | 코드값 ÷ 100을 분포 분위수에 입력 | ROP 계산 입력. 최종 실현 Fill Rate와 동의어가 아님 |
| 후보 `requested_csl`, `effective_csl` | 정규분포 z 산출 입력, 비율 0~1 | 설정한 Cycle Service Level. 실현 서비스 보장·합격값이 아님 |
| `current_demand_fill_rate` | Σ(당주 고객주문 충족+순 Forecast 충족) ÷ Σ(당주 고객주문+순 Forecast) | 수량 가중 동기간 충족률. 과거 BO 출고를 분자에 넣지 않음 |
| `projected_service_level` | Result Bundle의 `on_time_fulfilled_qty / horizon_demand_qty` | 위와 같은 수량 충족 의미. 주차 단위 충족을 뜻하며 일별 정시납품 실적·확률적 CSL이 아님 |
| `stockout_free_active_bucket_ratio` | Forecast Shortage=0이고 마감 고객 BO=0인 수요 주차 수 ÷ 수요가 있는 주차 수 | 주차 개수 비율. 기존 BO 잔존도 실패로 센다. 보충 Cycle 무결품 확률로 해석하지 않음 |
| `service_feasible` | 수요 존재, Fill Rate ≥ `minimum_fill_rate`, 마지막 고객 BO=0 | 탐색의 서비스 조건. 최종 선정에는 별도 Gate·비용·성공 조건도 필요 |
| `ending_backorder_qty` | 마지막 주차의 미충족 고객주문 수량 | Forecast Shortage는 이월 BO에 합산하지 않음 |
| Legacy `F_RATE` | 현재 확보 자료에서 생성식 원문을 재확인하지 못함 | `UNVERIFIED`. 금액 기준/수량 기준/현재 Fill Rate 중 어느 것으로도 자동 매핑하지 않음 |

예: 기존 BO=5, 현재 고객주문=5, 순 Forecast=5, 가용량=10이면 기존 BO 5와 현재 주문 5를 먼저 충족한다.
현재 수요 충족률은 **5/10=0.5**이며 기존 BO 출고까지 넣은 10/10이 아니다.
마감 BO가 0이어도 Forecast Shortage=5이므로 무결품 수요 주차는 아니다.

무수요 기간은 `NO_DEMAND_IN_HORIZON`과 서비스 지표 `null`로 남긴다. 과거 BO만 처리했어도 100%로 채우지 않는다.
Result Bundle은 UOM별 합산 수량으로 비율을 계산하고 소수 6자리 HALF_EVEN으로 표시한다.
품목별 탐색 지표는 Decimal 비율을 보존한다. **표시 반올림 값으로 선정 판정을 대체하거나 두 Artifact Hash가 같다고 요구하지 않는다.**
최종 선정은 [선택 계약](IO_POLICY_SELECTION_CONTRACT.md)의 원 PSI 재검증과 기준을 유지한다.

## 5. 비용·EOQ·최적이라는 표현의 범위

현재 탐색 비용은 주차말 On-hand 보관비, 마감 고객 BO 누적 비용, 품목별 수락 주문 고정비와 수량 변동비다.
비용 Profile이 없으면 `COST_PROFILE_NOT_BOUND`, 비용은 `null`이고 최저비용 정책을 선정하지 않는다.
Forecast Shortage의 Lost Sales 비용, 여러 품목의 공동 Shipment 고정비 배부, Landed Cost의 자동 합산,
할인율·종료 잔존가치는 이 탐색 비용식에 포함하지 않는다. 별도 비용 Child가 존재한다고 자동 적용된 것으로 쓰지 않는다.

따라서 Q는 **탐색한 발주량**이며 EOQ 공식이나 실제 원가로 경제적 최적성을 증명한 값이 아니다.
현재 선정은 서비스 조건을 통과한 유한 후보 중 `(총비용, 주차말 평균 On-hand, 발주횟수, Candidate ID)` 순이다.
동일 기간·입력·비용 가정 안의 최상위 후보이며 전역 최적·업무 승인·실제 발주를 뜻하지 않는다.
기간·비용·종료 효과의 비교는 [장기 평가·민감도 계약](IO_POLICY_SENSITIVITY_CONTRACT.md)을 따른다.

## 6. 확인 완료와 남은 확인을 구분한다

**완료 — 현재 개발 계약의 의미 고정 · InventoryEngine 문서**

- RMOS/EMOS 요청값·유효값, 기본 Q·최종 S·실제 주문량, 정책 MOS·관측 MOS를 분리했다.
- CSL 입력·수량 Fill Rate·주차 무결품 비율·선정 조건을 코드와 대조했다.
- 52/12 환산·정규분포식·비용 가정·유한 후보 최적 범위를 개발 정의로 명시했다.
  이번 대사만으로 기존 후보 값·Golden·Config Version·Hash를 바꾸지 않는다.

**외부 작업 대기 — Legacy 원본 동등성 확인 · Legacy Source 제공 경로**

- `SP_PLN_ROP_MNTHLY`, `SP_DSIO_ROP_UPD`, `SP_PLN_SEGMTN_PART_MST` 본문과 활성 인자/구간/후보 설정.
- RMOS/EMOS의 정확한 확장 명칭·단위·생성식, EOQ/발주비 배부, 실제 정책→주문 분기, 서비스 KPI/F_RATE 원문.
- 원본 언어·라이브러리·Seed·배포 Version. 확보 전에는 개발 `LEGACY_STYLE`을 `LEGACY_REPLAY`로 부르지 않는다.
  개발 Fixture 비교를 진행하는 데 이 원본 확보를 필수 선행조건으로 두지는 않는다.

**후속 구현 완료 — 별도 후보 Catalog의 선정·평가 연결 · InventoryEngine**

- 용어 대사 이후 [Catalog 선정·평가](IO_CATALOG_POLICY_SELECTION.md)를 구현했다.
  [Catalog PSI](IO_CANDIDATE_CATALOG_PSI.md)의 실제 후보 결과에 기존 선정 기준과 원 입력 재검증을 적용했다.
  기존 PSR인 것처럼 계약 ID를 바꾸지 않고 Catalog용 참조·검증을 유지했다.
  아래 용어 대사 검증 수치·근거 Hash는 대사 당시 이력이며 후속 코드 변경과 합산하지 않는다.
- 현재 다음은 동일 조건의 ABC·XYZ/LEGACY_STYLE/넓은 후보를 미사용 기간에서 비교하는 것이다.
  구현→단위/계약 테스트→독립 Offline 검증은 직렬이며, Legacy 원본 확보는 독립적으로 진행할 수 있다.
- 그 후 DB 적재·복구 검증과 Demand→IO 연결을 진행한다. 실제 DB 적용·Write는 별도 승인 대상이다.

## 7. 이번 검증 범위

- 정책 탐색·선정·민감도·수학 정책·기초 PSI·Catalog/기존 Hash 회귀: **188 passed / 75 subtests passed**.
- Result Bundle 지표·보충 제약·PSI Orchestrator 회귀: **123 passed / 129 subtests passed**.
  두 묶음은 서로 다른 17개 테스트 파일이며 합계 **311 passed / 204 subtests passed**다. 전체 회귀 재실행 수치는 아니다.
- 원문/코드 14개 파일 SHA-256과 근거 표현 33곳, 문서 5개의 로컬 링크 72개를 확인했다.
- 단위 관계·현재 BO 차감·예약재고 포함·무수요/비용 미연결의 메모리 내 확인 8건을 수행했다.
  새 Golden·테스트 파일을 추가하거나 기존 Hash를 재생성하지 않았다.
- 코드·테스트·Schema 337개 파일의 작업 전후 SHA-256이 모두 동일하다. 개인 보고서도 변경하지 않았다.
- Engine 전용 환경에 pytest가 없어 기존 Platform Backend 테스트 환경을 사용했다.
  의존성을 추가 설치하거나 기존 개발 환경을 바꾸지 않았다. 문서·근거 JSON과 `git diff --check`를 검증했다.

실행 대상과 보호 파일 불변 확인은 [근거 Register](evidence/policy-terminology-reconciliation-20261019.json)에 기록한다.
단위 관계 예제는 Legacy 수치 동등성·실제 운영 Source·DB E2E 검증을 대신하지 않는다.
