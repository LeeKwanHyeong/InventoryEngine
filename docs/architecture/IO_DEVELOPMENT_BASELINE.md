# InventoryEngine 개발 기준선과 작업 순서

## 2026-10-14 실제 로컬 서비스 실행·조회·재기동 복구 — 현재 기준선

**맥북 내부에서 dsai-platform Backend와 Inventory Runtime을 별도 프로세스로 기동하고 실제 HTTP API 실행·조회·복구를 검증했다.**
외부 접속 주소는 공개하지 않으며 이 문서에도 로컬 호스트·포트는 기록하지 않는다.
대상은 InventoryEngine `inventory_engine_dev`, Platform `codex/inventory-psi-strategy-v2`다.
아래 분할 계산의 Offline 결과는 선행 검증 이력으로 보존한다.

- 기존 전체 Backend 시작 과정에는 자동 Migration·계정 Seed 등이 포함돼 있어 이번에 실행하지 않았다.
  계산 API만 노출하는 별도 로컬 구성을 사용했다. `Planning Cycle Claim`, 게시, 회원가입·비밀번호 변경 경로는 노출하지 않는다.
- 기존 개발계정의 비밀번호·활성 상태와 실제 프로젝트 실행/조회 권한을 검증했다.
  JWT 검증·프로젝트 권한 확인을 우회하지 않았고, 계정·권한·기존 Secret을 변경하지 않았다.
  로컬 자격증명 확인은 로그인 시각을 갱신하지 않는다. 인증 응답·Secret은 증적과 문서에 저장하지 않는다.
- 개발 PostgreSQL은 인증·권한 확인에만 사용하며 연결의 `transaction_read_only=on`을 확인했다.
  직접 연결이 되지 않아 기존 SSH 경로를 로컬 읽기 전용 접속 포워딩에만 사용했다.
  공용 서버의 프로그램·설정·Runtime은 변경하지 않았다.
- 정확한 Source 파일 SHA-256·Command·설치 Source Build를 `inventory-local-calculation-deployment 1.0.0`에 고정했다.
  새로운 수요 실행·Snapshot 승격·FSN 설정 변경은 없다. 현재 서버는 검증한 봉인 입력만 선택하는 정상 계산 모드다.
- 계산 실행 중 **2개 Part가 실제로 Commit된 직후 Runtime을 종료**했다.
  Backend는 `503`을 반환했고 최종 Manifest는 없었다.
  이후 Backend와 Runtime의 원천 조회를 모두 끈 복구 모드로 재기동해 저장된 입력/Part에서 나머지를 계산했다.
- 완료 결과를 API로 조회하고 Runtime을 다시 재기동한 뒤 같은 요청을 Replay했다.
  Calculation ID·Manifest Hash가 동일했고 원천 재조회 없이 복구했다.
  검증 종료 후에는 장애 주입 없는 정상 로컬 모드로 전환했다. 저장한 증적은 삭제하지 않았다.

| 확인 항목 | 결과 |
|---|---|
| V100 범위 | 3,622품목 × 26주, 각 PSI View 94,172행, **227 Part** |
| Baseline | 기존 Preview와 Row Hash·집계 지표 일치 |
| 원 설정/자료 | 원 Source SHA-256·Config·분류·FSN/운영 Gate 유지 |
| 신규 주문/게시 | **0건**, 자동 게시·발주 `false`, 게시 Outbox 0건 |
| 인증/권한 | 무인증 `401`, 권한 없는 Subject `403`, 사용자 Hash 입력 `422` |
| 운영 유입 | 로컬 Claim 경로 `404`, 다른 프로젝트 데이터 미반환, 동일 Key의 다른 Site 요청 `422` |
| 중단/복구 | Part 2건 Commit 후 강제 종료 → 원천 조회 없는 복구 → 조회 → 재기동 Replay, 동일 Manifest |
| 실제 통신 검증 시간 | 약 134.6초. 개발 장비 단일 관측값이며 SLA 판정이 아님 |

전 품목의 운영 Gate가 차단이므로 Recommended View는 Baseline을 따른다.
이 검증은 서비스 연결·계산 증적·복구 검증이며 신규 보충 권고, 최적 정책 선정, 실제 Inventory Claim 성공으로 표시하지 않는다.
전체 입력 메모리 검증과 비용 `NOT_AVAILABLE / COST_PROFILE_NOT_BOUND` 조건도 그대로다.

### 봉인 증적과 검증 이력

- Calculation ID: `644a31bc-d43c-5d03-9f1f-754936160e0a`.
  Manifest Hash: `df75520ec5b22e962557efc1eeb6db7bace2053320ef8c02a9666929175f37cb`.
- Canonical Hash: `34d6317d8cbe2dd5e8aed63eaa0a3de08d5c98da9455ce3fb897ee294a4cfcb9`.
  Baseline PSI Hash: `9c685e72c3e26a29f5df0870aa57af2c20ee49e1ed22e389b0bd6a5056f62dbd`.
- Runtime Build Hash: `2551bde62b7e00d8144d50ca8047c8b5678535cdc925de61228aeedae8b13208`.
  기동 사양 Hash: `17b194c4c48d44c4ac6f61c2f01b4965c4e1d41b58ad164cfbb1398065834698`.
- 증적은 Platform `.tmp/inventory-local-services-20261014/`의
  `deployment-spec.json`, `tcp-verification-receipt.json`, `calculation-evidence.sqlite3`에 보존한다.
  실행 프로세스 정보는 `running-services.json`에만 기록한다. 모두 임시 개발 증적이며 Commit 대상이 아니다.
- 로컬 Runtime 신규 테스트 9건, Platform 관련 **517 passed**.
  InventoryEngine 전체 **804 passed / 27 skipped / 639 subtests passed**.
  기존 Framework 경고는 별도로 남아 있다.
  Ruff·Format·Diff Check 통과. Bandit은 현재 환경에 없어 별도 자동 취약점 스캔 완료로 보고하지 않는다.
- 이번 작업에는 PostgreSQL Write·Migration·공용 Runtime 배포·실제 발주·Secret/권한 변경·Commit·Push가 없다.
  로컬 SQLite의 계산 증적 저장만 수행했다. 기존 개인 보고서 변경은 보존했다.

### 남은 작업 순서 — 현재 실행 대상

**1. 검증한 코드·증적·문서의 저장소별 기준선을 고정 — 다음 작업 / Commit·Push는 별도 요청**

- 대상: InventoryEngine `inventory_engine_dev`, Platform `codex/inventory-psi-strategy-v2`.
  현재 로컬 서비스와 테스트의 Build·계약·입력 Hash를 대사하고 관련 변경만 선별한다.
  개인 보고서·환경·IDE·임시 증적은 제외한다. 완료 조건은 이후 연동이 참조할 독립적인 저장소별 기준선이다.

**2. 실제 Claim의 데이터·계약 차단을 정리 — 차단됨 / 계산 경로와 분리**

- 대상: InventoryEngine·Platform Backend. FSN 마감 근거, 미분류, 운영 Handoff 및
  Draft/분류 승인·Source Mapping·Template/Manifest를 대조한다.
  로컬 계산 성공을 운영 승인으로 바꾸거나 기존 Gate를 해제하지 않는다.
  Source 대사와 별도 성능 개선은 독립 검증 가능하며 공통 승인 입력 계약은 직렬이다.

**3. 승인 입력의 실제 로컬 Claim E2E — 차단 해소 후 직렬**

- 대상: 개발 PostgreSQL·Platform Backend·로컬 Inventory Runtime.
  실제 DB Write 대상과 승인 범위를 실행 전에 확인하고 Claim·조회·게시 차단·복구를 검증한다.
  기존 074~078은 재실행하지 않는다. 공용 Runtime 재배포·신규 Migration은 별도 승인 대상이다.
  비용/PPO/Stress는 별도 입력·Plan으로 후속 검증한다.


## 2026-10-14 계산 전용 실행·분할 증적·복구 — 선행 Offline 이력

**기존 FSN 설정과 운영 Gate를 유지한 개발 계산 전용 계약·구현·Offline 검증을 완료했다. 실제 Planning Cycle Claim과 운영 권고 게시 완료는 아니다.**

- 대상: InventoryEngine `inventory_engine_dev`, dsai-platform Backend `codex/inventory-psi-strategy-v2`.
  `inventory-development-calculation` / `CALCULATION_ONLY` `1.0.0`을 운영 Run과 분리했다.
  `engine_run_claimed`, `automatic_publish_allowed`, `automatic_order_allowed`는 항상 `false`다.
  기존 Config `2.1.0`, Canonical `2.1.0`, Strategy Plan `1.4.0`의 의미나 운영 승인 조건은 변경하지 않았다.
- 정확한 원 Mathematical 요청·Config·분류 원문을 한 번 검증하고 Source Artifact에 보존한다.
  품목별 26주 상태 이월은 유지하며 기본 **16품목씩** 분할한다.
  분할 때문에 원 Canonical/Config/Effective Policy Hash나 제외 품목의 의미를 바꾸지 않는다.
  기존 관측·행동 크기 제한과 **Part당 16,000,000 bytes** 제한을 늘리지 않는다.
- 분류된 품목은 7축 유효 정책을 적용한 정책 진단을 계산한다. 운영 부적격·주문 Gate 차단 품목에는 신규 행동을 만들지 않는다.
  미분류 품목은 `SOURCE_ONLY` 진단으로 보존하며 전략을 부여하지 않는다.
  계산 가능한 개발 사례에서는 실제 Mathematical 전략·공통 행동 검증기·PSI를 사용한다.
  `REVIEW`의 계산·증적은 가능하지만 이 경로 전체가 자동 게시·발주 불가다.
- Source → Part별 원자 저장 → 전체 Coverage/Hash/지표 검증 → 최종 Manifest 봉인 순서다.
  동일 Reference/동일 내용은 멱등 처리하고 다른 내용은 충돌한다. 중단 시 완료 Part만 검증·재사용한다.
  최종 복구는 저장된 Source와 모든 Part를 검증하며 원천 조회·PSI 재계산 없이 동일 Manifest를 반환한다.
  누락/변조 Part, 원 Gate 변경, 품목/기간 누락, 다른 Command는 실패한다.
- 저장은 기존 로컬 SQLite Append-only Artifact Adapter를 재사용한다. 게시 Outbox는 **0건**이다.
  PostgreSQL `TB_IO_*` 게시나 Evidence 온라인 보관/Archive 운영 계약을 대신하지 않는다.
  Backend의 계산 실행·조회 API는 프로젝트 권한을 검사하고 명시적으로 연결하지 않으면 `503`이다.
  운영 환경에서 비활성화하며 별도 로컬 Engine API도 인증이 필수다. 사용자가 Snapshot Hash를 입력하지 않는다.

### V100 검증 결과와 해석

| 항목 | 검증 결과 |
|---|---|
| 입력 | 기존 성공 Demand Run / W0 `202512` / V100 / 3,622품목 / 26주, 원 파일·FSN 설정 불변 |
| 분할 | **227개 Part**, 각 PSI View **94,172행**, 최대 Part **1,675,141 bytes** |
| 정책 진단 | `EFFECTIVE_POLICY` 3,619품목, 미분류 `SOURCE_ONLY` 3품목 |
| 운영 Gate | 3,622품목 전부 차단 유지, 신규 주문 **0건**, 자동 게시·발주 **false** |
| PSI 비교 | 차단 품목의 Recommended View는 Baseline을 그대로 따른다. 보충 효과나 최적 정책 선정 성공으로 표시하지 않는다. |
| 비용 | `NOT_AVAILABLE / COST_PROFILE_NOT_BOUND` 유지 |
| 저장/복구 | Part 합계 377,801,491 bytes, 로컬 SQLite 428,494,848 bytes, 동일 Manifest 조회·Replay |
| 성능 관측 | 첫 계산 약 62.1초, 조회+Replay 두 번 약 56.7초, 프로세스 최대 RSS 약 1.48 GB |

위 시간·메모리는 개발 장비의 단일 관측값이며 SLA 합격 기준이 아니다.
결과 저장은 작은 Part로 제한하지만 원 Canonical/Source 전체 검증은 메모리에 올린다.
따라서 **전체 입력 처리의 상수 메모리 보장까지 완료했다고 주장하지 않는다.**

- Manifest Reference: `artifact:inventory-development:default:9ed62f41:404e044b-d492-50c6-b710-1ab8d33fe78b:1:manifest`.
  Hash: `6386120c3d5d459071390b59866fe585e6fedc4551f5600487b71cee40859e61`.
- Canonical Hash: `34d6317d8cbe2dd5e8aed63eaa0a3de08d5c98da9455ce3fb897ee294a4cfcb9`.
  Config Hash: `2d9b69b2bd255a2317c507e94921152cdb148ba95207d97af17bb07d5bca773f`.
  Effective Policy Hash: `bb57eb3b5cc3417e77622ad8f192db254f3f0b93f47b35f8141bb0f66760b235`.
- Part별 Baseline Row를 순서대로 검증한 Hash는 기존 Preview와 동일한
  `9c685e72c3e26a29f5df0870aa57af2c20ee49e1ed22e389b0bd6a5056f62dbd`다.
  원 입력을 조회하지 않는 Replay와 기존 PSI/집계 지표 일치를 별도로 검증했다.
- 구현 Hash: Mathematical `7d146a92318c0b0629729545410a48d18ac2d8ceaab4cc3a89ef5b5e0aa248e1`,
  PSI `3e6d98b4ef798e3f532f57d4710da2d5b7ee1a1f083bfbf409a0ef05cf53c95a`.
- InventoryEngine 전체 **795 passed / 27 skipped / 639 subtests passed**, Platform 관련 **501 passed**.
  기존 Framework 경고는 별도로 남아 있다. 원본 입력 변조·크기 제한·권한·운영 유입·중단 복구·Gate 우회 Negative Test를 통과했다.
  Platform의 기존 Planning Cycle 테스트 3건은 공식 Handoff `to_dict()` 직렬화로 정합화했으며 운영 검증을 완화하지 않았다.
- 증적: Platform `.tmp/inventory-development-calculation-20261014/verified/`의
  `calculation-evidence.sqlite3`, `verification-summary.json`, `replay-verification-summary.json`.
  임시 개발 증적이며 Commit 대상이 아니다.
  이번 작업에서 PostgreSQL Write·Migration 적용·Runtime 설치/배포·Secret 변경·Commit·Push는 없었다.

### 당시 남은 작업 순서 — Offline 완료 시점 이력

**1. 계산 전용 로컬 서비스의 연결 사양을 고정 — 다음 작업**

- 대상: Platform Backend·로컬 Inventory Runtime. 이번 Offline Source/Artifact 위치와 정확한 Build Hash를
  개발 Service에 연결하는 설정을 준비한다. 원 입력/FSN Gate를 바꾸지 않고 API로 실행·조회·복구할 수 있어야 한다.
  완료 조건은 사용자에게 Hash 입력을 요구하지 않는 개발 계산 경로다. 공용 Runtime 재배포는 별도 승인 대상이다.

**2. 실제 Claim 연결의 데이터·계약 차단을 정리 — 차단됨 / 로컬 계산과 분리**

- 대상: InventoryEngine와 Platform Backend. FSN 26주 마감 근거 부족, 미분류 품목, 운영 부적격 Handoff,
  Draft/분류 승인·8종 Source Mapping·Template/Manifest를 대조한다.
  계산 전용 완료를 운영 승인으로 바꾸지 않는다. 완료 조건은 운영 Gate를 우회하지 않는 실제 Claim 경로다.
  Source 대사와 메모리 추가 개선은 독립 검증 가능하지만 승인 입력·Claim 연결은 직렬이다.

**3. 준비된 승인 입력으로 로컬 Claim E2E를 검증 — 차단 해소 후 직렬 / 실제 DB Write 실행 전 대상 확인**

- 대상: 개발 PostgreSQL·Platform Backend·로컬 Inventory Runtime.
  정확한 입력 승인 묶음 → Claim → 계산/조회 → 자동 게시 차단·실패 복구를 검증한다.
  기존 074~078은 재실행하지 않는다. 현재 차단 상태에서 승인·게시를 강제하지 않는다.
  공용 Runtime 재배포와 신규 Migration은 별도 승인 대상이다.

**4. 검증된 변경을 저장소별로 고정 — 별도 Commit·Push 요청 후**

- InventoryEngine `inventory_engine_dev`, Platform `codex/inventory-psi-strategy-v2`의 관련 변경만 선별한다.
  개인 보고서·환경·IDE·임시 증적은 제외한다. 비용/PPO/Stress는 별도 입력·Plan으로 후속 검증한다.


## 2026-10-12 V100 개발 입력·계산 증적 검증 — 이전 검증 이력

**사용자는 3,622품목 개발 범위와 기존 FSN 설정 유지를 확정했다. 계산·증적 검증은 완료했지만 실제 Claim·Recommended PSI E2E는 미완료다.**
대상은 InventoryEngine `inventory_engine_dev`, Platform Backend `codex/inventory-psi-strategy-v2`다.
Plan `20250317_DSE_P12` / W0 `202512` / Horizon 26주이며, 기존 성공 Demand Run
`e5b43229-c69e-4e08-a8ab-5515bfb0f2f3`의 봉인 Export를 재사용했다. Forecast와 Export를 다시 실행하지 않았다.
아래 과거 절의 완료·미완료 상태와 작업 순서는 당시 이력이다. 현재 실행 순서는 위 2026-10-14 절을 우선한다.

| 대상 | 완료한 개발 검증 | 미완료·적용 경계 |
|---|---|---|
| 계산 품목 범위 | Forecast 3,622품목만 명시적으로 계산하고 3,378품목은 `EXCLUDED_ENDED_DEMAND_WINDOW`로 보존했다. | 원 Master 7,000품목·Demand Snapshot은 그대로다. 제외는 업무 단종 승인이나 0수요 보장이 아니다. |
| Canonical 입력 | 개발 전용 Canonical `2.1.0`의 `development_universe`·`upstream_bindings`를 구현했다. 시작재고·이전 기말재고·정책·입고예정·고객주문 5종을 로컬에 봉인했다. | 개발 범위의 운영 환경 유입을 차단한다. 8종 Source Mapping 승인 묶음·실제 Claim용 Template/Manifest는 아직 연결하지 않았다. |
| 시작재고와 Cut-off | W0 이전 `202412~202511` 52주/188,344행의 봉인 수요 이력으로 독립 합성 재고 Simulation을 수행했다. 이전 EOH와 W0 BOH 일치·미도착 주문 연결을 검증했다. | `SYNTHETIC_BOH`·합성 정책이다. 고객주문은 명시적인 개발용 빈 Fixture이며 실제 ERP 재고 정확성·주문 Coverage 검증이 아니다. |
| 202512 분류 원문 | 3,619품목 분류·3품목 미분류, Snapshot `bbdc697b-bf7c-5765-b5d8-09a27c260948`을 로컬에 보존했다. 이력 창은 W0 이전에서 종료한다. | ABC 단가는 개발용 1 USD Fixture다. FSN은 설정을 바꾸지 않은 `OPERATIONAL + UNVERIFIED`, PLC는 `UNVERIFIED + SHADOW`다. 분류 DB 승인·운영 적격 승격은 없다. |
| Baseline PSI | 실제 공통 PSI로 3,622품목 × 26주 = **94,172행**을 계산하고 EOH→BOH 이월·비음수 재고·동일 Hash Replay를 확인했다. | `BASELINE_PREVIEW_NOT_CLAIMED` 결과다. Run Child·Parent Result Bundle 게시나 실제 Runtime 실행 완료로 표시하지 않는다. |
| 수학 정책 진단 | 안전재고·ROP·목표재고의 Source 정책 기반 진단 94,172건을 로컬에 보존했다. | `SOURCE_POLICY_DIAGNOSTIC_ONLY_NOT_EFFECTIVE_POLICY_RUN`이다. 7축 유효 정책이 적용된 보충 행동·Recommended PSI·최적 정책 선정 결과가 아니다. |

### 유지한 설정과 차단 근거

- Config 2.1 Draft `2708e79a-82b5-4c82-8261-5a236077bb18`는 여전히 `draft`다.
  활성 Revision `d4be4ad6-9e2e-47ce-84fa-d0c0b870fa4b`와 기존 FSN 설정을 변경하지 않았다.
- FSN의 26주 Lookback에서 W0 이전 마감 근거는 `202509~202511` **3주**뿐이다.
  DB의 `202512` 마감 기록은 판단 주차이므로 제외했다. 52주 이력 파일 봉인은 운영 마감 증명을 대신하지 않는다.
  3,622품목 모두 운영 적격·자동 게시·자동 발주가 `false`이며 `ITEM_POLICY_NOT_OPERATIONALLY_ELIGIBLE`을 확인했다.
- 전체 3,622품목/26주 보충 실행은 `RECOMMENDATION_OBSERVATION_LIMIT`에서 차단된다.
  현재 관측 크기·증적 크기 제한을 늘리지 않았다. Source 정책 진단 JSON도 135,487,214 bytes로,
  실제 Run 연결 전에 분할 계산·저장·Replay의 메모리/성능 검증이 필요하다.
- 기존 Planning Cycle 권위 검증은 운영 적격 Handoff를 요구한다. 현재 Handoff의
  `database_published=false`·`operational_io_eligible=false`를 바꿔 우회하지 않았다.
- Master 기준일은 Plan의 `2025-03-17`로 고정하지만, 현재 개발 Master 캡처가 과거 실제 Master의 재구성을 증명하지는 않는다.

### 검증과 증적

- InventoryEngine 전체: **768 passed, 27 skipped, 639 subtests passed**.
  Platform 관련: **210 passed**, 기존 Framework 경고 1건. Ruff·Format·Diff Check 통과.
  합성 BOH 수작업 Golden·호출자 소수 계산 설정 독립성, 범위/원본 Forecast 바인딩/미봉인 입력 방어,
  로컬 봉인의 동일 내용 Replay·다른 내용 충돌·중단/심볼릭 링크 차단을 검증했다.
- Canonical Preview Hash: `34d6317d8cbe2dd5e8aed63eaa0a3de08d5c98da9455ce3fb897ee294a4cfcb9`.
  Baseline PSI Hash: `9c685e72c3e26a29f5df0870aa57af2c20ee49e1ed22e389b0bd6a5056f62dbd`.
- 증적은 Platform `.tmp/inventory-v100-e2e-20261012/v100-inputs/`의
  `v100-verification-summary.json`과 그 안의 13개 파일 SHA-256 목록에 보존했다.
  Summary Content Hash는 `03dca2d85bd36e3a25e20e08f1bdf0a2ff36fe97d5676c9ebdafe159c93a5c63`다.
  이는 Claim 전 준비 증적이지 DB 승인 Input Set이나 Runtime 결과 Receipt가 아니다.
- 0.15.0 후보 Wheel SHA-256 `8a0516505e96c206ccb03cfb6bbbf8629affc930c395ae3274839342a497e322`,
  후보 Build Hash `23d8a7a59620f52d0b4b7d6c433bc4b161b552620bad5aa114a0a680da28bcbf`를 기록하고 Wheel/Source 일치를 확인했다.
  기존 로컬 Runtime 설치와 서버는 변경하지 않았다.
- 이번 입력/계산 검증에서 DB Write·Migration·5090/공용 서비스 배포·Secret 변경·Commit·Push는 없었다.
  원 Demand Run은 `succeeded`, Run 전체 Evidence는 `pending`, 해당 승인 Input Set은 0건이다.

### 당시 남은 작업 순서 — 2026-10-12 이력

**1. 개발용 계산 Run 계약과 품목 분할 처리를 연결 — 다음 작업**

- 대상: InventoryEngine `inventory_engine_dev`, Platform Backend `codex/inventory-psi-strategy-v2`.
  기존 FSN/운영 Gate를 유지하면서 계산·증적 전용 실행을 운영 권고와 구분한다. 미분류 3품목의 처리도 명시한다.
  동일 원 입력 Hash에서 품목별 분할 계산과 대용량 증적 저장/복구를 검증한다.
  완료 조건은 입력이나 정책을 승격하지 않고 개발 계산 결과를 재현하는 계약이다. 새 실행 계약의 버전·구현은 아직 미확정이다.
- 계약 확정 뒤 계산/저장 구현과 Backend 검증은 파일 경계별 병렬 검증이 가능하다. 공통 계약 확정은 선행 직렬 작업이다.

**2. 개발 승인 입력과 실제 로컬 Claim E2E 연결 — 1번 이후 직렬 다음 작업**

- 대상: 개발 PostgreSQL·Platform Backend·로컬 Inventory Runtime.
  Draft/분류 승인, 8종 Source Mapping과 정확한 Template/Manifest를 연결한 뒤
  Claim → Baseline/Mathematical 결과 → 조회·게시 차단·실패 복구를 검증한다.
  개발 DB/로컬 검증 승인은 확보했지만 차단 요소 해소 전에는 실행하지 않는다. 이미 적용한 074~078은 재실행하지 않는다.

**3. 비용·PPO·Stress와 저장소별 변경 고정 — 후속 작업**

- 비용·PPO·Stress는 별도 Source/Plan으로 검증한다. 공용 Runtime 재배포·신규 Migration은 별도 승인 대상이다.
  Commit·Push는 별도 요청 후 InventoryEngine `inventory_engine_dev`와 Platform `codex/inventory-psi-strategy-v2`에서 관련 변경만 선별한다.
  개인 보고서·환경·IDE·임시 산출물은 제외한다.

## 2026-10-12 기존 성공 Run의 선택 증적·Export 복구 — 입력 준비 이전 이력

**Forecast는 재실행하지 않았다. 지정된 성공 Run `e5b43229-c69e-4e08-a8ab-5515bfb0f2f3`의 Export만 복구했다.**
아래 신규 실행/초기 복구 절은 당시 이력이다. 사용자는 구현·검증 후 5090 적용과 실제 증적 게시를 승인했다.

| 대상 | 확인한 완료 범위 |
|---|---|
| DemandEngine `demand_engine_v3` | `CURRENT_WINNER_PUBLICATION`을 성능평가 `METRIC_SELECTION_DECISION`과 분리했다. 실제 게시된 결과 ID·Candidate·모델·수량·선택 순위/사유를 읽기 전용 Repeatable Read로 검증하고 선택 Artifact를 봉인한다. 기존 선택 사유는 `ANALYTICS_PRIMARY_V1`이며 새 최적 모델 선정이나 재학습이 아니다. |
| 원본 전달 계약 | 원 요청 파일 Hash와 기존 Claim의 Config·Revision·Config Hash·멱등 Hash·요청 Hash를 대사한다. 실행 전 Catalog/Observation/Guard를 검증한 원 `planning_input_artifact_id`·`planning_input_artifact_sha256`를 Handoff에 전달한다. 실행 전 파일을 수정하거나 새 Guard를 만들지 않는다. |
| 5090 개발 Runtime | 별도 후보를 실제 Source 읽기 Canary로 검증한 뒤 `http://192.168.0.71:8011`에 적용·재기동했다. 기존 Release·모델·Secret·저장 계약 1.7.0을 보존했다. |
| 실제 증적·Export | 3,622품목 × 26주 = 94,172행, `ARTIFACT_SEALED`. 선택 증적과 Parquet/Manifest/Receipt는 Run별 Artifact에 기록했다. 같은 Export를 재호출해 동일 ID·Hash·Receipt를 확인했다. |
| Platform Backend `codex/inventory-psi-strategy-v2` | 버전 있는 선택 Reference를 닫힌 계약으로 검증·보존한다. 기존 V1 Receipt도 지원하되 Source를 임의 생성하지 않는다. 실제 권한 조회·정확한 Tenant/Project/Run 조회·기존 URL/인증의 5090 HTTP 응답·Receipt 검증이 모두 통과했다. Backend 서비스 자체는 배포하지 않았다. |

- 적용 Release: `/home/leekwanhyeong/runtime/DemandEngine-v3-runtime-a43ca735-winner-export-1a27d7ca`, PID `1043007`.
  Base Commit `a43ca735195605e33459620bd9eaac07ad943d3c` + 승인된 10개 Runtime 파일 Delta다.
  Build Hash `1a27d7ca0d0e8a1a7ec02e1c4dd9ef6da2d3005596c34eca1f7091c2555151a9`이며 순수 Git Commit 배포가 아니다.
  복구 Pointer `DemandEngine-v3-before-winner-export-20261012`로 이전 Release를 보존했다.
- Forecast Snapshot ID: `FCST-b374749b4f18c959c58e7d0a71d2e83bd9d8f7391ab03140f1467f68941a73b1`.
  Content Hash `279d8dc8000ad2d5974c37829e733ac390cb5cc0aeee38b1f33e038c06b40ec1`,
  Manifest Hash `40c63d2acfbc9e153f41810a64cffd3e7b74d70ed5cb88e6039ad012c5b56c7a`.
- 선택 Evidence Hash: `daa37f884105268130eeb7ab72ae357a75c059cda22e488575a4ed8028bec38d`;
  Selection Content Hash `2e7352bf64df56bc5d74b9f843a1b09373bd3835e485a3dc8e835ec3a03479f6`.
  `CWSEL-*`은 해당 Artifact의 선택 식별자이며 성능평가 테이블의 Decision ID라고 표현하지 않는다.
- 원 Planning Artifact ID는 `planning-5a6174247b5981b4cd478676835cd246b654e4da9730a2fe88ca9132ae31a095`,
  파일 Hash도 `5a6174247b5981b4cd478676835cd246b654e4da9730a2fe88ca9132ae31a095`로 유지했다.
- 복구 전후 원 Run/모델/전체 Forecast DB Row 지문과 원 Guard 5개 파일 지문은 동일하다.
  새 Demand Run·Forecast 재실행·DB Write·Migration은 없었다. `tb_fcst_weekly_selection_decision`은 여전히 0건이며
  그 테이블에 임의 성능평가/선택 메타데이터를 채우지 않았다. Run 전체 Evidence 상태도 `pending`으로 유지했다.
- 실제 복구 검증 Hash `1045a4f41523e867c9a76cba1583548066c23bed62729fe691314e97cfac8e7a`;
  Platform 실제 읽기 검증 Hash `1cbdba9d8e50a1769aaa5813089e73be762eaa62746c62c13c9f2af03c09274e`.
  Platform `.tmp/inventory-v100-e2e-20261012/demand-202512/`에 원 실패 이력과 새 Receipt를 따로 보존했다.
- 관련 회귀: Demand **150 passed**, Platform **116 passed**. Ruff Lint·변경 구간 Format·Diff Check 통과.
  재시작 후 증적·Manifest·Parquet Hash 검증, 멱등 복구, 잘못된 Run/원 요청/Claim·혼합 Winner·빈/불완전 주차·변조 차단을 확인했다.

**미완료 경계:** Export 봉인은 새 DB Snapshot 게시나 운영 IO 입장이 아니다.
`database_published=false`, `operational_io_eligible=false`를 유지한다. Master 7,000품목 중 Forecast Gap 3,378개를
자동 제외/0수요로 처리하지 않았다. 실제 Planning Cycle 등록·Inventory 입력 묶음·Claim·PSI는 아직 없고,
공용 Inventory/Platform 배포·Commit·Push도 하지 않았다. 개인 보고서·환경·IDE는 보존했다.

### Export 복구 당시 작업 순서 — 이력, 현재 순서는 문서 맨 위 절 우선

**1. V100의 Forecast Gap 처리와 Plan 기준 입력을 확정 — 다음 작업**

- 대상: InventoryEngine `inventory_engine_dev`, Platform Backend `codex/inventory-psi-strategy-v2`.
  종료 수요창 3,378품목을 어떻게 처리할지 명시하고, 202512 기준 새 분류 원문·Config 2.1 Draft·합성 BOH/정책과 5종 입력을 준비한다.
  완료 조건은 원 Forecast Snapshot과 같은 Calendar/Master를 쓰는 닫힌 입력 묶음이며, 미래 이력·암묵적 0수요를 사용하지 않는 것이다.

**2. 입력 승인·게시와 로컬 V100 수학적 IO E2E — 다음 작업, 1번 이후 직렬**

- 대상: 개발 PostgreSQL·Platform Backend·로컬 Inventory Runtime. 입력 승인/게시 경계와 기존 승인 범위를 대사한 뒤
  Claim → Baseline/Mathematical PSI → 조회·REVIEW 차단·실패 복구를 검증한다. 이미 적용한 074~078은 재실행하지 않는다.
- 비용/PPO/Stress와 공용 서비스 배포는 별도 후속이다. Source 조사·입력 사양 준비는 독립 검증할 수 있지만 봉인·게시·Claim·E2E는 직렬이다.

**3. 저장소별 변경 고정 — 별도 Commit·Push 요청 후**

- 대상: DemandEngine `demand_engine_v3`, InventoryEngine `inventory_engine_dev`, Platform `codex/inventory-psi-strategy-v2`.
  관련 코드·계약·테스트·승인 문서만 선별하고 개인 보고서·환경·IDE·임시 산출물은 제외한다.

## 2026-10-12 V100 새 Demand 실행 검증 — Export 복구 이전 이력

**이 절은 Export 복구 이전의 검증 이력이다. 최신 상태와 작업 순서는 문서 맨 위 절을 따른다.**
사용자는 V100의 다음 Plan `20250317_DSE_P12`, Plan/W0 `202512`, 기준일 `2025-03-17`을 선택하고
5090 신규 실행·Export와 저장 계약 수정 후보 적용을 승인했다. **Demand 계산은 성공했지만 Export는 차단됐다.**

| 대상 | 실제 확인 결과 |
|---|---|
| 공유 입력 준비 — 완료 | Backend의 기존 Calendar/Master Producer와 승인 Config Selector로 26주 입력·요청을 봉인했다. Master는 `dsdm.tb_mst_oper_part` 7,000품목, EA이며 임의 제외·0수요 보정은 없다. Planning 파일 Hash는 `5a6174247b5981b4cd478676835cd246b654e4da9730a2fe88ca9132ae31a095`다. |
| 5090 실행 환경 보정 — 완료 | 기존 ICL 전용 Python과 봉인 Vendor를 사용하고 필요한 기존 모델 46개 파일의 Hash를 검증했다. 새 Release 안에 같은 모델을 복사하고 이전 연결·Release를 보존했다. Secret·모델 학습·Config 활성 Revision은 변경하지 않았다. |
| 저장 계약 보정 — 완료 | 074가 요구하는 `plan_source_key=demand.forecast_plan`, `plan_source_contract_version=1.0.0`을 Demand INSERT에 명시했다. 저장 계약은 `1.7.0`, Hash `87c952168a12fbfb59484be38764bb70b99c0253ae3bc3b9648e2a961bbe0518`다. 누락 SQLSTATE `23502`와 수정 INSERT를 Rollback Canary로 검증했고 잔여 Row는 0건이다. DB 제약·Migration은 수정/재적용하지 않았다. |
| 실제 신규 Demand — 완료 | Run `e5b43229-c69e-4e08-a8ab-5515bfb0f2f3`, `succeeded/completed`, 389,083ms. 14개 모델 성공·1개 취소이며 15개 모델을 모두 계산했다고 표현하지 않는다. 원래 요청·Config·Handoff·멱등 Hash를 유지했다. |
| 실행 전 Guard — 완료 | 정확한 Run의 Shared Receipt·입력 Manifest·Master Universe를 검증했다. Receipt는 최초 실제 모델 실행보다 먼저 생성됐다. 이력은 W0 이전 `202511`까지만 포함하며, 202512 전용 ICL 입력은 52주/188,344행/3,622품목이고 미래 실제 수요는 사용하지 않았다. |
| Forecast 결과 — 완료 | 해당 Run의 `POINT` 94,172행 = 3,622품목 × 26주. 품목당 모델 1개·26개 주차를 확인했고 불일치 품목은 0개다. |
| 봉인 Export — 차단됨 | `FAILED / SELECTION_EMPTY`. 이 Run의 `dsdm.tb_fcst_weekly_selection_decision`은 0건이다. Export가 요구하는 선택 증적과 실제 Current Winner 게시 경로가 연결되지 않았다. Snapshot ID/Export Receipt는 없으며 성공 Run·다른 Run·선택 ID를 임의 수정/대체하지 않는다. Run Evidence도 아직 `pending`이다. |

- 적용 Release: `/home/leekwanhyeong/runtime/DemandEngine-v3-runtime-a43ca735-plan-source-1_7-a64374a0`, PID `1020591`.
  Base Commit `a43ca735195605e33459620bd9eaac07ad943d3c` + 승인된 두 파일 Delta이며
  Candidate Build Hash는 `a64374a0f292917e5251f4843e60ec56872cef9a45e49e4efc4f0fe89d897886`다.
  순수 Git Commit 배포로 표기하지 않는다. Current/복구 Pointer를 대사했고 Commit/Push는 하지 않았다.
- Shared Binding Hash: `882b8261126b408d82a65728ee99ae61c06cba7dca8e9e6fb8e98534d69ac957`;
  실제 Input Manifest Hash: `082881c9291e8e65f2c22fb8872578ac264fdee1f5575e732f7a0fdb758e9f6c`.
  Planning Cycle/Revision 식별자는 요청에 고정했지만 실제 Cycle DB 등록·Inventory Claim 생성은 아직 아니다.
- Master 7,000개 중 Gap 3,378개는 같은 개발 Source의 `demand_end_dt <= 202512`와 일치한다.
  아직 시작하지 않은 수요창은 0개다. 이는 Source 수요창 분석이며 업무 단종 승인·무수요 보장·자동 제외를 뜻하지 않는다.
  Gap은 보존하고 운영 IO 입장을 열지 않는다. Master 기준일은 Plan Date로 고정했지만 과거 시점 Master 재구성 검증은 아니다.
- 추가 정적 대사: Runtime Export Receipt에는 Backend가 필수로 요구하는 `planning_input_artifact_id`와
  `planning_input_artifact_sha256`가 빠져 있다. 실제 현재 차단은 `SELECTION_EMPTY`이며,
  선택 Source 연결과 함께 원 실행 전 Planning Reference/File Hash도 닫힌 전달 계약에 연결해야 한다.
- Platform 기존 URL·인증의 실제 Transport도 Ready를 확인했고, 새 Run에 대해
  `demand_engine_inventory_handoff_not_ready`로 차단했다. 성공 Demand를 IO 적격으로 자동 승격하지 않는다.
- 로컬 증적: Platform `.tmp/inventory-v100-e2e-20261012/demand-202512/`의 Preparation·Plan Activation·
  Ledger Fix Package·Actual Run Verification Receipt. 실제 검증 Hash는
  `609168faba481ed99629547c0abe73b0ea13315091b63b73e5c32e4a3948b6a5`다.
- 이번 관련 테스트: Demand **132 passed**, Platform **46 passed**. 새 테스트는 수정 전 실패/수정 후 성공을 확인했다.
  Ruff Lint·`git diff --check`와 신규 Platform Helper Format을 통과했다. Demand 기존 파일 전체 Format 차이는 유지했다.
  Backend Producer/Config Selector → 5090 내부 API 경로의 개발 검증이며 Platform 화면/서비스 전체 E2E는 아니다.
  Inventory Config 2.1 Draft·5종 입력·새 분류 원문·Input Set·실제 PSI는 여전히 미준비다.

### 남은 작업 순서

**1. Current Winner 선택 증적과 Export 계약을 연결 — 다음 작업**

- 대상: DemandEngine `demand_engine_v3`, dsai-platform Backend. 실제 게시된 Winner와 선택 증적의
  권위 Source·ID·Hash·Scope를 먼저 정하고 생성/조회 경로를 연결한다. 빈 Decision에 다른 Run이나 임의 ID를 대입하지 않는다.
  Runtime Receipt/Backend가 원 Planning Input ID·File Hash를 동일하게 소비하도록 정합화한다.
  단위·계약·Offline 테스트 후 후속 5090 배포/실제 증적 게시가 필요하면 해당 변경 승인을 확인한다.
- 완료 조건: 기존 성공 Run `e5b43229-...`의 원 Guard/입력을 유지한 Export ID·Hash·Receipt 확보다.
  Forecast 재실행을 기본 복구로 삼지 않는다.

**2. 종료 수요창 Gap의 처리와 V100 분류·재고 입력을 봉인 — 다음 작업, 1번 이후**

- 대상: InventoryEngine `inventory_engine_dev`, Platform `codex/inventory-psi-strategy-v2`, 개발 PostgreSQL.
  3,378품목 처리 규칙과 Plan 기준 분류를 확정한 뒤 2.1 Draft 검증/승인, 원문 선보존,
  명시적 합성 BOH·정책/5종 입력·Template/Manifest를 연결한다. 미래 이력 사용·누락 0수요·Source 없는 공급 가산을 금지한다.
- 선택 Source 조사와 Gap 조사 자체는 독립적으로 가능하지만 봉인·Claim·DB E2E는 직렬이다. 이번에는 추가 IO 입력 Write를 하지 않았다.

**3. 실제 로컬 V100 IO E2E — 다음 작업, 2번 이후 직렬**

- 승인 Claim으로 Baseline/Math PSI·조회·REVIEW 차단·복구를 검증한다. 기존 074~078은 재적용하지 않는다.
  비용/PPO/Stress, 공용 Inventory/Platform 배포, Commit/Push는 별도 후속이다.

## 2026-10-12 초기 복구 이력 — V100 Draft·로컬 Build·Demand 연결 복구

**이 절은 신규 Demand 실행 이전의 초기 복구 기록이다. 최신 상태는 문서 맨 위 절을 따른다.**
사용자는 첫 IO 실행을 `default / 9ed62f41 / DSE / C100 / V100`의 수학적 PSI로 정했고,
Inventory Runtime은 로컬 개발 대상으로 확정했다. 별도 승인으로 5090의 기존 Demand Runtime도
Handoff 지원 Release로 갱신·기동했다. **Forecast Snapshot 확보와 실제 V100 IO E2E는 아직 완료하지 않았다.**

### 완료한 적용과 검증

| 대상 | 실제 완료 상태 | 완료로 해석하지 않는 범위 |
|---|---|---|
| 개발 PostgreSQL `192.168.0.46:5432/dsai` | Migration 078의 DDL과 Ledger를 원자 적용했다. `inventory_site_input_sets`의 PK/FK·Check·부분 Unique Index·승인 후 변경 금지 Trigger, 멱등 재실행과 8개 Negative Canary를 확인했다. 검사 행은 모두 Rollback했고 Registry는 0행이다. | 실제 입력 묶음 승인·Claim·PSI 게시 완료가 아니다. 이미 적용된 074~077은 재실행하지 않았다. |
| V100 Config 2.1.0 | Config `ac9f7ebb-425c-46db-82c3-1afa6d8af461`에 Revision 3 Draft `2708e79a-82b5-4c82-8261-5a236077bb18`를 추가했다. 기존 분류·Matrix·Overlay를 보존했다. | 승인·활성화하지 않았다. 활성 Revision은 기존 `d4be4ad6-9e2e-47ce-84fa-d0c0b870fa4b`다. |
| 로컬 Inventory Build | 0.15.0 Wheel을 새로 빌드하고 독립 가상환경에 설치했다. 설치 코드와 Source Closure Hash가 일치하고 `pip check`를 통과했다. | 기존 Engine/Platform 가상환경과 공용 Inventory Runtime은 변경하지 않았다. HTTP 영속 Queue·자동 Dispatch를 배포한 것은 아니다. |
| 로컬 실행 진입점 | [승인 Claim 실행 CLI](../../src/dsio_inventory_engine/entrypoints/approved_local_runtime.py)와 [설치 Build 식별](../../src/dsio_inventory_engine/infrastructure/runtime_build.py)을 준비했다. 정확한 V100 Plan 1.4와 설치 구현 Hash를 검증하고 Platform 입력 조회·Event·게시 API를 사용하는 One-shot Worker다. | 실제 승인 Claim으로 실행하지 않았다. Fixture나 로컬 파일만으로 DB 승인을 대신하지 않는다. |
| 5090 Demand Runtime | `a43ca735195605e33459620bd9eaac07ad943d3c`의 Secret 없는 봉인 Archive를 검증한 뒤 `/home/leekwanhyeong/runtime/DemandEngine-v3-runtime-a43ca735-inventory-handoff`에 설치·8011 기동했다. Platform의 기존 `http://192.168.0.71:8011` 설정에서 Health `200/ready`와 정확한 Handoff 조회를 확인했다. | 새로운 Forecast Run, 모델·Secret 변경, 기존 Run의 자동 승격은 하지 않았다. Inventory/Platform 서비스 배포와도 별개다. |

- Config 2.1 Draft Hash: `2d9b69b2bd255a2317c507e94921152cdb148ba95207d97af17bb07d5bca773f`.
  첫 실행에서 `policy_search.enabled=false`, 평가 Disabled, PPO/Stress 빈 목록, Cost Profile `null`,
  Landed Cost Binding 없음으로 분리한다. Draft의 탐색 범위 기본값은 아직 정책에 영향을 주지 않는다.
- Inventory Wheel SHA-256: `c0e462eabd62d098356b338b62f95b84a78730dcda72f069b016275a4ba4200d`.
  설치 Build Hash: `2f51bb9d1c49bddce18699ecb2b7b5cdf453f74a26b52dc1970d14fdf64b0e1b`.
  수학 구현 Hash: `d8172b65bb2f1f56bab4efecbec1cac6fda246fb362e4760c5352e826950e541`;
  PSI 구현 Hash: `ba3676c13a39714b9bb6722b494edf4d7aec15179176fe6139bb4de6dff1c833`.
  `inventory-python-source-closure-v1`은 전체 설치 Python 코드와 알고리즘 ID/Version을 함께 Hash한다.
  Request가 자신의 구현 Hash를 등록하지 못하며 코드가 바뀌면 새 Binding이 필요하다.
- 로컬 Root는 `/Users/igwanhyeong/PycharmProjects/dsai-platform/.tmp/inventory-v100-e2e-20261012`다.
  `runtime-venv`, `wheels`와 `catalogs/{analysis-inputs,source-snapshots,canonical-snapshots,runtime-inputs,planning,evidence}`를
  준비했다. Catalog는 빈 상태이며 환경 파일의 기존 경로를 바꾸거나 승인 데이터가 있다고 표시하지 않았다.
  `preparation-receipt.json`, `installed-runtime-build.json`, `v100-sealing-specification.json`은 로컬 준비 증적이다.
- 078 DDL SHA-256은 `f89c29652ec928b733ed754a099b93b57a9d64a6328618688b0ce6b87abe913f`다.
  적용 전 `dsai` Schema-only Backup과 Restore Catalog를 확인했다. 데이터 백업·전체 Restore 검증은 아니다.
- Engine 전체 **777 passed, 7 skipped, 653 subtests passed**; Platform Inventory/Handoff 관련 **489 passed**;
  실제 설치 Wheel의 Offline 검증 **48 passed, 9 subtests passed**다. Engine 237개 Python 파일과 신규 Platform
  Helper의 Ruff·Format, 양쪽 `git diff --check`, 설치 의존성 검사를 통과했다. 실제 IO E2E·성능·Coverage 검증은 아니다.
  Demand Handoff/HTTP/Archive 관련 로컬 테스트 **48 passed**, 5090 후보의 Python 666개 구문·Import·임시 HTTP 검증도 통과했다.

### Demand 연결 원인과 현재 Export 상태

- 최초 연결 실패는 DNS/인증이 아니라 8011 TCP `Connection refused`였다. 5090 SSH는 접속됐고,
  서버에도 8011 Listener가 없었다. 기존 Current Pointer의 `d671edec4ed28d6800b48e09b4a1191e64e5d045`
  Release에는 Handoff 조회/Export 코드가 없었으며 PID 파일 `532577`의 프로세스도 없었다.
  종료 시점·이유는 확인하지 않았으므로 Crash나 특정 원인을 단정하지 않는다.
- 승인된 새 Release는 기존 Python 환경과 모델/vendor를 재사용한다. 외부 Secret Profile은 변경하지 않았고,
  기존 실행 Contract Hash `a96d3908fcfb3b5772f06bd54530db3da82edd61ff9edeb2f321d54afc85a1a2`를 그대로 사용한다.
  기존 Release는 보존하고 Rollback Pointer `/home/leekwanhyeong/runtime/DemandEngine-v3-before-inventory-handoff-20261012`를 만들었다.
  활성 PID는 적용 시점 `943657`이며 프로세스 CWD와 Current Pointer를 대사했다. 상시 Supervisor를 추가한 것은 아니다.
- 정확한 Demand Run `e45cc634-29f0-4d59-8cfd-8be2ff18944c`의 Handoff는 이제 **HTTP 200 / `NOT_REQUESTED`**다.
  실제 Catalog `/home/leekwanhyeong/artifacts/pre_forecast-reset-20260828-r8/engine-studio-e45cc63429f04d598cfd8be2ff18944c`에는
  기존 입력 Manifest·Forecast Artifact가 있지만 `io_handoff/shared-input-receipt.json`과 Export Receipt는 없다.
  Platform은 `demand_engine_inventory_handoff_not_ready`로 입장을 차단한다. 성공 Run을 사후 봉인된 Run으로 바꾸지 않는다.
- 공유 Calendar/Master와 실행 전 Guard Receipt가 없는 과거 Run에 Receipt를 임의 생성하거나 Source를 바꿔 재Export하지 않는다.
  다음 실행은 정확한 Planning Input을 먼저 봉인하고 Handoff를 opt-in한 Demand Run이어야 한다. 신규 Demand Run은 아직 승인·실행하지 않았다.

### 아직 준비되지 않은 입력

확인한 `dsdm` Schema에는 Master/Calendar만 있고 `tb_pln_inv_boh`, `tb_pln_inv_duein`,
`tb_pln_inv_bo`, `tb_pln_inv_dmd`, `tb_pln_po_policy_qty`는 보이지 않는다. V100 재고관리 품목
7,000개의 MOQ·발주배수·Lead Time·Service Level도 전부 NULL이다. 이는 다른 ERP/Schema의
Source 부재를 단정하는 것이 아니다. BOH·전기 EOH·정책·확정 입고·고객주문 5종은 봉인하지 않았다.
개발 재고/정책은 명시적 합성 출처를 사용할 수 있지만 **Forecast는 사용자 결정에 따라 실제 Demand Export를 우선**한다.
기존 분류 원문 미확보, Config 2.1에 대응하는 새 분류, Forecast 미존재 품목의 명시적 처리,
Cut-off·Coverage·계획 기준일 검증도 남아 있다. NULL/미확약 공급/누락 수요를 0으로 채우지 않는다.

### 당시 남아 있던 작업 순서 — 위 최신 기준선으로 대체

**1. 정확한 Planning Input과 Handoff 실행 요청 준비 — 다음 작업 / 신규 Run 승인 필요**

- 대상: DemandEngine `demand_engine_v3`, dsai-platform Backend, 5090 Demand Runtime `a43ca735...`.
  실행할 V100 Plan·W0를 지정하고 공유 Calendar/Master Revision·기준일을 먼저 고정한다.
  Handoff opt-in과 실행 전 Guard/Receipt를 포함한 새 요청을 검증한다. 기존 Run 재Export/최신 Run 선택으로 대신하지 않는다.
- 신규 Forecast Run의 DB Write·계산은 별도 명시적 승인 후 수행한다. 완료 조건은 정확한 Run의 Export Snapshot ID·Hash·Receipt 확보다.

**2. V100 Inventory 입력을 봉인·승인 — 다음 작업, 1번 이후 직렬**

- 대상: InventoryEngine `inventory_engine_dev`, Platform `codex/inventory-psi-strategy-v2`, 개발 PostgreSQL.
  Config 2.1 Draft 검증/승인, 새 분류 원문 선보존, 5종 입력과 Template/Manifest를 봉인해 승인 묶음에 연결한다.
  합성 BOH와 정책은 출처를 표시하고 Coverage·Cut-off·EOH/BOH 연속성을 검증한다. 078 및 074~077은 재적용하지 않는다.

**3. 실제 로컬 V100 IO E2E — 다음 작업, 2번 이후 직렬**

- 승인 Claim을 로컬 One-shot Worker에 전달해 Baseline/Mathematical PSI·결과 조회·REVIEW 차단·실패 복구를 확인한다.
  현재 개발 DB/로컬 IO 검증 승인은 확보됐지만 입력이 준비되기 전에는 실행하지 않는다.
  비용·PPO·Stress는 후속이며 공용 Inventory Runtime 배포·Commit/Push는 이번 범위가 아니다.

## 2026-10-12 실제 개발 입력·실행 대상 대사 — 승인 전 읽기 전용 이력

사용자 결정: 첫 실제 개발 E2E는 **`default / 9ed62f41 / DSE / C100 / V100`의 수학적 PSI**다.
Lane 비용은 후속으로 분리한다. 첫 실행은 Baseline·Mathematical Recommended PSI를 대상으로 하고
PPO/Stress는 이번 최소 실행에 필수로 연결하지 않는다. 코드·Offline 연결이 완료됐다는 사실과
실제 승인 입력·설치 Runtime이 준비됐다는 사실을 구분한다. 확인 결과, **실행 가능한 승인 입력 묶음은 아직 미준비**다.

### 코드와 실제 실행 환경

| 대상 | 이번 확인 |
|---|---|
| InventoryEngine | `inventory_engine_dev`, HEAD `f9058d400352fa96005c68773e5e5ed84ffea395`. 자동 분석·Plan 1.4·Source Export/Runtime 조립 관련 변경은 미커밋이다. 새 브랜치는 만들지 않는다. |
| dsai-platform Backend | `codex/inventory-psi-strategy-v2`, HEAD `b558a00212877dc4731b20707844dfa063021bbe`. 다른 세션의 Intelligence 커밋을 보존한다. Inventory 연결 변경은 미커밋이다. |
| 개발 DB | `192.168.0.46:5432/dsai`. 11:20~11:26 KST에 `default_transaction_read_only=on`, Repeatable Read/readonly 트랜잭션으로 메타데이터만 조회했다. |
| Backend 설정 | Compose `dsai-backend`, 컨테이너 내부 `http://backend:8001`, 로컬 설정 Host Port `8011`. 이는 설정 확인이며 해당 서버의 배포 Commit·가동 상태를 확인한 것은 아니다. |
| Inventory Runtime | API URL·Host·설치 Release/Commit·구현 allowlist 미확인. 두 저장소의 배포/Compose에는 Inventory Runtime Service가 없다. `NOT_CONFIGURED`이며 전사적으로 존재하지 않는다고 단정하지 않는다. |

### 승인 데이터와 원본 파일의 실제 상태

| 항목 | 확인된 기준선 | 실행 적격성 |
|---|---|---|
| V100 Config | Config ID `ac9f7ebb-425c-46db-82c3-1afa6d8af461`, 활성 Revision `d4be4ad6-9e2e-47ce-84fa-d0c0b870fa4b`, Schema `2.0.0`, `published` | 원문 JSONB·Schema Identity·Config Hash 재검증 통과. `policy_search`는 없으며 V100의 `2.1.0` Revision은 0건이다. Plan 1.4 Source Export용 새 Revision 준비가 필요하다. |
| V100 분류 | `e54e5649-ad2b-578c-b1fa-2e5603446895`, Revision 2, `approved`, SEVEN_AXIS, `202601`, 7,000품목 | DB Header/Projection은 존재한다. 확인한 저장소·설정 경로·관련 임시 파일에서 전체 원본 Catalog 파일은 찾지 못했다. DB Projection으로 원문을 위조/복원하지 않는다. |
| 품목 Gate | 2,300개 분류, 4,700개 미분류 | Snapshot 승인만으로 7,000품목 모두 계산/게시 적격이라고 해석하지 않는다. 품목별 제외·REVIEW/BLOCK·Forecast Coverage 검증은 유지한다. |
| Mathematical Template·Runtime Manifest | 실제 V100 승인 입력에 대응하는 파일·ID·Hash 미확인 | 테스트 Fixture의 `tenant-a/project-a/V101` Manifest는 실제 프로젝트 입력이 아니다. 새 정확한 입력 원문에서 생성해야 한다. |
| Inventory 소유 Snapshot 5종 | Canonical Catalog 경로 미설정 | BOH·전기 EOH·정책·확정 입고·고객주문의 봉인 원문/Receipt, Cut-off·Watermark·Reserved 의미·Coverage를 확보해야 한다. 누락을 0으로 바꾸지 않는다. |
| Forecast·Calendar·Master | 지정 Demand Run `e45cc634-29f0-4d59-8cfd-8be2ff18944c`는 `succeeded/pending`, Plan ID `20250310_DSE_P11` | DB에 Handoff/Export 메타데이터가 없고 공유 Planning Catalog도 미설정이다. 설정된 Demand Runtime `http://192.168.0.71:8011`에 이 Run의 Receipt만 GET 했으나 `demand_engine_inventory_handoff_unavailable`이었다. 봉인 Handoff 존재/부재를 단정하지 않으며 성공 Run 상태만으로 IO 입장을 허용하지 않는다. |
| 기존 Cycle/Run | Cycle 3건·Inventory Run 5건, 모두 Manifest 포인터 없음 | `DEV-M074-*`, `DEV-M077-*` Snapshot 참조는 과거 Canary다. 실제 입력으로 사용하지 않는다. 실제 계획주차·기간·기준일은 검증된 Handoff 확보 후 고정한다. |
| 입력 묶음 Registry | `dsai.inventory_site_input_sets`가 없음 | Migration 078 Ledger도 없다. 승인 입력/Claim Export에 선행하는 적용 작업이며 이번에는 실행하지 않았다. 074~077은 모두 success, 재적용 대상이 아니다. |

V100 Config Hash: `48ad9893f8efca139a9c2f81ddefa49f13428795c929c0d3913ca529b0a4e77e`.
분류 전체 Hash: `54084553f1d444aa698de65847a9097e7f58bca91a80eb61efdd5c7789b4347d`.
Effective Policy Hash: `4ee176f6868dcdd5e45f116c67fd1ad3254c1528703a333148e564b1887b2049`.
세 값은 역사적 승인 기준선이다. Config 2.1을 준비할 때 기존 분류의 Config ID/Hash만 바꿔 재사용하지 않는다.

### Catalog 경로 — 현재 미설정, 아래는 로컬 준비 제안

Platform `.env`/`.env.local`, 실행 프로세스와 DemandEngine `.env`에서 관련 Catalog 경로를
확인했지만 설정된 값이 없었다. InventoryEngine에는 실제 `.env`/`.env.local`도 없다.
아래 디렉터리는 **미생성·미설정**이며 개발 준비 위치를 구분하기 위한 제안이다.
서버의 공유 Volume/Mount 및 읽기 권한은 Runtime 대상 확정 후 별도 대사한다.

로컬 제안 Root: `/Users/igwanhyeong/PycharmProjects/InventoryEngine/artifacts/development/9ed62f41`.

| 하위 경로 | 설정/소비자 | 용도 |
|---|---|---|
| `analysis-inputs` | `IO_CLASSIFICATION_ARTIFACT_ROOT` / Engine 원본 Catalog | 전체 분류 원문·Config·Mathematical/평가 원 요청 보존 |
| `canonical-snapshots` | `DSAI_INVENTORY_CANONICAL_SNAPSHOT_ROOT` | Inventory 소유 5종 봉인 Snapshot |
| `runtime-inputs` | `DSAI_INVENTORY_RUNTIME_INPUT_CATALOG_ROOT` | `runtime-inputs-<snapshot_id>.json` 승인 원문 전달 |
| `planning` | `DEMAND_IO_SHARED_INPUT_CATALOG_ROOT` / Demand·Platform 공통 | Calendar/Master와 Planning Receipt |
| `evidence/inventory.sqlite3` | `DSAI_INVENTORY_EVIDENCE_SQLITE_PATH` | 개발 결과/Outbox와 Backend 읽기 경로 |
| `run-cost-inputs` | `DSAI_INVENTORY_COST_INPUT_CATALOG_ROOT` | 후속 Lane 비용 입력; 첫 V100 실행에는 사용하지 않음 |

처음 구성할 V100 Plan 1.4는 `operational_strategy=MATHEMATICAL`,
`shadow_challenger_bindings=[]`, `stress_scenario_bindings=[]`, `cost_profile_binding=null`,
Landed Cost Binding 없음으로 최소 실행을 분리한다. 분석 사양은 승인할 Config 2.1의 값을 정확히 봉인하며,
이번 확인 작업에서 탐색/평가 설정·운영 Gate를 바꾸거나 자동 활성화하지 않았다.

### 설치 패키지·구현·모델 목록

| 대상 | 실제 관측 | 분류 |
|---|---|---|
| Engine `.venv` | Python 3.12.10, 패키지 0.15.0 editable (`src` 연결). FastAPI·HTTPX·Asyncpg·Polars·Torch 미설치 | 로컬 Source 계산용이며 공용 Runtime 설치 증명이 아니다. 런타임 Extra 준비가 필요하다. |
| Platform `backend/.venv` | Python 3.12.10, Torch 2.12.1·Polars 1.41.2·HTTPX 0.28.1·FastAPI 0.136.3·Asyncpg 0.31.0. InventoryEngine 패키지는 미설치 | Offline 검증은 명시적 `PYTHONPATH=InventoryEngine/src`로 사용했다. 패키지를 새로 설치하지 않았다. |
| Mathematical | `historical-normal-r-s / 1.0.0` | Source 구현 있음. 배포 allowlist의 설치 구현 Hash는 아직 미확정. |
| PSI | `inventory.psi.weekly_roll_forward / 1.0.0` | Source 구현 있음. 모든 전략의 공통 PSI이며 배포 구현 Hash 미확정. |
| Predictive ML | `positive-error-quantile-linear-v1 / 1.0.0` | Source 구현/연구 모델 있음. 첫 V100 실행에서는 제외. |
| PPO | `ppo-clip-target-mlp-v1 / 1.0.0` | Source 구현/연구 모델 있음. 실제 배포 모델/승인 allowlist는 확인되지 않았고 첫 V100 실행에서는 제외. |
| Stress | `inventory.stress.demand_receipt_transform / 1.0.0` | 개발 Registry, Seed 20260915, 입고 7일 지연·수요 20% 증가. 첫 실행에서는 제외. |
| Cost Profile | `DEV-KRW-COST-BASELINE / 1.0.0` | 개발 합성 Registry. 후속 비용 단계로 분리. |
| 배포 산출물 | `dist/dsio_inventory_engine-0.11.0-py3-none-any.whl`만 존재 | 현재 Plan 1.4 조립 배포에 사용하지 않는다. 새 검증 Build 필요. |

연구 증적의 모델 원문 14개(ML 7·PPO 7)는 구조·Hash 재검증을 통과했다.
Smoke 모델 2개는 `V101/TGSM/TRAINING-DEMO-v1`, 학습 종료 `2027-01-03`;
선정 보고서 모델 12개는 `V101/TGSM/STABILITY-v1`, 학습 종료 `2028-07-02`다.
둘 모두 실제 V100 Config Revision과 Scope가 다르고 역사적 `202601` W0보다 미래 학습 자료다.
연구 평가와 사용 가능한 Run 모델/개발 승인은 다르며, Scope·훈련 종료일을 고쳐 재봉인하지 않는다.
고정 JSON 모델의 현재 추론은 Torch를 사용하지 않는다. Torch 미설치와 모델 승인 미준비를 혼동하지 않는다.

Source 136개 Python 파일의 경로별 SHA-256 Canonical 목록 Hash는
`6c7ec5594f910d29599be6a60cd7379b53f896242cd249cfaf0ed57b8e0ae2e6`이다.
이는 미커밋 후보 Source를 식별하는 지문이며 설치 구현 Hash나 모델 승인 Hash가 아니다.
Offline 테스트의 Mathematical `d*64`, PSI `5*64`, PPO `e*64`도 배포 Hash로 사용하지 않는다.

후속 비용 참고: C100 Network `527755eb-ea3d-56b4-a288-7404bc9a2806`은 DEVELOPMENT/APPROVED이며
유효 시작일은 `2026-09-02`다. 역사적 `202601` 기준일에 임의 연결하지 않는다.
Trade Cost Revision Set `ce625f93-9cf8-59dd-96e6-d394aba1a8b8` R2는 APPROVED,
Hash `a609a003fd7d223a3c44b4d8ab72455bf8a648572c9d3c16c585f3569fb85898`다.
Lane 비용 V1은 V100→V101~V104이므로 V100 자체 재고 실행에 끼워 넣지 않는다.

### 남은 작업 순서 — 이번 대사 반영

**1. V100 최소 입력과 Runtime Build 준비 — 다음 작업(로컬)**

- 대상: InventoryEngine `inventory_engine_dev`, dsai-platform Backend `codex/inventory-psi-strategy-v2`.
  Config 2.1 Draft, 분류 원문 보존/재생성 계획, 정확한 Forecast/Calendar/Master Handoff와 Inventory
  5종 Source 수집·봉인 사양을 준비한다. Raw 자료가 미확인인 항목은 합성/미확인으로 명시한다.
- 같은 입력의 Canonical·Template·Manifest Hash와 Run 전 치환 필드, Gate를 Offline 대사한다.
  설치 Extra·검증 Build·구현 Hash Manifest 및 Runtime Host/URL·공유 Mount 후보도 준비한다.
- 입력 계약 준비와 Runtime 패키징 조사는 병렬 가능하다. Config→분류→입력→Manifest는 직렬이다.
  새 승인 ID나 Hash를 발급한 것처럼 기록하지 않는다.

**2. V100 개발 입력 승인과 실행 환경 연결 — 승인 필요**

- 대상: 개발 PostgreSQL `192.168.0.46:5432/dsai`, 대상 확정 후 dsai-platform 개발 Backend·공용 Inventory Runtime.
  Migration 078 → Config 2.1·새 분류·입력 묶음 승인/게시 → 서버 연결을 직렬로 수행한다.
  이미 적용된 074~077을 다시 실행하지 않는다. 실제 DB Write·공용 Runtime 배포는 별도 승인이다.

**3. 실제 V100 PSI E2E와 후속 비용 검증 — 승인 필요 / 후속 작업**

- 실제 Claim→Baseline/Math PSI→Evidence/Outbox→결과 조회·REVIEW 차단·복구를 확인한다.
  비용/PPO/Stress가 없어도 성공을 위장하거나 누락 비용을 0원으로 표시하지 않는다.
- Lane 비용과 승인 PPO/Stress는 별도 입력/Scope/모델이 준비된 뒤 새 Plan으로 검증한다.
  V101~V104 비용 대상에는 해당 Site의 Config·분류·수요·재고가 필요하다.

**4. 검증 변경의 원격 전달 — 별도 요청 후**

- InventoryEngine `inventory_engine_dev`와 Platform `codex/inventory-psi-strategy-v2`의 관련 변경·문서를
  저장소별 독립 Commit으로 정리한다. InventoryEngine `origin/inventory_engine_dev` 및 Platform
  원격 Inventory 브랜치/`develop` MR 대상은 각각 명시한다. 이번에는 Commit·Push를 하지 않았다.

이번 대사는 코드·설정·모델·패키지를 바꾸지 않았고 DB Write·Migration·Full Load·새 Run·Export·배포도 없다.
DB Config 원문 Hash·Schema Identity, 모델 14개·Offline Fixture의 봉인 계약을 확인했다.
762/432건 전체 회귀는 이전 연결 작업의 검증 이력이며 이번 문서 대사에서 재실행한 수치가 아니다.
`oma-docs` CLI 미설치로 승인한 세 문서는 코드·Diff·로컬 링크와 직접 대사한다.
개인 보고서·환경·IDE·임시 산출물을 보존한다.

아래 기록은 당시 상태·수치를 보존한다. **최신 적용 상태와 순서는 문서 맨 위의 승인 후 기준선을 우선한다.**

## 2026-10-12 재검증 기준선 — 로컬 계산·Run 결과·화면 연결

- 대상: InventoryEngine `inventory_engine_dev`, dsai-platform `codex/inventory-psi-strategy-v2`.
  개발 입력 봉인, Config `2.1.0`, 품목별 정책 탐색·선택·장기 평가와 Run 부속 Artifact,
  Platform 조회 API·결과 화면까지 로컬 구현을 검증했다. 상세 경계는
  [Run 정책 분석 계약](IO_POLICY_ANALYSIS_RUN_CONTRACT.md)을 따른다.
- `policy_analysis_inputs`를 명시적으로 전달하는 경로는 완료했다. 실제 Claim의 Config 허용은
  여전히 `1.1.0`/`2.0.0`이며 자동 분석 Producer·Command Resolver는 미완료다.
  Config `2.1.0` 저장·입력 묶음 검증 가능과 실제 자동 실행 가능을 구분한다.
- 재검증: Engine **727 passed, 7 skipped, 653 subtests passed**; Platform 관련 Backend
  **417 passed**; 관련 Frontend **28 passed**. Engine·Platform 교차 저장소 Offline 연결을 포함한다.
- Ruff·Format, Engine JSON Schema **29개**, 양쪽 Config `2.1.0` Schema 일치,
  `git diff --check`를 검증했다. Frontend 전체 Next 빌드도 타입 검사·페이지 생성까지 통과했다.
  삭제된 Portal을 참조하던 기존 `.next/dev/types`는 복구 가능한 임시 경로에 보관한 뒤 재검증했다.
- 환경: macOS arm64/Python 3.12.10, 기존 Platform 검증 환경의 Polars 1.41.2/Torch 2.12.1.
  선언 범위와 일치하며 패키지를 설치하거나 기존 환경을 변경하지 않았다. Coverage는 측정하지 않았다.
- 실제 DB 통합 테스트 7건은 명시적으로 비활성화했다. 이번 범위는 독립 로컬 Commit까지이며,
  Push·MR 생성·DB 접속/Write·Migration 적용·공용 Runtime/웹 배포는 수행하지 않는다.
  개인 보고서·환경·IDE·임시 산출물은 Commit에서 제외하고 보존한다.

### 당시 남은 작업 순서 (이력)

1. **자동 정책 탐색 Run 계약 — 다음 작업 · InventoryEngine·dsai-platform Backend**
   Config `2.1.0`의 탐색·평가 사양과 정확한 입력 Hash를 Claim에 봉인한다.
   허용 Version만 넓혀 탐색 설정을 조용히 무시하는 실행은 차단한다.
2. **개발 입력부터 분석까지 자동 생성 — 다음 작업 · 입력 Producer·Command Resolver**
   봉인된 Site 입력에서 탐색·선택·평가 Artifact를 자동 생성하고 실패·복구·Gate를 검증한다.
   공통 계약 확정까지 직렬이며, 이후 Engine 연결과 Frontend 상태 테스트는 병렬 검증 가능하다.
3. **실제 개발 E2E — 승인 필요 · 개발 PostgreSQL·확정할 개발 서버/Inventory Runtime**
   Migration 078은 초안이다. 별도 승인 후 입력 묶음 게시 → Claim → Runtime → 결과 조회를 검증한다.
   이미 적용된 Migration 074~077은 재실행하지 않는다.
4. **원격 기준선 전달 — 별도 요청 후 · 두 저장소의 현재 작업 브랜치**
   InventoryEngine은 `origin/inventory_engine_dev`, Platform은 현재 Inventory 브랜치의 원격
   대상과 `develop` MR 경계를 확인한다. 이번에는 로컬 Commit만 수행한다.

아래 2026-10-06 및 2026-09-29 기록은 각 단계 당시의 상태·수치를 보존한다.
최신 완료 범위와 실행 순서는 이 절과 Run 정책 분석 계약을 우선한다.

## 2026-10-06 추가 기준선 — 품목별 정책 탐색·선택(로컬)

- InventoryEngine `inventory_engine_dev`, dsai-platform `codex/inventory-psi-strategy-v2`에서
  [정책 탐색 업무 계약](IO_POLICY_SEARCH_CONTRACT.md), Config `2.1.0`, 그룹 후보·품목 예외 설정,
  품목×후보 Simulation과 로컬 Append-only 결과 저장을 구현했다. 아래 기록은 각 당시의 검증 이력이다.
- 기존 `1.1.0`/`2.0.0` Config, 고정 Mathematical·PPO Shadow 기준선은 유지한다.
  탐색 원본은 `DEVELOPMENT_SHADOW`이며 `selected_policies=null`, 자동 게시·발주 불가다.
- Baseline과 후보는 같은 봉인 입력을 사용한다. 후보별 실제 PSI·원 행동·제약 조정·서비스·주말 평균재고·
  발주횟수·비용을 보존한다. 비용 미입력은 0이 아니라 `COST_PROFILE_NOT_BOUND`다.
- 신규 설정 테이블·Migration, 실제 DB Write·웹/공용 Runtime 배포, Commit·Push는 이번에 수행하지 않았다.
- 최신 테스트 수치와 배포/Source 한계는 [정책 탐색 검증 기록](IO_POLICY_SEARCH_VERIFICATION.md)을 따른다.
- InventoryEngine에 [품목별 선택기](IO_POLICY_SELECTION_CONTRACT.md)를 추가했다. 서비스 충족·종료
  Backorder 0·비용 가용·주문 Gate를 만족하는 후보를 비용 → 평균재고 → 발주횟수 → Candidate ID로
  선택한다. 동점·미선정 사유를 보존하며 REVIEW는 개발 선정만 허용한다.
- 원본 PSR은 그대로 두고 별도 PSEL Artifact로 보존한다. 저장·조회 시 원본 PSI/Guard/지표와
  선택을 재검증한다. 운영 승인·자동 게시·발주, Platform Run 연결은 아직 아니다.
  최신 전체 회귀와 검증 범위는 [선택기 검증 기록](IO_POLICY_SELECTION_VERIFICATION.md)을 따른다.
- [장기 평가·비용 민감도](IO_POLICY_SENSITIVITY_CONTRACT.md)를 별도 개발 실험 계약으로 추가했다.
  실제 기간별 PSI 재실행, 동일 PSI 비용 재평가, 고정 정책의 새 Seed Holdout, 동일 Prefix 종료 효과를
  구분하고 별도 PSENS Artifact를 Append-only 저장·Replay한다. 운영 정책 자동 승격은 하지 않는다.
  실행 수치와 한계는 [장기 평가 검증 기록](IO_POLICY_SENSITIVITY_VERIFICATION.md)을 따른다.

Run 및 결과 화면 연결은 이후 로컬 구현했다. 당시 후속 목록을 현재 작업으로 다시 실행하지 않으며,
최신 자동 생성·개발 E2E·원격 전달 순서는 위 2026-10-12 절을 따른다.

기준일: 2026-09-29, 로컬 패키지 0.15.0. 이 문서는 **목표 계약 확정**, **코드 구현**, **개발 DB 검증**, **운영 사용**을 구분한다. 목표 아키텍처의 전체 파일 트리가 이미 구현돼 있다고 해석하지 않는다.

동일 순서의 [의존관계 계획 JSON](IO_DEVELOPMENT_PLAN.json), [Network 검증 요약](evidence/network-input-validation-20260903.json), [Canonical·PSI 검증 기록](IO_CANONICAL_PSI_VERIFICATION.md)을 함께 관리한다. 계획 파일은 향후 작업 목록이며 자동 실행 지시나 DB 변경 승인이 아니다.

## 1. 현재 기준선 — 완료한 범위

| 대상 | 실제 상태 | 아직 하지 않은 것 |
|---|---|---|
| InventoryEngine 프로젝트 | Python 3.12 패키지, 독립 Git 저장소, Runtime HTTP 접수·Platform Callback·Worker Orchestration, 전체 PSI Handler와 로컬 영속 Evidence Adapter | 운영 PostgreSQL/Object Storage Adapter, 영속 Queue Worker, 배포 |
| Network 입력 | 요청 ID/Hash·승인·Scope·Plan 기준일 검증, 불변 Network Snapshot·Manifest와 Site 후보 경로 생성 | Artifact 봉인·TB_IO 저장, Cycle/Run FK·Claim, 운송수단 선택·계산 적용 |
| Canonical 입력·Golden | 8개 Snapshot 구조·JSON Schema, 요청 Binding·Hash 검증, 독립 Golden 14개/45 PSI Row | 실제 Legacy SQL Adapter, 운영 Source 의미·정확성 검증 |
| 0.9.1 Source 읽기 호환 | 전체 로컬 검증 후 DB 연결과 기존 JSON v1 DTO·Hash 유지. 실제 DB 2건은 0.9.0 당시 기록 | 실제 재고/주문/정책 Collector와 운영 공유 Snapshot 미준비 |
| 0.10.0 Forecast 전달 | Demand POINT opt-in Export/Guard·분할 Parquet, IO 독립 Reader·Canonical v2 계획/물리 정밀도 분리. 111,020행 전달·입력 준비 검증 | 실제 Artifact Mapping/실행 전 Receipt 저장/Studio 연결·DB Publication·대형 Recommended PSI 성능 |
| 7축 분류·Effective Policy V2 | Config 1.1.0 호환과 2.0.0 7축 결과를 결정론적으로 생성한다. 개발 PostgreSQL DSE/C100/V100에 Config Revision 2와 V2 Snapshot Revision 2를 게시했고 7,000품목·49,000축 Row·26주 Actual Close·Exact Replay를 검증했다. Migration 074와 실제 Claim·Attempt·Retry·CAS도 개발 DB에서 검증했다. | Scheduler·공용 Runtime 배포. SDE/HML 운영 Source와 PLC 권위 Source는 아직 준비되지 않음 |
| Cut-off·단일 Site PSI | 전기 EOH–BOH·Watermark·Late Posting·봉인/고아 검증, 공통 주차 전이·Baseline/로컬 Recommended PSI와 Evidence JSON | ERP 신규 거래 수집/실제 봉인 저장·DB Evidence 적재 |
| 동일 Run PSI Result Bundle | Canonical 입력을 한 번 준비하고 실제 Baseline·Math·PPO Shadow·등록 Stress PSI를 계산한다. 승인된 개발 Cost Profile과 수요 급증·확정 입고 7일 지연 Registry를 Plan에 Hash로 봉인하고 실제 PSI Row에서 비용 Delta를 산출한다. 무비용 V1 경로는 `COST_PROFILE_NOT_BOUND`로 호환된다. | 권위 비용 Source와 운영 Stress Registry, PostgreSQL/Object Storage Adapter, 공용 Runtime 배포·DB 게시 |
| Trade Cost·Landed Cost V1 | C100 V100→V101~V104의 개발 Fixture Catalog, Pure Calculator, Migration 077, 승인·Supersede CAS API, Planning Cycle Run Binding과 Sealed Revision Set 조회를 구현했다. 봉인 Shipment·Projection의 Child 계산, Append-only 원자 저장과 독립 Replay도 Offline 완료했다. | Child의 실제 Claim/Runtime·Parent Bundle 연결과 개발 Simulation·MEIO 비용 소비. 운영 전환 시 권위 Source와 국가별 공식 Rule Adapter 필요 |
| 세 전략 공통 기반 | MATHEMATICAL/PREDICTIVE_ML/DEEP_RL 계약, 행동/납기/용량 검증, 공급 대기열 | 전략 간 자동 Fallback·운영 승인 인증 |
| 수학적 정책 | 13/26주 이력·SS/ROP/목표재고·승인 Override/Fallback·권고 Golden 14개/42 PSI Row | 실제 Source 적합성/서비스수준 달성 검증 |
| 학습·평가 기반 | 독립 52주 Generator, 시간순 Feature/Label, 비용·서비스·입고지연 Reference World, 130주·5품목·6시나리오 | 실제 Forecast Vintage/공급 이력 수집·운영 봉인 |
| 생산 수학적 전략 평가 | 기존 정책/공통 Guard 호출, 매주 실제 재고 상태 반영, 기간형 정책·Override·소수 UOM·지연 Golden | 운영 Source 성능 합격·실제 Source 전략 우월성 |
| 0.7.0 ML/PPO 및 비교 | 공통 14 Feature·Model Version/Hash, 실제 CPU ML/PPO 학습·JSON 추론, 동일 조건 VALIDATION/TEST 및 종료 점검 | 운영 모델 승인·봉인·배포 |
| 0.8.0 학습 안정성 | 학습 Seed3개×후보2개, 비반복 TRAIN52/VALIDATION52, 선정 Hash 고정 후 새 TEST Seed3개×104주·종료 민감도 실행 완료 | ML/PPO 모두 연구용 성능 기준 미달. 실제 Source 적합성·승격/운영 승인 없음 |
| dsai-platform Network Master | 이전 승인 작업에서 개발 PostgreSQL의 `dsim` Master 3개+Outbox 1개, 10/50/80건 검증 완료 | 이번 작업에서 새 Migration/DB Write 없음 |
| Network Graph | 이전 작업에서 DSDM 개발 Neo4j에 Inventory 전용 190 Node/340 Relationship, Outbox 전부 PUBLISHED | IO는 Graph를 입력 Source로 사용하지 않음. 상시 Worker 활성화 없음 |
| DemandEngine | 기존 `src/run_demand/full_pipeline.py` 등을 설계 참조로 사용 | 이번에 Demand Runtime 변경·재배포·전체 상태 재검증하지 않음 |
| DSIM | 미래 Consumer의 Read Contract만 정의 | Agent·조회 API·서비스 미구현. 현재 연동할 DSIM은 없음 |

InventoryEngine 작업 경로는 `/Users/igwanhyeong/PycharmProjects/InventoryEngine`이며 독립 Git 저장소와 `origin`이 구성돼 있다. `main.py`와 `sample_jupyter/io_proximal_policy_optimization.ipynb`는 기존 사용자 자료로 보존한다. 연구 Notebook은 재고정책·Solver 구현 기준선으로 자동 채택하지 않는다.

2026-09-29 선언 범위의 격리 환경 재검증에서 InventoryEngine Unit 459건, Contract 52건,
Offline Integration 85건이 통과했다. 합계 596건 통과·실패 0·Skip 7·Subtest 623건이다. Skip은 명시적으로
비활성화한 PostgreSQL 읽기 전용 테스트이며, DemandEngine 교차 Handoff와 111,020행 전달
검증은 Python 3.12 환경에서 포함했다. Ruff, Format과 JSON Schema 21개 검증도 통과했다.
dsai-platform은 최신 `origin/develop`의 `abe71826` 기준 Inventory 관련 회귀 248건이 통과했다.
`main.py`의 기존 import 위치·전체 서식 지적은 기준 커밋에도 있어 전면 재작성하지 않았다.
새 Inventory Router import는 같은 초기화 순서를 따르며, 그 외 후보 Python 파일의 Ruff와
Format을 검증했다. Frontend와 Platform 전체 기능 테스트는 이번에 재실행하지 않았다.

기존 환경의 Polars 1.31.0·Torch 2.14.0과 의존성 선언은 변경하지 않았다. 임시 환경 두 개에서
모든 선언 Extra를 함께 설치해 Polars 1.41.0/Torch 2.12.0 및 Polars 1.44.2/Torch 2.12.1을
검증했고 `pip check`도 통과했다. 하한 환경은 Inventory 자체 587건, 최신 허용 환경은
Demand 교차 Handoff 포함 596건 통과다. Handoff의 Demand 모듈 로딩에는 NumPy·pandas·
PyArrow·psycopg2-binary·python-dotenv·tqdm을 테스트 보조 의존성으로 추가했다.
이 결과는 macOS arm64/Python 3.12.10의 CPU·Offline 검증이며 Linux/CUDA·공용 Runtime
배포 검증을 뜻하지 않는다. [격리 의존성·Artifact 증적](evidence/landed-cost-artifact-validation-20260929.json)을 따른다.

Migration 074·075·076·077의 개발 적용, Config·분류 Snapshot 게시와 Claim·Retry·CAS는
이전 승인 작업의 증적이다. 이번 Git 기준선 검증에서는 DB 접속·Migration 적용·DB Write와
공용 Runtime 배포를 수행하지 않았다. 세부 검증과 Commit 경계는
[2026-09-29 Git 기준선 기록](evidence/git-baseline-validation-20260929.json)을 따른다.

dsai-platform의 관련 변경은 `origin/develop`에서 분리한 로컬 `codex/inventory-psi-strategy-v2`에서
Run/Plan 기준선 `b0df9b4c`와 Trade Cost 기준선 `c6c38373` 두 Commit으로 고정했다. Push와
`develop` 병합은 하지 않았다. `planning_cycle_revisions`, `planning_cycle_site_executions`,
`engine_run_input_bindings`의 Migration과 Repository/API, Demand Handoff Input Binding,
Runtime Dispatch가 구현돼 있다. Migration 074는 이전 개발 DB 작업에서 적용했고 실제 V2
Claim의 Attempt·Input Binding·Retry CAS와 V1 Effective Run CAS를 검증했다.
Scheduler→공용 Runtime E2E는 아직 수행하지 않았다.
InventoryEngine과 dsai-platform Backend의 Strategy Execution Plan은 모두 `1.1.0`이며,
수학 정책 입력·Replenishment Config·PSI Simulator·Stress Payload/Seed/Runner Binding과
승인 Reference 규칙 및 교차 저장소 Golden Hash가 일치한다. Migration 074의 Planning Cycle
Source 계약 Version은 별도 의미이므로 변경하지 않았다.

## 2. 다시 결정하지 않는 업무 기준선

- POSM/TGSM 동시 지원, 단일 Company, 한 Run 한 Site.
- 목표 주차는 `YYYYWW`, `YEARWEEK=YEARPWEEK` 가정. Demand와 IO의 Calendar/Master Snapshot을 고정한다.
- Demand를 먼저 실행해 검증된 Forecast Snapshot ID/Hash를 전달한다. IO의 최신 Forecast/Network 탐색은 금지한다.
- Master 기준일은 Planning Cycle의 Plan Version 기준일이다. 실행일은 Legacy 회귀용으로만 사용한다.
- 입력 순서는 Forecast + BOH/입고예정 + 정책 → Baseline PSI/품절 → 정책 기반 보충 권고/Recommended PSI다.
- 고객 확정 주문과 순 Forecast를 구분한다. 실제 주문 미충족만 Backorder로 이월하고 Forecast Shortage는 이월하지 않는다.
- 미확약 ORDERED/UNVERIFIED_DUE_IN은 Baseline에서 제외하고 원본·제외 사유를 Evidence에 남긴다. 민감도·Legacy 시나리오는 분리한다.
- Python 안전재고/ROP/목표재고/권고량을 기본으로 하고 Source의 MOQ·발주배수·물리 한도·Lead Time·승인 목표를 준수한다. Override도 Hard Constraint를 위반할 수 없다.
- TGSM MAX_QTY는 목표재고다. Legacy 비교는 POLICY_PROXY, 목표 개발 검증은 독립 SYNTHETIC_BOH Generator를 사용한다.
- Cut-off/Watermark와 전기 EOH–당기 BOH를 검증한 SEALED 재고 Snapshot만 PSI 입력으로 사용한다.
- Run별 입력·중간·최종 산출물은 Artifact와 TB_IO Evidence에 보존한다. 이번 Network DTO는 그 저장 단계의 입력이며 저장 완료를 대신하지 않는다.
- 수식 전용 V1 결정은 공통 PSI·제약 검증과 수학적/예측형 ML/심층 강화학습 전략 지원으로 확장됐다. ML/PPO는 개발 로컬 학습·추론까지 구현했으며 생산 Capacity, BOM/BOR, Site 간 수송 최적화·Multi-Echelon Solver는 별도 범위다.

## 3. 완료한 구현과 정확한 검증 범위

[Network 입력 계약](IO_NETWORK_INPUT_CONTRACT.md)의 `PrepareNetworkInputUseCase`를 실제 개발 PostgreSQL에 연결해 10개 Network/50개 Site를 읽기 전용으로 검증했다. Unit 27건, Producer 호환 1건, 통합 5건을 통과했다.

이 결과는 `NETWORK_INPUT_PREPARED`이며 `run_claimed=false`, `psi_computed=false`, `persistence_status=PREPARED_IN_MEMORY`다. 예제 Cycle/Run/Master Revision ID는 실제 DB Row가 아니며 통합 테스트도 Run을 만들지 않는다. 실제 Pipeline에 이 UseCase를 연결하는 것은 아래 공통 실행 작업에 포함한다.

[Canonical·Cut-off·PSI 계약](IO_CANONICAL_PSI_CONTRACT.md)의 입력 준비와 `RunPsiSimulationUseCase`도 구현했다. 14개 독립 Golden의 45개 PSI Row와 수량 보존식이 일치하며, 잘못된 입력은 PSI 진입 전에 거부한다. 단위 58건(기존 Network 27건 포함), 계약 3건, 오프라인 CLI 통합 3건이 통과했다. 이 작업에서 실제 DB에 접속하지 않았고 기존 Network 읽기 전용 검증 5건은 이전 작업의 기준선이다.

0.2.0 Baseline은 그대로 유지한다. 0.3.0에서 [세 전략 계약](IO_REPLENISHMENT_STRATEGY_CONTRACT.md), `advance_bucket`, `RunRecommendedPsiUseCase`를 추가했다. 원본 Snapshot, 관측/제안/행동 조정·주문·PSI를 JSON으로 반환하지만 Artifact/DB에 저장하지 않는다. `RECOMMENDED_PSI_COMPUTED_LOCALLY`는 계산 완료이며 운영 승인/Run 성공이 아니다. Probe는 학습 모델이 아니고 TGSM Fixture는 52주 Generator가 아니다.

0.3.0 검증은 단위 86건, 계약 5건(Producer 호환 포함), 오프라인 프로세스 통합 4건이며 모두 통과했다. 기존 14개 Golden은 Baseline과 HOLD 전략 양쪽에서 일치한다. [새 검증 기록](IO_REPLENISHMENT_STRATEGY_VERIFICATION.md)을 참조한다. 이번 작업에서도 실제 DB에 접속하지 않았다.

0.4.0에서 [수학적 정책 산출기](IO_MATHEMATICAL_POLICY_CONTRACT.md)를 구현했다. 과거 이력·Profile·승인 정책을 추가 Binding에 고정하고 Source/Python/Effective 정책을 분리한다. 독립 Golden 14개/42개 PSI Row가 통과했다. 당시 단위 111건·계약 7건·오프라인 통합 7건의 [검증 기록](IO_MATHEMATICAL_POLICY_VERIFICATION.md)을 보존한다.

0.5.0에서 [합성 학습·평가 기반](IO_TRAINING_EVALUATION_CONTRACT.md)을 구현했다. 최소 52주 독립 BOH·미도착 공급, 과거만 사용한 Feature/별도 Label, 시간순 구간과 비용·서비스·지연 시나리오가 실행된다. 실제 생성 BOH의 Canonical/PSI 호환 테스트도 통과했다. 최신 실행 수치와 한계는 [검증 기록](IO_TRAINING_EVALUATION_VERIFICATION.md)을 따른다. DB Write·배포·모델 학습은 수행하지 않았다.

0.6.0에서 [생산 전략 평가 Adapter](IO_PRODUCTION_EVALUATION_CONTRACT.md)를 완료했다. 기존 수학적 정책/공통 Guard를 실제로 호출하고 독립 World의 매주 실제 BOH·BO·입고를 다시 관측한다. 합계185개 테스트(단위159·계약12·오프라인 통합14), 3개 구간×6시나리오를 검증했다. [검증 기록](IO_PRODUCTION_EVALUATION_VERIFICATION.md)의 합성 조건을 따르며 운영 성능/배포/저장 완료가 아니다.

0.7.0에서 [ML/PPO 학습·추론·평가](IO_LEARNED_STRATEGIES_CONTRACT.md)를 완료했다. 실제 CPU 학습, JSON 모델 검증·추론, 동일 초기재고/지연/종료 조건의 네 정책 비교를 실행했다. 단위184·계약14·오프라인 통합17의 로컬 검증과 한계는 [최신 검증 기록](IO_LEARNED_STRATEGIES_VERIFICATION.md)을 따른다. ML/PPO가 모든 시나리오에서 수학적 전략보다 우수하지 않으며 운영 승인은 하지 않았다.

## 4. 남은 개발 작업 순서

0.8.0 [학습 안정성 계약](IO_LEARNING_STABILITY_CONTRACT.md)과 [검증 기록](IO_LEARNING_STABILITY_VERIFICATION.md)의 실험은 완료했다. 학습 모델12개, 새 홀드아웃·민감도128회/62,400 Item-week를 평가했지만 ML/PPO 모두 연구용 비용·서비스 Gate를 통과하지 못했다. 모델 승격은 하지 않았다.

[Inventory 분류 Snapshot Lifecycle](IO_CLASSIFICATION_SNAPSHOT_LIFECYCLE.md)은 승인된 Config와 Actual Close를 사용한 결정론적 7축 분류, 품목별 유효 정책 Hash, 집계 Receipt, append-only 게시와 exact replay를 구현했다. 개발 PostgreSQL의 DSE/C100/V100에 Config Hash `48ad9893...e77e`와 Snapshot `e54e5649-ad2b-578c-b1fa-2e5603446895`를 게시했다. 7,000개 중 2,300개를 분류했고 4,700개는 `INSUFFICIENT_DEMAND_HISTORY`로 명시적 차단했다. SDE/HML은 비활성, PLC는 `SYNTHETIC + SHADOW_ONLY`다. 이 Snapshot을 Migration 074의 실제 V2 Claim에 고정했으며 자동 게시 불가 Gate도 개발 DB에서 확인했다. 공용 Runtime 배포는 아직 수행하지 않았다.

[PSI·전략 실행 계약 V2](IO_PSI_STRATEGY_EXECUTION_CONTRACT_V2.md)의 저장소 독립 Orchestrator와
실제 Child Artifact를 구현했다. 하나의 Canonical 입력을 한 번 준비해 Baseline, Operational
Mathematical, 승인 PPO Shadow와 등록 Stress Scenario를 실행하고 실제 PSI Row로 비교값을
만든다. PPO/Stress 실패는 선택 Child에 격리하며 Mathematical 결과만 Effective Pointer가
된다. Stress Runner는 미래 수요·확정 입고 변형만 반환하며 Context·Master·Policy·BOH 변경과
임의 전략·PSI 결과 반환을 차단한다. 변형 입력의 실제 Mathematical 전략·공통 Guard·PSI는
Orchestrator가 수행한다. Raw Action에는 판단 당시의 Prepared/Execution Source를 봉인하고 Observation 재투영,
공통 Guard·Effective Policy Admission 재실행, PSI·입고·Capacity 대사까지 수행한다. 상세
검증 범위는 [동일 Run Orchestrator 검증 기록](IO_PSI_ORCHESTRATOR_VERIFICATION.md)을
따른다. 이 Artifact는 아래 로컬 영속 Adapter와 Runtime Handler에서 다시 검증한다.

### Platform Strategy Plan 1.1.0 정합화 — 완료(로컬)

대상: dsai-platform Backend `inventory_engine_run_contract`, 계약 Fixture와 문서.

- 수학 정책 입력 Snapshot, Replenishment Config Hash, PSI Simulator와 Stress Payload·Seed·Runner
  Binding을 Platform Pydantic Model과 Hash 계약에 반영했다.
- 승인 Reference를 InventoryEngine의 닫힌 식별자 규칙과 맞추고 두 저장소의 Golden Hash를
  일치시켰다. Migration 074와 개발 DB Claim 검증은 아래 단계에서 완료했으며 배포는 수행하지
  않았다.

### Result Bundle Artifact 저장·재검증 경계 구현 — 완료(로컬)

대상: InventoryEngine `inventory_evidence`와 저장 Port, dsai-platform Artifact Interface.

- Storage-neutral Port와 로컬 SQLite Adapter를 구현했다. PSI, 원 행동, 제약 행동, 조정 사유,
  Canonical·Prepared·Execution Source와 Bundle의 Canonical JSON 바이트를 한 Transaction에서
  Append-only 저장한다.
- Reader는 바이트 Hash·문서 Content Hash·Reference·Row Count·Child 연결을 다시 검증하고,
  Prepared/Execution Source와 품목별 Admission에서 Action Observation Source를 재구성한다.
- 동일 Reference·동일 바이트는 멱등 성공하고 다른 바이트·Hash는 전체 Transaction을
  Rollback한다. 동시 동일 저장도 하나의 원본과 Exact Replay 하나로 직렬화한다.
- Result-ready Outbox는 Bundle과 같은 Transaction에서 `PENDING`으로 생성되고 성공 게시 후
  `PUBLISHED`로 CAS 전환한다. 실제 PostgreSQL/Object Storage Adapter는 아직 없다.

### 전체 Runtime Pipeline Handler 연결 — 완료(로컬)

대상: InventoryEngine Runtime, dsai-platform Backend 공통 Runtime.

- Plan 1.1.0 Claim을 Command Resolver와 동일 Run Orchestrator에 전달해 입력 해소, PSI 실행,
  Artifact 저장·재검증, 단계 Event, Terminal Event와 Platform Publish를 연결했다.
- Mathematical Bundle Gate만 Runtime 결과로 사용한다. `REVIEW`는 Evidence와
  `WITHHELD_FOR_REVIEW` Outbox를 남기지만 자동 Publish 대상 조회와 Publish를 모두 차단한다.
- Platform 게시 실패 시 Outbox를 유지한다. Retry는 Command 해소와 PSI 계산을 건너뛰고 저장된
  Bundle을 다시 검증해 동일 Pointer를 게시하며, 성공 후 Outbox를 `PUBLISHED`로 전환한다.
- 공용 Runtime 배포와 실제 E2E Write는 별도 승인 대상이다.

### 개발 Cost Profile과 Stress Registry 연결 — 완료(로컬)

대상: InventoryEngine 계약·평가 모듈, 권위 비용·공급 Source.

- 개발 Cost Profile `DEV-KRW-COST-BASELINE/1.0.0`과 수요 20% 증가·확정 입고 7일 지연
  Scenario를 승인 Reference, Payload Hash, Runner Hash와 함께 Strategy Execution Plan에 봉인한다.
- Runtime은 Registry가 해소한 동일 Payload만 허용하고 실제 PSI·제약 적용 행동에서 보유비,
  Backorder 비용, 고정·변동 발주비를 다시 계산한다.
- 이는 개발 E2E용 합성 비용이다. 권위 회계 Cost Profile과 운영 승인 Stress Catalog가 준비될
  때까지 운영 경제성 판정에는 사용하지 않는다. Profile 미결합 Run은 계속
  `COST_PROFILE_NOT_BOUND`로 호환한다.

### Trade Cost Source·Landed Cost V1 실행 기준선 — 완료(개발 환경)

대상: InventoryEngine 계약·Schema, dsai-platform 문서, 개발 PostgreSQL 읽기 영역.

- Source Owner·수집 Manifest를 정의했다. 개발 Fixture는 개발 환경에서 승인할 수 있고
  `development_eligible=true`, `operational_eligible=false`로 해석한다.
- Source Domain별 Versioned Revision과 승인 Revision Set, 실행별 `TB_IO_*` Evidence를
  Migration 077로 설계하고 개발 PostgreSQL에 적용했다.
- Decimal 기반 종가·종량·복합세·Floor/Cap과 고정비 배부기를 구현하고 일본·중국·사우디·UAE
  합성 Golden 4건을 고정했다. 합성 Rate는 법정·운영 세율이 아니다.

- [Trade Cost·Landed Cost 계약](IO_TRADE_COST_LANDED_COST_CONTRACT.md)에서 V1을
  `C100: V100→V101~V104`로 제한하고, 물리 Grain을 Shipment Line+Lane+Item+적용일로 고정했다.
- 개발 PostgreSQL의 C100 35,000개 Site-Item과 승인 Network 8개 Lane을 읽기 전용으로 조사했다.
  `EA`와 Site/국가 관계 외의 HS·원산지·구매가격·Incoterms·운송비·VAT/FTA Source는 운영 계산에
  사용할 수 없으며, 과거 판매가격·환율은 `UNVERIFIED`로 분류했다.
- Source Catalog와 Landed Cost Assessment DTO/JSON Schema를 구현했다. 미확인 값을 0으로
  치환하지 않고 Component 합계, 회수 가능 VAT, FTA Evidence, Actual Replay와
  `operational_eligible`를 Fail Closed로 검증한다.
- Source Revision/Revision Set 승인·Supersede CAS API와 Backend-resolved Run Binding을 구현했다.
- Sealed Projection API는 Run에 고정된 Set ID·Hash를 받아 Repeatable Read로 Source 27개와
  Document·구조화 Record를 읽고 Source·Set Hash를 재계산한다. InventoryEngine Runtime Resolver는
  Projection Hash, Scope, 적용일과 개발/운영 환경을 다시 검증한다.
- 국가별 공식 Source 후보는 운영 전환 자료이며 개발 선행조건이 아니다. Landed Cost Child
  Artifact와 개발 Simulation·MEIO 비용 연결은 아직 수행하지 않았다.

### 실제 V2 Config·분류 Snapshot 게시 검증 — 완료(개발 DB)

대상: dsai-platform Backend, InventoryEngine `classify_inventory`, 개발 PostgreSQL.

- 프로젝트 `9ed62f41`, DSE/C100/V100에 프로젝트 전용 VED 승인본 7,000건과 Config 2.0.0
  Revision 2를 게시·활성화했다.
- 최근 봉인 Actual Close 26주(`202528`~`202601`)로 Snapshot Revision 2를 게시했다. Header
  1건, Item 7,000건, Axis 49,000건, Window 3건, Policy Reason 26,610건을 대사했다.
- 자동 실행 Gate는 `ALLOW` 2,296건, `REVIEW` 4건, `BLOCK` 4,700건이다. 동일 입력 재실행은
  같은 Snapshot ID/Hash의 `exact_replay`이고 추가 DB Write가 없음을 확인했다.
- 상세 식별자와 Hash는 [개발 V2 게시 증적](evidence/inventory-v2-publication-20260915.json)에
  기록한다. 실제 운영 중요도를 뜻하지 않는 개발 VED와 합성 PLC라는 한계는 유지한다.

### 3. Planning Cycle Migration 074와 실제 Claim 검증 — 완료(개발 DB)

대상: dsai-platform Backend, 개발 PostgreSQL.

- Migration 074 Rollback Canary를 통과한 뒤 개발 PostgreSQL에 적용하고 Migration Ledger와
  Table·Column·Constraint·Function·Trigger Signature를 대사했다. 동일 파일 재적용은 DB Write
  없는 Exact Replay였다.
- 실제 V2 Config Revision `d4be4ad6-...`, 분류 Snapshot
  `e54e5649-ad2b-578c-b1fa-2e5603446895`, Effective Policy Hash, 개발 Cost Profile과 Stress
  Scenario Hash를 Strategy Plan 1.1.0과 Claim에 고정했다.
- Attempt 1 실패 후 Attempt 2를 새 `engine_run_id`로 재시도했고 두 Attempt의 10개 Input
  Binding이 동일함을 확인했다. Stale Attempt CAS는 차단됐다.
- V2 집계 Gate가 자동 게시 불가이므로 `effective_run_id` 승격은
  `inventory_effective_run_review_required`로 차단했다. 별도 V1 호환 Cycle에서 성공·검증 Run의
  Effective Pointer CAS와 Exact Replay를 확인했다.
- 상세 식별자와 한계는 dsai-platform의
  `docs/backend/dsim/evidence/inventory-run-lifecycle-migration-074-20260915.json`을 따른다.
  Scheduler→공용 Runtime과 운영 Artifact Publication은 아직 범위 밖이다.

### 4. Landed Cost Child Artifact와 개발 Simulation 비용 연결 — 다음 작업

대상: InventoryEngine `run_inventory`·`calculate_landed_cost`, dsai-platform Run 결과 계약.

- 현재 기준선: [Child Artifact 계약](IO_LANDED_COST_CHILD_ARTIFACT_CONTRACT.md)의 봉인 입력,
  Source 해소, Component/총액 저장과 원본 기반 Replay는 Offline 완료했다. 별도 비용 Child이며
  기존 PSI `result_children`에 비용 Row를 추가하지 않고 Publication Outbox도 만들지 않는다.
- 다음 작업: 실제 Claim으로 Shipment를 해소하고 비용 Child 포인터를 Runtime·Parent 결과에
  연결한다. Parent 계약 변경은 InventoryEngine·Platform 사이에서 먼저 정합화한다.
- 그 후 개발 Simulation 평가에서 보유비·Backorder·발주비와 운송·관세·세금·구매비의 중복
  계산을 차단한다. 실제 Multi-Echelon 최적화 Solver 구현과는 별도 단계다.
- 완료 조건: 같은 Run의 봉인 비용 입력을 동일하게 복구하고 `CALCULABLE`인 개발 결과만
  비용 평가에 사용한다. 미확인 비용은 0으로 대체하지 않는다. Offline 개발에 운영 Owner
  승인은 필요 없고, DB Write·공용 Runtime 배포는 별도 승인 대상이다.

### 5. 실제 Demand·Inventory Source 연결 — 운영 전환 시 외부 작업 대기

대상: DemandEngine/Studio Export, InventoryEngine Source Reader, 운영 Source 소유 부서.

- Demand 성공 후 봉인 Forecast Artifact와 Receipt를 정확한 Cycle Binding으로 전달하고,
  고객 주문·재고·확정 공급·Reserved를 동일 Cut-off와 Watermark로 수집한다.
- SDE는 확정 발주·실입고 이력, HML은 평가 단가·통화·기준일, PLC는 승인 Lifecycle Revision이
  필요하다. 준비 전에는 SDE/HML `UNVERIFIED`, PLC `SYNTHETIC + SHADOW_ONLY`를 유지한다.
- 완료 조건: 동일 Source Binding이 동일 Canonical Hash를 만들고 누락·Late Posting을 차단한다.
  Source 게시나 DB Write가 필요하면 별도 승인받는다.

### 6. PPO 개선 실험 — 다음 작업, 병렬 가능

대상: InventoryEngine ML/PPO 학습·평가 모듈.

- 수학 전략을 운영 기준선으로 유지하고 TRAIN/VALIDATION에서 상태·행동 Mask·보상·종료 효과와
  무수요/간헐수요 표본을 개선한다. 이미 본 TEST에 맞춰 튜닝하지 않는다.
- 완료 조건: 새 Revision과 새 Holdout에서 비용과 서비스 Gate를 함께 충족하거나 명확한
  거부 근거를 남긴다. 자동 운영 전환이나 공용 Runtime 배포는 하지 않는다.

### 7. Publication 물리 계약과 실제 개발 E2E — 승인 필요

대상: InventoryEngine `deliver_inventory`, dsai-platform Runtime, 개발 PostgreSQL과 Artifact 저장소.

- 보류한 P0-13/P0-18을 재개해 `TB_IO_*` Grain·PK/FK·Hash·Receipt, 게시 멱등 Key,
  부분 실패 재발행과 Effective Run CAS를 확정한다.
- Demand→IO 순차 실행과 실패 Site Retry, Artifact/DB 불일치 복구를 실제 Run으로 검증한다.
- 완료 조건: 성공 Run의 Evidence와 Effective Pointer가 일치하고 실패 Run은 정상 결과로
  노출되지 않는다. Migration·DB Write·공용 Runtime 배포는 모두 별도 승인 대상이다.

### 8. 운영 전환과 후속 Capability — 외부 작업 대기 / 승인 필요

대상: 실제 운영 Source·배포 환경, 향후 Multi-Echelon 및 DSIM.

- 운영 전환 전에 실제 Source 정확성, P0-14 보존/복구, P0-19 회귀 합격 Gate를 확정한다.
- 단일 Site Core가 운영 검증된 뒤 Hub–Spoke Multi-Echelon Solver를 별도 범위로 착수한다.
- DSIM은 현재 미구현이며 선행 의존성이 아니다. 구현 시 봉인된 Evidence Read Contract를
  소비하게 한다.

## 5. 병렬 가능성과 승인 경계

공통 전략/PSI·수학적 기준·합성 기반·생산 평가·ML/PPO, V2 Runtime Binding과 개발 DB
Projection은 완료됐다. 모델 성능 합격이나 운영 활성화를 뜻하지 않는다. Artifact 저장
경계와 Runtime Handler 연결도 로컬 완료됐다.
완료된 Landed Cost Artifact와 PPO 연구를 사용하는 평가 구현은 공통 PSI·Result Bundle 계약을 바꾸지 않는 범위에서
병렬 진행할 수 있다. 공통 Artifact 계약 변경·DB Migration·실제 E2E는 직렬 작업이다.
운영 Source 연결은 운영 전환 시 외부 준비가 필요하며 현재 개발 Fixture 흐름을 차단하지 않는다.

실제 V2 Snapshot과 Migration 074·Claim 검증은 완료했다. Publication/E2E는 직렬 적용 전에
별도 승인이 필요하다.
`TB_IO_*` 물리 Schema는 Plan 1.1.0과 공통 Run ID 계약을 맞춘 뒤 작성한다.

기존 아키텍처·PM 기준선에 이번 Backend·QA 검증을 반영했다. 보호된 `.agents/` 대신 이 `docs/architecture`에서 기준선과 계획을 관리한다.

InventoryEngine은 `inventory_engine_dev`와 GitHub `origin/inventory_engine_dev`를 개발 기준 브랜치로 사용한다.
2026-09-29 사용자 결정에 따라 별도 `codex/` 브랜치를 만들지 않고 이 브랜치에서 직접 작업한다.
기존 `codex/inventory-result-orchestrator`는 동일 커밋의 병합 상태를 확인한 후 로컬·원격에서 삭제했고,
기존 미커밋 변경은 `inventory_engine_dev` 작업 트리에 그대로 보존했다.
dsai-platform의 후속 변경은 `origin/develop`에서 분리한 Inventory 전용 브랜치를 기준으로
독립 검토한다. 승인돼 완료된 개발 Migration 074·075·076·077 이외의 추가 Migration, 실제 업무 DB
Write와 Runtime 배포는 별도 승인 전 수행하지 않는다.

InventoryEngine은 PSI Orchestrator·영속 Artifact/Outbox·Runtime와 Trade Cost·Sealed Projection을
기능별 Commit으로 분리하고 이 문서·계획·브랜치 지침을 별도 기준선 Commit으로 관리한다.
Push 대상은 `origin/inventory_engine_dev`이며 Platform과 DemandEngine을 함께 Push하지 않는다.
사용자 원본 `main.py`, 연구 Notebook, 환경 파일, IDE 설정과 생성 Output은 제외한다.
별도 SCM 전문가 보고서의 사용자 수정도 Commit에 포함하지 않고 그대로 보존한다.

## 6. 의사결정과 검증을 분리한 목록

| 구분 | 처리 |
|---|---|
| 이미 결정됨 | POSM/TGSM, 단일 Site/Company, 실행 소유권, Calendar·Master 기준일, 세 보충 전략·공통 PSI, Source 제약 우선, Cut-off·중간 Evidence, Network Master 소유권 |
| 구현·근거 검증 필요 | P0-1 Legacy 외곽선, P0-10 운영 Artifact 봉인·업무 검토(독립 합성 Generator/Golden 로컬 완료), P0-11 공통 실행, P0-15 실제 Source/운영 검증(로컬 PSI Golden 완료), P0-16 실제 Column 의미·정규근사 정책의 운영 적합성(수학적 계산/정책 선택 Golden 완료) |
| 다음 물리 설계 | P0-13 TB_IO Grain·필드·PK/FK·Hash·Receipt |
| 보류 유지 | P0-14 보존·복구, P0-18 Publication 상세, P0-19 정식 Legacy 합격 Gate. 각 착수 경계에서만 재개 |
| 향후 범위 | Multi-Echelon 계산·실제 물류 Source, DSIM Agent/조회 API |

Source 읽기·변환, V2 Runtime Binding, 개발 DB V2 Config·분류 Snapshot 게시와 동일 Run 로컬
PSI Orchestrator는 완료했다. dsai-platform Strategy Plan 1.1.0 정합화, 개발 Cost/Stress
Registry, Result Bundle 영속 Adapter와 Runtime Handler 연결도 로컬 완료했다. Migration 074와
실제 Claim·Retry·CAS 검증 역시 완료했으므로 다시 실행 목록에 넣지 않는다. 다음 직렬 작업은
완료된 Landed Cost Child를 실제 Claim·Runtime·Parent 결과와 개발 Simulation 비용에 연결하는 것이다. 실제 Publication/E2E·공용 Runtime
배포는 별도 승인 대상이며, 권위 SDE/HML/PLC·Trade Source는 운영 전환 때 준비한다.
Multi-Echelon Solver와 DSIM은 별도 후속 범위다.
