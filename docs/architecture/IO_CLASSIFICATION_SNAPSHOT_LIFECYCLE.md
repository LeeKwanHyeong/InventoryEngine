# Inventory 분류 Snapshot Lifecycle

기준일: 2026-09-07. 이 문서는 승인된 Inventory Configuration과 Actual Close를 이용해
ABC-XYZ 분류 Snapshot을 계산하고 게시하는 독립 실행 경계를 정의한다.

## 1. 실행 경계

`InventoryClassificationLifecycleUseCase`는 정확한
`Company/Subsidiary/Plant/Site` 범위에서 다음 순서로 한 번 실행한다.

1. 활성 Inventory Configuration Revision과 Config hash를 읽는다.
2. 같은 조직 범위의 최신 `CLOSED` 또는 `REVISED` Actual Close를 고정한다.
3. 승인된 lookback 기간의 수요·매출 집계로 ABC-XYZ를 결정적으로 계산한다.
4. Source Revision, Source/Config/content hash, Coverage와 9개 Segment 합계를 만든다.
5. `--apply`가 있을 때만 append-only Snapshot을 게시한다.

기본 CLI는 no-write다. 같은 Scope와 content hash의 재실행은 기존 Snapshot ID와 Revision을
반환하며 행을 추가하지 않는다. 원천 근거가 부족한 자재는 Segment를 추정하지 않고 집계된
미분류 사유로 남긴다.

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

## 3. Receipt와 멱등성

Receipt 계약은 `inventory-classification-snapshot-publication-receipt-v1`이다. 조직 범위,
기간, Config/Source/content hash, 전체·분류·미분류 SKU 수, 9개 Segment 합계와 미분류 사유
합계만 반환한다.

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
3. `inventory.classification / succeeded`와 Snapshot ID·Revision/hash·집계 수치
4. 실패 시 원본 자재값 없이 안정적인 오류 코드로 `failed` Event

분류 성공 Event는 전체 Run 성공을 뜻하지 않는다. Run 상태는 후속 PSI·권고·봉인 단계가
끝날 때까지 `running`으로 유지한다. Platform의 공통 Run Claim 계약과 호환 Migration 074는
초안이며, DBA 승인 전에는 개발 DB에 적용하거나 실제 Claim을 수행하지 않는다. Scheduler
자동 실행과 공용 Runtime 배포도 별도 승인 대상이다.
