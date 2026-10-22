# 품목별 정책 탐색 업무·실행 계약 V1

기준일: 2026-10-06. 개발 환경의 `MONTHLY_COVER_SEARCH_V1` 프로필이다.
Legacy와 동일한 최종 정책을 재현했다거나 EOQ를 경제적으로 최적화했다고 주장하지 않는다.
기존 Mathematical 기준선과 PPO Shadow의 실행 계약은 변경하지 않는다.
실제 테스트 범위와 수작업 Golden은 [검증 기록](IO_POLICY_SEARCH_VERIFICATION.md)을 따른다.

2026-10-15 구조 보정: [공통 분류·후보 범위 계약](IO_SEGMENTATION_ADAPTER_CONTRACT.md)을
프로세스 내부에 추가하고 기존 ABC·XYZ Class/Adapter를 추출했다. 기존 Config·후보·Snapshot·결과
형식과 Hash는 유지한다. 아래 V1의 `AX~CZ` 필수 조건과 FSN/운영 Gate는 완화하지 않았다.
Legacy 방식과의 독립 비교는 후속 구현이며 현재 계약으로 Legacy 동등성을 주장하지 않는다.

2026-10-15 개발 규칙 정의: [LEGACY_STYLE V1](IO_LEGACY_STYLE_DEVELOPMENT_RULES.md)에
참조 ROP 커버리지·물리 Lead Time·단가 구간과 32개 후보 묶음/88개 정확한 합집합을 고정했다.
이는 별도 Offline 비교용 개발 프로필이며 활성 Config·Legacy 확인값이 아니다.
후속 [Class·Adapter·Feature Producer](IO_LEGACY_STYLE_IMPLEMENTATION.md)는 로컬 구현·개별 검증했으나
아래 기존 V1 실행에 연결하지 않았다.
현 `group_profiles`의 AX~CZ 제한과 기존 Hash/입장 조건은 유지한다. 후속 명시적 후보 Catalog 없이
중립 그룹 또는 88개 비직사각형 합집합을 기존 단일 프로필로 실행할 수 있다고 간주하지 않는다.

2026-10-19 [별도 후보 Catalog·공통 PSI 실행](IO_CANDIDATE_CATALOG_PSI.md)은 구현했다.
기존 `execute()`는 그대로이고 내부 실행기가 별도 검증 Catalog도 받을 수 있다. 기존 ABC PSR 1.0.0의
형식/Hash는 유지하며 새 결과는 `inventory-offline-catalog-psi-result`로 구분한다.
기존 선택기·Platform/Projection 게시 경로에 새 결과를 기존 PSR인 것처럼 전달하지 않는다.

2026-10-19 [MOS·ROP·Q·서비스 지표 대사](IO_POLICY_TERMINOLOGY_RECONCILIATION.md)를 추가했다.
요청 EMOS는 기본 Q 커버리지, 유효 EMOS는 최종 `(S−ROP)/Dm`이며 실제 주문량·Q/2와 구분한다.
Legacy의 `RMOS+EMOS → MOS` 적재식만으로 동일 의미를 확정하지 않는다. 수식·Hash·기존 Gate는 변경하지 않았다.

## 1. 목적과 단계별 책임

1. Segmentation: 품목을 관리 가능한 그룹으로 묶고 후보 정책의 탐색 범위를 정한다.
2. Simulation: 같은 Site의 같은 품목을 여러 정책 후보로 각각 계산한다.
3. Optimization: 서비스 조건을 만족하는 후보 중 비용·재고·발주횟수 등을 비교해 품목별 정책을 선택한다.
4. Order Planning: 선택·승인된 정책 Version에 최신 재고·수요를 적용해 품목별 발주계획을 만든다.
5. Execution: 검토·승인·ERP 연계를 거쳐 발주한다.

탐색 계약은 1~2의 설정·계산·증적을 담당한다. 3의 개발 후보 선정은
[별도 선택 계약](IO_POLICY_SELECTION_CONTRACT.md)으로 구현했다. 운영 승인과 4~5의 실행은 후속 작업이다.
세그먼트는 최종 정책의 Grain이 아니다. 결과 Grain은 `Site + Item + Candidate`다.
그룹을 품목 하나로 취급하거나 그룹 수요를 합산해 품목 정책을 대신 계산하지 않는다.

## 2. Legacy에서 확인된 것과 신규 정의를 분리한다

2026-10-15 [원본 규칙 대사](IO_LEGACY_SEGMENTATION_RULE_RECONCILIATION.md)에서 로컬 Rop Python/R,
제공 Inbound SQL과 보존 DDL을 확인했다. 아래 ROP 확인은 정적 코드 근거이며, 실제 배포 Version이나
Segmentation 구간·후보 생성 원본의 확보를 뜻하지 않는다.

| 항목 | 확인·정의 |
|---|---|
| Legacy ROP 사전 계산 | 로컬 Python/R은 일 수요와 품목 고정 `STOCK_LT`를 이용한 10,000회 재표본추출 분위수 방식. 현재 정규분포 공식 ROP와 구분 |
| Legacy Segmentation 구간·후보 | 그룹별 범위→품목별 Simulation·선택은 업무 설명으로 확인. 활성 인자·실제 구간 경계·RMOS/EMOS 후보 숫자·증분·조합은 원본 미확보 |
| Legacy MOS | 제공된 `SP_DSIO_ENG_IN_SAFETY_STOCK`에서 `RMOS + EMOS`로 적재됨 |
| Legacy RMOS·EMOS 생성식 | 상위 계산 프로시저 미제공. 계산식·단위·EOQ 배부 방식은 미확인 |
| Legacy MAX/ROP | Inbound 변환은 확인됐지만 `LOT_MAX_QTY`의 생성 의미까지 검증한 것은 아님 |
| 신규 RMOS 후보 | 월평균 수요 대비 ROP 커버리지의 하한 후보 |
| 신규 EMOS 후보 | 월평균 수요 대비 기본 발주량 Q의 커버리지 후보 |
| 신규 policy max MOS | 목표 Inventory Position S ÷ 월평균 수요 |
| 신규 policy midpoint MOS | (ROP + (S−ROP)/2) ÷ 월평균 수요. 실제 평균 재고와 구분 |
| 실제 평균 On-hand MOS | PSI의 주말 On-hand 평균 ÷ 월평균 수요. 예약재고를 포함 |

수량인 ROP/Q와 월수인 RMOS/EMOS/MOS는 동일 단위가 아니다.
`ROP + Q/2`는 단순 (s,Q) 모델의 Position 중간점이며 실제 평균 On-hand 재고 공식으로 쓰지 않는다.
실제 평균재고는 PSI에서 관측한다. Q는 탐색하는 발주량이지 경제적 최적 EOQ의 증명값이 아니다.

## 3. 수요와 수량 계산

봉인된 Mathematical 입력의 W0 이전 13주 또는 26주 수요를 고정한다.
누락 주는 0으로 채우지 않는다. 개발 합성 Demand는 Source Type을 유지한다.
미래 Forecast를 과거 수요 이력처럼 사용하지 않는다.

```text
월평균 수요 Dm = 주평균 수요 μ × 52 ÷ 12
보호기간 L    = ceil(Source 또는 유효 SDE 보호 Lead Time ÷ 7)
안전재고      = ceil_UOM(z(CSL) × σ × √L)
ROP           = max(ceil_UOM(Dm × RMOS 후보), ceil_UOM(μ × L + 안전재고))
기본 Q        = ceil_UOM(Dm × EMOS 후보)
기본 S        = ROP + Q
```

`52_WEEKS_PER_YEAR`는 명시적 개발 환산 기준이다. 실제 월별 일수·계절성 기반 MOS와 혼용하지 않는다.
기존 Source·Matrix의 CSL은 운영 비교 기준선이다. 개발 Shadow에서는 별도 CSL 후보를 탐색한다.
VED가 활성화됐으면 승인된 V/E/D 하한을 반드시 적용한다. 후보로 선택해도 기존 운영 CSL을 변경하지 않는다.
실제 업무에 추가 최소 서비스 보장 계약이 있다면 운영 승격 전에 별도의 하한으로 연결해야 한다.
단위 반올림은 기존 Canonical Quantity Rule을 사용한다.
MOQ와 발주배수는 정책 산출이 아닌 실제 주문 제약 단계에서 적용한다.

## 4. V1 정책 종류와 점검주기

| 정책 | 판단·제안 |
|---|---|
| `ROP_FIXED_Q` | 점검일의 Inventory Position이 ROP 이하이면 기본 Q를 주문 제안 |
| `PERIODIC_ORDER_UP_TO` | 점검일의 Position이 S보다 낮으면 S까지 주문 제안 |
| Baseline | 신규 주문 없이 기존 확정 공급과 수요만 반영 |

주기적 목표 S 정책은 R+L 보호기간을 사용한다.
안전재고를 먼저 `SS_H=ceil_UOM(zσ√(L+R))`로 올리고,
`S = max(기본 S, ceil_UOM(μ×(L+R) + SS_H))`로 계산한다. 한 번에만 올림하는 식과 구분한다.
ROP는 이 정책에서 주문 Trigger가 아니라 비교·분해용 값이다.
그 결과 실제 S−ROP가 기본 Q보다 커질 수 있으며 유효 EMOS에 이 보정을 기록한다.

현재 Position은 `가용재고 + 미도착 공급 − 고객 BO`이며 예약재고는 제외한다.
On-hand/MOS·보관비·물리 용량에는 예약재고를 포함한다. `ORDER_UP_TO(S)`의 S는 주문량이 아니라
목표 Position이며 실제 필요량 `max(0, S−Position)`과 MOQ·배수·용량 적용 수량을 별도로 보존한다.

발주 점검주기 R은 1~13주 정수 후보다. W0를 기준으로 R주마다 판단한다.
RMOS/EMOS의 월수와 발주 점검주기의 주수는 별도 필드다.
Source 주문 Calendar가 닫혀 있으면 후보 점검일이라도 주문하지 않는다.
물리 납기일은 Source의 실제 Lead Time으로 결정하며 SDE P90과 점검주기가 물리 납기를 바꾸지 않는다.
납기 Calendar 매핑·미도착 공급·중복 주문 방지는 기존 공통 Guard/PSI를 사용한다.

Legacy 화면의 OUTL/ROP/MIN/B/O 이름을 무조건 신규 정책과 동등하다고 매핑하지 않는다.
기존 HISTORICAL_NORMAL_R_S_V1은 고정 Mathematical 기준선으로 유지하고 별도 탐색 전략으로 실행한다.

## 5. Config 2.1.0

기존 `1.1.0`, `2.0.0` Schema와 Hash 의미를 유지한다.
`policy_search` 블록이 있는 7축 Config만 `2.1.0`으로 저장한다.
Config는 기존 JSONB Revision 저장을 사용하므로 신규 설정 테이블·Migration은 없다.

- 그룹 기본값: 기존 ABC-XYZ Matrix 셀의 서비스수준·점검주기를 초기 후보로 가져온다.
- 품목 예외: 해당 품목의 후보 목록을 전체 대체한다. 사유를 필수 기록한다.
- 미분류 품목: 품목 예외가 있어도 임의 Segment를 부여하지 않는다.
- 명시적 후보 목록: 정책 종류 × RMOS × EMOS × CSL × 점검주기의 Cartesian Product.
- 품목당 최대 256개 후보, 기본 128개. 후보 값은 항목당 최대 16개.
- 동일 숫자의 중복 입력을 차단하고 목록 순서·동일 소수 표기를 정규화한다.
- Configuration Hash·Search Hash·Candidate ID는 시스템에서 생성한다.
- 신규 2.1.0의 기존 축 숫자 필드는 Backend와 같은 타입으로 정규화해 `28`/`28.0` 표현 차이를 제거한다.
  1.1.0/2.0.0의 기존 Hash 의미는 변경하지 않는다.
- 탐색은 기본 꺼짐이며 `DEVELOPMENT_SHADOW`만 지원한다.

품목 예외의 실제 Master 존재 여부는 실행 시 Canonical Universe와 대사한다.
UI 저장 시에는 구조·중복·범위만 검증하며 실제 품목 Coverage를 검증한 것처럼 표시하지 않는다.
현재 UI는 저장 설정 화면이며 Simulation 실행·최적 후보 선택 버튼을 추가하지 않는다.

## 6. 같은 입력으로 품목별 비교

봉인된 Canonical 입력과 Mathematical 수요 이력, 분류 Snapshot·Config Revision을 전달받는다.
분류 Content Hash와 UUID, Scope, Config Hash·Revision, W0 이전 여부를 검증한다.
V2 Replenishment Binding이 있으면 Snapshot ID·개별 유효 정책 Hash도 일치해야 한다.

모든 후보는 같은 Canonical Hash·Prepared Hash를 기록한다.
입력은 한 번 준비하고 각 후보마다 새로운 재고 상태와 미도착 공급 Ledger를 만든다.
후보별 Decision/Order ID는 후보 실행 Hash를 포함하므로 다른 후보와 섞이지 않는다.

- FSN/PLC의 BLOCK은 HOLD로 유지한다.
- REVIEW는 계산·증적을 허용하지만 주문·자동 게시를 허용하지 않는다.
- SDE는 보호기간만 조정하고 HML의 승인 수준은 주문 Evidence에 보존한다.
- Source MOQ·발주배수·용량·발주일·승인 범위를 우회하지 않는다.
- 유효 승인 Override가 있는 품목은 `APPROVED_OVERRIDE_PINNED`로 탐색 제외한다.
- 수요 이력이 부족하거나 평균 0이면 명시적 제외 사유를 남긴다.
- 후보 하나의 계산 실패는 해당 후보 FAILED로 격리하고 다른 후보 결과를 유지한다.

Observation 비용의 상한은 `품목 수 × 품목당 후보 한도 × H(H+1)/2 ≤ 200,000`이다.
이는 대량 Full Load 지원 약속이 아니다. 대규모 실행에는 후속 분할·Job Orchestration이 필요하다.

## 7. 비용·서비스와 선택 기준

비용은 선택적으로 사용자가 명시한 개발 가정이다. 운영 원가나 관세 Source가 아니다.

```text
보관비       = 주말 On-hand 수량 합 × EA·주당 보관비
Backorder비  = 주말 미충족 고객주문 잔량 합 × EA·주당 Backorder비
발주 고정비  = 실제 제약 적용 후 주문 건수 × 품목별 주문 1건 비용
발주 변동비  = 실제 주문수량 × EA당 변동비
총비용       = 위 네 항목의 합
```

`PER_ITEM_ORDER` 배부 가정이다. 혼적 운송·Shipment 공동비 배부, 관세, 회수가능 VAT는 포함하지 않는다.
Landed Cost Child와 연결할 때는 구매·운송비와 변동비의 중복 계산을 별도 차단해야 한다.
Forecast Shortage에는 이 비용 프로필의 별도 Lost-sales 벌점을 부여하지 않는다. 서비스 지표로 평가한다.
비용이 없으면 `COST_PROFILE_NOT_BOUND`, 총액 `null`로 반환한다.

수량 충족률은 현재 고객 확정 수요와 순 Forecast의 동기간 충족량 / 동기간 수요다.
기존 Backorder 충족량을 현재 수요 충족 분자에 섞지 않는다.
이 지표는 CSL이 아니며, `stockout_free_active_bucket_ratio`도 확률적 CSL 보장의 추정값이 아니다.
무수요 기간은 `NO_DEMAND_IN_HORIZON`, 비율 `null`이다.
평균재고는 주말 On-hand 평균으로 정의하며 일평균·시간가중 평균이라고 표현하지 않는다.

개발 선택 계약: 최소 수량 충족률 만족 + 종료 Backorder 0 → 비용 최소 → 평균재고 → 발주횟수 → Candidate ID.
BLOCK/계산 불가 품목은 선택 부적격이다. 비용 미연결·전 후보 실패·무수요는 자동 최적 선정 대상이 아니다.
탐색 결과는 `service_feasible`, `policy_selection_eligible`과 비교값을 산출한다.
원본은 `selected_policies=null`로 유지한다. 별도 선택기가 실제 PSI·제약·지표를 재검증하고
Append-only 선택 결과를 만들지만 승인된 운영 정책 Version은 만들지 않는다.

평가 종료는 유한 Horizon, 종료재고 잔존가치 없음, Horizon 밖 도착 주문 거부다.
짧은 기간에서 발주를 미루는 후보가 유리해지는 편향이 있으므로 운영 정책 확정 전 장기 Holdout·비용 민감도·종료 효과를 검증한다.
탐색 범위 내 우위만 주장하고 전 세계 유일성·전역 최적·향후 실제 성과를 보장하지 않는다.

## 8. 결과와 로컬 보존

결과 JSON은 Baseline 품목별 PSI, 후보별 PSI, 원 제안·제약 적용·사유, 유효 RMOS/EMOS/CSL,
물리/보호 Lead Time, 서비스·평균재고·종료재고·Backorder·발주횟수·비용을 포함한다.
탐색 제외 품목도 Baseline PSI는 보존한다. 과거 평균수요가 미확인이면 MOS는 null로 둔다.
Python과 JSON Schema 모두 성공 후보는 1행 이상, 실패 후보의 행·Artifact는 null로 통일한다.

`inventory-policy-search --request request.json --artifact-root /absolute/existing/local/directory`
또는 `python -m dsio_inventory_engine.entrypoints.policy_search`로 실행한다.
Request의 필드는 `mathematical_request`, `config_values`, `classification_snapshot` 세 개다.

로컬 Writer는 Content-addressed Append-only JSON, 동일 Hash 멱등 저장, 읽기 Hash 재검증을 제공한다.
임시 파일 fsync → 배타적 Link 게시 → 디렉터리 fsync 순서로 저장하며 기존 파일을 덮어쓰지 않는다.
PostgreSQL Evidence, Result Bundle Child, Platform Run Claim 및 Durable Outbox 연결은 후속 작업이다.
UI/API 저장과 Offline CLI 실행을 구현한 것이며 공용 Runtime·웹 화면 배포나 실제 DB E2E를 수행한 상태가 아니다.
