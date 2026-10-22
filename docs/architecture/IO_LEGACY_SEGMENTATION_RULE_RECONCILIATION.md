# Legacy DSIO Segmentation·정책 후보 규칙 대사

기준일: 2026-10-15. **로컬 코드·제공 SQL·문서 대사는 완료했으며 Legacy 구간·후보 생성의 원본 동등성은 미확정**이다.
DB 접속·변경, Legacy 프로그램 실행, 신규 분류 알고리즘 구현은 하지 않았다.

2026-10-19 후속 [MOS·RMOS·EMOS·ROP·Q와 서비스 지표 대사](IO_POLICY_TERMINOLOGY_RECONCILIATION.md)에서
현재 Python의 요청/유효 커버리지·주기적 S·실제 주문량과 CSL/수량 충족률을 함께 확인했다.
아래 원본 확인 등급은 유지하며 개발 수식을 Legacy 생성식으로 승격하지 않는다.

## 1. 결론과 근거의 등급

확보한 자료는 세 부분으로 나뉜다.

1. `Rop` Python/R: 품목별 수요 재표본추출로 서비스수준별 ROP를 계산한다. Segmentation 구간 생성기가 아니다.
2. PostgreSQL Inbound: 이미 정해진 품목 정책을 Engine 입력으로 전달한다. 후보 탐색기를 구현하지 않는다.
3. AS-IS 화면·상무 대화: 그룹별 탐색 범위를 정한 뒤 품목별 Simulation·선택을 수행하는 업무 방향을 설명한다.

MSSQL 형식의 `Rop` 코드와 PostgreSQL Inbound가 **동일 배포 Version으로 이어지는지는 미검증**이다.
호출 이름·Column 이름이 유사하다는 이유로 하나의 완성된 실행 경로로 취급하지 않는다.

| 등급 | 이 문서에서 뜻하는 것 |
|---|---|
| `CODE_CONFIRMED` | 파일 또는 제공 SQL 본문에서 읽은 계산·조건·호출. 실행 성공·현재 배포의 증거는 아님 |
| `DDL_CONFIRMED` | 보존된 DDL에 Column·타입·주석이 존재. 생성식·업무 적용의 증거는 아님 |
| `BUSINESS_DESCRIBED` | 사용자가 제공한 AS-IS 화면·상무 대화에 있는 업무 설명. 현재 구현 검증과 구분 |
| `UNVERIFIED` | 원본 본문·구간표·설정값·배포 기준을 확보하지 못함 |
| `DEVELOPMENT_ASSUMPTION` | 향후 `LEGACY_STYLE` 재구성 또는 현 Python 탐색을 위한 명시적 개발 정의 |

세부 파일 Hash와 코드 위치는 [근거 Register](evidence/legacy-segmentation-rule-reconciliation-20261015.json)에 기록한다.

## 2. 확인한 원본과 범위

| ID | Source | 확인 범위 |
|---|---|---|
| `ROP_PY` | `/Users/igwanhyeong/PycharmProjects/Rop/RSC/ROP_SYS.py` | 74~123 인자/쓰기, 184~218 후속 호출, 245~407 수요 자료·ROP 계산 |
| `ROP_R` | `/Users/igwanhyeong/PycharmProjects/Rop/RSC/ROP_SYS.R` | 23~71 요청/기간, 107~280 자료·ROP 계산, 325~343 후속 호출 |
| `BUFFER_SQL` | 사용자 제공 `SP_DSIO_ENG_IN_BUFFER` 첨부 | 95~109 Segmentation 연결, 130~263 TGSM/POSM 정책·Lot·평균수요 전달 |
| `STOCK_SQL` | 사용자 제공 `SP_DSIO_ENG_IN_STOCK` 첨부 | 41~105 TGSM `MAX_QTY` 시작 Position 및 `%9%` 제외 |
| `PART_DDL` | dsai-platform의 보존 `artifacts/dbmg-dsai-pivot-prompt3-20260704T142700Z/schema_only.sql` | 8540~8602 Master Column, 8856~8874 빈도·변동성·Lead Time 주석 |
| 제공 SQL 텍스트 | 이 대화의 `SP_DSIO_ENG_IN_SAFETY_STOCK` | `RMOS + EMOS → MOS`, `LOT_MAX_QTY → MAX_LV`, `ROP_QTY → ROP` 적재 |
| 업무 자료 | 이 대화의 AS-IS 화면과 상무 대화 | 인자 구간 그룹, 그룹별 범위, 품목별 Simulation·선택, 품목 단독 그룹 |

관련 로컬 프로젝트·제공 첨부·SQL/문서·`Rop.zip` 목록을 확인했다. 아래 원본 본문은 확보하지 못했다.

- `SP_PLN_SEGMTN_PART_MST`: Rop 코드에서 호출하는 위치만 확인.
- `SP_PLN_ROP_MNTHLY`, `SP_DSIO_ROP_UPD`: 호출 위치만 확인.
- 실제 인자별 구간표/그룹 배정식, 정책×RMOS×EMOS 후보 생성·선정 프로그램.
- `STOCK_POLICY`를 주문으로 바꾸는 실제 엔진 분기. 이전에 언급된 OUTL/ROPQ/MINQ/SFST의 정확한 대응도 미확정.

DemandEngine의 NSPM Introspection 보고서는 24개 Target 집합 중심이며 원본 Evidence 경로는 현재 로컬에 없다.
이 보고서로 전체 NSPM 프로시저의 부재를 증명하거나, 이전 대화의 조회 결과를 재검증했다고 표현하지 않는다.
원격 NSPM 또는 개발 PostgreSQL은 이번 작업에서 조회하지 않았다.

## 3. 인자·구간·단위·후보 대사표

| 항목 | 원본에서 확인된 규칙 | 상태 / 아직 확정할 수 없는 것 |
|---|---|---|
| ROP 계산 Grain | 회사·BU·Plan Type·Plan ID·품목·서비스수준별 결과를 `TB_PLN_ROP`로 구성 | `CODE_CONFIRMED`. 현재 단일 Site 계약과 BU를 자동 동일시하지 않음 |
| 수요 Source | `TB_DYN_FCST_DMND_SELLOUT`의 일자별 수요를 운영품목으로 합산, Master의 `STOCK_LT` 사용 | `CODE_CONFIRMED`. 테이블 명칭만으로 Forecast/Actual 봉인 의미를 확정하지 않음 |
| ROP Lookback | `period_from`/`period_to`의 양 끝 포함 일자 범위. R 요청부의 POSM은 코드 `CAL_TERM/ROP_TERM`의 개월 값, TGSM은 `ROP_CAL_PERIOD_FROM/TO` | `CODE_CONFIRMED`. 현재 실제 개월 값 미확인. 변수명 `two_year`가 고정 24개월의 증거는 아님 |
| 누락 일자 | 위 일자 Grid에 수요를 연결하고 값이 없으면 0으로 채움 | `CODE_CONFIRMED`. 이 Legacy 동작을 현재 FSN 마감·Source 누락의 0수요 허용으로 확대하지 않음 |
| Lead Time | 일 단위 Grid에서 `STOCK_LT`만큼 표본을 뽑음. Python은 해당 품목의 첫 고유값 사용 | 계산상 일수로 소비함은 확인. DDL의 정확한 UOM·소수/복수 LT 처리 및 현재 적용값은 `UNVERIFIED` |
| 재표본추출 가중치 | 과거 Window 전반/후반 각 일자에 0.4/0.6 비례 가중치. Python은 합으로 정규화 | `CODE_CONFIRMED`. 짝수 일수일 때만 두 구간 전체 질량이 정확히 40%/60%. 계절성 모델의 증거는 아님 |
| ROP 반복 | Lead Time 동안 수요 합에 양수 수요 표본 수만큼 정규 잡음 추가, 10,000회 분포 생성 | `CODE_CONFIRMED`. 10,000개 발주정책 후보 또는 장기 PSI Simulation을 뜻하지 않음 |
| 서비스수준 후보 | `TB_MST_CODE.GROUP_CD='SERVICE_LEVEL'`의 `CODE_CD`를 정수화하고 100으로 나눔 | `CODE_CONFIRMED`. 실제 코드 목록은 미확보. 95% 고정 또는 70~97% 목록이라고 단정하지 않음 |
| ROP/안전재고 출력 | `ROP_QTY=서비스수준 분위수`, `LT_QTY=중앙값`, `SFST_QTY=두 값의 차이` | `CODE_CONFIRMED`. 현 Python의 정규분포 공식 ROP와 같은 수식이 아님 |
| 그룹 인자 후보 | 빈도·단가·변동성·Lead Time·ROP·총수요 등이 화면/대화에 등장 | `BUSINESS_DESCRIBED`. 실제 활성 인자 목록·계산식·선택 알고리즘은 미확정 |
| Master 인자 Column | `dmd_frqc`, `cv`, `stock_lt`, 단가/통화, `segmt_id`, `rmos/emos/rop_qty/eoq_qty`, `appy_*` 존재 | `DDL_CONFIRMED`. `cv`를 CV²로 바꾸거나 금액을 단일 통화로 간주할 근거는 아님 |
| 그룹별 경계 | 화면의 A/B/C/D/Z와 대화의 범위·그룹 설명 | `BUSINESS_DESCRIBED`. 실제 수치·경계 포함 방향·Null·동점·중복 구간 규칙은 `UNVERIFIED` |
| 품목 단독 그룹 | 이슈 품목을 별도 그룹으로 둘 수 있다는 설명 | `BUSINESS_DESCRIBED`. 원본 Override 우선순위·승인/적용 기간·저장 구조는 미확인 |
| TGSM 정책 전달 | Plan의 `SEGMTN_ID`로 `TB_PLN_SEGMTN_PART_INFO`를 연결해 `PO_POLICY_CD → STOCK_POLICY` | `CODE_CONFIRMED`. 분류·후보 생성 자체가 아니라 결정된 정책의 소비 경로 |
| POSM 정책 전달 | `TB_MST_OPER_PART.APPY_PO_POLICY_CD → STOCK_POLICY` | `CODE_CONFIRMED`. TGSM과 동일한 Source 경로로 합치지 않음 |
| 최소/배수 Lot | `MIN_PO_QTY → MIN_LOT_SIZE`, `PO_LOT_QTY → MULTIPLY_LOT_SIZE/LOT_SIZE` | `CODE_CONFIRMED`. 이것을 EOQ의 생성식으로 해석하지 않음 |
| 수요 환산 | 이름이 `ANNUAL_AVG_QTY`지만 식은 `ROUND(M24_DMD_QTY / 24, 0)` | `CODE_CONFIRMED`. 월평균 꼴이며 Column 이름만 보고 연평균으로 해석하면 안 됨 |
| MOS 전달 | 제공 Safety Stock SQL은 `COALESCE(RMOS,0)+COALESCE(EMOS,0) → MOS` | SQL 텍스트로 확인. 각 항의 생성식·단위·정책별 의미는 `UNVERIFIED` |
| MAX/ROP 전달 | 월별 `LOT_MAX_QTY → MAX_LV`, `ROP_QTY → ROP` | SQL 텍스트로 확인. `LOT_MAX_QTY`를 물리 용량으로 확정할 수 없음 |
| TGSM 시작 Position | Segmentation `MAX_QTY`를 재고 입력으로 사용, `SEGMT_ID NOT LIKE '%9%'` 적용 | `CODE_CONFIRMED`. `%9%` 그룹의 업무 의미는 미확인. 목표 BOH/FSN Gate에 전파하지 않음 |
| 정책 후보 조합 | 그룹 범위를 품목별로 적용하고 Simulation·최적 선택한다는 업무 설명 | `BUSINESS_DESCRIBED`. 실제 RMOS/EMOS 시작·끝·증분, 조합/가지치기, 예산은 `UNVERIFIED` |
| 비용·서비스·선정 | 발주비·재고 운영비의 절충, 품목별 최적 선택이라는 설명 | `BUSINESS_DESCRIBED`. 실제 비용 배부·목적식·서비스 탈락·동점/미선정 기준은 미확인 |

확보한 ROP/Inbound 경로에는 **ABC·XYZ 등급을 필수 입력으로 요구하는 코드가 없다**.
이 관찰은 Legacy 제품 전체에 ABC·XYZ 기능이 없다는 증명이 아니며, 현재 Engine의 ABC·XYZ/FSN Gate를 제거하는 결정도 아니다.

## 4. ROP Python/R 차이와 재현 위험

| 항목 | Python | R | 재현 시 주의점 |
|---|---|---|---|
| 난수 Seed | 계산 본문/파일에 고정 호출 없음 | 품목 반복 안 `set.seed(0)` | 현 파일만으로 Python의 재실행 동일 결과를 보장할 수 없음 |
| 표준편차 분기 | 전체 일수 벡터 고유값 수 >2이면 양수 수요의 표준편차, 아니면 첫 양수 값의 제곱근, 0수요는 0 | 양수 수요 고유값 수 >1이면 표준편차, 아니면 제곱근 | 두 양수 값만 있고 0이 없는 이력 등에서 분기가 다름 |
| 표준편차 정밀도 | 5자리 반올림 | 해당 반올림 없음 | 잡음 분포가 달라질 수 있음 |
| 수요 합 | 표본 합을 `int`로 변환한 뒤 잡음 추가 | 표본 합을 그대로 사용 | 소수 수량의 의미·반올림 차이 |
| 음수 보정 | 음수는 0, 나머지는 반올림한 표본 사용 | 0 보정 직후 `round(sub_result)`로 다시 대입 | R의 보정이 덮어써지는 정적 사실. 이번에 수정·실행하지 않음 |
| 분위수 출력 | `np.quantile` 결과를 추가 정수화하지 않음 | 분위수 반올림 후 정수화 | 저장 수량과 UOM 오차가 생길 수 있음 |

원본은 SQL 조회뿐 아니라 상태 UPDATE·기존 ROP DELETE·결과 Export·후속 프로시저 호출을 포함한다.
**원본 파일을 import/source하거나 생성자를 호출하지 않았다.** 위 내용은 정적 코드 대사다.
동등성 회귀의 기준 구현·라이브러리 Version·Seed·수량 반올림은 별도로 고정해야 한다.

현재 확인된 ROP 경로는 품목별 고정 `STOCK_LT`를 사용한다. PO 납기 편차를 표본추출하는 코드는 없다.
상무 대화의 “실적 축적 후 PO 편차 반영”은 별도 업무 요구이며 구현됐다고 확정하지 않는다.

## 5. RMOS·EMOS·MOS의 의미를 합치지 않는다

- SQL에서 확인된 식: **적재 MOS = RMOS + EMOS**.
- 대화의 `ROP + Q/2`는 수량 관계 설명이다. 월수로 쓰려면 월평균 수요로 나누어야 하며,
  이 관계가 Legacy RMOS/EMOS Column의 정확한 정의라는 증거는 없다.
- 현재 `MONTHLY_COVER_SEARCH_V1`은 **신규 개발 정의**다.
  `Dm=주평균×52/12`, `ROP=max(공식 ROP, Dm×RMOS)`, `Q=Dm×EMOS`, `S=ROP+Q`를 사용한다.
  EMOS를 `Q/2` 커버리지로 자동 재해석하지 않는다.
- 실제 평균 On-hand/MOS는 PSI에서 관측한다. 위 중간점 관계를 실적 평균재고로 대체하지 않는다.
- 원본의 월별 변환·최적 정책 배부를 거치는 `SP_PLN_ROP_MNTHLY`/`SP_DSIO_ROP_UPD` 본문이 없으므로
  기존 정규분포 기반 계산을 “Legacy Bootstrap ROP의 재현”으로 이름만 바꾸지 않는다.

## 6. 개발 가정 Register — 개발 V1 정의 완료 / Legacy 확인값은 아님

아래 `D1~D7`의 개발 선택은 [LEGACY_STYLE 개발 규칙 V1](IO_LEGACY_STYLE_DEVELOPMENT_RULES.md)에
별도로 고정했다. 이 절의 원본 근거/미확인 판정은 그대로이며, 개발 숫자를 원본 확인값으로 바꾸지 않는다.

| ID | 개발 재구성에서 정할 항목 | 현재 권장 경계 |
|---|---|---|
| `D1` | 활성 인자와 Source | ROP·Lead Time·단가 등 실제 사용 가능한 인자를 명시. 임의 0·위조 Source로 대체하지 않음 |
| `D2` | 인자별 구간 | 고정된 수치 또는 TRAIN 자료로만 산출한 경계를 설정 Version으로 봉인. 예: `[lower, upper)` 규약은 개발 선택이지 원본 확인이 아님 |
| `D3` | 결합 그룹/품목 예외 | `search_group_id`로 표현하고 ABC·XYZ 기본 등급은 보존. 예외 사유·우선순위를 별도 정의 |
| `D4` | 그룹별 후보 범위 | 정책 종류·RMOS·EMOS·서비스수준·점검주기를 명시한 목록. 원본 숫자로 확인되지 않은 값을 Legacy 기본값으로 넣지 않음 |
| `D5` | 후보 수·선정 기준 | 현 공통 Guard/PSI/선택기를 공유하며 동일 후보 예산 또는 실제 비용을 함께 보고 비교 |
| `D6` | ROP Feature 생성 | 기존 수학식·보존 Legacy 값·재구성 Bootstrap 중 하나를 구분. 그룹 효과와 ROP 계산기 변경 효과를 한 실험에 섞지 않음 |
| `D7` | 누락·0평균 처리 | 공통 계약의 미분류/사유 보존. 기존 FSN/운영 Gate·미분류 3품목 차단을 변경하지 않음 |

현재 코드의 정책 2종, RMOS 0~12, EMOS >0~12, 서비스수준 0.5~0.9999,
점검주기 1~13주, 품목당 최대 256개는 **신규 Python 계약의 검증 상한**이지 Legacy의 실제 탐색 범위가 아니다.
앞선 실험의 SMALL/MEDIUM/LARGE EMOS 값도 합성 Fixture의 개발 가정이며 Legacy에서 추출한 값이 아니다.
이 대사 단계는 Class/Config 활성화 이전 기록이다. 후속 [Class·Adapter·Producer 로컬 구현](IO_LEGACY_STYLE_IMPLEMENTATION.md)은
완료했으나 새 Config 활성화·운영 연결·Legacy 원본 동등성은 여전히 미완료다.
개발 V1은 참조 ROP 커버리지·물리 Lead Time·USD 단가를 고정 구간으로 나누고,
최대 27개 그룹을 3개 후보 묶음(각 32개 요청 조합, 정확한 합집합 88개)에 연결한다.
인자·구간·점수·모든 후보 숫자는 개발 가정이며 Legacy의 실제 설정이라는 뜻은 아니다.

## 7. 이번 대사의 검증 결과

- 근거 파일 5개의 원문 SHA-256, 코드 위치/표현 14곳, 관련 문서 5개의 로컬 링크 49개를 확인했다.
- `test_segmentation_compatibility_contract.py`, `test_segmentation_adapters.py`, `test_policy_search.py`,
  `test_policy_selection.py`를 다시 실행해 **69 passed**를 확인했다. 기존 Snapshot/PSI/선택 Hash와
  FSN BLOCK/REVIEW 차단을 검증한 것이며 Legacy 프로그램의 수치 동등성 테스트가 아니다.
- `git diff --check`를 통과했다. 이번 작업에는 Python 코드·테스트·Schema 변경이 없다.
- 선행 전체 회귀 **897 passed / 34 skipped**를 이번에 다시 실행한 것으로 합산하지 않는다.

## 8. 다음 작업과 완료 조건

**원본 동등성 경로 — 원본 자료 대기**

- 대상: Legacy Source 제공 경로. 위 세 후속 프로시저, 그룹 경계/후보 설정, 실제 주문 계산 분기,
  적용 언어/Version을 확보해야 한다. 확보 후 수량·단위·구간·동점까지 고정해야 `LEGACY_REPLAY`라고 부를 수 있다.
- 현재 자료만으로 이 경로의 구현 완료를 선언하지 않는다.

**개발 비교 경로 — 다음 작업 · InventoryEngine**

- `D1~D7`의 개발 규칙·수작업 기대값과 Feature Producer·Class/Adapter 개별 검증은 완료했다.
  2026-10-19 [중립 후보 Catalog의 공통 Guard/PSI 실행](IO_CANDIDATE_CATALOG_PSI.md)도 완료했다.
  후속 [중립 결과의 품목별 선정·평가](IO_CATALOG_POLICY_SELECTION.md)도 완료했다.
  현재 다음은 새 미사용 기간의 방법 간 성과 비교다. 현 Config가 AX~CZ만 받는 경계를 우회하지 않는다.
- 완료 조건: 원 ABC 결과/Hash·운영 Gate를 유지하면서 실제 PSI 검증과 기존 선정 기준을 함께 적용하는 것.
- 이후 동일 후보 예산의 ABC·XYZ/LEGACY_STYLE/넓은 참조를 새 미사용 기간에서 비교한다. 구현과 비교는 직렬이다.
  원본 자료 확보는 개발 규칙 설계와 독립적으로 진행할 수 있지만 개발 결과를 원본 동등성 근거로 올리지 않는다.

방법: `oma-architecture`의 책임/근거 구분을 적용했다. 외부 문헌으로 사내 규칙을 추정하거나
DB 조회·FSN 설정·운영 Gate·실행 Build·배포·Commit·Push를 수행하지 않았다.
