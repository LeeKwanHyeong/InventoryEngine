# Landed Cost Child Artifact 개발 계약

기준일: 2026-09-29. 계약 Version `1.0.0`, InventoryEngine `inventory_engine_dev`의 로컬 구현.

## 현재 기준선 — 완료(Offline)

승인·봉인된 Trade Cost Projection과 Shipment 입력을 기존 Decimal Calculator에 연결했다.
Shipment 원본, Projection 원본, 계산 Child를 동일 로컬 SQLite Transaction에서 Append-only
저장하고 원본으로 다시 계산해 검증한다. 실제 PostgreSQL 적재나 Runtime 배포는 하지 않았다.

이 Child는 기존 `inventory-result-bundle-v1`의 **PSI Child가 아니다**. 기존 PSI의
`result_children`와 서비스·Backorder 지표에 추가하지 않는다. 동일 Run의 별도 비용 증적이며,
Parent Bundle·Runtime Pipeline·개발 Simulation 비용에 연결하는 작업은 다음 단계다.

## 1. 봉인 입력

- Shipment 계약: `io-landed-cost-shipment-input-v1` / `1.0.0`.
- Scope: C100, Origin V100, Destination V101/JP·V102/CN·V103/SA·V104/AE.
- 목적: `DEVELOPMENT_FIXTURE`만 허용한다. 외부 Source Owner 승인을 기다리지 않고 개발할 수
  있지만, 운영 권위 자료나 운영 MEIO 합격으로 해석하지 않는다.
- Run Binding: `engine_run_id`, `attempt_no`, Tenant/Project/Company/Subsidiary/Origin,
  Canonical Input Hash, Revision Set ID/Hash, Network Revision ID, 적용일, 판단 시점.
- Shipment: Shipment/Lane/Line/Item, EA 수량, 거래금액·통화, 적용 통화·반올림, 비용 Source와
  Customs Value 포함 여부. 선언하지 않은 필드는 거부하고 Decimal은 문자열로 전달한다.
- 거래금액 기준은 `GOODS_ONLY_EXCLUDING_ADDITIONAL_CHARGES`로 고정한다. 이미 운송비가
  포함된 Invoice를 그대로 넣고 다시 운송비를 더하면 안 된다. Incoterms 자동 해석은 미구현이다.
- 비용 Component는 금액 Source에 연결하거나 `charge_exemptions`에 미적용 사유와 근거를
  명시한다. Source가 빠졌다는 이유로 0으로 바꾸지 않는다. 명시적인 Source 0은 허용한다.
- 입력 생성은 Backend/Adapter의 책임이다. 사용자가 Snapshot Hash를 직접 입력하는 UI를
  추가하지 않았다. Writer 자체는 Platform Claim을 조회하지 않으므로, 향후 Runtime Resolver가
  실제 Claim과 Shipment Binding을 연결해야 한다.

## 2. Source 해소와 계산

`calculate_projection_shipment()`는 Projection의 Source·Set·Projection Hash를 다시 검증하고
Shipment Binding의 Scope·Set Hash·적용일을 대조한다. 계산 중 최신 Source를 조회하지 않는다.

| Source Domain | 선택 기준 |
|---|---|
| ITEM_CLASSIFICATION | Item + 목적국 + 유효기간 |
| ITEM_ORIGIN | Item + KR Source + 유효기간 |
| CUSTOMS_TARIFF | 목적국 + HS Version/Code + 제조 원산지 + 명시적인 MFN + 유효기간 |
| CUSTOMS_FX | 목적국 + From/To 통화 + 적용일 이하의 최근 확정일 |
| IMPORT_TAX_PROFILE | Importer Site + 목적국 + 유효기간 |
| LANE_CHARGE | Network Revision + Lane + 목적국 + 유효기간 |
| ITEM_PHYSICAL_ATTRIBUTE | 중량/부피 배부 시 Item + KR Source + KG/M3 단위 |

Source Record의 Revision·Domain·Document 연결, 필수 필드와 Document 적용기간을 확인한다.
판단 시점 이후 수집·공표된 Document는 사용하지 않는다. 미래 환율은 선택하지 않으며,
동일 기준에 적용 가능한 중복 Rule이나 동일 최신일의 중복 환율은 추측으로 선택하지 않는다.

V1 Adapter는 MFN과 `SHIPMENT` 정액 Lane Charge를 지원한다. 종가·종량·복합 관세와
최소/최대 관세는 기존 Calculator를 사용한다. Lane의 종량/비율 Charge, FTA·Broker·신고 Replay,
다른 물성 단위 환산은 자동 대체하지 않고 후속 Adapter 범위로 남긴다.

고정비는 적용 통화로 변환한 뒤 전체 Shipment Line에 정확히 배부한다. 기본 배부 기준은
**Lane 고정비 배부 전 Customs Value**다. QUANTITY/WEIGHT/VOLUME도 명시적인 입력·단위가
있을 때만 허용한다. 반올림 잔여는 기존 결정론적 배부기를 사용하고 배부 합계를 보존한다.
수치 계산의 Decimal 정밀도를 고정해 호출자의 전역 정밀도가 Hash를 바꾸지 않게 했다.

한 Line의 필수 Source가 없으면 불완전한 Shipment에 고정비를 임의 배부하지 않는다. 각 Line의
차단 사유와 입력을 보존하고 Assessment·전체 총액은 `null`로 남긴다. 진단 증적 저장 성공과
`CALCULABLE` 판정은 서로 다른 의미다.

## 3. Child와 원본 저장

Child 계약은 `io-landed-cost-child-artifact-v1` / `1.0.0`이다. 입력 Hash, Projection Hash,
계산기 Key/Version/구현 Hash, 각 Assessment의 Component/Hash와 Shipment 총액을 보존한다.

```text
Run + Attempt + Shipment + Lane
  └─ IO-LC-<결정론적 식별자>
      ├─ .shipment   봉인 Shipment 원본
      ├─ .projection 봉인 Projection 원본 + 공통 Artifact Hash Wrapper
      └─ Child       Line별 Component, 총액, 차단 사유, 원본 Reference/Hash
```

`SqliteInventoryEvidenceUnitOfWork.commit_artifacts()`는 세 Artifact를 원자 저장한다.
동일 Reference·동일 바이트/Hash는 Exact Replay이고 다른 바이트/Hash는 충돌한다. 마지막
Artifact에서 충돌하더라도 앞의 두 INSERT가 함께 Rollback된다. 동시 저장도 직렬화한다.

**PSI Publication Outbox를 만들지 않는다.** 따라서 Run/Attempt의 유일한 게시 Key를 소비하거나
기존 PSI 게시를 가로채지 않는다. Child의 `operational_eligible`, `automatic_publish_allowed`,
`automatic_order_allowed`는 모두 `false`이고 Reader도 이를 강제한다.

## 4. 독립 재검증

`ReadAndVerifyLandedCostChildArtifactUseCase.execute()`는 외부에서 고정한 Child Reference/Hash,
Run Binding, Shipment Hash를 받는다. 저장된 원본의 바이트·문서 Hash와 계약을 검증한 뒤,
같은 Source를 다시 해소하고 실제 Calculator로 Component와 총액을 재계산한다.

단순히 잘못된 총액에 새 Hash를 붙여도 통과하지 않는다. 다른 Run/Attempt/Canonical Hash/
Revision Set/적용일을 넣거나 빈 Child를 만들면 거부한다. Reader는 DB나 최신 Source가 없어도
저장 원본만으로 복구할 수 있다.

구현 Hash가 다른 과거 Calculator는 현재 코드로 임의 Replay하지 않고
`LANDED_COST_CALCULATOR_VERSION_UNAVAILABLE`로 차단한다. 과거 구현을 Hash별로 로드하는
Version Registry와 운영 Archive Adapter는 아직 없다. 이후 배포·보존 계약에서 별도로 구현한다.

## 5. 검증과 남은 작업

네 목적국의 합성 Golden은 기존 Pure Calculator의 Customs/Gross/Recoverable/Net 금액과
일치한다. 고정비 배부·물성 단위, 누락/0 구분, 유효기간·판단시점·환율, Scope와 Hash 변조,
동시 Exact Replay, Transaction Rollback과 재봉인된 잘못된 총액을 검증했다.

금액·세율은 **개발 Fixture이며 법정 세율 또는 실제 운영 비용 주장이 아니다**.
실행 결과와 격리 의존성 기록은 [검증 증적](evidence/landed-cost-artifact-validation-20260929.json)을
참조한다. 기존 [Landed Cost 업무 계약](IO_TRADE_COST_LANDED_COST_CONTRACT.md)을 확장하지만,
PSI Bundle와 Platform Run 계약의 Version은 변경하지 않는다.

**Runtime와 비용 평가 연결 — 다음 작업, 직렬**

- 실제 Claim의 Revision Set·Canonical Binding으로 Shipment 원본을 해소하고 Child 포인터를
  Parent Result에 연결한다. 기존 PSI Child 계약에 비용 Row를 섞지 않는다.
- 개발 Simulation에서 `CALCULABLE` 결과만 소비하고 기존 보유비·Backorder·발주비와
  운송·관세·구매비의 중복 계상을 방지한다. 실제 MEIO Solver 구현과 구분한다.

**운영 저장·배포 — 승인 필요 / 운영 Source는 후속**

- PostgreSQL/Object Storage 게시, 실제 E2E DB Write, 공용 Runtime 배포는 별도 승인 대상이다.
- 권위 Source·Incoterms 해석·FTA/신고 Replay와 과거 계산기 Version 보존은 운영 전환 범위다.
