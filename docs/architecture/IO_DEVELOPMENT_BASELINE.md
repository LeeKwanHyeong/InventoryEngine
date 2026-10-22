# InventoryEngine 개발 기준선과 작업 순서

## 2026-10-22 최신 — 같은 요청의 중복 재계산 축소·Offline 검증 완료

- InventoryEngine `inventory_engine_dev`에서 Link·Projection·최종 반환이 같은 결과를 반복 계산하던
  경로를 보완했다. 최초 실행은 **계산 1회 + 별도 전수 재계산 검증 1회**를 유지하고,
  완료 결과의 복구·강한 조회는 **요청마다 전수 재계산 검증 1회**를 수행한다.
  여기서 1회는 전체 후보 평가·품목별 선정·발주 계획 Pipeline 한 번이며, 개별 C# 전이 호출 한 번과 다르다.
- 같은 요청·같은 Worker의 성공한 재검증 이력만 잠시 재사용한다. Run·INPUT/Manifest Hash·DLL과
  계산 Kernel 객체가 같아야 하며, 재사용 범위를 반환·예외·취소로 벗어나면 이력을 폐기한다.
  새 요청·다른 스레드/작업·새 프로세스로 검증 상태를 넘기거나 DB에 `verified=true`를 저장하지 않는다.
- 재사용 시에도 원본 Artifact 바이트·전체 후보·건수·Hash·입력/Build/Profile Binding과 FSN·운영 Gate는
  매번 확인한다. 후보별 수량 상태·계산식·내부 검증·`completed_transition_calls`와 기존 ID/Hash 형식은 그대로다.
  상세 조회 GET은 이미 재계산하지 않으므로 수정하지 않았다. 별도 Writer Worker의 권한·DB 대사도 유지한다.

**실제 C# Offline 검증 — 소규모 1품목·3주, 동일 후보 Build 안에서 비교**

| 후보 방식 | 후보 수 | 재사용을 끈 최초 실행 전이 호출 | 재사용한 최초 실행 전이 호출 | 완료 복구 / 강한 조회 각각 |
| --- | ---: | ---: | ---: | ---: |
| ABC·XYZ | 32 | 2,970 | 990 | 495 |
| LEGACY_STYLE | 32 | 2,970 | 990 | 495 |
| 넓은 참조 | 88 | 8,010 | 2,670 | 1,335 |

- 위 최초 실행은 전체 Pipeline **6회 → 2회**다. 같은 설치 후보에서 재사용만 끈 대조 경로와 비교해
  입력·결과 Hash·저장 참조가 같음을 확인했다. 이전 Release와 새 Build의 Hash가 같다는 의미는 아니다.
  전체 응답시간·메모리 개선율이나 실제 V100 9품목의 재측정 수치로 해석하지 않는다.
- 실제 DLL 검증은 정상 후보 3개 사례와 `CHECKPOINT`·`BUNDLE`·`TARGET_PART`·`LINK`·`PROJECTION`
  게시 직후 `SIGKILL` 5개 사례, 총 **8개 사례**다. 새 프로세스는 Source 재조회 없이 복구했다.
  COMPLETE 전 중단은 전체 계산과 별도 검증 2회, COMPLETE 후 연결 중단은 검증 1회를 수행한다.
- 관련 Engine 회귀는 서로 다른 세 묶음 **61 + 132 + 404 = 597 passed / 147 subtests passed**다.
  Platform 관련 **56 passed**에는 새 임시 Wheel을 명시한 연결 6건이 포함된다. 기존 Starlette 경고 12건은 남는다.
  변경 파일 Ruff·Format·Diff Check도 통과했다. 전체 저장소·Frontend 회귀를 다시 실행한 것은 아니다.
- 검증용 새 설치 Build는 `1478bd76206d4eeebafe569569ac5015ae8489dd6e494ae74de35c0fcae66b55`,
  기존 DLL은 `9bc7b1576eb3bcd00d11fb28affb77f7b875764667f120ca54ac98faa944fe7f`다.
  기존 V100 Run·입력·설치 Build·원본 Artifact·FSN·운영 Gate는 교체하지 않았다.
  개발 PostgreSQL 접속/Write·Migration·Forecast 재실행·공용/M3 배포·실제 발주는 없다.
  이번 문서 정정에서는 계산·테스트를 재실행하지 않고 앞선 검증 기록을 코드와 대조했다.

구현 범위와 재사용 조건은 [저장 계약](IO_TARGET_PIPELINE_STORAGE.md#31-같은-요청의-재계산-검증-재사용)과
[로컬 Runtime 계약](IO_LOCAL_TARGET_RESULT_RUNTIME_CONTRACT.md#2026-10-22-후속--요청-내-중복-재계산-축소-완료)을 따른다.
기존 전체 회귀와 개발 DB 검증 이력은 아래에 보존한다. 자원 관리 고도화·공식 강건성 본실험은 계속 보류한다.

**1. 검증된 변경과 문서를 커밋 후보로 정리한다 — 다음 작업 · InventoryEngine `inventory_engine_dev`**

- 이번 코드·테스트·검증 스크립트·승인된 세 문서만 대사한다. 개인 보고서·환경·임시 산출물·Wheel·DLL은 제외한다.
  Commit·Push는 별도 요청 후 진행한다. 같은 공통 호출·저장 계약을 다루므로 현재 세션에서 직렬로 정리한다.

**2. 공유 환경 반영 여부를 정한다 — 승인 필요 · dsai-platform·M3/공용 Inventory Runtime**

- 공유 반영이 필요할 때 새 Build와 정확한 입력 Binding·설치 참조, 정식 Migration 호환성을 확인한다.
  기존 Run의 Build를 새 코드로 바꿔 표시하지 않는다. 실제 DB Write·공용 배포·운영 게시·발주는 별도 승인 대상이다.

## 2026-10-22 이전 이력 — 계산 완료·C# 상세 조회 연결과 저장소 기준선 정리

- 대표 9품목 Run `a791759c-92e5-48b0-a428-f8648ca4eec8`은 기존 봉인 결과를 유지한 채
  계산 완료 이벤트 반영·중복 재시도와 실제 인증 화면 조회까지 완료했다.
  Run은 `succeeded`, Evidence는 `pending`, `effective_run_id=NULL`이며 운영 검토는 남아 있다.
- Platform Backend/Frontend에서 C# 후보 288개·선정 6품목·무수요 미선정 3품목·권고 주문 21건을
  같은 Run의 저장 참조로 조회한다. 부모 Python 분석 81개는 별도 표시하며 합산하지 않는다.
  큰 후보 PSI 원문이나 C# 재계산 없이 봉인 선정·Projection의 참조·Hash·건수를 확인한다.
- 상세 조회 계약은 Platform `docs/backend/dsim/inventory-native-target-read-v1.md`,
  실제 인증 조회 증적은 `docs/backend/dsim/evidence/inventory-v100-native-target-read-20261022.json`이다.
  아래 절의 완료 이벤트·상세 화면 미완료 표기는 당시 이력이며 현재 다음 작업이 아니다.
- 이번 저장소 정리는 InventoryEngine `inventory_engine_dev`와 Platform
  `codex/inventory-psi-strategy-v2`의 관련 코드·계약·테스트·문서만 대상으로 한다.
  개인 보고서·환경·임시 산출물·설치 Wheel·DLL·SQLite 원본은 제외한다.
  개발 DB 재접속/Write·재계산·FSN/운영 Gate 변경·M3/공용 Runtime 배포·MR 생성은 하지 않는다.

**현재 기준선 — 저장소별 선별 커밋과 전체 Offline 회귀 완료**

- Engine Unit·Contract·Offline Integration은 **2,366 passed / 61 skipped / 655 subtests passed**,
  기존 Starlette 경고 1건이다. 총 1,355.51초는 회귀 전체 소요시간이며 업무 계산 성능 지표가 아니다.
  Platform 경로를 명시한 별도 목표 실행·Writer·복구 연결은 **47 passed / 0 skipped**다.
- 최신 develop Rebase 후 Platform Inventory 관련 회귀·Migration 목차 계약은 **968 passed / 28 skipped**,
  실제 설치 r5를 명시한 별도 연결은 **45 passed / 0 skipped**다. CI Backend 테스트 묶음도 **45 passed**다.
  Frontend 전체 **527 passed**, TypeScript와 두 저장소의 후보 Ruff·Format을 통과했다.
- Engine 315개 파일과 Platform 133개 파일을 선별했다. 원문 SQL 086의 기존 EOF 빈 줄 경고는
  Checksum 보존을 위해 유지하며 나머지 후보 Diff Check는 통과했다. 상세 범위·Bash 환경 제약은
  Platform `docs/backend/dsim/inventory-scm-baseline-20261022.md`에 기록했다.
- Platform 새 로컬 커밋 3개만 `fb6f2fd36d77515f9a009c431eb77ce53eafcf17` 위로 Rebase했고
  충돌과 커밋 내용 차이는 없다. 별도 문서 정정 커밋을 포함해 요청된 작업 브랜치에 일반 Push한다.
  기존 공유 이력의 강제 Push·develop 직접 병합·MR 생성은 하지 않는다.

**1. 실행·복구에서 반복되는 전수 재생을 줄인다 — 다음 작업 · InventoryEngine·로컬 Runtime Adapter**

- 같은 호출의 불변 입력만 재사용하고 실제 계산 검증·Build/입력 Binding·결과 Hash·Gate를 유지한다.
  상세 조회 GET은 이미 재계산하지 않으므로 다시 실행 대상으로 잡지 않는다.
  자원 관리 고도화와 공식 강건성 본실험은 계속 보류한다.

**2. 공용 환경 반영을 준비한다 — 승인 필요 · dsai-platform·M3**

- 공유 반영이 필요할 때 정식 Migration 호환성과 Runtime 설치 참조를 정리하고 새 MR을 준비한다.
  로컬 개발 DB에 적용한 SQL 원본은 Platform `infra/development-sql/inventory/`에 별도 보존한다.
  원본 파일명·SQL Checksum과 기존 개발 DB 이력은 유지하며 자동 Migration에 등록하지 않는다.
  MR 생성·공용 배포·운영 게시·실제 발주는 이번 커밋·Push와 별도다.

## 2026-10-22 이전 이력 — 대표 9품목 C# 계산·적재·조회·중복 재시도 완료

- InventoryEngine `inventory_engine_dev`·Platform `codex/inventory-psi-strategy-v2`에서 별도 설치 Wheel r5,
  실제 DLL·TARGET_GUARDED Binding과 Plan 1.4.0·Runtime Manifest를 조립했다. 공용 배포는 없다.
- 실제 288개 LEGACY_STYLE 후보의 계산 결과가 129,075,722바이트여서 종전 전체 128MB 검사에 걸렸다.
  사용자 승인 후 후보별 저장·재생 검증으로 보정했다. 개별 레코드 128MB와 입력·조각·Manifest 원문 20GB 제한은 유지한다.
  [저장 계약](IO_TARGET_PIPELINE_STORAGE.md)을 따르며 기존 레코드·계산 결과 Hash 형식은 바꾸지 않았다.
- 실제 C# 결과는 301개 조각, 최대 1,979,439바이트다. 입력·Manifest 포함 원문은 150,641,003바이트이고
  보정 전 진단과 보정 후 COMPLETE Manifest Hash가 같다. 후보·PSI·선정·행동·주문 내용이 보존됐다.
- 로컬 프로세스 중단 후 Source 재조회 없이 복구해 `RECOMPUTED_VERIFIED`를 확인했다.
  후보 288개·후보 PSI 7,488행, 선정 6품목·무수요 미선정 3품목, 권고 주문 21건이다.
  기존 Python 분석 81개와 C# 분석은 분리하며 Projection은 Receipt 포함 21개 테이블·41,342행이다.
  이후 별도 개발 Run에 같은 21개 테이블·41,342행을 실제 적재했고 전체 행 조회·중복 저장까지 통과했다.
- 같은 호출에서 불변 입력과 검증 Binding만 재사용한다. 후보 재고 상태·PSI 결과를 캐시하지 않는다.
  Engine 관련 302 passed 이후 Platform 경로를 지정한 나머지 26개도 통과했고, 설치 r5 Platform 관련 42 passed다.
  전체 저장소 회귀나 운영 성능 검증 수치는 아니다. Ruff·Format·Diff Check를 통과했다.
- 기존 Run·원본 Artifact·FSN·운영 Gate·활성 Config와 개선형 Python 계산을 유지한다.
  별도 프로세스 독립 조회도 같은 포인터·건수와 실제 C# 재생으로 통과했다.
  승인된 별도 개발 Config·분류·Cycle Revision 4와 새 Run `a791759c-92e5-48b0-a428-f8648ca4eec8` / Attempt 1을 생성했다.
  같은 Claim의 멱등 재시도도 통과했다. 첫 저장 `replayed=false`, 중복 저장 `replayed=true`로 같은 Receipt를 반환했다.
  별도 읽기 전용 SQL에서도 41,342행과 선정 발주 계획의 양수 권고 21행, 자동 게시·발주 true인 행 0건을 확인했다.
- 첫 저장 시 선택 Hash를 `None`과 비교하던 개발 Reader 연결 오류는 결과 INSERT 전에 중단됐다.
  저장된 Run 포인터에서 기대 Hash를 확인하도록 보정했으며 Hash 검사를 생략하지 않는다.
  이후 같은 Run의 봉인 Projection으로 재시도했다. 최초 계산 Pipeline·Config·입력·Claim은 새로 만들지 않았다.
- 두 차례 임시 Role/Role Permission/User Role은 모두 0행으로 회수했고 새 Resolver의 callback=false,
  기존 `engine_report.read`=true를 확인했다. 기존 활성 Config·원래 Run의 행 지문과 원래 결과 건수가 전후 같다.
  새 Run 원장은 아직 **created / pending**, Site running·`effective_run_id=NULL`이다.
  계산 완료 이벤트·증적 VERIFIED·운영 유효 결과 게시나 신규 Run 화면 연결까지 완료한 것은 아니다.
  Migration·Forecast 재실행·기존 Run 재계산·M3 배포·실제 발주·Commit/Push/MR은 없다.
  자원 관리 고도화는 계속 보류한다.

**현재 기준선 — 로컬 C# Runtime과 개발 DB의 계산 결과 저장·조회·재시도 완료**

- 대표 9품목의 후보 생성→Simulation→품목별 선정→발주 계획을 실제 DLL과 개발 PostgreSQL에서 확인했다.
  전체 품목·공유 Runtime·운영 발주 완료로 확대 해석하지 않는다. 아래는 남은 작업이다.

**1. 새 Run의 완료 상태 전달과 결과 화면을 연결한다 — 다음 작업 · dsai-platform Backend/Frontend**

- 기존 완료 API를 재사용해 저장 완료와 계산 완료·증적 검증·운영 검토를 구분한다. 새 계산은 필요 없다.
  실제 완료 이벤트 Write와 공용 배포는 이번 완료 범위에 포함하지 않는다.

**2. 같은 호출의 중복 전수 재계산을 줄인다 — 후속 작업 · InventoryEngine·로컬 Runtime Adapter**

- 이번 Runtime 조립은 같은 결과를 여러 Reader 계층에서 반복 재생했다. 실제 재계산·입력/Build 고정·Gate를 유지하면서
  요청 내부 중복을 제거하는 계약과 회귀 검증을 별도로 진행한다. 자원 관리 고도화·공식 강건성 실험 재개는 아니다.

**3. 관련 코드·문서를 독립 커밋 후보로 정리한다 — 후속 작업 · 두 저장소**

- InventoryEngine `inventory_engine_dev`, dsai-platform `codex/inventory-psi-strategy-v2`의 관련 변경만 정리한다.
  개인 보고서·환경·임시 산출물은 제외하며 Commit/Push/MR은 별도 요청 시 진행한다.

실제 상태와 증적은 Platform `docs/backend/dsim/inventory-v100-csharp-target-development-v1.md`,
`docs/backend/dsim/evidence/inventory-v100-csharp-target-development-20261022.json`에서 관리한다.
현재 로컬 검증·개발 DB 실행에는 MR이 필요 없다. 공유 M3 반영 시 최신 develop 대사와 새 MR·배포 승인이 필요하다.

## 2026-10-22 이전 이력 — V100 대표 품목 개발 입력 준비 완료

- [준비 계약](IO_CATALOG_DEVELOPMENT_INPUT_PREPARATION.md): 9품목·26주(`202438`~`202511`)·234행으로
  새 로컬 개발 Config·원문 보존 분류·Feature·Catalog를 준비했다. ABC·XYZ/LEGACY_STYLE 각 288개,
  넓은 참조 792개다. 품목당 32/32/88개이며 기존 8개 분류와 FSN REVIEW 사례를 포함한다.
- 원 Master·전체 분류 3,622품목·상위 Forecast 참조·FSN·정책 Matrix·운영 Gate를 보존했다.
  새 Config/분류 Binding은 별도 Draft이고 전체 유효 정책 Hash는 기존 `73510cf0…98e27c`와 같다.
  차단 3품목·수요창 종료 3,378품목을 보존하고 나머지 3,610품목은 개발 표본 제외로 명시했다.
- 봉인 합성 수요와 명시한 개발 비용·단가를 사용했으며 운영 실적으로 표시하지 않았다.
  Universe v3 표본 제외는 계산 가능한 품목만 허용하고 v1/v2 Hash·Production 차단을 유지한다.
- Engine 관련 76 passed / 2 subtests, Backend 관련 18 passed, 기존 Starlette 경고 5건이다.
  전체 회귀는 아니며 Ruff/Format·Schema·Diff Check를 확인했다. 네트워크·PSI·목표 계산을 금지한
  실제 입력 준비 재시도에서 원본·출력 지문이 같았다.
- DB 접속/Write·Forecast/PSI/C# 실행·Claim·활성 설정 변경·M3 배포·Commit/Push는 하지 않았다.
  Platform `.tmp/inventory-v100-catalog-preparation-20261022-r2/`가 최종 준비본이다.
- 다음은 설치 Build·실제 DLL Calculation Binding·Plan/Runtime Manifest 조립이다.
  이후 실제 DLL Offline 계산·저장·조회·복구를 검증하며 DB 적재는 별도 승인 대상이다.
  아래 작업 순서는 각각 당시 이력이며 자원 관리 고도화는 계속 보류한다.

## 2026-10-22 이전 이력 — 실제 인증·로컬 화면·기존 V100 결과 조회 확인

- dsai-platform `codex/inventory-psi-strategy-v2`에서 기존 Inventory 결과 화면을 저장·계산·운영 검토로 나눴다.
  실제 BFF·HttpOnly 쿠키·proxy·JWT·프로젝트 RBAC·PostgreSQL Repository·원본/파생/Receipt Reader를 연결했다.
  Actor 주입이나 응답 재생 없이 기존 V100·202512 Run을 조회했다. 전체 Portal/M3 배포 검증은 아니다.
- 일반 로그인의 last_login UPDATE를 피하기 위해 기존 개발 검증의 읽기 전용 인증 조립을 사용했다.
  실제 비밀번호·계정 상태·Tenant 소속과 기존 JWT 발급/검증을 거치며 사용자·Secret·권한은 바꾸지 않았다.
- Run `4f7c3cac-9264-4b34-bf77-0eda92f0b6b2`의 조회·새로고침은 200이며
  저장 완료 / 계산 완료 / 검토 대기, 21개 테이블·588,066행을 표시했다. 증적 pending·유효 Run NULL·
  자동 게시/발주 false는 그대로다. 무인증·로그아웃 후 조회는 401이며 인증 쿠키도 정리했다.
- 시작/종료의 Run·Config·Revision·입력·Site·Run Metadata·사용자 행 지문, 이벤트 6건과 결과 건수,
  원본/파생/Receipt Hash가 같았다. callback 권한은 false이며 새 권한을 부여하지 않았다.
  FSN·운영 Gate·Engine 계산 코드·Build·ID·Hash·DB는 유지하고 재계산·재적재·완료 이벤트 재반영·M3 배포는 하지 않았다.
- 관련 Backend 41 passed, Frontend 462 passed, TypeScript·Ruff·Format·Diff Check 통과다.
  전체 Backend 회귀 수치는 아니며 기존 Starlette 경고 7건·ESLint 미설치 경계는 남는다.
  로컬 두 서버는 종료했다. 자원 관리 고도화는 보류하며 Commit/Push는 하지 않았다.

**1. 다음 독립 계산의 실제 검증 범위를 정한다 — 다음 작업 · InventoryEngine·dsai-platform Backend**

- 이번 수학적 PSI 저장·완료·인증 조회와 C# 목표 계산·전체 후보·품목별 선정·발주 계획 검증을 구분한다.
  기존 입력으로 가능한 범위를 정한 뒤 진행하며 신규 실제 DB Write·운영 게시·공용 배포는 별도 승인 대상이다.

**2. 검증된 변경과 커밋 후보를 정리한다 — 후속 작업 · dsai-platform `codex/inventory-psi-strategy-v2`**

- 관련 코드·테스트·문서를 기존 미커밋 변경과 구분한다. 개인 보고서·환경·임시 산출물은 제외하고 Commit/Push는 별도 요청 시 수행한다.

실제 증적은 Platform `docs/backend/dsim/evidence/inventory-v100-authenticated-ui-readonly-20261022.json`,
계약은 `docs/backend/dsim/inventory-stored-result-completion-v1.md`를 따른다.
아래의 화면 연결·실제 인증 미완료 표기는 이전 단계의 이력이다.

## 2026-10-22 이전 이력 — 기존 V100 Run 계산 완료·조회·중복 재시도 확인

- 사용자 승인 후 로컬 dsai-platform Backend에서 V100·202512 기존 Run
  `4f7c3cac-9264-4b34-bf77-0eda92f0b6b2` / Attempt 1에 계산 완료 이벤트 한 건을 반영했다.
  Run은 `succeeded`다. 최초 응답 `replayed=false`와 중복 응답 `replayed=true` 모두 `event_seq=6`이며 중복 이벤트는 없다.
- 같은 Run 조회는 `STORED`, `completion_event_required=false`, `review_required=true`를 반환했다.
  실제 프로젝트 권한·Repository를 사용한 로컬 ASGI 검증이며 JWT 로그인·공용 서버 검증은 아니다.
- 결과 21개 테이블의 Receipt 포함 588,066행·기존 Receipt·원본/파생/Receipt 전체 Hash와
  Config·Revision·입력·Site·Run Metadata 지문은 보존했다. Engine 계산 코드·설치 Build·ID·Hash·FSN·운영 Gate는 그대로다.
  Forecast/PSI 재계산·결과 재적재·Migration·운영 게시·M3/공용 배포·실제 발주·Commit/Push는 없다.
- 프로젝트 한정 임시 callback 권한은 검증 후 회수했다. 별도 읽기 전용 확인에서도 임시 Role·권한 연결·
  사용자 부여 행은 모두 0건, callback=false·기존 조회 권한=true다. 기본 Role·인증정보는 변경하지 않았다.
- 계산 완료는 운영 게시와 별개다. Evidence `pending`, Site/Cycle `running`, `effective_run_id=NULL`과
  자동 게시·발주 false를 유지했다. Cycle 전체 성공이나 C# 목표 계산·정책 탐색 E2E 완료로 표현하지 않는다.
- 관련 Platform 15개 모듈 **273 passed / 0 skipped**, 기존 Starlette 경고 15건과 새 실행 명령·테스트의
  Ruff·Format·Diff Check 통과다. 전체 저장소 회귀 수치는 아니며 실제 장애 복구는 Offline 검증과 구분한다.
  자원 관리 고도화는 계속 보류한다.

**1. 저장·계산 완료·검토 대기를 결과 화면에 연결한다 — 다음 작업 · dsai-platform Frontend**

- 새 Backend 조회 계약으로 세 상태를 구분한다. M3/공용 배포는 별도 승인 대상이며 자동 게시·발주를 활성화하지 않는다.

**2. 다음 독립 계산의 실제 검증 범위를 정한다 — 후속 작업 · InventoryEngine·dsai-platform Backend**

- 이번 수학적 PSI 저장·완료와 C# 목표 계산·전체 후보·품목별 선정·발주 계획의 실제 검증은 분리한다.
  신규 실제 DB Write는 승인 후 진행하며 Demand→IO 최종 연결·ERP 연계·공용 배포는 후속으로 유지한다.

실제 적용·조회·재시도·권한 회수는 Platform
`docs/backend/dsim/evidence/inventory-v100-calculation-completion-applied-20261022.json`,
상세 계약은 `docs/backend/dsim/inventory-stored-result-completion-v1.md`를 따른다.
아래의 승인 대기·미제출 표기는 이전 단계의 이력이다.

## 2026-10-22 이전 이력 — 계산 완료·저장 결과 조회 연결 구현 / 실제 완료 이벤트 승인 대기

- 실제 V100·202512 Run `4f7c3cac-9264-4b34-bf77-0eda92f0b6b2`을 읽기 전용으로 확인했다.
  Receipt는 저장됐지만 마지막 이벤트는 `artifact_persistence / running`이며 전체 5건이다.
  이전 적재 명령이 계산 완료 이벤트를 보내지 않은 것이 원인이고 비용 입력은 없는 Run이다.
- dsai-platform Backend에서 기존 Receipt·Projection·Bundle·입력 Binding을 검증하는 저장 결과 조회와
  기존 이벤트 트랜잭션을 재사용하는 계산 완료 API를 구현했다. 사용자는 Run·Project만 지정한다.
  새 Table·Column·업무 ID/Hash는 없고 Engine 계산 코드·설치 Build·FSN·운영 Gate도 그대로다.
- 실제 V100은 로컬 ASGI GET에서 200·STORED와 기존 봉인 참조·Receipt 포함 588,066행의 건수를 반환했다.
  Actor는 로컬 주입이며 실제 프로젝트 권한과 Repository를 사용했다. JWT 로그인·공용 서버 검증은 아니다.
  기본 읽기 전용 DB 연결로 Run 지문·이벤트 5건과 원본/파생/Receipt 전체 Hash가 전후 동일함을 확인했다.
- 계산 완료 이벤트는 준비만 했다. 실제 Run `running`, Evidence `pending`, 유효 포인터 NULL,
  callback=false를 유지했다. 실제 완료 트랜잭션은 아직 실행하지 않았고 운영 게시와 구분한다.
  Forecast/PSI 재계산·Migration·M3/공용 배포·실제 발주·Commit/Push는 없다.

**1. 준비된 계산 완료 이벤트를 실제 반영한다 — 승인 필요 · 로컬 Backend·개발 PostgreSQL**

- 기존 Run에 이벤트 한 건만 반영하고 조회·동일 이벤트 재시도를 확인한다.
  필요 시 프로젝트 한정 임시 callback 권한을 부여하고 회수한다. 결과 재적재·재계산·운영 유효 포인터 변경은 하지 않는다.

**2. 결과 화면에서 저장·계산 완료·검토 대기를 구분한다 — 후속 작업 · dsai-platform Frontend**

- 새 Backend 조회 계약을 화면에 연결하는 작업은 미완료다. 공용 배포는 별도 승인 대상이다.
  C# 목표 계산·후보·선정·발주 계획의 다음 실제 독립 검증과 Demand→IO 최종 연결도 완료로 간주하지 않는다.

상세 계약은 Platform `docs/backend/dsim/inventory-stored-result-completion-v1.md`,
실제 증적은 `docs/backend/dsim/evidence/inventory-v100-stored-result-completion-20261022.json`을 따른다.
아래는 2026-10-21 실제 적재 완료 이력이다.

## 2026-10-21 기준선 — V100 실제 적재·같은 Run 조회·중복 재시도 완료

- 사용자 승인 후 로컬 dsai-platform Backend에서 기존 V100·202512 Run
  `4f7c3cac-9264-4b34-bf77-0eda92f0b6b2` / Attempt 1과 새 봉인 참조를 기존 Writer에 연결했다.
  최초 적재는 `replayed=false`, 동일 요청 재시도는 `replayed=true`이며 같은 Receipt를 반환했다.
  저장 결과의 같은 Run 조회도 원본·파생 Artifact와 전체 DB 행 대사를 통과했다.
- 3,619품목·26주, 21개 테이블 대사 결과 Receipt 포함 **588,066행**이다.
  Baseline/Mathematical PSI 각 94,094행·보충 권고 94,094행을 저장했다. 후보·평가·선정 테이블은 0행이다.
  이번 결과는 수학적 PSI 영속화 검증이며 C# 목표 계산·전체 후보·품목별 선정·발주 계획의 실제 E2E 완료가 아니다.
- 프로젝트 `9ed62f41` 한정 임시 callback 권한은 `finally`에서 회수했다. 종료 후 실제 Resolver와 DB를
  별도 읽기 전용으로 확인해 callback=false, 기존 조회 권한=true와 임시 Role/연결/사용자 부여 행 0건을 확인했다.
- 원본·파생 전체 SHA-256과 Config·Revision·입력·Run·Site 행 지문이 전후 동일하다.
  권고의 자동 게시·발주 값은 모두 false이며 FSN·운영 Gate는 유지했다. Forecast/PSI 재계산·Migration·
  M3/공용 Runtime 배포·실제 발주·Commit/Push는 없다. 자원 관리 고도화도 계속 보류한다.
- Run/Site는 `running`, 원장 Evidence는 `pending`, `effective_run_id=NULL`을 유지했다.
  저장·Receipt 검증과 원장 완료·운영 게시를 구분하며 false인 게시 Gate를 우회하지 않는다.
- 관련 Platform **32 passed**, 6개 관련 파일 Ruff·Format 및 Diff Check 통과다. 전체 회귀 수치는 아니다.
  실제 실행 1,007.284초는 Artifact 검증·적재·조회·재시도 전체 시간이며 PSI 계산 성능이 아니다.

**1. 계산 완료 상태와 결과 조회 연결을 정리한다 — 다음 작업 · dsai-platform Backend**

- 이미 저장한 Receipt를 재사용해 계산 완료 Event와 조회 연결을 대사한다. 운영 유효 결과 게시와 분리하고
  FSN·운영 Gate를 완화하거나 유효 결과 포인터를 강제로 바꾸지 않는다.

**2. 독립 IO의 다음 실제 계산 범위를 정한다 — 후속 작업 · InventoryEngine·dsai-platform Backend**

- 수학적 PSI 저장을 기준선으로 C# 목표 계산·후보 평가·품목별 선정·발주 계획의 실제 검증 범위를 정한다.
  신규 실제 DB Write는 승인 후 진행하고, Demand→IO 최종 연결·ERP 연계·공용 배포는 후속으로 유지한다.

실제 포인터·Receipt·건수·보존 지문·권한 회수는 Platform
`docs/backend/dsim/evidence/inventory-v100-repaired-load-read-retry-20261021.json`을 따른다.
아래의 권한 승인 대기·DB 미적재 표기는 이전 단계의 이력이다.

## 2026-10-21 이전 이력 — DB 읽기 전용 대사 완료 / 임시 권한 승인 대기

- 기존 V100·202512 Run의 개발 PostgreSQL 적재·조회·재시도는 사용자 승인됐다.
  대상·Cluster와 091/092 적용 완료를 확인했으며 Migration은 재실행하지 않았다.
- 실제 결과 21개 테이블·Receipt는 모두 0행이다. 원장 `running`/`pending`, 자동 게시·발주 false를 유지한다.
  실제 권한 Resolver에서 `admin`의 프로젝트 조회 권한은 있지만 `inventory_run.callback`은 없었다.
  임시 프로젝트 권한 부여·검증 후 회수 승인을 별도로 요청했으며 아직 데이터나 권한은 변경하지 않았다.
- Platform의 새 적재 명령은 기본 읽기 전용이며 `--apply`와 임시 권한 승인 옵션을 구분한다.
  중간 권한 부여 실패도 회수하는 경로를 Offline으로 확인했다. 관련 **32 passed**, Ruff·Format·Diff Check 통과다.
  원본·파생 전체 Hash·FSN·운영 Gate를 유지했다. Forecast/PSI 재계산·M3 배포·Commit/Push는 없다.

**1. 임시 프로젝트 권한 승인 후 실제 적재·조회·재시도를 진행한다 — 승인 필요 · 로컬 Backend·개발 PostgreSQL**

- 적재 자체의 승인은 완료됐다. 남은 승인은 `admin`에게 프로젝트 `9ed62f41` 범위의 임시
  `inventory_run.callback`을 부여하고 검증 후 회수하는 것이다. 권한을 우회하거나 기본 Role을 변경하지 않는다.

증적은 Platform `docs/backend/dsim/evidence/inventory-v100-repaired-load-preflight-20261021.json`을 따른다.
아래의 DB 미접속은 이전 봉인·Offline 연결 단계의 이력이다.

## 2026-10-21 V100 전체 파생 Projection 봉인·독립 검증 완료 — DB 미접속·미적재

**현재 기준선 — 같은 Run의 전체 저장용 결과 봉인 완료 · InventoryEngine·Platform Backend**

- 기존 Run `4f7c3cac-9264-4b34-bf77-0eda92f0b6b2` / Attempt 1, V100·202512·3,619품목·26주를 유지했다.
  원 계산 설치본은 교체하지 않고 검증된 보정 Wheel만 별도 로컬 경로에 설치했다.
  원 계산 Build `b0da22ce…a1d4`와 Projection Build `0b90759d…f983`를 구분한다. 공용 Runtime 변경은 없다.
- 원본 SQLite는 `mode=ro`와 `query_only`로 읽었다. 원본 1,202개 Artifact와 Outbox·Config·Canonical·Dispatch·
  Source 실행 포인터의 바이트를 보존했으며 원본 저장소 전체 SHA-256도 봉인 전후 동일하다.
  Forecast·PSI 계산 진입점과 네트워크 연결은 호출되면 실패하도록 막았다.
- 전체 행 대사에서 19,251개 권고의 자동 게시·발주 허용값과 해당 행 Hash만 바뀌었다.
  수량·발주일·도착일·정책·PSI·나머지 테이블은 원본과 같다. 권고 94,094행의 두 자동 허용값은 모두 false다.
  Baseline/Mathematical PSI는 각 94,094행을 그대로 재사용한다.
- 전체 1,190개 분할 중 1,001개는 원본 참조를 재사용하고 189개만 새로 봉인했다.
  새 Manifest와 보정 증적을 포함해 별도 저장소에 191개 Artifact·91,914,418바이트를 추가했다.
  조회에는 원본과 파생 저장소가 모두 필요하다. 새 Publication Outbox는 만들지 않았다.
- 새 Projection Manifest Hash는 `db59441b…7b85`, 논리 결과 Hash는 `1bb744fa…f3c6`,
  보정 증적 Hash는 `1cf0990a…83e2`다. 원 Source·원 Manifest 참조를 덮어쓰지 않았다.
  실제 Projection 행은 588,065행이며 미래 DB 적재 시 생성할 Receipt 1행과 구분한다.
- 봉인 후 별도 프로세스에서 두 저장소를 읽기 전용으로 열어 독립 검증을 통과했다. 원본 지문과 전체 건수도 같다.
  봉인은 491.542초, 독립 검증은 180.584초였으며 PSI 계산 속도 측정은 아니다. 설치 패키지 관련 Engine **20 passed**,
  Platform·로컬 저장소 관련 **10 passed**와 Ruff·Format이 통과했다. 전체 저장소 회귀 수치는 아니다.
  초기 로컬 조립의 Outbox 읽기 연결 누락은 보완했으며 실패한 첫 시도에는 파생 Artifact가 추가되지 않았다.
- 이번 작업에서는 PostgreSQL에 접속하지 않았다. 마지막 DB 대사 때의 결과·Receipt 0행과 원장의
  `running`/`pending`을 새로 확인하거나 변경한 것은 아니다. FSN·운영 Gate·ID·원 입력을 유지했다.
  Forecast·PSI 재계산·DB 적재·Migration·공용 배포·실제 발주·Commit/Push는 없다.

**후속 현재 기준선 — 로컬 dsai-platform Backend Writer 연결·Offline 검증 완료**

- Backend `inventory_local_projection_repair.py`가 원본·파생 저장소를 읽기 전용으로 연결하고 원 Outbox만 읽는다.
  참조 충돌·변조·누락과 파일/Hard Link 중복을 거부한다. Writer Receipt는 별도 저장소에만 추가할 수 있다.
  새 Table이나 업무 ID를 만들지 않았으며 Engine 계산 코드·설치 패키지·봉인 결과도 바꾸지 않았다.
- `write_gate_repair_verified`는 권한·Scope·전체 보정 증적을 검증한 뒤 정확한 새 Manifest만 기존 Writer로 넘긴다.
  원 계산 Build를 새 보정 Build로 바꾸거나 잘못된 원 Projection으로 돌아가지 않는다.
- 실제 V100의 새 봉인 참조가 기존 Writer까지 전달됐고 쓰기 스위치 false에서 정상 차단됐다.
  PostgreSQL Pool 접근 0회, 원본·파생 저장소 전체 Hash 동일, 실제 Receipt 추가도 0건이다.
  권한 Adapter는 Offline 대역이며 실제 개발 DB 권한·현재 상태를 조회한 것은 아니다.
- 작은 합성 Fixture와 기록용 SQL Pool에서 저장·같은 Run 조회·멱등 재시도·실패 후 복구를 확인했다.
  관련 Platform **183 passed / 0 skipped**, 기존 Starlette 경고 1건, Ruff·Format·Diff Check 통과다.
  실제 PostgreSQL 제약·Trigger·Lock 검증이나 Engine 전체 회귀 합격 수치는 아니다.
- 맥북 로컬 코드·Service 호출만 검증했다. M3/공용 Runtime 배포·실제 DB 접속/적재·Migration·
  Forecast/기존 V100 PSI 재계산·FSN/운영 Gate 변경·Commit/Push는 하지 않았다.

**1. 같은 Run의 실제 적재·조회·중복 재시도를 진행한다 — 승인 필요 · 로컬 Backend·개발 PostgreSQL**

- 보정된 참조만 사용하고 기존 Migration을 재적용하지 않는다. Forecast·PSI 재계산·Gate 완화·공용 배포·실제 발주는 제외한다.
  C# 목표 계산·정책 탐색·선정·발주 계획의 실제 검증은 후속이며 자원 관리 고도화도 계속 보류한다.

실제 전체 지문과 건수는 Platform `docs/backend/dsim/evidence/inventory-v100-gate-repair-seal-20261021.json`을 따른다.
후속 Backend 연결 결과는 Platform `docs/backend/dsim/evidence/inventory-v100-repair-writer-offline-20261021.json`을 따른다.
아래의 수정 Wheel 미설치·전체 파생 미봉인은 이번 작업 전의 이력이다.

## 2026-10-21 동일 Run 봉인 결과 적재 재개 — 092 보정 완료 / Run Gate 충돌 차단

**현재 기준선 — Forecast·PSI 재계산 없이 기존 결과만 적재 시도 · Platform Backend·개발 PostgreSQL**

- Revision 3 Run `4f7c3cac-9264-4b34-bf77-0eda92f0b6b2`, Attempt 1과 기존 Build·Source 실행 참조를 재사용했다.
  Baseline/Mathematical PSI 각 94,094행·분할 Artifact 1,190개·원본 Bundle/Projection Hash는 그대로다.
  봉인 Manifest의 적재 예정량은 Receipt 포함 21개 테이블 합계 588,066행이다. 실제 적재 완료 건수와 구분한다.
- nullable `approval_level`을 거부하던 CHECK는 사용자 승인 후 후속 Migration 092로 한 개만 보정했다.
  스키마 백업·Rollback 예행·PostgreSQL 9개 사례를 확인했다. 기존 091·원 Catalog와 다른 제약은 보존했다.
- 그 뒤 실제 INSERT는 `IO_EVIDENCE_CANNOT_ESCALATE_CLAIM_GATE`로 중단됐다.
  Run Claim의 자동 게시·발주 허용값은 false인데 봉인된 권고 94,094행 중 19,251행이 true였다.
  Projection 생성기가 품목 Admission·Child Role은 적용하지만 상위 Run Gate를 빠뜨린 오류다.
  DB Trigger는 정상적으로 차단했다. 봉인 행을 수정하거나 Trigger·FSN·운영 Gate를 완화하지 않았다.
- 적재 Transaction은 전부 Rollback됐다. 같은 Run의 결과 21개 테이블·Receipt는 모두 0행이고 임시 callback Role은 0건이다.
  입력 Binding 10건·Config 전체 값/Hash·활성 Revision·승인 분류는 유지됐다. Run/Site `running`, Evidence `pending`,
  `effective_run_id=NULL`도 그대로다. 적재 프로세스는 종료됐으며 실제 DB 결과 조회·중복 재시도는 미완료다.
- 이번 관련 Platform 검증은 **115 passed**, 기존 Starlette 경고 1건이다. Ruff·Format·Diff Check도 통과했다.
  Engine 테스트 207/192는 앞선 계산 검증 이력이며 이번 적재 작업에서 재실행한 수치가 아니다.
  Forecast·PSI 재계산·새 Run/Build/입력 Revision·공용 배포·실제 발주·Commit/Push는 하지 않았다.

**후속 승인 — 파생 Projection 보완·Offline 검증 완료 · InventoryEngine·Platform Backend**

- Gate는 `Run Claim AND 품목/Child 허용`으로 결합하고 자동 발주는 자동 게시도 허용돼야 true다.
  계산 코드·PSI·FSN·원본 분류/입력·운영 Gate와 DB Trigger는 바꾸지 않았다.
- `RepairDsimProjectionGatesUseCase`는 기존 Source 실행·원 Bundle·Outbox·Run/Attempt·입력 Binding을 검증하고
  새 Projection·보정 증적만 Append한다. 계산 Build는 원 Run의 값, Projection Build는 현재 검증할 별도 값으로 고정한다.
  원 Source 실행의 고정 참조·원본 Artifact는 덮어쓰지 않는다. 별도 UUID 없이 기존 Run/Attempt와 Artifact Hash를 사용한다.
- 허용하는 변경은 권고 행의 두 자동 허용값 축소와 해당 행 Hash다. 수량·날짜·정책·다른 테이블의 차이는 거부한다.
  일반 Writer/Reader는 Source 검증을 계속 수행한다. 원본 무결성 읽기만으로 적재 가능하다고 판정하지 않는다.
- Platform의 `prepare_gate_repair`·`verify_gate_repair`는 권한·Scope를 먼저 검사하고 별도 Projection Build가 없으면 차단한다.
  원천 조회·PSI 실행·DB Writer는 호출하지 않는다. 기존 Runtime Factory·HTTP Route를 자동 활성화하지 않았다.
- 합성 Offline Fixture에서 동일 Run의 파생 봉인·독립 검증·SQLite 저장/조회/중복 재시도·원본 바이트 보존을 확인했다.
  이미 Receipt나 부분 행이 있으면 기존 Writer가 교체를 거부한다. 실제 V100은 마지막 DB 대사에서 이 조건을 충족하는 0행이었다.
  테스트는 Source·PSI 실행 진입점과 전이 함수를 호출 시 실패하도록 막는다.
- 실제 V100 권고 94,094행의 읽기 전용 Gate 대사에서 19,251행만 축소되고 수량·날짜·정책 필드가 같음을 확인했다.
  원 계산 Build와 전략·PSI 구현 Hash도 기존 Plan과 일치한다. 이는 전체 파생 봉인·실제 DB 적재 합격이 아니다.
  수정 Wheel 설치·V100 전체 파생 봉인·DB 저장/조회·재시도는 아직 하지 않았다.
- 최종 관련 회귀는 Engine **72 passed**, Platform **119 passed**이며 기존 Starlette 경고 1건이 남아 있다.
  Ruff·Format·Diff Check도 통과했다. 전체 저장소 합격이나 실제 적재 성공 수치는 아니다.

**1. 실제 V100 파생 결과와 설치 후보를 고정한다 — 다음 작업 · InventoryEngine·Platform Backend / DB Write 없음**

- 원 계산 Build와 새 Projection Build를 구분해 설치 후보를 검증한다. 기존 PSI·Bundle에서 전체 파생 Projection·보정 증적을 봉인하고,
  같은 Run과 새 저장 포인터의 연결을 확인한다. 기존 잘못된 원 Projection을 `load_verified`로 다시 적재하지 않는다.

**2. 보정 포인터로 적재·조회·중복 재시도를 검증한다 — 승인 필요 · 개발 PostgreSQL**

- 092·기존 Migration을 재적용하지 않는다. Forecast·PSI 재계산·Gate 완화·공용 배포·실제 발주는 하지 않는다.
  C# 목표 계산·전체 후보·선정·발주 계획의 실제 검증은 그 뒤로 분리한다. 자원 관리 고도화는 계속 보류한다.

상세 증적은 Platform `docs/backend/dsim/evidence/inventory-v100-sealed-load-20261021.json`을 따른다.
아래 적재 보류와 이전 실패 기록은 보존한 과거 이력이며, 현재 상태는 이 절을 우선한다.

## 2026-10-21 정식 Run 분할 계산·원본/파생 Snapshot 보정 — 검증 완료 / DB 적재 보류

**현재 기준선 — 실패 원인 두 건 보완 · InventoryEngine·Platform Backend**

- 최초 Run `efb8ed64-6d16-4092-bea0-06a7519f9c8c`의 관측 크기 제한은 전체 품목을 한 작업으로
  검증한 것이 원인이다. 수학적 Runtime의 품목별 계산을 최대 128품목 단위로 나누고, 기존 관측
  200,000·증적 500,000 상한을 각 작업에 그대로 적용한다. 단일 품목 초과나 다른 입력 오류는 계속 차단한다.
  정책 공식·Guard·후보별 상태는 바꾸지 않는다. 작은 동일 입력의 PSI·주문·관측 Hash·Guard 결과가 같다.
- 분할 경로의 RAW_ACTION Artifact 1.1.0은 반복되는 불변 관측 필드를 봉인된 공통 Source로 참조한다.
  가변 재고·미도착 공급은 행마다 보존하며 복원한 원 관측 Hash와 Guard를 검증한다.
  기존 1.0.0 읽기와 비분할 결과는 유지한다. 저장 표현이 바뀐 Artifact Hash까지 같다고 주장하지 않는다.
- 입력 묶음 1.1.0·Cycle 요청 V2는 새 IO Revision과 원 Demand Planning Input Revision을 분리한다.
  기존 Cycle Revision 구조를 재사용하므로 추가 DDL은 적용하지 않았다. 원 Forecast·공유 Master/Calendar와
  실패 Run·승인 입력은 덮어쓰지 않는다.
- Revision 2 Run `4b4e5b81-99bf-4eb9-9c31-654905ed4ca4`는 PSI 계산·Artifact 저장/검증까지 통과했지만
  `PROJECTION_INPUT_BINDING_MISMATCH`로 실패했다. 행 조립기가 원본 Calendar/Master/Forecast Hash와
  Canonical 변환 Hash를 동일하게 요구한 오류였다. 이 Run의 결과 21개 테이블은 0행이며 임시 권한은 회수했다.
- Projection은 Claim의 원본 ID·Hash와 Canonical의 계산용 ID·Hash를 각각 검증·보존하도록 보완했다.
  `snapshot_manifest.source_metadata.canonical_snapshot`에 계산용 ID·Hash·건수를 저장하고,
  `source_row_count_basis=CANONICAL_SNAPSHOT`으로 건수의 기준을 명시한다. 원본 건수를 추정하거나 0으로 대체하지 않는다.
- 최신 관련 회귀는 Engine **207 passed / 192 subtests passed**, 새 설치 Wheel을 사용한 Platform
  **106 passed**다. 기존 Starlette 경고 1건은 남아 있다. 전체 저장소·C#·정책 탐색 합격 수치는 아니다.

**완료 — Revision 3 계산·봉인·Artifact 검증 / DB 적재는 사용자 요청으로 보류**

- 새 승인 입력 `68c8e529-67a7-44f3-998d-7731cf7d47ad`, Build
  `b0da22cef6ac91b86dd17d686f40c8b7d0c3cdef4e2fcf6bbe163f5ba902a1d4`와 Plan 1.4를 고정했다.
  실제 Run은 `4f7c3cac-9264-4b34-bf77-0eda92f0b6b2`, Attempt 1이다. 동일 Claim은 같은 Run을 반환했다.
- 원문 3,622품목과 차단 3품목을 보존하고 3,619품목·26주를 계산한다. Config 2.1·FSN·운영 Gate는 같다.
  Baseline PSI와 Mathematical PSI 각각 94,094행, 결과 분할 Artifact 1,190개와 Manifest를 봉인·검증했다.
  Source 실행의 최종 검증도 통과해 정확한 저장 참조를 보존했다.
  자동 게시·발주는 false다.
  이번 실행은 기존 수학적 Python 경로이며 C# 목표 계산·정책 탐색·비용/PPO/Stress·Forecast 재실행·공용 배포·ERP 연계는 제외한다.
- 사용자 요청으로 Writer의 반복 검증 중 실행을 중단했다. 결과 INSERT 전이며, 중단 후 읽기 전용 대사에서
  이 Run의 결과 21개 테이블 모두 0행·Receipt 0건·임시 callback Role 0건을 확인했다.
  실제 프로세스는 종료했고 자동 재개하지 않는다. Run/Site 원장의 `running`, Evidence `pending`은 수정하지 않았으며
  `effective_run_id=NULL`이다. 화면의 running 표시는 실제 계산 프로세스가 남았다는 의미가 아니다.
- 입력·계산 결과·정확한 포인터를 프로젝트의 비공개 임시 산출물 경로에 보존했다.
  검증된 Wheel도 함께 복사하고 SHA-256 일치를 확인했다. 실제 DB 저장·같은 Run 결과 조회·Writer 재시도는 미검증이다.

**1. 저장된 같은 Run의 적재·조회·재시도를 진행한다 — 다음 작업, 사용자 재개 요청 후 · Platform Backend·개발 PostgreSQL**

- 보존된 동일 Build·Run·Source 실행 포인터를 기존 Writer에 전달한다. Forecast·PSI를 재계산하거나 새 Revision을 만들지 않는다.
  저장 후 같은 Run의 행·Receipt를 조회하고 재시도가 중복 없이 같은 결과를 반환하는지 확인한다.
  기존 실패 Run과 Artifact를 보존하고 079·091 재적용·Gate 완화·공용 배포·실제 발주는 하지 않는다.

**2. 독립 IO의 다음 실제 검증 범위를 정한다 — 위 결과 확인 후 · InventoryEngine·Platform Backend**

- 이번 수학적 PSI 적재와 아직 실제 검증하지 않은 C# 목표 계산·전체 후보·선정·발주 계획 적재를 구분한다.
  독립 흐름을 확인한 뒤 Demand→IO 한 Plan의 최종 순차 연결로 진행한다. 자원 관리 고도화는 계속 보류한다.
  Commit/Push·공용 배포·실제 발주는 이번 작업에 포함하지 않는다.

실제 재실행의 단계·Hash·최종 건수는 Platform
`docs/backend/dsim/evidence/inventory-v100-partitioned-rerun-20261021.json`에 기록한다.

아래는 수정 전 실패를 포함한 이전 시점의 이력이다.

## 2026-10-21 V100 정식 개발 입력·Claim — 완료 / 계산 분할 연결은 차단

**현재 기준선 — 실제 입력 승인과 Run 생성까지 완료 · InventoryEngine·Platform Backend·개발 PostgreSQL**

- 사용자 추가 승인으로 기존 079 SQL을 백업·Rollback 예행 후 적용했다. 091은 재적용하지 않았다.
  Config 2.1 `2708e79a-82b5-4c82-8261-5a236077bb18`을 게시하고 활성 포인터를 전환했다.
  이전 2.0 Revision과 이력은 보존했으며 `segmentation`·`policy_overlays` 전체 값이 같음을 확인했다.
- Plan `202512`, 직전 마감 `202511`과 26주 마감 Hash에 맞춰 새 분류
  `29aa1570-058a-589a-b330-bbf8be9d57a5`를 승인했다. 원문 3,622품목 중 3,619품목은 계산 가능하다.
  `DS_A017518`·`DS_A082612`·`DS_A082843`은 `MISSING_REVENUE` 차단을 그대로 보존한다.
- 사용자 선택대로 개발 Universe V2에서 계산 3,619품목과 차단 3품목을 명시했다.
  원 Master 7,000품목, 원 Forecast ID·Hash, 전체 분류와 Effective Policy Hash는 유지했다.
  계산용 Forecast만 94,094행으로 파생하고 8종 Source 매핑·5종 IO Snapshot·Plan 1.4 Manifest를 봉인했다.
  재고·입고·정책 등의 개발 합성/Fixture를 운영 실제 Source로 승격하지 않았다.
- 승인 입력 묶음 `22f872cf-54f6-48e5-92d5-6a9542c0771b`와 실제 Inventory Run
  `efb8ed64-6d16-4092-bea0-06a7519f9c8c` / Attempt 1을 생성했다. 동일 Claim 재요청은 같은 Run을 반환했다.
  기존 Canonical의 Site 논리 ID `V100-202512-PRECLAIM`도 재사용했으며 원장 ID를 위조하지 않았다.
- 실제 연결 중 발견한 asyncpg JSONB 문자열 처리와 원 Forecast/계산용 Forecast 참조 불일치를 보완했다.
  V1 기존 Canonical Hash는 이전 설치 Wheel과 동일하다. Source 2.1과 원 정책 Hash를 유지하는
  계산 대상 필터를 추가했지만 PSI 공식·FSN 설정·업무 Gate는 바꾸지 않았다.

**차단됨 — 3,619품목의 정식 Run 계산·DB 결과 저장**

- 설치 Wheel의 입력·권위 조회·Cut-off·분류 검증 후 실제 수학적 PSI 경로를 실행했다.
  `3,619 × 26 × 27 / 2 = 1,270,269` 관측 행이 기존 `200,000` 제한을 넘어
  `RECOMMENDATION_OBSERVATION_LIMIT`로 중단됐다. 제한을 올리거나 추가 품목을 제외하지 않았다.
- 계산 전용 `DevelopmentCalculationExecutor`에는 분할 실행이 있지만 정식
  `RunPsiBundleUseCase → RunMathematicalReplenishmentUseCase`에는 연결되지 않았다.
  기존 분할 저장 구현 완료를 정식 Run 대용량 처리 완료로 보아서는 안 된다.
- 실제 Run·Site는 `failed`, `effective_run_id=NULL`이며 결과 21개 테이블은 이 Run 기준 모두 0행이다.
  DB 결과 조회·저장 재시도 합격은 아직 아니다. Claim 재요청의 멱등성과 구분한다.
- 사용자 승인으로 `admin`에게 프로젝트 `9ed62f41`의 임시 callback 권한만 부여해 실제 RBAC 경로를 통과했다.
  시도 종료 시 전량 회수했으며 임시 Role 잔여 0건을 확인했다. 인증정보·상시 권한은 바꾸지 않았다.
  자동 게시·자동 발주는 false다. Forecast 재실행·C# 목표 계산·정책 후보 탐색·공용 배포·ERP 연계는 하지 않았다.
- 관련 Engine **93 passed / 112 subtests passed**, 설치 Wheel을 포함한 Platform **76 passed**다.
  전체 저장소 합격 수치가 아니며 Platform의 기존 Starlette 경고 1건은 남아 있다.
  실제 DB 실패를 Offline 테스트 통과로 대체하지 않는다. Commit/Push도 하지 않았다.

**1. 기존 분할 계산을 정식 Run 경로에 연결한다 — 다음 작업 · InventoryEngine·Platform Backend**

- 기존 Guard·정책·관측 제한을 유지하고 품목별 분할 계산을 Result Bundle·Projection·저장/복구까지 연결한다.
  기존 계산 전용 구현을 재사용하되 운영 Run으로 위장하지 않는다. 작은 입력에서 단일 실행과 결과·Hash가 같아야 한다.
- 새 설치 Build는 새 Plan/입력 Revision으로 봉인한다. 이번 실패 Run·승인 입력을 덮어쓰거나
  다른 Build를 같은 실행 Binding으로 통과시키지 않는다. 실패 Run은 재계산하지 않는다.

**2. 새 Run으로 계산·적재·같은 Run 조회·재시도를 확인한다 — 분할 연결 후 직렬 · 로컬 Runtime·개발 PostgreSQL**

- 079·091 적용과 이번 Config/분류/입력 승인은 현재 기준선이다. 다시 Migration 대상으로 나열하지 않는다.
  DB 적재를 확인한 뒤 Demand→IO 자동 순차 연결로 진행한다. FSN·운영 Gate·공용 배포·실제 발주 금지는 유지한다.

상세 식별자·Checksum·실패/미적재 상태는 Platform의
`docs/backend/dsim/evidence/inventory-v100-real-claim-20261021.json`을 따른다.
아래는 이전 시점의 이력이다. 당시의 079 제외·Draft·입력 0건 표기는 현재 상태가 아니다.

## 2026-10-21 개발 결과 테이블 적용 — 완료 / 단건 실행은 입력 연결 차단

**현재 기준선 — 개발 PostgreSQL 결과 스키마 준비 완료 · Platform Backend·InventoryEngine**

- Migration 091로 결과 21개 테이블·277개 컬럼과 필수 Domain/제약을 적용했다.
  스키마 백업·목록 확인·Rollback 예행과 동일 Checksum 재호출을 확인했다.
  신규/기존 물리 계약 테스트 **87 passed**(0.62초, Skip 0), Ruff·Format도 통과했다.
- 기존 Config·분류·Run·입력 Binding 지문과 079 CHECK는 그대로다. 079·선택적 분석 확장·공용 배포·실제 발주는 제외했다.
  Engine 계산 코드·업무 ID/Hash·FSN·운영 Gate는 변경하지 않았다. Commit/Push도 하지 않았다.
- 기존 V100/202512 원문은 계산 전용이며 정식 Run이 없다. Config 2.1은 Draft, DB 승인 분류는 202601뿐이고
  승인 입력 묶음은 0건이다. 현재 26주 마감은 봉인됐지만 원본 분류의 과거 마감 Hash와 다르다.
  새 Plan-bound 원문은 현재 076 주차 CHECK에도 맞지 않으며 079는 이번 승인에서 제외됐다.
- 따라서 실제 계산 → DB 저장 → 같은 Run 조회/재시도는 **미실행**이다. 결과 21개 테이블은 모두 0행이다.
  소형 Fixture를 정식 Run으로 등록하거나 기존 원문/승인/Gate를 수정해 통과시키지 않았다.
  상세 적용 증적은 Platform `docs/backend/dsim/inventory-result-evidence-development-application-v1.md`를 따른다.

**1. 단건 실행 입력과 주차 제약의 처리 범위를 정한다 — 승인 필요 · Platform Backend·InventoryEngine·개발 PostgreSQL**

- V100/202512를 사용한다면 079 제외와 새 분류 승인 불가의 충돌을 먼저 해소해야 한다.
  다른 독립 테스트 입력으로 바꾸는 경우도 사용자 선택이 필요하다. 기존 ID/Hash 덮어쓰기는 하지 않는다.

**2. 같은 Run으로 실제 적재·조회·재시도를 확인한다 — 입력 준비 후 · 로컬 Runtime·Platform Backend**

- 적용된 091을 재사용한다. 이후 Demand→IO 최종 연결로 진행한다. 공용 배포·실제 발주는 포함하지 않는다.

아래는 이전 이력이다. 결과 테이블 적용을 다시 미완료로 잡거나 Offline 수치를 실제 DB E2E 합격으로 사용하지 않는다.

## 2026-10-21 독립 실행 → Writer → 같은 Run 조회 — Offline 완료

**현재 기준선 — 계산 결과를 저장 호출까지 연결 · InventoryEngine·Platform Backend**

- [실제 호출 연결 계약](IO_INITIAL_EXECUTION_PROJECTION_CALL.md)에 따라 Platform 최초 실행/목표 결과 Factory에
  선택적 Pool과 쓰기 Switch를 연결했다. 계산/복구가 반환한 기존 Projection 참조·Hash를 Writer에 그대로 전달한다.
  Pool이 없는 기존 Artifact-only 호출은 그대로다. 기본 쓰기 Switch는 false다.
- 같은 Run 조회는 읽기 권한만 사용하고 원본 Artifact·Binding·Receipt·전체 행을 대조한다.
  읽기 전용 Transaction에서는 SELECT만 실행하며, 누락 결과를 INSERT하거나 자동 복구하지 않는다.
- 저장 중 실패는 기존 봉인 결과를 남기고 `recover`로 재시도한다.
  commit 응답 유실·반복 호출은 같은 Receipt·행을 검증하고 재삽입하지 않는다.
- 기존 Engine 계산 코드·업무 ID/Hash·FSN·운영 Gate·DDL은 유지한다.
  DB 증적 저장은 업무 게시/발주 승격이 아니며 자동 게시·자동 발주는 계속 false다.
  실제 DB 접속/적용/Write·공용 배포·Commit/Push는 하지 않았다.
- Engine 관련 **150 passed**(139.15초), Platform 관련 **197 passed**(58.96초), 두 묶음 모두 Skip 0이다.
  신규 최초 계산·저장·조회 테스트 10건과 설치 Wheel Factory 추가 4사례를 포함한다.
  Ruff·Format·양쪽 Diff Check도 통과했다. 수량 Port/SQL은 ReferenceKernel·Recording Pool이며
  실제 V100/PostgreSQL/C# 실행 합격이나 전체 저장소 테스트를 뜻하지 않는다.
  기존 Engine Python 212파일은 설치 Wheel과 바이트가 같고 Catalog·DDL·079·086도 이번 작업 전후 동일하다.

**1. 독립 IO 결과를 개발 DB에 저장하고 같은 Run으로 조회한다 — 승인 필요 · 개발 PostgreSQL·로컬 Runtime·Platform Backend**

- 승인된 결과 테이블 적용 후 기존 개발 입력 한 건으로 계산 → 저장 → 조회·재시도를 확인한다.
  079·선택적 분석 확장·실제 발주를 자동 포함하지 않는다. 별도 적재 후보 정리 단계를 다시 만들지 않는다.

**2. Demand→IO 한 Plan의 최종 연결을 검증한다 — 독립 적재 후 · DemandEngine·InventoryEngine·Platform Backend**

- 정확한 Forecast·Config·분류·입력 Binding을 사용한다. 실제 통합 DB Write는 별도 승인 대상이다.

아래는 이전 검증 이력이다. 실제 호출 연결을 다시 다음 작업으로 잡지 않는다.
자원 관리 고도화·공식 강건성 경로/본실험 보류는 유지한다.

## 2026-10-21 선정 PSI SQL·Writer 정합화 — Offline 완료

**현재 기준선 — 기존 결과를 유지하고 실제 저장 경로의 불일치 보완 · InventoryEngine·Platform Backend**

- [선정 PSI·Writer 계약](IO_SELECTED_PSI_WRITER_ALIGNMENT.md)에 따라 양쪽 Catalog CHECK와
  Platform SQL Trigger를 맞췄다. `POLICY_SELECTION / MATHEMATICAL / EVIDENCE_ONLY`의
  `SELECTED_ORDER_PLAN` PSI만 허용하며 위장 Kind/전략/Marker·잘못된 NULL·실패 Child는 계속 차단한다.
- Platform Writer Factory에 같은 목표 결과 Reader를 명시적으로 주입했다.
  Reader 누락/다른 Build·잘못된 Hash·재봉인 Gate 변조·Scope/권한 실패는 저장하지 않는다.
  기본 쓰기 Switch는 false이며 실제 DB 자동 연결/Runtime 활성화는 하지 않았다.
- 기존 Run/Claim·원본 Bundle·선정/발주 계획·행 데이터/Hash·논리 Projection Hash는 보존했다.
  Catalog 및 이를 봉인한 새 Manifest 지문만 변경되며, 과거 Artifact는 이전 Catalog로 읽고 덮어쓰지 않는다.
  기존 Engine `src` Python 212파일, FSN·운영 Gate, 079·086은 수정하지 않았다.
- Engine 관련 **106 passed**(150.24초), Platform **193 passed**(42.02초), 두 묶음 모두 Skip 0이다.
  실제 설치 Wheel과 캐시 Parser를 사용했으며 SQL/PLpgSQL·생성 SQL·Ruff/Format 검증도 통과했다.
  수량 Port/DB는 ReferenceKernel·Recording Pool이다. 실제 C#/PostgreSQL 실행 합격 수치가 아니다.
  이 단계에서 개발 DB 접속/Canary/적용/Write·공용 배포·Commit/Push는 하지 않았다.

**1. 실제 개발 적재 입력·적용 후보를 고정한다 — 다음 작업 · InventoryEngine·Platform Backend**

- 정확한 승인 Config/분류·입력 Binding·정식 Run·Build·저장 참조·허용 계정과 단건 적재/복구 절차를 정한다.
  기존 분석/비용 테이블은 재사용하고, 신규 결과 21개 테이블·별도 079·선택적 분석 조회 확장을 구분한다.

**2. 독립 적재·조회·복구를 검증한다 — 승인 필요 · 개발 PostgreSQL·로컬 Runtime**

- 승인된 범위만 Backup/Canary 후 적용하고 Hash·건수·누락·원자 Rollback·멱등 복구를 확인한다.
  독립 검증 이후 Demand→IO 최종 연결로 진행한다. FSN·운영 Gate 변경·공용 배포·실제 발주는 포함하지 않는다.

아래는 이전 검증 이력이다. SQL·Reader 불일치 보완을 다시 다음 작업으로 잡지 않는다.
자원 관리 고도화·공식 강건성 경로/본실험 보류는 유지한다.

## 2026-10-21 독립 결과의 실제 적재 범위 — 읽기 전용 대사 완료

**현재 기준선 — Artifact 저장과 개발 DB 적재를 구분 · InventoryEngine·Platform Backend**

- [실제 적재 범위 대사](IO_INDEPENDENT_PROJECTION_DB_RECONCILIATION.md)를 완료했다.
  결과 Projection 21개 테이블·277개 컬럼과 Domain 4개는 개발 DB에 없다.
  기존 분석 테이블 2개·7축 View 2개와 비용 결과 테이블 2개는 재사용하며, 네 테이블의 현재 적재 건수는 모두 0이다.
- 기존 Run/입력/Config/분류의 FK 대상 Key는 존재한다. 074~078·086은 재실행하지 않는다.
  079·방식별 분석/Run 조회 확장은 여전히 미적용이다. 승인 Site 입력 묶음도 0건이다.
- 선정 PSI의 `POLICY_SELECTION / SELECTED_ORDER_PLAN`을 기존 CHECK/Trigger 초안이 거부하고,
  Platform Writer Factory에는 목표 결과 Reader가 빠져 있음을 확인했다.
  실제 같은 봉인 Projection은 기본 Factory 방식에서 `PROJECTION_SOURCE_ROWS_MISMATCH`로 거부된다.
  테이블 적용만으로 독립 실행의 DB 적재가 완성되지 않는다. 이번에는 계산 코드/SQL 초안을 수정하지 않았다.
- 관련 Engine 테스트 **39 passed**(36.66초), Platform **133 passed / 4 skipped**(6.03초)다.
  선택적 Parser 2건·설치 Wheel 2건은 Skip됐다. DB는 명시적 READ ONLY로 조회 후 Rollback/연결 종료했다.
  실제 DDL/INSERT/Canary·FSN·운영 Gate·공용 배포·Commit/Push는 수행/변경하지 않았다.

**1. 선정 PSI의 SQL 규칙과 Writer Reader 조립을 보완한다 — 다음 작업 · InventoryEngine·Platform Backend**

- 기존 Kind/Grain/ID/Hash를 재사용하고, 같은 목표 Artifact가 SQL 규칙과 Platform Writer 검증을 통과하게 한다.
  위장 Kind/Simulation·Gate 상승을 계속 차단하는 Offline 회귀가 완료 조건이다. DB는 변경하지 않는다.

**2. 실제 적재에 사용할 입력·변경 묶음을 확정한다 — 후속 작업 · Platform Backend·개발 PostgreSQL 읽기 영역**

- 정확한 승인 Config/분류/입력·정식 Run·설치 Build·원 Artifact와 허용 계정을 고정한다.
  결과 21개 테이블, 필요 시 079, 선택적 분석 조회 확장은 별도 범위로 정리한다.

**3. 독립 적재·조회·복구를 검증한다 — 승인 필요 · 개발 PostgreSQL·로컬 Inventory Runtime**

- 승인된 DDL만 Backup/Canary 후 적용하고 단건 Hash·건수·누락·전체 Rollback·멱등 복구를 검증한다.
  완료 후 Demand→IO 최종 연결로 진행한다. 공용 배포·실제 발주·ERP 연계는 포함하지 않는다.

아래는 이전 검증 이력이다. 자원 관리 고도화·공식 경로/본실험 보류와 FSN·운영 Gate 유지 조건은 그대로다.

## 2026-10-21 독립 IO 최초 실행 연결 — Offline 완료

**현재 기준선 — 봉인 입력부터 계산·저장·조회까지 한 호출 · InventoryEngine·Platform Backend**

- [독립 최초 실행 계약](IO_INDEPENDENT_INITIAL_EXECUTION_CONTRACT.md)의 Engine/Backend 조립을 구현했다.
  결과가 없는 상태에서 시작해 기존 Python Bundle·Outbox, C# 목표 후보·선정·발주 계획,
  연결 Artifact·Projection을 생성·검증해 반환한다. 기존 Runtime Registry/공용 Route는 자동 변경하지 않는다.
- 계산 전 원본 Manifest와 실행 Binding을 복구용 Artifact에 저장한다.
  기존 Snapshot/Run/정책/결과 ID·Hash를 재사용하며 문서/바이트 체크섬만 추가한다.
  같은 Run에 다른 Attempt·Claim·입력을 넣거나 Checkpoint를 재봉인해도 차단한다.
- 기존 운영 Gate를 자동 분석보다 먼저 검사한다. BLOCK은 계산으로 넘기지 않고 REVIEW는 계산/증적 저장만 허용한다.
  원본 Python 계산과 Native 수량 전이는 수정하지 않았다. 기본 Child·FSN·운영 Gate·DB 스키마도 유지한다.
- 중단 후 Provider가 없어도 저장 Manifest로 원본 계산과 목표 계산을 이어서 조립한다.
  미완료 목표 후보는 동일 입력·DLL로 전체 재계산하고, 이미 봉인된 Bundle/Outbox는 재사용한다.
  계산 루프 중간 상태를 이어받거나 최신 Source로 대체하는 방식은 아니다.
- 실제 C# DLL로 32·32·88개 후보의 최초 실행과 5개 저장 지점 SIGKILL을 포함한 8사례를 검증했다.
  Checkpoint/Bundle/목표 조각/연결/Projection 저장 후 새 프로세스에서 원천 조회 없이 복구했다.
  격리 설치 Wheel을 포함한 Backend 관련 테스트는 **82 passed**(17.06초), 기존 경고 4건이다.
  신규 Engine/Platform 연동 테스트 26건을 포함한 관련 회귀는 **1,028 passed / 210 subtests passed**(312.69초)다.
  이 테스트 묶음에는 Skip이 없다. Python 425파일 Ruff·Format, 기존 Schema JSON 42개와 Diff Check도 통과했다.
  저장소 전체 테스트·Coverage·성능 합격을 뜻하지 않는다.
  Backend 테스트의 참조 계산 Port와 별도 실제 DLL 검증을 실제 Source 통합 E2E로 합쳐 주장하지 않는다.
- 상세 회귀·파일 지문은 [검증 증적](evidence/independent-initial-execution-verification-20261021.json)을 따른다.
  개발 PostgreSQL 접속/변경·Migration·실제 V100·공용 배포·실제 발주·Commit/Push는 수행하지 않았다.
  SQLite는 임시 Artifact 저장기다. 자원 관리 고도화·공식 강건성 경로/본실험은 계속 보류한다.

**1. 독립 결과의 실제 적재 범위 대사 — 다음 작업 · InventoryEngine·Platform Backend·개발 PostgreSQL 읽기 영역**

- 기존 테이블·권한·미적용 DDL과 결과 Projection의 컬럼/Grain을 대조한다.
  재사용 대상과 필요한 변경·승인 범위를 구분한다. 이 단계는 읽기 전용이며 DB를 변경하지 않는다.
  실제 결과 계약과 DB 구조에 의존하므로 현재 세션에서 직렬 진행한다.

**2. 독립 IO 실제 적재·조회·복구 — 승인 필요 · 개발 PostgreSQL·로컬 Runtime**

- 승인받은 범위에서 Hash·건수·누락·Rollback·멱등 복구를 확인한다.
  FSN·운영 Gate를 유지하며 공용 배포나 실제 발주를 포함하지 않는다.

**3. Demand→IO 최종 연결 — 독립 검증 후 · DemandEngine·InventoryEngine·Platform Backend**

- 한 Plan의 정확한 Forecast·Config·분류·8종 입력·Build로 검증한다.
  Forecast 재실행/최신 Source 탐색으로 연결 차단을 우회하지 않는다. 실제 E2E Write는 별도 승인 대상이다.

Commit/Push는 별도 요청 후 InventoryEngine `inventory_engine_dev`, Platform `codex/inventory-psi-strategy-v2`에서
관련 변경만 진행한다. 개인 보고서·환경·임시 산출물은 제외한다. 아래는 과거 이력이며 최초 실행을 다시 다음 작업으로 잡지 않는다.

## 2026-10-21 로컬 Runtime·Platform 목표 결과 조회 조립 — Offline 완료

**현재 기준선 — 정확한 저장 참조와 원본 Bundle을 함께 조회 · InventoryEngine·dsai-platform Backend**

- [로컬 Runtime 계약](IO_LOCAL_TARGET_RESULT_RUNTIME_CONTRACT.md)의 명시적 실행/조회/복구 Service를 구현했다.
  원본 Bundle Reference·Hash, INPUT/COMPLETE 참조, 연결 Artifact와 Projection 포인터를 검증한다.
  반환 포인터는 기존 참조를 묶을 뿐 새 업무 ID·부모 Hash·Wrapper를 만들지 않는다.
- 설치 Build와 Claim 구현 Hash를 대조하고 다른 Scope·Run·Attempt·Build·Hash·누락 결과를 차단한다.
  Backend는 Tenant/Project·Run 권한을 먼저 검사한다. 조회 권한과 저장/복구 권한은 구분한다.
- 조회는 저장 원본에서 전체 목표 PSI를 재계산하며 commit/원천 조회를 호출하지 않는다.
  완료 결과의 연결/Projection 저장 응답 유실을 멱등 복구한다. 미완료 목표 계산을 자동 탐색·재계산하지 않는다.
- 신규 Engine Runtime 20건과 Platform 연동 10건, 격리 설치 Wheel을 포함한 Backend 관련 81건을 통과했다.
  Engine 관련 회귀는 **1,002 passed / 210 subtests passed**(260.99초), Backend는 **81 passed**(16.25초)다.
  선택적 Platform 연동을 활성화했으며 이 묶음에는 Skip이 없다. 저장소 전체 테스트 결과를 뜻하지 않는다.
  Backend의 기존 HTTP 422 Alias 경고 4건은 유지했다. 실제 C# DLL의 32·32·88개 후보와
  두 저장 지점 SIGKILL 복구를 포함한 5사례도 새 프로세스에서 확인했다.
  실제 DLL 검증과 Backend 참조 대역 검증을 실제 Source E2E로 합쳐 주장하지 않는다.
- 기존 Python 계산·분류 결과·FSN·운영 Gate·Schema/DDL·DLL·원본 Bundle/Outbox는 유지한다.
  HTTP 서버 기동·공용 Route/Runtime 등록·개발 DB 접속/적재·Migration·실제 발주·Commit/Push는 하지 않았다.
  테스트의 SQLite는 임시 Artifact 저장기다. 상세 회귀·파일 지문은
  [검증 증적](evidence/local-target-runtime-verification-20261021.json)에 기록한다.

**1. 독립 IO 최초 계산·저장·조회 연결 — 다음 작업 · InventoryEngine·Platform Backend**

- 이번 조립은 완료된 목표 결과를 Reader에 전달한다. 최초 봉인 입력에서 목표 계산 Pipeline·저장기를
  호출하고 정확한 포인터를 이 서비스에 넘기는 경로는 후속으로 연결한다.
  작은 독립 입력에서 계산·조회·차단·복구를 먼저 검증한다. 같은 저장 계약에 의존하므로 현재 세션에서 직렬 진행한다.

**2. 독립 결과의 실제 DB 적재 검증 — 범위 대사 후 승인 필요 · 개발 PostgreSQL·로컬 Runtime**

- 기존 테이블·권한·미적용 DDL을 읽기 전용으로 대사해 재사용/추가/승인 대상을 구분한다.
  실제 Write는 별도 승인 후 Hash·건수·누락·Rollback·멱등 복구를 확인한다. 공용 배포는 포함하지 않는다.

**3. Demand→IO 최종 연결 — 독립 검증 이후 · DemandEngine·InventoryEngine·Platform Backend**

- 한 Plan의 정확한 Forecast·Config·분류·8종 입력·Build를 연결한다.
  기존 Forecast를 재실행하거나 운영 Gate를 완화하는 것으로 연결 차단을 해결하지 않는다.
  실제 E2E Write·공용 배포·발주 확정·ERP 연계는 별도 승인/후속이다.

검증된 변경의 Commit·Push는 별도 요청 후 InventoryEngine `inventory_engine_dev`,
dsai-platform `codex/inventory-psi-strategy-v2`에서 수행한다. 개인 보고서·환경·임시 산출물은 제외한다.
아래는 기존 이력이며, 로컬 Reader 조립을 다시 다음 작업으로 잡지 않는다.

## 2026-10-21 저장 참조의 Result Bundle·Projection 연결 — 독립 Offline 검증 완료

**현재 기준선 — 원본 결과를 유지한 후보·선정·발주 계획 근거 연결 · InventoryEngine**

- [결과 연결 계약](IO_TARGET_RESULT_LINK_CONTRACT.md)의 개발용 연결 Artifact와 Reader를 구현했다.
  정확한 Run·Attempt·Scope·Config·분류 Header·수학 입력·INPUT/Manifest 참조와 동일 DLL 재계산을 확인한다.
  원본 Bundle/Outbox는 재봉인하지 않으며 기존 ID·결과 Hash·유효 Child·Gate를 유지한다.
- 기존 `POLICY_SEARCH/POLICY_SELECTION` Registry와 테이블 Grain을 재사용한다.
  선정 계획은 동일 선정 Child의 `SELECTED_ORDER_PLAN` Simulation이며 자동 게시/발주가 차단된 Evidence다.
  Catalog 결과를 기존 PSR 검증기로 우회시키거나 새로운 ORDER_PLAN 종류/테이블을 추가하지 않았다.
- 기존 분석은 동일 Hash이면 중복을 막고, 다른 Catalog이면 별도 근거를 보존한다.
  선택 의존성을 생략한 기존 Projector는 내용/Hash가 같으며, 새 경로는 기존 Writer/Verifier로 저장·대사한다.
- 실제 C#에서 32·32·88개 후보와 2건씩의 계산용 주문을 연결하고 새 프로세스에서 재검증했다.
  연결/Projection 저장 후 `SIGKILL`한 2개도 복구했다. 5개 실제 DLL 확인은 Unit 대역과 구분한다.
- 신규 Unit·Offline Integration **39 passed**(38.78초)다. 임시 SQLite는 Artifact/트랜잭션 모형이고 개발 DB가 아니다.
  기존 계산·계약·저장·Bundle·Projection을 포함한 관련 회귀는 **965 passed / 7 skipped / 210 subtests passed**(203.03초)다.
  선택적 Platform Source 연동 7개는 `DSAI_PLATFORM_ROOT` 미지정으로 제외했다. Python 415파일 Ruff·Format,
  `git diff --check`와 기존 Schema JSON 42개 파싱을 확인했다.
  Runtime Registry·Backend API/화면·실제 V100·PostgreSQL/공용 배포 검증은 아직 아니다.
  상세 검증 결과와 파일 지문은 [검증 증적](evidence/target-result-link-verification-20261021.json)을 따른다.
- 기존 Python 계산·DLL·계약/Schema·ABC/XYZ·FSN·운영 Gate·개발 DB·Platform은 미변경이다.
  BLOCK이 기존 운영 Bundle 생성을 차단하는 경우는 그대로 차단하며 가짜 Bundle을 만들지 않는다.
  DB 접속·Migration·실제 적재/발주·배포·Commit/Push는 하지 않았다.

**1. 로컬 Runtime·Platform 조회 조립 — 다음 작업 · InventoryEngine·dsai-platform Backend**

- 새 Reader와 정확한 저장 참조·Bundle 포인터를 명시적으로 주입하고 Offline 호출·조회·차단·복구를 확인한다.
  운영 Mathematical Child 교체나 최신 Source 탐색 없이 연결한다. 공통 Run/결과 계약에 의존하므로 직렬로 진행한다.

**2. 실제 개발 입력·DB 연결 검증 — 이후 / 실제 Write·배포 승인 필요**

- 실제 입력 차단/승인 상태를 대사한 뒤 정확한 Demand Snapshot·Run·입력·결과의 연결을 확인한다.
  실제 DB Write·Migration·공용 Runtime 배포·실제 E2E Write는 별도 승인 대상이다.
  발주 확정·ERP 연계·자원 관리 추가 고도화·공식 강건성 본실험은 계속 후속/보류 범위다.

아래는 과거 이력이다. 독립 결과 계약 대사를 다시 다음 작업으로 잡지 않으며 최신 순서는 이 최상단 절을 따른다.

## 2026-10-21 계산 Binding·전체 후보·선정·발주 계획의 저장·조회·중단 복구 — Offline 완료

**현재 기준선 — 동일 입력·Profile·DLL의 재계산 검증과 로컬 영속 보존 · InventoryEngine**

- [저장·복구 계약](IO_TARGET_PIPELINE_STORAGE.md)의 개발용 진입점을 기존 목표 Pipeline에 연결했다.
  기존 POSIX 저장기의 배타 게시·`fsync`·파일 크기 제한·경로 검증을 재사용하며 기존 저장 형식/Reader는 수정하지 않았다.
- 원본 입력/Config/분류/Catalog와 계산 Binding을 먼저 고정한 뒤 전체 후보·선정·발주 계획 조각→Manifest→완료 포인터 순으로 보존한다.
  신규 업무 ID나 부모 결과 Hash는 없으며 기존 결과의 원문·Hash를 유지한다. 레코드 체크섬은 저장 무결성용이다.
- `INTEGRITY_VERIFIED` 조회는 수량 전이를 호출하지 않는다. `RECOMPUTED_VERIFIED`와 중단 복구는 저장 입력과 동일 DLL로
  후보·선정·발주 계획 전체를 재계산한다. 후보 중간의 수량 상태를 이어서 계산하는 기능은 아니다.
- 실제 C# DLL에서 ABC·XYZ 32개·LEGACY_STYLE 32개·참조 88개 후보의 저장·새 프로세스 재검증을 확인했다.
  입력/결과 조각/Manifest/완료 포인터 직후 `SIGKILL`한 4개도 새 프로세스에서 복구했고 게시된 원문은 그대로였다.
  이 7항목은 Unit 대역 검증과 구분한다. 신규 Unit·Offline Integration **39 passed**(21.73초)다.
- 기존 계산·계약·저장·복구를 포함한 관련 회귀 **838 passed / 158 subtests passed**(152.77초),
  Python 409파일 Ruff·Format·`git diff --check`와 기존 Schema JSON 42개 파싱을 확인했다.
- 기존 Python 계산·수량 DLL·계약/Schema·ABC/XYZ·FSN·운영 Gate·DB·Platform·공용 Runtime은 미변경이다.
  DB 접근·DB Write·Migration·발주·배포·Commit/Push는 없으며 전체 저장소 회귀/성능/실제 V100 검증을 뜻하지 않는다.
  파일 지문과 관련 회귀 결과는 [검증 증적](evidence/target-pipeline-storage-verification-20261021.json)을 따른다.

**1. 독립 IO 저장 결과를 기존 업무 결과 계약과 대사 — 다음 작업 · InventoryEngine**

- 계산 Binding·후보·선정·발주 계획의 저장 참조를 기존 Result Bundle/Projection 계약과 연결할 필드를 확인하고 Offline 검증한다.
  계산·저장을 다시 구현하지 않는다. 공통 결과 계약에 의존하므로 현재 세션에서 직렬로 이어갈 작업이다.

**2. Platform·Demand 실행 연결 — 독립 IO 대사 이후 · InventoryEngine·dsai-platform Backend**

- 정확한 Forecast Snapshot·승인 입력과 기존 Run/결과 포인터를 연결한다.
  실제 DB Write·Migration·공용 Runtime 배포·실제 E2E Write는 승인 필요다.
  발주요청 확정·ERP 전송, 자원 관리 추가 고도화와 공식 강건성 본실험은 계속 후속/보류 상태다.

아래는 과거 이력이다. 저장·조회·중단 복구를 다시 다음 작업으로 잡지 않으며 최신 순서는 이 최상단 절을 따른다.

## 2026-10-21 목표 전략·Guard·C# 전이 연결 — Offline 완료 / 저장·Runtime 미연결

**현재 기준선 — 후보 평가·선정·발주 계획의 같은 입력·Profile·수치 Build 실행 · InventoryEngine**

- [실행 연결 보고서](IO_TARGET_GUARDED_EXECUTION.md)에 전략 제안→기존 Guard→허용 공급 등록→C# 목표 전이를 정리했다.
  기존 계산을 복제하지 않고 공통 실행부 8파일에 선택적 수치 전이 주입 지점만 추가했다.
  기본값은 기존 Python이며 계산식·정책식·Guard·FSN·분류·기존 ID/Hash 규칙은 유지했다.
- 기존 실제 Mathematical 전략, ABC 기본 후보, ABC·XYZ 32개·LEGACY_STYLE 32개·참조 88개 후보를 연결했다.
  후보 평가·선정 검증·선정 정책 발주 계획 재실행이 같은 C# DLL을 사용하며 기존 Python의 전체 결과/Hash와 일치했다.
- 실제 C# 확인은 26항목·18 Pipeline·전이 13,986회다. 재계산 검증 호출을 포함하며 고유 PSI Row나 성능 수치가 아니다.
  기존 발주 계획 Golden 8개, FSN REVIEW/BLOCK V1/V2, SDE 물리 LT와 보호기간, Guard 조정과 다음 관측을 확인했다.
- 신규 Unit **28 passed**, 이를 포함한 관련 회귀 **736 passed / 158 subtests passed**(31.14초), Python 404파일 Ruff·Format을 확인했다.
  전체 저장소 회귀·새 성능 측정·모델 평가·실제 V100 실행 결과는 아니다.
- 새 반환 객체는 기존 계산 Binding과 중첩된 검색/선정/발주 계획을 가진 로컬 View다. 새로운 Run/Snapshot ID나 묶음 Hash는 만들지 않았다.
  Runtime Registry·기존 Run Plan·Result Bundle·DB·공용 서버는 연결하지 않았다. [검증 증적](evidence/target-guarded-execution-verification-20261021.json)에 보존 범위를 남긴다.

**1. 계산 결과 저장·조회·중단 복구 — 다음 작업 · InventoryEngine**

- 기존 Writer/Reader/Verifier와 ID·Hash를 재사용하며 바깥 계산 Binding과 전체 후보·선정·발주 계획을 함께 보존한다.
  누락·멱등·복구·동일 Profile/Build를 Offline으로 검증한다. 공통 저장 계약에 의존하는 직렬 작업이다.

**2. 독립 IO 검증 후 Platform·Demand 연결 — 저장 검증 이후**

- 독립 IO 검증을 마친 뒤 정확한 Demand Snapshot과 Platform Run을 연결한다.
  실제 DB Write·Migration·공용 배포·E2E Write는 별도 승인 대상이며 실제 발주 확정·ERP 전송은 후속이다.
  자원 관리 추가 고도화 보류도 유지한다. 이번에 DB 접근·DB Write·배포·Commit/Push는 하지 않았다.

아래는 과거 이력이다. 전략·Guard 실행 연결을 다시 다음 작업으로 잡지 않으며 최신 순서는 이 최상단 절을 따른다.

## 2026-10-21 정식 계산 DLL·Canonical Adapter — Offline 구현 완료 / Runtime 미연결

**현재 기준선 — 범용 Legacy 입출력과 별도 목표 Baseline · InventoryEngine**

- [구현 보고서](IO_CANONICAL_DLL_ADAPTER.md)에 새 `Inventory.Calculation.dll`과 Canonical Adapter의 지원 범위·제한을 정리했다.
  원본 지문을 확인한 계산문·Lot·LT·기간 처리는 유지하고 16품목·32후보·52주·고정 연도 실험 가정을 제거했다.
- 실제 C# 캡처 27사례·81행이 일치했다. Canonical Legacy 투영 1,638행, 목표 Baseline 72행,
  별도 목표 전이 125입력, 1/53주 가변 기간과 15개 오류/취소 거부를 실제 DLL로 검증했다.
  V1/V2/V2.1·Reserved/BO·분수 수요·복수 입고/미확인 공급 제외를 확인했고 목표 Baseline의 기존 결과 Hash도 같다.
- 원문 Lot/MOS/정책 기간·원문 LT는 기존 Reference/Hash로 봉인한 별도 인자를 요구한다.
  누락·기간 공백·정밀도 초과는 실패한다. 목표는 소수 6자리·정수부 34자리 이하의 정확 전이를 지원하며 중간 합계 초과도 실패한다.
- 신규 단위 검증 **50 passed**, 이를 포함한 관련 회귀 **708 passed / 158 subtests passed**(27.38초)다.
  실제 C# 실행은 격리된 로컬 이미지에서 별도 수행했다. 전체 저장소 회귀·새 성능 측정·운영 실적 검증 수치는 아니다.
- 기존 개선형/재현 Python·실험 DLL/Adapter·Canonical/Run 계약·분류·FSN·운영 Gate·과거 Golden/Hash·DB·Platform·공용 Runtime은 미변경이다.
  실제 목표 Recommended·후보 선정/발주 계획 연결·저장/복구·운영 Source Producer는 아직 연결하지 않았다.
  Migration·DB Write·발주·배포·Commit/Push도 없다. [검증 증적](evidence/canonical-dll-adapter-verification-20261021.json)에 기록한다.

**1. 목표 보충 전략·공통 Guard 실행 연결 — 다음 작업 · InventoryEngine**

- 기존 전략의 제안→공통 Guard→허용 행동→공급 Ledger→C# 목표 전이를 연결한다.
  후보 목록·선정 정책·발주 계획을 동일 입력·Profile·Build로 검증하고 다음 관측도 허용 행동을 사용하게 한다.
  이 실행 연결은 공통 상태·계약에 의존하므로 현재 세션에서 직렬로 진행한다.

**2. 저장·조회·복구와 독립 IO 검증 — 1번 이후 · InventoryEngine**

- 기존 Writer/Reader/Verifier와 ID·Hash를 재사용한다. 독립 검증 후 Platform/Demand 연결 테스트로 넘어간다.
  실제 DB Write·Migration·공용 배포는 별도 승인 대상이며 자원 관리 추가 고도화 보류도 유지한다.

아래는 과거 이력이다. 정식 DLL·Canonical 변환 준비를 다시 실행 대상으로 삼지 않으며 최신 다음 작업은 이 최상단 순서를 따른다.

## 2026-10-21 Python.NET 계산 역할·입출력·Guard 경계 — 계약 완료 / 변환·실행 연결 없음

**현재 기준선 — Python.NET 채택 확정·선언 전용 계약과 Source 대응 · InventoryEngine**

- [계산 경계 계약](IO_CALCULATION_BOUNDARY_CONTRACT.md)에 Python/C#의 책임, Canonical 입력 대응,
  `LEGACY_REPLAY/TARGET_GUARDED`, 발주·공통 Guard·PSI 순서를 고정했다. 호출 방식 재선정은 다음 작업이 아니다.
- 신규 [CalculationBoundaryBinding](../../src/dsio_inventory_engine/inventory_contracts/calculation_boundary.py)과
  [Schema](../../schemas/inventory_calculation_boundary_binding.schema.json)는 `CALCULATION_ONLY` 선언만 받는다.
  기존 Canonical/Prepared/Build·8종 Snapshot·전략 입력·Replenishment Config 참조를 재사용하며 실행별 ID/Hash를 추가하지 않는다.
- Canonical은 1.0/2.0/2.1을 대사했다. 원문 단일 Demand/Forecast BO, BaseMonth 기간, LT 단위,
  Lot 기준점·물리 상한, 원문 ExpMOS·목표 서비스 지표를 동일한 것으로 취급하지 않는다.
  Legacy 인자 근거가 없으면 0이나 임의 정책으로 대체하지 않는다.
- 현 C# 전체 기간 커널은 Guard 개입점이 없다. Legacy 결과의 주문표만 사후 수정해 Target PSI로 만드는 것은 금지한다.
  Target은 검증된 행동만 공급/재고 상태에 반영한 뒤 다음 판단을 진행해야 한다. Baseline은 신규 발주가 없는 별도 규칙이다.
- **신규 계약 검증 81 passed**, 관련 Unit·Contract·Offline Integration **610 passed / 102 subtests passed**(27.10초),
  Python 394파일 Ruff·Format·`git diff --check`를 확인했다. 전체 저장소 회귀·새 C# 실행/성능 재측정 수치는 아니다.
  추가 Baseline·Canonical V2·CLI·Context 검증은 별도 **48 passed / 56 subtests passed**(1.00초)다.
- 새 계약의 구조 통과는 Source 승인·Prepared 투영 확인·DLL 지원 능력·Runtime 입장을 증명하지 않는다.
  기존 `validate_prepared_projection`, FSN·공통 Guard·Run/모델/게시 Gate는 유지한다.
- 기존 개선형/재현 Python 계산·기존 계약/Schema·원문 C#·실험 DLL/Adapter·기존 Golden/Artifact Hash·DB·Platform·Runtime은 미변경이다.
  Source Producer·실제 변환기·Target C# 전이·Run 등록·저장 연결은 아직 없고 Migration·DB Write·발주·배포·Commit/Push도 없다.
  [검증·보존 기록](evidence/calculation-boundary-contract-verification-20261021.json)에 확인 범위를 남긴다.

**1. 정식 DLL·Canonical Adapter 확장 — 다음 작업 · InventoryEngine**

- 실제 Source·원문 인자 Coverage를 대사하고, 원문 재현과 목표 전이/외부 행동 인터페이스를 구분해 입력·출력을 구현한다.
  16품목·32후보·52주 고정 실험 형식을 실제 후보 목록과 Calendar에 맞게 확장한다. 누락/비지원 값은 명시적으로 실패한다.
- 계약 이후 C# 라이브러리와 Python Adapter 준비는 병렬 가능하며 실제 동등성 검증은 직렬로 합친다.

**2. 기존 Guard·후보 평가·선정 정책의 발주 계획 연결 — 1번 이후 · InventoryEngine**

- 기존 Python 전략·Guard를 재사용하고 허용 수량·납기가 후속 PSI·다음 제안에 반영되는지 확인한다.
  후보 Simulation과 선정/발주 계획의 Profile·Build를 일치시킨다. 기존 구현을 다시 만드는 작업은 하지 않는다.

**3. 독립 IO 저장·조회·복구 — 2번 이후 · InventoryEngine**

- 기존 Writer/Reader/Verifier에 연결해 중단·Hash·건수·재계산을 검증한 후 Platform/Demand 연결로 넘어간다.
  실제 DB Write·Migration·공용 Runtime 배포는 별도 승인 대상이다. 자원 관리 추가 고도화 보류도 유지한다.

아래는 과거 이력이다. Python.NET 채택 여부와 계산 경계는 이제 확정됐으며 실제 구현은 이 최상단 순서를 따른다.

## 2026-10-21 C# 계산 DLL·Python.NET 소규모 검증 — 완료 / 운영 연결 없음

**현재 기준선 — 별도 진단 경로의 결과·속도·메모리·중단 확인 · InventoryEngine Offline**

- [실험 보고서](IO_LEGACY_PYTHONNET_EXPERIMENT.md)에 구현·동결 조건·실측·지원하지 않는 범위를 정리했다.
  원본 C# 계산문을 바꾸지 않은 DB 없는 DLL과 명시적 Python.NET Adapter를 추가했다.
  기존 개선형 Python·별도 재현 Python·ABC/XYZ 결과·FSN·운영 Gate·DB·공용 Runtime은 유지했다.
- 동일 입력의 1/16품목·32후보·52주를 비교했다. 조건별 새 프로세스 3회 × 본측정 7회다.
  최초·Warm-up 포함 **198파일·2,800,512행**의 Header·Hash·전체 바이트 불일치 0건이며 이전 출력 Hash도 그대로다.
- 16품목 입력 준비·변환·결과 Hash 포함 호출 중앙값은 C# 직접 **7.858ms**, Python.NET **9.918ms**, Python **167.237ms**다.
  Python.NET은 이 반복 호출에서 Python보다 **16.86배** 빨랐고 직접 C#보다 **1.26배** 오래 걸렸다.
  이전 준비 제외 측정 수치와 혼합하지 않는다. 순수 경계 호출 비용이나 모든 입력의 성능으로 일반화하지 않는다.
- 프로세스 최대 RSS 중앙값은 각각 **75.89 / 119.97 / 36.83MiB**다. Python.NET Adapter 생성 약 **144ms**는 별도다.
  짧은 호출마다 CLR을 새로 시작하면 이득이 줄 수 있어 Worker 재사용을 후속 검토한다.
- 이미 취소된 Token·100ms Timer 취소·SIGTERM·150ms 강제 시간 초과·SIGKILL을 확인했다.
  호출 간 상태·취소 후 계산·세 중단 후 새 Worker 결과 Hash는 같았다. Checkpoint 재개나 실제 게시 복구는 미검증이다.
- 신규 **25 passed**, 관련 대상 회귀 **489 passed / 51 subtests passed**(27.11초), Python 392파일 Ruff·Format을 확인했다.
  전체 회귀·장기 부하·독립 검토·Coverage 수치는 아니다. Release 빌드의 원본 DTO nullable 경고 1건은 보존했다.
- Python.NET은 고정 의존성과 함께 격리 Docker 이미지에만 설치했다. 운영 Canonical 입력·Run·Writer에 등록하지 않았으며
  Migration·DB Write·실제 발주·공용 배포·Commit/Push는 하지 않았다.

**1. 계산 Backend 채택 범위 확정 — 다음 작업 · InventoryEngine**

- C# 재현 모드·기존 개선형 모드의 의미와 실제 Canonical 입력 지원 범위를 먼저 정한다.
  이번 수치만으로 기존 계산 경로를 자동 교체하지 않는다. 완료 조건은 지원 범위와 실패·결과 계약이 명확한 것이다.

**2. 독립 실행·저장 연결 — 후속 직렬 작업 · InventoryEngine**

- 채택 범위가 정해지면 기존 ID/Hash·Writer/Verifier에 연결해 중단·조회·복구를 검증한다.
  실제 DB Write·공용 Runtime 배포는 별도 승인 대상이다. 자원 관리 고도화 보류도 유지한다.

아래는 과거 이력이다. 현재 다음 작업은 이 최상단 절을 우선하며 과거 측정 결과는 그대로 보존한다.

## 2026-10-21 동일 조건 C# Release·Python 성능 기준선 — 측정 완료 / 최적화 없음

**완료 — 입력 고정·직렬 계산/저장 측정·전체 결과 대조 · InventoryEngine Offline**

- [성능 기준선 보고서](IO_LEGACY_PSI_PERFORMANCE_BASELINE.md)에 입력·실행 환경·전체 표본·해석 경계를 정리했다.
  Apple M5 Max의 동일 Linux Arm64 이미지에서 양쪽을 CPU quota 1·1GiB로 제한했다.
  C#은 Release, Python은 기존 별도 재현 기준 그대로다. 기존 개선형·재현 계산 코드·FSN·운영 Gate·DB는 미변경이다.
- 52주·후보 32개를 고정하고 품목 1/16개, 결과 1,664/26,624행을 비교했다.
  조건별 새 프로세스 3회 × 본측정 7회로 21개 표본을 수집했다. 최초 호출·Warm-up은 별도 기록이다.
- 16품목 계산 중앙값은 C# **1.390ms**, Python **141.220ms**,
  직렬화·Hash·비교용 파일 저장 포함 전체는 **9.237ms / 161.743ms**다.
  현재 Python이 계산 **101.59배**, 전체 **17.51배** 오래 걸렸다. 개선 성과로 표현하지 않는다.
- 사전 검증·최초·Warm-up·본측정의 결과 파일 **136개**, 총 **1,923,584행**을 길이·Hash·전체 바이트로 대조했고 불일치 0건이다.
  [입력](data/legacy-psi-benchmark-input-20261021.json)과 [원시 표본](evidence/legacy-psi-benchmark-results-20261021.json),
  [Source/Build 준비 기록](evidence/legacy-psi-benchmark-preparation-20261021.json)을 보존한다.
- 신규 측정 도구 **17 passed**, 기존 재현·보충·분류·정책·발주 계획 포함 대상 회귀
  **442 passed / 124 subtests passed**(22.08초), Python 388파일 Ruff·Format을 확인했다.
  전체 회귀 수치가 아니며, Release C#의 기존 Golden·수치 경계 출력도 이전 캡처와 같았다.
- 실제 운영 DB/JSON/Parquet 게시·최종 정책 선정·운영 병렬 실행은 측정하지 않았다.
  Source 원문·기존 Hash·Runtime은 유지했으며 최적화·Migration·DB Write·발주·배포·Commit/Push는 수행하지 않았다.

**1. Python 계산 비용 분해 — 다음 작업 · InventoryEngine**

- 같은 입력에서 어느 작업이 비용을 차지하는지 프로파일로 확인한다. 현재 구현 간 차이를 언어 자체의 속도 차이로 일반화하지 않는다.

**2. 병목 근거를 보고 실행 방식 선택 — 후속 직렬 작업 · InventoryEngine**

- 확인 후 인덱스·배열·JIT 또는 C# Adapter의 적용 범위를 정한다. 실제 최적화는 이번에 수행하지 않았다.
  결과 동등성과 FSN·운영 Gate·DB 유지 경계를 보존한다.

아래는 과거 검증 이력이다. 성능 미측정 문구는 당시 상태이며 현재는 이 최상단 절을 우선한다.

## 2026-10-21 별도 Python C# 재현 기준 구현 — 제한 범위 완료 / 성능 미측정

**완료 — 실제 C# 출력과 별도 Python 계산 대조 · InventoryEngine**

- `inventory_engine_dev`, HEAD `99f2f12`와 기존 미커밋을 유지한다.
  [새 재현 계약](IO_LEGACY_PYTHON_REFERENCE_CONTRACT.md)의 `run_legacy_reference`는 명시적으로 호출하는 진단용 함수다.
  기존 개선형 Python·전략 Registry·Runtime Binding·FSN·운영 Gate·DB는 변경하지 않는다.
- 발주 처리 순서·Lot Min/Max 기준점·BaseMonth·LT/도착 Bucket·LT 0·Forecast BO 이월을 재현했다.
  double/decimal 중간 변환과 .NET 반올림, 입력 순서의 첫 일치도 보존한다.
- 기존 C# 캡처의 계산 27회와 추가 실제 출력 65회, 합계 **92회**를 허용 오차 없이 대조했다.
  추가 65회는 PSI 27·반올림 16·decimal 올림 14·Lot 8이다. 수치·날짜·순서의 불일치 0건이다.
  기존 Golden·C# 캡처는 그대로이며 반복 C# 출력도 같았다.
- 신규 **54 passed**와 기존 보충·분류/정책·선정·발주 계획을 포함한 대상 회귀
  **425 passed / 124 subtests passed**(21.48초)다. 전체 회귀 재실행 수치는 아니다.
  Ruff·Format은 Python 385개 파일 기준 통과했다. Coverage 도구가 없어 Coverage 비율은 측정하지 않았다.
- 원본 Main·DB 전후처리·운영 배포본은 실행하지 않았다. 전체 서비스 집계·최종 정책 선정·시각 있는 입력은 미검증이다.
  테스트 시간은 성능 비교 수치가 아니다. JIT·병렬화·성능 측정·의존성 설치는 하지 않았다.
- [추출 근거](evidence/legacy-python-reference-extraction-20261021.json)와
  [검증 기록](evidence/legacy-python-reference-verification-20261021.json)에 확인 범위를 남긴다.
  실제 Migration·DB Write·발주·배포·Commit/Push는 수행하지 않았다.

**1. 성능 비교 입력과 실행 조건 고정 — 다음 작업 · InventoryEngine Offline**

- 동등성 검사를 통과한 입력으로 품목/후보/주차·Release·동등 CPU 조건과 계산/전체 시간 범위를 고정한다.
  현재 기준 구현을 다시 미구현으로 나열하지 않는다. [비교 계획](IO_LEGACY_COMPATIBLE_PERFORMANCE_PLAN.md)을 따른다.

**2. 기준 측정 후 병목 최적화 — 후속 직렬 작업 · InventoryEngine**

- 측정 후 필요한 부분만 최적화하고 매번 실제 C# 동등성을 재검증한다. 전체 서비스·선정 Source 조사는 병렬 가능하다.
  기존 FSN·운영 Gate·DB는 유지하고 운영 연결·공용 배포는 별도 승인 대상으로 둔다.

아래는 과거 검증 이력이다. 기존 개선형 Python의 변경 없음과 새 진단용 재현 경로 추가를 구분한다.

## 2026-10-21 DB 없는 C# 실제 참조 실행·Python 비교 — 완료 / 제품 계산은 미변경

**완료 — 원문 계산 구간의 실제 결과와 Golden 검증 · InventoryEngine 테스트**

- `inventory_engine_dev`, HEAD `99f2f12`와 기존 미커밋을 유지한다.
  [참조기](../../tests/reference/legacy_csharp/README.md)는 지문이 일치하는 원본 7파일에서 계산문/DTO 21곳을 추출한다.
  원본 계산문은 줄바꿈 정규화 외에 바꾸지 않았고 테스트 입출력/메서드 Wrapper만 붙였다.
- 공식 SDK 8.0.426 / .NET 8.0.32 / Linux Arm64 컨테이너에서 실제 컴파일·실행했다.
  네트워크 차단·읽기 전용 소스·임시 메모리 빌드를 사용했다. DB·Secret·Socket은 Mount하지 않았다.
  원본 Main·라이선스·DB 전후처리·공용 Runtime은 실행/변경하지 않았고 Mac 전역 SDK도 설치하지 않았다.
- C# 대응 **20개 사례·29회 계산**, Python **21개 사례**, 기존 수작업 Golden 불일치 **0건**이다.
  새 격리 컨테이너에서 반복한 실제 JSON도 바이트가 같았다. 비용 미확인 1건은 대응 Legacy 계약이 없어 비교 불가로 보존했다.
- 공통 비교 필드 일치 6·문서화한 의미 차이 12·선정 적격 구성요소 비교 2건이다.
  실제 첫 발주 C# 23/Python 13, Lot 25/20, 주중 도착 Bucket과 BaseMonth 차이를 확인했다.
  적격 표시와 최종 Winner를 구분하며 전체 Optimizer·SVC_LV 집계·그룹→품목 전개·배포본은 미검증이다.
- [C# 실제 출력](../../tests/fixtures/native_legacy_reference_output.json),
  [비교 결과](evidence/legacy-native-python-comparison-20261021.json),
  [실행/검증 기록](evidence/legacy-native-execution-verification-20261021.json)을 보존한다.
  수작업 Golden은 덮어쓰지 않고 실제 실행 증적으로 검증했다.
- 신규 비교/변조 차단 9건을 포함한 대상 회귀 **371 passed / 124 subtests passed**(최종 재검증 22.79초),
  Golden 관련 묶음 **48 passed**(0.14초)다. 전체 회귀 재실행 수치는 아니다. Ruff·Format은 Python 379개 파일 기준 통과했다.
- 계산 코드·기존 Golden/Hash·ABC/XYZ 결과·FSN·운영 Gate·DB·Platform·공용 Runtime은 미변경이다.
  실제 발주·Migration·배포·Commit/Push는 하지 않았다. 공식 SDK 이미지 캐시와 로컬 임시 준비 파일만 추가했다.

**1. 확인된 차이의 목표 적용 범위를 정한다 — 다음 작업 · InventoryEngine 계약**

- 실제 결과를 근거로 기초/기말 판단·공통 `(s,S)`/현재 두 정책·Lot·시간 처리의 유지/변경을 정한다.
  완료 조건은 변경할 규칙과 Version/Binding 범위가 명확한 것이며 이번 실행으로 운영 규칙을 자동 변경하지 않는다.

**2. 남은 서비스·품목 정책 전개 Source를 확인한다 — 병렬 가능 다음 작업 · Legacy Source·InventoryEngine 문서**

- SVC_LV 집계·최종 그룹/품목 선택·RMOS/EMOS와 실제 LT 변환을 확인한다. 확인되지 않은 식을 가정으로 채우지 않는다.
- 목표 적용 판단과 독립적으로 조사할 수 있다. 작은 Golden 실행과 참조기 준비는 완료했으므로 다시 다음 작업으로 나열하지 않는다.

**3. 필요한 목표 보정·회귀 후 저장을 재개한다 — 후속 직렬 작업 · InventoryEngine → dsai-platform**

- 합의한 변경만 구현한 뒤 기존 Writer·Reference/Receipt로 발주 계획 저장·조회·복구와 독립 IO 검증을 재개한다.
  Platform·Demand 최종 연결은 그 뒤다. 실제 Migration·DB Write·공용 Runtime 배포는 **승인 필요**다.

아래는 보존한 과거 이력이다. ‘C# 참조 실행 미완료’는 각 기록 당시 상태이며 현재는 이 최상단 절을 우선한다.

## 2026-10-21 재현 규칙·수작업 Golden 고정 — 완료 / C# 참조 실행은 다음 작업

**완료 — 원본 재현과 Python 개선 유지의 경계·작은 기대값 확정 · InventoryEngine**

- `inventory_engine_dev`, HEAD `99f2f12`와 기존 미커밋을 유지한다.
  [재현·Golden 계약](IO_LEGACY_REPLAY_GOLDEN_CONTRACT.md)에 원본 재현 기준과 현재 Python에서 유지할 개선을 구분했다.
  C# 기말 판단·ROP/OUTL·Lot/Calendar·BO 이월과 Python의 주차 시작·두 정책·수요 구분을 섞지 않는다.
- [새 Golden 21건](../../tests/fixtures/golden_legacy_python_semantics.json)에 재고 전이 4·발주 3·Trigger 1·Lot 4·납기 4·월별 정책 2·선정 경계 3건을 고정했다.
  C#은 동결한 원문에서 손으로 계산한 기대값이며 실제 C# 실행 결과가 아니다. Python으로 Legacy 값을 자동 생성하지 않는다.
  SVC_LV 집계·그룹→품목 전개·전체 C# Winner는 미확인으로 남긴다.
- 신규 단위·계약·Offline 통합 **39 passed**(0.12초), 보충/수학·후보/선정·분류 호환·LEGACY_STYLE·
  선정 정책 실행까지 포함한 대상 회귀 **362 passed / 124 subtests passed**(22.00초)다. 전체 회귀 재실행 수치가 아니다.
  실제 CandidateStrategy→Guard→PSI에서 발주량·일자·EOH·거부 사유·입력 불변·게시 차단을 확인했다.
- 새 테스트 3개의 Ruff·Format, 기존 근거 19개 지문·원문 45곳을 확인했다.
  전체 Python 376개 파일의 Ruff·Format·문서 링크 115건·Diff Check도 통과했다.
  시작 시 기존 568개 파일 중 승인된 두 문서 외 566개는 동일하다. 새 파일은 Golden·테스트 3개·계약/검증 기록, 총 6개다.
  [검증 기록](evidence/legacy-replay-golden-verification-20261021.json)에 결과·검증 범위를 남긴다.
  캐시 쓰기 권한 오류는 캐시 없는 검사로 재실행했다. 패키지 설치나 기존 계산 코드 보정은 하지 않았다.
- 계산 코드·기존 Golden/Hash·ABC/XYZ 결과·FSN 설정·REVIEW/BLOCK·DB·Platform·Runtime은 변경하지 않았다.
  .NET 설치·C# 실행·Migration·실제 발주·배포·Commit/Push·전체 자원 실험은 하지 않았다.

**다음 작업 — DB 없는 원본 참조 실행 → 차이 판정 → 필요한 목표 보정 · InventoryEngine**

- 첫 작업은 동결한 C# 계산 메서드의 최소 참조 실행을 준비해 수작업 기대값을 실제로 대조하는 것이다.
  원본 DB 전후처리나 라이선스 진입점을 실행/우회하지 않는다. 도구 준비 범위도 별도로 확인한다.
- SVC_LV·그룹→품목 전개·RMOS/EMOS·LT 변환 Source 조사는 독립적으로 병렬 진행할 수 있다.
  Golden 원본 비교와 목표 규칙 변경·회귀는 직렬로 진행한다. 예상 차이와 실제 구현 오류를 구분하는 것이 완료 조건이다.
- 발주 계획 저장·조회·복구는 그 결과 이후 재개한다. 독립 IO 검증 뒤 Platform·Demand 연결로 진행하며
  실제 Migration·DB Write·공용 Runtime 배포는 **승인 필요**다. FSN·운영 Gate는 유지한다.

아래는 보존한 이전 이력이다. 재현 규칙·작은 Golden 고정을 다시 다음 작업으로 나열하지 않는다.

## 2026-10-20 C# 원본 확보·Python 정적 대사 — 문서 정리 완료 / 실행 동등성 미검증

**완료 — 원본 계산 경로와 현재 Python의 차이 정리 · InventoryEngine 문서**

- `inventory_engine_dev`, HEAD `99f2f12`와 기존 미커밋을 유지한다.
  `NSPM 2/SupplyEngine/DSIO_postgresql_20250908/RootIO`에서 PSI·Lot·Calendar·Optimizer·PostgreSQL 출력 원문을 확인했다.
  [상세 대사](IO_LEGACY_CSHARP_PYTHON_RECONCILIATION.md)와
  [근거 기록](evidence/legacy-csharp-reconciliation-20261020.json)에 출처·지문·확인 경계를 남긴다.
- 핵심 4개 원본 파일은 제공 ZIP과 바이트가 같다. 이는 운영 배포본과의 일치를 증명하지 않는다.
  기본 프로젝트의 `PSI;PROD`와 별도 OPTIMIZER 진입점, 활성 단일 Buffer 테스트 필터도 구분했다.
  원본 프로그램 실행·.NET 설치·DB 전후처리는 수행하지 않았다.
- C#은 당주 입고·수요·BO 처리 **후** `EOH+누적 PO−누적 BO<ROP`이면 OUTL까지 보충한다.
  `OUTL/ROPQ/MINQ/SFST`의 switch는 주문 알고리즘이 아니라 **예상 MOS 계산**이다.
  현재 Python의 주차 시작 판단·고정 Q/주기적 S 정책과 일대일 대응하지 않는다.
- Forecast 부족 이월, BaseMonth 정책, Lot 기준점, LT 환산·도착 Bucket, 기간 밖 주문, 서비스 지표의 차이도 확인했다.
  기존 Cut-off·고객주문/Forecast 분리·미확약 공급 제외·물리 제약·FSN/운영 Gate는 유지할 개선으로 구분한다.
- C# Optimizer는 Segment×Scenario 집계·수요금액 가중 FILL_RATE/MOS를 소비한다.
  현재 Python의 품목별 비용·재고·발주횟수 순위와 다르다. `SVC_LV` 생성식과 그룹→품목 전개는 추가 원문 확인이 필요하다.
- LEGACY_STYLE은 후보 생성 Adapter이지 C# PSI 재현 모드가 아니다.
  [선정 정책 연결 계약](IO_SELECTED_POLICY_ORDER_PLAN_CONTRACT.md)의 ‘C# 본문 미확보’를 정정하되 기존 Python 계약·검증 이력은 보존한다.
- 이번 작업은 문서 대사다. 직전 **404 passed / 186 subtests passed**는 기존 Python 실행 연결의 검증 이력이며
  이번에 재실행한 결과나 Legacy 수치 동등성 수치가 아니다. 계산 코드·Schema·Golden·기존 Hash는 변경하지 않는다.
- 원본 ZIP 포함 19개 지문·원문 근거 45곳·로컬 문서 링크 109건·계획 7개 의존관계·Diff Check를 확인했다.
  시작 시 기존 565개 파일 중 승인된 두 문서 외 563개는 동일하다. 새 파일은 대사 문서·근거/계획 JSON 3개다.
- FSN·운영 Gate·DB·Platform·Runtime·설치·Migration·실제 발주·Commit/Push는 변경하지 않는다.
  자원 관리 추가 고도화와 공식 대규모 실험도 계속 보류한다.

**다음 작업 — 계산 의미·원본 비교를 먼저 닫고 저장을 재개 · InventoryEngine → dsai-platform**

1. **다음 작업 · InventoryEngine 계약:** 원본 재현과 목표 개선을 구분하고 발주 시점·정책식·Calendar·서비스/선정의 작은 Golden을 확정한다.
2. **다음 작업 · C# 원본·InventoryEngine Offline:** DB 없는 최소 참조 실행을 준비하고 같은 입력의 PSI·주문·선정 차이를 검증한다.
   서비스 집계·그룹→품목 Source 조사는 독립 진행 가능하지만 계산 계약과 비교 실행은 직렬이다.
3. **다음 작업 · InventoryEngine:** 비교 후 승인한 목표 수정만 기존 Version/Binding 범위에 구현하고 회귀 검증한다.
4. **현재 보류 · InventoryEngine 저장 → dsai-platform Run/API/화면:** 위 결과 뒤 기존 Writer·Reference/Receipt로 저장·조회·복구와 독립 IO 검증을 재개한다.
   완료된 선정 정책 실행 Adapter를 다시 구현하지 않는다. 실제 DB 적용/E2E Write·공용 배포는 **승인 필요**다.

목적·완료 조건·의존관계는 [재정렬 작업 순서](IO_LEGACY_CSHARP_PYTHON_RECONCILIATION.md#7-재정렬한-작업-순서)와
[계획 JSON](data/legacy-csharp-reconciliation-plan-v1.json)을 따른다.
아래는 보존한 이전 이력이다. 현재 ‘다음 작업’은 이 최상단 절을 우선하며, 과거의 저장 우선 계획은 보류한다.

## 2026-10-20 선정 정책 실제 실행 연결 — 로컬 구현·Offline 검증 완료 / 저장·Platform 연결은 후속

**완료 — 선정 정책을 기존 보충 전략·공통 Guard·PSI로 실행 · InventoryEngine**

- `inventory_engine_dev`, HEAD `99f2f12`와 기존 미커밋을 유지한다.
  [RunSelectedPolicyOrderPlanUseCase](../../src/dsio_inventory_engine/recommend_replenishment/application/selected_order_plan.py)를 추가하고
  [연결 계약](IO_SELECTED_POLICY_ORDER_PLAN_CONTRACT.md)에 실제 완료 범위를 반영했다.
- 기존 7필드 Binding을 소비한다. 원 선정·Source·Config·전체 분류·수학 입력을 검증한 뒤 선정 품목만
  기존 `CandidateStrategy`·공통 Guard/PSI로 다시 계산한다. 과거 주문 배열을 새 계획으로 복사하지 않는다.
- 동일 Candidate Execution Hash·입력 Binding을 재사용해 기존 행동/주문 ID·Source 정책 ID와 원 후보 Row/Hash를 유지한다.
  새 Plan ID·Binding ID·Root Hash를 만들지 않는다. 원 봉인 Prepared 입력과 정책 적용용 계산 Projection도 분리한다.
- ABC V1·ABC/XYZ 공유예산·LEGACY_STYLE·참조 후보의 실제 선정 결과를 연결했다.
  미선정은 사유·Baseline만 유지하며 대체 정책을 넣지 않는다. 전 품목 미선정은 `NO_POLICY_SELECTED`다.
  REVIEW는 계산·증적만 허용하고 BLOCK은 신규 보충을 만들지 않는다. 모든 결과는 계산 전용이며 자동 게시/발주를 허용하지 않는다.
- [수작업 Golden 8종](../../tests/fixtures/golden_selected_order_plan.json)으로 수량·발주일·물리 납기·입고 반영일과 조정/거절 사유를 대조했다.
  고정 수량/목표재고·MOQ/배수·용량·MOQ 미충족·주중 도착·2주 점검·LT=0을 포함한다.
  별도 사례에서 확정 입고 20EA·미확인 100EA·Backorder 5EA·확정 고객주문 8EA를 적용해
  EOH 15→5→3EA, 두 번째 주 주문 8EA를 확인했다. SDE P90=21일이어도 물리 납기 7일은 유지했다.
- 신규 단위·Offline 통합 51건을 포함한 관련 회귀 **404 passed / 186 subtests passed**(25.78초)다.
  공통 PSI·수학 정책·기존 후보/선정·Catalog·계약·Runtime Gate·CLI를 포함하며 전체 회귀 재실행 수치는 아니다.
  재봉인한 원 주문/비용/제외·정책 치환·REVIEW 하향과 새 계산의 Row/행동 불일치를 차단했다.
- Ruff·Format 373개 Python 파일·JSON/Schema 41개·Golden 8종·현재 문서 링크 8건·Diff Check를 확인했다.
  시작 시 기존 파일 560개 중 승인된 두 문서를 제외한 558개는 동일하며 새 구현/테스트/Golden 파일은 5개다.
- 이 결과는 메모리 내 계산 View다. 발주 계획 영속 저장·조회/복구·Platform Run/API/화면·실제 DB Run은 아직 없다.
  발주요청 확정·ERP도 후속 범위다. Coverage·Bandit은 환경에 없어 실행하지 않았으며 독립 에이전트 검토도 하지 않았다.
- 기존 소스·분류 규칙·ID/Hash·FSN 설정·운영 Gate·DB·Platform·공용 Runtime을 변경하지 않았다.
  설치·Migration·DB Write·Forecast 재실행·실제 발주·Commit/Push는 수행하지 않았다.

**다음 작업 — 발주 계획의 로컬 저장·조회·복구 · InventoryEngine**

- 기존 Artifact Writer·Reference/Hash/Receipt를 우선 재사용해 결과·선정 근거를 저장한다.
  동일 입력/결과 멱등 처리·다른 Hash 충돌·누락·중단 후 복구를 Offline으로 검증한다.
- 독립 IO 검증 후 Platform Run/API/화면을 연결한다. 저장 계약 확정 후 Backend/Frontend는 분리할 수 있지만
  결과 봉인·복구·독립 통합은 직렬 작업이다. 실제 DB 적용·E2E Write는 별도 승인 대상이다.
- 발주요청 확정·ERP 전송·Demand 연결은 그 뒤로 분리한다. 자원 관리 추가 고도화·대규모 강건성 실험은 계속 보류한다.

아래는 보존한 이전 이력이다. 현재 실행 연결 완료 범위와 다음 작업은 이 최상단 절을 우선한다.

## 2026-10-20 선정 정책 → 발주 계획 연결 계약 — 로컬 구현·계약 검증 완료 / 실행 Adapter 미구현

**완료 — 기존 ID·Hash를 재사용하는 계산 전용 연결 계약 · InventoryEngine**

- `inventory_engine_dev`, HEAD `99f2f12`와 기존 미커밋을 유지한다. 새 [선정 정책·발주 계획 계약](IO_SELECTED_POLICY_ORDER_PLAN_CONTRACT.md),
  [Python 검증기](../../src/dsio_inventory_engine/inventory_contracts/order_planning.py),
  [JSON Schema](../../schemas/selected_policy_order_plan_binding.schema.json)를 추가했다.
- 기존 `PSEL`/`OCPSEL` 선정 결과 Reference·Hash, Canonical Hash와 수학 입력 Snapshot Binding을 그대로 사용한다.
  연결 문서는 7개 필드이며 새 `order_plan_id`, `binding_id`, 연결 자체의 Hash·품목별 중복 Hash·정책 목록을 만들지 않는다.
- V1은 동일 Plan·Site·Run·W0·Config·Cut-off와 같은 봉인 입력에 한정한다. 다른 입력의 재계획은 허용하지 않는다.
  기존 Context·Canonical/수학 Hash·활성 재고관리 품목 Coverage를 대조하고 추가 필드·미봉인 입력·권한 상승을 거부한다.
- 문서/기존 Hash의 연결 검증과 실제 선정 Source 재생 검증·입력 Admission은 구분한다. 연결 검증은 PSI를 실행하지 않는다.
  후속 실행 Adapter가 기존 선정 Verifier·보충 전략·공통 Guard·PSI를 재사용해야 한다.
- 신규 단위·계약 테스트 **51건**, 관련 후보 탐색/선정·고정 Hash·Runtime Gate·Offline 통합을 포함해
  **249 passed / 62 subtests passed**(19.21초)다. 전체 회귀를 다시 실행한 수치가 아니다.
  Ruff·Format 신규 Python 4개·JSON 문서/Schema 41개·새 계약 로컬 링크 3개·Diff Check를 확인했다.
- 실제 ABC V1·ABC/XYZ 공유예산·LEGACY_STYLE·참조 후보의 선정 결과로 계약을 검증했다.
  원 Source/선정·입력·기존 Hash는 변경하지 않고 FSN BLOCK/REVIEW와 미선정·검토 사유를 보존했다.
- 실제 선정 정책 실행 Adapter·신규 발주 계획 계산/Artifact 봉인·Platform Run/화면 연결·DB 적재는 아직 없다.
  발주요청 확정·ERP 연계는 후속 범위다. FSN·운영 Gate·DB·공용 Runtime·설치·Commit/Push는 변경하지 않았다.

**보류 — 자원 관리 추가 고도화와 대규모 강건성 실험**

- 기존 20GB 예산·호출당 3,600초·중단/재개·압축/공유 입력 보호는 유지한다. 추가 사전 추정·전체 Split 측정은 진행하지 않는다.
- 아래의 부분 자원 추정과 미측정 항목은 대규모 실험의 보류 근거다. IO 핵심 개발·소규모 독립 검증의 선행 차단으로 사용하지 않는다.
- 과거 자원/실험 기록을 보존한다. 실제 실행 병목이 확인될 때 추가 최적화를 재검토한다.

**다음 작업 — 선정 정책을 기존 보충 전략·공통 PSI에 연결 · InventoryEngine**

- 원 선정 결과를 검증하고 선택한 품목 정책 모수만 기존 전략으로 실행한다. 과거 Simulation 주문 배열을 복사하지 않는다.
- 실제 PSI Row·주문·발주/납기/반영일·제약 조정·미선정 사유를 수작업 Golden으로 대조한다.
  계약과 실행이 연결되기 전 완료된 발주 계획·업무 승인으로 표시하지 않는다.
- 후속은 로컬 저장/조회/복구와 Platform Run·화면 연결이다. API 계약 확정 뒤 Backend/Frontend는 분리할 수 있으나
  실행 Adapter·Golden·전체 흐름은 직렬로 검증한다. 실제 DB 적용·E2E Write는 별도 승인 후 수행하고 Demand 연결은 IO 독립 검증 이후다.

아래는 보존한 이전 이력이다. 과거의 자원 사전 검증·공식 실험 다음 작업보다 이 최상단 절을 우선한다.

## 2026-10-20 전체 Split 자원 사전 검증 — 구현·실측·Offline 검증 완료 / 전체 자원 합격 보류

**완료 — 전체 작업 목록과 미측정 항목의 합격 차단 · InventoryEngine**

- `inventory_engine_dev`, HEAD `99f2f12`와 기존 미커밋을 유지한다. 기존 코드·규칙·Hash·FSN·운영 Gate·DB·공용 Runtime은 변경하지 않는다.
- [Split 사전 검증 계약](IO_ROBUSTNESS_SPLIT_PREFLIGHT_CONTRACT.md)과 별도 계획/응답을 추가했다.
  고정 Protocol에서 기본 198개·Stress 16개·시간 비교 27개·종료 재생 상한 180개, 총 421개의 메타데이터를 대조한다.
  기본 Bank를 범위/비용 Profile마다 다시 곱하지 않으며 공식 경로·정책을 생성하거나 선정하지 않는다.
- 같은 자원 관리 루트에서 기존 제어기→실제 전체 후보→무결성 조회→전체 재계산을 측정하는 진입점을 추가했다.
  수작업 W0 `2055-01-04`, Path `HAND-CATALOG-PATH`, Seed 71로 제한하고 기존 32/32/88 후보와 결과 Hash·PSI Row를 확인한다.
- 알려진 Catalog 부분 추정과 개발 여유 25%를 구분한다. 아직 측정하지 않은 종료 재생·전체 집계·누적 루트 대사·
  실제 경로 크기·중단 여유·Source 생성/전체 분류는 0으로 채우지 않고 전체 크기/시간/파일 수를 `null`로 남긴다.
  상태는 `BLOCKED_INCOMPLETE_MEASUREMENTS`이며 부분 추정이 한도 안에 있어도 공식 실험을 허용하지 않는다.
- 새 대상 회귀 **59 passed**(115.60초), Ruff·Format 365개 파일·JSON Schema 40개·Diff Check를 확인했다.
  별도 수작업 2회 순환, 총 6회 실측은 모든 기존 결과 Hash·PSI Row를 유지했다.
  ABC/XYZ·LEGACY_STYLE·WIDE의 원문 증가는 각각 22.996/22.993/60.763MB, 계산·조회·재계산과 대사를 합친 호출은
  각각 23.64~27.99/24.69~26.54/65.75~66.38초였다. 이는 정책 성과나 일정 속도 개선을 판정한 실험이 아니다.
- 알려진 Catalog 부분 추정은 원문 13.964GB·물리 1.377GB·36,497개 파일·약 4.25시간이다.
  개발 여유 25% 포함 시 원문 17.455GB·물리 1.721GB·45,622개 파일·약 5.31시간이다.
  실제 전체 합계/상한이 아니며 미측정 6개 항목을 포함할 여유가 검증된 것은 아니다. 호출당 3,600초를 전체 실험 제한시간으로 해석하지 않는다.
  실측은 원문 213.5MB/물리 21.4MB를 보존한 한 관리 루트에서 수행했고 Job 18개를 격리했다.
  [검증 기록](evidence/robustness-split-preflight-20261020.json)에 표본·추정값·지문·보류 사유를 남겼다.
- 최종 전체 회귀는 **1,796 passed / 9 skipped / 669 subtests passed**(1,196.83초), 기존 Starlette 경고 3건이다.
  회귀 실행 중 소스는 고정했다. Ruff·Format 365개 파일·JSON Schema 40개·선택한 여섯 문서의 로컬 참조 149건·Diff Check를 확인했다.
  승인된 기존 문서 3개 외에 기존 보호 파일 541개·모든 기존 코드/규칙/Schema/Golden/측정 기록·개인 보고서 Hash는 동일하다.
  새 파일은 구현/테스트/Schema 8개와 계약/검증 기록 2개다. Coverage·Bandit·Mypy·독립 에이전트 검토·호스트 전체 자원 보장은 확인하지 않았다.

**다음 작업 — 미측정 계산·집계 경로를 측정 가능한 단위로 완성 · InventoryEngine**

- 잠긴 단일 정책의 52주 재생을 추가하고, 기존 순수 평가기·Warm-up Source를 전체 Split에 연결해 저장/재시작·집계 비용을 검증한다.
  대표 수작업 Source와 누적 루트/중단 여유도 확인한다. 기존 평가기를 새로 구현하는 작업은 아니다.
- 계약 확정 뒤 순수 테스트는 분리할 수 있지만 같은 Pool의 성능 측정은 직렬로 진행한다. 전체 합계 확인 전 공식 경로·본실험은 진행하지 않는다.
- DB 적용·공용 배포·Demand P12 연결은 별도 승인 범위다. 이번에는 Commit/Push도 수행하지 않는다.

아래는 보존한 이전 이력이다. 현재 완료 범위와 다음 작업은 이 최상단 절을 우선한다.

## 2026-10-20 전역 자원 통제 — 로컬 구현·Offline 검증 완료

**완료 — 관리 루트 전체의 원문 예산·중단·재개·임시 파일 정책 · InventoryEngine**

- `inventory_engine_dev`, 기준 HEAD `99f2f12`와 기존 미커밋을 유지한다. 설치·배포·Commit/Push는 수행하지 않았다.
- [자원 통제 계약](IO_ROBUSTNESS_RESOURCE_CONTROL_CONTRACT.md)과
  [ControlledCatalogExecutor](../../src/dsio_inventory_engine/evaluate_replenishment/robustness/resource_execution.py)를 추가했다.
  같은 관리 루트의 모든 저장 폴더/프로세스가 최대 20GB 원문 예산을 공유한다. 원문 크기·예약/관리 메타데이터·격리 파일을 합산하며,
  V3 공유 파일과 같은 Inode의 Hard Link는 한 번 계산한다. 압축 크기로 할인하지 않고 기존 128MB 문서 상한을 유지한다.
- 같은 OS Lock 아래 예산 확인·예약 fsync·원래 V1/V2/V3 게시를 수행한다. 재시작은 실제 파일을 대사하며 새 인스턴스가 예산을 초기화하지 못한다.
  계산·분류·원문·Leaf·결과·기존 Receipt에 자원 메타데이터를 넣지 않는다. 기존 직접 호출과 공용 업무 Runtime은 변경하지 않았다.
- 호출별 최대 3,600초 Deadline에 대기·기동·계산을 포함하고 계산 Worker는 관리 루트당 하나로 제한한다.
  별도 Process Group과 Watchdog으로 응답하지 않는 계산·취소·감독 프로세스 소멸을 처리한다.
  시간 초과를 성공/실패 후보로 위장하지 않으며 중단 후 정확한 입력 Hash로 기존 실제 재생·미완료 Leaf 계산을 이어간다.
  큰 입력은 같은 예산의 Job 파일로 전달하며 독립 Worker에는 작은 참조만 보낸다. 시작 파이프 교착과 Python 시작 지연도
  시간 제한을 우회하지 못하도록 보정했다. Job Hash를 다시 검증하고 호출 후에는 같은 임시 파일 정책을 적용한다.
- 미완성 임시 파일은 격리·원문 예약 크기/물리 Hash 기록 후 최소 24시간 보존한다. 명시적 정리 호출은 만료된 격리 파일만 대상으로 한다.
  게시된 Artifact·Checkpoint·미참조 공유 파일은 자동 삭제하지 않는다. 메타데이터 없는 미확인 파일은 128MB로 보수적으로 계산한다.
- 새 단위 25건·계약 20건·Offline 통합 18건을 추가했다. 기동 Job의 파일 수 여유 검사까지 포함한
  최종 대상 회귀는 **63 passed**(139.98초)다. 기동 Job도 파일을 쓰기 전에 원문 예산과 파일 수 여유를 확인한다.
  실제 32/32/88개 후보의 기존 결과 Hash·2,080/2,080/5,720 Row,
  두 프로세스 예산 경합·Worker/하위 프로세스 취소·감독 프로세스 소멸·실제 첫 Leaf 후 재개·임시 파일 격리를 확인했다.
  20GB 파일 생성/3,600초 대기 시험 대신 작은 예산·짧은 Deadline과 원래 최대값 계약을 사용한다.
  수치·파일 지문·검증 범위는 [자원 통제 검증 기록](evidence/robustness-resource-control-20261020.json)에 기록했다.
- 전체 Unit·Contract·Offline Integration 회귀는 **1,736 passed / 9 skipped / 669 subtests passed**(1,042.46초), 기존 Starlette 경고 3건이다.
  전체 회귀에서 수집한 테스트는 마지막 Job 파일 수 검사 1건 추가 전 기준이며, 그 보완을 포함한 최종 대상 63건은 별도로 통과했다.
  Ruff·Format 358개 Python 파일·JSON Schema 39개·Diff Check·다섯 문서의 로컬 참조 136건을 확인했다.
  독립 에이전트 검토·Coverage/Bandit/Mypy·호스트 전체 디스크/RSS 제한·정해진 속도 개선율을 검증한 것은 아니다.
- FSN 설정·운영 Gate·DB·Platform·공용 Runtime과 원문/결과 Hash는 유지한다. 공식 경로 생성·본실험·실제 발주도 수행하지 않는다.
  이 통제는 새 로컬 제어 경로와 같은 관리 루트를 사용하는 저장 Adapter의 범위다. 기존 임의 직접 호출까지 자동 통제한 것은 아니다.
  승인된 기존 문서 4개 외에 기존 보호 파일 527개·개인 보고서와 모든 기존 코드·Schema·Golden·측정 기록은 동일하다.
  새 파일은 13개다. 구현/테스트/Schema 11개 파일 지문을 검증 기록과 대조했다.

**다음 작업 — 전체 Split 조립의 자원 사전 검증 · InventoryEngine**

- 새 제어기와 관리 루트를 사용하는 진입점을 고정하고 수작업 입력의 시간·용량으로 모든 후보/경로의 예상 자원을 확인한다.
  공통 실행·저장 계약에 의존하므로 직렬로 진행한다. 완료 조건은 전체 Bank·동일 Hash·완전성을 유지하면서 예산 안에서 조립할 수 있는 것이다.
- 공식 경로 생성·본실험은 이번 작업과 분리한다. 실제 DB 적재·Migration·공용 배포·Demand P12 연결은 별도 승인 범위다.

아래는 보존한 이전 이력이다. 현재 완료 범위와 다음 작업은 이 최상단 절을 우선한다.

## 2026-10-20 무결성 조회·전체 재계산 검증 분리 — 로컬 구현·Offline 검증 완료

**완료 — 조회 범위와 실제 재계산 상태 구분 · InventoryEngine**

- 작업 브랜치 `inventory_engine_dev`, 기준 HEAD `99f2f12`와 기존 미커밋을 유지한다. Commit/Push·설치·배포는 수행하지 않았다.
- [조회·재계산 계약](IO_ROBUSTNESS_VERIFICATION_READ_CONTRACT.md)에 별도 응답 `inventory-robustness-verification-view` `1.0.0`을 추가했다.
  `inspect`는 전체 파일·후보·Hash·건수·연결을 확인하고 재계산 상태를 `NOT_RUN`으로 반환한다.
  `verify_recalculation`만 기존 `read`의 실제 Source·전략·Guard·PSI·평가 재생을 거쳐 `VERIFIED`로 반환한다.
  검증 전후 입력/Manifest/결과 Binding이 달라지면 거부한다.
- Header만 읽는 조회가 아니다. V1/V2/V3 원문과 공유 파일·전체 Leaf·32/32/88개 후보·성공/실패 분모·65주 Row를 확인한다.
  Leaf를 하나씩 최종 Hash에 반영해 큰 부모 결과를 한 문서로 펼치지 않는다. 실제 계산값의 옳고 그름은 빠른 조회가 확인하지 않는다.
- 새 응답은 원 결과와 별개이며 원문·Leaf·결과 Hash와 기존 Receipt를 바꾸지 않는다. 기존 `read`·`recover`의 실제 재생은 유지했다.
  이전 검증 응답을 캐시 승인으로 재사용하지 않고 매번 다시 읽는다. 전체 검증 후 빠른 조회를 해도 재계산은 `NOT_RUN`이다.
  응답은 운영 승인 증명서가 아니며 자동 게시·자동 발주·정책 승인·DB Write 값은 모두 `false`다.
- 새 단위 26건·계약 23건·Offline 통합 16건으로 **65건**을 확인했다.
  실제 32/32/88개 결과 Hash·2,080/2,080/5,720 PSI Row와 전체 재계산의 전략 실행 횟수(후보 수 × 65)를 확인했다.
  비용·관련 Hash를 일관되게 고친 사례는 무결성만 통과하고 실제 재생에서 거부된다.
  공유/압축 파일 손상·누락·건수·순서·Binding·Version 변조·구현 변경·재계산 중 Manifest 변경도 검증했다.
- 최종 전체 Unit·Contract·Offline Integration 회귀는 **1,674 passed / 9 skipped / 669 subtests passed**(925.76초), 기존 Starlette 경고 3건이다.
  Ruff·Format 348개 Python 파일·JSON Schema 38개·Diff Check를 통과했다.
  검증 범위와 보호 파일 대사는 [조회·재계산 검증 기록](evidence/robustness-verification-read-20261020.json)에 남긴다.
  별도 속도/RSS 개선율·운영 합격·보안 스캔·커버리지 수치·독립 에이전트 검토를 주장하지 않는다.
- 기존 ABC/XYZ 결과/Hash·분류/규칙 Golden·FSN 설정·운영 Gate·DB·Platform·공용 Runtime은 변경하지 않았다.
  공식 경로 생성·본실험·실제 발주도 수행하지 않았다. 이전 측정·검증 이력은 그대로 보존한다.
  연결 코드 1개와 승인된 기존 문서 3개 외에 기존 보호 파일 518개와 개인 보고서 Hash는 동일하다. 새 파일은 9개다.
  네 문서의 로컬 참조 117개에 누락이 없으며 구현·Schema·테스트 8개 파일 지문을 검증 기록과 대조했다.

**다음 작업 — 전역 자원 통제 · InventoryEngine**

- 원문 20GB 예약/집계·호출당 3,600초 취소·중단 재개·임시 파일 정책을 서비스 수준에서 연결한다.
  조회/검증도 같은 원문 예산을 적용하며 압축으로 상한을 우회하지 않는다.
- 공통 저장·복구 경로에 의존하므로 현재 세션에서 직렬로 진행한다. 완료 조건은 중단·실패·재개에도 자원 한도와 기존 Hash를 유지하는 것이다.

**후속 작업 — 전체 Split 조립의 자원 사전 검증 · InventoryEngine**

- 수작업 입력의 시간·용량을 기준으로 전체 예상 용량과 모든 후보/경로의 완전성을 확인한다.
  공식 경로 생성·본실험은 이번 범위가 아니다. 실제 DB 적재·Migration·공용 배포·Demand P12 연결은 별도 승인 범위다.

아래는 보존한 이전 이력이다. 현재 완료 범위와 다음 작업은 이 최상단 절을 우선한다.

## 2026-10-20 불변 입력 준비·직렬화 재사용 — 로컬 구현·Offline 검증 완료

**완료 — 동일 입력 준비의 실행별 재사용과 후보 상태 분리 · InventoryEngine**

- 브랜치 `inventory_engine_dev`, 기준 HEAD `99f2f12`와 기존 미커밋을 유지한다. 설치·배포·Commit/Push는 수행하지 않았다.
- [재사용 계약](IO_ROBUSTNESS_IMMUTABLE_PREPARATION_CONTRACT.md)과
  [준비 슬롯](../../src/dsio_inventory_engine/evaluate_replenishment/robustness/immutable_preparation.py)을 로컬 Catalog/영속 조립기에 연결했다.
  배포 Scope·전체 요청 JSON·Source/생성 요청의 실제 정규 JSON·기대 Hash가 같은 경우에만 검증된 준비와 직렬화를 재사용한다.
  새 실행·Build·읽기/복구 Context는 새 슬롯을 사용하며 전역 캐시는 없다.
- 원 Source 정책·시작재고는 불변 입력으로만 보존한다. 후보별 정책 계산·FSN Admission·재고·BO·미도착 주문·관측·Guard·PSI는
  분리된 Dict/List에서 실행한다. 새 실현 경로를 준비된 계획 입력으로 위장하거나 미래 실현 수요를 전략에 노출하지 않는다.
  128MB 한 슬롯의 보존 한도를 넘는 유효 입력은 재사용하지 않고 계산한다. 프로세스 전체 메모리 제한의 구현은 아니다.
- 새 단위 10건·계약 1건·Offline 통합 7건을 추가했다. 32/32/88개 후보의 재사용 ON/OFF Leaf 원문 바이트·결과 Hash가 같았고,
  Leaf 역순 실행·반환 결과 변조·입력/Source/Scope/정책 제약 변경·FSN BLOCK/REVIEW·실패 후보 보존을 검증했다.
  후보 16개가 든 Leaf 한 개의 재생에서도 실제 전략 1,040회·Guard·PSI 계산을 수행했다. 저장/복구의 기존 실제 재생 검증은 유지한다.
- [동일 수작업 입력의 18회 비교](../../scripts/benchmark_robustness_preparation.py)는 두 방식·세 반복을 새 프로세스로 실행했다.
  공통 준비는 후보 32/88회에서 각각 1회가 됐다. 전체 계산시간 중앙값은 ABC/XYZ **4.145→3.728초**,
  LEGACY_STYLE **4.257→3.824초**, 참조 88개 **11.352→10.130초**로 약 **10.1/10.2/10.8% 감소**했다.
  대조군에도 입력 Hash 지역 변수 재사용은 적용했으므로 과거 Commit 전체와의 속도 비교가 아니다.
- 최대 RSS 중앙값은 각각 **380.7→341.4 / 376.9→490.7 / 971.4→783.0 MB**로 일관되게 줄지 않았다.
  저장·조회 속도나 공식 전체 경로의 자원 합격도 주장하지 않는다. 기존 V1/V2/V3 저장 형식과 이전 측정 기록은 보존했다.
  Source 지문·측정 사양·결과와 검증 범위는 [준비 재사용 기록](evidence/robustness-immutable-preparation-20261020.json)에 남긴다.
- 기존 ABC/XYZ 결과/Hash·분류/규칙 Golden·FSN 설정·운영 Gate·DB·Platform·공용 Runtime은 변경하지 않았다.
  공식 경로 생성·본실험·실제 발주도 수행하지 않았다.
- 최종 전체 회귀는 **1,609 passed / 9 skipped / 669 subtests passed**(735.40초), 기존 Starlette 경고 3건이다.
  Ruff·Format 342개 Python 파일·기존 JSON Schema 37개·Diff Check·변경 부분의 로컬 문서 참조 6개를 확인했다.
  Coverage·Bandit·Mypy는 설치되지 않았으며 별도 보안 스캔·독립 에이전트 검토나 커버리지 수치를 보고한 것은 아니다.
  이번 연결 코드 4개와 승인된 기존 문서 2개 외에 기존 보호 파일 509개와 개인 보고서 Hash는 동일하다. 새 파일은 7개다.

**다음 작업 — 빠른 조회와 실제 재생 검증 분리 · InventoryEngine**

- 파일/원문 무결성 조회와 전체 후보의 계산 재생을 호출·결과 상태에서 구분한다. 조회만 끝났는데 재생 검증도 끝난 것으로 표시하지 않는다.
- 이번 재사용 계약에 의존하므로 직렬로 진행한다. 완료 조건은 기존 실제 검증 강도를 유지하면서 두 범위를 구별하는 것이다.

**후속 작업 — 전역 자원 통제와 전체 Split 조립 사전 검증 · InventoryEngine**

- 원문 20GB·호출당 3,600초 예약/취소·중단 재개·임시 파일 정책을 서비스 수준에서 연결하고 전체 예상 용량을 확인한다.
- 공식 경로 생성·본실험은 아직 진행하지 않는다. 실제 DB 적재·Migration·공용 배포·Demand P12 연결은 별도 승인 범위다.

아래는 보존한 이전 이력이다. 현재 완료 범위와 다음 작업은 이 최상단 절을 우선한다.

## 2026-10-20 공통 입력 V3 참조화 — 로컬 구현·Offline 검증 완료

**완료 — 분류·Calendar·Forecast 중복 저장과 원문 복원 · InventoryEngine**

- 작업 브랜치는 `inventory_engine_dev`, 기준 HEAD는 `99f2f12`와 보존된 미커밋이다. 설치·배포·Commit/Push는 수행하지 않았다.
- [공유 입력 계약](IO_ROBUSTNESS_SHARED_INPUT_CONTRACT.md)과
  [V3 저장기](../../src/dsio_inventory_engine/infrastructure/robustness_shared_artifacts.py)를 추가했다.
  분류·Calendar·Forecast의 512 Byte 이상 Dict/List를 내용 Hash의 불변 공유 파일로 저장하고,
  원 문서의 정확한 경로·원문 크기/Hash·물리 참조를 고정한다. 실제 영속 조립기의 동일 Repository 인터페이스에 연결했다.
  기존 계산 코드·V1/V2 Writer·기본 Runtime은 변경하지 않는다.
- 원 관측·행동·정책·Guard·전체 PSI와 부모 분류 모집단을 생략하지 않는다. 공유 값을 복원한 뒤
  원 정규 JSON UTF-8+LF 바이트·논리 Hash와 기존 최종 Result Hash를 검증한다. 모든 Leaf의 실제 Catalog·전략·Guard·PSI 재생 검증도 유지한다.
  저장 Manifest/Receipt는 물리 참조가 달라 Hash가 바뀔 수 있으며 이를 계산 Hash 변경과 구분한다.
- 원문 복원 크기를 공유 파일 로딩 전에 계산하고 문서당 128MB 상한을 유지한다. 참조 경로·4,096개/깊이 64 제한·중복/겹침·
  물리 파일/프레임/CRC·Wrapper·값 Hash를 검사한다. 누락·변조를 다른 형식이나 최신 Source로 대체하지 않는다.
- V1/V2 완료 결과의 파일과 Receipt는 그대로 읽고, 부분 V1/V2 실행은 기존 조각을 유지한 채 V3로 재개한다.
  공유 파일만 게시된 중단의 동일 입력 재요청과, Leaf Checkpoint 직후 실제 프로세스 종료 후 새 프로세스 복구를 검증했다.
  같은 Hash는 파일 inode·mtime·크기를 유지하며 다른 참조는 충돌이다. 임시/고아 파일 청소·전역 자원 취소는 아직 구현하지 않았다.
- 2055년 한 수작업 경로의 32/32/88 후보에서 Input/Header/Leaf 원문·Hash·크기와 기존 결과 Hash·
  2,080/2,080/5,720 PSI Row가 일치했다. V2→V3 비압축 저장량은 **50.807→22.781 / 50.803→22.777 / 139.085→60.455 MB**,
  실제 파일은 **3.884→2.137 / 3.884→2.150 / 10.584→5.675 MB**다. 공유 파일은 폴더마다 129개다.
  원 문서를 펼친 논리 크기는 그대로이며, 비압축 저장량 약 55~57%·실제 점유량 약 45~46%가 줄었다.
- V2/V3 검증 원문·측정 Metadata는 346,861,341 Byte, 실제 462파일·28,466,267 Byte다.
  이는 실제 비압축 중복 제거이지 압축률로 20GB 예산을 할인한 것이 아니다. 파일 수가 늘었고 복원·실제 재생도 필요하므로
  속도·메모리 개선이나 공식 전체 경로의 자원 합격을 주장하지 않는다. 기존 18회 벤치마크/이력은 그대로 보존했다.
- 새 단위 48건·계약 3건·Offline 통합 13건으로 **64건**을 추가했다. 최종 전체 회귀는
  **1,591 passed / 9 skipped / 669 subtests passed**(761.16초), 기존 Starlette 경고 3건이다.
  Ruff·Format 337개 파일·기존 JSON Schema 37개·Diff Check를 통과했다. Coverage·Bandit·Mypy는 현재 환경에 없으며,
  커버리지 수치·별도 보안 스캔·독립 에이전트 검토를 완료한 것으로 표현하지 않는다.
  Source 지문·정확한 참조·저장량·회귀·보존 결과는 [공유 입력 검증 기록](evidence/robustness-shared-inputs-20261020.json)에 보존한다.
- 승인된 이 문서와 [강건성 계약](IO_CATALOG_ROBUSTNESS_CONTRACT.md) 외에 기존 보호 파일 481개와 개인 보고서는 동일하다.
  ABC/XYZ 결과/Hash·규칙/Golden·FSN 설정·운영 Gate·DB·Platform·공용 Runtime은 변경하지 않았다.
  FSN `BLOCK`/`REVIEW`·자동 게시/발주 금지·실패 후보 전체 분모를 유지했고 공식 경로 생성·본실험·실제 발주도 수행하지 않았다.

**다음 작업 — 검증된 불변 입력의 준비·직렬화 재사용 · InventoryEngine**

- 공통 입력의 물리 중복은 줄였지만 준비·복원·직렬화는 반복된다. 정확한 입력/Build/Config Hash에 묶인 불변 정보만 재사용하고,
  판단 시점의 재고·미도착 공급·후보별 정책·운영 Gate는 다른 계산과 섞지 않는다.
- 완료 조건은 같은 결과/Hash·재생 검증 강도를 유지하면서 다른 입력의 잘못된 재사용을 차단하고 시간·메모리 효과를 확인하는 것이다.

**후속 작업 — 빠른 조회와 실제 재생 검증 분리 · InventoryEngine**

- 빠른 조회 완료와 원 입력부터의 실제 검증 완료를 별도로 표시한다. 일부 증적만 읽고 전체 재생 검증도 완료된 것처럼 표시하지 않는다.
  공유 저장/재사용 계약에 의존하므로 앞 단계 이후 직렬로 진행한다.

**후속 작업 — 전역 자원 통제와 Split 전체 조립 사전 검증 · InventoryEngine**

- 원문 20GB·호출당 3,600초 예약/취소·중단 재개·임시 파일 보존을 서비스 수준에서 연결한 뒤 전체 Bank와 예상 용량을 검증한다.
  후보·기간·경로를 줄여 통과시키지 않는다. 공식 경로 생성·본실험은 이번에 실행하지 않는다.
- 실제 DB 적재·Migration·공용 배포·Demand P12 연결은 별도 승인 범위다.

아래는 보존한 이전 이력이다. 현재 완료 범위와 다음 작업은 이 최상단 절을 우선한다.

## 2026-10-20 성능 비교 기준·V2 무손실 압축 — 로컬 구현·검증 완료

**완료 — 원문을 보존하는 압축 저장·V1 호환·18회 성능 비교 · InventoryEngine**

- 작업 브랜치는 `inventory_engine_dev`, 기준 HEAD는 `99f2f12`와 보존된 미커밋이다. 설치·배포·Commit/Push는 수행하지 않았다.
- [저장·측정 계약](IO_ROBUSTNESS_STORAGE_COMPRESSION_CONTRACT.md)과
  [고정 비교 사양](data/robustness-storage-benchmark-v1.json)에 동일 수작업 입력·32/32/88개 전체 후보·V1/V2·각 3회 기준을 고정했다.
  준비·계산·게시 전 재생 검증·저장·무결성 조회·재생 검증 시간과 원문/물리 크기·프로세스 최대 RSS를 구분한다.
  OS 캐시를 비운 실험이나 순간 최대 임시 디스크 측정은 아니다.
- [선택 가능한 V2 저장기](../../src/dsio_inventory_engine/infrastructure/robustness_compression.py)를 기존 영속 조립기에 연결했다.
  Gzip level 1·코덱/버전·원문 크기/Hash·압축 파일 크기/Hash를 봉인 참조에 포함하며, 기존 V1 Writer와 기본 Runtime은 바꾸지 않는다.
  V1 완성 결과는 수정 없이 읽고, 부분 V1 실행은 기존 조각을 보존한 채 V2로 재개한다.
- 복원 크기를 JSON 해석 전에 제한한다. 잘린 프레임·CRC·추가 Member/Trailing Byte·Hash/건수/누락·비정규 참조를 차단한다.
  같은 참조는 멱등 처리하고, 유효한 기존 압축 파일은 재압축하지 않는다. 다른 참조는 충돌로 차단한다.
  모든 후보·원 PSI·행동·Guard·평가를 남기며, 저장된 Leaf의 실제 재생 검증도 유지한다.
- 2055년 수작업 한 경로의 18회 비교가 모두 완료됐다. 입력·부모 분류·Catalog·결과·Leaf Hash와
  2,080/2,080/5,720 PSI Row가 두 저장 방식·세 반복에서 같았다. 파일 점유량은
  ABC·XYZ 50.805→3.884 MB / LEGACY_STYLE 50.801→3.884 MB / 참조 88개 139.081→10.584 MB로 약 **92.4% 감소**했다.
- 저장 중앙값은 약 3.1~9.1% 늘었다. 속도·메모리 개선은 일관되지 않았고, ABC·XYZ/V2 첫 반복의 높은 RSS도 제외하지 않았다.
  따라서 압축을 수학적 전략의 계산 속도 개선으로 표현하지 않는다. 원문 20GB·파일당 128MB 상한은 압축으로 할인하지 않는다.
  측정 Metadata까지 원문 예산 1,444,270,123 Byte, 실제 230파일·777,238,696 Byte다.
  공식 전체 경로의 자원 합격을 뜻하지 않으며 전역 자원 예약/시간 취소·청소 정책은 미구현이다.
- 새 단위 48건·계약 4건·Offline 통합 10건으로 **62건**을 추가했다. 최종 전체 회귀는
  **1,527 passed / 9 skipped / 669 subtests passed**(529.72초), 기존 Starlette 경고 3건이다.
  Ruff·Format 330개 파일·기존 JSON Schema 37개·Diff Check를 통과했다. Coverage·Bandit·Mypy·독립 에이전트 검토를 수행한 수치는 아니다.
  세부 측정값·파일 지문·보존 검증은 [압축 저장·성능 비교 기록](evidence/robustness-storage-compression-20261020.json)에 보존한다.
- 승인된 이 문서와 [강건성 계약](IO_CATALOG_ROBUSTNESS_CONTRACT.md) 외에 기존 보호 파일 471개와 개인 보고서는 동일하다.
  기존 계산 코드·ABC/XYZ 결과/Hash·규칙/Golden·FSN 설정·운영 Gate·DB·Platform·공용 Runtime은 변경하지 않았다.
  공식 경로 생성·본실험·실제 발주도 수행하지 않았다. 무손실/V1 호환과 음성 조건 검증 기준을 구현·회귀에 반영했다.

**다음 작업 — 공통 입력의 중복을 줄이는 참조 계약 · InventoryEngine**

- 부모 분류·Calendar·Master·고정 Forecast를 매 후보에 반복 저장하지 않는 구조를 정한다.
  원 관측·행동·전체 PSI를 누락 없이 다시 구성하고 기존 논리 Hash·구버전 읽기 기준을 어떻게 보존할지 먼저 확정한다.
- 완료 조건은 압축을 풀어도 반복되는 원문 중복을 줄이면서 같은 결과를 재검증할 수 있는 것이다.

**후속 작업 — 반복 처리 재사용과 조회·재생 검증 분리 · InventoryEngine**

- 검증된 동일 입력의 준비·직렬화를 재사용한다. 입력/Build/Hash가 바뀌면 재사용하지 않으며,
  빠른 결과 조회와 원 입력부터의 실제 재생 검증을 별도 상태로 표시한다.
- 앞 단계와 공통 계약을 공유하므로 직렬로 진행한다. 완료 조건은 검증을 약화하지 않고 시간·메모리 개선을 같은 기준으로 확인하는 것이다.

**후속 작업 — 전역 자원 통제와 Split 전체 조립 사전 검증 · InventoryEngine**

- 전체 20GB 원문 예산·호출당 3,600초·중단/재개·임시 파일 보존을 서비스 수준에서 연결한다.
  전체 경로의 완전한 Bank와 예상 용량을 확인한 뒤 공식 입력 봉인·선정·검증·Holdout/Stress로 이어간다.
- 이번에는 공식 경로 생성과 본실험을 실행하지 않는다. 실제 DB 적재·Migration·공용 배포·Demand P12 연결은 별도 승인 범위다.

아래는 보존한 이전 이력이다. 현재 완료 범위와 다음 작업은 이 최상단 절을 우선한다.

## 2026-10-19 분할 결과의 영속 저장·복구 — 로컬 구현·검증 완료

**완료 — 입력·전체 후보·분할 결과를 같은 Hash로 저장·복구 · InventoryEngine**

- 작업 브랜치는 `inventory_engine_dev`, 기준 HEAD는 `99f2f12`와 보존된 미커밋이다. Commit/Push·설치·배포는 수행하지 않았다.
- [로컬 저장기](../../src/dsio_inventory_engine/infrastructure/robustness_artifacts.py)와
  [영속 실행 조립기](../../src/dsio_inventory_engine/evaluate_replenishment/robustness/durable.py)를 추가했다.
  봉인 입력 → 최대 16후보 Leaf·Checkpoint → 결과 Header → 전체 후보 Manifest → 완료 표시 순서로 저장한다.
  한 파일의 128MB 제한과 기존 작업 상한을 유지하며, 기존 Flat Reader를 변경하지 않는다.
- 같은 참조·같은 Hash는 기존 파일을 덮지 않는다. 다른 Hash의 재사용은 충돌로 차단한다.
  Manifest는 전체 후보 ID·각 실행/Leaf Hash·성공/실패 수·PSI Row 수와 부모 분류·Catalog·입력 Binding을 보존한다.
  누락·중복·순서 변경·재봉인한 가짜 건수/Hash도 실제 재생 검증과 다르면 거부한다.
- 완료 표시 전 중단은 봉인 입력의 정확한 Hash로 재개한다. 이미 저장된 Leaf도 실제 Catalog·전략·Guard·PSI로 재검증하며,
  아직 저장되지 않은 Leaf를 계산한다. 계산 없는 복구나 전체 메모리 Streaming을 구현했다는 의미는 아니다.
  완료 후 파일 손상·누락은 조회/자동 복구를 차단하고, 명시적 검증 Write로 누락된 동일 원문을 복원할 수 있다.
  손상된 기존 파일을 조용히 덮어쓰지는 않는다.
- 원 계산 결과의 Hash와 순수 계산 플래그는 보존한다. 별도 Receipt의 `durable_local_artifact=true`가 로컬 저장 완료를 나타낸다.
  운영 승인·자동 게시·자동 발주·DB Write 권한으로 전환하지 않는다. FSN BLOCK·REVIEW는 그대로 적용한다.
- 새 저장기 단위 28건·Offline 통합 35건으로 **63건**을 추가했다. 실제 32/88후보 저장·조회와
  ABC/XYZ 전체 목록 보존, 전체/일부 후보 실패, FSN BLOCK·REVIEW, Hash·건수·누락·멱등·실제 프로세스 중단 복구를 검증했다.
  88후보는 6개 Leaf·5,720 PSI Row이며 Manifest 21,899 Byte와 각 128MB 이하 파일로 보존된다.
- 최종 전체 회귀는 **1,465 passed / 9 skipped / 669 subtests passed**(430.34초), 기존 Starlette 경고 3건이다.
  Ruff·Format 323개 파일·기존 JSON Schema 37개·Diff Check·두 문서의 로컬 링크 81개를 확인했다.
  두 승인 문서와 공통 실행 단계로 분리한 `item_execution.py` 외에 기존 보호 파일 464개와 개인 보고서는 동일하다.
  Coverage·Bandit·Mypy 수치나 독립 에이전트 검토를 완료했다고 주장하지 않는다. 세부 결과와 파일 지문은
  [분할 저장·복구 검증 기록](evidence/catalog-robustness-durable-recovery-20261019.json)에 기록한다.

**다음 작업 — 자원 사전 검증과 Split 전체 조립 준비 · InventoryEngine**

- 한 품목·제공된 한 경로의 저장/복구 이후, 별도 수작업 Smoke로 시간·파일 크기·전체 20GB 예산을 확인한다.
  호출당 3,600초·전역 저장 예산 통제와 여러 경로의 완전한 Bank 조립은 아직 미구현이다. 두 작업은 자원 기준 확정 후 직렬 연결한다.
- 공식 경로 생성·본실험은 이번에 실행하지 않는다. 독립 IO 검증 이후의 실제 DB 적재·Migration·공용 Runtime 배포·Demand P12 연결은 별도 범위다.
  이번에는 FSN 설정·운영 Gate·DB·Platform·Runtime·기존 규칙/Golden/개인 보고서를 변경하지 않는다.

아래는 보존한 이전 이력이다. 현재 완료 범위와 다음 작업은 이 최상단 절을 우선한다.

## 2026-10-19 부모 분류를 보존한 품목별 Catalog 실행 조립 — 로컬 구현 완료

**완료 — 전체 후보를 실제 Guard·PSI·평가기까지 연결 · InventoryEngine**

- 작업 브랜치는 `inventory_engine_dev`, 기준 HEAD는 `99f2f12`와 보존된 미커밋이다. 설치·배포·Commit/Push 완료를 뜻하지 않는다.
- 새 품목 조립기는 부모 분류 원문·ID·Hash·전체 모집단과 전체 Catalog를 보존한다. 한 품목의 Canonical 입력과
  원 Effective Policy V2를 따로 대사하며, 잘라낸 분류에 부모 Hash를 붙이거나 품목별 ABC 순위를 재산출하지 않는다.
- ABC·XYZ 32개 / LEGACY_STYLE 32개 / 참조 88개를 실제 `CandidateStrategy`·공통 Guard·관측 분리 PSI로 계산한다.
  수작업 경로당 65주를 연속 실행하며 16후보 이하의 메모리 Leaf와 전체 후보·Leaf Hash 목록을 만든다.
  88개 작업량은 188,760으로 기존 200,000 상한을 유지한다. Leaf는 128MB 이하이며 Durable 저장·재시작은 아직 없다.
- 실제 Row·수락 주문·입고에서 52주/추가 13주·중간 시점·서비스·고객 BO·5종 비용을 재계산한다.
  경로별 평가까지만 연결했으며, Split 전체 집계·정책 선정·공식 비교를 수행한 것으로 표시하지 않는다.
- 실패 후보도 전체 목록에 남긴다. 공통 실행기가 감싼 `STRATEGY_EXECUTION_FAILED`와 판단 ID·전략 증거를 보존한다.
  Source/Scope/Hash/계약 불일치는 후보 실패로 낮추지 않고 실행 전체를 중단한다.
- Verifier는 원 입력에서 실제 Catalog·전략·Guard·PSI·비용을 재실행한다. 후보/Leaf 누락, Guard 수량·비용·충족률 위조와
  산술상 유효한 HOLD 전용 가짜 전략도 재봉인 Hash만으로 통과하지 못한다. FSN BLOCK·REVIEW·자동 게시/발주 금지는 유지한다.
- 수작업 입력은 2055-01-04의 합성 개발 사례다. 운영 Source 승인이나 V100/P12 차단 해소가 아니다.
  공식 기간/Seed·규칙·기존 Golden과 ABC·XYZ 결과/Hash, FSN·운영 Gate·DB·Platform·Runtime은 변경하지 않았다.
  검증 수치와 파일 지문은 [품목별 실행 검증 기록](evidence/catalog-robustness-item-execution-20261019.json)에 기록한다.
- 새 단위 28건·Offline 통합 23건으로 **51 passed**다. 128MB보다 큰 88후보 부모 결과도 Leaf별 검증·전체 재실행을 통과했다.
  최종 전체 회귀는 **1,402 passed / 9 skipped / 669 subtests passed**(304.79초), 기존 Starlette 경고 3건이다.
  Ruff·Format 318개 파일·기존 JSON Schema 37개·Diff Check·두 문서의 로컬 링크 72개를 확인했다.
  승인된 두 문서를 제외한 기존 보호 파일 459개와 개인 보고서의 Hash는 모두 동일하다.

**다음 작업 — 분할 결과의 영속 저장·재시작 복구 · InventoryEngine**

- 현재 메모리 Leaf와 원 입력 Binding을 Append-only Writer/Reader에 연결한다. 전체 32/32/88개 기대 후보·Leaf Hash·건수를
  검증하고 저장 실패·누락·동일 Hash 재요청·다른 Hash 충돌·중단 후 재개를 Offline으로 확인한다.
- 완료 조건은 일부 후보를 버리거나 다시 분류하지 않고 같은 봉인 입력으로 전체 결과를 복구하는 것이다. 현재 조립 이후 직렬로 진행한다.

**후속 작업 — 자원 사전 검증 후 공식 입력 봉인 · InventoryEngine**

- 별도 수작업 Smoke로 파일 크기·계산시간·전체 20GB 예산을 검증한 뒤 공식 경로 Producer와 Split 전체 조립을 연결한다.
  공식 생성·선정·선택 Hash 고정·검증·Holdout/Stress는 이후 순서다. 이번에는 공식 경로 생성과 본실험을 실행하지 않았다.
- 실제 DB 적재·Migration·공용 Runtime 배포는 별도 승인 대상이다. 독립 IO 저장 검증 후 Demand P12 연결을 진행한다.

아래는 보존한 이전 이력이며, 현재 상태와 다음 작업은 이 최상단 절을 우선한다.

## 2026-10-19 순수 강건성 평가기·독립 Source·관측 분리 — 로컬 구현 완료

- [순수 평가기 검증 기록](evidence/catalog-robustness-pure-evaluator-20261019.json)의 결과를 반영했다.
  52주 본평가·13주 추가 관찰·동일 경로의 13/26/52주 지표, 5종 비용 재계산,
  무수요/고객 BO/기술 실패 구분, 단일 Forecast·8경로 위험 선정과 재선정 차단을 구현했다.
- 순수 평가기 구현 당시 전체 회귀는 **1,300 passed / 9 skipped / 669 subtests passed**, 기존 Starlette 경고 3건이다.
  새 평가기 수작업 테스트 84건과 기존 사양·Hash 호환성의 집중 검증은 113건 통과했다.
  Ruff·Format 307개 파일·기존 JSON Schema 37개·Diff Check를 통과했고 보호 파일 384개는 동일하다.
- 평가기는 실제 Row·주문으로 재고 산술과 비용을 재계산한다. Source/Gate의 권위를 승인하는 연결부는 아니다.
  작은 수작업 후보 집합의 검증을 공식 32/32/88개 Catalog 전체 검증으로 표현하지 않는다.
- 고정 사양 Hash와 이전 검증 기록은 보존한다. 공식 경로 생성·본실험·FSN/운영 Gate·DB·공용 Runtime은 변경하지 않았다.

**완료 — 독립 Warm-up Source와 미래 정보 분리 · InventoryEngine**

- 주어진 104주 이력에서 52주 Warm-up을 독립 계산하고 W0 재고·고객 BO·미도착 공급을 연결했다.
  생산 전략/PSI에 의존하지 않는 Generator이며 공식 난수 이력/경로를 만든 것은 아니다.
- 새 개발 전용 V2 `LOCAL_SHADOW` 수학 실행기는 전략·공통 Guard에 고정 Forecast만 전달하고,
  행동 검증 후 당주 실현 수요를 PSI에 반영한다. 기존 `run.py`·FSN·운영 Gate는 변경하지 않았다.
- 수작업 재고 이월·W0 도착 중복 방지·실제 수학적 전략의 미래 Suffix 변경 전 행동 불변을 검증했다.
  새 Warm-up 23건·Source/관측 Offline 통합 28건과 기존 평가기/호환성 집중 검증은 **164 passed**다.
  이번 최종 전체 회귀는 **1,351 passed / 9 skipped / 669 subtests passed**(195.51초), 기존 경고 3건이다.
  Ruff·Format 313개 파일·기존 JSON Schema 37개·Diff Check·두 문서의 로컬 참조를 확인했다.
  기존 보호 파일 392개는 모두 동일하며 세부 사항은
  [Source·관측 검증 기록](evidence/catalog-robustness-source-causal-20261019.json)에 기록했다.
- 주 단위 개발 LT 0~49일(7일 배수)과 DSE/C100/V101 합성 사례만 대상으로 했다. 작은 수작업 데이터로
  실제 Source 승인·공식 실험·PPO/ML 검증·운영 성능 우월성을 주장하지 않는다.

**다음 작업 — 원 분류 Projection·분할 증적·자원 사전 검증 · InventoryEngine**

- 원 분류와 전체 후보 목록을 보존하고 실제 Catalog/Guard 결과를 평가기로 전달하도록 조립한다.
  Manifest/Verifier·재시작·자원 사전 검증은 해당 입력 계약에 의존하므로 이후 직렬로 진행한다.
  공식 경로 생성과 본실험은 아직 실행하지 않는다. DB Write·공용 Runtime 배포는 별도 승인 대상이다.

아래는 보존한 이전 이력이다. 현재 작업은 이 최상단 절을 우선한다.

## 2026-10-19 강건성 비교 조건 고정 — 완료 / 구현·공식 실행 미수행

- [강건성 계약](IO_CATALOG_ROBUSTNESS_CONTRACT.md)과
  [기계 판독 사양](data/catalog-robustness-protocol-v1.json)에 새 가정·기간·Seed·판정 기준을 고정했다.
  사양 Hash는 `a3e35fcf23915e87823883ea8c1f09448b245ae7ea1fffa8c77482bab0433280`이다.
  새 공식 이력·경로·BOH를 생성하거나 52주 실험을 실행한 것은 아니다.
- 가상 W0는 2037-01-05 / 2044-01-04, 평가 52주+추가 관찰 13주다. 전 104주 합성 이력 중
  52주 Warm-up의 EOH·고객 BO·미도착 주문을 W0 Source로 잇는다. 안정 LT49·간헐 LT7/49일 세 품목이다.
  이전 Holdout과 2040 기술 Smoke의 이력까지 피했고, 간헐 두 품목은 같은 수요로 LT 효과를 분리한다.
- 후보 32/32/88개는 유지하며 단일 Forecast 선정과 8경로 위험 선정을 별도 실험 결과로 비교한다.
  검증 8경로·최종 16경로에서는 재선정하지 않는다. 경험 성공비율≥90%와 나쁜 10% 경로의 평균 부족률≤10%를
  적용하고 경로별 충족률≥95%·종료 고객 BO=0·기존 Gate를 검사한다. 운영 신뢰도 보장을 의미하지 않는다.
- 가격 연동 PRICE_BASE를 주 비용 가정으로 하고 고정비 호환·높은 보관비/발주비·Forecast 부족 무과금의
  5개 Profile을 분리했다. 부족 비용은 개발 가정이며 기존 고객 BO/PSI/Source 총비용을 덮거나 이중 합산하지 않는다.
- 기존 13주 Runner의 상한은 바꾸지 않는다. 새 전용 조립에서 품목별 65주 작업량은 188,760으로 기존 200,000 이하다.
  원 분류 모집단·부모 Hash 보존, 미래 Truth 관측 분리, Window/위험/비용 Scorecard,
  16후보 이하 Leaf·전체 후보 Manifest·자원 Preflight·동일 입력 재개는 **아직 구현 필요**하다.
- 조건 확정·수작업 산술 검증과 기존 Hash 호환성만 확인한다. 직전 전체 회귀 1,191건은 아래 완료 이력이며,
  이번에 새 강건성 PSI를 실행한 수치로 표현하지 않는다. 기존 Engine 코드·규칙·Golden/이전 실험은 수정하지 않는다.
- 이번 검증은 새 정적 조건/산술 25건·기존 Hash 호환성 4건·이전 비교 Offline 회귀 16건으로 **45 passed**다.
  Ruff·Format 300개 파일·JSON Schema 37개·새 문서 링크·Diff Check를 확인했다.
  기존 코드/스크립트/테스트/Schema/기존 사양·근거와 개인 보고서를 포함한 보호 파일 **381개**의 SHA-256이 동일하다.
  전체 회귀·새 52주 PSI·HTTP/DB E2E를 다시 실행한 수치가 아니며 세부 범위는
  [조건 검증 기록](evidence/catalog-robustness-contract-20261019.json)에 보존한다.
- FSN 설정·운영 Gate·DB 접속/Write·승인 설정·Platform·Runtime·실제 발주·Commit/Push는 변경/수행하지 않았다.
  개인 보고서·환경·기존 미커밋은 보존한다. 스킬의 일부 공통 참조는 미설치여서 저장소 계약·QA 절차로 보완했다.

**다음 작업 — 평가기·Generator/관측 계약 구현 · InventoryEngine**

- 52주 Window·5성분 비용·경로 위험·두 선정기를 수작업 사례로 검증한다. 독립 Warm-up/경로 Producer와
  미래 Truth 관측 차단은 계약 고정 후 별도 파일에서 병렬 검증 가능하다. 현재 세션의 조립은 직렬로 이어간다.
- 완료 조건: 누락·무수요·고객 BO·심한 단일 경로 실패·기술 실패를 구분하고 기존 결과/Hash·FSN/Gate를 보존한다.

**그다음 — 품목별 분할·봉인 증적·기술 Preflight 후 공식 비교 · InventoryEngine**

- 원 분류→품목 Projection·Chunk Manifest/Verifier·멱등 재시작을 구현한다. 별도 2055 Smoke로 크기/상한을
  검증한 뒤에만 공식 입력을 봉인한다. 기존 상한을 늘리거나 기간·Seed·후보를 줄여 통과시키지 않는다.
- 선정→선택 Hash 봉인→검증→Holdout/Stress→근거 작성은 직렬이다. 이번 조건 확정만으로 공식 실행 완료를 선언하지 않는다.

**승인 필요 — 독립 DB 저장·복구 후 Demand P12 연결 · InventoryEngine·Platform·개발 PostgreSQL**

- 승인 원본과 실제 소량 Write 범위를 별도로 확인한 뒤 진행한다. Migration·업무 DB Write·공용 Runtime 배포는
  이번 작업의 범위가 아니며, Forecast 추가 실행으로 IO 독립 검증을 대체하지 않는다.

아래는 이전 완료 이력이며, 현재 다음 작업은 이 최상단 절을 우선한다.

## 2026-10-19 새 Holdout의 32·32·88개 후보 독립 비교 — 완료

- [비교 조건·결과](IO_CATALOG_HOLDOUT_COMPARISON.md)와
  [기계 판독 프로토콜](data/catalog-holdout-comparison-v1.json)을 결과 생성 전에 고정했다.
  기존 실험과 겹치지 않는 2033/2034년 가상 Calendar·새 Seed의 합성 2창, 각 4품목·13주다.
  실제 V100/P12 Source나 미래 예측이 아니며 ML/PPO를 새로 학습한 실험도 아니다.
- 동일 입력·비용·제약·종료 조건에서 ABC·XYZ 32 / LEGACY_STYLE 32 / 참조 88개를 실제 공통
  수학 전략·Guard·PSI로 실행했다. 원 PSI 전체 재실행 검증·선정을 포함해 순서를 회전하며 3회 측정했고
  반복 Hash·공유 정책 Row/지표가 일치했다. 계획 정책을 모두 고정한 후 새 수요에서만 평가했다.
- 세 방법 모두 실제 서비스 자격은 **6/8**이다. 전체 수량 충족률은 **93.11% / 87.61% / 87.61%**,
  공통 적격 6건 비용은 **3,898 / 4,713 / 4,013 USD**, 평균 EOH 합계는 **221.77 / 270.23 / 229.85 EA**다.
  두 기간의 계산/재검증/선정 중앙값 합은 **5.481 / 5.603 / 15.186초**다. 저장/복구 시간은 별도다.
- 계획 좋은 후보 누락은 **4/8 / 3/8 / 0/8**, 새 수요의 참조 좋은 후보 범위 누락은 **1/8 / 5/8 / 0/8**이다.
  세 방법 모두 간헐·고가의 2창에서 서비스 미달이며 참조에도 사후 적격 후보는 있었다.
  후보 범위만 넓히는 것으로 해결되지 않았고 계획 최저 비용 후보와 미래 성과를 구분해야 한다.
- 이번 조건에서는 ABC가 더 나았지만 합성 8건으로 일반적 우월성·기본 전략 채택을 확정하지 않는다.
  비용은 단가 비례가 아니고, 기간은 13주·종료 잔존가치가 없어 장기 LT·고가품 해석에 한계가 있다.
  Legacy 원 엔진과의 동등성·운영 성능 합격·FSN/미분류 차단 해소로 표현하지 않는다.
- 원 입력을 실행 전에 고정하고 원 PSI 8개·선정 6개의 Append-only 저장·멱등 재요청·재시작
  원 입력 재검증을 완료했다. **14개 / 291,700,986 bytes**의 원본은 로컬 임시 폴더이며 중앙 보관은 아니다.
  24개 품목·방법·기간 지표·원인·포인터와 파일 지문은
  [검증 기록](evidence/catalog-holdout-comparison-20261019.json)에 보존했다.
- 결과 의미 Hash는 `bb2b9a5876949f864980dc4669ab3099cd90bfb11e96a292dff958e4e8634208`이다.
  기존 Engine 코드/테스트/Schema/규칙 파일은 수정하지 않고 새 비교 경로를 추가했다.
  최종 전체 회귀 **1,191 passed / 9 skipped / 669 subtests passed**(182.57초), 새 비교 경로 16건과
  기존 호환성 4건, Ruff·Format **299개 파일**·JSON Schema **37개**·Diff Check를 통과했다.
  보호 파일 **350개 모두**와 개인 보고서는 작업 전후 SHA-256이 동일하다. 기존 Platform 경로의
  Starlette 경고 3건은 유지되며, 커버리지 비율이나 별도 에이전트 검토를 수행한 수치로 표현하지 않는다.
- 개발 PostgreSQL 접속/Write·FSN 설정·운영 Gate·Platform·Runtime·실제 발주·Commit/Push는 없다.
  개인 보고서·환경·기존 미커밋을 보존했다. 실행 기준은 `inventory_engine_dev`의 `99f2f12`와
  검증 기록의 미커밋 구현 Manifest이며 설치/배포 완료 Commit으로 표현하지 않는다.

**다음 작업 — 간헐수요·장기 LT의 별도 강건성 비교 조건 확정 · InventoryEngine**

- 이번 결과는 고정한다. 다음 실험의 장기 평가기간·새 수요 경로·가격/비용 가정을 먼저 정하고,
  단일 평균 Forecast의 최소 비용 선정과 여러 수요 경로에서의 서비스 위험 평가를 비교한다.
- 후보 범위 부족과 선정 기준의 과적합을 분리하는 것이 완료 조건이다. ABC/Legacy 채택이나 운영 Gate는
  이번 소표본만으로 바꾸지 않는다. 조건→구현/검증→평가는 직렬이며 원 Legacy 자료 확보는 병행 가능하다.

**후속 작업 / 승인 필요 — IO 독립 저장 검증 후 Demand 연결 · InventoryEngine·Platform·개발 PostgreSQL**

- 승인된 정확한 원본·소량 저장/재조회/복구 범위를 먼저 준비한다. 이후 승인받아 실제 DB 검증을 진행한다.
  IO 독립 검증 후 P12 한 건의 정식 Demand 연결로 진행하며 Forecast 추가 실행으로 대체하지 않는다.
  Migration·실제 DB Write·공용 Runtime 배포는 별도 승인 대상이다.

아래는 보존된 이전 이력이며, 현재 상태와 다음 작업은 이 최상단 절을 우선한다.

## 2026-10-19 Catalog 후보의 품목별 선정·평가 연결 — 로컬 구현 완료

- [Catalog 선정·평가 계약](IO_CATALOG_POLICY_SELECTION.md)에 별도 결과
  `inventory-offline-catalog-policy-selection-result / 1.0.0`을 정의했다. 원 Config/분류/Canonical/
  Mathematical 입력과 Catalog로 실제 수학 전략·Guard·PSI·지표를 재실행해 원 PSI 전체를 검증한다.
- 성공 후보는 공통 Guard·재고 이월·서비스·비용도 추가 대사하고 기존 서비스 자격→총비용→평균재고→
  주문 횟수→Candidate ID 기준으로 품목별 선정한다. 후보 누락·성공→실패 위조·비용/Gate 변조는
  Hash를 재생성해도 거부한다. 미선정/실패/제외·REVIEW 사유와 Baseline 대비 변화를 보존한다.
- 별도 로컬 Source/Selection Writer는 부모 우선 Append-only 저장·멱등 재요청·재시작 복구를 지원한다.
  정확한 입력을 다시 제공해야 하며 최신 Source 조회·DB 연결·실제 운영 승인으로 대체하지 않는다.
- 기존 ABC 순위 함수는 기본 `PC-`를 유지하고 신규 경로에서만 `OCCP-`를 명시한다.
  ABC 분류·Config·기존 탐색/선정 및 Catalog PSI의 기존 Hash, 규칙/Golden·FSN·운영 Gate는 유지했다.
- 수작업 사례는 BOH 10·주수요 10·LT 7일·ROP 10·Q 11·S 21이다. 서비스 미달인 28 USD 후보를 제외하고
  30/30 충족·EOH 0/1/1·주문 11+10인 54 USD 후보를 선정한다. Legacy EOQ 동등성 검증이 아니다.
- 개발 PostgreSQL 접속/업무 Write·Migration·입력 승인·공용 Runtime 배포·실제 발주·Commit/Push는 수행하지 않았다.
  개인 보고서·환경·기존 미커밋을 보존하며 설치된 Platform 테스트 환경을 사용한다.
- 신규 검증 **61개**, 전체 Unit·Contract·Offline Integration **1,175 passed / 9 skipped / 669 subtests passed**다.
  변경하지 않은 Platform 경로의 Starlette 경고 3건은 남아 있다. 전체 Ruff·Format **293개 파일**,
  Schema 필드/사유 일치·양성/음성 사례·Diff Check와 Hash Seed 2개를 확인했다.
- 보호 파일 343개 중 기존 공통 순위 함수 1개만 변경했고 나머지 342개·규칙/Golden·개인 보고서는 동일하다.
  커버리지 비율은 도구 미설치로 측정하지 않았다. 개발 PostgreSQL 대신 테스트 임시 저장소만 사용했으며,
  [검증 기록](evidence/catalog-policy-selection-verification-20261019.json)에 범위와 고정 Hash를 보존했다.

**다음 작업 — 같은 조건의 3개 후보 모드 독립 비교 · InventoryEngine**

- ABC·XYZ 32개/LEGACY_STYLE 32개/넓은 참조 88개의 입력·기간·비용·제약·종료 조건을 먼저 고정한다.
  이어 새 미사용 기간에서 서비스·비용·재고·실행/재검증 시간·좋은 후보 누락을 비교한다.
- 조건 고정→실험 실행→선정/평가는 직렬이다. 현재 기능 테스트를 성과 Holdout으로 재사용하지 않는다.
  Legacy 원본 확보는 개발 비교와 독립적으로 진행할 수 있다.

**후속 작업 / 승인 필요 — IO 독립 저장 검증 후 Demand 연결 · InventoryEngine·Platform·개발 PostgreSQL**

- 독립 계산/조회·로컬 복구의 기준선을 고정한 뒤 실제 DB 입력·소량 적재/재조회/복구 범위를 승인받아 검증한다.
  그 다음 P12 한 건의 Demand→IO 연결로 진행한다. 공용 배포·Forecast 재실행으로 대체하지 않는다.

아래 용어 대사와 Source/Schema 절은 완료 이력이다. 각 절의 과거 다음 작업보다 이 절의 순서를 우선한다.

## 2026-10-19 MOS·ROP·Q·서비스 지표 대사 — 완료 / Legacy 생성식 동등성 미확인

- [용어 대사](IO_POLICY_TERMINOLOGY_RECONCILIATION.md)에 Legacy 파일/제공 SQL·보존 DDL·업무 설명과
  현재 Python 구현·개발 가정을 분리했다. 원본 Rop은 10,000회 일 수요 분포의 분위수,
  Python 탐색은 주 수요 정규분포식과 월 커버리지 개발 프로필이다. 같은 계산으로 표현하지 않는다.
- Legacy MOS는 `RMOS+EMOS` 적재까지만 확인했다. 현재 요청 EMOS는 기본 Q 커버리지,
  유효 EMOS는 `(S−ROP)/Dm`이며 Q/2·실제 주문량과 다르다. 정책 중간점 MOS와 PSI 관측 MOS도 구분했다.
- CSL 설정값·현재 수요의 수량 충족률·주차 무결품 비율·서비스 적격 조건을 대조했다.
  Result Bundle의 `projected_service_level`도 수량 충족률이며 확률적 CSL이나 일별 납기 실적이 아니다.
  Legacy F_RATE 생성식과 RMOS/EMOS 상위 생성 프로시저는 미확인으로 유지했다.
- 기존 탐색 문서의 주기적 목표량 수식을 실제 코드의 안전재고→목표량 순차 올림으로 정정했다.
  계산 코드·Schema·Golden·후보·설정·기존 Hash·FSN/운영 Gate는 변경하지 않았다.
  DB 접속/적용/Write·Runtime 실행/배포·실제 발주·Commit/Push는 수행하지 않았다.
- 원문 Hash·코드 위치와 이번 로컬 검증은
  [근거 Register](evidence/policy-terminology-reconciliation-20261019.json)에 기록한다.
- 관련 17개 테스트 파일 **311 passed / 204 subtests passed**, 단위·서비스 메모리 확인 8건,
  원문/코드 Hash 14개·근거 위치 33곳·로컬 링크 72개를 검증했다. 코드/테스트/Schema 337개와
  개인 보고서는 작업 전후 동일하다. 전체 회귀·실제 DB E2E를 다시 수행한 수치로 합산하지 않는다.

**다음 작업 — Catalog 후보의 품목별 선정·평가 연결 · InventoryEngine**

- Catalog PSI 실행은 현재 기준선으로 유지한다. 실제 후보 Row·제약·서비스·비용을 재검증하고
  별도 결과 계약으로 선정/미선정 사유를 보존한다. 기존 PSR 계약으로 위장하지 않는다.
- 다음은 같은 조건의 ABC·XYZ/LEGACY_STYLE/넓은 후보를 미사용 기간에서 독립 비교하는 것이다.
  선정 구현→관련 테스트→독립 Offline 검증은 직렬이다. Legacy 원본 자료 확보는 별도로 진행할 수 있다.

**후속 작업 / 승인 필요 — IO 독립 적재 검증 후 Demand 연결 · InventoryEngine·Platform·개발 PostgreSQL**

- 먼저 IO 독립 계산·조회·저장/복구의 완성 범위를 확인한다. 아래 원본 복구·저장 준비 이력은
  이 목표의 선행 자료이지 이번에 다시 DB를 변경하라는 뜻이 아니다.
- 실제 DB 적용·데이터 Write의 범위를 승인받아 검증한 뒤 P12 한 건의 Demand→IO 연결로 진행한다.
  Forecast 추가 실행으로 IO 계산·정책 원본의 미완료 범위를 대신하지 않는다.

## 2026-10-19 분석 저장 원본 대사 — 완료 / 실제 저장용 승인 Source 미준비

- [원본 식별 계약](IO_SEGMENTATION_SOURCE_RECONCILIATION.md)에 V100/Plan 202512/W0 2025-03-17,
  Draft 2.1 Revision·Hash, 원 분류/Canonical/봉인 Artifact 포인터를 고정했다. 목표를 202602로 바꾸지 않았다.
- Engine의 원문/Byte/Bundle/명령/정책 Projection/Canonical 검증기를 재사용했다.
  로컬 `bbdc...` 원문은 계산 전용 Artifact와 일치하지만 DB 승인 Snapshot은 아니다.
  실제 승인 V2 `e54e...`는 Config 2.0/마감 202601/W0 202602여서 목표와 맞지 않는다.
- 현재 DB의 과거 26주 마감은 충분하고 Hash는 `70d3cb63...4bf622e`다.
  기존 봉인 원문의 마감 Hash는 `240eeeca...511b3d`로 달라, FSN UNVERIFIED/BLOCK 3,622개를 그대로 유지했다.
  문서에 있던 새 후보 `29aa...`의 임시 원문 경로는 현재 없다. 문서의 ID만 승인 원문으로 사용하지 않는다.
- `selected_storage_source=null`, `database_write_allowed=false`다. Config Draft/분류 미승인·옛 마감 증적·
  Legacy Source/Projection Artifact 미준비를 구분했다. 신규 분석 Header/Item·승인 입력 묶음은 0건이다.
- Platform 읽기 전용 도구/Receipt를 추가했다. DB·기존 SQLite·원문 파일을 읽기만 했으며,
  새 분류 계산·FSN/Gate 변경·DB 적재/승인·079·Runtime/Forecast/실제 발주·Commit/Push는 없다.
- Source 대사 Helper **22 passed**, 관련 Backend 묶음 **197 passed**, Ruff·Format·Diff Check 통과.
  테스트 임시 SQLite와 실제 원본 저장소를 구분했다. 기존 Engine 계산/분류 코드는 변경하지 않았다.

**다음 작업 — 올바른 26주 원문의 로컬 복구·재생성 · InventoryEngine·Platform Backend**

- 정확한 Plan/Site/W0와 현재 마감 Source로 별도 분류 원문을 검증한다. 이후 Legacy Feature와 Source/Projection을
  봉인한다. 기존 원문을 덮어쓰거나 사라진 후보 ID를 강제로 재사용하지 않는다.

**승인 필요 — DB 입력 계약·승인과 실제 소량 Writer 저장**

- 대상: Platform·개발 PostgreSQL. 079·Config/분류 게시·승인과 실제 저장/재조회/복구는 별도 범위를 확인한다.
  순수 후보 선정·조회 준비는 Source 작업과 병행 가능하지만 DB 게시/E2E는 선행 원본 고정 후 직렬이다.

아래 Schema 적용은 완료한 기준선이며 원본 대사도 위에서 완료했다.

## 2026-10-19 분석 저장 구조 개발 PostgreSQL 적용 — 완료

- 승인된 개발 PostgreSQL `192.168.0.46:5432/dsai`에 086만 적용했다.
  `dsim.tb_io_segmentation_analysis`(31컬럼)·`tb_io_segmentation_analysis_item`(27컬럼)은 각각 **0행**이다.
  `v_io_item_analytics` **7,000행**, `v_io_item_analytics_axis` **49,000행**은 기존 분류를 읽은 결과다.
  신규 LEGACY_STYLE 분석·Plan 202512 입력이 생성된 것은 아니다.
- 실제 Rollback 제약 Canary **16개 통과** 후 dsai/dsim Schema-only Backup·Restore Catalog를 확인하고,
  DDL/원장을 원자 Commit했다. 적용 시각은 **2026-10-19 09:41:27 KST**, 원장은 `success/module=dsdm`이다.
  적용 SQL SHA-256: `0442ff7d91f981c6b637be0974fec0ffed1fa45381ae08815cfd38cf3e6717de`.
- 외부 접속 주소와 컨테이너 내부 주소 차이는 확인된 Cluster System ID로 검증하도록 보정했다.
  다른 DB를 허용하거나 서버·포트·Secret을 바꾸지 않았다. 적용 SQL의 Table/View 정의는 검토 초안과 동일하다.
- 분류 7개·설정 2개 Table의 행수/전체 행 진단 지문과 Gate 분포를 적용 전후 대사했다.
  `ALLOW=2,296/REVIEW=4/BLOCK=4,700`이며 기존 FSN 설정·Gate·ABC/Legacy 코드는 유지했다.
  새 읽기 전용 연결에서도 실제 건수·Checksum·PUBLIC 권한 차단·079 미적용을 확인했다.
- Platform `docs/backend/dsim/evidence/inventory-segmentation-086-development-20261019.json`에
  적용/독립 재조회/백업 증적을 남겼다. 백업은 로컬 `.codex-build/inventory-schema-backups/`에 보존하고 Commit에서 제외한다.
- 관련 Backend **175 passed**, 별도 Migration 목록 정적/Python **17 passed**, Ruff·Format·Diff Check 통과.
  직전 Mac 기본 Bash 셸 실행기 9건 실패는 이번 작업에서 변경하지 않았다. Engine 계산 코드는 변경하지 않았다.
- 079·다른 Evidence 초안·업무 데이터 적재·실제 Claim·Runtime 배포·발주·Commit/Push는 수행하지 않았다.
  086 개발 대상 전용 SQL은 자동 Migration 목록에 등록하지 않았다. 일반 배포·최소권한 Runtime E2E는 미완료다.

**다음 작업 — 실제 저장할 분석 원본·입력 Binding 확정 · InventoryEngine·Platform Backend**

- 정확한 분류/Config/Plan·Site·W0와 봉인 Feature/Legacy Artifact를 고정한다. 기존 View 행이 신규 Plan 결과가
  아님을 구분한다. FSN/Gate는 유지하며 Source 대사와 Catalog 선정·조회 준비는 독립 파일에서 병행 가능하다.

**승인 필요 — 소규모 분석 데이터 저장·재조회·복구 · 개발 PostgreSQL·InventoryEngine·Platform**

- 원본이 고정되면 Writer로 실제 소량 저장/멱등 재요청/프로세스 복구를 검증한다.
  이번 Schema Canary와 달리 업무 분석 행을 남기는 통합 Write이므로 범위를 별도로 확인한다.

**후속 작업 — Catalog 선정·Run/Studio 연결 및 변경 고정**

- 대상: InventoryEngine·Platform Backend/Frontend. 실제 후보 PSI의 선정/평가와 분석 포인터를 연결한다.
  검증 후 `inventory_engine_dev`와 `codex/inventory-psi-strategy-v2`의 관련 변경만 Commit 후보로 정리한다.
  Commit·Push는 요청 시 진행하며 개인 보고서·환경·임시 산출물은 제외한다.

아래 접속 차단과 로컬 구현 절은 앞선 이력이며 다음 실행 대상으로 다시 잡지 않는다.

## 2026-10-19 분석 저장 구조 Notion 정리·개발 DB 적용 시도 — 당시 접속 차단 이력

- Modified Table Notion에 공통 분석 View 2개와 LEGACY_STYLE Header/Item Table 2개의
  원본 매핑·컬럼·Grain·PK/FK·Hash·저장/복구·권한 경계를 추가하고 재조회했다.
- 사용자가 해당 개발 Schema 적용을 승인했다. 개발 PostgreSQL `192.168.0.46:5432/dsai`에
  연결을 시도했지만 연결 거부가 반복됐다. 로컬 경로는 확인했으나 DB 인증·원장 조회까지 도달하지 못했다.
  원인이 DB 프로세스/컨테이너/바인딩/접근 정책 중 무엇인지는 미확정이다.
- Platform에 개발 DB/PG15 Guard가 있는 `drafts/086_dsim_inventory_segmentation_analytics_v1.sql`과
  전용 적용 도구를 준비했다. 기본 읽기 전용 Preflight, Rollback Canary, Schema-only Backup,
  DDL/원장 원자 적용 모드를 구분했다. 번호 충돌과 실제 제약/권한은 접속 복구 후 확인한다.
- 실제 DB 적용·Canary·업무 데이터 적재·Runtime 배포·발주·Commit/Push는 수행하지 않았다.
  기존 FSN/운영 Gate·ABC/Legacy Class·규칙/Hash와 개인 보고서·기존 미커밋은 유지했다.
- Platform 추가 적용 후보 11개를 포함한 관련 회귀 **169 passed**, 목록 정적/Python 검사 **17 passed**다.
  전체 목록 검사의 Mac 기본 Bash `declare -A` 미지원 실패 9개는 별도 환경 문제로 기록했다.
  신규 Ruff·Format·양쪽 Diff Check 및 보호 파일 6개 지문을 확인했다. 실제 PG 검증 결과는 아니다.

**차단됨 — 개발 PostgreSQL 연결 복구 후 Schema 적용 · Platform·개발 PostgreSQL**

- 다음에 바로 할 작업은 승인된 동일 DB의 접속 확인이다. 접속 후 구조·권한·086 원장 충돌을
  대사하고 실제 Rollback 제약 검증 → 백업 → Table 2개/View 2개 적용 → 생성/원장 검증을 직렬 수행한다.
  서버 재시작·포트 변경·다른 DB로 우회 적용하지 않는다. 079/21개 Evidence 초안도 적용하지 않는다.

**다음 작업 — 분석 데이터 저장과 Run/Studio 연결 · InventoryEngine·Platform**

- Schema 생성과 별개로 원본 승인 분류·봉인 Artifact를 연결한 소규모 실제 Write/재조회/복구를 검증해야 한다.
  업무 데이터 적재와 실제 통합 Write는 별도 범위를 확인한다. 신규 Catalog 선정·평가/Run 포인터 연결 후
  공통 분석과 선택한 후보 생성 방식을 화면에서 분리해 조회한다. 순수 계산/조회 준비는 DB 복구와 병행 가능하다.

아래 저장기 구현·테스트 수치는 이 접속 시도 이전의 완료 이력이다.

## 2026-10-19 중간 분석 Artifact·2-Table 저장/검증 — 로컬 구현 완료

- [저장·복구 계약](IO_SEGMENTATION_ANALYSIS_STORAGE.md)에 원 분류 V2와 Legacy Feature/결과를
  봉인하는 별도 Source/Projection Artifact, Header/Item 컬럼·Hash·멱등 트랜잭션을 정리했다.
- `dsim.tb_io_segmentation_analysis`와 `dsim.tb_io_segmentation_analysis_item`은 Header 1건과
  명시적 Universe 전체를 저장한다. 공통 7축은 기존 dsai 분류를 재사용하고 새 Table에 복제하지 않는다.
- 원 분류의 기존 검증·UUID/Hash·7축/정책을 재사용한다. Feature/Legacy를 원 입력에서 다시 검증하고
  Projection을 재생성한다. 새 Hash만 붙인 원문/행/건수/계약/포인터 변조는 거부한다.
- 기존 Artifact 저장기의 `commit_artifacts()`로 Source/Projection을 원자 저장한다. 기존 PSI Outbox나
  새 Run Claim·정책 승인·Effective Run CAS를 생성하지 않는다. 자동 게시/발주는 항상 false다.
- 별도 Writer는 승인 원 Header/품목 존재·Scope/Config/Hash를 대사하고 Header/Item을 한 트랜잭션에 저장한다.
  같은 결과는 전체 재조회 후 멱등 반환하며, 누락/추가/다른 값은 덮어쓰지 않는다. 최대 500행 다중 VALUES다.
- 신규 Engine Offline **31 passed**: FSN BLOCK/REVIEW 불변, Source/Projection/Byte Hash 변조,
  건수·누락·추가, Artifact 원자성, DB Rollback·Commit 응답 유실·재시작·동시 Offline 멱등 복구를 확인했다.
- Engine 전체 Unit·Contract·Offline Integration **1,096 passed / 34 skipped / 655 subtests passed**,
  기존 Starlette 경고 1건이다. Platform 관련 묶음은 임시 `pglast 8.7` 환경에서 **158 passed**다.
  PostgreSQL/PLpgSQL Parser 검사는 실제 PG의 FK/Trigger/Role/Lock/실행계획 검증이 아니다.
- 후속 포인터 방어/서식 정리 뒤 관련 Engine 회귀 **104 passed**, 전체 Ruff·Format **287파일**,
  보호 코드/개인 보고서 6개 지문과 양쪽 Diff Check를 재확인했다.
- Platform SQL은 미배정·자동 실행 목록 미등록·명시적 검토 Guard·항상 Rollback인 초안이다.
  Backend 조립은 선택 설치 Engine을 사용하고 DB Adapter의 `allow_draft_writes`는 기본 false다.
- **FSN 설정·운영 Gate·원 ABC/Legacy Class·규칙/Golden·Config/Run Schema는 변경하지 않았다.**
  실제 DB 접속/적용·업무 데이터 Write·입력 승인·Runtime 배포·발주·Commit/Push는 없다.
  기존 개발 Python 환경을 교체하지 않고 임시 Target에만 Parser를 설치했다. 개인 보고서·기존 미커밋은 보존했다.

**다음 작업 — 새 Catalog 결과의 선정·영속 및 분석 포인터 연결 · InventoryEngine·Platform**

- 후보 원문/실제 PSI 검증을 유지하며 선정·평가 Artifact를 연결한 뒤, 정확한 분석 포인터를 Run Binding에 고정한다.
  기존 ABC 선택 결과/Hash와 FSN/운영 Gate는 유지한다. Studio 분석 조회 준비는 계약 고정 후 병행 가능하다.

**당시 승인 경계 — 위 최신 절에서 Schema 적용 승인·접속 차단 상태로 갱신**

- 실제 Schema/Role 읽기 대사 후 별도 승인으로 Canary·동시성·권한·실행계획을 검증하고 Migration을 적용한다.
  079·21개 Evidence Table 초안과 별도 범위이며 기존 074~078을 재실행하지 않는다.

아래 후보 Catalog·Class/Adapter 절은 완료한 선행 이력이다.

## 2026-10-19 명시적 후보 Catalog·공통 Guard/PSI 연결 — 로컬 구현 완료

- [실행·검증 계약](IO_CANDIDATE_CATALOG_PSI.md)에 새 입력 Catalog와 별도 Offline 실행 진입점을 정리했다.
  Legacy/ABC 공통 예산은 각 32개 요청 후보, 넓은 참조는 정확한 합집합 88개다. 256개 전체 곱으로 늘리지 않는다.
- 전체 분류/Feature를 한 번 검증하고 품목별로 후보를 조회한다. 원 Config/분류·W0/Scope/Cut-off·공유
  Calendar/Master·26주 수요·물리 Lead Time을 실제 수학적 입력과 대사한다. 원 Snapshot을 재봉인하지 않는다.
- 기존 `RunPolicySearchUseCase` 내부에 선택적 Catalog 연결을 추가했다. 모든 후보는 실제 기존
  CandidateStrategy·Guard·PSI·평가 함수를 사용하며 기존 기본 ABC 호출의 결과/Hash는 유지한다.
- 결과는 `inventory-offline-catalog-psi-result / 1.0.0`으로 별도 구분한다. 원 입력과 Catalog/후보·PSI·주문·
  실패/제외·평가 Hash를 보존하며 결과 Verifier는 원 입력에서 Guard/PSI를 재실행한다.
  기존 ABC 선택기는 새 결과를 거부한다. 최적 정책을 선정하거나 기존 Artifact/DB에 게시한 것은 아니다.
- 신규 기능 48개와 기존 고정 Hash 계약 4개를 함께 실행해 **52 passed**를 확인했다.
  수작업 Q=11/ROP=10/S=21 Golden, 88개 실제 Guard 호출, 2품목 176개 후보의 Ledger 격리,
  FSN BLOCK/REVIEW·VED·SDE·MOQ/Lot/용량·실패 격리·누락/변조·작업 상한을 검증했다.
- 별도 메모리 Probe에서 13주×88개=1,144개 PSI Row·8,008 Observation 상한과 BOH/EOH 이월을 확인했다.
  Python Hash Seed 1/731의 Catalog/결과 Hash도 동일했다. 미사용 Holdout 성과 실험은 아니다.
- 최종 전체 Unit·Contract·Offline Integration **1,065 passed / 34 skipped / 655 subtests passed**,
  기존 Starlette 경고 1건. Ruff·Format 282파일·JSON Schema 36개·Diff Check를 확인했다.
  기존 ABC/Class·Legacy Class/Adapter·호환/Golden·개인 보고서의 작업 전후 파일 지문도 대사했다.
- **기존 FSN 설정·운영 Gate·원 ABC/Class·Golden·DB·Platform은 변경하지 않았다.**
  외부 DB 조회/Write·Migration·실제 Claim/E2E Write·Runtime 설치/공용 배포·실제 발주·Commit/Push는 없다.
  개인 보고서·환경·이전 미커밋 변경은 보존했다.

**다음 작업 — 중립 후보 결과의 선정·평가를 연결한다 · InventoryEngine**

- 기존 선택기의 ABC 후보 재생성 의존성을 분리하고, 새 Catalog의 정확한 목록과 실제 PSI 검증을 유지하며
  기존 서비스·비용·재고·발주횟수·동점/미선정 기준을 적용한다. 원 ABC PSR/선택 Hash는 유지한다.
- 이후 새 미사용 기간에서 ABC·XYZ/LEGACY_STYLE/넓은 참조를 동일 조건으로 비교한다.
  선정 연결·검증 → 성과 비교는 직렬이다. Legacy 원본 자료 확보는 별도 병행 가능하다.

아래 Class/Adapter·규칙 정의·원본 대사는 완료한 선행 이력이다.

## 2026-10-15 LEGACY_STYLE Class·Adapter·Feature Producer — 로컬 구현 완료

- [로컬 구현 계약](IO_LEGACY_STYLE_IMPLEMENTATION.md)에 입력·책임·Hash·재검증·실행 경계를 정리했다.
  새 `legacy_rules.py`·`legacy_features.py`·`legacy_style.py`·`legacy_adapters.py`만 추가했으며
  기존 ABC Class/Adapter·분류 Snapshot·정책 탐색/선택·7축/FSN·운영 Gate 코드는 수정하지 않았다.
- Producer는 정확히 W0 이전 마감 26주·물리 Lead Time·USD/EA 단가와 별도 입력 Binding을 검증한다.
  입력 원문·기존 ABC 결과·참조 통계/정책/계산 근거·모든 미분류 사유를 가진 불변 Feature ID/Hash를 생성한다.
  Source 수집·DB 게시·업무 승인 또는 실제 설치 Build 탐색 기능은 아니다.
- Class는 확정한 구간/점수와 W0 기준 품목 예외를 적용하고 Adapter는 공통 중립 분류/후보 범위로 변환한다.
  원 ABC 등급·Hash는 별도 보존한다. 기존 Snapshot을 재봉인하거나 Legacy 그룹을 AX/CZ로 위장하지 않는다.
- 단순 Hash 검사뿐 아니라 원 입력에서 Feature와 분류를 재생성해 변조를 대사한다.
  구간 비교는 정확한 유리수로 처리하며, 새 내부 설정 Hash에는 원 Config·규칙·품목 예외 Hash를 묶는다.
  기존 Config/Run 계약 Version과 규칙 Profile Hash·원 Golden 파일은 그대로다.
- 신규 테스트 **120 passed**: 그룹 27건·경계 15건·참조 ROP 7건·누락 12건, 0일/잘못된 LT·가격 신선도,
  품목 예외·기간/중복·입력/Hash/Version 변조·순서/Decimal Context·불변성·새 결과 지문을 검증했다.
  기존 실제 정책 탐색 경로의 FSN BLOCK/REVIEW 전후 Hash·Baseline·주문 제한과 미분류 3품목도 확인했다.
- 최종 전체 Unit·Contract·Offline Integration **1,017 passed / 34 skipped / 655 subtests passed**,
  기존 Starlette 경고 1건을 확인했다. 실제 DB 연동 Flag는 비활성으로 고정했다.
  Ruff·Format 277파일·JSON Schema 36개와 작업 전후 기존 코드/Schema/호환 테스트·Golden 무변경을 대사했다.
  Coverage/Bandit은 사용 가능한 두 Python 환경에 설치되지 않아 수치/검사 결과를 주장하지 않는다.
- **FSN 설정·운영 Gate·DB는 변경하지 않았다.** DB/Source 원천 접속·Migration·실제 Claim/E2E Write·
  Runtime 설치/배포·실제 발주·Commit/Push는 수행하지 않았다. 개인 보고서·환경·기존 미커밋 변경도 보존했다.

**당시 다음 작업 — 후보 Catalog/PSI 연결은 위 최신 절에서 완료**

- 후보 범위 Adapter까지 완료했다. 다음은 32개 요청 조합과 정확한 88개 합집합을 실제 공통 Guard/PSI로
  전달하는 경로다. 전체 결과를 품목마다 반복 재검증하는 방식은 대량 실행으로 확대하지 않는다.
- 이후 새 미사용 기간에서 ABC·XYZ/LEGACY_STYLE/넓은 참조를 동일 조건으로 비교한다.
  후보 실행 경로 구현·테스트 → 성과 비교는 직렬이며, Legacy 원본 자료 확보는 별도 병행 가능하다.

아래 규칙 정의와 대사는 완료한 선행 이력이다. Class 구현을 다시 다음 작업으로 잡지 않는다.

## 2026-10-15 LEGACY_STYLE 개발 규칙 V1 — 정의 완료 / 구현·성과 비교 미수행

- [개발 규칙](IO_LEGACY_STYLE_DEVELOPMENT_RULES.md)과 [프로필 원문](data/legacy-style-development-rules-v1.json)에
  `D1~D7`을 고정했다. 확인된 Legacy 근거와 새 인자·구간·단위·점수·후보 숫자를 명확히 분리했다.
  프로필 Hash는 `a3121dfa885f8b4d2e0df54605789ad841661693b0f386329fe4fa60946ecbae`다.
- W0 이전 26주로 계산한 참조 ROP 커버리지(0.5/1.5개월), 물리 Lead Time(14/42일),
  USD/EA 단가(10/100)의 3인자를 최대 27개 그룹으로 나눈다. 분류용 정규분포 ROP는 Legacy Bootstrap이 아니다.
- 그룹을 SMALL/MEDIUM/LARGE 후보 묶음에 연결한다. 요청 조합 각 32개, 중복 제거한 넓은 참조 88개다.
  고정 구간·점수·후보 숫자는 미보정 개발 가정이며 기존 ABC/ML 실험과는 별도 프로필이다.
- 누락·미검증 Source·통화/기간 오류·평균 0은 품목 미분류로 보존한다. 유효한 Lead Time 0은 누락과 구분한다.
  기존 미분류 3품목·FSN·운영 Gate·Config/Run/DB·설치 Build는 변경하지 않았다.
- [수작업 Golden](data/legacy-style-development-rules-golden-v1.json)의 그룹 27건·경계 15건을
  메모리 산술로 대사하고 참조 ROP 7건을 기존 순수 계산 함수와 비교했다. 공통 CandidateRange로
  3개 프로필의 범위·32개 조합/88개 합집합·중복/상한을 확인했다. 12개 누락/오류와 3개 Gate 항목은
  **후속 Class의 기대 사양**이며 신규 알고리즘 실행 통과 건수가 아니다.
- 기존 호환·Adapter·탐색·선택 회귀 **69 passed**를 재확인했다. 아래 선행 전체 회귀 897건을
  이번에 재실행한 것으로 합산하지 않는다. Python 코드·테스트·Schema는 이번 단계에서 수정하지 않았다.
- 관련 문서 5개의 로컬 링크 62개·JSON 2개·규칙 Hash·Diff Check를 검증했다.
  작업 시작/종료의 `src`·`tests`·`schemas` 차이와 신규 Class/계약/테스트 파일 지문도 동일함을 확인했다.
- DB 조회/Write·Migration·실제 Claim/E2E·Runtime 배포·발주·Commit/Push는 수행하지 않았다.

**당시 다음 작업 — Class·Adapter는 위 최신 절에서 완료 / 후보 PSI 연결은 후속**

- Feature Producer·`LegacyFactorSegmentation`·공통 Adapter와 개별 Golden 검증을 완료했다.
  현 AX~CZ 전용 Config를 임의 확장하거나 Legacy 그룹을 위장하지 않는다.
- 명시적 중립 후보 Catalog를 공통 PSI/Guard에 연결한 뒤 ABC 32개/LEGACY_STYLE 32개/참조 88개를
  새 미사용 기간의 소규모 Offline 실험으로 비교한다. 기존에 본 Holdout은 재사용하지 않는다.
- 이 경로는 계약→구현→개별 검증→비교 순으로 직렬이다. Legacy 원본 자료 확보는 독립 진행 가능하다.

아래는 선행 대사/구현 이력이다. 완료한 개발 규칙 정의는 다시 실행 대상으로 삼지 않는다.

## 2026-10-15 Legacy 규칙 로컬 대사 — 완료 / 원본 Segmentation 동등성 미확정

- [규칙 대사표](IO_LEGACY_SEGMENTATION_RULE_RECONCILIATION.md)에 인자·구간·단위·후보 범위를
  코드 확인·DDL 확인·업무 설명·미확인·개발 가정으로 구분했다. 근거 파일 5개의 Hash·코드 위치를 보존했다.
- 로컬 Rop Python/R은 품목별 일 수요와 고정 `STOCK_LT`를 이용해 10,000회 표본을 만들고
  서비스수준별 ROP·중앙값·안전재고를 산출한다. Segmentation 그룹 생성이나 10,000개 정책 후보 탐색이 아니다.
- TGSM은 Segmentation `PO_POLICY_CD`, POSM은 Master `APPY_PO_POLICY_CD`를 Engine에 전달한다.
  실제 그룹 구간·RMOS/EMOS 후보·후속 변환·주문 분기 본문과 현재 적용 언어/Version은 미확보다.
- Python/R 간 Seed·표준편차 분기·수량/분위수 반올림·R 음수 보정 덮어쓰기 차이를 기록했다.
  원본을 import/source·실행하거나 수정하지 않았고 현 Python의 월수 커버리지 후보식을 Legacy로 재명명하지 않았다.
- 이번 변경은 대사 문서·근거 Register와 관련 계약/기준선 정리뿐이다.
  FSN 설정·운영 Gate·미분류 3품목 차단·Engine 계산 코드·Platform 코드는 변경하지 않았다.
  DB 접속/Write·Migration·실제 E2E·배포·Commit·Push도 수행하지 않았다.
- 근거 파일 Hash **5개**, 코드 위치 **14곳**, 로컬 문서 링크 **49개** 검증과 관련 호환/탐색/선택
  회귀 **69 passed**, Diff Check를 확인했다. Legacy 원본 프로그램의 계산 동등성 검증은 아니다.
- 아래 **897 passed / 34 skipped**는 선행 Class/Adapter 추출의 전체 회귀 이력이다.
  이번 문서 대사의 검증 건수와 혼합하지 않으며, 최종 근거·링크·호환 검사 결과는 대사 문서를 따른다.

**당시 다음 작업 — 개발 규칙 정의는 위 최신 절에서 완료**

- 원본 숫자를 Legacy 기본값으로 추정하지 않고 `D1~D7`을 개발 설정으로 정의했다.
  Class/Adapter·Offline 후보 전달·동일 조건 비교는 아직 후속이며 현재 순서는 최상단 기준선을 따른다.
- 원본 동등성 경로는 상위 프로시저·구간/후보 설정·실제 주문 분기 자료 대기다.
  자료 확보는 개발 재구성과 독립 진행할 수 있으나 개발 결과를 원본 동등성 근거로 올리지 않는다.

아래 절은 선행 구현과 검증 이력이며 완료한 로컬 규칙 대사를 다시 실행 대상으로 삼지 않는다.

## 2026-10-15 공통 분류·후보 범위 계약과 ABC·XYZ Class/Adapter 추출 — 완료

- [공통 계약과 호환 경계](IO_SEGMENTATION_ADAPTER_CONTRACT.md)를 프로세스 내부 1.0.0으로 정의했다.
  기본 `abc_xyz_class`와 탐색용 `search_group_id`를 분리하고, 방법/구현 Version·Source/설정 Hash,
  품목별 배정·미분류 사유·후보 범위·품목 예외 근거와 별도 내부 Hash를 보존한다.
- `AbcXyzSegmentation`에 기존 계산을 이동하고 `classify_items()`의 기존 호출을 유지했다.
  `AbcXyzSegmentationAdapter`는 원 분류 결과를 중립 계약으로 변환한다.
  후보 Adapter는 실제 `RunPolicySearchUseCase`에 연결하되 기존 V1 Candidate 형식으로 출력한다.
- 기존 Config 1.1.0/2.0.0/2.1.0의 Snapshot·UUID·Source/설정/유효 정책·후보·PSI 탐색·선택 Hash를
  변경 전 지문으로 검증했다. 참조 Commit `99f2f12`와 600개 입력의 반환/오류, 전체 Snapshot 4건도 일치했다.
  ABC·XYZ 계산 본문 AST는 동일하다. 기존 계산·Gate 테스트는 수정하지 않았다.
- 신규 **23 passed**, 전체 **897 passed / 34 skipped / 655 subtests passed**, 기존 경고 1건.
  Ruff·Format 10파일·JSON Schema 36개·Diff Check 통과. 이전 Offline 비교의 의미상 결과 Hash도
  `b8b8d7423dafdbcf717da2919820e863dedb570f3ad7c1561bd10c9107332d80`으로 동일하다.
  이번 비교 재실행은 메모리 검증만 했고 Artifact 재게시·DB 기록은 하지 않았다.
- 분류 조립 파일은 1,533→1,107줄로 줄였고 새 ABC·XYZ Class는 401줄이다.
  파일 전체 재설계가 아닌 분류/직렬화/자료형의 책임별 추출이며 나머지 7축 조립 코드는 유지했다.
- **FSN 설정·운영 Gate·미분류 3품목의 차단은 그대로다.** 신규 중립 그룹 표현은 Legacy Runtime 허가가 아니다.
  Platform 코드·Config/Run/JSON Schema·DB·설치 Build/공용 Runtime은 변경하지 않았다.
  개인 보고서·이전 미커밋 변경·환경·임시 산출물을 보존했으며 Commit·Push도 수행하지 않았다.

이 단계 후속의 로컬 규칙 대사는 위 절에서 완료했다. 개발 규칙 확정·Class/Adapter·비교의 현재 순서는
최상단 기준선을 따른다. 서비스 충족 여부를 우선하고 비용·재고·발주횟수·계산시간·누락을 함께 평가한다.

Source/Claim·079/Evidence 실제 DB 적용은 별도 승인 경계이며 아래 절은 완료/검증 이력이다.

## 2026-10-15 정책 성과 기반 ML Segmentation Offline 비교 — 완료 / 우월성 미확인

- [실험 방법·결과](IO_POLICY_SEGMENTATION_OFFLINE_EXPERIMENT.md): ABC·XYZ 후보, ML Profile 후보,
  넓은 후보를 같은 Canonical·Prepared 입력으로 실제 검색·Guard·PSI·선택기를 거쳐 비교했다.
  TRAIN/VALIDATION과 두 미사용 HOLDOUT 기간을 분리하고, Forecast에서 선택한 정책을 고정한 뒤
  실적 Replay를 평가했다. 기존 Config·FSN·운영 Gate·Source·DB와 Platform 코드는 변경하지 않았다.
- 독립 합성 Window의 작은 실험이다. ML의 좋은 후보 누락은 5→4건이지만 실적 서비스 자격 충족은
  ABC 5건/ML 4건(일반 평가대상 8건), 공통 서비스 충족 3건의 비용은 ML이 35.02% 높았다.
  ML과 ABC의 후보 수는 동일하고 넓은 탐색보다 적다. 성능 개선·운영 승격을 주장하지 않는다.
- 신규 19 passed, 전체 **874 passed / 34 skipped / 655 subtests passed**, 기존 경고 1건.
  Ruff·Format·JSON Schema 36개·Diff Check 통과. 입력 원문 및 PSR/PSEL 16쌍 재읽기를 확인했다.
- 기존 합성 데이터 생성 독립성 검사를 보존하고 평가 Runner를 `evaluate_replenishment`에 배치했다.
  학습 모델은 개발용 Profile Routing이며 기존 보충용 ML/PPO·Runtime 모델 등록과 별개다.
- 개인 보고서·환경·임시 산출물은 보존했다. 실제 DB 접속·Write·Migration·배포·발주·Commit·Push는 없다.

**다음 작업 — 서비스 손실 원인과 새로운 검증 자료를 정리한다.**

- 독립 연속 자료·다중 Seed·수요/비용 가정·종료 효과로 검증 범위를 넓힐지 결정한다.
  모델 개선에 이번 Holdout 결과를 사용하면 새 미사용 Holdout을 준비한다.
  신규 Config/API/UI·운영 Runtime의 필수 ML 경로 확대는 우월성 검증 전 보류한다.
- 아래 Source/Claim·079/Evidence 적용 작업은 별도 기준선이다. 이번 실험이 기존 운영 차단을
  해소하거나 DB 변경을 승인한 것은 아니며, 완료한 기존 구현도 다시 실행하지 않는다.

## 2026-10-15 Source·설치 Build·Projection 실행 조립 — 현재 로컬 기준선

- [Source 실행 계약](IO_DSIM_SOURCE_EXECUTION.md)에 설치 Build·정확한 Manifest·분류·Bundle·Projection
  포인터를 연결했다. 분석 OFF여도 원본 분류 Header와 전체 품목 근거를 별도 봉인한다.
- 원 Header Hash/UUID·Effective Policy Hash·Config/Scope·품목 Coverage를 검사하고 새 조립 경로는
  판단 주차와 Plan 주차를 계산 전에 대조한다. 기존 FSN/운영 Gate는 유지한다.
- 고정 Source Execution Artifact로 재기동·저장 실패·commit 응답 유실을 복구한다.
  복구 시 원천 조회·PSI 재계산은 하지 않으며 과거 Header 없는 Outbox를 수정하지 않는다.
- 격리 Wheel 설치의 신규 Source/Platform 조립 **19 passed**, Platform 관련 **293 passed**.
  전체 Engine **873 passed / 9 skipped / 669 subtests passed**, 기존 Framework 경고 3건.
  Ruff·Format·Catalog/DDL 생성물·Diff Check 통과. 설치 후보 Hash는 Source 실행 계약에 기록했다.
- 실제 승인 Authority/Audit/Writer는 Offline 대역이다. 실제 DB 조회·적용·승인·기본 Route/Runtime 활성화·
  기존 설치 교체·공용 배포·V100 실제 Claim E2E·실제 발주·Commit·Push는 수행하지 않았다.

**다음 작업 — 저장소별 변경과 설치 후보 검토**

- InventoryEngine `inventory_engine_dev`, Platform `codex/inventory-psi-strategy-v2`의 관련 변경을 선별한다.
  개인 보고서·환경·임시 Wheel은 제외한다. Commit·Push는 별도 요청 후 수행한다.

**승인 필요 — 개발 PostgreSQL Canary와 실제 Source/Claim 연결**

- Schema/Role 대사 → Rollback·FK/Trigger·동시성 → 필요한 Migration → 실제 입력·Claim·조회/복구 순서다.
  079와 Evidence DDL은 별개이며 074~078 재실행·공용 배포·실제 발주는 포함하지 않는다.
- 아래 동일 날짜 절은 검증 이력이다. 완료한 Source 조립/Writer 작업을 다시 실행하지 않는다.

## 2026-10-15 DSIM Projection Writer·Verifier — 선행 구현 이력

- [구현·검증 계약](IO_DSIM_PROJECTION_WRITER_VERIFIER.md)에 봉인 Artifact → 20개 논리 Table →
  검증 Receipt의 Writer/Verifier·분할 저장·재생성 대사·복구를 정리했다.
- Platform Callback 권한·Scope·원 Claim Hash·배정 Row Version·전체 입력 Binding을 검사하고,
  Source 검증은 Transaction 밖에서 수행한 뒤 저장 직전에 Claim을 다시 검사한다.
- 실제 Artifact와 대조해 미적용 초안의 NO_ORDER·입고일/Source 시각 null·Site 공통 Cut-off·
  원 HML 수준/별도 주문 Gate·장기 평가 FK를 보정했다. 신규 초안은 21개 Table·277개 컬럼이다.
- 분류 Effective Policy Hash와 Header Hash를 혼동하지 않는다. 원문 Header 없는 Bundle는 차단하며
  분석이 꺼진 실행의 별도 Header 봉인/Resolver는 다음 실제 Source 조립 범위다.
- InventoryEngine 전체 **854 passed / 9 skipped / 669 subtests passed**, 기존 Framework 경고 3건.
  신규 Projection/복구 19건, Platform 관련 273건·Catalog/DDL 대사·파싱·Ruff/Format 통과.
- **실제 DB 접속/DDL/DML·079/신규 Migration·V100 실제 Claim E2E·HTTP/Runtime 기본 활성화·
  기존 Wheel 교체·공용 배포·Commit·Push는 미수행**이다. 기존 FSN·운영 Gate와 개인 보고서는 보존했다.

**다음 작업 — InventoryEngine·Platform 실제 봉인 Source·설치 Build 대사**

- 적용할 분류 원문·정확한 Artifact 포인터와 조립 경로를 고정한다. 아래 이전 절의 Writer 구현은
  완료된 이력이며 다시 실행하지 않는다. Read API 계약은 Source 조립 계약 고정 후 병렬 준비할 수 있다.

**승인 필요 — 개발 PostgreSQL Canary·동시성·권한·실제 적용**

- 기존 Schema와 초안을 대사한 뒤 별도 승인으로 검증한다. 079와 Evidence Migration을 합쳐
  자동 적용하거나 기존 074~078을 재실행하지 않는다.


## 2026-10-15 DSIM 관계형 Evidence 물리 초안 — 현재 설계 기준선

- [물리 계약 초안](IO_DSIM_PHYSICAL_CONTRACT.md)과 `schemas/inventory-dsim-physical-contract-v1.json`에
  Modified Table 후보 24개를 대사했다. 기존 Run·승인 입력·Config·7축·Network·Trade/비용은 재사용하고,
  신규 21개 Evidence Table의 컬럼·Grain·PK/FK·Hash·Receipt를 로컬 초안으로 정리했다.
- 정책 승인/배정 Master 2개와 DB 업무 게시 Outbox는 보류한다. 선정은 업무 승인이 아니며,
  새 `publication_receipt`는 Evidence Projection 완료만 의미한다. 자동 발주·Effective Run CAS는 변경하지 않는다.
- Platform `infra/migrations/drafts/UNASSIGNED_dsim_inventory_evidence_v1.sql`은 적용 번호가 없고,
  검토 Flag가 없으면 중단하며 마지막은 ROLLBACK이다. 실제 DB 접속·적용·적재·배포·Commit·Push는 하지 않았다.
- 기존 `CALCULATION_ONLY`는 영속 Artifact를 유지한다. 정식 Run Claim 없이 새 Ledger를 만들지 않는다.
  아래 079 미적용·V100 새 분류/승인/Handoff 차단과 기존 FSN/운영 Gate는 그대로다.
- 검증: InventoryEngine Unit·Contract·Offline Integration **835 passed / 9 skipped / 669 subtests passed**,
  기존 Framework 경고 3건. Platform 관련 Migration **114 passed**(신규 물리 초안 57건 포함),
  pglast DDL/PLpgSQL 구문·Ruff·Format·Catalog byte 대사·생성 SQL 대사 통과.
  구문 분석/SQLite CHECK 검사는 실제 PostgreSQL FK·Trigger·동시성 Canary가 아니다.

**다음 작업 — InventoryEngine·Platform의 Projection Writer/Verifier를 Offline으로 연결한다.**

- 원 Artifact/Binding/Policy/Scenario Hash, 전체 품목·후보·주차 Coverage, Chunk 건수·멱등 Receipt를 검증한다.
  Reader 권한 계약은 공통 계약 고정 후 병렬 준비할 수 있다. DB 적용이나 발주는 포함하지 않는다.

**승인 필요 — 개발 PostgreSQL Rollback Canary·권한·동시성을 검증한다.**

- 실제 Schema/Index/Role 대사 후 필요한 Table만 번호가 있는 적용 후보로 정리한다.
  079 판단 주차 제약과 신규 Evidence DDL은 별개이며 자동 적용·기존 Migration 재실행은 하지 않는다.

## 2026-10-15 Plan 판단 주차·직전 마감 보정 — 기존 실행 기준선

**Plan `202512`의 판단 주차와 직전 마감 `202511`을 고정하는 구현·회귀·실제 DB 읽기 전용 검증을 완료했다.**
신규 분류 게시·Config 승인·실제 Claim·Runtime 배포는 완료하지 않았다. 이 절의 Plan·분류 실행 기준선은 유지하며,
신규 Evidence 물리 초안과 다음 작업은 위 설계 기준선 절을 따른다.

- 코드 기준: InventoryEngine `inventory_engine_dev` HEAD `919201b820b0e42174e8c3eebc12b4f617445a88`,
  Platform `codex/inventory-psi-strategy-v2` HEAD `8bc60f8210c72e91fac0a7374597565c9b2f7eff` 위 로컬 변경이다.
  이번 보정은 아직 Commit·Push하지 않았다. 개인 보고서·환경·임시 산출물은 대상에서 제외한다.
- 선행 승인 작업으로 누락 23주 `202438~202508`의 개발 마감을 게시했고,
  기존 3주 `202509~202511`을 보존해 26주 전체를 충족했다. 마감 Header만 게시했으며 원본 수요를 변경하지 않았다.
  개발 합성 Full Load 기반 증적이지 실제 ERP 마감의 정확성 검증은 아니다. 이 게시를 다시 실행하지 않는다.
- 기존 Source의 전체 최신 마감 선택과 Builder의 실제 Source 판단 주차 거부를 보정했다.
  Plan 호출은 정확한 직전 주차의 최신 Revision만 읽고, 누락·미마감이면 다른 주차나 과거 Revision으로 우회하지 않는다.
  Claim된 Run CLI의 `--decision-yyyyww`는 필수다. Source·Context·Receipt의 판단 주차 불일치도 차단한다.
- `as_of_yyyyww=202512`, 실제 마감 `202511`, 26주 `202438~202511`, 날짜 범위
  `[2024-09-16, 2025-03-17)`을 유지한다. 새 V2 Source Revision은
  `ACTUAL-CLOSE-DECISION-WINDOW-*`로 구분하며, 기간·이력 Hash·Source Relation을 게시 전에 검증한다.
  기존 개발 합성 입력·V1 의미·FSN 설정·운영 Gate는 유지한다. 후속 Migration 079는 아래와 같이 준비했으며 실제 적용하지 않았다.
- 판단 주차를 생략한 독립 분류는 이전 최신 마감 동작을 호환용으로 유지한다. Plan 실행·재현에는 사용하지 않는다.
  기존 승인 Snapshot을 수정하지 않으며 확정 Run은 기존 Snapshot ID·Revision·Hash로 재현한다.

### 이번 검증과 적용 경계

| 확인 항목 | 결과 |
|---|---|
| 실제 개발 DB 조회 | 연결을 읽기 전용으로 강제. 최신 마감 `202601`이 있어도 `202511` Revision 1 선택 |
| 기존 활성 설정 | Config 2.0.0 Revision `d4be4ad6-9e2e-47ce-84fa-d0c0b870fa4b` 유지 |
| 순수 계산 범위 | 전체 Master 7,000품목, 분류 6,991 / 미분류 9, FSN 7,000품목 분류 |
| 후보 검증 | Content Hash·Snapshot ID 재계산과 Python 게시 전 Validator 통과. DB INSERT 검증·게시 없음 |
| 전체 회귀 | Unit·Contract·Offline Integration **828 passed / 16 skipped / 669 subtests passed**, 기존 Framework 경고 3건 |
| 관련 회귀·정적 검사 | 69 passed / 24 subtests, Ruff·Format·Diff Check 통과 |
| 실행 경계 | 신규 DB Write·Migration·승인·Claim·Forecast 재실행·실제 발주·Runtime 배포·Commit·Push 없음 |

이 실제 Source 검증은 **Config 2.1 Draft·Forecast 3,622품목 개발 범위의 신규 승인 결과가 아니다.**
기존 원문 Snapshot `bbdc697b-bf7c-5765-b5d8-09a27c260948`은 자동 갱신하지 않았으며,
그 원문의 FSN 미검증·미분류 3품목과 운영 Gate는 그대로다. 26주 마감 충족이 기존 Snapshot·Handoff를 자동 승격하지 않는다.
실제 조회에 사용한 임시 SSH 터널은 검증 후 종료했고 로컬 주소·포트는 문서에 기록하지 않는다.

**추가 발견 — DB 게시 차단:** 실제 개발 DB의 `chk_inventory_classification_v2_lineage`는
이력 종료일을 `as_of 주차 + 7일`로 강제한다. 새 판단 주차 계약은 이력 종료일이 `as_of 주차 시작일`이므로
Python 검증을 통과해도 현재 DB 제약을 만족하지 않는다. `pg_constraint`를 읽어 실제 적용 상태도 확인했다.
기존 Migration 076은 그대로 보존하고, 구형 Snapshot과 새 실제 마감 판단 주차 형식을 구분하는
후속 079 SQL·회귀 테스트 준비는 완료했지만 실제 적용하지 않았으므로 새 Snapshot DB 게시는 여전히 차단 상태다.

### Migration 079 준비와 DB 변경 범위 — 완료 / DB 미적용

- dsai-platform `codex/inventory-psi-strategy-v2`에
  `infra/migrations/drafts/079_inventory_classification_decision_week.sql`과
  `tests/unit/platform/test_inventory_classification_decision_week_migration.py`를 준비했다.
  기존 076 SHA-256은 `7829281813f544581d7fdd9d238f190ef5d47dec3f0267946d805c8aaaf9ab60`으로 유지했다.
- 079의 스키마 변경은 `dsai.inventory_classification_snapshots`의
  `chk_inventory_classification_v2_lineage` 하나다. 테이블·컬럼·키·Index·Trigger·권한 추가와 기존 업무 행 변경은 없다.
  유효한 V1·기존 V2 기간을 유지하며 새 실제 마감 판단 주차 Marker에만 새 종료일을 허용한다.
  Version 누락과 채워진 V2 이력이 CHECK의 UNKNOWN으로 통과하는 경로도 차단했다.
- 관련 **46 passed**, Platform Inventory **538 passed**, 기존 Framework 경고 25건이며 Ruff·Format·Diff Check 통과다.
  pglast 7.20으로 PostgreSQL 문장 8개·PL/pgSQL 본문 2개를 파싱했다. Parser는 임시 경로에만 설치했고
  프로젝트 의존성은 변경하지 않았다. SQLite Offline 식 검증은 PostgreSQL DDL·잠금·Trigger 실행 검증이 아니다.
- 실제 개발 PostgreSQL의 읽기 전용 SELECT 평가에서 기존 Header 2건의 부적합은 0건이었다.
  기존 활성 Config·전체 Master 7,000품목의 `202512` 후보도 새 식을 만족했다. Config 2.1 Draft·3,622품목 후보 승인은 아니다.
  조회 당시 Header 테이블은 41컬럼·5 Index·사용자 Trigger 4개, 079 적용 이력 0건이었다.
- 제약은 테이블 전체에 적용되며 단일 Transaction·`ACCESS EXCLUSIVE`, `lock_timeout=5s`,
  `statement_timeout=120s`를 사용한다. 기존 행 검사와 잠금 영향을 실제 Rollback Canary에서 확인해야 한다.
  적용 전 Schema·제약·Ledger·Snapshot ID/Hash를 보존하고 Commit 전 오류는 전체 Rollback한다.
  새 형식 게시 후 076 복귀를 위해 불변 결과를 삭제·수정하지 않는다. 백업 주기·보존기간은 변경하거나 검증 완료로 주장하지 않는다.

| 앞으로 승인받아 실행할 단계 | 대상 테이블 | 실제 변경 |
|---|---|---|
| 079 적용 | `dsai.inventory_classification_snapshots` | CHECK 하나 교체·기존 행 검사. 업무 행 변경 없음 |
| 적용 이력 기록 | `dsai._migrations` | 승인된 적용 절차가 정확한 079 파일명·상태 기록. Draft SQL 자체의 INSERT는 없음 |
| Config 승인·활성화 | `dsai.demand_engine_config_revision`, `dsai.demand_engine_config` | 검증·게시 정보와 명시적 활성화 CAS. FSN 값 변경은 별도 범위 |
| 신규 분류 게시 | 기존 `dsai.inventory_classification_*` 7개 | 새 Snapshot Aggregate 추가. 기존 행·Hash 보존, Exact Replay 중복 없음 |
| 승인 입력 묶음 | `dsai.inventory_site_input_sets` | Source·Config·분류 권위 검증 통과 후 새 승인 Binding 추가 |
| Cycle·Site 준비 | `dsai.planning_cycle_revisions`, `dsai.planning_cycle_site_executions` | 정확한 Binding 추가, 상태·Attempt와 검증 후 유효 Run CAS |
| Claim·실행 증적 | `dsai.engine_runtime_runs`, `dsai.engine_run_input_bindings`, `dsai.engine_runtime_run_events` | 신규 Inventory Attempt·입력·Event. 기존 성공 Demand Run 수정 없음 |

분류 7개 테이블의 정확한 목록은 [분류 Lifecycle](IO_CLASSIFICATION_SNAPSHOT_LIFECYCLE.md)의 2.2절,
승인·Claim의 상세 물리 경계는 Platform `docs/backend/dsim/inventory-policy-analysis-run-contract-v1.md`에 기록했다.
원 수요·마감·Master·Network·Forecast와 `TB_IO_*`는 이번 보정으로 변경하지 않는다.
임시 증적은 `/private/tmp/inventory-week-constraint-20261015.PjFYsk/`의 회귀 XML·`read-only-preflight.json`·
`db-change-impact.json`이다. 실제 DDL·Rollback Canary·Snapshot 게시·Config 승인·Claim·Runtime 배포·Commit·Push는 하지 않았다.

- 로컬 후보 Snapshot ID: `65a7c371-7a8e-58a9-bad6-dfaff655f60c`.
  Content Hash: `910c63cf4128422029f06c9bc183961771bd9621865062c00f6a3746b2d886af`.
  History Hash: `70d3cb63c7a9a63fe1dc8bad33cf55db98a28901c50029c71b035a2b54bf622e`.
  모두 미게시 후보의 검증값이며 승인된 DB Snapshot ID로 사용하지 않는다.
- 이번 회귀 XML과 읽기 전용 Receipt는 `/private/tmp/inventory-decision-week-20261015.SYJfz2/`에 보존한다.
  이전 마감 게시 Receipt는 `/private/tmp/v100-weekly-close-publish-20261014.XnHMTS/`의 원본을 유지한다.
  임시 증적은 Commit 대상이 아니다. 전체 테스트는 Fixture 경로를 포함한 `PYTHONPATH=src:tests/support:tests/unit`과
  Platform 테스트 의존성 환경에서 실행했다. 이전 검증과 환경·수집 방식이 달라 총 건수만으로 신규 테스트 수를 계산하지 않는다.

### 남은 작업 순서 — 현재 실행 대상

**1. V100의 새 분류 후보와 남은 차단을 대사 — 다음 작업 / 읽기 전용·로컬 계산**

- 대상: InventoryEngine·Platform Backend. 079 준비 완료를 기준선으로 삼는다.
  Config 2.1 Draft, 3,622품목 범위의 분류 원문·8종 Source Mapping·Template/Manifest를
  판단 주차 계약과 맞추고 미분류 3품목·Handoff 입장 제한을 대사한다.
  전체 Master 7,000품목 검증과 개발 범위를 구분한다. 완료 조건은 승인할 후보와 추가 결정·보완이 필요한 차단 사유가 분리되는 것이다.

**2. Migration 079의 Rollback Canary·실제 적용 검증 — 승인 필요**

- 대상: 개발 PostgreSQL·Platform Backend. 정확한 079 SQL·제약·잠금·Ledger 변경 범위를 승인받아 검증한다.
  1번의 Source 대사와 준비는 독립적으로 가능하지만 새 분류의 실제 게시는 079 적용 후다. 기존 076은 재실행하지 않는다.

**3. 확정 후보를 승인 입력에 연결하고 로컬 Claim E2E 검증 — 별도 승인 필요 / 1·2번 후 직렬**

- 대상: 개발 PostgreSQL·Platform Backend·로컬 Inventory Runtime. Config·신규 분류·입력 묶음의 승인 대상을 확인한 뒤
  Claim·계산·조회·게시 차단·복구를 검증한다. FSN 설정과 운영 Gate를 우회하지 않는다.
  기존 074~078은 재실행하지 않는다. 공용 Runtime 재배포·신규 Migration·실제 발주는 별도 승인 대상이다.

**4. 검증한 변경을 저장소별로 고정 — 별도 Commit·Push 요청 후**

- 대상: InventoryEngine `inventory_engine_dev`, Platform `codex/inventory-psi-strategy-v2`.
  관련 코드·테스트·승인된 문서만 선별하며 개인 보고서·환경·IDE·임시 증적은 제외한다.
  제약 보정안·Source 대사·문서 확인은 독립 검증 가능하지만 Migration 적용·신규 분류 승인·입력 묶음·Claim은 직렬로 진행한다.
  비용/PPO/Stress는 수학적 V100 Claim 이후 별도 입력·Plan으로 검증한다.

## 2026-10-14 실제 로컬 서비스 실행·조회·재기동 복구 — 선행 이력

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

### 당시 남은 작업 순서 — 로컬 서비스 검증 이력

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
아래 과거 절의 완료·미완료 상태와 작업 순서는 당시 이력이다. 현재 실행 순서는 위 2026-10-15 절을 우선한다.

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
