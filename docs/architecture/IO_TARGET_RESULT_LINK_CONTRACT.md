# 저장된 목표 계산 결과와 Result Bundle·DSIM Projection 연결

기준일: 2026-10-21 · InventoryEngine `inventory_engine_dev` · 기준 HEAD `99f2f12`.

상태: **독립 로컬 조회·재계산 검증·중단 복구의 Offline 연결 완료**.
개발 PostgreSQL, 실제 Run Claim, Platform API/화면과 공용 Runtime은 변경하지 않았다.

## 1. 기존 계약을 대사한 결과

기존 Result Bundle의 Baseline·Mathematical Child와 [저장된 목표 Pipeline](IO_TARGET_PIPELINE_STORAGE.md)의
후보 탐색·선정·발주 계획은 같은 결과 종류가 아니다. 선정 정책을 재실행한 발주 계획으로 기존 Mathematical Child를
교체하면 전략·입력·효력 포인터의 의미가 달라진다. 기존 정책 분석 검증기도 Catalog 결과를 PSR/PSEL로 받아들이지 않는다.

또한 기존 DSIM Registry에는 `ORDER_PLAN` Child 종류가 없다. `RECOMMENDED_PSI`를 EVIDENCE_ONLY로 사용하면
현 물리 계약의 Mathematical OPERATIONAL 제약에 맞지 않는다. 이 작업에서는 기존 계약이나 DDL을 완화하지 않았다.

연결 방식은 다음으로 고정한다.

| 대상 | 연결·표현 방식 | 보존하는 것 |
| --- | --- | --- |
| 원본 Bundle·Outbox | 변경하지 않고 정확한 Reference·Hash로 연결 | 기존 내용·Hash·effective Child·게시 Gate |
| 계산 Binding·입력·완료 Manifest | 별도 연결 Artifact가 기존 저장 참조를 고정 | 기존 Run·8종 Snapshot·Profile·DLL Hash |
| 전체 후보 | 기존 `POLICY_SEARCH` Registry와 후보·평가·PSI·행동 Projection | PSR/OCPSI와 후보 ID·Hash, 실패 후보 원문 |
| 품목별 선정 | 기존 `POLICY_SELECTION` Registry·선정 행 | PSEL/OCPSEL과 선정/미선정 사유 |
| 선정 정책 발주 계획 | 동일 선정 Child의 `simulation_id=SELECTED_ORDER_PLAN` | PSI·판단·발주일·도착일·허용 수량·조정 사유 |

새 업무용 Run/분류/정책/발주 계획 ID나 부모 결과 Hash를 추가하지 않는다.
검색·선정 Artifact의 기존 Reference와 Content Hash를 재사용하고, Registry ID도 기존 `AN-<Hash>` 규칙을 따른다.
새 연결 문서에는 Artifact 저장 계약이 요구하는 문서/바이트 체크섬만 둔다.
선정 정책 발주 계획은 항상 EVIDENCE_ONLY다. 실제 발주 확정이나 운영 정책 승격을 의미하지 않는다.

## 2. 연결 Artifact와 필수 검증

[TargetResultLinkUseCase](../../src/dsio_inventory_engine/inventory_evidence/target_result_link.py)는
기존 Evidence Repository의 `commit_artifacts/read_artifact` 경계를 사용한다.
Reference는 기존 Run·Attempt로 결정되는 `artifact:inventory:<Run>:<Attempt>:target-policy-result`이고,
계약 Key는 `inventory.target_result_link`, 문서 계약은 `inventory-target-result-link-v1 / 1.0.0`이다.

문서에는 다음 근거를 고정한다.

- 기존 Claim의 Run·Attempt·tenant/project·Site Binding·할당 행 Version·원천 입력 Binding
- Runtime 요청 Hash, 원본 Bundle Reference·Hash와 Canonical Hash
- 목표 저장기의 정확한 INPUT·COMPLETE Manifest 참조와 8종 Canonical Snapshot을 고정한 계산 Binding
- 기존 검색·선정 Artifact 참조/Hash와 기존 Order Plan Binding
- 전체 후보·선정·PSI·판단·주문 건수 및 기존 결과 Hash
- 자동 게시·자동 발주·운영 승인 `false`

쓰기와 복구 전에 기존 Bundle Reader, Canonical/Runtime/Effective Policy 검증기를 통과한다.
저장 Canonical은 Run·Cycle·Plan·Site를 포함해 원본 Bundle 입력과 정확히 같아야 한다.
원본 분류 Header와 Config Hash·판단 주차, 수학 입력 Snapshot Binding도 일치해야 한다.
단순히 Effective Policy Hash가 같다는 이유로 다른 분류 Header를 받아들이지 않는다.

그 뒤 `read_verified()`로 동일 입력·Profile·DLL의 전체 후보·선정·발주 계획을 재계산한다.
`INTEGRITY_VERIFIED` 조회를 강한 검증으로 표시할 수 없다. 재계산과 정확한 Prepared 일치 확인을 통과한 뒤에만
기존 검색/선정 원문 2개와 연결 Artifact를 하나의 저장 작업으로 기록한다.
원본 Bundle 또는 Outbox에는 새 포인터를 끼워 넣거나 재봉인하지 않는다.

## 3. 기존 Projection에서 읽는 방법

[DsimSourceProjector](../../src/dsio_inventory_engine/inventory_evidence/projection_source.py)에
명시적인 `target_result_reader` 선택 의존성을 추가했다. 생략하면 기존 결과가 그대로이며 새 연결을 자동 탐색하지 않는다.
의존성을 제공하면 해당 Run·Attempt의 연결 Artifact가 반드시 있어야 한다. 없거나 검증되지 않으면 차단한다.
서로 다른 저장소에서 같은 참조의 다른 내용을 제공하는 경우도 거부한다.

[목표 결과 Projection](../../src/dsio_inventory_engine/inventory_evidence/target_result_projection.py)은
기존 후보/평가/선정·PSI·행동 변환기를 재사용한다. 물리 Catalog·Schema·DDL은 수정하지 않았다.

- 검색/선정은 원래 계약을 유지한 Artifact로 함께 저장한다. Catalog 결과를 기존 PSR 검증기에 우회 입력하지 않는다.
- 기존 분석과 검색/선정 Hash가 모두 같으면 중복 Registry·후보·평가 행을 만들지 않는다.
  다른 Catalog 결과이면 별도 근거를 유지한다. 한쪽 Hash만 충돌하는 모호한 연결은 거부한다.
- 선정 발주 계획은 기존 선정 Child 아래의 별도 Simulation이다. 기존 Baseline/Mathematical PSI나 유효 Child를 교체하지 않는다.
- 선정 계획의 주문 행은 기존 `tb_io_replenishment_proposal`에 해당하는 **계산용 Projection**이다.
  자동 게시/발주 플래그는 `false`이며 원 행동과 Guard의 조정·거부 사유를 보존한다.
- 실패 후보는 삭제하지 않는다. 후보 원문/Hash를 보존하고 진단 행으로 원인을 남긴다.
  존재하지 않는 평가나 주문을 0으로 꾸며 성공 행으로 만들지 않는다.

기존 Projection Manifest·Chunk Writer/Verifier가 새 Source 참조와 행을 함께 봉인·검증한다.
여기서 테이블별 건수는 봉인 Projection이 담은 행 수다. 실제 `TB_IO_*` 테이블에 적재했다는 뜻이 아니다.
새 근거와 행이 추가된 Projection Hash는 기존 Projection과 달라질 수 있다.
선택 의존성이 없는 기존 경로의 내용/Hash와 원본 Bundle·계산 결과 Hash는 유지한다.

## 4. 조회·멱등·중단 복구

`execute()`는 검증 후 기록하고 다시 읽어 확인한다. 동일 참조·동일 바이트는 멱등이며 다른 내용은 충돌한다.
`read()`는 연결 문서·원문·목표 입력/Manifest·Bundle을 실제 저장소에서 읽고 전체 Pipeline을 다시 검증한다.
저장된 원문·연결 Hash·건수·Binding을 다시 계산해 비교하므로 임의로 Hash를 재봉인해도 통과하지 않는다.

`recover()`는 원본 Bundle·요청 Scope·고정 입력을 먼저 확인한 뒤 기존 목표 저장기의 복구를 호출한다.
미완료 계산은 원래 저장 입력으로 재계산하고 동일 연결을 재구성한다. 최신 Forecast·Config·모델·DB Source를 찾지 않는다.
계산 중간 상태를 이어서 실행하는 기능은 아니다. 같은 Run의 다른 입력/Build를 새 Retry로 취급하지 않는다.

연결 또는 Projection 기록 직후 응답을 받지 못했어도 새 인스턴스에서 동일 참조를 조회·검증·멱등 복구할 수 있다.
Projection 행 쓰기는 기존 Unit of Work의 트랜잭션·Receipt 계약을 유지한다.
로컬 모형에서는 중간 행 기록 실패 시 전체 Rollback 후 재시도되는 것을 확인했다.
이 모형은 실제 PostgreSQL의 FK/Trigger/Lock 의미나 Migration 적용을 검증하지 않는다.

`REVIEW`는 계산 근거만 연결한다. `BLOCK`은 기존 운영 Bundle 생성 자체가 차단될 수 있으며 이 제약을 유지했다.
BLOCK을 이유로 새 가짜 Bundle을 만들거나 Source 설정/Gate를 완화하지 않는다.

## 5. Offline 검증 범위

신규 Unit·Integration은 다음을 확인한다.

- 기본 ABC 후보·ABC/XYZ 32개·LEGACY_STYLE 32개·참조 88개 전체와 기존 원문/Hash
- 후보·선정·선정 계획의 관계와 내부 FK 키 대사, 원본 Bundle·Outbox 바이트 보존
- tenant/project·Run/Attempt·Cycle/Plan/Site·할당 행 Version 불일치의 복구 전 차단
- 다른 DLL/환경, 연결 Hash·건수·선정 Binding·Manifest·권한 변조와 원문 누락 차단
- 재봉인한 Projection의 후보/PSI 누락·주문량/도착일 변경·권한 상향·근거 참조 누락 차단
- 기존 분석 중복 방지·다른 Catalog 근거 보존, 기본 Projector의 기존 결과 유지
- 저장 전/후 중단 복구·동일 참조 충돌·Projection 부분 기록 Rollback·멱등 Receipt

신규 테스트 **39 passed**(38.78초), 이를 포함한 관련 회귀 **965 passed / 7 skipped / 210 subtests passed**(203.03초)다.
선택적 Platform Source 연동 7개는 `DSAI_PLATFORM_ROOT`를 지정하지 않아 건너뛰었다.
Python 415파일 Ruff·Format·`git diff --check`와 기존 Schema JSON 42개 파싱도 확인했다.
전체 저장소 회귀나 실제 Platform E2E 합격을 뜻하지 않는다.

[실제 DLL 실행기](../../scripts/verify_target_result_link.py)는 고정 Python.NET 이미지에서 별도 수행했다.
원래 검증 이미지에는 SQLite 공유 라이브러리가 없어, 기존 로컬 `python:3.12-slim-bookworm` 이미지에서
라이브러리만 임시 추출해 읽기 전용으로 제공했다. 다운로드·설치·공용 이미지 수정은 하지 않았다.
네트워크를 끄고 코드/DLL은 읽기 전용, 전용 임시 폴더만 쓰기 가능하게 했다.

실제 C#에서 32·32·88개 후보를 연결하고 새 프로세스에서 저장 그래프만으로 Projection을 재검증했다.
연결/Projection 저장 직후 `SIGKILL`한 2개도 복구했으며 원본 Bundle·Outbox는 같았다.
이 5개 항목은 Unit 대역 결과와 구분한다. 임시 SQLite는 Artifact/Outbox 모형이며 개발 DB가 아니다.

정확한 수치·실행 환경·파일 보존 범위는 [검증 증적](evidence/target-result-link-verification-20261021.json)을 따른다.
성능/메모리·실제 V100·운영 자료·PostgreSQL DDL·공용 배포·macOS/Windows CLR 검증은 포함하지 않는다.
기존 개선형 Python·DLL·FSN·운영 Gate·기존 계약/Schema와 데이터는 그대로다.

## 6. 다음 순서

**현재 기준선 — 로컬 Runtime·Platform 조회와 최초 실행 조립 완료 · InventoryEngine·dsai-platform Backend**

- [로컬 Reader](IO_LOCAL_TARGET_RESULT_RUNTIME_CONTRACT.md)와
  [독립 최초 실행](IO_INDEPENDENT_INITIAL_EXECUTION_CONTRACT.md)을 후속 구현·검증했다.
  운영 Mathematical Child와 기본 Runtime Registry를 유지한다. 앞의 수치는 이 연결 계약의 당시 검증 이력이다.

**다음 작업 — 독립 결과의 실제 적재 범위 대사 / 실제 Write·배포 승인 필요**

- 기존 테이블·권한·미적용 DDL과 독립 결과를 읽기 전용으로 대조한다. 독립 적재 검증 이후 Demand 연결을 진행한다.
  실제 DB Write·Migration·공용 Runtime 재배포·실제 E2E Write는 별도 승인 대상이다.
  발주 확정·ERP 연계·자원 관리 추가 고도화·공식 강건성 본실험은 계속 후속/보류 범위다.
