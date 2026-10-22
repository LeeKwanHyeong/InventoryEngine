# Catalog 후보의 품목별 선정·평가 계약

기준일: 2026-10-19. 대상: InventoryEngine `inventory_engine_dev`의 독립 Offline 개발 경로다.
**실제 Catalog PSI의 재검증·품목별 선정·사유/비교값 보존과 로컬 파일 복구를 구현했다.**
[새 Holdout의 방법 간 비교](IO_CATALOG_HOLDOUT_COMPARISON.md)는 별도 합성 개발 경로에서 완료했다.
Platform Run/Studio 연결, DB 적재·정책 승인·발주는 별도 범위다.
선행 [Catalog PSI](IO_CANDIDATE_CATALOG_PSI.md)와 [용어·서비스 지표](IO_POLICY_TERMINOLOGY_RECONCILIATION.md)를 따른다.

## 1. 기존 ABC 경로와 분리하는 이유

기존 선택기는 Config의 ABC 후보 집합을 재생성하며 `PC-*`, PSR/PSEL 계약을 사용한다.
새 Catalog는 `LEGACY_STYLE`, `ABC_XYZ_SHARED_BUDGET`, `WIDE_REFERENCE`와 `OCCP-*` 후보를 가진다.
중립 그룹을 AX~CZ로 위장하거나 Candidate ID를 바꾸어 기존 선택기에 넣지 않는다.

| 구성 | 책임 |
|---|---|
| `RunCatalogPolicySelectionUseCase` | 정확한 원 입력·Catalog로 PSI를 재실행한 뒤 품목별 평가·선정 |
| 기존 `verify_candidate_evaluation` | 성공 후보의 Guard·재고 이월·주문·실제 서비스/비용 지표를 추가 대사 |
| 기존 `assess_candidate`·`rank_candidate_assessments` | 같은 서비스 자격·비용/재고/발주횟수·동점 기준 공유 |
| `verify_catalog_selection_result`·신규 JSON Schema | 별도 결과의 닫힌 구조·상태·포인터·지표/비교값 정합성 검증 |
| `LocalCatalogPsiArtifactStore`·`LocalCatalogPolicySelectionArtifactStore` | 원 PSI 우선 Append-only 파일 저장·멱등 재요청·재시작 후 재검증 |

공통 순위 함수에 명시적 `candidate_prefix="OCCP-"` 경로만 추가했다.
기존 호출의 기본값은 `PC-`이며 ABC 전용 선택기·Verifier의 허용 계약을 넓히지 않았다.
기존 Config/분류·후보·PSI·선정 결과와 Hash, FSN·운영 Gate·DB 구조는 변경하지 않는다.

## 2. 선정 전에 원 PSI 전체를 재검증한다

입력은 다음과 같으며 “최신” Source를 자동 탐색하지 않는다.

- `inventory-offline-catalog-psi-result / 1.0.0`과 정확한 `expected_source_hash`.
- 원 `MathematicalPolicyRequest`, 봉인 Catalog와 `expected_catalog_hash`.
- 원 Config 전체와 원 분류 Snapshot. Legacy 인자·규칙은 검증된 Catalog Producer에 연결한다.

Hash는 내부 포인터이며 사용자가 화면에서 작성하는 값이 아니다.
같은 Scope·W0·Canonical·Mathematical·분류·Config·Catalog로 **실제 수학적 전략→Guard→PSI→지표**를 재실행한다.
저장된 수량·Boolean·비용·사유를 신뢰해 선정하지 않는다. 이 검증은 실패/제외 후보와 Baseline도 포함한다.

따라서 후보 삭제/추가·중복·고아, 성공을 실패로 바꾸기, 제외 사유 위조, 재고/주문/Gate/비용 수정은
Parent/Child Hash를 모두 다시 만들고 예상 Hash까지 바꾸어도 원 입력과의 재생성 대사에서 거부한다.
원 Source 목록의 순서도 고정된 Hash 계약의 일부다. 저장 결과를 임의로 재정렬해 다른 Source로 받지 않는다.

성공 후보는 이후 공통 Guard와 PSI 이월을 다시 확인하고 서비스·비용을 실제 Row/주문에서 계산한다.
두 검증 모두 계산용 로컬 입력의 정합성 확인이다. 원천 업무 자료의 진실성·승인·DB 권한 인증을 대신하지 않는다.
실행량은 기존 Catalog의 관측 작업 상한을 유지한다. 재검증 비용이 추가되므로 이 경로를 성과 계산시간에서 제외하면 안 된다.

## 3. 선정 자격과 비교 순서는 기존 기준 그대로다

다음 조건을 모두 만족해야 순위를 부여한다.

1. 후보 PSI가 실제 성공했고 원 입력·주문·행동·지표 재검증을 통과했다.
2. 기존 FSN/PLC Gate가 BLOCK이 아니며 권고 계산이 허용된다.
3. 현재 수요가 존재하고 수량 충족률이 봉인된 최소 Fill Rate 이상이며 종료 고객 BO가 0이다.
4. 명시적 개발 비용 Profile이 있다. 없는 비용을 0으로 만들지 않는다.

통과 후보를 `(총비용, 주차말 평균 On-hand, 수락 주문 횟수, 원 Candidate ID)` 오름차순으로 비교한다.
수치 비교는 Decimal이며 허용 오차·임의 가중 합·CSL 설정값으로 서비스 판정을 대체하지 않는다.
최적 범위는 `EVALUATED_CANDIDATE_SET_ONLY`다. 등록한 유한 후보 밖이나 다른 기간의 최적성을 뜻하지 않는다.

REVIEW는 개발 선정 결과와 `source_review_required=true`를 보존한다.
BLOCK은 보유재고만으로 Fill Rate가 1이어도 미선정이며 기존 계산 차단 사유도 함께 남긴다.
미분류·인자 누락·고정 승인 Override 같은 탐색 제외를 기본 그룹·CZ·0수요로 대체하지 않는다.

| 상황 | 보존하는 사유·값 |
|---|---|
| 후보 자체 실패 | `CANDIDATE_FAILED`, 원 `failure_reason`, 지표·비교값·순위 null |
| 전 후보 실패 | 품목 `ALL_CANDIDATES_FAILED`, 각 후보 실패 사유 유지 |
| 탐색 제외 | `SEARCH_ITEM_EXCLUDED`와 원 `search_exclusion_reason`, Baseline 유지 |
| 서비스 미달 | `FILL_RATE_BELOW_MINIMUM` 및/또는 `ENDING_BACKORDER_REMAINS` |
| 무수요 | `NO_DEMAND_IN_HORIZON`, Fill Rate와 서비스 변화 null |
| 비용 없음 | `COST_PROFILE_NOT_BOUND`, 총비용과 비용 변화 null |
| Gate 차단 | `ORDER_GATE_BLOCKED` 및/또는 `RECOMMENDATION_CALCULATION_BLOCKED` |
| 적격 후보 탈락 | 더 높은 비용·평균재고·주문 횟수 또는 Candidate ID 동점 해소 사유 |

미선정 품목은 선택 정책·ID/Hash·점수·Review 포인터를 모두 null로 둔다.
일부 후보 실패는 다른 품목·후보의 성공과 Baseline을 지우지 않는다.

## 4. 별도 결과와 Baseline 대비 평가값

새 결과 계약은 `inventory-offline-catalog-policy-selection-result / 1.0.0`이다.

- `OCPSI-<hash>` 원 PSI Reference와 `OCC-<hash>` Catalog Reference/Hash·Mode.
- 원 Canonical/Prepared/Mathematical/Config/분류 Snapshot Hash와 Scope·종료 조건.
- 품목별 Baseline 지표, 선정 정책·점수·Candidate 결과 Hash·Review 필요 여부.
- 모든 후보의 순위·선정/탈락/부적격/실패 사유, 성공 후보의 `verified_metrics`.
- `baseline_comparison`: 수량 충족률·평균/종료재고·종료 BO·BO Unit Weeks·Forecast Shortage·
  주문 횟수/수량·총비용의 **후보−Baseline** 차이. 값이 없으면 차이도 null.

비교값은 같은 품목·같은 기간의 설명용이다. 예를 들어 Baseline이 발주하지 않고 품절을 방치해 비용 0이면,
권고 비용이 양수라는 이유로 더 나쁜 정책이라고 해석할 수 없다. 서비스 자격을 먼저 적용한다.
비용은 기존 개발 Profile 범위이며 Lost Sales·공동 Shipment·Landed Cost·종료 잔존가치를 새로 합산하지 않는다.
수량 충족률을 CSL 보장으로, 정책 MOS를 실제 평균재고로 바꾸어 표기하지 않는다.

`automatic_publish_allowed`, `automatic_order_allowed`, `operational_policy_approved`, `database_writes`는 모두 false다.
원 PSI의 `selected_policies=null`을 수정하지 않는다. 별도 결과가 운영 포인터를 변경하지 않는다.

## 5. 로컬 저장과 복구

1. `LocalCatalogPsiArtifactStore`가 정확한 입력을 받아 원 PSI를 검증·저장한다.
2. 선택 결과는 부모 PSI 파일을 다시 읽고 선정까지 재검증한 뒤 `OCPSEL-<hash>`로 저장한다.
3. 같은 내용·Hash는 멱등 반환한다. 기존 파일을 덮어쓰지 않고 충돌·변조·부모 누락을 거부한다.
4. 재시작 시 같은 원 입력·Catalog·Config·분류를 명시적으로 다시 제공해야 한다. 원천 DB나 최신 입력을 조회하지 않는다.

저장은 기존 로컬 저장기의 크기 제한·임시 파일 fsync·배타적 게시·디렉터리 fsync를 재사용한다.
Source의 `durable_artifact_persisted=false`는 원 계산 문서의 기존 값으로 보존하고,
실제 파일 저장 여부는 별도 Receipt의 `durable_local_artifact=true`로 구분한다.
이 파일 저장은 PostgreSQL Projection·Planning Cycle 게시·Outbox·공용 Runtime E2E를 의미하지 않는다.

```python
selector = RunCatalogPolicySelectionUseCase(deployment, producer)
bindings = dict(
    catalog=catalog, expected_catalog_hash=catalog_hash,
    config_values=original_config, classification_snapshot=original_classification,
)
selected = selector.execute(
    source_psi, mathematical_request, expected_source_hash=source_psi_hash, **bindings,
)
source_store = LocalCatalogPsiArtifactStore(existing_directory, selector, mathematical_request, **bindings)
source_store.write(source_psi)
store = LocalCatalogPolicySelectionArtifactStore(existing_directory, source_store)
receipt = store.write(selected)
restored = store.read(receipt["content_hash"])
```

## 6. 검증 범위와 다음 작업

- 신규 검증 **61개**, 전체 Unit·Contract·Offline Integration **1,175 passed / 9 skipped / 669 subtests passed**다.
  변경하지 않은 Platform Adapter의 Starlette Deprecated 상태 코드 경고 3건은 남아 있다.
- ABC·XYZ 결과/Hash, 실제 PSI의 서비스 우선 선정, 실패 격리, 미연결 비용, Gate, 변조, 재시작 복구를 확인했다.
  Fill Rate가 30/31로 95%를 넘어도 마감 고객 BO=1이면 미선정하는 실제 입력도 고정했다.
- 기존 파일 343개 중 342개는 SHA-256이 동일하다. 변경한 1개는 명시적 Candidate Prefix를 추가한
  공통 순위 함수 파일이다. 기존 ABC 분류·탐색·선정과 Catalog PSI/목록 Hash는 서로 다른 Hash Seed 2개에서도 동일하다.
- 전체 Ruff·Format(293개 파일), JSON Schema의 양성/음성 사례·Python 필드/사유 일치와 Diff Check를 통과했다.
  테스트 환경에 coverage가 없어 커버리지 비율은 측정하지 않았으며 의존성을 설치하지 않았다.

기능 검증 결과와 고정 Hash는 [검증 기록](evidence/catalog-policy-selection-verification-20261019.json),
작업 순서는 [개발 기준선](IO_DEVELOPMENT_BASELINE.md)에 기록했다.
위 1,175건은 선정 연결 당시 검증 이력이며 방법의 업무적 우월성을 뜻하지 않는다.
후속 독립 비교와 최신 전체 회귀는 [개발 기준선](IO_DEVELOPMENT_BASELINE.md)의 최상단 절을 따른다.

**완료 — 독립 비교 조건 고정과 실제 실행 · InventoryEngine**

- 새 합성 2창·8건에서 32/32/88개를 비교하고, 계획 선택을 고정한 뒤 실현 서비스·비용·재고·누락·시간을 보존했다.
  ABC가 이 조건에서 유리했지만 세 방법 모두 간헐수요 2건은 실패했다. 일반적인 우월성이나 운영 채택의 근거는 아니다.
- 후속 [강건성 조건](IO_CATALOG_ROBUSTNESS_CONTRACT.md)은 고정했다. 새 Offline Scorecard·다중 경로 선정,
  Window/비용·미래 관측 분리·분할 증적 검증은 다음 구현 대상이다. 이번 Holdout에 맞춰 범위를 튜닝하지 않는다.
  실제 DB 저장/복구와 Demand 연결은 정확한 승인 입력과 별도 Write 승인이 필요하다.

DB·FSN 설정·운영 Gate·공용 Runtime·Commit/Push는 이번 작업에서 변경하지 않는다.

방법: `oma-backend`의 계산/계약/저장 책임 분리를 적용하고 `oma-qa` 기준으로 실제 실행·음성 사례·복구를 검증했다.
스킬 설치에 일부 공통 참조 파일이 없어 해당 부분은 저장소의 기존 계약·체크 절차를 기준으로 보완했다.
