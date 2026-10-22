# 분류 방법과 정책 후보 범위 공통 계약 V1

기준일: 2026-10-15. InventoryEngine `inventory_engine_dev`의 로컬 구현이다.
`inventory_contracts/segmentation.py`의 **프로세스 내부 계약 1.0.0**이며,
기존 Config 1.1.0/2.0.0/2.1.0, Snapshot, Run Plan, Candidate, DB의 Version을 바꾸지 않는다.
Legacy·ML 구현을 운영에 추가하거나 분류/FSN 입장 조건을 완화하는 계약이 아니다.
[LEGACY_STYLE 로컬 구현](IO_LEGACY_STYLE_IMPLEMENTATION.md)은 별도 모듈로 추가했으며 기존 ABC 경로는 그대로다.

## 1. 목적과 구현 경계

ABC·XYZ와 향후 Legacy 방식이 후보 범위를 만드는 과정을 분리한다.
그룹 수요를 합산해 하나의 품목처럼 계산하지 않으며, 최종 정책은 계속 품목별로 선택한다.

| 구성 | 현재 책임·상태 |
|---|---|
| `classify_inventory/abc_xyz.py::AbcXyzSegmentation` | 기존 결정론적 ABC·XYZ 계산을 그대로 추출. 선택적 VED 표기는 호환 메타데이터이며 7축 정책 적용은 하지 않음 |
| `classify_inventory/models.py` | 기존 `ItemMetric`, `VedAssignment`, `ClassificationResult`를 이동. `application`의 기존 import 경로는 재노출로 유지 |
| `classify_inventory/application.py::classify_items` | 기존 인자·기본값을 유지하는 호환 함수. 위 Class에 위임 |
| `AbcXyzSegmentationAdapter` | 기존 계산 결과를 공통 그룹 배정 결과로 변환. 기존 Snapshot에 신규 필드를 추가하지 않음 |
| `AbcXyzCandidateRangeAdapter` | 기존 ABC·XYZ 그룹/품목 예외를 공통 후보 범위로 변환한 뒤 기존 Candidate 형식으로 출력 |
| `RunPolicySearchUseCase` | 위 후보 Adapter를 사용. 입력 검증·유효 정책 입장·Guard·PSI·서비스/비용 계산은 기존 코드 유지 |
| `LegacyFeatureProducer` | **로컬 구현**. 봉인 대상 원문/별도 Binding을 검증해 분류 Feature·근거/사유·ID/Hash 생성. Source 수집·승인 기능 아님 |
| `LegacyFactorSegmentation` | **로컬 구현**. [개발 규칙 V1](IO_LEGACY_STYLE_DEVELOPMENT_RULES.md)의 인자 구간·품목 예외를 적용. Legacy 원본 동등성 미검증 |
| `LegacyStyleSegmentationAdapter` / `LegacyStyleCandidateRangeAdapter` | 결과 재생성 대사 후 중립 분류/후보 범위로 변환. Runtime Config/PSI 실행에 연결하지 않음 |

기존 실행은 `classify_items → AbcXyzSegmentation → 기존 Snapshot/유효 정책 계산`,
`RunPolicySearchUseCase → AbcXyzCandidateRangeAdapter → 기존 후보 전개 → 공통 Guard/PSI`다.
공통 분류 결과 Adapter는 독립 호출·검증할 수 있지만, 신규 결과를 Run Claim/DB에 게시하는 경로는 없다.

## 2. 공통 분류 결과

`SegmentationProvider[RequestT].segment(request, configuration_hash=...)`가
`SegmentationResult`를 반환한다. 방법별 원본 계산 자료는 Adapter에서 다루고 출력 형식을 공유한다.
현재 ABC Adapter의 입력은 순수 Class가 반환한 `ClassificationResult`다.

| 필드 | 의미·검증 |
|---|---|
| `contract_version` | 내부 직렬화 형식 `1.0.0` |
| `method_id`, `implementation_version` | 분류 방법과 구현 Version. 현재 `ABC_XYZ` / `1.0.0` |
| `source_content_hash` | 계산에 사용한 Source 근거 Hash. 원본 Revision·Scope·기간은 기존 Snapshot/입력 Binding이 계속 소유 |
| `configuration_hash` | 적용 설정 Hash |
| `assignments` | 품목별 배정. 비어 있거나 품목이 중복되면 거부. 품목 ID 순 정규화 |
| `content_hash` | 위 내용을 정규화한 별도 내부 Hash. 기존 Snapshot/정책 Hash와 혼용 금지 |

품목 배정 `SearchGroupAssignment`는 다음과 같다.

- `item_id`: 원 품목 ID를 유지한다.
- `search_group_id`: 후보 범위를 찾기 위한 그룹. `AX~CZ`로 제한하지 않는다.
- `abc_xyz_class`: 별도로 보존하는 ABC·XYZ 기본 분류. 없으면 null.
- `status`: `ASSIGNED` 또는 `UNCLASSIFIED`.
- `reason_code`: `ASSIGNED`이면 null, `UNCLASSIFIED`이면 사유 필수이며 그룹 ID는 null.

`ASSIGNED`는 그룹을 배정했다는 뜻일 뿐 계산·자동 게시·발주 허가가 아니다.
ABC Adapter는 원 결과 건수·중복·등급/그룹 일치 여부를 확인하며 미분류 품목을 없애거나 CZ로 바꾸지 않는다.
향후 다른 Adapter도 원 Universe와 품목별 Coverage를 대사해야 한다.

## 3. 공통 후보 범위

`CandidateRangeProvider.resolve(search_config, assignment=...)`가 `CandidateRangeBinding`을 반환한다.

| 구성 | 필드·검증 |
|---|---|
| 배정 근거 | 원 `SearchGroupAssignment`를 보존하며 `ASSIGNED`만 범위 연결 가능 |
| `candidate_range` | `policy_kinds`, `rmos_values`, `emos_values`, `service_level_values`, `review_cycle_weeks` |
| `search_config_hash` | 적용 탐색 설정의 정규화 Hash |
| `range_source` | `GROUP_PROFILE` 또는 `ITEM_OVERRIDE` |
| `override_reason` | 품목 예외일 때만 사유 필수 |
| `content_hash` | 배정·범위·설정·출처·사유의 별도 내부 Hash |

범위는 기존 정책 탐색 숫자·종류·후보 상한 검증을 재사용한다.
목록 순서와 동등한 소수 표기는 정규화하고 중복·알 수 없는 필드·상한 초과는 거부한다.
범위 객체는 불변 tuple로 보관하고 직렬화 시 새 list를 반환한다.
서비스 조건·비용 가정은 후보 범위에 섞지 않고 기존 탐색 설정을 따른다.

ABC Adapter는 **품목 예외 → 그룹 설정** 우선순위를 유지한다.
설정 미존재·탐색 비활성·미분류는 기존 오류를 유지하며, 품목 예외도 미분류를 구제하지 않는다.
기존 Config는 여전히 `segment_key=AX~CZ`를 요구한다. 중립 계약이 다른 그룹 이름을 표현할 수 있어도
Legacy 그룹을 현재 Runtime에 연결할 수 있다는 뜻은 아니다.

## 4. 결과·Hash 보존 규칙

- ABC 누적 매출의 품목 투입 전 비율, 경계 비교, 매출 동률의 품목 ID 정렬을 그대로 유지한다.
- XYZ 평균·분산·CV², 0평균/누락/오류 사유와 축별 Lookback의 기존 의미를 유지한다.
- VED 표기, Source Decimal 문자열 및 ASCII 기반 분류 Hash 직렬화를 유지한다.
- `build_snapshot`, 7축 분류·유효 정책·FSN·운영 Gate 계산은 변경하지 않는다.
- `candidate_policies()`의 기존 인자·오류 순서·반환값을 보존한다. 범위 해석과 후보 전개만 함수로 추출한다.
- 새 Adapter도 동일 전개기를 사용하며 Candidate ID, `range_source`, Override 사유와 순서를 보존한다.
- 신규 `method_id`·`search_group_id`·내부 Hash를 기존 Candidate/Snapshot/Result Hash 본문에 넣지 않는다.
- Config Schema, 공통 Run 계약, Platform API/Frontend, DDL은 이번 작업에서 변경하지 않는다.

## 5. 검증 근거

추출 전에 `tests/contract/test_segmentation_compatibility_contract.py`에 지문을 고정하고
원 구현에서 4개 테스트가 통과함을 확인했다. 새 구현의 결과로 기대 Hash를 갱신하지 않았다.

- Config 1.1.0/2.0.0: 전체 Snapshot Hash·UUID, Source/Config/유효 정책 Hash 고정.
- Config 2.1.0 단일·다품목: 분류·유효 정책·탐색·전체 PSI 결과·정책 선택 Hash 고정.
- 신규 공통 계약/Adapter: 미분류·중복·Coverage·구간 범위·숫자 정규화·불변성·품목 예외·오류 순서 검증.
- 참조 Commit `99f2f12`와 직접 비교: ABC·XYZ 계산 본문 AST 동일, 600개 결정론적 입력의
  반환/오류 일치(오류 194건 포함), 전체 Snapshot 4건 일치.
- 기존 FSN BLOCK/REVIEW·미검증 Source 차단 테스트를 수정하지 않고 다시 실행한다.

전체 회귀·정적 검사 결과는 [개발 기준선](IO_DEVELOPMENT_BASELINE.md)의 이번 절에 기록한다.
이는 Offline 검증이며 실제 DB/Runtime 통합·운영 성과 검증은 아니다.

## 6. 다음 작업과 승인 경계

현재 기준선: [Legacy 규칙의 로컬 대사](IO_LEGACY_SEGMENTATION_RULE_RECONCILIATION.md)는 완료했다.
품목별 Bootstrap ROP와 정책 전달은 코드로 확인했고 그룹 경계·후보 생성식은 원본을 확보하지 못했다.

현재 기준선: `D1~D7`의 [개발 규칙·수작업 Golden](IO_LEGACY_STYLE_DEVELOPMENT_RULES.md)과
Feature Producer·Class/Adapter 개별 구현을 완료했다. 인자 3개·27개 그룹·32개 후보 묶음은
개발 가정이며 원본 동등성을 의미하지 않는다. 2026-10-19에는 [별도 후보 Catalog의 공통 PSI 실행](IO_CANDIDATE_CATALOG_PSI.md)도 완료했다.

1. **다음 작업 — 중립 결과 선정·평가 연결:** 완료한 Catalog/PSI의 정확한 후보 목록을 검증하며 기존
   서비스·비용·재고·발주횟수·동점 기준을 적용한다. 현 Config/Run 외부 계약 확장이나 DB 게시로 연결하지 않는다.
2. **후속 작업 — 동일 조건 비교:** ABC·XYZ/LEGACY_STYLE/넓은 후보를 새 미사용 기간에서 서비스·비용·재고·
   발주횟수·실행시간·좋은 후보 누락으로 비교한다. 미분류·미선정도 분모에서 숨기지 않는다.

현재 FSN 설정·운영 Gate와 미분류 3품목의 입장 조건은 그대로다.
DB 접속·Write·Migration, 실제 Claim/E2E, Runtime 설치/배포, 발주, Commit/Push는 이번 범위가 아니다.

방법: `oma-architecture`로 책임 경계를 검토하고, `oma-refactor`의 변경 전 지문/동작 보존과
`oma-qa`의 계약·회귀 검증 절차를 적용했다. 독립 에이전트 검토나 운영 승격을 수행했다는 뜻은 아니다.
