# Inventory 분류 Snapshot Lifecycle

기준일: 2026-10-15. 이 문서는 승인된 Inventory Configuration, Actual Close와 VED
Assignment와 선택된 추가 분석 축을 이용해 품목별 7축 분류 Snapshot을 계산하는 독립 실행
경계를 정의한다.

## 1. 실행 경계

`InventoryClassificationLifecycleUseCase`는 정확한
`Company/Subsidiary/Plant/Site` 범위에서 다음 순서로 한 번 실행한다.

1. 활성 Inventory Configuration Revision과 Config hash를 읽는다.
2. Plan 실행은 판단 주차를 명시하고, 같은 조직 범위에서 **그 직전 주차의 최신 Revision**만
   선택해 `CLOSED` 또는 `REVISED`인지 검증한다. 해당 주차가 없거나 최신 Revision이
   `OPEN/PARTIAL`이면 차단한다. 다른 주차나 과거 완료 Revision으로 우회하지 않는다.
3. 승인된 축별 Lookback 기간의 수요·매출 집계로 품목별 ABC-XYZ를 결정적으로 계산한다.
   가장 긴 조회 범위는 Source I/O 최적화에만 사용하며 ABC와 XYZ의 집계·분모는 서로의
   Lookback이나 FSN Lookback에 영향을 받지 않는다. SDE 입고 Lookback은 Demand Actual Close
   조회·Lineage 기간을 확장하지 않는다.
4. VED가 활성화되면 정확한 승인 Assignment ID/Hash와 Scope를 검증하고 품목별 배정을
   결합한다. 미배정 품목은 Configuration의 `default_class`를 사용한다.
5. 품목별 `ABC/XYZ/VED`, `segment_key`, `final_segment_key`, 계산 근거와 미분류 사유를 만든다.
6. 분류된 품목에는 ABC-XYZ 정책 Matrix의 서비스수준·검토 주기·전략을 적용하고, VED
   하한이 더 높으면 서비스수준을 상향한다. Config 2.0.0이면 Operational 축만 Effective
   Policy V2에 투영해 FSN과 향후 권위 PLC의 주문 Gate, SDE 보호기간, HML 승인 수준을 추가한다.
   현재 합성 PLC는 Config 2.0.0에서 Shadow-only다.
7. Source Revision, Source/Config/content hash, 품목별 유효 정책 Hash, Coverage와 9개 Segment
   합계를 만든다.
8. `--apply`가 있을 때만 Header·집계·품목 결과를 하나의 Transaction으로 게시한다.

Config 2.0.0에서는 FSN·SDE·HML·PLC를 독립 `axis_results`로 추가한다. 결합 규칙과 Source
상태는 [IO_SEVEN_AXIS_SEGMENTATION_CONTRACT.md](IO_SEVEN_AXIS_SEGMENTATION_CONTRACT.md)를
따른다. `SHADOW` 축은 결과와 근거만 보존하고 유효 정책에는 영향을 주지 않는다. 7축 품목
결과의 DB Projection Migration 075·076은 2026-09-11 개발 PostgreSQL에 적용해 게시 Schema와
Deferred Constraint를 검증했다. 2026-09-15에는 DSE/C100/V100 개발 범위의 Config 2.0.0과
실제 V2 분류 Snapshot을 영속 게시했다. 실제 업무 중요도가 아닌 개발 VED와 합성 PLC를
사용했으므로 운영 승인 Snapshot으로 해석하지 않는다.
`ABC_XYZ` 모드는 승인된 Config Schema 1.1.0과, `SEVEN_AXIS` 모드는 2.0.0 또는 2.1.0과 정확히
결합한다. allowlist에 있는 Schema라도 모드가 다르면 Source 계산 전에
`CLASSIFICATION_CONFIG_SCHEMA_MISMATCH`로 차단한다.

기본 CLI는 no-write다. 같은 Scope와 content hash의 재실행은 기존 Snapshot ID와 Revision을
반환하며 행을 추가하지 않는다. 원천 근거가 부족한 자재는 Segment를 추정하지 않고 품목별
미분류 사유와 집계 사유를 모두 남긴다.

품목 결과의 Grain은 `classification_snapshot_id + item_id`다. VED가 활성화된 분류의
`final_segment_key`는 `AX-V`처럼 ABC-XYZ와 VED를 결합하고, VED가 비활성화되면 기존 `AX`
형식을 유지한다. 분류되지 못한 품목은 VED 근거는 보존하되 ABC·XYZ·최종 Segment를 비워
잘못된 정책 적용을 차단한다.

유효 정책은 다음 규칙으로 고정한다.

```text
effective_target_service_level
    = max(ABC-XYZ Matrix 목표 서비스수준, VED 서비스수준 하한)

effective_review_cycle_weeks / effective_strategy
    = ABC-XYZ Matrix 셀 값
```

`MATHEMATICAL`만 `operational_io_eligible=true`다. `PREDICTIVE_ML`과 `DEEP_RL`은 분류와
정책 선택 결과를 보존하되 운영 적격을 `false`로 두며, 승인된 Model ID·Version·Hash가
승인 참조에 결합되고 실행 Descriptor와 완전히 일치하는 Shadow 실행에서만 사용한다. 운영 요청에서 미승인 학습 전략을 수학적 전략으로
조용히 대체하지 않는다. ABC 또는 XYZ가 미분류인 품목에는 기본 정책을 만들지 않는다.

1.1.0의 품목별 `effective_policy_hash`는 기존대로 Config Hash, 품목·분류·VED 근거와 최종
정책 필드를 결합한다. 2.0.0의 Effective Policy V2 Hash는 선택된 Matrix 셀과 VED 하한,
Operational 축의 분류·매핑·검증값만 봉인한다. 따라서 Shadow 축 설정이나 분류값이 바뀌면
전체 Classification Snapshot Hash는 바뀌지만 Effective Policy Hash는 바뀌지 않는다.
Header의 `effective_policy_content_hash`는 정렬된 품목 ID와 정책 Hash 목록을 봉인한다.
각 품목은 `classification_config_hash`와 `classification_config_binding_hash`를 별도 보존한다.
Config Hash는 Shadow 무영향 규칙을 위해 Effective Policy Hash에서는 제외하지만, Binding Hash가
품목 ID·Config Hash·Effective Policy Hash를 함께 봉인한다. 보충 Admission은 Binding Hash를 먼저
재검증하고 Run의 `config_hash`와 완전히 대조한다. 필드 재표기는
`ITEM_POLICY_CONFIG_BINDING_HASH_MISMATCH`, 다른 Config Revision 사용은
`ITEM_POLICY_CONFIG_HASH_MISMATCH`로 차단한다.

## 2. 소유권과 호출 방식

분류 계산과 게시 책임은 InventoryEngine에 있다. dsai-platform은 Snapshot을 읽어 Engine
Studio Matrix에 표시하는 Consumer다. 실제 분류 단계가 성공한 뒤 다음 명령의 동일한
entrypoint를 Worker/Runner가 호출한다.

```bash
inventory-classification \
  --project-id <project> \
  --company-cd <company> \
  --subs-cd <subsidiary> \
  --plant-cd <plant> \
  --site-cd <site> \
  --decision-yyyyww <plan-yyyyww> \
  --approved-by <service-user> \
  --apply
```

DB 연결은 `IO_POSTGRES_DSN`으로 외부 주입한다. Secret, 자재별 수요와 분류 행은 Receipt에
포함하지 않는다. 현재 구현된 ABC 원천은 `REVENUE`뿐이며 다른 기준은 해당 Source Adapter가
승인될 때까지 `CLASSIFICATION_ABC_BASIS_UNSUPPORTED`로 차단한다.

VED Source Adapter는 `dsai.inventory_ved_assignment_snapshots`의 승인 상태, Content Hash와
Site Scope를 확인한 뒤 정규화된 품목 배정을 읽는다. Master/수요 품목에 없는 고아 배정이나
같은 품목의 중복 배정은 전체 분류를 실패시킨다.

FSN 활성 시 Source Adapter는 품목별 Movement Row Count와 Invalid Row Count를 함께 제공한다.
0건 또는 불완전한 Source는 `N`으로 간주하지 않고 `UNVERIFIED`로 남기며, 완전한 Actual Close가
0 movement를 증명한 경우에만 `N`을 허용한다. Shadow FSN의 전체 Lookback이 봉인되지 않았으면
ABC·XYZ 계산은 유지하되 FSN만 `UNVERIFIED`로 남긴다. Operational FSN은 같은 조건에서 실행을
차단한다.

Demand Source Lineage는 Lookback 전체 주차의 Revision·Publication ID·Relation·Manifest Hash를
정규화해 Hash한다. PLC 합성 Source는 ISO 주차와 Lifecycle 순서, Profile Hash를 검증한 뒤
`SYNTHETIC` 등급과 Evidence를 만들지만 Shadow-only이므로 유효 정책에는 투영하지 않는다.

### 2.1 Plan 판단 주차와 직전 마감 고정 (2026-10-15)

기존 Source는 전체 마감 중 최신 주차를 선택했고, Snapshot Builder는 실제 마감 Source에
별도의 판단 주차를 허용하지 않았다. 따라서 후속 마감이 있는 과거 Plan을 재현하거나,
직전 마감으로 Plan 주차의 Snapshot을 만드는 두 요구를 함께 만족하지 못했다.

- Plan `202512`는 `decision_yyyyww=202512`, 실제 마감은 `202511`로 고정한다.
  26주 이력은 `202438~202511`, 날짜 범위는 `[2024-09-16, 2025-03-17)`이다.
  `202512` 이후 마감이 있어도 이력에 포함하지 않는다.
- `PostgresClassificationSource(..., decision_yyyyww=...)`는 정확한 직전 주차를 조회한다.
  ISO 연도 경계도 날짜로 계산하며 존재하지 않는 ISO 주차는 DB 접근 전에 차단한다.
  최신 Revision 선택 후 상태를 검사하므로 미마감 Revision을 건너뛰어 성공하지 않는다.
- Snapshot의 `as_of_yyyyww`는 판단 주차다. 조회·축별 Lookback은 직전 마감에서 끝나며,
  `window_end_exclusive`는 판단 주차 월요일이다. 기존 전체 Lookback 봉인·VED·Config·FSN 검증은 유지한다.
- 실제 마감 기반 V2 Source Revision은
  `ACTUAL-CLOSE-DECISION-WINDOW-<이력 시작 주차>-<직전 마감 주차>-<이력 Hash>`로 구분한다.
  Publisher는 이력 시작·종료, 판단 주차, 날짜 범위, History Hash와 실제 Source Relation을 다시 검증한다.
  기존 개발용 `DEVELOPMENT-PINNED-WINDOW-*`를 실제 마감으로 승격하지 않는다.
- Claim된 Run의 CLI 호출에는 `--decision-yyyyww`가 필수다. Run Context와 Source·Receipt의
  판단 주차가 다르면 실패한다. 독립 CLI에서 판단 주차를 생략하는 기존 동작은 일반 분류 호환용으로만
  유지하며, Plan 실행·회귀 재현에는 사용하지 않는다. 실제 Claim 조회를 이 CLI가 대신하는 것은 아니다.
- V1의 기존 의미와 V2 JSON 필드는 유지한다. 기존 Snapshot을 수정하지 않고
  새 입력·판단 주차에 대한 신규 Content Hash를 계산한다. 판단 주차만 고정하면 미래 정정까지 영구 고정되는
  것은 아니므로, 확정 Run 재현에는 저장된 Snapshot ID·Revision·Hash를 계속 사용한다.

로컬 Unit·Contract·Offline Integration은 **828 passed / 16 skipped / 669 subtests passed**다.
직전 마감 누락·미마감, 미래 주차 존재, ISO 연도 경계, 잘못된 판단 주차·Lineage·Hash 및
게시 전 Context 불일치를 검증했다. 관련 테스트 69건과 Ruff·Format·Diff Check도 통과했다.
실제 개발 DB 읽기 전용 검증에서도 최신 `202601` 대신 `202511`을 선택했다. 기존 활성 Config와
전체 7,000품목의 FSN 계산·Python 게시 전 검증을 통과했지만, Config 2.1 Draft의 3,622품목 검증이나
신규 Snapshot 게시·Config 승인·실제 Claim을 대신하지 않는다.
이번 변경의 DB 게시·승인 및 Runtime 재배포는 수행하지 않았다.

**DB 게시 경계 — 차단됨:** 실제 개발 DB의 `chk_inventory_classification_v2_lineage`는
기존 Migration 076과 같이 `window_end_exclusive = as_of 주차 월요일 + 7`을 요구한다.
이번 판단 주차 계약의 `window_end_exclusive = as_of 주차 월요일`과 충돌하므로 현재 DB에는
새 형식의 Snapshot을 게시할 수 없다. 읽기 전용 Source 조회·순수 계산·Python 게시 전 Validator 통과가
실제 INSERT 가능을 뜻하지 않는다. 이후 후속 079 SQL·회귀 테스트 준비는 완료했지만 적용하지 않았으므로
DB 게시 차단은 유지한다. 기존 Migration 076은 수정·재실행하지 않았다.

### 2.2 후속 Migration 079 — 준비 완료 / 실제 DB 미적용 (2026-10-15)

- 대상: dsai-platform `codex/inventory-psi-strategy-v2`의
  `infra/migrations/drafts/079_inventory_classification_decision_week.sql`과
  `tests/unit/platform/test_inventory_classification_decision_week_migration.py`다.
  기존 076을 보존하고 Draft를 일반 Migration 자동 탐색 경로와 분리했다.
- 실제 스키마 변경은 `dsai.inventory_classification_snapshots`의
  `chk_inventory_classification_v2_lineage` CHECK 하나 교체뿐이다. 새 테이블·컬럼·PK/FK·Index·Trigger·권한은 없다.
  기존 Snapshot 행·ID·Hash는 수정하지 않으며 제약은 V100만이 아니라 테이블 전체에 적용된다.
- 유효한 V1과 기존 V2는 이전 기간 규칙을 유지한다. 새 실제 마감 판단 주차 Marker만 이력 종료일을
  판단 주차 월요일로 허용하고, 시작·직전 주차·History Hash·실제 Source Relation을 함께 검증한다.
  모든 Source에 두 기간 규칙을 허용하지 않는다. NULL Version과 채워진 V2 이력이 CHECK의 UNKNOWN으로
  통과하는 경로도 차단한다. 개발 합성 입력의 게시·운영 권한을 확장하지 않는다.
- 선행 검증은 관련 **46 passed**, Platform Inventory **538 passed**, 기존 Framework 경고 25건이다.
  Ruff·Format·Diff Check와 pglast 7.20의 PostgreSQL 문장 8개·PL/pgSQL 본문 2개 파싱을 통과했다.
  Offline 식 검증은 SQLite 호환 Harness이며 PostgreSQL DDL·잠금·Trigger 검증을 대신하지 않는다.
- 실제 개발 DB에서는 읽기 전용 SELECT로 새 제약식을 평가했다. 기존 Header 2건의 부적합은 0건이고,
  기존 활성 Config·전체 Master 7,000품목의 `202512` 후보도 식을 만족했다.
  이는 Config 2.1 Draft·3,622품목 후보 승인이나 실제 INSERT 검증이 아니다. 079 적용 이력도 0건이었다.
- 적용 시 단일 Transaction과 `ACCESS EXCLUSIVE` 잠금을 사용하고 `lock_timeout=5s`,
  `statement_timeout=120s`로 제한한다. 현재 Schema·제약·Ledger와 Snapshot ID·Hash를 보존한 뒤
  Rollback Canary·실제 적용을 별도 승인받아야 한다. Commit 전 오류는 전체 Rollback한다.
  새 형식 게시 후 구형 제약 복귀를 위해 불변 Snapshot을 삭제·수정하지 않는다.

079 자체는 업무 행을 추가하지 않는다. 이후 승인된 새 분류 게시에서만 기존 7개 테이블
`dsai.inventory_classification_snapshots`, `dsai.inventory_classification_snapshot_segments`,
`dsai.inventory_classification_snapshot_unclassified_reasons`, `dsai.inventory_classification_snapshot_items`,
`dsai.inventory_classification_snapshot_axis_windows`, `dsai.inventory_classification_snapshot_item_axes`,
`dsai.inventory_classification_snapshot_item_policy_reasons`에 완전한 Aggregate를 추가한다.
이때도 기존 Snapshot은 보존하고 Exact Replay는 중복 추가하지 않는다. 승인 입력·Claim의 Config,
Input Set·Cycle·Run 데이터 변경은 079와 별도 단계이며 기존 FSN 설정·운영 Gate를 우회하지 않는다.
원 수요·마감·Master·Network·Forecast와 `TB_IO_*`는 이번 보정으로 변경하지 않는다.
회귀·읽기 전용 증적은 `/private/tmp/inventory-week-constraint-20261015.PjFYsk/`에 보존하며 Commit 대상이 아니다.

## 3. Receipt와 멱등성

Receipt 계약은 `inventory-classification-snapshot-publication-receipt-v1`이다. 조직 범위,
기간, Config/Source/content hash, 전체 유효 정책 Hash, 전체·분류·미분류 SKU 수, 9개
Segment 합계와 미분류 사유 합계만 반환한다.

- `dry_run`: 계산만 완료, DB write 없음
- `published`: 새 content hash를 새 Revision으로 게시
- `exact_replay`: 같은 content hash가 이미 존재, DB write 없음

게시 Transaction은 조직 범위 advisory lock과 `SERIALIZABLE` 격리를 사용한다. Header,
9개 Segment, 미분류 사유는 하나의 Transaction으로 저장되며 DB의 지연 제약과 불변성
Trigger가 합계와 append-only 규칙을 다시 검증한다.

Publisher는 V1과 V2를 명시적으로 구분한다. V1 Header는 `ABC_XYZ`, 품목 계약 1.1.0, 정책
계약 1.0.0이어야 하고 V2 전용 필드가 없어야 한다. V2 Header는 `SEVEN_AXIS`와 계약 2.0.0,
전체 Window Lineage·Source Manifest·Effective Policy Content Hash를 요구한다. V1 Header로
V2 품목을 위장하면 차단한다. Snapshot 본문에서 Content Hash와 UUIDv5 Snapshot ID를 다시
계산하고 둘 중 하나라도 다르면 Lock이나 Insert를 시작하지 않는다. 품목 Key·정책 Hash,
집계·7축·Gate 사유까지 Transaction 전과 Deferred Constraint에서 재검증한다.

Migration 075·076은 개발 PostgreSQL에서 072 이후 순서대로 적용됐고, V2 1품목·7축·4 Window·
5 사유 Canary가 `SET CONSTRAINTS ALL IMMEDIATE`를 통과한 뒤 Rollback됐다. 이후 프로젝트
`9ed62f41`의 DSE/C100/V100 범위에 Config Revision 2와 V2 Snapshot Revision 2를 게시했다.
Snapshot은 Header 1건, Item 7,000건, Axis 49,000건, Window 3건, Policy Reason 26,610건이며
Content Hash `54084553...347d`, Effective Policy Hash `4ee176f6...2049`를 가진다. 동일 Source와
Config 재실행은 같은 Snapshot ID를 반환하고 `database_writes=false`인 Exact Replay였다.
공용 Runtime과 운영 DB에는 적용하지 않았다.

## 4. Run 단계 연결

독립 CLI는 Run을 만들지 않으므로 Receipt의 `run_claimed`를 `false`로 유지한다. 공통
Runner에서는 `InventoryClassificationStageUseCase`가 이미 Claim된 Inventory Run을 받아
다음 증적을 같은 `engine_run_id`에 연결한다.

1. `inventory.classification / running`
2. Snapshot의 새 Revision 게시 또는 exact replay 검증
3. `inventory.classification / running` 완료 Event와 Snapshot ID·Revision/hash·집계 수치
4. 실패 시 원본 자재값 없이 안정적인 오류 코드로 `failed` Event

분류 완료 Event는 전체 Run 성공을 뜻하지 않는다. Run 상태는 후속 PSI·권고·봉인 단계가
끝날 때까지 `running`으로 유지한다.

Effective Policy V2 실행에서는 Platform이 다음 Binding을 시스템에서 조립한다.

```text
input_type              = INVENTORY_CLASSIFICATION
source_contract_key     = inventory.classification_effective_policy
source_contract_version = 2.0.0
source_snapshot_id      = classification_snapshot_id
source_content_hash     = effective_policy_content_hash
```

동시에 `INVENTORY_POLICY / 2.0.0`을 요구하고 Classification Config Revision·Hash와 Run Config를
정확히 대조한다. Runtime은 품목별 Binding Hash를 재검증한 뒤 FSN/PLC Gate, SDE P50/P90
Lead Time, HML 승인 수준을 실제 보충 계산에 사용한다. `REVIEW`는 권고와 Evidence를 만들지만
`automatic_publish_allowed=false`, `automatic_order_allowed=false`로 반환해 Platform Publish를
자동 호출하지 않는다. `BLOCK` 또는 미검증 Operational 축은 계산 전에 차단한다. V1은
Classification Binding 없이 기존 Policy 1.0.0으로 호환된다.

이 Run Binding과 Mock/Offline 통합 검증, 과거 개발 V2 Snapshot 게시·Exact Replay 검증은 완료했다.
Migration 074와 Claim·Attempt·Retry·CAS도 개발 PostgreSQL에서 별도로 검증한 이력이 있다.
이는 현재 V100 Plan의 새 분류 승인·입력 묶음·실제 Claim 완료를 뜻하지 않는다.
현재 Plan의 DB 게시·승인, Claim E2E, Scheduler 자동 실행, 공용 Runtime 배포와 실제
Result Bundle DB/Object Storage 게시 E2E는 각각 별도 확인·승인 대상이다.
