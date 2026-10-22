# 독립 IO 최초 실행 계약

## 현재 기준선과 적용 범위

2026-10-21, InventoryEngine `inventory_engine_dev`와 Platform Backend
`codex/inventory-psi-strategy-v2`의 명시적 개발 조립이다.
`InventoryInitialTargetExecution.execute(request, target_input_reference)` 한 번으로
봉인 입력 → 원본 Python 계산/저장 → 목표 계산/저장 → 연결·Projection 봉인 → 조회를 수행한다.
별도의 결과 Bundle이나 완료된 목표 계산을 미리 만들 필요는 없다.

기존 Python 계산·Run/결과 계약·업무 ID·결과 Hash·FSN·운영 Gate·DB 스키마는 바꾸지 않는다.
원본 운영 Mathematical Child는 그대로이고, C# 기반 선정 정책 발주 계획은 별도 EVIDENCE_ONLY다.
이 경로를 운영 Runtime Registry나 공용 HTTP Route에 자동 등록하지 않는다.

## 입력은 계산 전에 봉인한다

다음 두 입력을 기존 계약으로 준비한다.

1. Plan이 정확한 ID·Hash로 고정한 Runtime Input Manifest. 원본 Config·분류 Header·Mathematical Template을 포함한다.
2. 목표 저장기의 INPUT 참조 `{kind, content_hash, byte_count}`. 기존 계산 Binding·수학 입력·Config·분류·선택적 Catalog/Recipe를 포함한다.

`RunDurableTargetPolicyPipelineUseCase.seal_input()`은 기존 INPUT 형식/슬롯을 재사용해
두 번째 입력을 저장하며, 수량 전이를 실행하지 않는다. `read_input()`은 파일 Hash·크기·INPUT 슬롯·
동일 DLL/계산 Profile의 입력 계약을 확인한다. 기존 `execute/read/recover` 동작은 유지한다.
원천에서 Snapshot·Config·Catalog를 새로 생성하거나 승인하는 서비스는 아니다.

Runtime 최초 호출은 설치 Build·Claim·Scope·판단 주차·분류 Header와 두 입력의 일치를 확인한다.
목표 수학 입력은 Manifest에서 복원한 원본 수학 입력을 기존 `run_shadow_request`로 변환한 값과
완전히 같아야 한다. Config와 분류도 봉인 Manifest의 원문과 같아야 한다.
입력의 목록 정렬 등 정규화는 Manifest 생산 시 완료한다. 소비자가 불일치를 조용히 고치지 않는다.

기존 `PrepareInventoryInputUseCase`와 `apply_effective_policy_controls`를 호출해 Cut-off와 운영 Gate를
먼저 확인한다. FSN BLOCK은 자동 분석 Producer나 목표 수량 계산 이전에 차단한다.
REVIEW는 계산·증적 저장을 허용하되 자동 게시·발주는 허용하지 않는다.
Gate 적용은 새로 복원한 Prepared 사본에만 수행하며 원본 입력을 수정하지 않는다.

## 한 호출의 실행 순서

| 순서 | 수행 작업 | 재사용하는 경계 |
| --- | --- | --- |
| 1 | 정확한 두 봉인 입력·Build·운영 Gate 대사 | 기존 Manifest/Canonical/분류/입력 준비/정책 입장 검증 |
| 2 | 복구용 입력 Checkpoint 저장 | 기존 Append-only Artifact Repository |
| 3 | 원본 Baseline/Mathematical Bundle·Outbox 생성 | 기존 승인 입력 Resolver와 Python Result Pipeline |
| 4 | 전체 후보 → Simulation → 품목별 선정 → 발주 계획 생성 | 기존 목표 Pipeline·공통 Guard·C# TARGET_GUARDED 수량 전이 |
| 5 | 목표 결과 조각 → Manifest → COMPLETE 저장 | 기존 목표 결과 저장기 |
| 6 | 원본 Bundle 연결·Projection 저장 후 검증된 조회 반환 | 기존 Target Result Link·Runtime Reader·Projection Verifier |

기존 비용·Stress·PPO 입력은 원본 Resolver/Registry의 지원·검증 조건을 그대로 따른다.
미등록 구현이나 잘못된 Resource Binding을 수학적 전략으로 대체하지 않는다.
이번 실제 DLL Canary와 Backend 설치 검증은 수학적 입력을 사용한다.

## Checkpoint와 ID·Hash

복구용 문서는 `inventory.target_initial_input`, `inventory-target-initial-input-v1 / 1.0.0`이다.
저장 참조는 `artifact:inventory:<engine_run_id>:target-initial-input`으로 고정한다.
Run 하나는 물리 실행 시도 하나이므로, 다른 Attempt/Claim이 같은 Run의 목표 INPUT을 재사용하면 충돌시킨다.

문서에는 원본 Runtime 요청 전체, 설치 Build, 봉인 Manifest 전체, 목표 INPUT 참조를 보존한다.
문서/바이트 체크섬은 기존 Artifact 계약이 요구하는 저장 무결성 값이다.
새 Snapshot·정책·발주 계획·부모 결과 ID/Hash를 만들지 않는다.
최종 `result_pointer`도 기존 Bundle/INPUT/MANIFEST/연결/Projection 참조 형식을 유지한다.
응답 최상단의 `initial_input`에는 Checkpoint의 저장 참조·Hash만 추가한다.
원본 Bundle/Outbox에 새 필드를 넣거나 재봉인하지 않는다.

## 중단과 복구

`recover(request, target_input_reference)`는 이미 봉인된 Checkpoint를 요구한다.
원본 Python Bundle이 아직 없어도 저장 Manifest로 기존 Resolver를 조립해 생성할 수 있다.
Provider가 `None`이거나 원천이 사라져도, Checkpoint가 존재하면 최초 `execute` 재호출도 같은 원본으로 복구한다.

- Checkpoint 이전 실패: PSI/후보 시뮬레이션은 시작하지 않았다. `recover`는 차단하며 최초 `execute`를 다시 호출해야 한다.
- Checkpoint 이후/Bundle 이전: 저장 Manifest로 원본 계산부터 재실행한다.
- Bundle commit 이후: 기존 Outbox/Bundle을 검증·재사용하며 원본을 덮어쓰지 않는다.
- 목표 결과 조각 저장 도중: 동일 INPUT·Profile·DLL로 전체 후보를 다시 계산한다. 후보 수량 루프의 중간 상태 재개는 아니다.
- 연결/Projection commit 이후: 기존 저장 참조와 Hash로 멱등 복구한다.

다른 요청·Build·입력으로 Checkpoint를 재봉인해도 현재 요청/저장 원본과 문서 전체를 대조해 거부한다.
오염된 기존 Artifact를 덮어쓰거나 최신 Forecast·Config·분류를 탐색하지 않는다.

`read(request, result_pointer)`는 Checkpoint와 원본 Manifest까지 검증한 뒤 기존 Reader를 호출한다.
목표 후보·선정·발주 계획은 동일 DLL로 전체 재계산하며, 원본 Python Bundle은 기존 Verifier로 대사한다.
Artifact commit이나 원천 조회는 호출하지 않는다. 빠른 조회 SLA나 Thread 취소에 따른 강제 중단은 보장하지 않는다.

## Platform Backend 조립

`build_inventory_local_initial_execution_service`는 설치 Wheel 검증기를 통과한 Engine과
명시적으로 전달한 로컬 Manifest Provider·목표 저장기·Artifact Store·기존 RBAC를 연결한다.
`InventoryInitialTargetExecutionService.execute/recover`는 목표 INPUT 참조를 받고,
`read`는 기존 결과 포인터를 받는다.

Actor Tenant·Project와 Run 범위 권한을 먼저 확인한다.
저장/복구는 `inventory_run.callback`, 조회는 `engine_report.read`를 요구한다.
기존 결과 조회 Service의 동기 Reader는 그대로 지원하고, 새 비동기 최초 실행도 완료될 때까지 기다린다.
Engine 본체는 DB Pool·실제 Claim 생성·승인·Publication Callback·발주 API를 호출하지 않는다.
Platform의 [후속 Writer 호출 연결](IO_INITIAL_EXECUTION_PROJECTION_CALL.md)은 선택적으로 전달한 Pool에 한해
계산/복구 뒤 증적을 저장하고, 같은 Run 조회는 읽기 전용으로 처리한다. 기본 Artifact-only 호출은 유지한다.
실제 승인 DB Authority 검증이나 운영 입장 승격을 이 로컬 서비스로 대체하지 않는다.

## Offline 검증 범위

- 신규 Engine/Platform 연동 26건 포함 관련 회귀: **1,028 passed / 210 subtests passed**, Skip 0, 312.69초.
- Backend 관련 82건: Skip 0, 17.06초. 기존 HTTP 422 Alias 경고 4건을 유지했다.
- 실제 DLL: 32/32/88 후보 최초 실행과 5개 SIGKILL 복구, 총 8사례.
- Python 425파일 Ruff·Format, 기존 Schema JSON 42개 파싱, 두 저장소 Diff Check 통과.

전체 저장소 회귀·Coverage 측정·성능/메모리 합격을 주장하지 않는다.

신규 테스트는 결과가 없는 저장소에서 시작한다. 32/32/88 후보의 실제 생성·조회,
원천 조회 없는 재실행/복구, 잘못된 참조·Scope·Attempt·Build·재봉인 Checkpoint,
FSN BLOCK/REVIEW·미선정 원인 보존과 멱등 동작을 확인한다.
실제 DLL Canary는 격리 프로세스의 Checkpoint/Bundle/목표 조각/연결/Projection commit 직후
SIGKILL과 새 프로세스 복구를 포함한다.

Backend의 설치 Factory 테스트는 실제 임시 설치 Wheel과 참조 계산 Port를 사용한다.
실제 DLL은 별도 네트워크 차단 컨테이너에서 검증하며, 이를 실제 Source·Backend HTTP·DB E2E로 합쳐 주장하지 않는다.
검증 수치·파일 지문은 [증적](evidence/independent-initial-execution-verification-20261021.json)에 기록한다.

개발 PostgreSQL 접속/DDL/DML, 공용 Runtime/Backend 배포, 실제 V100 Source·발주·ERP 연계,
성능/메모리 측정, 공식 강건성 본실험, Commit/Push는 이번 범위가 아니다.
SQLite는 테스트용 Artifact 저장기이며 PostgreSQL FK/Trigger/Lock 검증을 대신하지 않는다.

## 남은 작업 순서

**현재 기준선 — 적재 범위 대사·SQL/Writer 정합화·실제 호출 연결은 Offline 완료**

- [실제 호출 연결 계약](IO_INITIAL_EXECUTION_PROJECTION_CALL.md)을 따른다. 실제 DB 적용/적재는 미완료다.

**1. 독립 IO 실제 적재·조회·복구 — 승인 필요 · 개발 PostgreSQL·로컬 Runtime**

- 승인된 범위에서 Hash·건수·누락·Rollback·멱등 복구를 검증한다.
  공용 배포와 실제 발주를 자동 포함하지 않는다.

**2. Demand→IO 최종 연결 — 독립 검증 후 · 세 저장소**

- 한 Plan의 정확한 Forecast·Config·분류·8종 입력과 설치 Build로 검증한다.
  Forecast 재실행이나 Gate 완화 없이 연결하며 실제 E2E Write는 별도 승인 대상이다.
