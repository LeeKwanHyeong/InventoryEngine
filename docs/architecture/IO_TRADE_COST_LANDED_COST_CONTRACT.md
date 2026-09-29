# Inventory Trade Cost·Landed Cost 계약 V1

기준일: 2026-09-16. 상태: **계약·Pure Calculator·Migration 077 개발 DB 적용·승인 API·Run Binding·Sealed Projection Runtime 조회 완료**.

이 문서는 InventoryEngine이 향후 `Total Landed Cost`를 PSI·보충 전략·Multi-Echelon 평가에 사용할 때의 입력 신뢰성, 계산 Grain, Component와 차단 규칙을 정의한다. 관세·세금 신고를 자동화하거나 법률·세무 판단을 대신하는 계약은 아니다.

## 1. V1 범위와 결론

V1은 `DSE / C100 / V100 → V101·V102·V103·V104`로 제한한다.

| 도착 Site | 국가 | Network Lane | 현재 판정 |
|---|---|---|---|
| V101 | 일본 | SEA, AIR | 경로·Lead Time은 개발 합성값, 비용 Source 없음 |
| V102 | 중국 | SEA, AIR | 경로·Lead Time은 개발 합성값, 비용 Source 없음 |
| V103 | 사우디아라비아 | SEA, AIR | 경로·Lead Time은 개발 합성값, 비용 Source 없음 |
| V104 | UAE | SEA, AIR | 경로·Lead Time은 개발 합성값, 비용 Source 없음 |

현재 개발 DB만으로는 네 Lane의 운영 Landed Cost를 확정할 수 없다. 대신 Migration 077에 승인된 개발 Fixture Revision Set을 개발 환경의 권위 입력으로 사용해 Landed Cost·Simulation·MEIO 개발 흐름을 검증한다. 2018~2023 판매자료 4,613건의 USD 가격·환율은 수입 구매가격과 신고일 세관환율이 아니므로 Fixture에 자동 전용하거나 운영 Source로 승격하지 않는다.

필드별 판정과 Coverage는 [C100 읽기 전용 Source Audit](evidence/trade-cost-source-audit-c100-20260916.json)에 봉인했다. 확인되지 않은 값은 0이 아니라 `UNVERIFIED` 또는 `NOT_FOUND`다.

## 2. 아키텍처 선택

### 선택 A — 기존 Cost Profile과 Network Lane에 비용을 직접 추가

- 장점: Table과 Binding 수가 적다.
- 단점: 재고 보유비·품절비와 법정 관세·세금의 개정주기가 섞인다. Lane 한 행으로 품목 HS, 원산지, Incoterms, 신고일 환율과 Shipment 고정비를 표현할 수 없다. 과거 신고 Replay와 미래 Planning도 분리되지 않는다.

### 선택 B — Trade Cost Revision과 실행별 Landed Cost Evidence 분리 — 채택

- `dsim.tb_mst_inventory_network*`는 방향·운송수단·Lead Time을 계속 소유한다.
- Trade Cost Source는 별도 Versioned Revision으로 관리한다.
- InventoryEngine은 Run에 봉인된 Source Revision만 읽고 Shipment Line별 Component를 계산한다.
- 실제 계산 결과는 실행별 Append-only `TB_IO_*` Evidence와 Artifact에 저장한다.
- 기존 개발 Cost Profile은 보유비·Backorder·발주비 평가에만 사용한다.

이 분리는 같은 Lane이라도 품목·원산지·적용일·신고조건에 따라 관세가 달라지는 문제와, 세율이 개정돼도 과거 결과가 재현되어야 하는 문제를 동시에 해결한다.

## 3. Source 계약

각 필드는 다음 상태 중 하나를 가진다.

| 상태 | 의미 | 운영 계산 |
|---|---|---|
| `AUTHORITATIVE` | 승인된 업무/공식 Source와 Revision·적용일·Hash가 있음 | 허용 |
| `UNVERIFIED` | 후보 자료는 있으나 Grain·의미·시점 또는 권위가 맞지 않음 | 차단 |
| `DEVELOPMENT_FIXTURE` | 개발 시나리오용으로 명시적으로 생성하거나 승인함 | 계산 가능, 운영 게시 불가 |
| `NOT_FOUND` | 조사 범위에서 Source를 찾지 못함 | 차단 |

필수 Source는 다음과 같다.

- 품목별 HS Version, HS 6자리와 도착국 National Tariff Line
- 제조 원산지, 실제 출하국과 도착국
- Shipment 구매 거래가격, 통화와 수량 UOM
- 품목별 중량·부피와 해당 UOM
- Incoterms, 보험료와 과세가격 가산·공제 항목
- Lane·Mode별 국제운송비, 출발/도착 Handling과 내륙운송비
- 세관이 인정하는 적용일 환율과 Source Revision
- 수입자별 VAT 환급 가능 여부와 적용기간
- 협정별 원산지 기준 충족 결과, 원산지증명서 식별자·유효성

`0`은 결측 대체값이 아니다. 실제 0원 Component는 승인 Source와 `zero_value_reason`을 함께 보존해야 한다.

운영 Source 수집은 [C100 Source Owner Register](evidence/trade-cost-source-owner-register-c100-20260916.json)의
9개 Domain을 따른다. 실제 운영 승격 시 Owner, Source System, 수집 방식·주기, Freshness, 적용기간,
Raw Content Hash, Evidence Reference와 License를 확인한다. 현재 개발에서는 이 Register의 미지정
상태가 차단 조건이 아니며, 명시적으로 승인된 `DEVELOPMENT_FIXTURE` Revision Set을 사용한다.

## 4. 공식 Source Registry

공식 사이트가 존재한다는 사실과 실제 품목에 세율을 적용할 수 있다는 것은 다르다. 다음은 Adapter 후보이며, 현재 Run에는 어느 것도 Snapshot으로 봉인되지 않았다.

| 국가 | 공식 Source 후보 | 계약에 반영할 사항 |
|---|---|---|
| 일본 | [Japan Customs Tariff Schedule](https://www.customs.go.jp/english/tariff/index.htm), [Customs Valuation](https://www.customs.go.jp/english/summary/value_details.htm) | HS 6자리+일본 통계 세번, 적용일 Schedule, 거래가격과 운임·보험 등 가산요소, 수입신고일 환율을 Revision으로 고정 |
| 중국 | [GACC 세율 조회](https://online.customs.gov.cn/ociswebserver/pages/jckspsl/index.html), [2026 관세 조정 공고](https://gss.mof.gov.cn/gzdt/zhengcefabu/202512/t20251229_3980625.htm), [China Tax System](https://www.chinatax.gov.cn/eng/c102962/c102968/c102969/c5245967/content.html) | MFN·일반·잠정·협정세율과 10자리 신고세번, 적용일, 수입 VAT·소비세를 별도 Component로 고정 |
| 사우디아라비아 | [ZATCA 12자리 GCC 통합세율](https://zatca.gov.sa/en/RulesRegulations/Taxes/Pages/Integrated-Tarrifs.aspx), [ZATCA Customs Valuation](https://zatca.gov.sa/en/RulesRegulations/Taxes/Pages/customs-bussiness/basics-of-customs-value.aspx) | 12자리 품목번호, 거래가격 우선 평가, 관할기관 고시 환율과 수입시점, 수입 VAT 회수 증빙을 고정 |
| UAE | [UAE Customs Clearance](https://u.ae/en/information-and-services/finance-and-investment/clearing-the-customs-and-paying-customs-duty), [FTA VAT Return Guide](https://tax.gov.ae/-/media/Files/EN/PDF/Guides/VAT-Returns-User-Guide.pdf) | GCC 세율, 가격+운임+보험 기반 과세가격, 관세·소비세를 포함한 수입 VAT Base, 등록 수입자의 회수 가능 여부를 고정 |
| 한국 수출 원산지 | [Korea Customs Service Certificate of Origin](https://www.customs.go.kr/engportal/cm/cntnts/cntntsView.do?cntntsId=2339&mi=7320) | 중국 FTA, RCEP, UAE CEPA 등 협정별 증명 방식·HS Version·품목별 원산지결정기준과 증명서 진위를 Shipment에 연결 |

공식 관세 조회 화면도 참고자료일 수 있다. 운영 `AUTHORITATIVE` 승격에는 다운로드 시각, 적용일, 원문 Revision/공고번호, Raw Hash와 승인자가 필요하다. 관세사 확인값은 `BROKER_VERIFIED` 근거로 별도 관리한다.

## 5. 계산 Grain과 식별자

업무 Grain은 `Shipment + Lane + Item + 적용일`이다. 같은 Shipment에 동일 Item이 둘 이상의 Invoice Line으로 존재할 수 있으므로 물리 Key에는 `shipment_line_id`를 추가한다.

```text
shipment_id
+ shipment_line_id
+ lane_id
+ item_id
+ valuation_date
```

이 Grain에 `source_revision_set_hash`와 `judgment_at`을 봉인한다. 판단 이후 Source가 바뀌면 기존 결과를 수정하지 않고 새 Assessment/Run을 생성한다.

## 6. Component와 합계 계약

Landed Cost는 하나의 금액 컬럼이 아니라 Component 원장으로 계산한다.

```text
Customs Value
= Customs Value에 포함된 Component의 합

Gross Landed Cost
= 거래가격 + 운송·보험·Handling + 관세·세금 + 통관·내륙 비용

Net Landed Cost
= Gross Landed Cost - 회수 가능한 수입 VAT
```

각 Component는 금액, Source 상태·Reference, 과세가격 포함 여부, Gross 포함 여부와 회수 가능 여부를 가진다. `customs_value_amount`, `gross_landed_cost_amount`, `recoverable_tax_amount`, `net_landed_cost_amount`는 Component 합과 정확히 일치해야 한다.

관세 Rule은 다음 형태를 모두 표현해야 한다.

- 종가세: `customs_value × rate`
- 종량세: `quantity × unit_duty`
- 복합세: 종가세와 종량세의 합 또는 법정 선택 규칙
- 최소·최대 관세: Rule에 명시된 Floor/Cap

국가별 가산항목과 세금 Base는 공통 Formula로 추측하지 않는다. 공식 Rule Adapter가 포함 여부를 Component Flag로 반환하고, 공통 계약은 합계와 Evidence만 검증한다.

## 7. 고정비 배부·환율·반올림

Shipment 고정비의 기본 배부 기준은 `CUSTOMS_VALUE`다. 승인된 Source가 있을 때만 `WEIGHT`, `VOLUME`, `QUANTITY`를 사용할 수 있다.

- 배부 전 Shipment 총액과 배부 후 Line 합은 통화 최소단위까지 일치해야 한다.
- 잔여 최소단위는 `(shipment_line_id, item_id)` 안정 정렬 순서로 배부한다.
- 중량·부피 Source가 없으면 해당 배부 방식을 선택할 수 없다.

환율은 일반 Spot 환율이 아니라 관할 세관이 해당 신고·평가일에 인정하는 Source를 우선한다. Currency Pair, 적용일, Rate, Source Revision과 Raw Hash를 봉인한다. 역환산은 별도 Rate로 추측하지 않는다.

계산 내부는 Decimal을 사용하고 반올림은 Rule의 `mode`, `currency_scale`, `application_stage`를 따른다. 국가별 법정 반올림이 있으면 `LEGAL_RULE` 단계에서 적용하고 Reference를 남긴다. 단순 최종 표시 반올림으로 법정 계산을 대체하지 않는다.

## 8. MFN·FTA·Broker·실제 신고 Replay 선택

이를 하나의 전역 우선순위로 섞지 않는다.

| 실행 목적 | 선택 규칙 |
|---|---|
| 과거 신고 Replay | 해당 Shipment의 실제 신고 Component와 세번·환율을 `ACTUAL_DECLARATION_REPLAY`로 사용. 미래 권고에 자동 전용하지 않음 |
| 미래 운영 Planning | 적용기간·품목·Lane을 확인한 `BROKER_VERIFIED` Rule이 있으면 사용. 없으면 공식 Schedule을 사용 |
| 협정세율 | 제조 원산지, 품목별 원산지 기준과 유효 원산지증명서가 모두 확인될 때만 `PREFERENTIAL` |
| 증명 미확인 | 협정세율을 가정하지 않고 공식 `MFN` Scenario를 계산. 분류·원산지 자체가 미확인이면 계산 차단 |

`PREFERENTIAL`은 단순히 한국에서 출하했다는 이유로 선택할 수 없다. 제조 원산지와 FTA 원산지는 별도 Evidence다.

## 9. 상태와 Fail-closed 규칙

| 상태 | 대표 조건 | 금액 결과 |
|---|---|---|
| `CALCULABLE` | 분류·원산지·환율·세금 회수성·Component Source가 검증됨 | Component와 총액 생성 |
| `UNVERIFIED_CLASSIFICATION` | HS Version/국가 세번 미확인 | 총액 없음 |
| `UNVERIFIED_ORIGIN` | 제조 원산지 미확인 | 총액 없음 |
| `UNVERIFIED_TAX_RECOVERABILITY` | 수입 VAT의 Net Cost 포함 여부 미확인 | 총액 없음 |
| `MISSING_EXCHANGE_RATE` | 관할 세관 적용일 환율 없음 | 총액 없음 |
| `NOT_CALCULABLE` | 거래가격·운송비·배부 Source 등 그 밖의 필수값 미확인 | 총액 없음 |

`DEVELOPMENT_FIXTURE`는 `CALCULABLE`한 개발 Scenario를 만들 수 있지만 `operational_eligible=false`다. `OPERATIONAL` 목적의 결과에는 모든 Component가 `AUTHORITATIVE`여야 한다.

## 10. 구현된 계약과 아직 하지 않은 것

구현 완료:

- `inventory_contracts.trade_cost`의 Source Catalog와 Landed Cost Assessment 검증·Hash
- `inventory_contracts.trade_cost_sources`의 Source Owner·수집·Raw Evidence 승인 Manifest
- `calculate_landed_cost`의 Decimal 기반 종가·종량·복합세·Floor/Cap 계산과 고정비 배부
- JSON Schema 3종
- Component 합계, 회수 가능 세금, FTA Evidence, 실제 신고 Replay, 0원 Evidence와 운영 적격성 Fail-closed 검증
- C100 개발 PostgreSQL 읽기 전용 Source Audit Evidence
- 일본·중국·사우디아라비아·UAE 합성 개발 Golden과 결정론적 Result Hash
- dsai-platform Migration 077 개발 DB 적용, Versioned Master·Revision Set·실행 Evidence
- Source Revision/Revision Set 승인·Supersede CAS Repository/API
- Planning Cycle Claim의 Backend-resolved `TRADE_COST_REVISION_SET` Binding
- C100/V100 개발 Source Revision 27개와 승인 Revision Set
- dsai-platform Repeatable Read 기반 Sealed Revision Set Projection 조회 API
- InventoryEngine의 Source·Set·Projection Hash, Scope·적용일·환경 재검증 Runtime Resolver

미구현:

- 국가별 Tariff/Customs/Tax Collector와 승인 Workflow
- 거래가격·원산지·중량·Incoterms·물류 계약·관세사 Source Adapter
- 실제 공식 Rule Collector와 국가별 법정 세액 Adapter
- Landed Cost Child Artifact·Append-only 게시와 개발 Simulation/MEIO 비용 연결

공용 Runtime 배포와 운영 Source 승격은 별도 승인 범위다.

## 11. 다음 작업 순서

### 개발 Fixture 권위와 운영 Gate — 완료

- 승인 개발 Revision Set은 `development_eligible=true`, `operational_eligible=false`로 사용한다.
- 개발 Landed Cost·Simulation·MEIO 입력을 허용하고 운영 자동 게시·발주는 차단한다.
- 실제 Owner와 공식 Source Collector는 운영 전환 범위로 이관한다.

### Versioned 물리 모델과 Pure Calculator — 완료(로컬 초안)

- Source Domain별 Revision과 승인 Revision Set, 실행별 Assessment/Component Projection을 설계했다.
- Decimal Calculator와 네 국가 합성 Golden을 구현했다. Golden Rate는 법정 세율이 아니다.
- Shipment 고정비 배부 보존식과 종가·종량·복합세·Floor/Cap을 검증했다.

### Repository·승인 API·Run Binding — 완료

- Migration 077을 개발 PostgreSQL에 적용하고 Parser·Repository·Guard 계약을 검증했다.
- Source Revision과 Revision Set의 승인·Supersede CAS를 구현했다.
- Planning Cycle Claim에 승인 `revision_set_id + content_hash`를 Backend가 고정한다.

### Sealed Projection·Runtime Resolver — 완료

- Run Binding의 Set ID·Hash로 27개 Source Projection을 읽는다.
- Source·Set·Projection Hash와 Scope·적용일·환경을 InventoryEngine에서 재검증한다.
- 개발 Fixture 결과는 개발 Scenario와 MEIO 입력에 사용하되 운영 적격성은 부여하지 않는다.

### Landed Cost Child Artifact와 Simulation·MEIO 비용 연결 — 다음 작업

- 봉인 Projection을 Pure Calculator 입력 DTO로 변환한다.
- Shipment+Lane+Item+적용일별 Component와 총액을 Append-only Child Artifact로 보존한다.
- 개발 Simulation·MEIO는 `development_eligible=true` 결과만 소비하고 운영 게시·발주는 계속 차단한다.
