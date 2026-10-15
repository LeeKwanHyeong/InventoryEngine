# DSIM Projection Writer·Verifier 구현 기준선

기준일: 2026-10-15 · InventoryEngine `inventory_engine_dev` / Platform `codex/inventory-psi-strategy-v2`.

> 최신 추가: [Source·설치 Build·Projection 실행 조립](IO_DSIM_SOURCE_EXECUTION.md)을 완료했다.
> 분석 OFF 실행의 별도 분류 Header 봉인/Resolver와 설치 Build 대사 항목은 해소했다.
> 아래 기존 수치는 Writer 검증 이력이며 실제 DB 미적용·Runtime 기본 미활성 경계는 유지한다.

## 완료 범위

- [Source Projector](../../src/dsio_inventory_engine/inventory_evidence/projection_source.py)가 기존 봉인 Result Bundle Verifier를 재사용해 실제 Canonical·Prepared·PSI·행동·후보·평가·선정 증적을 20개 논리 Table Row로 변환한다.
- [Writer·Verifier](../../src/dsio_inventory_engine/inventory_evidence/projection.py)는 분할 Artifact/Manifest, 정확한 Claim·입력 Binding, Content/Byte/Row Hash와 전체 원본 재생성 대사, 원자 저장·Read-back·Receipt·멱등 복구를 구현했다.
- [Offline 영속 모형](../../src/dsio_inventory_engine/infrastructure/sqlite/inventory_projection.py)은 임시 SQLite 전용이다. PostgreSQL DDL/FK/Trigger/Lock의 대체 검증이 아니다.
- Platform의 명시적 조립 Service/Pool Adapter를 연결했으며 기본 쓰기는 비활성이다. 실제 DB 적용·DML·Migration·기본 Runtime/HTTP 연결·기존 Wheel 교체·공용 배포·Commit·Push는 하지 않았다.
- FSN·정책 계산·운영 Gate·Effective Run CAS·실제 발주는 변경하지 않았다. 계산 전용 실행을 정식 UUID Run으로 위장하지 않는다.

## 저장 계약

1. 기존 Artifact를 먼저 봉인한다. Producer 출력은 `artifact_reference`, Manifest `content_hash`, 별도 논리 `projection_content_hash`다.
2. 8종 Canonical Source와 원 Claim 전체 입력 Binding을 고정한다. 분류 Effective Policy Hash와 Header Content Hash는 별개이며 원문 Header를 입증하지 못하면 차단한다.
3. 기본 500행/최대 1000행 Chunk를 Table·PK 순으로 생성한다. 누락·중복·다른 Run·알 수 없는 컬럼·원문과 다른 재봉인 값은 거부한다.
4. Ledger를 먼저 대조한 뒤 Transaction 밖에서 Source를 검증한다. 저장 Transaction에서는 Ledger를 다시 대조하고 FK 순서대로 INSERT한다.
5. 20개 Table Read-back과 Receipt Artifact를 검증한 후 Receipt 1건을 포함해 commit한다. 실패는 전체 Rollback, 커밋 응답 유실은 기존 Receipt/Row 확인으로 복구한다.
6. 동일 논리 Hash는 기존 Receipt 반환, 다른 Hash는 충돌이다. 이미 Receipt가 있는데 Row가 누락된 경우도 차단하며 덮어쓰기/자동 삭제하지 않는다.
7. Receipt는 Evidence 적재 완료일 뿐 업무 게시/발주 승인이나 Run 성공 승격이 아니다. 무수요 서비스율·미가용 비용·미제공 Source 시각은 null로 보존한다.

완전한 구성·필드 매핑·SQL Adapter·권한/성능 경계는 Platform `docs/backend/dsim/inventory-dsim-projection-writer-verifier-v1.md`에 있다. 물리 [Catalog](../../schemas/inventory-dsim-physical-contract-v1.json)와 [초안](IO_DSIM_PHYSICAL_CONTRACT.md)은 기존 구조 재사용을 유지한다.

## 실제 Artifact 대조에 따른 초안 보정

- NO_ORDER와 입고일 null, 품목 없는 Site Cut-off, 미제공 Source 봉인 시각을 그대로 저장한다.
- HML 승인 수준은 AUTO/STANDARD/HIGH_VALUE 또는 null이고, FSN/PLC의 ALLOW/NO_ORDER/REVIEW 주문 Gate와 별도다.
- Shadow·Guard 거부·실제 무주문 행동의 행별 자동 실행은 false다. 원 정책 Gate는 Source Artifact에 유지한다.
- 장기 평가 기간을 현재 Run Calendar에 FK로 강제하지 않는다. Parent Run·기간·Source·Cost Profile·종료 조건을 평가 Context/Hash로 구분한다.
- Legacy Buffer ID가 원문에 없으면 만들지 않는다. 큰 Cut-off 배열 원문은 기존 Artifact에 두고 Projection에는 건수·Hash만 저장한다.
- 신규 물리 초안은 21개 Table·277개 컬럼·42개 FK다. 아직 개발 PostgreSQL에 적용하지 않았다.

## 검증 결과

- InventoryEngine Unit·Contract·Offline Integration: **854 passed / 9 skipped / 669 subtests passed**, 기존 Framework 경고 3건.
- 신규 Projection·복구: **19 passed**. 변조/누락/중복/다른 Run, 실패 Rollback·재기동·응답 유실, 충돌 Hash, 검증 중 Claim 변경, null·Profile/Parent Hash를 검증했다.
- Platform 관련 계약·Migration·Repository·Source 연결: **273 passed**. 실제 Source를 사용한 Recording Pool Service 검증은 5개 권한/Scope/Binding/성공/실패 사례다.
- Catalog byte·DDL 생성물 대사, pglast DDL/PLpgSQL 구문·Ruff/Format·Diff Check 통과. PostgreSQL 실제 제약/동시성/권한과 V100 3,622품목 전체 부하 검증은 미수행이다.

## 남은 작업 순서

**완료 이력 — InventoryEngine·Platform의 봉인 Source·설치 Build 연결**

- 분석이 없는 Bundle의 분류 Header 원문 봉인/Resolver와 설치 Build·정확한 Artifact 포인터를 연결했다. 사용자 Hash 입력이나 최신 분류 탐색으로 우회하지 않는다. 현재 후속 순서는 Source 실행 조립 문서를 따른다.

**승인 필요 — 개발 PostgreSQL Rollback Canary·FK/Trigger·동시성·권한을 검증한다.**

- Migration 079와 새 Evidence DDL은 구분한다. 실제 적용이나 운영 입력 승인·Claim E2E·기본 기능 활성화는 이번 구현에 포함하지 않는다.

**후속 작업 — 검증된 조회·복구/Runtime 조립과 저장소별 기준선을 고정한다.**

- 실제 적용 뒤 기존 Run/CAS와 통합 검증한다. InventoryEngine `inventory_engine_dev`, Platform `codex/inventory-psi-strategy-v2`의 Commit·Push는 별도 요청 후 진행한다.
