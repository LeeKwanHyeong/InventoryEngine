# DSIM Source·설치 Build·Projection 실행 조립

기준일: 2026-10-15. InventoryEngine `inventory_engine_dev` / Platform `codex/inventory-psi-strategy-v2`의 로컬 후보다.

## 완료 범위

- [실행 조립](../../src/dsio_inventory_engine/run_inventory/projection_execution.py)은 설치 Build와 정확한 Claim/Manifest를 대조하고 기존 PSI Pipeline·Projection 봉인·검증을 연결한다.
- [분류 Source](../../src/dsio_inventory_engine/inventory_evidence/classification_source.py)는 정책 분석 OFF여도 원본 Header·전체 품목을 봉인한다. Header Hash/UUID와 Claim/Config/Scope·Effective Policy Hash·품목 Coverage를 검증한다.
- 승인 입력 Resolver가 분류 원문을 Command로 전달하며 기존 Bundle Writer가 PSI·Bundle·Outbox와 같은 원자 경계에 포함한다. Reader는 원문과 Content/Byte Hash를 재검증한다.
- Projection은 새 분류 Artifact를 사용한다. 분석 ACTUAL_INPUT도 있으면 두 원문이 같아야 한다. Header Hash·Effective Policy Hash·Wrapper Hash를 혼동하지 않는다.
- 새 조립 경로는 분류 판단 주차와 Plan 주차를 계산 전에 확인한다. 기존 FSN·정책·운영 Gate를 바꾸지 않는다.
- Platform `InventorySourceProjectionExecutionService`가 기존 승인 Source 조회 Service와 설치 Engine을 연결한다. Callback 권한·Tenant/Project를 Source/Artifact 접근 전에 검사하며 이웃 checkout을 자동 import하지 않는다.

## 봉인 포인터와 복구

내부 `inventory.dsim_source_execution / 1.0.0` Artifact에 Installed Build, 전체 Claim/Run/Attempt/입력 Binding, Runtime Input Manifest, Bundle·분류·Outbox·Projection의 정확한 Reference/Hash를 고정한다. 기존 Result Bundle/Run Wire Version과 DB Catalog는 바꾸지 않는다.

Reference는 `artifact:inventory:<run>:<attempt>:source-execution`이며 Append-only다. 복구 때 원본에서 Projection과 Execution 문서를 다시 구성한다. 자기 Hash만 맞는 재봉인 변조도 차단한다.

- 분류/PSI/Bundle/Outbox 봉인 → Projection Chunk/Manifest 봉인 → 실행 포인터 봉인 순서다.
- 뒤 단계 실패 시 이미 봉인한 PSI를 지우거나 재계산하지 않는다. 원 Source 없이 남은 단계만 재시도한다.
- 실행 포인터 commit 응답 유실은 고정 Reference로 복구한다. Chunk 크기를 바꿔 재시도해도 최초 포인터를 유지한다.
- 분류 원문이 없는 과거 Outbox는 수정하지 않는다. 새 조립에서는 `SOURCE_EXECUTION_CLASSIFICATION_REQUIRED`로 차단하며 최신 분류를 대신 연결하지 않는다.
- 설치 Build·입력·Catalog가 바뀐 신규 계산을 기존 Run 복구로 위장하지 않는다. 정확히 일치하지 않으면 차단하고 새 Run으로 구분한다.

## 설치 후보와 검증 경계

임시 디렉터리에서 Wheel을 빌드·설치하고 checkout `src` 없이 설치 패키지로 Source/Platform 통합 19건을 검증했다. 실제 import 경로도 설치 Target 아래인지 확인했다. 현재 `0.15.0`은 미배포 로컬 후보이며 기존 Backend/Runtime 설치는 바꾸지 않았다.

| 구분 | Hash |
| --- | --- |
| Wheel SHA-256 | `afaafad303fd22e270aef3c1ea47658b700d915fbf62b68875c2aa1938c812f7` |
| Installed Build | `21a0efb40c7de14ac42f385c983ee5685d27cf18ad21b6c5fedfeeef2f3b6097` |
| Package Source Closure | `a94ec96827ce35e26fe57b8aeef94caa3088848b392079fd84ddfaccbdec4be2` |

Wheel Byte Hash는 입력 Hash·Source Closure와 별개다. 신규 DB Table/컬럼/Index/Migration은 없다. Run별 분류 원문 크기만큼 저장량이 늘며 기존 Artifact 크기 제한을 따른다. 보존기간·Archive·RTO와 대용량 처리량은 이번 테스트로 확정하지 않는다.

승인 Authority·Audit·Writer는 Offline 대역으로 연결했다. 실제 승인 DB를 조회하거나 V100 3,622품목 E2E를 수행한 것은 아니다. 실제 PostgreSQL 접속·Migration·DDL/DML·기본 Runtime/HTTP 활성화·공용 배포·실제 발주·Commit·Push는 하지 않았다.

최종 검증: Engine 전체 **873 passed / 9 skipped / 669 subtests passed**, 기존 Framework 경고 3건. Platform 관련 **293 passed**, 기존 Framework 경고 1건. 격리 설치 Source/Platform 조립 **19 passed**. Ruff·Format·Catalog/DDL 생성물·Diff Check 통과.

완전한 Platform 조립 호출·포인터 필드·권한·복구 계약은 Platform `docs/backend/dsim/inventory-dsim-source-execution-v1.md`에 있다. [개발 기준선](IO_DEVELOPMENT_BASELINE.md)의 과거 검증 이력도 보존한다.

## 남은 작업 순서

**다음 작업 — 두 저장소의 변경과 설치 후보를 검토한다.**

- InventoryEngine `inventory_engine_dev`, Platform `codex/inventory-psi-strategy-v2`의 관련 변경만 선별한다. 개인 보고서·환경·임시 Wheel은 제외하고 Commit·Push는 별도 요청 후 수행한다.

**승인 필요 — 개발 PostgreSQL과 실제 Source/Claim을 연결해 검증한다.**

- Schema/Role 대사 → Rollback Canary·FK/Trigger·동시성 → 필요한 Migration 적용 → 실제 승인 입력·Claim·조회·복구 순서다. 079와 Evidence DDL은 별개이며 기존 074~078을 재실행하지 않는다. 공용 배포·실제 발주를 자동 포함하지 않는다.
