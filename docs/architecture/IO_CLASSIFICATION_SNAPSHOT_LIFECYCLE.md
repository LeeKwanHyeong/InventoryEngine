# Inventory 분류 Snapshot Lifecycle

기준일: 2026-09-11. 이 문서는 승인된 Inventory Configuration, Actual Close와 VED
Assignment와 선택된 추가 분석 축을 이용해 품목별 7축 분류 Snapshot을 계산하는 독립 실행
경계를 정의한다.

## 1. 실행 경계

`InventoryClassificationLifecycleUseCase`는 정확한
`Company/Subsidiary/Plant/Site` 범위에서 다음 순서로 한 번 실행한다.

1. 활성 Inventory Configuration Revision과 Config hash를 읽는다.
2. 같은 조직 범위의 가장 최신 Actual Revision을 먼저 선택한 뒤 그 Revision이 `CLOSED` 또는
   `REVISED`인지 검증한다. 최신 Revision이 `OPEN/PARTIAL`이면 과거 완료 Revision으로 우회하지 않는다.
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
Deferred Constraint를 검증했다. 다만 실제 V2 업무 Snapshot은 아직 영속 게시하지 않았다.
`ABC_XYZ` 모드는 승인된 Config Schema 1.1.0과, `SEVEN_AXIS` 모드는 2.0.0과 정확히
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
5 사유 Canary가 `SET CONSTRAINTS ALL IMMEDIATE`를 통과한 뒤 Rollback됐다. 기존 Snapshot
1건은 보존됐으며 실제 V2 업무 Snapshot은 생성하지 않았다. 공용 Runtime과 운영 DB에는
적용하지 않았다.

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

이 Run Binding과 Mock/Offline 통합 검증은 완료했다. 남은 승인 대상은 공통 Run Migration 074
개발 적용, 실제 V2 업무 Snapshot/Run DB Write, Scheduler 자동 실행과 공용 Runtime 배포다.
