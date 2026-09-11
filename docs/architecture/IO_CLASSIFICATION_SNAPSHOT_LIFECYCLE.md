# Inventory 분류 Snapshot Lifecycle

기준일: 2026-09-11. 이 문서는 승인된 Inventory Configuration, Actual Close와 VED
Assignment를 이용해 품목별 ABC-XYZ-VED 분류 Snapshot을 계산하고 게시하는 독립 실행
경계를 정의한다.

## 1. 실행 경계

`InventoryClassificationLifecycleUseCase`는 정확한
`Company/Subsidiary/Plant/Site` 범위에서 다음 순서로 한 번 실행한다.

1. 활성 Inventory Configuration Revision과 Config hash를 읽는다.
2. 같은 조직 범위의 최신 `CLOSED` 또는 `REVISED` Actual Close를 고정한다.
3. 승인된 lookback 기간의 수요·매출 집계로 품목별 ABC-XYZ를 결정적으로 계산한다.
4. VED가 활성화되면 정확한 승인 Assignment ID/Hash와 Scope를 검증하고 품목별 배정을
   결합한다. 미배정 품목은 Configuration의 `default_class`를 사용한다.
5. 품목별 `ABC/XYZ/VED`, `segment_key`, `final_segment_key`, 계산 근거와 미분류 사유를 만든다.
6. 분류된 품목에는 ABC-XYZ 정책 Matrix의 서비스수준·검토 주기·전략을 적용하고, VED
   하한이 더 높으면 서비스수준을 상향한다.
7. Source Revision, Source/Config/content hash, 품목별 유효 정책 Hash, Coverage와 9개 Segment
   합계를 만든다.
8. `--apply`가 있을 때만 Header·집계·품목 결과를 하나의 Transaction으로 게시한다.

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
결합된 Shadow 실행에서만 사용한다. 운영 요청에서 미승인 학습 전략을 수학적 전략으로
조용히 대체하지 않는다. ABC 또는 XYZ가 미분류인 품목에는 기본 정책을 만들지 않는다.

품목별 `effective_policy_hash`는 Config Hash, 품목·분류·VED 근거와 최종 정책 필드를
결합한다. Header의 `effective_policy_content_hash`는 정렬된 품목 ID와 정책 Hash 목록을
봉인하므로 같은 Classification 입력과 Configuration은 항상 같은 정책 결과를 만든다.

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

## 4. Run 단계 연결

독립 CLI는 Run을 만들지 않으므로 Receipt의 `run_claimed`를 `false`로 유지한다. 공통
Runner에서는 `InventoryClassificationStageUseCase`가 이미 Claim된 Inventory Run을 받아
다음 증적을 같은 `engine_run_id`에 연결한다.

1. `inventory.classification / running`
2. Snapshot의 새 Revision 게시 또는 exact replay 검증
3. `inventory.classification / running` 완료 Event와 Snapshot ID·Revision/hash·집계 수치
4. 실패 시 원본 자재값 없이 안정적인 오류 코드로 `failed` Event

분류 완료 Event는 전체 Run 성공을 뜻하지 않는다. Run 상태는 후속 PSI·권고·봉인 단계가
끝날 때까지 `running`으로 유지한다. 품목 결과 Table과 무결성 Trigger는 dsai-platform의
Migration 075 초안이며 개발 DB에는 아직 적용하지 않았다. 공통 Run Migration 074 적용,
Runtime Segmentation Binding, Scheduler 자동 실행과 공용 Runtime 배포도 별도 승인 대상이다.
