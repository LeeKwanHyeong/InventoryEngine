# 명시적 후보 Catalog와 공통 Guard·PSI 연결

기준일: 2026-10-19. InventoryEngine `inventory_engine_dev`의 로컬 개발 구현이다.
**32개 후보·88개 참조 목록의 실제 수학적 실행과 별도 품목별 선정·평가는 완료했으며,
새 Holdout 성과 비교·운영 연결은 미완료**다.
확정 개발 규칙은 [LEGACY_STYLE V1](IO_LEGACY_STYLE_DEVELOPMENT_RULES.md), 선행 분류 모듈은
[Class·Adapter·Feature Producer](IO_LEGACY_STYLE_IMPLEMENTATION.md)를 따른다.
후속 [Catalog 선정·평가 계약](IO_CATALOG_POLICY_SELECTION.md)은 원 PSI 전체를 재생성 대사한 뒤
별도 결과로 선정·미선정·Baseline 대비 지표와 로컬 복구를 제공한다. 원 PSI 계약/Hash는 바꾸지 않는다.

## 1. 책임과 유지한 경계

| 구성 | 역할 |
|---|---|
| `classify_inventory/candidate_catalog.py::CandidateCatalogProducer` | 검증한 Legacy 분류 결과에서 명시적 후보 목록 생성·재생성 대사 |
| `OfflineCandidateCatalog` | 원 분류/Feature·규칙·모드·품목별 그룹/예외·후보/제외 사유를 보존하는 불변 메모리 문서 |
| `VerifiedCandidateCatalog` | 전체 목록을 한 번 검증한 뒤 품목별 조회. 원 계산 입력과의 Binding 대사 |
| `recommend_replenishment/application/catalog_search.py::RunCatalogPolicySearchUseCase` | DEVELOPMENT·LOCAL_SHADOW 전용 진입점. 승인 원문을 수정하지 않고 공통 실행기로 전달 |
| `RunPolicySearchUseCase._execute` | 기존 입장 검증·수학적 전략·Guard·PSI·비용/서비스 계산을 공유. 내부 Catalog 인자가 없으면 기존 ABC 경로 그대로 |

신규 Runner가 전략이나 PSI Row를 외부에서 받아 결과로 취급하는 구조가 아니다.
모든 요청 후보는 기존 `CandidateStrategy` → `simulate()` → `validate_action()` → 재고 이월을 거친다.
기존 `execute()` 호출 서명·ABC 결과 계약/Hash·Config/Run Schema·FSN/운영 Gate는 유지한다.
수학적 계산을 Legacy Bootstrap ROP나 경제적 최적 EOQ의 재현이라고 부르지 않는다.

## 2. 후보 전개 계약

| 모드 | 후보 범위 | 품목당 요청 조합 |
|---|---|---|
| `LEGACY_STYLE` | 검증한 인자 그룹 또는 유효 품목 예외의 SMALL/MEDIUM/LARGE | 32 |
| `ABC_XYZ_SHARED_BUDGET` | 별도 개발 비교 프로필의 X→SMALL, Y→MEDIUM, Z→LARGE. 유효 품목 범위 예외는 보존 | 32 |
| `WIDE_REFERENCE` | 세 묶음의 의미상 요청 파라미터 합집합 | 88 |

두 좁은 모드의 후보 수를 같게 한다. 기존 ABC 운영 Matrix나 과거 ML 실험의 후보 설정은 변경하지 않는다.
88개는 96개 원 전개에서 중복 8개를 제거한 목록이다. 각 인자 열을 합쳐 다시 곱한 256개가 아니다.
넓은 모드는 범위 누락을 비교하기 위한 참조이므로 개발용 품목 범위 예외에 좁혀지지 않는다.
단, 기존 승인 업무 Override·Source Hard Constraint·FSN/PLC/HML Gate를 우회하지 않는다.

후보는 `policy_kind`, `rmos`, `emos`, `target_service_level`, `review_cycle_weeks`와
품목·중립 `search_group_id`·범위 출처·예외 사유를 가진다. `segment_key`를 위조하지 않는다.
`semantic_policy_hash`는 같은 요청 정책의 방법 간 대사용이며 기존 Candidate ID나 동점 규칙을 대체하지 않는다.
새 `OCCP-*` Candidate ID는 새 계약 내부에만 사용한다.

Class/Feature 미분류와 원 ABC 미분류는 목록에서 제외 사유를 보존한다. 기본 묶음이나 CZ를 만들어 넣지 않는다.
공통 실행기도 원 ABC 입장 조건을 다시 적용한다. Legacy 그룹이 배정돼도 기존 미분류 품목은 계산 입장을 얻지 못한다.
32/88은 요청 파라미터 수다. 제약에 따라 같은 유효 정책이 되거나 실패할 수 있으며, 실행 가능 정책 수를 보장하지 않는다.

## 3. 원 입력과의 Binding

생성 시 정확한 Legacy Result Hash, 실행 시 정확한 Catalog Hash를 별도 인자로 받는다.
Hash는 내부 전달 계약이며 사용자 입력 화면을 추가하지 않았다.

실행 전 다음을 검사한다.

- 원 Config Hash·분류 Snapshot ID/Hash·품목별 ABC 등급/상태/사유가 실제 분류 원문과 일치.
- 회사·Subsidiary·Site·W0·Cut-off·Master 기준일/Revision·공유 Calendar ID가 Canonical 입력과 일치.
- Feature Universe와 실제 계산 품목/UOM이 정확히 일치. 고아·누락·추가 품목으로 대체하지 않음.
- 26주 이력 Calendar와 유효 수량이 Mathematical 입력과 일치. 미래 Forecast로 과거 이력을 대체하지 않음.
- 유효 Feature의 물리 Lead Time이 W0 정책과 일치. SDE 보호기간을 물리 납기로 바꾸지 않음.
- 최소 Fill Rate는 고정 개발 프로필 0.95와 일치하고 Config의 후보 상한이 32/88개를 수용.

기존 Config `policy_search.item_overrides`가 있으면 `CATALOG_CONFIG_RANGE_OVERRIDE_UNSUPPORTED`로 차단한다.
새 Profile의 품목 범위 예외와 기존 임의 목록 예외를 조용히 혼합/무시하지 않는다.
이 제한은 별도 Offline 경로만의 초기 지원 범위이며 기존 ABC Runtime의 예외 기능을 제거하지 않는다.

원 분류 Header의 Hash/ID·Config Revision·기간·Scope, Canonical Cut-off/EOH·고아 품목·봉인 조건은
기존 공통 입장 경로에서 계속 검증한다. 원 승인 Snapshot을 재봉인하거나 Config를 변경해 맞추지 않는다.
양수 이력이 있어도 Feature Source가 누락/미검증이면 후보를 제외하고 Baseline/원 입력을 보존한다.

## 4. 실제 PSI·제약·결과

- 준비된 입력과 Baseline PSI는 한 번 만든다. 각 후보의 재고 상태·미도착 공급은 매번 새로 만든다.
- 후보 주문은 MOQ·발주배수·용량·주문일·승인 범위 검증을 거친다. 용량에 맞추려고 Lot 배수를 깨지 않는다.
- 기존 미도착 권고를 Inventory Position에 반영하며 주문을 중복 생성하지 않는다.
- FSN/PLC BLOCK은 HOLD다. REVIEW는 계산/증적만 허용하며 자동 주문·게시 권한은 항상 false다.
- SDE는 보호기간, VED는 서비스 하한, HML은 승인 수준으로 기존과 동일하게 적용한다.
- 승인된 업무 Override는 기존 `APPROVED_OVERRIDE_PINNED`로 탐색 제외한다.
- 후보 실패는 FAILED로 격리하고 Row/Artifact/metrics를 null로 둔다. Baseline과 다른 후보는 유지한다.
- 서비스·평균/종료재고·Backorder·비용은 실제 PSI Row와 제약 적용 주문에서 계산한다.
  무수요는 `NO_DEMAND_IN_HORIZON`, 비용 미연결은 `COST_PROFILE_NOT_BOUND`·총액 null이다.
- 비용은 기존 개발용 품목별 보관/Backorder/발주 가정이다. 분류용 단가를 보관비로 바꾸거나 Landed Cost를 더하지 않는다.
- 전체 Universe × 모드별 후보 상한 × H(H+1)/2 ≤ 200,000을 실행 전에 검사한다.
  제외 품목도 분모에 포함하며 후보/기간을 조용히 줄이지 않는다. 대량 Full Load 기능이 아니다.

새 결과는 `inventory-offline-catalog-psi-result / 1.0.0`이다. 원 Config/분류/Canonical/Prepared/Math Hash와
Catalog 본문·Hash, 후보별 원 파라미터·행동/조정·주문·PSI·평가값·실패/제외를 보존한다.
원 `search_config_hash`와 실제 후보 Observation Binding인 `candidate_input_binding`을 분리한다.
선정 정책은 null이며 DB Write·자동 게시·자동 주문·Durable Artifact 게시 Flag는 false다.

`verify_result()`는 외부에서 받은 결과 Hash를 확인한 뒤 같은 입력과 Catalog로 Guard·PSI·평가를 재실행한다.
PSI Row/metrics/후보를 바꾸고 새 Hash만 붙여도 재생성 대사에서 거부한다.
현재는 메모리 결과이며 기존 PSR Artifact·Platform Run·DSIM Projection 테이블로 게시하지 않는다.
기존 ABC 전용 선택기는 이 새 결과 ID를 거부한다. 선정 연결 없이 기존 PSR인 것처럼 전달하지 않는다.

## 5. 실행 예와 검증

```python
producer = CandidateCatalogProducer(classifier)
catalog = producer.produce(legacy_result, mode="LEGACY_STYLE", expected_result_hash=legacy_result_hash)
result = RunCatalogPolicySearchUseCase(deployment, producer).execute(
    mathematical_request, catalog=catalog, expected_catalog_hash=catalog_hash,
    config_values=original_config, classification_snapshot=original_classification,
)
```

[수작업 Golden](../../tests/fixtures/golden_catalog_psi.json)은 시작재고 0, 주수요 10, LT 7일,
RMOS 0·EMOS 0.25의 Q=11·ROP=10·S=21을 사용한다.

| 후보 | 주별 EOH | 권고 입고 | 주문량 | 총비용 |
|---|---|---|---|---|
| 주기적 목표 S | 0 → 11 → 1 | 0 → 21 → 0 | 21 | 59 |
| ROP 고정 Q | 0 → 1 → 0 | 0 → 11 → 0 | 11 | 28 |

총비용 차이가 이 후보의 우월성을 뜻하지 않는다. 두 사례 모두 서비스 미달이며 최적 정책을 선정하지 않았다.
새 Unit/Offline Integration 48개와 기존 고정 Hash 계약 4개를 함께 실행해 52 passed를 확인했다.
두 품목의 176개 후보에서 Ledger/주문 ID 격리, 기존 FSN BLOCK/REVIEW, VED 하한·SDE 납기 분리,
입력/범위/결과 변조, 비용 미연결·무수요·실패 격리·계산 상한도 검증했다.

별도 메모리 Probe에서는 Calendar·정책기간·Forecast·제어일을 13주로 함께 구성해
88개 후보·1,144개 PSI Row와 8,008 Observation 작업 상한, BOH/EOH 이월을 확인했다.
결과 Hash는 `068de00b605c88a52f60eb1fb838a228dbf006414c3a3e3c19e2cf4e95f84949`다.
이는 3주 전용 Fixture에 Forecast 값만 늘린 테스트가 아니며 성과 Holdout 실험도 아니다.
서로 다른 Python Hash Seed 2개에서도 Catalog/3주 결과 Hash가 동일했다.
전체 회귀/정적 검사 건수는 [개발 기준선](IO_DEVELOPMENT_BASELINE.md)에 기록한다.

고정 규칙 Profile과 Golden은 변경하지 않았다. 규칙 파일의 구현 여부 메타데이터는 정의 당시 기록이지
현재 실행 능력을 제어하는 Flag가 아니다. 새 Catalog/결과 계약과 이 문서가 현재 구현 범위를 설명한다.

## 6. 남은 작업

**완료 — 중립 후보 결과의 품목별 선정·평가 연결 · InventoryEngine**

- 정확한 입력/Catalog로 원 PSI를 재실행하고 공통 Guard/PSI/지표와 서비스·비용·재고·발주횟수·동점 기준을 적용했다.
  기존 ABC PSR/PSEL 계약과 Hash를 유지하며 새 결과는 `inventory-offline-catalog-policy-selection-result`다.
- 후보 누락·실패 위조·지표/사유 변조를 거부하고 미선정/REVIEW 사유·Baseline 대비 지표를 보존한다.
  별도 Append-only 로컬 파일 저장·멱등 요청·재시작 복구를 검증했다. DB/Platform 게시는 포함하지 않는다.

**완료 — 새 미사용 기간의 동일 조건 비교 · InventoryEngine**

- [독립 비교](IO_CATALOG_HOLDOUT_COMPARISON.md)에서 새 합성 2창의 조건을 먼저 고정한 뒤 실제 실행했다.
  32/32/88개의 서비스·비용·재고·계산/재검증 시간과 좋은 후보 누락을 보존했다.
- [간헐수요·장기 LT의 강건성 조건](IO_CATALOG_ROBUSTNESS_CONTRACT.md)은 고정했다. 다음은 전용 평가기·Producer·분할 조립이다.
  기존 Golden과 이번 Holdout을 보고 재튜닝하지 않으며,
  현재 결과가 Legacy 동등성·실제 업무 성능 우월성을 입증한 것으로 표현하지 않는다.

실제 DB·운영 Claim·공용 Runtime 배포·실제 발주·Commit/Push는 수행하지 않았다.
`oma-backend`로 공통 실행을 재사용하고 `oma-qa`로 실제 Guard/PSI 호출·오류·Hash 호환성을 검증했다.
