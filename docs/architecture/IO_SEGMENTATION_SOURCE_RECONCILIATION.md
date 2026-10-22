# 분석 저장 원본의 식별과 승인 경계

기준일: 2026-10-19. 상태: **목표·참조 원본 대사 완료 / 저장용 승인 Source는 미준비**.
Engine 계산/분류/FSN/운영 Gate 코드는 변경하지 않았고 PostgreSQL·기존 SQLite에는 적재하지 않았다.

## 1. 목표는 V100 / 202512로 유지한다

- Tenant/Project: `default / 9ed62f41`, Scope: `DSE/C100/V100/V100`.
- Plan `202512`, W0 `2025-03-17`, 직전 마감 `202511`, 과거 26주 `202438~202511`.
- Config 2.1 Revision `2708e79a-82b5-4c82-8261-5a236077bb18`, Hash
  `2d9b69b2bd255a2317c507e94921152cdb148ba95207d97af17bb07d5bca773f`는 현재 Draft다.
  기존 활성 2.0으로 바꾸지 않았고 Draft를 승인된 입력으로 표시하지 않았다.
- Demand Run `e5b43229-c69e-4e08-a8ab-5515bfb0f2f3`도 유지했다. Forecast는 재실행하지 않았다.

## 2. 봉인 참조 원본은 확인했지만 DB 승인 원본은 아니다

- 로컬 원문 `bbdc697b-bf7c-5765-b5d8-09a27c260948`과 기존 계산 전용 Artifact 내부 원문이 완전히 같다.
  Header Hash `ad8a82d7eed940595743bdbbe3064dfb613965882a94f367a2ed963fd6efc79c`,
  Policy Hash `bb57eb3b5cc3417e77622ad8f192db254f3f0b93f47b35f8141bb0f66760b235`를 재검증했다.
- 참조 Artifact는 `inventory.development_calculation_input`, Reference
  `artifact:inventory-development:default:9ed62f41:644a31bc-d43c-5d03-9f1f-754936160e0a:1:input`이다.
  Content Hash `7507f293bf1096cf4523a62f4d1b05bb879916b6bac7a03cf76b838a3a265299`,
  Byte Hash `452714c8b28132f133482bebf3baad791597e7f404dad151e028c3789d48dc07`를 고정했다.
- Engine의 `ArtifactObject`, `_validate_publishable_snapshot`, `CanonicalInputRequest`,
  `PrepareInventoryInputUseCase`, `validate_command`, `validate_source_bundle`을 재사용했다.
  마지막 검증은 Source-only이며 설치 Build 확인/PSI 실행/Run 입장 허용이 아니다.
- Canonical Hash `34d6317d8cbe2dd5e8aed63eaa0a3de08d5c98da9455ce3fb897ee294a4cfcb9`,
  원 Config/Scope/기간/정책 Projection도 일치했다. 기존 preclaim 식별자를 정식 Run으로 쓰지 않는다.
- DB 승인 V2 `e54e5649-ad2b-578c-b1fa-2e5603446895`는 Config 2.0/7,000품목이다.
  as_of=`202601`은 마감 주차이며 실제 Window End=`2026-01-05`, W0=`202602`다.
  이를 목표 `202512`로 재해석하거나 SQL 행에서 원문을 재구성하지 않는다.

## 3. 현재 마감과 고정된 옛 증적은 다르다

- 현재 DB의 목표 26주는 모두 CLOSED/REVISED 및 유효 Source/Manifest/Publication을 충족한다.
  마감 이력 Hash는 `70d3cb63c7a9a63fe1dc8bad33cf55db98a28901c50029c71b035a2b54bf622e`다.
- 기존 원문이 보존한 마감 이력 Hash는 `240eeeca536a2c16e5a9feb1f0251772070b0d9787745cfd880d43f3ce511b3d`다.
  원문의 `FSN UNVERIFIED=3,622 / BLOCK=3,622`는 변경하지 않는다. 현재 마감이 충분해도 같은 ID/Hash의
  원문을 덮어쓸 수 없다. 정확한 Source로 새 후보를 만들어야 한다.
- 앞서 문서에 기록한 새 후보 `29aa1570-058a-589a-b330-bbf8be9d57a5`의 임시 원문 경로는 현재 없다.
  문서의 ID/Hash만으로 Artifact가 존재하거나 승인됐다고 간주하지 않는다.
- 검토한 로컬 저장소에 `inventory.segmentation_analysis_source`/`projection`은 없으며,
  계산 전용 Wrapper를 새 저장 계약의 Artifact로 자동 변환하지 않았다.

## 4. 결정과 다음 작업

**완료 — 목표·참조 원본과 차단 원인 대사**

- `selected_storage_source=null`, `database_write_allowed=false`로 기록했다.
  신규 분석 Header/Item·승인 입력 묶음은 각각 0건이다. 기존 DB 분류와 Config도 바꾸지 않았다.
- Platform의 `scripts/reconcile_inventory_segmentation_source_development.py`는 기본 연결과 트랜잭션 모두
  읽기 전용이며, Audit/Writer/Publisher/Claim을 호출하지 않는다. SQLite도 `mode=ro/query_only`다.
- 실제 ID/Hash·기간·검증 결과는 Platform
  `docs/backend/dsim/evidence/inventory-segmentation-source-reconciliation-20261019.json`에 있다.
  원문/임시 SQLite는 Commit에서 제외한다. 다른 호스트/Catalog 전체를 조사했다고 주장하지 않는다.
- Helper **22 passed**, 기존 저장/Projection/분류/주차/Evidence 계약을 포함한 Backend 묶음 **197 passed**다.
  Ruff·Format·양쪽 Diff Check도 확인했다. Engine Source 검증을 재사용한 대사이며 실제 계산/분류 코드는 바꾸지 않았다.

**다음 작업 — 새 분류 원문을 로컬에서 복구·재생성 · InventoryEngine·Platform Backend**

- 고정한 Plan/Config와 현재 26주 마감 Source로 새 원문·품목 집합·Hash·미분류 사유를 검증한다.
  이전 원문·기존 FSN 규칙·미분류 3품목·운영 Gate는 유지한다. 사라진 후보의 ID를 강제 재사용하지 않는다.

**후속 작업 — Legacy Feature/Source/Projection Artifact 준비**

- 새 원문 검증 후 물리 Lead Time·단가·수요·Calendar/Master와 Rule/Build Hash를 연결한다.
  개발 합성값과 권위 Source를 구분한다. 순수 Catalog 선정/조회 준비는 Source 대사와 병행 가능하다.

**승인 필요 — DB 계약/입력 승인과 실제 소량 저장**

- 079·Config 게시/분류 승인·실제 Writer 적재는 별도 범위다. 이번 대사에서 자동 적용·승격하지 않는다.
  선행 원본 계약이 고정된 뒤 저장·Hash/건수·멱등·중단 복구 E2E를 직렬로 검증한다.
