# LEGACY_STYLE 개발 규칙 V1

규칙 정의일: 2026-10-15. 2026-10-19 현재 **규칙·Class·Adapter·Feature Producer·후보 PSI 로컬 연결 완료 / 운영 연결 미구현**.
프로필 `LEGACY_STYLE_DEVELOPMENT_V1`, 규칙 Version `1.0.0`을 개발 비교의 첫 기준으로 고정한다.
원본 동등성·성능 우월성·운영 승인을 뜻하지 않는다. FSN 설정·운영 Gate·DB는 변경하지 않는다.

## 1. 확인한 Legacy와 이번 개발 정의

원본 근거는 [Legacy 규칙 대사](IO_LEGACY_SEGMENTATION_RULE_RECONCILIATION.md)와
[근거 Register](evidence/legacy-segmentation-rule-reconciliation-20261015.json)를 따른다.

| 항목 | 원본에서 확인한 범위 | 이번 개발 결정 |
|---|---|---|
| 그룹의 역할 | 업무 설명상 인자 구간으로 그룹을 만들고 그룹별 탐색 범위를 품목별로 적용 | 동일한 책임 분리. 그룹 수요를 합산하지 않고 품목별 Simulation·선택 |
| 활성 인자·경계 | ROP·Lead Time·단가 등이 업무 자료에 등장하나 실제 활성 목록·구간표는 미확보 | ROP 커버리지·물리 Lead Time·단가 3개, 아래 고정 구간 사용 |
| ROP | Python/R의 일 수요 Bootstrap 10,000회와 서비스수준별 분위수 계산은 코드로 확인 | 기존 정규분포 수학식을 **분류용 참조값**으로 사용. Legacy Bootstrap 재현 아님 |
| MOS | 제공 SQL의 `RMOS + EMOS → MOS` 적재만 확인 | 기존 `MONTHLY_COVER_SEARCH_V1`의 RMOS·EMOS 의미 유지 |
| 후보·선정 | 그룹 범위 → 품목별 후보 Simulation·선택은 업무 설명으로 확인 | 3개 후보 묶음·묶음당 32개 요청 조합과 기존 선택기 사용 |
| 품목 단독 그룹 | 업무 설명으로 확인. 원본 예외/승인 규칙은 미확인 | 기간·사유·Revision이 있는 개발 범위 예외만 정의 |

**이 문서의 인자 선택, 모든 숫자·단위 환산·구간·점수·후보 목록은 `DEVELOPMENT_ASSUMPTION`이다.**
원본 미확인 항목을 Legacy 기본값으로 승격하지 않는다. 원본이 확보되면 별도 `LEGACY_REPLAY` 경로로 대사한다.

고정 구간은 작은 실험에서 사람이 경계와 결과를 추적하기 쉽다는 이유로 선택했다.
TRAIN 분위수 구간·군집 학습은 자료 의존성까지 함께 바뀌므로 후속 비교 대상으로 분리한다.
원본을 기다리는 경로도 유지하되 개발용 분류의 정의를 막지는 않는다.

## 2. 입력 Grain·판단 시점과 근거

- Grain은 `company_cd + subs_cd + site_cd + item_id`다. 대상 Universe와 원 ABC·XYZ 분류를 보존한다.
- 판단 주차 W0·공유 Calendar·Master Revision·`master_as_of_date`·입력 Cut-off를 전달받는다.
  최신 마감/단가를 임의 검색하거나 다른 Scope로 대체하지 않는다.
- 이력은 W0 직전까지의 **연속된 마감 26주**다. 실제 수요 또는 개발 합성 수요임을 구분한다.
  수요 의미는 `UNCENSORED_DEMAND`이며 판매 출고량만 있으면 수요로 자동 간주하지 않는다.
- 가격은 USD/EA 하나로 제한한다. 가격 기준일은 `master_as_of_date` 이하여야 하며 경과기간은 91일 이하다.
  Source의 `available_at`도 입력 Cut-off 이하여야 한다. 환율 Source가 없으면 자동 환산하지 않는다.
- Source는 `OBSERVED_PINNED` 또는 생성기 Version·Seed/입력 Revision을 가진 `SYNTHETIC_PINNED`를 허용한다.
  원문 ID·Revision·Content Hash를 검증한다. 미확인 Source를 합성/실제로 이름만 바꾸지 않는다.
- 합성 자료를 쓰는 데 운영 Source Owner 승인 절차를 추가하지 않는다. 단, 개발 자료라는 표시는 유지하며
  기존 FSN/HML/PLC 상태나 Run 운영 적격성을 올리는 근거로 사용하지 않는다.
- BOH·미래 Forecast·미래 실적·후보의 평가 성과·승인 Override 결과는 이 3개 분류 인자의 입력이 아니다.

Feature Snapshot은 위 Scope/시점/Source Binding과 품목별 원값·단위·유효성·참조 ROP 계산 근거를
보존한다. [로컬 Producer·Class 구현 계약](IO_LEGACY_STYLE_IMPLEMENTATION.md)에 입력 형식과 Hash 경계를 기록했다.
이는 순수 메모리 문서 생성이며 실제 Source 수집·봉인 HTTP API·DB 게시를 구현한 것은 아니다.

## 3. D1·D2·D6 — 인자와 구간

### 3.1. 분류용 참조 ROP

```text
μ = 26주 수요 평균
σ = 26주 수요의 모집단 표준편차(ddof=0)
L = ceil(물리 Source Lead Time 일수 / 7)
z = NormalDist.inv_cdf(0.95), 소수 12자리 ROUND_HALF_EVEN
SS_ref  = ceil_EA(z × σ × √L)
ROP_ref = ceil_EA(μ × L + SS_ref)
월평균 수요 Dm = μ × 52 / 12
ROP 커버리지 = ROP_ref / Dm
```

기존 `HISTORICAL_NORMAL_R_S_V1`의 참조 서비스수준 95%, 참조 점검주기 1주를 사용한다.
점검주기는 이 Feature의 ROP에 영향을 주지 않는다. 계산은 Decimal 정밀도 40·HALF_EVEN을 사용하고,
SS를 먼저 EA 정수로 올린 후 ROP를 올린다. 전체 식을 한 번만 올리는 방식과 혼용하지 않는다.
참조 계산의 EA 정수화는 기존 Forecast·PSI의 소수 EA/Quantity Rule을 변경하지 않는다.
SDE 보호 Lead Time이나 승인/Legacy Fallback을 이 Feature에 넣지 않는다.

구간 비교는 나눗셈 반올림 대신 다음 비율의 **교차 곱**을 사용한다.
`분자 = ROP_ref × 26 × 12`, `분모 = 26주 수요 합 × 52`.
평균 0은 미분류이며 임의 0개월이나 무한대 그룹으로 만들지 않는다.

### 3.2. 경계표

| 인자 | 낮음 / 점수 0 | 중간 / 점수 1 | 높음 / 점수 2 |
|---|---|---|---|
| ROP 커버리지(개월) | `R1: [0, 0.5)` | `R2: [0.5, 1.5)` | `R3: [1.5, +∞)` |
| 물리 Lead Time(일) | `L1: [0, 14)` | `L2: [14, 42)` | `L3: [42, 3660]` |
| 단가(USD/EA) | `P1: (0, 10)` | `P2: [10, 100)` | `P3: [100, 10^12]` |

Lead Time은 0~3660의 정수다. 명시된 `0일`은 유효하며 누락과 구분한다.
단가는 양수·유한값이어야 한다. 음수 수요, NaN/Infinity, bool을 숫자로 사용, 소수/음수/상한 초과 Lead Time은 거부한다.
단가는 그룹만 바꾸며 실제 보관비나 HML 승인 수준을 자동 산출하지 않는다.

## 4. D3 — 결합 그룹과 품목 예외

최대 27개 그룹을 `LS1:R{1~3}-L{1~3}-P{1~3}`로 표시한다. 표본 수가 적어도 자동 병합하지 않는다.
그룹의 후보 묶음은 다음 점수로 결정한다.

```text
score = ROP 구간 점수 + Lead Time 구간 점수 − 단가 구간 점수
score ≤ 0   → SMALL
score 1~2   → MEDIUM
score ≥ 3   → LARGE
```

저가·긴 보호기간에는 큰 Q 후보를, 고가에는 작은 Q 후보를 더 살펴보는 **미보정 개발 가설**이다.
ROP에 이미 Lead Time이 포함되므로 두 인자가 중복 가중될 위험이 있다. 후속 인자 제거 실험으로 확인한다.
그룹 배정이 해당 묶음의 우월성이나 운영 적격을 증명하지 않는다.

품목 예외는 다음과 같이 제한한다.

- 동일 Scope 품목의 SMALL/MEDIUM/LARGE 중 하나로 범위를 대체한다. 후보 수는 32개로 유지한다.
- 사유·Source Revision·적용기간 `[effective_from, effective_to)`가 필수다. 기본 예외 목록은 빈 목록이다.
- 활성 예외가 중복되거나 존재하지 않는 품목을 가리키면 Run Binding 오류로 차단한다.
- 별도 품목 그룹 ID는 `LS1:ITEM:{SHA256(canonical_json([company,subs,site,item]))}`로 만들고 원 인자 그룹도 보존한다.
- 누락 인자·평균 0·ABC 미분류·FSN BLOCK을 구제하지 않는다. 새 운영 Override 승인 기능이 아니다.
  기존 승인 업무 Override는 계속 우선하며 기존 `APPROVED_OVERRIDE_PINNED` 탐색 제외를 유지한다.

## 5. D4 — 공통 후보 묶음

두 정책 `ROP_FIXED_Q`, `PERIODIC_ORDER_UP_TO`를 사용한다. 아래 각 목록을 곱해 **32개 요청 조합**을 만든다.

| 묶음 | RMOS(개월) | EMOS(개월) | CSL 후보 | 점검주기(주) | 조합 수 |
|---|---|---|---|---|---|
| SMALL | 0, 0.5 | 0.25, 0.5 | 0.95, 0.99 | 1, 2 | 32 |
| MEDIUM | 0, 1.5 | 0.5, 1 | 0.95, 0.99 | 1, 3 | 32 |
| LARGE | 0, 3 | 1, 2 | 0.95, 0.99 | 1, 4 | 32 |

RMOS는 월수요 대비 ROP 하한, EMOS는 기본 Q의 월 커버리지다. EMOS를 Q/2로 해석하지 않는다.
`ROP=max(공식 ROP, ceil_UOM(Dm×RMOS))`, `Q=ceil_UOM(Dm×EMOS)`, 기본 `S=ROP+Q`를 유지한다.
주기적 S 정책의 R+L 보호기간·VED 서비스 하한·Source 제약·주문 Guard는 기존
[탐색 계약](IO_POLICY_SEARCH_CONTRACT.md)을 따른다. 분류용 ROP_ref가 실제 후보 ROP를 대체하지 않는다.
VED 하한/수량 반올림/제약에 따라 여러 요청 후보가 같은 유효 정책이 되거나 탈락할 수 있다.
32개는 서로 다른 **요청 파라미터 수**이지 실행 가능한 정책 32개나 최종 유효 정책 32개를 보장하지 않는다.

넓은 참조 목록은 위 세 묶음의 **정확한 합집합 88개**로 한정한다.
원 전개 96개에서 SMALL∩MEDIUM 4개·MEDIUM∩LARGE 4개를 제거하며 SMALL∩LARGE는 없다.
중복 Key는 `(정책 종류, RMOS, EMOS, CSL, 점검주기)`이며 기존 Candidate ID·동점 순서를 대체하지 않는다.
각 열의 합집합을 다시 Cartesian Product로 만들면 256개가 된다. 이것을 88개 참조 목록으로 잘못 취급하지 않는다.

## 6. D7 — 누락·오류 처리

| 상황 | 결과 |
|---|---|
| 규칙/Source Hash·Scope·W0 불일치, 중복/고아 품목, 잘못된/중복 예외 | Run 전체 입력 거부. 다른 Revision으로 우회하지 않음 |
| 이력 주차 누락·인자 없음·미검증 Source·단위/통화 불일치·미래/오래된 단가 | 품목 `UNCLASSIFIED`, 원인 보존. 임의 채움/환산/기본 묶음 없음 |
| 이력 평균 0 | `LS_ZERO_MEAN_DEMAND`. 미래 Forecast로 보충하지 않음 |
| 단가 0 이하 | `LS_NON_POSITIVE_UNIT_COST`. 무료 품목으로 간주하지 않음 |
| 유효한 이력 평균·가격과 명시적 Lead Time 0 | 정상 분류 가능. 누락 처리하지 않음 |
| 과거 인자는 유효하나 평가기간 수요 0 | 그룹은 유지. 기존 선택 단계의 `NO_DEMAND_IN_HORIZON`, 서비스 비율 null·미선정 |

품목별 모든 원인을 보존하고 대표 사유는 아래 순서로 고정한다.

```text
INVALID_FEATURE_VALUE → MISSING_REQUIRED_FACTOR → HISTORY_INCOMPLETE
→ SOURCE_UNVERIFIED → UNSUPPORTED_UOM → CURRENCY_MISMATCH
→ FUTURE_OR_STALE_FEATURE → NON_POSITIVE_UNIT_COST → ZERO_MEAN_DEMAND
→ REFERENCE_ROP_UNAVAILABLE
```

실제 코드는 각 이름 앞에 `LS_`를 붙인다. 비정상 숫자는 INVALID, 0 이하 가격은 NON_POSITIVE로 구분한다.
미분류의 그룹·후보 묶음은 null이고 Baseline과 원 근거는 남긴다.
ABC 미분류 3품목을 CZ로 바꾸거나 운영 Claim 입장을 허용하는 결정이 아니다.

## 7. D5 — 비교·선정과 실행 예산

- 새로운 별도 Offline 비교 프로필을 만든다. 기존 ABC 설정·ML 실험 프로필·과거 결과를 수정하지 않는다.
- 동일 3개 후보 묶음을 ABC에서도 사용한다. 개발 비교용으로 각 A/B/C 안의 X→SMALL, Y→MEDIUM,
  Z→LARGE를 적용한다. 기존 운영 Matrix의 보편적 의미라고 주장하지 않는다.
- ABC 32개 / LEGACY_STYLE 32개 / 넓은 참조 88개를 같은 Source·Prepared Hash·Gate·비용·기간·종료조건으로 비교한다.
- 최소 Fill Rate 0.95·종료 Backorder 0을 평가 조건으로 두고 기존 비용→재고→발주횟수→Candidate ID 선택을 유지한다.
  후보 CSL 0.95/0.99와 평가 Fill Rate는 서로 다른 지표이며 서비스 보장으로 바꾸지 않는다.
- 비용 미연결은 `COST_PROFILE_NOT_BOUND`·총액 null이며 비용상 최적 후보를 임의 선정하지 않는다.
  비용 민감도는 같은 별도 Scenario로 계산하며 그룹 인자에 섞지 않는다.
- Planning Forecast에서 선택한 정책을 고정한 뒤 미사용 기간 실적을 Replay한다. 실적을 보고 재선택하지 않는다.
  실적 사후 최선은 진단용으로만 표시한다. 이전 ML 실험의 Holdout은 새 미사용 자료로 재사용할 수 없다.
- 서비스·비용·평균/종료재고·발주횟수·실행시간·좋은 후보 누락과 미분류/미선정 비율을 함께 보존한다.
  여기서 최선은 **유한한 88개 요청 집합 안에서의 참조값**이며 전역 최적해가 아니다.
  좋은 후보 누락은 넓은 목록의 서비스 충족·최저 비용/재고/발주횟수 동점 집합이 좁은 목록에 하나도 없는 경우로 센다.
  이 대사는 의미상 파라미터 Key로 하고 그룹별 Candidate ID 차이만으로 누락이라 판단하지 않는다.
  최종 선택의 기존 Candidate ID 동점 규칙은 유지하며, 비용 미연결/참조 미선정은 누락률 분모와 별도 상태로 보고한다.
- 기존 계산 상한 `전체 Universe 품목 수 × 후보 수 × H(H+1)/2 ≤ 200,000`을 유지한다.
  첫 비교는 최대 8품목·13주, 넓은 목록 기준 `8×88×91=64,064`로 제한한다.
  기간을 늘릴 때는 동일 조건의 독립 품목 묶음으로 나누며, 품목 하나도 상한을 넘으면 차단한다.
  평가기간/후보를 조용히 줄이지 않는다. 대량 Full Load/분할 실행 기능을 구현했다는 뜻은 아니다.

## 8. Hash·Adapter·Runtime 경계

규칙 원문은 [기계 판독 프로필](data/legacy-style-development-rules-v1.json), 기대값은
[수작업 Golden](data/legacy-style-development-rules-golden-v1.json)에 고정한다.
규칙 `profile`만 UTF-8·정렬 Key·공백 없는 JSON으로 정규화해 SHA-256을 계산하며
현재 Hash는 `a3121dfa885f8b4d2e0df54605789ad841661693b0f386329fe4fa60946ecbae`다.
상태를 담은 바깥 Wrapper는 규칙 Hash에 포함하지 않는다. 이 파일은 등록된 실행 JSON Schema가 아니다.

로컬 구현은 다음 Binding을 새 내부 결과에 보존한다.

1. 기존 ABC 분류 Snapshot ID/Hash·Config Hash는 그대로 보존.
2. 규칙 Profile Hash와 정확한 Feature Snapshot ID/Hash를 별도로 고정.
3. 새 내부 `configuration_hash`는 `{base_configuration_hash, rules_profile_hash, item_exceptions_hash}`의 정규화 Hash로 분리.
4. Feature Source Binding은 Scope/W0/Cut-off·Calendar/Master·참조 계산 구현/Build 고정값까지 묶음.
5. 각 품목의 원 인자·구간·점수·범위/예외·모든 실패 사유를 별도 근거로 보존.

[공통 내부 계약](IO_SEGMENTATION_ADAPTER_CONTRACT.md)은 중립 그룹과 후보 범위를 표현할 수 있다.
그러나 **현 Config 2.1의 `group_profiles`와 실행 Adapter는 여전히 AX~CZ 전용**이며, 비직사각형인
88개 합집합도 단일 기존 프로필로 전달할 수 없다.
[명시적 Offline 후보 Catalog](IO_CANDIDATE_CATALOG_PSI.md)는 중립 그룹/범위를 받아 공통 PSI/Guard로 연결했다.
Legacy 그룹을 AX/CZ로 위장하거나 승인 분류를 새로 봉인해 승인된 것처럼 전달하지 않는다.
현재 Runtime·Run Plan·기존 Snapshot Hash 투영은 변경하지 않는다.

## 9. 완료 범위와 다음 작업

수작업 Golden은 27개 그룹 배정·15개 경계·7개 참조 ROP 예제와 누락/오류 12개·Gate 3개 기대 사양이다.
양수 일정 수요에서 LT 7일은 ROP 10/R1, LT 49일은 ROP 70/R3이고 고가에 따라 묶음만 달라진다.
참조 함수·숫자/후보 구조 검증 결과는 [개발 기준선](IO_DEVELOPMENT_BASELINE.md)에 기록한다.
현재 Producer·Class에서 위 Golden을 실행했다. BLOCK/REVIEW는 기존 실제 정책 탐색 경로의 전후
Hash/주문 제한으로 검증한다. 후속 [새 합성 Holdout 비교](IO_CATALOG_HOLDOUT_COMPARISON.md)는 완료했지만,
운영 Source의 성과와 Legacy 원 엔진 동등성 비교는 수행하지 않았다.

**현재 기준선 — 중립 후보 선정·평가 및 새 합성 Holdout 비교 완료 · InventoryEngine**

- Feature Producer·`LegacyFactorSegmentation`·중립/범위 Adapter·공통 PSI와 별도 Catalog 선정/평가를 완료했다.
  원 ABC 계약/Hash와 이 개발 규칙을 바꾸지 않고 32/32/88개를 새 합성 2창에서 비교했다.
- [간헐수요·장기 LT의 강건성 조건](IO_CATALOG_ROBUSTNESS_CONTRACT.md)은 고정했다. 전용 평가기·Producer·분할 검증은 다음 구현 대상이다.
  이번 결과에 맞춰 기존 구간·Seed·Holdout을 튜닝하지 않는다.
- 원본 자료 수집은 별도 병행 가능하며, 원본 확보가 새 개발 규칙을 Legacy 확인값으로 바꾸지는 않는다.

DB/DDL·Config 활성화·운영 Claim·실제 E2E Write·Runtime 배포·Commit/Push는 이 작업에 포함하지 않는다.
`oma-architecture`의 대안 비교·책임 경계·결정 근거 구분을 적용했으며 독립 에이전트 검토는 수행하지 않았다.
