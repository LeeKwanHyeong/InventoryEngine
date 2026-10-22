# 독립 IO 결과의 실제 DB 적재 범위

> 후속 정정: [선정 PSI SQL·Writer 보완](IO_SELECTED_PSI_WRITER_ALIGNMENT.md)을 Offline으로 완료했다.
> 아래 불일치는 읽기 대사 당시의 관측 이력이다. 실제 DB 적용/적재는 여전히 미수행이다.

상태: **2026-10-21 읽기 전용 대사 완료 / 실제 적재 미수행**.
대상: InventoryEngine `inventory_engine_dev`, dsai-platform `codex/inventory-psi-strategy-v2`, 개발 PostgreSQL.

## 확인된 기준선

- 동일한 두 저장소의 DSIM Catalog는 **21개 테이블·277개 컬럼**이며 실제 DB에는 이 21개가 모두 없다.
  수량/비율/Hash Domain 4개와 Evidence Trigger도 아직 없다. Artifact 저장과 DB 적재는 별개다.
- 기존 분석 `dsim.tb_io_segmentation_analysis`, `tb_io_segmentation_analysis_item`과 7축 View 2개는 존재한다.
  분석 두 테이블은 각 0건이며, 기존 비용 결과 두 테이블도 존재하지만 각 0건이다. 다시 생성하지 않는다.
- Run/Cycle/Binding·Config·분류·Network·Trade 구조는 재사용한다.
  신규 FK가 참조하는 기존 테이블의 PK/Unique Key 4개는 존재한다. 실제 FK 생성/Trigger 실행은 미검증이다.
- 074~078은 적용 이력이 있고, 086은 적용 파일 Checksum도 일치한다. 079는 미적용이며 현행 CHECK는 `as_of + 7`이다.
  방식별 분석/Run 조회 View 2개와 nullable `segmentation_analysis_id` Binding 확장은 별도 미적용 초안이다.
- Catalog에 적힌 `dsdm.tb_com_calendar`는 없고 `dsdm.calendar_week`가 존재한다.
  의미·구조 동등성을 추정하지 않는다. 정확한 봉인 Calendar 사용 계약은 유지한다.

## 테이블 적용 전에 보완할 세 가지

1. 목표 결과의 선정 PSI는 `POLICY_SELECTION / SELECTED_ORDER_PLAN / candidate_id=NULL`이다.
   현재 SQL의 `ck_io_psi_1`은 NULL 후보에 `BASE`만 허용하고, Child Trigger도 선정 PSI를 허용하지 않는다.
   작은 2후보·3주 Fixture에서 선정 PSI 3행의 CHECK 거부를 Offline 재현했다.
2. Platform의 `build_inventory_dsim_projection_service`는 목표 결과 Reader를 Projector에 전달하지 않는다.
   같은 봉인 목표 Projection이 기본 조립에서 `PROJECTION_SOURCE_ROWS_MISMATCH`로 거부되는 것을 확인했다.
   Verifier를 완화하지 말고 같은 Reader·Build·저장 참조를 주입해야 한다.
3. 독립 최초 실행에는 실제 PostgreSQL Writer 연결이 없다. 별도 조립의 쓰기 Switch도 기본 `false`다.
   `dsai.inventory_site_input_sets`도 0건이다. DDL 적용만으로 승인 입력/정식 Claim이나 적재 경로가 생기지 않는다.

선정 계획은 기존 `tb_io_psi_bucket`, `tb_io_policy_result`, `tb_io_replenishment_proposal`을 재사용할 수 있다.
새 업무 ID/Hash·Child Kind·발주 테이블은 추가하지 않는다. 선정 결과는 `EVIDENCE_ONLY`로 유지한다.
DB 검증 Receipt는 발주 승인/업무 게시가 아니며 운영 Gate나 Effective Run을 자동 변경하지 않는다.

## 검증과 승인 경계

명시적 `REPEATABLE READ / READ ONLY`에서 메타데이터와 제한된 건수만 SELECT하고 Rollback/연결 종료했다.
확인 Role `dsai`는 Superuser/Owner/BYPASSRLS이므로 최소권한 서비스 Role 검증으로 설명하지 않는다.
Migration·Canary·실제 INSERT·Audit Write·FSN·운영 Gate·Secret·공용 배포는 수행/변경하지 않았다.
관련 Engine 테스트는 **39 passed**(36.66초), Platform은 **133 passed / 4 skipped**(6.03초)다.
선택적 PostgreSQL Parser 2건과 명시적 설치 Wheel 2건은 Skip됐다. 전체 저장소/Native/실제 DB E2E 합격 수치가 아니다.

상세 Relation 목록·컬럼/제약 확인 범위·적용 Ledger·권한·파일 지문은 Platform 저장소의 다음 문서/증적을 따른다.

- `docs/backend/dsim/inventory-independent-projection-db-reconciliation-v1.md`
- `docs/backend/dsim/evidence/inventory-independent-projection-db-reconciliation-20261021.json`

**1. 선정 PSI의 SQL 규칙과 목표 결과 Reader 조립을 보완한다 — 다음 작업 · InventoryEngine·Platform Backend**

- 기존 Kind/Grain/ID/Hash를 유지하며 위 세 경계를 닫는다. 임의 Kind/Simulation·Gate 상승·잘못된 Reader/Build를
  거부하는 테스트까지 통과해야 한다. 이 단계는 로컬 코드/초안/Offline 검증이며 DB를 바꾸지 않는다.

**2. 적용·단건 실행 후보를 고정한다 — 후속 작업 · Platform Backend·개발 PostgreSQL 읽기 영역**

- 정확한 승인 입력/분류/Config·정식 Run·Build·Artifact 참조와 허용 계정을 정한다.
  신규 Evidence DDL, 필요 시 079, 선택적 분석 조회 확장을 별도 변경 묶음으로 구분한다.
  이전 086과 중복 생성 초안은 적용 대상에서 제외한다.

**3. 실제 적재·조회·복구를 확인한다 — 승인 필요 · 개발 PostgreSQL·로컬 Runtime**

- 승인된 변경만 Backup/Canary 후 적용하고 단건 Hash/건수/누락/원자 Rollback/멱등 복구를 검증한다.
  독립 IO 적재 검증 후 Demand→IO 최종 연결로 진행한다. 공용 배포·발주요청 확정·ERP 연계는 포함하지 않는다.

FSN·운영 Gate와 기존 계산 코드는 유지했다. 자원 관리 고도화·공식 강건성 경로/본실험도 계속 보류한다.
