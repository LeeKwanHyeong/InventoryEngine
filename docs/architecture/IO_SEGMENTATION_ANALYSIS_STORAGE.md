# 분류 방식별 중간 분석 Artifact와 2-Table 저장 계약

기준일: 2026-10-19. 로컬 저장기 구현과 개발 PostgreSQL Table 2개/View 2개 적용을 완료했다.
신규 분석 데이터 적재·Runtime/Run 연결은 수행하지 않았다.
기존 ABC·XYZ/7축 계산·FSN 설정·운영 Gate·Config/Run 계약과 Hash를 변경하지 않는 별도 경로다.

추가 상태: 접속 복구 후 승인된 `192.168.0.46:5432/dsai`에 086만 적용했다.
실제 Rollback Canary 16항목·Schema-only Backup·DDL/원장 원자 적용·새 연결 재조회를 확인했다.
Header/Item은 각각 0행이고 View 7,000/49,000행은 기존 분류 자료다. 기존 9개 분류/설정 Table의
행수와 진단 지문·FSN/운영 Gate는 동일하며 079·다른 Evidence 초안은 미적용이다.
적용 SQL SHA-256은 `0442ff7d91f981c6b637be0974fec0ffed1fa45381ae08815cfd38cf3e6717de`이며
Platform `docs/backend/dsim/evidence/inventory-segmentation-086-development-20261019.json`이 실제 증적이다.
개발 대상 전용 `drafts/086_*`는 자동 목록 미등록 상태로 보존한다. 원본 검토 초안도 그대로다.
실제 Writer 데이터 게시·최소권한 Runtime E2E·동시성·전체 데이터 Restore는 별도 검증이다.

현재 원본 대사는 [Source 식별 계약](IO_SEGMENTATION_SOURCE_RECONCILIATION.md)을 따른다.
V100/202512의 계산 전용 봉인 원문은 검증했지만 Config Draft·분류 미승인·마감 증적 차이와
Legacy Source/Projection 미준비로 실제 저장용 Source는 선택하지 않았다. DB 적재는 수행하지 않았다.

## 1. 책임과 구현 파일

| 구현 | 역할 |
| --- | --- |
| `inventory_contracts/segmentation_storage.py` | Header/Item의 고정 컬럼·형식·계약 Key/Version |
| `inventory_evidence/segmentation_analysis.py` | 원문 봉인, 분류/Legacy 재검증, 결정론적 Projection, 저장·전체 재조회 대사 |
| 기존 `ArtifactObject` + `SqliteInventoryEvidenceUnitOfWork.commit_artifacts()` | 원본/Projection의 Content·Byte Hash와 원자 Append-only 저장 |
| `infrastructure/sqlite/segmentation_analysis.py` | 별도 임시 SQLite의 Offline UoW. PostgreSQL FK/Trigger/권한/Lock 구현이 아님 |
| Platform `services/inventory_segmentation_analysis.py` | 권한/Scope, 선택 설치 Engine 조립, 기본 비활성 PostgreSQL Adapter |
| Platform `infra/migrations/drafts/UNASSIGNED_dsim_segmentation_analysis_v1.sql` | 새 Table 2개와 무결성/불변 Trigger의 검토용 SQL. 항상 Rollback, 자동 실행 목록 미등록 |

기존 원본 분류 Header/Item/7축/Window를 재사용한다. 새 저장소는 승인·정책 선정·운영 게시 원장이 아니다.
`WIDE_REFERENCE`를 새 Method로 만들거나 비교 모드마다 동일 Feature/Legacy 분석을 복제하지 않는다.

## 2. 원본과 별도 Binding

`analysis_binding(result, classification_snapshot)`은 개발용 명시적 Pin 생성 도우미이며 Source 승인이 아니다.
호출자는 Tenant/Project/전체 Site Scope, 분류 Snapshot ID·Header Hash·Config Hash, 원 Legacy 결과 Hash,
Feature Hash, 정확한 Universe Hash, Feature Context Hash를 별도로 고정해 전달한다.

- 기존 Publisher의 순수 검증기를 재사용해 원 분류의 V2 계약·UUID·Hash·7축·정책·집계·계보를 검사한다.
  이 검증 경로는 DB에 접속하지 않는다. V1을 V2로 자동 변환하지 않는다.
- 주입한 신뢰 Class/규칙/Reference Calculator Build로 원 Feature와 Legacy 결과를 재생성해 대사한다.
- 원 분류 UUID·Header Hash·Config·Source Hash, 회사/Subsidiary/Site, 정확한 W0 직전 종료 경계를 확인한다.
  과거 분류의 as_of를 최신 주차로 재해석하거나 재봉인하지 않는다.
- Feature Universe는 원 분류 품목의 명시적 부분집합이어야 하고, ABC 등급·상태·사유가 원문과 같아야 한다.
  Universe Hash를 별도로 고정해 임의 품목 누락·추가/대체를 막는다. 원 Master를 축소하지 않는다.
- Class가 인자 그룹을 배정해도 원 ABC 미분류·FSN BLOCK/REVIEW를 해제하지 않는다.

## 3. Artifact·Hash와 멱등 식별자

원본 Artifact `inventory.segmentation_analysis_source / 1.0.0`은 원 분류 Snapshot 전체와 원 Legacy 결과
(Feature 입력·Reference Statistics/Policy/Trace 포함)를 함께 보존한다.
분류의 기존 Hash는 UUID 제외 등 고유 계산 규칙을 유지하며 새 Wrapper Hash로 대체하지 않는다.

Projection Artifact `inventory.segmentation_analysis_projection / 1.0.0`은 별도 Binding,
정확한 원본 Reference·Content Hash·Byte Hash, Header 1건과 Item 전체를 포함한다.

- Artifact Reference는 분류 UUID와 Legacy 결과 Hash로 고정한다. 사용자 Hash 입력 UI는 없다.
- Analysis UUID는 `uuid5(NAMESPACE_URL, 'inventory-segmentation-analysis-v1:' + classification_id + ':' + analysis_hash)`다.
- Item Row Hash는 자기 Hash를 제외한 정규화 컬럼의 Hash다.
- Projection Hash는 자기 Hash/DB 적재 시각을 제외한 Header와 `item_id` 정렬 Item 전체의 Hash다.
- 원 결과 Hash, Feature Hash, Wrapper Content/Byte Hash, Row Hash와 Projection Hash를 구분한다.
- 같은 Reference·같은 바이트는 멱등이며 다른 바이트는 기존 Artifact 저장기가 충돌시킨다.

Verifier는 원본을 다시 읽어 원 계약/Feature/분류를 검증하고 Projection 전체를 재생성한다.
행/수량/건수/계약 Key·Version을 바꾸고 새 Hash만 붙인 결과는 승인하지 않는다.

## 4. 두 Table과 값 보존

- `dsim.tb_io_segmentation_analysis`: 원 분류+Method+원 결과 Hash의 불변 Header.
- `dsim.tb_io_segmentation_analysis_item`: Analysis+원 품목. 평균/표준편차·물리 LT·USD/EA 단가,
  Reference ROP·정확한 커버리지 분자/분모·구간·그룹·점수·후보 Profile/예외·미분류 사유를 보존한다.
- 첫 Method는 고정 `LEGACY_STYLE` 개발 규칙만 지원한다. `development_only=true`, `operational_approved=false`다.
- 원본 Feature가 미분류이면 수치/그룹을 정상값으로 저장하지 않고 NULL과 원 사유를 저장한다.
  파싱된 원 LT/가격 등 추가 진단값도 원본 Artifact에는 보존한다.
- Reference ROP는 선정된 실제 ROP가 아니다. 분류 단가는 비용 Profile/Landed Cost가 아니다.
- NUMERIC 변환/대사는 Decimal Context로 다시 반올림하지 않는다. 80자리 분자/분모도 정확히 보존한다.
- 필수 컬럼·길이·형식을 검사하며 기존 Gate/Source 정책을 새 Table에 덮어쓰지 않는다.

## 5. 트랜잭션과 복구

1. 원본과 Projection Artifact를 기존 `commit_artifacts()`에 원자 저장한다. PSI Outbox를 생성하지 않는다.
2. 별도 Writer가 봉인된 포인터와 별도 Binding으로 원본/Projection을 검증한다.
3. PostgreSQL Adapter는 `allow_draft_writes=False`가 기본이며 True를 명시하지 않으면 Pool 획득도 하지 않는다.
4. SERIALIZABLE Transaction과 원 분류+Method Advisory Lock 안에서 승인 원 Header의 Tenant/Project/Scope/
   Hash/Config와 모든 원 품목 존재 여부를 대사한다. 요청 값만으로 원본 권위를 만들지 않는다.
5. 같은 Natural Key가 있으면 Header와 Item 전체를 재조회해 대사한다. 같으면 기존 ID와 `replayed=true`,
   누락/추가/다른 내용이면 충돌이다. 기존 손상 행을 조용히 보충하거나 UPDATE하지 않는다.
6. 신규이면 Header 다음 Item을 최대 500행씩 INSERT하고 모든 값을 다시 읽어 대사한 뒤 Commit한다.
   각 Batch는 다중 VALUES 한 문장이다. 행별 executemany로 전체 건수를 N회 검사하지 않는다.
7. DDL은 Header의 지연 완전성 검사와 Item Statement의 건수 상한, PK/FK/CHECK, UPDATE/DELETE/TRUNCATE
   차단을 정의한다. 실패 시 전체 DB Projection을 Rollback하고 원 Artifact는 유지한다.
8. DB Commit 응답 유실·프로세스 재시작은 봉인 포인터로 재요청한다. Forecast/PSI나 원천 조회 없이 복구한다.
   Feature·분류 재생성 대사는 검증의 일부로 계속 수행한다.

Platform Service는 `engine_config.write/read` 권한과 Tenant/Project를 검사한다. Router/startup 자동 등록,
새 Run Claim/실제 업무 승인/Effective Run CAS·운영 게시·발주·DB Migration 실행은 추가하지 않았다.
실제 인증 HTTP, DB 최소권한/동시성/실행계획과 서버 배포 검증은 미완료다.

## 6. 검증과 남은 작업

Offline 검증은 실제 Engine Artifact 저장기·원 검증기와 별도 SQLite UoW, Platform Recording Pool을 사용한다.
기본 성공/재시도, 원본/Projection/Byte Hash 변조, 건수·누락·추가·잘못된 원 품목,
FSN BLOCK/REVIEW 불변, Scope/권한/DB 기본 차단, 부분 Rollback·Commit 응답 유실·재기동,
동시 Offline 멱등 요청과 501품목의 500+1행 Batch를 검증한다.
실제 PostgreSQL의 FK·Trigger·Lock/동시성 검증과 동일시하지 않는다.

- 신규 Engine 검증: **31 passed**.
- Engine 전체 Unit·Contract·Offline Integration: **1,096 passed / 34 skipped / 655 subtests passed**,
  기존 Starlette 경고 1건.
- Platform 관련 계약/연동: 임시 `pglast 8.7` Target을 포함해 **158 passed**.
  신규 Platform 연동/DDL 묶음은 그중 **25 passed**다.
- PostgreSQL DDL·PL/pgSQL 구문 분석은 통과했다. 기존 개발 환경·설치 Runtime 패키지는 변경하지 않았다.
- 전체 Engine Ruff·Format **287파일**, Platform 신규 코드 Ruff/Format,
  기존 보호 코드/개인 보고서 6개 지문과 Diff Check를 대사했다.
- 이후 실제 PG의 FK/Trigger/불변성 Canary **16개**, Schema 백업/제한 적용 및 새 연결 검증을 완료했다.
  관련 Backend 회귀는 **175 passed**, 목록 정적/Python **17 passed**다. 두 Table은 0행이다.
  위 Offline 결과와 실제 DB Schema 검증을 구분하며 Runtime 동시성·업무 분석 Write는 미완료다.

**다음 작업 — 새 Catalog 결과의 선정·영속 경로와 분석 포인터를 연결한다.**

- 정확한 원본/분석 포인터를 향후 Run Binding과 조회에 연결한다. 기존 ABC 선택 Hash와 Gate는 유지한다.
- 정책 선정과 미사용 기간 성과 비교는 직렬이며, Studio 분석 조회 준비는 저장 계약이 고정된 뒤 병행할 수 있다.

**승인 필요 — 소규모 실제 분석 데이터 게시·E2E Write.**

- Schema 적용은 완료한 기준선이다. 이제 정확한 Plan/Site/W0와 원 승인 분류·봉인 원문을 고정한 뒤
  별도 범위 승인으로 Writer의 실제 저장·멱등·복구/동시성/역할 권한을 검증한다.
- 이번에는 업무 분석 행 게시·입력 승인·Runtime 배포·Commit/Push를 수행하지 않았다.
