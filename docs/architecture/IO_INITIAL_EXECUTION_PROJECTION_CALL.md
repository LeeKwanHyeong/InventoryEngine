# 독립 실행 결과의 Writer·같은 Run 조회 연결

## 2026-10-22 후속 — 실제 인증·로컬 화면에서 기존 Run 읽기 전용 조회 확인

로컬 Frontend의 기존 BFF·HttpOnly 쿠키·proxy와 Backend의 실제 JWT·프로젝트 RBAC·Repository·봉인 Reader로
V100·202512 기존 Run을 조회했다. 저장 완료 / 계산 완료 / 검토 대기와 588,066행, 기존 Receipt를 확인했다.
Actor 주입·응답 재생이 아니며 무인증/로그아웃 후 조회는 401이다. 전체 Portal/M3 배포 검증과는 구분한다.

DB Write 금지 조건에 맞춰 기존 개발 읽기 전용 인증 방식을 사용했다. 실제 비밀번호·계정 상태·Tenant 소속을
검사하고 기존 발급기로 JWT를 발급하지만 일반 로그인의 last_login UPDATE는 하지 않는다. 공용 인증 코드는 유지한다.
시작/종료 DB 지문·건수·이벤트 6건과 원본/파생/Receipt Hash가 같다. 증적 pending·유효 Run NULL·자동 게시/발주 false,
FSN·운영 Gate·ID·Hash·계산 Build는 그대로다. 재계산·재적재·완료 이벤트 재반영·권한 부여·M3 배포·Commit/Push는 없다.

관련 Backend 41 passed·Frontend 462 passed와 TypeScript·Ruff·Format·Diff Check를 확인했다.
ESLint 미설치와 기존 경고는 남는다. 인증 쿠키를 정리하고 로컬 두 서버를 종료했다.
실제 증적은 Platform `docs/backend/dsim/evidence/inventory-v100-authenticated-ui-readonly-20261022.json`,
다음 작업은 [개발 기준선](IO_DEVELOPMENT_BASELINE.md)의 최신 절을 따른다.

## 2026-10-22 최신 — 기존 Run 계산 완료·같은 Run 조회·중복 재시도 확인

사용자 승인 후 로컬 Backend에서 기존 V100·202512 Run `4f7c3cac-9264-4b34-bf77-0eda92f0b6b2` / Attempt 1에
계산 완료 이벤트 한 건을 반영했다. Run은 `succeeded`이며 최초와 중복 응답 모두 `event_seq=6`이다.
중복 응답은 `replayed=true`이고 같은 Run 조회는 `STORED`·`completion_event_required=false`·`review_required=true`다.

결과 21개 테이블의 Receipt 포함 588,066행과 기존 Receipt·원본/파생/Receipt 전체 Hash는 유지했다.
Config·Revision·입력·Site·Run Metadata도 그대로다. 프로젝트 한정 임시 실행 권한은 검증 후 회수했고
별도 읽기 전용 확인에서 임시 Role·권한 연결·사용자 부여 행은 0건, callback=false·기존 조회 권한=true다.

Evidence는 `pending`, Site/Cycle은 `running`, 유효 포인터는 NULL이다. 계산 완료를 운영 게시로 승격하지 않는다.
Engine 계산 코드·설치 Build·ID·Hash·FSN·운영 Gate는 유지했고 재계산·결과 재적재·Migration·운영 게시·
M3/공용 배포·실제 발주·Commit/Push는 없다. 관련 Platform **273 passed / 0 skipped**와 Ruff·Format·Diff Check를 확인했다.
실제 PostgreSQL 완료·조회·중복 재시도와 Offline 장애 복구 검증은 구분한다.

상세 계약은 Platform `docs/backend/dsim/inventory-stored-result-completion-v1.md`,
실제 증적은 `docs/backend/dsim/evidence/inventory-v100-calculation-completion-applied-20261022.json`을 따른다.
남은 순서는 [개발 기준선](IO_DEVELOPMENT_BASELINE.md)의 최신 절을 따른다.
아래의 완료 이벤트 미제출 표기는 사전 이력이다.

## 2026-10-22 이전 이력 — 저장 결과 조회·계산 완료 API 구현 / 실제 완료 이벤트 미제출

로컬 Backend에서 기존 Receipt·Manifest·Bundle·전체 입력 Binding을 대조하는 조회와 계산 완료 API를 구현했다.
완료 API는 기존 이벤트·멱등 Key를 재사용하며 운영 게시·발주를 호출하지 않는다. 비용 입력이 있는 Run의
Parent Manifest 검증도 유지한다. Engine 계산 코드·설치 Build·ID·Hash·FSN·운영 Gate는 바꾸지 않았다.

실제 V100은 읽기 전용 ASGI 조회에서 기존 저장 결과를 반환했다. DB Run 지문·이벤트 5건과 원본·파생·Receipt
파일 Hash는 그대로다. 실제 완료 이벤트는 준비만 했으며 Run `running`·Evidence `pending`·유효 포인터 NULL이다.
기록용 SQL Pool의 완료·같은 이벤트 재시도·Rollback·응답 유실 복구와 실제 DB 적용은 구분한다.

상세 계약은 Platform `docs/backend/dsim/inventory-stored-result-completion-v1.md`,
증적은 `docs/backend/dsim/evidence/inventory-v100-stored-result-completion-20261022.json`을 따른다.
남은 순서는 [개발 기준선](IO_DEVELOPMENT_BASELINE.md)의 최신 절을 따른다.

## 최신 정정 — V100 실제 적재·같은 Run 조회·멱등 재시도 완료

2026-10-21. 사용자 승인 후 로컬 dsai-platform Backend에서 기존 Run
`4f7c3cac-9264-4b34-bf77-0eda92f0b6b2` / Attempt 1의 보정 Manifest `db59441b…7b85`를
기존 Writer에 명시적으로 전달했다. 원 Source 실행·Bundle·원본/파생 저장소·두 Build는 유지했다.

- 실제 PostgreSQL 최초 적재는 `replayed=false`였다. 같은 Run 조회에서 Artifact·DB 전체 행을 대사했고,
  동일 요청 재시도는 같은 Receipt와 `replayed=true`를 반환했다. 행 수는 늘지 않았다.
- 21개 테이블을 대사했으며 Receipt 포함 588,066행이다. PSI 188,188행은 Baseline/Mathematical 각 94,094행,
  보충 권고는 94,094행이다. 후보·평가·선정 0행은 아직 실제 정책 탐색 검증을 하지 않았다는 경계다.
- 실제 권한 Resolver를 사용했고 승인된 프로젝트 한정 임시 callback 권한은 검증 후 회수했다.
  별도 종료 확인에서 임시 Role·Role Permission·User Role 0건, callback=false·기존 조회 권한=true다.
- 원본·파생 전체 SHA-256과 Config·Revision·입력·Run·Site DB 지문을 보존했다.
  모든 권고의 자동 게시·발주 값은 false다. FSN·운영 Gate·ID·Hash는 바꾸지 않았다.
  Forecast/PSI 재계산·Migration·M3/공용 배포·실제 발주·Commit/Push는 하지 않았다.
- Run/Site `running`, 원장 Evidence `pending`, 유효 결과 포인터 NULL은 유지했다.
  계산 완료 Event·조회 연결은 다음 대사 대상이며 운영 게시 Gate를 우회하는 완료 처리는 하지 않는다.
  관련 Platform 32개 테스트·Ruff·Format·Diff Check를 확인했으며 전체 저장소 회귀를 뜻하지 않는다.

실제 실행 증적은 Platform
`docs/backend/dsim/evidence/inventory-v100-repaired-load-read-retry-20261021.json`을 따른다.
남은 작업 순서는 [개발 기준선](IO_DEVELOPMENT_BASELINE.md)의 최신 절을 따른다.
아래의 DB 적재 보류·승인 필요는 이전 봉인·Offline 연결 이력이다.

## 이전 이력 — V100 전체 파생 봉인·독립 검증 완료 / 실제 DB 적재 보류

2026-10-21. 검증한 보정 패키지를 별도 로컬 경로에 설치하고 기존 Run
`4f7c3cac-9264-4b34-bf77-0eda92f0b6b2` / Attempt 1의 봉인 Bundle만 재사용했다.
원 계산 설치본·Build·입력·원 Source 실행·원 Projection은 교체하지 않았다.

- 새 Manifest `db59441b…7b85`, 논리 Projection `1bb744fa…f3c6`, 보정 증적 `1cf0990a…83e2`를 봉인했다.
  3,619품목·26주와 전체 Table 건수를 유지한다. 19,251개 권고 행의 자동 허용값 축소와 행 Hash 외에는 차이가 없다.
- Baseline/Mathematical PSI 각 94,094행·권고 94,094행·원본 분류를 보존했다. 전체 분할 1,190개 중
  원본 1,001개를 재사용하고 새 189개 분할·Manifest·보정 증적, 총 191개만 별도 저장했다.
  새 Manifest는 원본과 파생 저장소를 함께 읽어야 완성된다. 별도 저장소만 독립 결과 전체라고 해석하지 않는다.
- 원본은 SQLite 읽기 전용 연결로 열었고 봉인 전후 원 저장소 전체 SHA-256과 7종 파일 지문이 일치했다.
  Source 실행·원 Bundle·Outbox·Run Binding은 그대로이며 새 Publication Outbox를 만들지 않았다.
  PSI/전략 계산 함수와 네트워크 연결은 실행 시 실패하게 막았다. PostgreSQL은 접속하거나 적재하지 않았다.
- 원 계산 Build `b0da22ce…a1d4`, 별도 Projection Build `0b90759d…f983`와 정확한 두 포인터는
  `inventory.dsim_projection_gate_repair`에 함께 봉인됐다. 기존 Source 실행의 고정 참조는 여전히 원본을 가리킨다.
- 봉인 후 별도 프로세스에서 두 저장소를 읽기 전용으로 열어 독립 검증을 통과했다. 원본 지문과 전체 건수도 같다.
  새 설치 패키지 관련 Engine **20 passed**,
  Platform·로컬 저장소 관련 **10 passed**, Ruff·Format이 통과했다. FSN·운영 Gate·DB·공용 Runtime은 유지했다.

후속으로 로컬 dsai-platform Backend의 `write_gate_repair_verified`와
`build_inventory_local_projection_repair_assembly`에 새 보정 참조·두 저장소 Reader를 연결했다.
원본·파생은 모두 읽기 전용이며 Writer Receipt만 별도 저장소에 추가할 수 있다.
기존 권한·Scope·전체 보정 증적을 확인한 뒤 정확한 새 Manifest를 기존 Writer로 넘긴다.
원 계산 Build를 바꾸거나 잘못된 원 Projection을 다시 선택하지 않는다.

실제 V100 새 참조는 기존 Writer의 쓰기 차단까지 전달됐다. PostgreSQL Pool 접근 0회이고
원본·파생 전체 Hash도 같다. 실제 권한 검사가 아닌 Offline 권한 대역이며 DB 상태는 재조회하지 않았다.
관련 Platform **183 passed / 0 skipped**, 기존 Starlette 경고 1건과 Ruff·Format·Diff Check 통과다.
작은 합성 Fixture/기록용 SQL Pool에서는 저장·같은 Run 조회·중복 재시도·Rollback 후 복구를 확인했다.
실제 PostgreSQL 제약·Trigger·Lock 검증이나 전체 저장소 회귀 합격을 의미하지 않는다.
실행 대상은 맥북 로컬이며 M3 배포·실제 DB 접속/적재·Migration·기존 V100 Forecast/PSI 재계산·
FSN/운영 Gate 변경·Commit/Push는 하지 않았다.
실제 개발 PostgreSQL 저장·같은 Run 조회·중복 Writer 재시도만 별도 승인 후 진행할 다음 작업이다.
588,065행의 Projection Artifact와 미래 Receipt 1행을 DB 적재 완료 건수로 표현하지 않는다.
Run 상태·유효 결과 포인터를 변경하지 않았으며 C# 목표 계산·정책 탐색·ERP 연계도 이번 범위가 아니다.
정확한 Hash·건수는 Platform `docs/backend/dsim/evidence/inventory-v100-gate-repair-seal-20261021.json`을 따른다.
후속 Backend 연결 결과는 Platform `docs/backend/dsim/evidence/inventory-v100-repair-writer-offline-20261021.json`을 따른다.
아래의 미설치·전체 파생 미봉인은 이번 작업 전의 이력이다.

## 최신 정정 — 동일 Run 적재 시도 / CHECK 보정 완료 / 봉인 Projection의 Run Gate 충돌

2026-10-21. 사용자 재개 승인으로 기존 Run `4f7c3cac-9264-4b34-bf77-0eda92f0b6b2`의
봉인 Source 실행 포인터를 Writer에 전달했다. `load_verified --input-revision 3`는 계산 진입점을 호출하지 않는다.
기존 Build·Run·입력·Artifact를 그대로 읽으며 Forecast·PSI 재계산이나 새 Revision을 만들지 않는다.

최초 INSERT의 NULL 거부는 사용자 별도 승인으로 Migration 092에서 `ck_io_prop_15` 한 개만 보정했다.
`approval_level IS NULL OR btrim(approval_level) <> ''`을 허용하고 기존 승인 등급과 자동 게시·발주 제약은 유지한다.
기존 091·봉인 Catalog·원본 Hash는 수정하지 않았다. 백업·Rollback 예행·PostgreSQL 9개 사례를 확인했다.

재개한 INSERT는 `IO_EVIDENCE_CANNOT_ESCALATE_CLAIM_GATE`로 중단됐다.
Run의 자동 게시·발주 허용값은 false인데 봉인된 권고 중 ADJUSTED 19,136행과 ACCEPTED 115행은 true다.
Projection 생성기가 품목 Admission·Child Role에서 플래그를 만들 때 상위 Run Gate를 적용하지 않은 것이 원인이다.
DB Trigger는 정상적으로 차단했다. 기존 봉인 행을 수정하거나 Trigger를 우회하지 않았다.

적재 Transaction은 전부 Rollback됐다. 결과 21개 테이블·Receipt는 이 Run 기준 0행이고 임시 callback Role은 0건이다.
입력 Binding 10건·Config 전체 값/Hash·활성 Revision·승인 분류와 FSN·운영 Gate는 그대로다.
Run/Site는 `running`, Evidence는 `pending`, 유효 결과 포인터는 NULL이며 실제 프로세스는 종료됐다.
저장 결과의 같은 Run 조회·중복 Writer 재시도는 아직 완료하지 못했다.

관련 Platform **115 passed**, 기존 Starlette 경고 1건과 Ruff·Format·Diff Check 통과를 기록한다.
이 수치를 실제 DB 적재 성공이나 Engine 전체 회귀 합격으로 해석하지 않는다.
공용 배포·실제 발주·Commit/Push는 수행하지 않았다.

### 후속 승인 — 같은 Run의 파생 저장 계약·Offline 검증 완료

사용자 승인 후 `RepairDsimProjectionGatesUseCase`와 Platform의 `prepare_gate_repair`·`verify_gate_repair`를 구현했다.
새 HTTP Route·공용 Runtime·기본 Factory·상시 권한을 활성화하지 않으며 이번에는 추가 DB 적용·적재를 하지 않는다.

| 항목 | 원본 보존·파생 저장 규칙 |
| --- | --- |
| Run·Attempt·입력 Binding·Plan | 기존 값을 유지하며 새 실행으로 위장하지 않는다 |
| 원 PSI·Bundle·Outbox·분류 | 저장된 Artifact만 읽는다. Source/PSI를 재실행하거나 다시 봉인하지 않는다 |
| 원 Source 실행·원 Projection | 기존 고정 참조와 바이트를 보존한다 |
| 파생 Projection | `Run Claim AND 품목/Child 허용` 적용. 두 허용값 축소와 해당 행 Hash만 변경 가능 |
| 계산 Build | 원 Source 실행의 값을 유지하고 기존 Run Plan의 전략·PSI 구현 Hash와 대조한다 |
| Projection Build | 운영자가 별도로 고정한 현재 코드 지문과 일치해야 한다. 원 계산 Build를 바꾸지 않는다 |
| 보정 증적 | `inventory.dsim_projection_gate_repair` / `RUN_AND_ITEM_GATES_V1`. 원본·파생 참조와 두 Build를 함께 봉인한다 |
| 저장 계약 | 기존 Projection/Receipt 1.0.0·테이블·ID를 재사용한다. 새 UUID나 DB 컬럼은 만들지 않는다 |

원본의 Hash·건수 무결성 읽기는 보정에 필요한 과거 자료의 해석일 뿐 Source 검증 합격이 아니다.
일반 Writer/Reader는 여전히 Source에서 행을 재구성해 검증한다. 잘못된 원 Projection은 계속 차단한다.
파생 결과가 수량·발주일·도착일·정책·다른 테이블을 변경하면 거부한다. 기존 Receipt나 부분 행이 있다면
Writer가 동일 Run의 교체를 거부한다. 이번 V100은 마지막 DB 대사 기준 결과·Receipt 모두 0행이다.

합성 Offline Fixture에서 같은 Run 파생 봉인·독립 검증·SQLite 저장/조회/중복 재시도와 원본 보존을 확인했다.
Source·PSI 실행 진입점과 실제 전이 함수는 테스트에서 호출 시 실패하게 막았다.
실제 V100의 94,094개 봉인 권고에 대해서도 19,251행의 Gate만 축소되고 나머지 필드가 같음을 읽기 전용으로 대사했다.
전체 V100 파생 Projection을 봉인하거나 실제 DB에 저장한 것은 아니다. 수정 Wheel도 아직 설치하지 않았다.
최종 관련 검증은 Engine **72 passed**, Platform **119 passed**, 기존 Starlette 경고 1건과 Ruff·Format·Diff Check 통과다.

**1. 실제 V100 파생 참조와 설치 후보를 준비한다 — 다음 작업 / DB Write 없음**

- 원 계산 Build와 별도 Projection Build를 고정하고 기존 Bundle에서 전체 파생 Projection·보정 증적을 만든다.
  원 Source 포인터를 덮어쓰거나 기존 `load_verified`로 잘못된 원 Projection을 반복 적재하지 않는다.

**2. 새 파생 참조로 실제 적재·조회·재시도를 검증한다 — 승인 필요 · 개발 PostgreSQL**

- DB Trigger·FSN·운영 Gate는 유지하며 Forecast·PSI 재계산·공용 배포·실제 발주는 하지 않는다.

실제 지문·행 그룹·Rollback 건수는 Platform
`docs/backend/dsim/evidence/inventory-v100-sealed-load-20261021.json`을 따른다.

아래의 사용자 보류·미실행 표기는 재개 이전의 이력이다.

## 최신 정정 — Revision 3 계산·봉인 검증 완료, DB 적재는 사용자 요청으로 보류

2026-10-21. 기존 관측·증적 상한을 올리지 않고 수학적 Runtime의 품목별 분할 계산을 연결했다.
반복 불변 관측은 RAW_ACTION Artifact 1.1.0의 공통 Source 참조로 보존하며 기존 1.0.0 읽기를 유지한다.
동일한 작은 입력에서 PSI·주문·관측 Hash·Guard 결과가 같고, 복원 후 원 관측·제약을 다시 검증한다.
원문 저장 표현이 달라진 Artifact Hash까지 동일하다고 보지는 않는다.

새 입력 묶음 1.1.0과 Cycle 요청 V2는 원 Demand Planning Input Revision을 유지한 채 새 IO Revision을
고정한다. 기존 DB 구조를 재사용해 추가 Migration은 적용하지 않았다. 이전 입력/실패 Run을 수정하지 않는다.

Revision 2 Run `4b4e5b81-99bf-4eb9-9c31-654905ed4ca4`는 계산·Artifact 검증 뒤 Projection에서 실패했다.
원본 Calendar/Master/Forecast와 Canonical 변환 Snapshot의 ID·Hash를 혼동한
`PROJECTION_INPUT_BINDING_MISMATCH`였다. 결과 테이블은 0행이고 임시 권한은 회수했다.
Projection의 원본 포인터는 정확한 Claim을 따르며 계산용 포인터·건수는
`source_metadata.canonical_snapshot`과 `source_row_count_basis=CANONICAL_SNAPSHOT`으로 분리했다.
다른 원본 ID/Hash는 계속 차단한다. 결과 DDL·FSN·운영 Gate는 바꾸지 않았다.

승인 입력 Revision 3의 Run `4f7c3cac-9264-4b34-bf77-0eda92f0b6b2`에서
3,619품목·26주를 계산했다. Baseline/Mathematical PSI 각 94,094행과 결과 1,190개 분할 Artifact·Manifest의
봉인·Source 실행 검증이 완료돼 정확한 저장 참조를 보존했다.
관련 회귀는 Engine **207 passed / 192 subtests passed**, 새 Wheel을 사용한 Platform **106 passed**다.
사용자 요청으로 Writer의 반복 검증 중 프로세스를 종료했으며 결과 INSERT는 하지 않았다.
중단 후 읽기 전용 대사에서 해당 Run의 결과 21개 테이블·Receipt 0행과 임시 callback Role 0건을 확인했다.
Run/Site 원장 `running`, Evidence `pending`은 수정하지 않았고 유효 결과 포인터는 NULL이다.
실제 프로세스는 종료됐으며 자동 적재·재개하지 않는다. 기존 입력·결과·포인터와 검증 Wheel은 보존했다.
DB 저장·같은 Run의 저장 결과 조회·Writer 재시도는 미검증이다.
사용자 재개 요청 후 동일 Build·Run·봉인 참조로 Writer부터 이어가며 Forecast/PSI를 다시 계산하지 않는다.
자동 게시·발주·공용 배포는 하지 않는다.
재실행 단계·Hash·실측 결과는 Platform
`docs/backend/dsim/evidence/inventory-v100-partitioned-rerun-20261021.json`을 따른다.
아래 내용은 이전 단계의 호출 계약과 검증 이력이다.

2026-10-21. InventoryEngine `inventory_engine_dev`, Platform Backend
`codex/inventory-psi-strategy-v2`의 명시적 로컬 조립이다. 실제 DB는 적용하거나 접속하지 않았다.

> 최신 정정(2026-10-21): 사용자 추가 승인으로 079를 적용하고 Config 2.1 활성 전환·새 분류·
> 3,619품목 개발 입력 승인·실제 Claim 생성까지 완료했다. 전체 분류의 차단 3품목은 보존했다.
> 실제 Run `efb8ed64-6d16-4092-bea0-06a7519f9c8c`는 `RECOMMENDATION_OBSERVATION_LIMIT`로 실패했다.
> 정식 Run의 분할 계산 연결이 없어 이 Run의 결과 21개 테이블은 0행이며 실제 저장·조회·재시도는 미완료다.
> 아래의 ‘정식 Run 없음/079 제외’는 이전 시점 이력이며 현재 기준선은 `IO_DEVELOPMENT_BASELINE.md`를 따른다.

> 후속 정정: 같은 날 Migration 091로 개발 결과 21개 테이블을 적용했다.
> 기존 V100 입력의 정식 Run/승인 연결은 없어 실제 적재·조회·재시도는 차단됐다.
> 상세 현재 상태는 Platform `inventory-result-evidence-development-application-v1.md`와 개발 기준선을 따른다.
> 아래의 Offline 검증 이력과 호출 계약은 유지한다.

## 완료 범위

기존 독립 실행의 봉인 입력 → 원본 Bundle → 전체 후보 → 선정 → 발주 계획 → Projection 경로를
그대로 사용한다. Platform Service가 계산 완료 응답의 정확한 Projection 참조·Hash를 기존 Writer에
전달하고, 조회 시 같은 Run의 DB 행·Receipt를 원본 Artifact와 대조한다.
Engine 계산 코드와 기존 저장기는 바꾸지 않았다.

| 호출 | Pool이 명시적으로 연결됐을 때 | 쓰기 권한/동작 |
| --- | --- | --- |
| `execute` | 계산·Artifact 봉인 → Writer → 저장 Receipt 반환 | 기존 `inventory_run.callback`, 쓰기 Switch 필요 |
| `recover` | 저장 입력/결과 복구 → 같은 Writer 재시도 | 같은 Receipt·행은 재삽입 없이 멱등 처리 |
| `read` | 기존 Artifact 검증 → 같은 Run의 Receipt·전체 행 조회 | `engine_report.read`만 요구, SELECT 전용 |

Pool이 없으면 기존 Artifact-only 호출과 응답을 유지한다.
새 `projection_pool`, `allow_projection_writes=False`는 Platform 최초 실행/목표 결과 Factory의
선택 인자다. Writer에는 실행 때 사용한 동일 목표 Reader가 전달된다.
Pool을 자동 생성하거나 개발 DB 주소를 검색하지 않는다. 공용 Route/Runtime 등록도 하지 않는다.

## 결과와 실패·복구

- 기존 `result_pointer`의 Projection Reference·Hash를 그대로 전달한다. 별도 업무 ID나 Hash를 만들지 않는다.
- 응답의 `database_projection`에 저장 Receipt 또는 조회한 행을 담는다. 이는 봉인 원문 밖의 서비스 응답이다.
  원본 Bundle/선정/발주 계획·행/논리 Projection Hash는 바꾸거나 재봉인하지 않는다.
- `database_writes`는 새 저장이 완료됐을 때만 true다. 읽기·멱등 재시도는 false다.
- SQL 저장 실패는 오류로 반환하되 이미 봉인된 Artifact를 지우지 않는다.
  `recover`는 원천을 다시 조회하지 않고 동일 참조로 Writer를 재호출한다.
  commit 응답만 유실된 경우도 기존 Receipt와 전체 행을 대조하고 재삽입하지 않는다.
- 조회는 원본 재검증 중 DB Transaction을 잡지 않는다. Binding을 먼저 확인하고,
  행 조회 시 별도 `repeatable_read / readonly=True` Transaction에서 Binding·Receipt·행을 다시 확인한다.
  누락/불일치·권한 실패는 차단한다. INSERT·Run 쓰기 Lock·자동 수리는 하지 않는다.

FSN BLOCK은 Writer 호출 이전에 차단한다. REVIEW는 계산·증적 저장만 허용한다.
자동 게시·자동 발주는 계속 false이며 DB 증적 저장을 업무 게시/발주 승인으로 보지 않는다.

## 검증 경계

신규 테스트 `tests/integration/test_initial_target_projection_platform_offline.py`는
기존 봉인 개발 Fixture(2개 후보·3주)에서 최초 계산부터 동일 Run 조회까지 검증한다.
SQL은 Recording Pool, 수량 Port는 ReferenceKernel이다. 실제 PostgreSQL/C# 실행 검증이 아니다.
Platform에서는 기존 격리 설치 Wheel로 두 Factory의 연결과 기본 Artifact-only 호환성을 검사한다.
Engine `src` 전체와 Catalog·DDL·079·086 지문은 이번 작업 전후 동일해야 한다.

최종 관련 회귀는 Engine **150 passed**(139.15초), Platform **197 passed**(58.96초), Skip 0이다.
변경 Python의 Ruff·Format·양쪽 Diff Check도 통과했다. Engine Python 212파일은 기존 설치 Wheel과 같다.
전체 저장소 테스트·Coverage 합격 수치는 아니다.

최종 수치·파일 지문은 Platform의
`docs/backend/dsim/evidence/inventory-initial-execution-projection-call-20261021.json`에 기록한다.
실제 승인 V100 입력/Claim·개발 PostgreSQL DDL/DML·공용 배포·실제 발주·Commit/Push는 수행하지 않는다.

## 남은 작업

**1. 기존 분할 계산을 정식 Run의 결과 경로에 연결한다 — 다음 작업 · InventoryEngine·Platform Backend**

- 계산 전용 분할 구현을 재사용하되 기존 Guard·관측/증적 상한·정책 의미는 유지한다.
  3,619품목×26주의 계산 결과가 완전한 Bundle·Projection으로 저장/복구돼야 한다.
  새 Build/Plan/입력 Revision을 사용하며 이번 실패 Run과 승인 입력은 덮어쓰지 않는다.

**2. 실제 저장·같은 Run 조회·재시도를 확인한다 — 분할 연결 후 직렬 · 로컬 Runtime·개발 PostgreSQL**

- 이미 적용된 079·091을 재사용한다. 실패 Run의 결과를 빈 성공으로 게시하지 않는다.
  FSN·운영 Gate를 유지하고 자동 게시·발주·공용 배포는 제외한다.

**3. Demand→IO 한 Plan의 연결을 검증한다 — 독립 적재 완료 후 · DemandEngine·InventoryEngine·Platform Backend**

- 정확한 Forecast·Config·분류·입력 Binding으로 연결한다. Forecast 재실행이나 Gate 완화로 우회하지 않는다.
  실제 통합 DB Write는 별도 승인 대상이다.

Source 입력에는 `io-source-input-v2 / 2.1.0`과 개발 Universe V2를 추가했다.
전체 분류·정책 Hash를 유지하면서 승인된 차단 품목만 계산에서 제외하며, 원본 Master/Forecast는 바꾸지 않는다.
Backend는 asyncpg의 JSONB 문자열 표현을 처리하고 원 Forecast Binding과 계산용 파생 Snapshot을 구분한다.
사용자 승인으로 부여한 프로젝트 범위 임시 callback 권한은 실제 시도 종료 후 회수했다.
이번 실측은 기존 수학적 Python 경로이며 C# 목표 계산/정책 탐색의 실제 DB 합격을 뜻하지 않는다.
