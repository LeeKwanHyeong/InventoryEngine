# 선정 PSI SQL·Writer 정합화

2026-10-21. **로컬 Catalog/SQL 초안·Writer 보완과 Offline 검증 완료**.
대상: InventoryEngine `inventory_engine_dev`, Platform `codex/inventory-psi-strategy-v2`.
실제 DB 접속·적용·Canary·Write·공용 배포는 하지 않았다.

## 수정과 유지 범위

- 양쪽 물리 Catalog의 PSI CHECK에 후보 없는 `SELECTED_ORDER_PLAN`을 허용했다.
  이 Marker를 후보 ID로 사용해 선정 결과처럼 위장하는 것은 거부한다.
- Platform SQL Trigger는 성공한 `POLICY_SELECTION / MATHEMATICAL / EVIDENCE_ONLY` Child에만
  `candidate_id=NULL`, `simulation_id=SELECTED_ORDER_PLAN`을 허용한다. 잘못된 역할·전략·Marker·NULL은 차단한다.
- 기존 Baseline은 `BASE`·신규 권고 입고 0, 권고/Stress는 `BASE`, 탐색은 후보 ID와 같은 Simulation을 유지한다.
  수량 보존식·FK·행동 Gate·Receipt·Transaction/멱등 복구 규칙은 바꾸지 않았다.
- Platform `build_inventory_dsim_projection_service`에 기존 실행과 같은 `target_result_reader`를 명시적으로 주입한다.
  기본 None은 Bundle-only 호환이며 목표 결과가 추가된 Projection은 계속 거부한다.
  Reader를 최신 값으로 탐색하거나 누락 목표 행을 제외하는 Fallback은 없다.
- 테이블 21개·컬럼 277개·기존 PK/FK/Index·업무 ID/Hash 필드는 그대로다.
  Engine의 계산/Runtime/Guard/FSN을 포함한 기존 `src` Python 212파일은 변경하지 않았다.
  신규 테이블·Child Kind·발주 Master를 만들지 않는다.

## Hash와 과거 자료

이전/새 Catalog로 같은 원문을 봉인해 전체 Row, 행 Hash, Run/입력 Binding,
논리 `projection_content_hash`, 원본 Bundle·선정/발주 계획 Artifact가 같음을 검증했다.
이전 봉인 파일은 덮어쓰지 않는다.

SQL CHECK가 들어 있는 Catalog 자체의 지문과 이를 봉인한 새 Projection Manifest 지문은 변경된다.
이전 Manifest는 정확한 이전 Catalog로 읽고, 새 Catalog로 재해석하면 `PROJECTION_CONTRACT_MISMATCH`로 거부한다.
물리 저장 계약의 변경을 원 결과 Hash 변경이나 같은 Reference의 강제 덮어쓰기로 숨기지 않는다.
실제 DB에 예전 Receipt가 있는 경우도 Catalog/Receipt 불일치를 자동 복구로 덮어쓰지 않는다.

## 검증

- Engine 관련 **106 passed**, Skip 0, 150.24초. Catalog 계약·Projection/복구·목표 결과 연결·최초 실행/Runtime을 포함한다.
- Platform 관련 **193 passed**, Skip 0, 42.02초. 실제 설치 Wheel과 로컬 캐시의 Python 3.12용
  `pglast v7.20`을 사용했고 의존성 설치/교체·네트워크 다운로드는 하지 않았다.
- 신규 34사례는 SQL 허용/차단 20건, 실제 Factory + 목표 Projection/실패/복구 13건,
  Engine 이전/새 Catalog의 ID·Row·Hash/원문 보존 1건이다.
- 실제 Factory에 32개 ABC·XYZ, 32개 LEGACY_STYLE, 88개 참조 후보의 Projection을 전달했다.
  원문 재검증·SQL 파라미터·Read-back·새 Reader 멱등 재시도와 저장 중 실패 후 전체 Rollback을 확인했다.
- Reader 누락·다른 DLL Build·잘못된 Hash·재봉인한 Gate 변조·Scope/권한 실패·기본 쓰기 차단을 확인했다.
  SQL/PLpgSQL 10개 Function 구문 검사와 Catalog/생성 SQL 정합성·Ruff·Format을 통과했다.

Factory의 DB는 Recording Pool이고 SQL 판정식은 SQLite에서 평가했다.
수량 Port도 명시적 `ReferenceKernel`이다. 이번 결과는 실제 PostgreSQL FK/Trigger/동시성 또는 C# 실행 재검증이 아니다.
독립 최초 실행의 실제 DB Writer 자동 연결·승인 Source 준비·실제 V100 E2E 완료로 설명하지 않는다.

상세 계약/증적은 Platform 저장소의 다음 파일을 따른다.

- `docs/backend/dsim/inventory-selected-psi-writer-alignment-v1.md`
- `docs/backend/dsim/evidence/inventory-selected-psi-writer-alignment-20261021.json`

**1. 실제 개발 적재 후보를 확정한다 — 다음 작업 · InventoryEngine·Platform Backend**

- 승인 Config/분류·입력 Binding·정식 Run·설치 Build·정확한 저장 참조와 허용 계정을 고정한다.
  신규 결과 21개 테이블과 별도 079·분석 조회 확장을 구분하고 단건 적재/복구 절차를 확정한다.
  기존 분석/비용 테이블은 재사용한다.

**2. 독립 적재·조회·복구를 검증한다 — 승인 필요 · 개발 PostgreSQL·로컬 Runtime**

- 승인된 DDL만 Backup/Canary 후 적용하고 실제 Hash·건수·누락·Rollback·멱등 복구를 확인한다.
  FSN·운영 Gate·공용 Runtime·실제 발주/ERP는 바꾸지 않는다. Demand→IO는 이 독립 검증 이후 연결한다.

자원 관리 고도화·공식 강건성 경로/본실험 보류는 유지한다. Commit/Push는 수행하지 않았다.
