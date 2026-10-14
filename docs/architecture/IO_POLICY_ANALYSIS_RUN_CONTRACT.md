# Run에 연결된 정책 탐색·선택·평가 결과 계약

기준일: 2026-10-14. 대상: InventoryEngine `inventory_engine_dev`, dsai-platform
`codex/inventory-psi-strategy-v2`. **로컬 구현·실제 HTTP 계산 검증 기준선**이다.
개발 DB 게시·실제 Claim 생성·공용 Runtime 배포 완료를 뜻하지 않는다.

최신 상태는 아래 **맥북 내부 실제 HTTP 실행·조회·복구**와 [개발 기준선](IO_DEVELOPMENT_BASELINE.md)을 따른다.
Demand 전달/복구 계약은 아래 11절을 유지한다. 기존 성공 Run의 Export를 재사용해 Baseline Preview를 계산했지만,
실제 Inventory Claim·Recommended PSI·Run 분석 Artifact 게시는 미완료다.

## 맥북 내부 실제 HTTP 실행·조회·복구 — 최신 기준선 (2026-10-14)

**두 로컬 프로세스를 실제로 기동해 계산 API를 검증했다.** 외부 접속은 열지 않았고 로컬 호스트·포트도 문서에 기록하지 않는다.
기존 FSN/운영 Gate, 계산 전용 `1.0.0` 계약과 원 봉인 입력은 그대로다.
아래 Offline 계약 절은 선행 검증 이력이며 최신 작업 순서는 [개발 기준선](IO_DEVELOPMENT_BASELINE.md)을 따른다.

- [로컬 Runtime Factory](../../src/dsio_inventory_engine/entrypoints/development_calculation_runtime.py)는
  정확한 기동 사양/Command·Source 파일 SHA-256·설치 Build를 검증한다.
  운영 환경·원격 목적지·증적 경로 이탈·Build 불일치를 거부한다.
  실제 기동 인터페이스는 맥북 내부로만 제한했다.
- Source 조회가 가능한 정상 계산 모드와 원천 조회가 꺼진 복구 모드를 구분한다.
  복구 모드는 입력 파일 Loader를 호출하지 않고 저장된 Source Artifact와 완료 Part를 사용한다.
  기존 Source를 “최신 입력”으로 대체하거나 누락 시 가짜 데이터로 채우지 않는다.
- 실제 2개 Part Commit 직후 Runtime을 종료하는 로컬 장애 주입을 수행했다.
  API `503`·최종 Manifest 미생성을 확인한 뒤 Backend/Runtime 원천 조회를 모두 끄고 재기동했다.
  나머지 Part를 계산·봉인하고 조회했으며, Runtime을 다시 재기동해 같은 요청이 같은 Manifest를 반환하는지 검증했다.
  장애 주입은 개발 환경에서만 명시적으로 사용할 수 있으며 정상 기동에는 적용하지 않는다.
- 검증 후 서비스는 검증한 봉인 입력만 사용하는 정상 로컬 모드로 기동했다.
  원천 조회 없는 복구 성공과 현재 정상 모드를 혼동하지 않는다.
  Claim·게시·실제 발주를 활성화하지 않았다.

### 결과·경계

- V100 **3,622품목 / 26주 / 각 PSI View 94,172행 / 227 Part**.
  분류 3,619품목의 유효 정책 진단과 미분류 3품목 Source-only 진단을 보존한다.
  원 FSN Gate로 전 품목 차단·신규 주문 0건이다. Recommended View는 Baseline을 따르며 보충 효과를 주장하지 않는다.
- Calculation ID `644a31bc-d43c-5d03-9f1f-754936160e0a`;
  Manifest Hash `df75520ec5b22e962557efc1eeb6db7bace2053320ef8c02a9666929175f37cb`.
  Baseline Hash `9c685e72c3e26a29f5df0870aa57af2c20ee49e1ed22e389b0bd6a5056f62dbd`로 기존 Preview와 일치했다.
- Runtime Build Hash `2551bde62b7e00d8144d50ca8047c8b5678535cdc925de61228aeedae8b13208`,
  기동 사양 Hash `17b194c4c48d44c4ac6f61c2f01b4965c4e1d41b58ad164cfbb1398065834698`.
- 무인증 401, 권한 없는 Subject 403, 사용자 Hash 입력/멱등 Scope 충돌 422,
  운영 Claim 경로 404, 다른 프로젝트 데이터 미반환을 실제 HTTP에서 확인했다.
  기존 개발계정·JWT 검증·실제 프로젝트 권한을 사용했고 이를 테스트 Override로 대체하지 않았다.
- 개발 PostgreSQL은 인증·권한 조회의 읽기 전용 연결만 사용했다.
  계정·권한·기존 Secret·로그인 시각 변경, Migration·공용 Runtime 배포·실제 발주·Commit·Push는 없다.
  로컬 SQLite Append-only 증적 저장만 수행했고 게시 Outbox는 0건이다.
- Platform `.tmp/inventory-local-services-20261014/`의 `tcp-verification-receipt.json`,
  `deployment-spec.json`, `calculation-evidence.sqlite3`가 실제 통신 증적이다.
  인증정보는 기록하지 않으며 임시 증적은 Commit 대상이 아니다.
- 신규 Runtime 테스트 9건, Platform 관련 **517 passed**.
  InventoryEngine 전체 **804 passed / 27 skipped / 639 subtests passed**. 기존 Framework 경고는 별도로 남아 있다.
  현재 Claim·운영 권고·최적 정책 선정·화면 연결은 이 검증의 완료 범위가 아니다.


## 계산 전용 실행 계약 1.0.0 — 선행 Offline 검증 이력 (2026-10-14)

운영 Run/Claim/Result Bundle과 별도인 `inventory-development-calculation`을 추가했다.
이 절의 현재 상태가 아래 2026-10-12 준비 이력보다 우선한다. 기존 FSN 설정과 운영 승인 조건은 그대로다.

- 계약: `DEVELOPMENT + CALCULATION_ONLY`, 계산 ID `calculation_id`와 `attempt_no`를 사용한다.
  `engine_run_claimed`·자동 게시·자동 발주는 모두 `false`다.
  Config/Canonical `2.1.0`, Strategy Plan `1.4.0`을 계산 계약 버전과 혼동하지 않는다.
  [Command Schema](../../schemas/inventory-development-calculation-command.schema.json)와
  [Manifest Schema](../../schemas/inventory-development-calculation-manifest.schema.json)를 닫힌 구조로 검증한다.
- 입력: 정확한 소유자·Bundle ID/Hash로 Mathematical 요청, Config, 분류 원문을 조회한다.
  원 Source 전체를 검증·보존한 뒤 내부 계산만 품목별로 나눈다. Calendar·기초재고·정책 기간·원 Canonical Hash는 변하지 않는다.
  실행 시 설치된 Mathematical/PSI 구현 Hash를 고정하며 이미 완료된 결과 복구에는 저장된 구현 Binding을 보존한다.
- 출력: 원 Source Artifact, 품목 Part, 전체 품목/주차 Coverage·Hash·실제 PSI 지표를 담은 Manifest다.
  [계산기](../../src/dsio_inventory_engine/run_inventory/development_calculation.py)가 실제 정책 계산과 PSI를 실행한다.
  [개발 API](../../src/dsio_inventory_engine/run_inventory/development_api.py)는 별도 인증·개발 환경을 요구한다.
  미분류는 `SOURCE_ONLY`; 운영 부적격/주문 Gate 차단 품목에는 신규 주문을 생성하지 않는다.
  계산 가능한 개발 사례는 실제 Mathematical 전략·공통 행동 Guard를 사용하며 `REVIEW`도 자동 게시되지 않는다.

| Artifact | Contract Key | Reference suffix |
|---|---|---|
| 원 입력 | `inventory.development_calculation_input` | `:input` |
| 분할 계산·PSI·정책·Gate·행동 | `inventory.development_calculation_part` | `:part:000000`부터 순차 |
| 최종 봉인 | `inventory.development_calculation_manifest` | `:manifest` |

공통 Prefix는 `artifact:inventory-development:<tenant>:<project>:<calculation_id>:<attempt_no>`다.
기존 `artifact:inventory:<engine_run_id>...`나 정책 분석 Manifest로 치환할 수 없다.
Append-only SQLite Adapter의 `commit_artifacts()`를 재사용하며 게시 Outbox를 만들지 않는다.
동일 Reference/Hash는 멱등, 다른 내용은 충돌한다. Part가 모두 저장·검증되기 전에는 최종 Manifest가 없다.

### Gate와 분할 복구 규칙

1. 원 Canonical/Config/분류/정책 Hash, Scope, W0 이전 이력·Cut-off, 전체 품목 Universe를 먼저 검증한다.
2. 기본 16품목씩 전 기간을 계산하며 기존 관측 제한과 Part 16,000,000-byte 제한을 유지한다.
3. 분류 품목의 정책 진단은 7축 유효 정책을 소비한다. 미분류에 전략을 부여하거나 FSN 미확인을 해제하지 않는다.
4. 차단 품목은 Baseline PSI를 보존하고 신규 주문을 만들지 않는다.
   그 Recommended View는 보충 실행 성공이 아니라 **차단된 계산 View**다.
5. 각 Part를 원자 저장하고 전체 품목/주차 누락·중복·원 Gate 불일치·PSI 수량/지표를 검증한다.
6. 중단 시 완료 Part를 재사용한다. 완료 결과 조회·Replay는 원천 조회·PSI 재계산 없이 저장된 Source/모든 Part를 검증한다.
7. 누락·변조 Artifact는 실패시킨다. 동일 입력 Retry가 아닌 변경 입력은 기존 계산 ID의 증적을 덮어쓰지 않는다.

### 실제 개발 검증과 미완료 경계

- V100 3,622품목/26주를 **227 Part**, 각 PSI View **94,172행**으로 저장·복구했다.
  `EFFECTIVE_POLICY` 3,619품목, `SOURCE_ONLY` 3품목; FSN 등 원 Gate 때문에 전 품목 차단·신규 주문 0건이다.
  원 파일, Config Draft, 활성 Revision, 운영 Handoff 상태는 변경하지 않았다.
- Manifest Hash `6386120c3d5d459071390b59866fe585e6fedc4551f5600487b71cee40859e61`.
  비용은 `NOT_AVAILABLE / COST_PROFILE_NOT_BOUND`다.
  Evidence는 Platform `.tmp/inventory-development-calculation-20261014/verified/`의 SQLite·검증 Summary에 보존한다.
  `replay-verification-summary.json`에서 분할 Baseline Row의 Hash가 기존 Preview
  `9c685e72c3e26a29f5df0870aa57af2c20ee49e1ed22e389b0bd6a5056f62dbd`와 동일함을 확인했다.
- 전체 테스트: InventoryEngine **795 passed / 27 skipped / 639 subtests passed**, Platform 관련 **501 passed**.
  크기 제한·권한·계약 변조·원 Gate 우회·중단·누락/충돌·운영 게시 차단을 검증했다.
  Backend/Engine API 연결은 로컬 ASGI Offline 검증이며 공용 Runtime 배포·실제 DB Claim E2E가 아니다.
- Part 최대 1,675,141 bytes, SQLite 428,494,848 bytes. 원 입력 전체 검증의 최대 RSS는 약 1.48 GB다.
  분할 저장 완료와 전체 입력의 상수 메모리 처리를 구분한다.
- 이번에는 PostgreSQL Write·Migration 적용·기존 Runtime 설치/배포·Secret 변경·Commit·Push를 수행하지 않았다.
  실제 승인 입력·Claim, 비용/PPO/Stress, 공용 배포는 후속이다. 최신 작업 순서는 [개발 기준선](IO_DEVELOPMENT_BASELINE.md)을 따른다.


## V100 Claim 전 개발 계산 — 2026-10-12 준비 이력

- 사용자가 `DSE/C100/V100`, Plan/W0 `202512`, 26주 Horizon의 개발 계산 범위를 **3,622품목**으로 승인했다.
  원 Master 7,000품목과 성공 Demand Run `e5b43229-c69e-4e08-a8ab-5515bfb0f2f3`의 봉인 Forecast는 변경하지 않는다.
  나머지 3,378품목은 `EXCLUDED_ENDED_DEMAND_WINDOW`로 보존하며 Forecast를 0으로 만들지 않는다.
  Demand의 `demand_end_dt`는 첫 제외 주차이므로 `<=202512`가 종료 경계다. 업무 단종 승인으로 해석하지 않는다.
- 개발 전용 [Canonical 2.1 Schema](../../schemas/canonical_input_v21.schema.json)는
  `development_universe`와 `upstream_bindings`를 추가한다. 원 Calendar/Master/Forecast ID·Hash와
  Canonical 변환 ID·Hash를 구분하고 범위·누락·변조·운영 환경 유입을 검증한다.
  Canonical 버전 `2.1.0`, Config 버전 `2.1.0`, Strategy Plan `1.4.0`은 서로 다른 계약이다.
- W0 이전 `202412~202511` 52주 수요로 독립 `SYNTHETIC_BOH`·연결된 이전 EOH/미도착 주문을 생성했다.
  시작재고·이전 기말재고·정책·입고예정·고객주문 5종은 로컬 봉인·Replay를 확인했다.
  빈 고객주문은 개발 Fixture일 뿐 ERP 주문 완전성을 증명하지 않는다. 현재 Master 캡처도 과거 실제 Master 복원은 아니다.
- 분류 Preview는 3,619품목 분류/3품목 미분류다. Snapshot
  `bbdc697b-bf7c-5765-b5d8-09a27c260948`, Content Hash
  `ad8a82d7eed940595743bdbbe3064dfb613965882a94f367a2ed963fd6efc79c`를 원문으로 보존했다.
  Source Revision은 `DEVELOPMENT-PINNED-WINDOW-*`이며 운영 Actual Close와 구분한다.
  ABC 단가는 합성 1 USD, FSN은 기존 `OPERATIONAL` 설정을 유지한 `UNVERIFIED`, PLC는 `UNVERIFIED + SHADOW`다.
- **Baseline Preview 94,172행**을 실제 PSI에서 계산했다. PSI Hash는
  `9c685e72c3e26a29f5df0870aa57af2c20ee49e1ed22e389b0bd6a5056f62dbd`이며 동일 입력 Replay·EOH→BOH를 검증했다.
  Source 정책 진단 94,172건은 `SOURCE_POLICY_DIAGNOSTIC_ONLY_NOT_EFFECTIVE_POLICY_RUN`이다.
  이 진단을 7축 유효 정책 보충 행동, Recommended PSI, 정책 탐색/최적 선택, Run Child 또는 Parent Bundle로 표시하지 않는다.
- FSN 26주 창의 W0 이전 마감 기록은 **202509~202511 3주**다. 202512 기록은 판단 주차 자료이므로 이력에서 제외한다.
  52주 이력 파일 Hash가 마감 근거를 대신하지 않는다. 운영 적격·자동 게시·발주는 모두 `false`다.
  `ITEM_POLICY_NOT_OPERATIONALLY_ELIGIBLE`과 전체 보충 실행의 `RECOMMENDATION_OBSERVATION_LIMIT`을 확인했다.
  135,487,214-byte 정책 진단의 분할 저장/검증도 다음 구현 대상이며 기존 제한을 늘리지 않았다.
- Config Draft `2708e79a-82b5-4c82-8261-5a236077bb18`와 기존 활성 Revision을 그대로 유지한다.
  분류 DB 승인, 8종 Source Mapping 승인 Input Set, 실제 Claim용 Template/Manifest, 실제 Claim/Recommended PSI E2E는 아직 없다.
  기존 Cycle 권위 검증의 운영 Handoff Gate를 우회하지 않으며 별도 개발 계산 실행 역할·버전은 미확정이다.
- InventoryEngine **768 passed / 27 skipped / 639 subtests passed**, Platform 관련 **210 passed** 및 정적 검사 통과.
  Platform `.tmp/inventory-v100-e2e-20261012/v100-inputs/v100-verification-summary.json`에 파일별 SHA-256을 보존했다.
  Summary Hash `03dca2d85bd36e3a25e20e08f1bdf0a2ff36fe97d5676c9ebdafe159c93a5c63`.
  후보 Wheel은 생성·Source 일치만 검증했으며 기존 Runtime 설치/배포는 유지했다. DB Write·Commit·Push는 없었다.

## 1. 완료한 범위와 적용 경계

- 실제 Run의 Mathematical 입력과 같은 Config·분류 Snapshot에서 만든 정책 탐색 결과를 검증하고,
  품목별 선택 및 선택적으로 장기 평가를 Run 부속 Artifact로 봉인한다.
- 탐색 결과는 [정책 탐색 계약](IO_POLICY_SEARCH_CONTRACT.md), 선택은
  [선택 계약](IO_POLICY_SELECTION_CONTRACT.md), 평가는
  [장기 평가 계약](IO_POLICY_SENSITIVITY_CONTRACT.md)을 그대로 사용한다.
- 기존 PSI Result Bundle 및 운영 Mathematical 결과를 개발 선정 정책으로 교체하지 않는다.
  부속 결과의 `operational_policy_approved`, `automatic_publish_allowed`,
  `automatic_order_allowed`는 모두 `false`다. 기존 부모 PSI의 승인 규칙도 변경하지 않는다.
- Config `2.1.0`은 탐색·평가 사양을 봉인한 Strategy Execution Plan `1.3.0/1.4.0`에 연결한다.
  Engine은 정확한 ID/Hash의 Config·전체 분류·Mathematical 요청에서 분석 입력을 자동 생성한다.
  기존 명시적 `policy_analysis_inputs` 경로와 Plan `1.1.0`/`1.2.0`의 Hash는 유지한다.
- Platform Claim·Site Input 승인/조회는 Config `2.1.0`에서 분석 사양 누락·설정 불일치·전체 분류
  Hash 불일치를 거부한다. 이는 로컬 코드/Offline 검증 완료이며 실제 DB Claim 생성·배포는 아니다.
  승인 입력 Export와 등록 Cost/PPO/Stress Resolver의 개발 조립 경로까지 구현했다.
  실제 DB 입력 게시·Claim 생성 및 공용 Runtime 연결 완료를 뜻하지 않는다.
- UI와 조회 API는 구현했지만 서비스에 배포하지 않았다. 과거 Run에 부속 결과가 없으면
  `NOT_BOUND`로 표시한다. 과거 장기 실험 결과를 새 Run 결과로 자동 연결하지 않는다.

## 2. 봉인 구조

Manifest 계약은 `inventory-policy-analysis-manifest`, Version `1.0.0`, Contract Key
`inventory.policy_analysis`다. Schema는
[`inventory-policy-analysis-manifest.schema.json`](../../schemas/inventory-policy-analysis-manifest.schema.json)이다.

Reference는 `artifact:inventory:<engine_run_id>:<attempt_no>:policy-analysis`다.
Child Reference는 그 아래 `<role>:<content_hash>`로 고정한다.

| Role | 내용 | 필수 여부 |
|---|---|---|
| `ACTUAL_INPUT` | 원 Mathematical 요청·정규화된 Config·전체 분류 Snapshot | 필수 |
| `SEARCH` | 같은 Run 입력에서 실제 PSI/공통 행동 검증기로 계산한 탐색 결과 | 필수 |
| `SELECTION` | 원 탐색 결과에서 재계산한 품목별 선택 결과 | 필수 |
| `EVALUATION` | 원본 탐색들을 이용한 장기 평가·비용 재평가·종료 효과 | 선택 |
| `EVALUATION_SOURCE` | 장기 평가의 다른 기간·수요 경로에 대한 원본 탐색 결과 | 평가에 따라 필수 |

Manifest에 Run·Attempt·Tenant·Project·Company·Subsidiary·Site, Config Hash,
Site Binding Hash, 분류 Snapshot ID·전체 Hash·Effective Policy Hash,
Canonical Input Hash, Strategy Plan Hash, 부모 PSI Bundle Hash와 Child Hash를 보존한다.

**전체 분류 Snapshot Hash와 Effective Policy Hash는 다르다.** 기존 Run Input Binding의
`INVENTORY_CLASSIFICATION.source_content_hash`는 Effective Policy Hash다. 이를 전체
Snapshot Hash와 혼동하지 않도록 Manifest에 두 필드를 별도로 둔다.

## 3. 연결 및 복구 검증

1. 실제 Run의 봉인된 Mathematical 입력에서 로컬 Shadow 사본을 만들고 탐색 결과를 재계산한다.
   실행 목적만 Shadow로 바꾸며 원 입력·제약·승인 상태는 수정하지 않는다.
2. 전달받은 탐색 결과와 완전히 일치하는지 검사한 후 기존 선택기로 선택을 재계산한다.
3. 평가가 있으면 Reference Case는 이 Run의 탐색 Hash와 같아야 한다. 기준 비용도 Run의
   탐색 비용과 같아야 한다. 다른 비용 Profile은 민감도 Case로만 허용한다.
4. 평가의 모든 Source Hash·Reference를 닫힌 집합으로 확인하고 기존 평가기로 Replay한다.
5. Manifest·Child들을 PSI Bundle과 **같은 기존 Evidence 트랜잭션**에 Append-only 저장한다.
   Durable Outbox에는 `policy_analysis_reference`와 `policy_analysis_content_hash`를 추가한다.
6. 같은 Reference·동일 Artifact는 멱등 처리한다. 다른 Hash·내용은 충돌한다.
7. 복구는 저장된 Bundle·Outbox를 읽는다. Run PSI를 새로 실행하지 않지만, 저장된 분석의
   탐색·선택·평가 Replay는 무결성 검증을 위해 수행한다. 최신 Source를 다시 탐색하지 않는다.

새 PostgreSQL 테이블·Migration은 추가하지 않았다. 저장 대상은 현재 로컬 SQLite Evidence
Adapter다. 운영 Object Storage/PostgreSQL Adapter 및 큰 탐색 결과의 성능 검증은 별도다.
검증 과정에는 여러 차례 Replay가 포함되므로 대규모 품목의 실행 시간·메모리 보장은 하지 않는다.

## 4. Platform 조회와 화면

Platform API는 프로젝트 범위의 Run 조회 권한을 먼저 검사한다. 사용자가 Hash나 Artifact
Reference를 입력하는 화면·API는 만들지 않는다. Backend가 Run ID·Attempt로 정해진 Reference를 읽는다.

- `GET /api/v1/engine-studio/inventory/runs`: 실제 Ledger 목록, 안정적인 정렬·페이지 처리
- `GET /api/v1/engine-studio/inventory/runs/{engine_run_id}/policy-analysis`: 품목별 결과 페이지

Backend는 Hash·Run·Scope·입력 관계를 검증하고 표시용 결과를 만든다. 재고 계산을 다시 하지 않으며
`SEALED_LINEAGE_VERIFIED_NOT_RECALCULATED`로 검증 수준을 표시한다.
Artifact Reader가 미설정이면 `503`, 변조·누락 Child는 `422`다. Manifest 자체가 권위 Reader에서
존재하지 않는다고 확인된 기존 선택 분석 Run만 `NOT_BOUND`이며, 조회 장애를 빈 성공으로 처리하지 않는다.
자동 분석이 필수로 봉인된 Run은 Manifest 미존재도 오류로 처리한다.

Engine Studio Inventory 실행 탭은 실제 Ledger를 표시하고 다음을 구분한다.

- 후보별 정책 종류·RMOS·EMOS·검토주기·CSL, 서비스·비용·평균재고·발주횟수·미선정 사유
- 비용 단위·통화·개발 Source·배부 가정과 비용 미연결 상태(0원으로 표시하지 않음)
- 고정 정책의 새 수요 경로 평가, 장기 서비스 미달·Backorder 및 기간·비용별 선정 변화
- 종료 구간 행동 변화·미도착 주문, 미연결·비교 불가 상태
- Run/Manifest/Child의 읽기 전용 Reference와 Hash

`CSL`은 정책 입력이고 수요 충족률은 결과 지표다. 개발 가정 비용은 실제 Landed Cost·관세와
동일하지 않다. 평가가 연결됐어도 Holdout 또는 종료 비교가 없으면 각각 미검증으로 표시한다.

## 5. 최초 연결 검증 (2026-10-06 이력)

- InventoryEngine 전체 Unit·Contract·Offline Integration: **712 passed, 22 skipped,
  631 subtests passed**(112.28초). 이번 분석 연결 테스트는 7건이다.
- Backend 최신 변경과 함께 실제 PSI → 분석 봉인 → 로컬 SQLite → Platform Reader의 교차
  저장소 Offline 테스트를 다시 실행했다. 작은 단일 품목·평가 Reference Case 기준이며
  실제 개발 DB Claim·장기 다품목 화면 E2E로 해석하지 않는다.
- dsai-platform Inventory 관련 Backend: **335 passed**, 기존 Starlette 경고 24건.
- Frontend 결과·정책 탐색 테스트: **9 passed**. SSR 표시와 상태·번역·Query 구조를 검증했다.
- 320/768/1024/1440px 로컬 SSR에서 카드 표시·본문 가로 넘침을 확인했다.
  이는 실제 DB·로그인·브라우저 상호작용 통합 검증이 아니다.
- Frontend Source 타입 검사는 통과했다. 일반 `tsc`는 기존 `.next` Validator가 삭제된 Route를
  참조해 실패했다. 해당 생성 파일을 제외한 Source 검사 결과를 전체 Next 빌드 통과로 주장하지 않는다.
- Ruff·Format, JSON Schema 29개, `git diff --check`를 확인했다. Coverage 비율은 측정하지 않았다.
- DB 접속·Write, Migration 적용, 서비스 기능 활성화·배포, Commit·Push는 수행하지 않았다.

### 5.1. 로컬 기준선 재검증 (2026-10-12)

- Engine 전체 Unit·Contract·Offline Integration: **727 passed, 7 skipped,
  653 subtests passed**(118.36초). Platform·Demand 소스 경로를 명시해 교차 저장소 검증을 포함했다.
  Skip 7건은 PostgreSQL 읽기 전용 통합 검증을 명시적으로 비활성화한 결과다.
- Platform Inventory·Demand Handoff·공통 Run/RBAC 관련 Backend **417 passed**, 기존 경고 24건.
  Frontend 정책 탐색·결과·7축·Matrix·VED·Runtime controls **28 passed**.
- Engine Ruff·Format(223개 Python 파일), Platform 변경 Python 28개 Ruff·Format,
  JSON Schema 29개, 양쪽 Config `2.1.0` Schema 일치와 `git diff --check`를 확인했다.
- Frontend 전체 `next build`가 타입 검사·정적 페이지 생성까지 통과했다. 기존 `.next/dev/types`가
  삭제된 Portal 경로를 참조해 처음 실패했고, 실행 중인 Next 서버가 없는지 확인한 뒤 생성 캐시만
  복구 가능한 임시 경로에 보관했다. Source/환경 설정 파일이나 의존성은 변경하지 않았다.
- Python 3.12.10, Polars 1.41.2, Torch 2.12.1의 기존 검증 환경을 사용했다.
  Coverage·Linux/CUDA·실제 브라우저 로그인/DB E2E·공용 배포 검증은 아니다.
- 이번 승인 범위는 관련 코드·Schema·테스트·문서를 저장소별 독립 로컬 Commit으로 고정하는 것이다.
  개인 보고서·환경·IDE·임시 파일은 제외한다. Push·DB 접속/Write·Migration·배포는 수행하지 않는다.

재현 명령(저장소 루트, 테스트 의존성이 있는 Python):

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests:tests/support:. \
DSAI_PLATFORM_ROOT=/absolute/path/to/dsai-platform \
DEMAND_ENGINE_ROOT=/absolute/path/to/DemandEngine \
IO_READONLY_INTEGRATION=0 IO_SOURCE_READONLY_INTEGRATION=0 \
python -m pytest -p no:cacheprovider tests/unit tests/contract tests/integration -q
```

### 5.2. 자동 분석 최종 로컬 검증 (2026-10-12)

- Engine 전체 **739 passed, 7 skipped, 653 subtests passed**(133.72초). 기존 Starlette 경고 1건.
  Skip은 읽기 전용 PostgreSQL 통합을 비활성화한 결과이며 실제 DB를 조회하지 않았다.
- 신규 자동 분석 Offline 통합 **12 passed**. 정확한 Catalog 입력부터 실제 탐색/평가,
  원자 저장·복구 및 Platform 조회, Config ID/Hash·누락·사전 결과 주입·변조 차단을 포함한다.
- Platform 관련 Backend **429 passed**, 기존 경고 24건. 이번 신규 자동 분석 계약 테스트는 12건이다.
- Engine Ruff/Format(226개 Python 파일), 관련 Platform Python Ruff/Format,
  Engine JSON Schema 30개와 양쪽 분석 사양 Schema 일치, `git diff --check`를 확인했다.
- 별도 경계 검토에서 필수 분석 저장/복구 누락, 분류의 Config ID 불일치, 이벤트 루프 내 장시간
  계산을 보완했고 수정 경계에서 추가 HIGH/MEDIUM 문제는 발견하지 못했다.
- `oma-docs` CLI 미설치로 승인된 두 계약 문서는 코드·Diff 및 로컬 링크를 직접 대사했다.
  다른 작업의 Intelligence 파일과 개인 보고서·환경·임시 산출물은 변경 대상에서 제외했다.
- 실제 DB Claim/Write·Migration·공용 Runtime/서비스 배포·이번 변경 Commit/Push는 하지 않았다.
  이번에 Frontend를 수정하거나 브라우저 E2E/대규모 자료 성능·Coverage를 검증하지 않았다.

## 6. 자동 분석 봉인 계약 (2026-10-12)

닫힌 사양은 `inventory-policy-analysis-spec/1.0.0`이고 Plan `1.3.0`의
`policy_analysis_spec`에 포함한다. [사양 Schema](../../schemas/inventory_policy_analysis_spec.schema.json)와
[Python 계약](../../src/dsio_inventory_engine/inventory_contracts/policy_analysis_spec.py)을 따른다.

- Config ID·Revision·Schema Version·정규화된 전체 Config Hash를 고정한다.
- 분류 Snapshot ID·전체 Content Hash·Effective Policy Hash를 별도로 고정한다.
- `search_spec`은 Config `2.1.0`의 정규화된 `policy_search` 전체다. 후보 범위·Lookback·서비스
  조건·선택 기준·개발 비용 가정·별도 Search Hash를 봉인한다. Backend가 Hash를 계산한다.
- `evaluation_spec.mode=DISABLED`는 평가 미요청이다. 평가 결과를 임의로 채우지 않는다.
  `SEALED_SYNTHETIC_CASES`는 기준 Case, VALIDATION/HOLDOUT, Seed, 정확한 Mathematical 요청
  ID/전체 Hash, 비용 Profile, 기간 종료 비교쌍을 고정한다. Case 24·비용 Profile 12개 상한이다.
  현재 Run 기준 Case는 정확히 하나이며 VALIDATION이다. 다른 Case는 봉인된 원 요청부터 계산한다.
- 탐색이 Disabled이면 평가도 Disabled여야 한다. 평가 연결만으로 장기·Holdout·종료 효과를
  모두 검증했다고 표시하지 않는다. 준비한 Case가 작은 Reference뿐이면 그 범위만 평가한다.
- 기존 Landed Cost가 있으면 Plan `1.3.0`에 함께 유지한다. 비용 입력을 새 분석 설정으로 덮어쓰지 않는다.

### 자동 생성과 복구

[입력 Catalog](../../src/dsio_inventory_engine/infrastructure/policy_analysis_inputs.py)는 개발 전용
Config·전체 분류·원 Mathematical 요청을 정확한 ID로 Append-only 저장한다. Tenant/Project,
내용 Hash, 중복 JSON Key, 크기, Symlink를 검사한다. Hash는 ERP 권위·승인을 증명하지 않는다.

[자동 분석 Producer/Resolver](../../src/dsio_inventory_engine/run_inventory/policy_analysis_producer.py)는
저장된 Search를 입력 권위로 받지 않고 원 Mathematical 요청부터 실제 탐색·선택·평가를 생성한다.
계산은 이벤트 루프 밖에서 수행하며 Canonical 부모 PSI와 운영 Mathematical 정책을 바꾸지 않는다.
최소 `ExactMathematicalCommandResolver`는 수학적 입력만 지원한다. Cost Profile/PPO/Stress가 있으면
조용히 누락하지 않고 명시적으로 거부한다. 이 경우 기존 신뢰된 Resource Resolver를 wrapper에
연결해야 한다. 아래 Plan `1.4.0` 경로는 이 최소 Resolver와 별도로 Cost/PPO/Stress를 조립한다.

새 `ACTUAL_INPUT/1.1.0`은 분석 사양 Hash와 평가 원 Mathematical 요청도 보존한다.
Pipeline·Writer·Reader 모두 `search.enabled`와 분석 Evidence/pointer 존재의 일치를 강제한다.
불완전한 과거 Record를 발견하면 성공 복구하지 않는다. 재시도 복구는 Catalog/Resolver를
다시 호출하지 않고 저장된 원 입력에서 탐색·선택·평가를 Replay한다.

## 7. 승인 Source와 Runtime 조립 (2026-10-12 로컬 완료)

### Source → Claim → Resource Resolver

- [Runtime Manifest 계약](../../src/dsio_inventory_engine/inventory_contracts/runtime_inputs.py)과
  [Schema](../../schemas/inventory_runtime_input_manifest.schema.json)는
  `inventory-runtime-input-manifest/1.0.0`이다. Config·전체 분류 원문·Mathematical Template,
  선택적 평가 원 요청·PPO JSON Model·Cost Profile·Stress Payload·Run Cost Input을 닫힌 집합으로 봉인한다.
- [입력 조립기](../../src/dsio_inventory_engine/run_inventory/approved_inputs.py)의
  `build_runtime_input_manifest`가 원문에서 Manifest와 Plan `1.4.0`을 생성한다.
  Plan의 `runtime_input_binding` ID/Hash와 전체 Resource Hash가 Claim·Site Binding에 포함된다.
  Resource Hash는 Manifest Binding과 Plan Version만 제외해 자기참조를 피한다.
  기존 Plan `1.1.0/1.2.0/1.3.0` 직렬화·Hash는 유지한다.
- Mathematical 요청은 Claim 전에 봉인한 [Template](../../schemas/inventory_mathematical_request_template.schema.json)이다.
  Runtime은 `PLATFORM_ASSIGNED_RUN_ID`를 새 Run ID로만 치환한다. 시작재고·정책·수요·Calendar·이력은
  바꾸지 않으며, Canonical Hash·보충 Config Hash·정책 입력 Hash를 다시 검사한다.
- Platform 내부 인증 API `POST /api/v1/engine-studio/inventory/runs/{engine_run_id}/runtime-inputs/resolve`는
  저장된 Run/Attempt/Claim Hash, 승인 Site Input Set, 승인 Config·전체 분류 Hash를 대조한다.
  Tenant/Project 권한을 먼저 확인하며 최신 자료를 고르지 않는다. 요청은 Platform Dispatch이고,
  사용자에게 Snapshot Hash 입력을 요구하지 않는다. 조회 Audit에는 식별자·Hash·건수만 기록한다.
- [HTTP Reader](../../src/dsio_inventory_engine/infrastructure/http/runtime_inputs.py)는 이 API에서
  정확히 봉인된 원문을 읽는다. [Runtime 조립](../../src/dsio_inventory_engine/run_inventory/runtime_composition.py)은
  Resolver → 실제 PSI/분석 → Evidence → Outbox → 기존 Worker Event/게시 경로를 연결한다.
  개발 전용이며 배포·활성화·실제 DB Write는 수행하지 않았다.

### 원문 보존과 Resource 승인 경계

- [Classification Publisher](../../src/dsio_inventory_engine/infrastructure/postgresql/classification_snapshot.py)는
  `--original-artifact-root` 또는 `IO_CLASSIFICATION_ARTIFACT_ROOT`가 지정되면 DB Projection 전에 전체 원문을
  기존 입력 Catalog에 Append-only 보존한다. Archive 실패 시 DB 승인/게시를 시작하지 않는다.
  과거 호출 호환성 때문에 옵션은 선택이며 원문이 없는 기존 Snapshot을 자동 복원하지 않는다.
- DB `revenue_share` 등의 소수 정밀도가 원문과 달라 Projection에서 전체 분류 Hash를 재구성하지 않는다.
  승인 원문·Template·Manifest가 실제 Catalog에 준비되지 않았다면 실행을 차단한다.
  파일 Hash만으로 DB 승인이나 ERP Source 권위를 주장하지 않는다.
- Platform Catalog는 `DSAI_INVENTORY_RUNTIME_INPUT_CATALOG_ROOT`로 지정한다. 미설정은 `503`,
  승인/Scope/Hash/자원 집합 불일치는 차단한다. 기존 JSONB Plan/Input Set을 사용하며 신규 Migration은 없다.
- Cost/Stress는 현재 개발 Registry의 정확한 Binding·Payload·Seed·구현을 사용한다.
  PPO는 설치 측에 등록된 구현 ID/Version/Hash와 봉인 Model ID/Hash·승인 Reference를 대조한 뒤
  Shadow로만 실행한다. Request로 임의 구현을 등록하거나 Python/Pickle 코드를 읽지 않는다.
- Landed Cost는 봉인 Run Cost Input과 exact Revision Set ID/Hash·평가기준일 Projection을 연결한다.
  미등록 Resource는 조용히 제외하지 않고 차단한다. 등록 PPO의 실행 실패는 해당 Child만 실패시키며
  Mathematical 결과는 보존한다. `REVIEW` 결과는 Evidence를 보존하되 자동 게시하지 않는다.
- 저장 결과 복구는 Source HTTP/Catalog/Resource Resolver를 다시 호출하지 않고 기존 Evidence를 검증한다.

### 최종 Offline 검증

- Engine 전체 Unit·Contract·Offline Integration: **762 passed, 7 skipped, 653 subtests passed**(124.92초).
  이번 연결 23건은 Manifest/Schema 7건, 조립·재시도·위장 Resource·PPO 실패·REVIEW 10건,
  HTTP 교차 저장소/엄격 JSON 4건, 원본 선보존/Archive 실패 2건이다.
- Platform Inventory·Demand Handoff·공통 Run/RBAC 관련 Backend: **432 passed**, 기존 Starlette 경고 25건.
  승인 Export 16건을 포함한다. Engine 교차 저장소 테스트에서 실제 HTTP Reader → Registry →
  PSI/분석/비용 → SQLite → 복구를 검증했다.
- Engine Python 234개와 관련 Backend Python 17개 Ruff·Format, Schema 32개,
  양쪽 공유 계약 AST/Schema 동일성, `git diff --check`를 확인했다.
- 독립 읽기 전용 리뷰에서 HIGH/MEDIUM 잔여는 발견하지 못했다. 권고된 승인 입력 묶음과 Manifest의
  Canonical Hash 직접 대조를 추가했다. Coverage·대규모 품목 성능·새 장기 Holdout은 검증하지 않았다.
- 실제 PostgreSQL/Runtime 접속·Write·Migration·배포·Commit·Push는 수행하지 않았다.
  다른 세션의 Inventory Intelligence 변경과 개인 보고서는 보존했다. `oma-docs` CLI 미설치로
  승인한 두 문서는 코드·Diff·로컬 링크를 직접 대사했다.

## 8. 실제 입력·설치 준비 상태 대사 (2026-10-12 승인 전 이력)

실제 확인 결과는 [개발 기준선의 최신 대사](IO_DEVELOPMENT_BASELINE.md)를 우선한다.
사용자는 첫 개발 E2E를 `default / 9ed62f41 / DSE / C100 / V100`의 수학적 PSI로 결정했고
Lane 비용은 후속으로 분리했다. 최소 V100 Plan은 PPO/Stress 빈 목록, Cost Profile `null`,
Landed Cost Binding 없음으로 준비한다. 이는 구성 기준이며 실제 Config/Plan/Claim 게시가 아니다.

- 개발 PostgreSQL `192.168.0.46:5432/dsai`를 11:20~11:26 KST에 readonly/Repeatable Read로 조회했다.
  074~077은 success다. `dsai.inventory_site_input_sets`와 078 Ledger는 실제로 없다.
- V100의 활성 Config는 Schema `2.0.0`, Revision `d4be4ad6-9e2e-47ce-84fa-d0c0b870fa4b`다.
  원문 Config Hash와 Schema Identity는 검증됐지만 `policy_search`와 `2.1.0` Revision은 없다.
- 승인 7축 분류 `e54e5649-ad2b-578c-b1fa-2e5603446895`의 DB Header/Projection은 존재한다.
  확인한 저장소·설정 경로·관련 임시 파일에서는 전체 원문 Catalog 파일을 찾지 못했다.
  실제 V100 Template·Manifest·Inventory 5종의 봉인 Receipt도 확보하지 못했다.
- 지정 성공 Demand Run `e45cc634-29f0-4d59-8cfd-8be2ff18944c`는 Evidence pending이다.
  Runtime `http://192.168.0.71:8011`의 이 Run Handoff GET은 unavailable이었다.
  성공 Run 상태/Canary Snapshot 참조를 검증된 Forecast Snapshot으로 대신 사용하지 않는다.
- Canonical·Runtime·Cost·Evidence·Planning Catalog와 원본 분류 경로는 미설정이다.
  개발 기준선에 기록한 로컬 경로는 제안이며 디렉터리 생성·환경 설정 변경은 하지 않았다.
- Engine `.venv`의 editable 패키지는 0.15.0이지만 Runtime Extra는 미설치이고,
  Platform 검증 환경은 Source를 명시적 PYTHONPATH로 읽는다. 실제 공용 Runtime의 설치 모델/
  구현 Hash는 미확인이다. 테스트의 임의 구현 Hash와 옛 `dist` 0.11.0 Wheel은 배포 기준이 아니다.
- 연구 모델 14개는 구조/Hash가 맞지만 V101·별도 TGSM 학습 Revision용이며 학습 종료일이
  2027/2028이다. V100 역사 입력의 Run 모델로 승격하거나 Scope·날짜를 바꿔 사용하지 않는다.
- 이번에는 DB 메타데이터/Config와 지정 Receipt만 읽었다. 원천 전체 적재·모델 학습·새 Export·
  Claim·DB Write·Migration·배포·Commit·Push는 하지 않았다. 기존 전체 회귀 수치는 이전 절의 이력이다.

## 9. 초기 승인 후 적용 이력 (2026-10-12, 신규 Demand 실행 이전)

최신 적용 상태는 [개발 기준선](IO_DEVELOPMENT_BASELINE.md)의 문서 맨 위 절을 우선한다.
위 readonly 대사 이후 사용자가 Migration 078·V100 개발 입력·로컬 IO E2E를 승인했고,
추가로 5090 Demand Runtime의 Handoff 지원 Release 적용·8011 기동도 승인했다.

- `192.168.0.46:5432/dsai`에 078을 원자 적용했다. DDL/Ledger·멱등 재실행과 8개 FK/Hash/승인/
  크기/불변성/중복 Canary를 확인했다. 검사 Row는 모두 Rollback했으며 `inventory_site_input_sets`는 0행이다.
  Schema-only Backup·Restore Catalog를 확인했지만 데이터 백업/전체 Restore 시험을 뜻하지 않는다. 074~077은 재적용하지 않았다.
- V100 Config 2.1 Revision 3 Draft는 `2708e79a-82b5-4c82-8261-5a236077bb18`, Hash는
  `2d9b69b2bd255a2317c507e94921152cdb148ba95207d97af17bb07d5bca773f`다.
  기존 분류·Matrix·Overlay는 보존했고 활성 2.0 Revision은 바꾸지 않았다. 탐색·평가는 Disabled이며
  비용·PPO·Stress도 첫 Run에 결합하지 않는다. 새 분류/Template/Manifest 승인이나 Run 생성은 아직 없다.
- [설치 Build 계약](../../src/dsio_inventory_engine/infrastructure/runtime_build.py)의 Recipe는
  `inventory-python-source-closure-v1`이다. 설치 패키지 전체 Source와 알고리즘 ID/Version에서 구현 Hash를
  생성하고 사전 Build 기록과 완전히 같은 경우만 등록한다. 테스트 임의 Hash나 Request가 자기 등록한 Hash는 허용하지 않는다.
- [로컬 실행 CLI](../../src/dsio_inventory_engine/entrypoints/approved_local_runtime.py)는 정확한
  `default/9ed62f41/DSE/C100/V100` Plan 1.4의 수학 전략만 받는다. 승인 입력은 Platform HTTP Reader로 읽고
  기존 Worker의 Event/Publish와 로컬 SQLite Evidence/복구를 사용한다. 인증된 Loopback Platform URL이 필요하다.
  One-shot CLI이며 영속 Queue·공용 Inventory HTTP 서버 배포·실제 Claim 실행을 뜻하지 않는다.
- 로컬 준비 Root는 `/Users/igwanhyeong/PycharmProjects/dsai-platform/.tmp/inventory-v100-e2e-20261012`다.
  독립 `runtime-venv`에 새 0.15.0 Wheel을 설치했고 Source/설치 Hash와 의존성 검사를 통과했다.
  Catalog 디렉터리는 생성했지만 비어 있으며 기존 환경 파일·공용 Inventory Runtime을 변경하지 않았다.

### Demand 접속 복구와 Export 미완료

- 기존 5090 서버의 SSH는 접속됐지만 8011 Listener/기존 PID가 없었고 `d671ede` Current Release에는
  Handoff/Export 코드가 없었다. 종료 이유는 확인하지 않았다. 기존 Release·모델/vendor·외부 Secret을
  보존한 채 `a43ca735195605e33459620bd9eaac07ad943d3c` 후보를 검증해 8011에 적용했다.
  새 경로는 `/home/leekwanhyeong/runtime/DemandEngine-v3-runtime-a43ca735-inventory-handoff`다.
  기존 실행 Contract를 그대로 사용하며 이전 Release의 Rollback Pointer도 보존했다.
- Platform의 기존 `http://192.168.0.71:8011` 설정에서 Health는 `200/ready`, 정확한 Run
  `e45cc634-29f0-4d59-8cfd-8be2ff18944c`의 Handoff는 **`200/NOT_REQUESTED`**다.
  실제 Catalog의 기존 Forecast/input Manifest는 있지만 실행 전 Shared Receipt·Export Receipt가 없다.
  Platform은 `demand_engine_inventory_handoff_not_ready`로 차단한다. 새 Forecast Run을 자동 실행하지 않았다.
- 실행 전 공유 Calendar/Master 검증을 하지 않은 과거 Run에 Receipt를 사후 생성하거나 현재 Source로
  덮어써 성공 Export처럼 만들지 않는다. 새 실행은 정확한 Planning Input을 먼저 봉인하고 Handoff를 opt-in해야 한다.
- `dsdm`에서 5종 Legacy 재고/주문/정책 테이블은 보이지 않고 V100 Master 7,000품목의 MOQ·발주배수·
  Lead Time·Service Level은 모두 NULL이다. 이는 다른 Schema/ERP의 Source 부재 판정이 아니다.
  5종 Inventory Snapshot 봉인·새 분류 원문·승인 Input Set·실제 V100 PSI 결과는 아직 미준비다.

### 검증 수치와 한계

- Engine **777 passed, 7 skipped, 653 subtests passed**; Platform Inventory/Handoff **489 passed**;
  설치 Wheel **48 passed, 9 subtests passed**. Engine Python 237개와 신규 Platform Helper의 Ruff/Format,
  양쪽 `git diff --check`, `pip check`를 통과했다. Demand 관련 로컬 테스트 48건, 실제 5090 후보의
  666개 Python 구문·Import·임시 포트 및 적용 후 인증 HTTP 조회도 확인했다.
- 별도 Source 정확성·대형 PSI 성능·Coverage·전체 Frontend/브라우저 E2E는 검증하지 않았다.
  DB Schema/Draft 적용 및 Demand Runtime 배포와 실제 IO Run 완료를 구분한다.
  개인 보고서·환경·IDE 변경을 보존했고 Commit/Push·공용 Inventory/Platform 서비스 배포는 하지 않았다.

## 10. V100 새 Demand Run의 실제 검증 (2026-10-12, Export 복구 이전 이력)

- 사용자 선택 Plan은 `20250317_DSE_P12`, Plan/W0 `202512`, Master 기준일 `2025-03-17`, Horizon 26이다.
  Backend의 기존 Producer/승인 Config Selector에서 공유 Calendar/Master와 정확한 Handoff 요청을 봉인했다.
  기존 Demand Config Revision `36844a20-4b45-4173-8a55-86d8a1dc2395`를 유지했고 Master는 7,000품목/EA다.
- 5090 Runtime의 기존 ICL 환경·봉인 Vendor를 다시 연결하고 같은 모델 46개 파일 Hash를 검증했다.
  202512 전용 추론 자료는 52주 Actual 188,344행/3,622품목이며 이력 종료는 `202511`이다.
  모델·Secret·활성 Config를 변경하거나 새 모델 학습을 하지 않았다.
- 처음 실행은 074의 필수 Plan Source 두 컬럼을 Demand INSERT가 누락해 Claim 이전에 Rollback됐다.
  누락 SQLSTATE `23502`, 수정 INSERT 및 잔여 0행을 Canary로 검증했다. 사용자 추가 승인 후
  `plan_source_key=demand.forecast_plan`, `plan_source_contract_version=1.0.0`을 명시하는 저장 계약 `1.7.0`을 적용했다.
  계약 Hash는 `87c952168a12fbfb59484be38764bb70b99c0253ae3bc3b9648e2a961bbe0518`다. DB 제약/074는 변경하지 않았다.
- 적용 Release는 `/home/leekwanhyeong/runtime/DemandEngine-v3-runtime-a43ca735-plan-source-1_7-a64374a0`, PID `1020591`이다.
  Base Commit `a43ca735...` + 승인된 Source/계약 두 파일 Delta, Build Hash
  `a64374a0f292917e5251f4843e60ec56872cef9a45e49e4efc4f0fe89d897886`다. 순수 Git Commit 배포로 표시하지 않는다.
- 정확한 신규 Run `e5b43229-c69e-4e08-a8ab-5515bfb0f2f3`은 `succeeded/completed`, 389,083ms다.
  모델 14개 성공·1개 취소이며, 실제 `POINT`는 94,172행 = 3,622품목 × 26주다.
  품목당 단일 모델·26주 Coverage를 확인했고 불일치는 0품목이다. 원 Guard Receipt는 최초 모델 실행 전에 기록됐다.
- Shared Binding Hash `882b8261126b408d82a65728ee99ae61c06cba7dca8e9e6fb8e98534d69ac957`,
  Input Manifest Hash `082881c9291e8e65f2c22fb8872578ac264fdee1f5575e732f7a0fdb758e9f6c`다.
  Cycle/Revision ID는 요청에 고정했지만 실제 Cycle 등록·Inventory Claim·PSI 생성은 아니다.
- **Export는 `FAILED/SELECTION_EMPTY`다.** 같은 Run의 Current Winner `POINT`는 있으나
  `dsdm.tb_fcst_weekly_selection_decision`은 0건이다. Export가 요구하는 선택 증적 생성/조회 경로의 연결이 남았다.
  Export Snapshot/Receipt는 없고 Run Evidence는 `pending`이다. 다른 Run 조회·임의 Decision ID·사후 Guard 위조로 우회하지 않는다.
- 추가 정적 대사에서 Runtime Receipt는 Backend 필수 필드 `planning_input_artifact_id`와
  `planning_input_artifact_sha256`를 반환하지 않는다. 원 실행 전 Reference/File Hash에서 동일하게 전달해야 한다.
  현재 실측한 선행 차단 `SELECTION_EMPTY`와 이 정적 계약 불일치는 구분한다.
- Platform 기존 Runtime URL·인증의 실제 Transport는 Ready이며 새 Run의 Handoff는
  `demand_engine_inventory_handoff_not_ready`로 차단된다. 성공 Run 상태만으로 IO 입장을 허용하지 않는다.
- Master Gap 3,378개는 개발 Source 수요창 종료일 `<=202512`와 일치하며 미시작 수요창은 0개다.
  업무 단종 승인이나 무수요 보장으로 해석하지 않고 자동 제외/0수요도 적용하지 않았다. Plan Date 고정과 과거 Master 재구성은 다르다.
- 관련 Demand 테스트 **132 passed**, Platform **46 passed**. 수정 전 회귀 실패/수정 후 성공,
  Ruff Lint·Diff Check·신규 Platform Helper Format을 확인했다. 기존 Demand 파일 전체 Format 차이는 유지했다.
  실제 Run 검증 Hash는 `609168faba481ed99629547c0abe73b0ea13315091b63b73e5c32e4a3948b6a5`다.
  이는 Backend 준비 Helper→5090 내부 실행 API 검증이며 Platform 화면/서비스 전체 E2E는 아니다.
  기존 Inventory 2.1 Draft·새 분류·5종 입력·Input Set은 그대로 미준비며 개인 보고서·환경·IDE·Commit/Push는 변경하지 않았다.

## 11. Current Winner 선택 증적과 Export-only 복구 계약 (2026-10-12 완료)

- 권위 Source는 정확한 성공 Run/Scope의 `tb_sum_fcst_weekly_res`와 연결된 모델 Run이다.
  `CURRENT_WINNER_PUBLICATION`은 이미 게시된 선택을 입증한다. 실제 사유 `ANALYTICS_PRIMARY_V1`을 보존하며,
  사후 성능평가를 수행하거나 새 최적 모델을 선택한 것으로 표시하지 않는다.
- `METRIC_SELECTION_DECISION` 조회 경로는 별도로 유지한다. 빈 성능평가 Decision 테이블에서 다른 Run을 찾거나
  가짜 Metric Snapshot·점수·Decision Row를 생성하는 Fallback은 없다.
- Run/공유 입력/품목별 모델·Candidate·결과 ID·주차·수량·원 선택 메타데이터에서
  `demand-current-winner-selection-evidence-v1`을 만들고 `CWSEL-*` Artifact 선택 ID와 Hash를 고정한다.
  선택 ID는 DB 성능평가 Decision ID가 아니다. Source가 달라지면 기존 Run의 봉인을 덮어쓰지 않고 충돌한다.
- 선택 증적 게시 전 품목당 단일 모델·전체 주차 Coverage·중복·Master 적격·원 Manifest/Profile Hash를 검증한다.
  같은 읽기 전용 Repeatable Read에서 결정과 결과를 소비하고, 선택 Artifact → Forecast Parquet/Manifest → Handoff Receipt 순으로 봉인한다.
  실패 시 Source 계산을 재실행하지 않으며 같은 Source/Reference의 복구는 멱등이다.
- `demand-current-winner-selection-reference-v1`은 Source 종류·Artifact ID/Content Hash·정확한 Run·공유 입력 Hash·
  Selection Hash·품목 수·행 수를 전달한다. Backend는 닫힌 Key/Version/Hash/Count와 상위 Receipt의 동일성을 검증한다.
  기존 V1 Receipt에는 새 Source를 임의 주입하지 않는다. 이 Reference만으로 DB 게시/운영 적격을 승격할 수 없다.
- 원 요청 파일 Hash와 실제 Claim의 Config ID/Revision/Hash·멱등 Hash·요청 Hash가 같아야 한다.
  원 Planning Catalog 파일·저장된 입력·실행 전 Observation/Guard를 대사해
  `planning_input_artifact_id`·`planning_input_artifact_sha256`를 동일하게 전달한다. 사후 Guard 수정은 없다.
- Export-only 명령은 DemandEngine `scripts/run_demand/recover_inventory_forecast_export.py`다.
  정확한 Run, 원 요청 파일/Hash, 원 Planning Catalog와 Artifact Root만 받으며 Forecast Dispatch/학습/DB Write를 수행하지 않는다.
  Runtime 재시작 후에는 선택 증적·원 Reference·Guard·Manifest·Parquet Hash를 검증해 기존 Receipt를 읽는다.
- 5090 적용 Release `DemandEngine-v3-runtime-a43ca735-winner-export-1a27d7ca`, Build Hash
  `1a27d7ca0d0e8a1a7ec02e1c4dd9ef6da2d3005596c34eca1f7091c2555151a9`는
  Base `a43ca735...` + 승인된 10개 파일 Delta다. 이전 Release/모델/Secret/1.7.0 저장 계약을 보존했다.
- 실제 Run `e5b43229-c69e-4e08-a8ab-5515bfb0f2f3`의 3,622품목/26주/94,172행을 봉인했다.
  Snapshot `FCST-b374749b4f18c959c58e7d0a71d2e83bd9d8f7391ab03140f1467f68941a73b1`,
  Content Hash `279d8dc8000ad2d5974c37829e733ac390cb5cc0aeee38b1f33e038c06b40ec1`,
  Selection Evidence Hash `daa37f884105268130eeb7ab72ae357a75c059cda22e488575a4ed8028bec38d`다.
- 실제 재호출 ID/Hash 동일, 원 Run/모델/전체 Forecast Row 및 Guard 파일 지문 동일,
  Platform 실제 권한/Scope Repository/HTTP/닫힌 Receipt 검증을 확인했다. Demand 150/Platform 116 테스트와 정적 검사를 통과했다.
  Forecast 재실행·DB Write·Migration·Backend 서비스 배포·Commit/Push는 없었다.
- Export는 `ARTIFACT_SEALED`지만 `database_published=false`, `operational_io_eligible=false`다.
  Gap 3,378개·Run 전체 Evidence `pending`·미등록 Cycle·Inventory 입력/Claim/PSI 미준비를 유지한다.

## 12. 당시 남은 작업 순서 — 2026-10-12 이력

이 절은 이전 계획을 보존한다. 계산 전용 계약·분할 저장/복구는 위 2026-10-14 절에서 완료했으며,
현재 순서는 [개발 기준선](IO_DEVELOPMENT_BASELINE.md)의 최신 절을 따른다.

**현재 기준선 — 완료**

- 위 V100 절의 개발 범위·5종 입력·분류/PSI Preview와 11절의 원 Export가 기준선이다.
  Forecast/Export 복구·범위 결정·합성 입력 생성·Baseline Preview를 다시 실행 대상으로 나열하지 않는다.

**1. 계산·증적 전용 실행 계약과 분할 계산을 구현 — 다음 작업**

- 대상: InventoryEngine `inventory_engine_dev`, Platform `codex/inventory-psi-strategy-v2`, 개발 PostgreSQL.
  FSN 설정과 운영 Gate를 유지하고 개발 진단을 운영 Mathematical 결과와 구분한다. 미분류 3품목 처리도 명시한다.
  동일 원 입력/Calendar/시작재고를 유지한 품목 분할·대용량 증적 저장/Replay를 검증한다.
  완료 조건은 운영 적격 승격 없이 개발 계산을 실행하고 재현하는 닫힌 계약이다.

**2. 승인 Input Set과 실제 로컬 Claim E2E를 연결 — 다음 작업, 1번 이후 직렬**

- Draft/분류 승인·8종 Source Mapping·정확한 Template/Manifest → 실제 Claim → Baseline/Math 결과 →
  조회·자동 게시 차단·복구를 검증한다. 개발 DB/로컬 검증 승인은 확보했지만 차단 해소 전 실행하지 않는다.
  적용된 074~078은 재실행하지 않는다. 비용/PPO/Stress·공용 배포와 Commit·Push는 별도 후속이다.

실행 계약 확정은 직렬이다. 이후 Engine 분할 처리와 Backend 검증은 독립 파일별 병렬 검증이 가능하며,
승인 입력·Claim·실제 E2E는 위 순서대로 직렬 진행한다.
