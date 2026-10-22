# 계산 Binding·전체 후보·선정·발주 계획의 로컬 저장·조회·복구

기준일: 2026-10-21 · InventoryEngine `inventory_engine_dev` · 기준 HEAD `99f2f12`.

상태: **개발용 Offline 저장·조회·재계산·프로세스 중단 복구 구현 완료**.
실제 DB 적재, Platform Run/Result Bundle 등록, 공용 Runtime 배포와 발주 승인은 포함하지 않는다.

후속 상태(2026-10-21): [기존 Result Bundle·Projection 연결](IO_TARGET_RESULT_LINK_CONTRACT.md)을
개발용 선택 의존성으로 구현·검증했다. 원본 Bundle/Outbox는 유지한다.
후속 [로컬 Reader](IO_LOCAL_TARGET_RESULT_RUNTIME_CONTRACT.md)와
[독립 최초 실행](IO_INDEPENDENT_INITIAL_EXECUTION_CONTRACT.md)의 Backend 조립도 Offline 검증했다.
기본 Runtime Registry·실제 DB 연결은 아직 바꾸지 않았다. 아래 검증 이력은 당시 범위다.

후속 상태(2026-10-22): [최신 기준선](IO_DEVELOPMENT_BASELINE.md)의 대표 9품목 개발 Run에서
실제 DLL·Runtime·Projection을 연결해 21개 테이블·41,342행 적재·조회·중복 저장을 확인했다.
이 문서의 기존 Offline 이력은 보존한다. 적재 당시 별도였던 계산 완료 이벤트·상세 조회도 이후 연결했다.
기본 운영 Runtime 등록·운영 게시·공용 배포는 여전히 별도이며, 최신 상태는 개발 기준선을 따른다.

후속 보정(2026-10-22): 같은 요청에서 성공한 전수 재계산 검증만 재사용하도록 구현·Offline 검증했다.
최초 계산과 별도 검증은 유지하며, 파일·Hash·건수·Binding 검사를 생략하지 않는다.
[요청 내 재사용 규칙](#31-같은-요청의-재계산-검증-재사용)과 [최신 기준선](IO_DEVELOPMENT_BASELINE.md)을 따른다.
기존 V100 Build·Run·Artifact와 개발 PostgreSQL·FSN·운영 Gate·공용 Runtime은 변경하지 않았다.

## 1. 연결 대상과 변경하지 않은 범위

[목표 실행 연결](IO_TARGET_GUARDED_EXECUTION.md)의 `RunTargetPolicyPipelineUseCase`를
[RunDurableTargetPolicyPipelineUseCase](../../src/dsio_inventory_engine/recommend_replenishment/application/target_durable.py)로 감싼다.
기존 Python/C# 계산식, 후보 생성·평가·선정, Guard·FSN·운영 Gate는 수정하지 않았다.
기본 Runtime/CLI를 이 진입점으로 바꾸거나 운영 실행을 허용하지도 않았다.

[LocalTargetPipelineArtifactStore](../../src/dsio_inventory_engine/infrastructure/target_pipeline_artifacts.py)는
기존 `LocalRobustnessArtifactStore`의 POSIX 파일 게시·읽기 장치를 재사용한다.
임시 파일 기록→파일 `fsync`→배타적 Hard Link→디렉터리 `fsync` 순서를 유지한다.
비정상 경로·심볼릭 링크·루트 교체·128,000,000바이트를 넘는 레코드를 차단한다.
기존 V1/V2/V3 저장 형식이나 Reader를 수정·변환하지 않는다. 전용 `target-*` 파일명으로 구분한다.

기존 Canonical/Prepared/Config/분류/Catalog/후보/선정/PSI/행동/주문 Hash와 Run ID를 보존한다.
새 업무용 Run·Snapshot·발주 계획 ID나 부모 결과 Hash는 없다.
저장 레코드·Manifest·포인터에는 바이트 무결성 확인에 필요한 체크섬만 별도로 둔다.
이 체크섬은 정책이나 계산 결과의 기존 의미를 바꾸지 않는다.

## 2. 입력부터 완료 표시까지의 순서

```text
봉인 원본 입력·계산 Binding·Config·분류·Catalog와 재구성 규칙
    ↓ 입력 레코드 저장 + 기존 engine_run_id의 INPUT 포인터 고정
실제 목표 Pipeline 실행
    ↓ 같은 DLL·Profile의 후보 평가 → 선정 → 선정 정책 발주 계획 재실행
계산 Binding/실행 Binding·검색 Header·Baseline·전체 후보·선정·발주 계획 조각 저장
    ↓ 모든 참조·건수·기존 Hash 대사
Manifest 저장 → 전체 조각 재조회·원본 복원 대사
    ↓
기존 engine_run_id의 COMPLETE 포인터를 마지막에 게시
```

입력 포인터는 첫 수량 전이 호출 전에 고정한다. Config·분류·Catalog 원문과 규칙/Feature 계산 Build 참조를 함께 보존하므로
새 프로세스가 원천 DB나 최신 Config를 찾지 않아도 재구성할 수 있다. DLL 자체는 저장 입력에 포함하지 않는다.
신뢰하는 로컬 조립부가 동일 DLL Hash의 계산 Adapter를 명시적으로 제공해야 한다.

후보는 성공한 것만 남기지 않는다. 실패/제외/미선정 사유, 원 행동·Guard 결과·PSI·발주일·도착일을 원래 문서대로 보존한다.
후보 평가는 1개씩, 발주 계획의 긴 목록은 최대 128행씩 나눈다. 이 분할은 **저장 단위**이지 수량 계산 단위가 아니다.
단일 후보나 입력 레코드 자체가 파일 상한을 넘으면 실패한다.

2026-10-22 보정: 전체 결과를 단일 JSON 파일처럼 128,000,000바이트로 제한하던 검사를 제거했다.
동일한 후보별 조각으로 저장과 실제 재계산 비교를 수행한다. 개별 저장 레코드는 기존처럼
128,000,000바이트 이하이며, 입력·전체 조각·Manifest의 원문 합계는 20,000,000,000바이트 이하로 제한한다.
저장 전 실제 레코드 인코딩으로 크기를 확인하고, 읽을 때도 전체 참조의 크기를 먼저 확인한다.
압축으로 제한을 우회하거나 완료 전의 조각을 성공 결과로 노출하지 않는다.
기존 레코드 형식·업무 ID·계산 결과 Hash와 이전 결과 읽기는 유지한다.

실제 V100 9품목 진단에서는 전체 결과 129,075,722바이트가 종전 검사에서 실패했다.
같은 결과의 301개 조각은 총 129,151,081바이트이고 최대 조각은 1,979,439바이트였다.
이 진단은 실제 C# 계산 후 조각 저장·복원만 확인했으며 COMPLETE 게시·DB 적재는 하지 않았다.
보정 Wheel의 전체 Runtime·DB 검증 결과는 Platform의
`docs/backend/dsim/inventory-v100-csharp-target-development-v1.md`에서 별도로 관리한다.
그 상한을 넘는 결과를 분할로 우회해 게시한 뒤 복구할 수 없는 상태를 만들지 않는다. 상한을 넘으면 완료 게시 전에 실패한다.
대용량 성능 최적화·추가 자원 관리 고도화는 이번 작업이 아니다.

Manifest는 후보 키·Hash의 순서 있는 전체 목록, 후보 수·PSI 행 수·선정/미선정 수·판단/주문 수와 기존 결과 Hash를 보존한다.
조각 누락·중복·순서 변경·건수 불일치·입력/Run/Build 바인딩 불일치는 정상 완료로 읽지 않는다.
외부에서 받은 계산 View는 `persist()`의 전체 Pipeline 재계산을 통과해야 저장할 수 있다.

## 3. 조회 상태와 복구의 의미

| 진입점 | 확인 범위 | 반환 상태 |
| --- | --- | --- |
| `execute()` | 입력 선저장→실제 계산→분할 저장→복원 대사 | `RECOMPUTED_VERIFIED` |
| `persist()` | 외부 View를 같은 입력/DLL로 재계산 후 저장 | `RECOMPUTED_VERIFIED` |
| `read_integrity()` | 모든 참조/바이트·건수·기존 Hash·Binding 확인, 수량 전이 호출 없음 | `INTEGRITY_VERIFIED` |
| `read_verified()` | 위 확인 후 전체를 동일 DLL로 재계산. 같은 요청에서 동일 결과의 검증이 이미 성공한 경우에만 수량 재계산 생략 | `RECOMPUTED_VERIFIED` |
| `recover()` | INPUT에서 재구성, 미완료면 전체 계산 후 저장 재개; 완료면 전체 재계산 검증 | `RECOMPUTED_VERIFIED` |

무결성 조회는 새 운영 승인이나 C# 계산의 독립 증명이 아니다. 레코드와 체크섬을 함께 바꾼 잘못된 메타데이터는
무결성 조회만으로 계산 오류인지 판정할 수 없다. 전체 재계산 검증은 그러한 변경도 실제 결과와 대조한다.
선택적으로 `expected_input_reference`를 전달하면 호출자가 고정한 입력 참조와도 대조한다.
이 로컬 저장기는 신뢰하는 호출자가 소유한 전용 디렉터리를 전제로 하며, 악의적인 OS 관리자에 대한 전자서명이 아니다.

복구는 **후보 중간의 수량 상태를 이어서 계산하지 않는다**. 저장 입력으로 전체 Pipeline을 다시 실행하고
이미 게시된 동일 바이트는 멱등 확인한다. 미완료 조각을 검증 없이 계산 결과로 채택하지 않는다.
완료 포인터가 있는데 조각이 손상/누락됐으면 자동 복구로 덮어쓰지 않고 실패한다.
Build·Profile·입력·권한이 달라지면 Python으로 대체하거나 최신 Source로 재시도하지 않는다.

같은 Run·같은 입력·같은 결과는 새 파일이나 이력을 만들지 않는다. 같은 Run에 다른 입력/Manifest를 연결하면 충돌한다.
입력이 바뀐 계산은 기존 계약대로 새 `engine_run_id`가 필요하다.
INPUT 포인터 게시 전의 중단은 복구할 실행으로 보이지 않는다. 게시되지 않은 임시 파일과 참조 없는 레코드는
완료 결과로 간주하지 않으며 이번 작업에서 자동 삭제·정리하지 않았다.

### 3.1 같은 요청의 재계산 검증 재사용

[TargetReplayVerification](../../src/dsio_inventory_engine/recommend_replenishment/application/target_verification.py)는
한 요청·동일 Worker의 검증 이력만 보존한다. 결과 전체를 저장하는 전역 캐시가 아니며,
한 번에 한 결과의 검증 Key와 Kernel 객체만 잠시 기억한다. 새 업무 ID·Hash·저장 레코드는 추가하지 않는다.

- `read_verified()`는 항상 INPUT/COMPLETE 포인터, 모든 조각의 바이트·Hash·순서·건수·결과 Hash를 다시 확인한다.
  원본 입력을 복원하고 현재 DLL·Profile·입력 Binding·FSN/운영 Gate를 다시 검사한 뒤 재사용 여부를 판단한다.
- 재사용 Key는 기존 Run ID·INPUT Hash·Manifest Hash·DLL Hash이며, 계산 Kernel 객체도 같아야 한다.
  입력·Manifest·DLL·Kernel이 달라지면 이전 검증을 사용하지 않는다. 다른 Runtime 요청의 권한·Scope·Build 대사는
  바깥 Reader에서 매번 수행하며, 검증 재사용은 권한 검사나 Source 대사를 대신하지 않는다.
- `runner.verify_result()`가 전체 후보·선정·발주 계획을 별도 계산해 같은 결과임을 확인한 뒤에만 이력을 남긴다.
  `execute()`의 최초 계산·`_publish()`의 저장 성공·`read_integrity()`·외부의 검증 상태 문자열은 이를 대신하지 못한다.
- 같은 요청의 후속 Link·Projection·최종 조회는 물리·의미 검사를 계속 수행하고 중복 수량 재계산만 생략한다.
  `RECOMPUTED_VERIFIED`는 그 요청 안에서 전수 재검증이 이미 성공했다는 의미이며, 무결성 조회의 승격이 아니다.
- 재사용 범위를 반환·예외·취소로 벗어나면 이력을 폐기한다. 새 요청·스레드·비동기 작업·프로세스로 넘기지 않는다.
  실패/미완료 검증을 재사용하지 않으며, 반환 객체를 수정해도 다음 조회의 원본 결과는 달라지지 않는다.

HTTP 취소가 이미 실행 중인 Worker 스레드를 강제 중단한다는 뜻은 아니다.
강제 중단·새 프로세스 복구는 별도 SIGKILL 사례로 검증했다.

최초 실행 조립은 계산 1회와 별도 전수 검증 1회를 수행한다. 완료 결과 복구·강한 조회는 요청마다 검증 1회다.
미완료 입력에서 복구하면 기존처럼 전체 계산을 다시 수행하고, Runtime 조립이 별도 검증까지 마친다.
후보 내부의 수량 전이·검증 순서와 `completed_transition_calls`는 바꾸지 않는다.
분리된 Writer Worker의 독립 검증·DB Binding·Receipt·실제 행 대사도 그대로다.

관련 [단위 테스트](../../tests/unit/test_target_verification_scope.py)와
[Offline 실행 비교](../../tests/integration/test_target_verification_reuse_offline.py)는
같은 Hash·호출 횟수, 누락·변조·다른 Binding·Kernel 교체, 실패·취소와 요청 간 분리를 확인한다.
[실제 DLL 실행기](../../scripts/verify_initial_target_execution.py)에서는 32/32/88개 후보의 결과 Hash·참조가
재사용을 끈 대조 경로와 같았다. 최초 호출은 각각 2,970→990 / 2,970→990 / 8,010→2,670회였고,
새 프로세스의 완료 복구·강한 조회는 각각 495 / 495 / 1,335회였다. 전체 Pipeline으로는 최초 6→2회,
완료 복구·강한 조회는 각 1회다. 1품목·3주 개발 사례이며 응답시간·대규모 V100 개선율은 측정하지 않았다.
정상 3개·게시 직후 SIGKILL 5개의 총 8개 사례에서 복구와 기존 Hash를 확인했다.

## 4. 권한과 기존 결과의 보존

저장 완료 여부는 바깥 응답의 `durable_local_artifact=true`로만 표시한다.
기존 발주 계획 문서의 `artifact_sealed/evidence_persisted`나 자동 게시·발주 플래그를 수정해 기존 Hash를 바꾸지 않는다.
`REVIEW`는 계산/Evidence만 보존하고 자동 게시·발주는 계속 차단한다. `BLOCK`은 미선정·발주 없음 상태를 유지한다.
V1/V2 Gate, 실제 Python 정책/Guard, 수량 DLL 및 기존 계약/Schema 파일은 그대로다.

## 5. 검증과 한계

신규 Unit·Offline Integration은 저장 경로, 전체 후보/Hash, 누락·중복·잘림·변조·심볼릭 링크,
동일 입력 멱등·동시 게시, 다른 입력 충돌, 실패 후보·미선정 사유, 무수요/비용 미확보,
FSN REVIEW/BLOCK V1/V2와 서로 다른 Build/환경의 복구 차단을 확인한다.

[실제 DLL 검증 실행기](../../scripts/verify_target_storage.py)는 기존 고정 Python.NET 이미지에서 별도로 실행했다.
네트워크를 끄고 저장소·DLL은 읽기 전용, 전용 임시 출력 폴더만 쓰기 가능하게 했다.
DLL Hash는 `9bc7b1576eb3bcd00d11fb28affb77f7b875764667f120ca54ac98faa944fe7f`다.

- ABC·XYZ 32개, LEGACY_STYLE 32개, 참조 88개 후보를 저장하고 새 프로세스에서 전체 재계산했다.
  기존 Python의 검색·선정·발주 계획과 전체 내용/Hash가 일치했다.
- 별도로 LEGACY_STYLE 입력·결과 조각·Manifest·완료 포인터 게시 직후 프로세스를 `SIGKILL`로 종료했다.
  4개 모두 새 프로세스가 저장 입력만으로 복구했고 이미 게시한 바이트는 그대로였다.
- 실제 DLL 검증은 7개 확인 항목이다. Unit의 대역 Port 결과와 구분한다.
- 신규 Unit·Offline Integration **39 passed**(21.73초), 이를 포함한 관련 회귀 **838 passed / 158 subtests passed**(152.77초)다.
  Python 409파일 Ruff·Format·`git diff --check`와 기존 Schema JSON 42개 파싱도 확인했다.

정확한 테스트 수·파일 지문·실제 확인 결과는 [검증 증적](evidence/target-pipeline-storage-verification-20261021.json)을 따른다.
전체 저장소 회귀·성능/메모리·대규모 V100·실제 DB·운영 자료·macOS/Windows CLR를 검증한 것은 아니다.
자원 관리 추가 고도화와 공식 강건성 본실험도 계속 보류한다.

## 6. 남은 작업 순서

**1. 독립 IO 실행의 저장 결과를 업무 조회 구조와 대사 — 후속 완료 · InventoryEngine**

- 계산 Binding·후보·선정·발주 계획의 저장 참조를 기존 Result Bundle/Projection 계약과 대조한다.
  실제 DB 적용 없이 조회할 수 있는 범위와 추가 연결이 필요한 필드를 정리하고 Offline 검증한다.
  후속 [결과 연결 계약](IO_TARGET_RESULT_LINK_CONTRACT.md)에서 원본 Bundle 보존·선정 계획의 EVIDENCE_ONLY Projection을 검증했다.

**현재 기준선 — 로컬 Runtime·Platform 조회·최초 실행 조립 완료 · InventoryEngine·dsai-platform Backend**

- 새 결과 Reader와 기존 Run/결과 포인터를 로컬에 연결하고 최초 계산·저장·조회·차단·복구를 Offline 검증했다.
  대표 9품목의 실제 개발 DB 적재는 후속 이력에서 완료했다. 요청 내 중복 재계산 축소도 Offline 검증을 마쳤다.
  남은 순서는 [최신 기준선](IO_DEVELOPMENT_BASELINE.md)에서 관리하며 완료된 적재·회귀를 다시 실행 대상으로 잡지 않는다.
  실제 DB Write·Migration·공용 Runtime 재배포·실제 E2E Write는 별도 승인 필요다.
  발주요청 확정·ERP 전송은 여전히 후속 범위다.
