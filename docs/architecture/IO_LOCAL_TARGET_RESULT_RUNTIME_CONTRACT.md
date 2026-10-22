# 로컬 목표 계산 결과의 Runtime·Platform 조회 조립

> 후속 [독립 IO 최초 실행](IO_INDEPENDENT_INITIAL_EXECUTION_CONTRACT.md)을 구현했다.
> 최초 입력부터 계산·저장·조회까지의 조립은 더 이상 미완료가 아니다. 아래는 결과 Reader의 고유 계약이며,
> 최초 실행의 후속 상태·남은 순서는 새 계약과 개발 기준선 최상단을 따른다.

## 2026-10-22 후속 — 요청 내 중복 재계산 축소 완료

`InventoryTargetResultExecution`의 Link·Projection·최종 Reader가 같은 봉인 결과를 반복 재계산하던 경로를
보완했다. [독립 최초 실행](IO_INDEPENDENT_INITIAL_EXECUTION_CONTRACT.md)은 계산 1회와 별도 전수 재검증 1회를
유지하며, 완료 결과의 복구·강한 조회는 요청마다 전수 재검증 1회를 수행한다.

한 요청의 동기 계산·연결 단계는 같은 Worker 스레드에서 조립한다. 같은 Run·INPUT/Manifest Hash·DLL과
Kernel 객체에 대해 성공한 검증 이력만 요청 안에서 재사용하고, 재사용 범위를 반환·예외·취소로 벗어나면 폐기한다.
다른 스레드·작업·프로세스로 검증 상태를 전달하거나 외부의 `verified=true`를 신뢰하지 않는다.
원본 Artifact·전체 후보/건수·Hash·입력/Build/Profile Binding·FSN·운영 Gate는 매번 대사한다.
원 수량 계산·후보별 상태·내부 검증·ID/Hash 형식·단일 Pipeline의 전이 호출 수는 그대로다.
세부 조건은 [저장 계약](IO_TARGET_PIPELINE_STORAGE.md#31-같은-요청의-재계산-검증-재사용)을 따른다.

### 같은 후보 Build의 실제 DLL 검증

소규모 1품목·3주 입력에서 재사용만 끈 대조 경로와 결과 Hash·저장 참조가 같았다.

| 후보 방식 | 후보 수 | 최초 실행 전이 호출: 대조 → 재사용 | 새 요청의 완료 복구 | 새 요청의 강한 조회 |
| --- | ---: | ---: | ---: | ---: |
| ABC·XYZ | 32 | 2,970 → 990 | 495 | 495 |
| LEGACY_STYLE | 32 | 2,970 → 990 | 495 | 495 |
| 넓은 참조 | 88 | 8,010 → 2,670 | 1,335 | 1,335 |

정상 후보 3개 사례와 CHECKPOINT·BUNDLE·TARGET_PART·LINK·PROJECTION 게시 직후 SIGKILL 5개 사례를
새 프로세스에서 복구·조회했다. 미완료 계산은 전체 계산과 별도 검증 2회, COMPLETE 이후 복구는 검증 1회다.
실행기는 [verify_initial_target_execution.py](../../scripts/verify_initial_target_execution.py)다.
검증용 새 설치 Build는 `1478bd76206d4eeebafe569569ac5015ae8489dd6e494ae74de35c0fcae66b55`다.
이 비교는 같은 후보 Build 안의 재사용 유무 비교이지, 기존 V100 Build를 대체하거나 같은 Hash로 표시한 것이 아니다.

관련 Engine **597 passed / 147 subtests passed**, Platform **56 passed**를 확인했다.
Platform 수치에는 별도 임시 Wheel의 Backend Factory 연결 6건이 포함되며 기존 Starlette 경고 12건은 남는다.
Ruff·Format·Diff Check도 통과했다. 전체 저장소·Frontend·대규모 V100 회귀나 응답시간·메모리 검증은 아니다.
이번 문서 정정에서 계산·테스트를 다시 실행하지는 않았다.

Backend의 요청별 권한·Scope 검사와 별도 Writer Worker의 독립 검증·트랜잭션·Receipt·행 대사는 유지한다.
현재 화면의 저장 결과 GET은 이미 재계산하지 않으므로 변경하지 않았다.
기존 V100 Run·입력·Build·Artifact·FSN·운영 Gate와 개발 PostgreSQL·공용/M3 Runtime은 변경하지 않았다.
새 후보 검증 외 기존 V100 Forecast/PSI 재계산·DB Write·배포·실제 발주·Commit/Push는 없다.
아래 DB 미연결·검증 수치·남은 작업은 2026-10-21 당시 이력이며, 현재 순서는 [최신 기준선](IO_DEVELOPMENT_BASELINE.md)을 따른다.

## 2026-10-21 구현 기준선 — 이력

2026-10-21, `inventory_engine_dev`의 독립 Offline 구현이다. 기존 Python 계산과
운영 Runtime Registry·Run/결과 Wire 계약·ID·Hash·FSN·운영 Gate는 수정하지 않았다.
[결과 연결 계약](IO_TARGET_RESULT_LINK_CONTRACT.md)의 Reader를 명시적으로 연결하며
기존 Result Bundle/Outbox의 내용·Hash·유효 Child를 바꾸지 않는다.

- Engine: `run_inventory/target_result_execution.py`의 `InventoryTargetResultExecution`
- Platform Backend: `services/inventory_target_result_execution.py`의
  `InventoryTargetResultExecutionService`와 `build_inventory_local_target_result_service`
- 사용 범위: 개발 로컬 서비스 호출·조회·Artifact 저장 응답 유실 복구. HTTP Route, app startup,
  기본 Runtime 전략 등록, 운영 게시·발주, PostgreSQL Writer 연결은 추가하지 않았다.

## 정확한 포인터를 전달하는 방법

기존 Run·Attempt에 속하는 다음 참조를 `sources`로 전달한다.

| 필드 | 내용 |
| --- | --- |
| `bundle` | 원본 Bundle의 `artifact_reference`, `content_hash` |
| `target_input` | 목표 저장기의 INPUT `kind`, `content_hash`, `byte_count` |
| `target_manifest` | 같은 Run의 COMPLETE 슬롯이 가리키는 MANIFEST 참조 3필드 |

실제 파일 경로·DLL 경로·Build·Reader·Catalog·권한 검사기는 서버 구성에서 주입한다.
요청의 참조를 파일 경로로 사용하지 않으며 최신 Source나 최신 완료 결과를 탐색하지 않는다.
슬롯을 확인하는 것은 전달된 참조와 완전히 같은지 검증하기 위해서다.

반환하는 `result_pointer`는 다음 세 필드만 묶는다.

- `sources`: 위 원본 참조
- `target_result_link`: 기존 연결 Artifact의 Reference·Hash
- `projection`: 기존 Projection Manifest의 Reference·Hash·논리 Projection Hash

새 업무 ID, 부모 결과 Hash 또는 저장용 Wrapper를 만들지 않는다.
기존 Artifact 체크섬과 결과 Hash를 재사용한다. Projection은 기존 기본 Chunk 크기 500을
고정해 응답 유실 후에도 같은 물리 참조를 재현한다.

## 호출·조회·차단

`execute(request, sources)`는 원본 Bundle, 동일 Runtime 요청과 설치 Build, 정확한 INPUT/COMPLETE 참조를
검증한 뒤 연결 Artifact와 Projection을 Append-only로 저장한다. 기존 Reader로 재검증한 결과를 반환한다.

`read(request, result_pointer)`는 원본 Bundle, 연결 Artifact, 원본 검색/선정 문서,
INPUT/Manifest/전체 결과 조각과 Projection을 대사한다. 동일 입력·Profile·DLL로 전체 후보·선정·발주 계획을
재계산하며 `RECOMPUTED_VERIFIED`를 반환한다. 2026-10-22 보정 후에는 같은 요청의 중복 수량 재계산만 생략하고,
최소 한 번의 전수 재검증을 유지한다. 빠른 무결성 조회를 강한 검증으로 표시하지 않는다.
Artifact commit이나 원천 조회는 호출하지 않는다. 새 요청마다 전수 재검증하므로 즉시 응답·성능 SLA를 보장하지 않는다.

다른 Run·Attempt·Cycle·Site·분류·Config, 변경된 Hash·Build·DLL, 누락된 Reader/완료 포인터,
원본 운영 Projection을 목표 Projection 대신 전달하는 경우는 차단한다.
과거 Build 결과를 현재 Build로 조용히 바꾸지 않는다. 당시 설치 Build를 유지하거나 별도 실행으로 구분해야 한다.

Backend는 Actor Tenant·요청 Project를 확인하고 Run 범위 RBAC를 먼저 검사한다.
조회는 `engine_report.read`, 저장/복구는 `inventory_run.callback` 권한을 사용한다.
조회 권한은 복구 권한을 포함하지 않는다. Reader 미설정은 오류로 처리하고 로컬 파일 경로가 포함될 수 있는
저장소 I/O 오류는 `inventory_target_result_storage_unavailable`로 반환한다.
공용 API에 자동 등록하지 않으므로 기존 인증·API 동작은 바뀌지 않는다.

## 복구와 계산 권한

`recover(request, sources)`는 같은 실행·같은 참조로 `execute`를 반복한다.
연결 Artifact 저장 후, 또는 Projection 저장 후 프로세스가 종료돼도 새 프로세스에서 재검증·멱등 복구한다.
이미 있는 참조의 다른 내용은 덮어쓰지 않는다.

이 조립의 복구 대상은 **완료된 목표 계산 결과의 연결·Projection 저장**이다.
INPUT 또는 COMPLETE 슬롯이 없는 미완료 목표 계산은 차단한다. 그 경우는 먼저
[목표 계산 저장기](IO_TARGET_PIPELINE_STORAGE.md)의 별도 복구 계약으로 처리해야 한다.
후보 계산 중간 상태를 이어받거나 최신 Source로 재실행하는 계약이 아니다.

Thread로 호출된 작업의 HTTP 취소를 강제 계산 중단으로 보장하지 않는다.
강제 중단 검증은 격리 프로세스 `SIGKILL`과 새 프로세스 복구로 수행했다.

원본 Bundle은 기존 운영 결과다. 별도 선정 정책 발주 계획은 `EVIDENCE_ONLY`이고
이 서비스의 자동 게시·자동 발주 허용 값은 항상 `false`다. REVIEW는 증적을 조회할 수 있지만
자동 게시할 수 없다. BLOCK이 원본 운영 Bundle 생성을 막는 기존 규칙도 유지한다.

## 2026-10-21 검증과 미완료 경계 — 이력

- 신규 Engine Runtime Offline 테스트 20건, Platform 연동 테스트 10건을 통과했다.
- 실제 DLL로 ABC·XYZ 32개, LEGACY_STYLE 32개, 참조 88개와 계산용 주문 2건씩을 확인했다.
  연결/Projection commit 직후 SIGKILL 2건을 포함한 5개 사례를 새 프로세스로 복구·조회했다.
  원본 Bundle·Outbox가 바뀌지 않는지 확인했다.
- 별도 임시 경로에 설치한 Wheel과 실제 Backend Factory의 호출·조회·서비스 재구성을 검증했다.
  이 Backend 조립 테스트의 계산 Port는 Python 참조 대역이다. 실제 DLL 검증은 네트워크가 차단된
  별도 컨테이너에서 수행했으며 두 검증을 실제 Source 통합 E2E로 주장하지 않는다.
- 테스트용 Artifact SQLite는 임시 저장/트랜잭션 모형이다. 개발 PostgreSQL의 테이블 적재,
  FK·Trigger·Lock, 실제 V100 입력, 공용 Runtime 배포, ERP 연계는 검증하지 않았다.
- 전체 저장소 테스트·성능/메모리 측정·공식 강건성 경로 생성/본실험은 이번 범위가 아니다.

관련 회귀 수치·Wheel/Build 지문과 미변경 파일 대사는
[검증 증적](evidence/local-target-runtime-verification-20261021.json)에 기록한다.

## 2026-10-21 당시 남은 작업 순서 — 이력

**현재 기준선 — 독립 IO의 최초 실행 조립 완료 · InventoryEngine·Platform Backend**

- 이 Reader 자체는 완료 결과를 받는다. 후속 최초 실행 조립이 봉인 입력부터 원본 Bundle과 목표 계산을
  생성·저장하고 이 Reader로 연결한다. 독립 Offline 검증이며 실제 DB 적재는 아직 아니다.

**2. 실제 개발 연결 검증 — 승인 필요 · 개발 PostgreSQL·로컬 Runtime**

- 기존 테이블 재사용과 미적용 DDL·권한·적재 대상을 대사하고 실제 Write 범위를 먼저 제시한다.
  승인 후 독립 IO 결과의 Hash·건수·Rollback·재시도를 검증한다. 운영 Gate를 우회하지 않는다.

**3. Demand→IO 최종 연결 — 독립 검증 후 · DemandEngine·InventoryEngine·Platform Backend**

- 실제 Plan/Site의 Forecast·승인 설정·분류·입력과 설치 Build를 대조해 한 건의 정확한 연결을 검증한다.
  Forecast 재실행·공용 배포·ERP 발주는 이번 기준선에서 자동 승인되지 않는다.

**4. 검증된 변경 기준선 고정 — 별도 요청 후 · 두 저장소**

- InventoryEngine `inventory_engine_dev`, dsai-platform `codex/inventory-psi-strategy-v2`의
  관련 변경만 검토해 Commit한다. Push/MR/배포는 현재 작업에 포함하지 않는다.
