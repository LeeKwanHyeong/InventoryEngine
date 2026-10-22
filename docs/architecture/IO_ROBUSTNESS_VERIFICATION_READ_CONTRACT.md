# 강건성 Catalog의 무결성 조회와 전체 재계산 검증

## 1. 범위와 목적

2026-10-20, InventoryEngine `inventory_engine_dev`의 로컬 강건성 Catalog 저장·조회 계약이다.
기존 결과·Leaf 원문·Hash와 V1/V2/V3 저장 형식은 바꾸지 않고 조회 시점의 검증 응답을 별도로 반환한다.
공식 실험 결과나 운영 승인, 업무 Runtime/API 배포를 의미하지 않는다.

빠른 조회에서도 전체 후보와 파일을 확인한다. 다만 Source 생성·전략·Guard·PSI·서비스/비용의 계산은 하지 않는다.
“파일과 연결이 온전하다”는 사실과 “현재 구현으로 같은 계산이 재현된다”는 사실을 구분하는 것이 목적이다.

## 2. 호출과 상태

[영속 조립기](../../src/dsio_inventory_engine/evaluate_replenishment/robustness/durable.py)의 호출은 다음과 같다.

| 호출 | 수행 범위 | `verification_level` | Source·PSI·지표 재계산 상태 |
|---|---|---|---|
| `inspect` | 봉인 입력·Header·전체 Leaf·공유 파일의 저장 무결성 확인 | `ARTIFACT_INTEGRITY` | 모두 `NOT_RUN` |
| `verify_recalculation` | 무결성 확인 → 기존 `read`의 전체 실제 재생 → 무결성/Binding 재확인 | `FULL_PSI_RECALCULATION` | 모두 `VERIFIED` |
| 기존 `read` | 기존 Source·Catalog·Guard·PSI·평가 재생 후 원 결과 반환 | 기존 결과 형식 유지 | 새 응답 형식으로 대체하지 않음 |
| 기존 `recover` | 저장된 Leaf도 실제 재생하고 미완료 부분을 계산·복구 | 기존 Receipt 유지 | 빠른 조회로 대체하지 않음 |

두 새 호출 모두 `execution_id`와 정확한 `expected_input_hash`를 받는다.
전체 재계산은 `expected_result_hash`도 필수다. 무결성 조회는 이 값을 주면 일치 여부를 확인하고,
생략하면 조회한 결과 Hash를 응답한다. 최신 Source나 다른 Run으로 대체하지 않는다.

응답 계약은 [Python 검증기](../../src/dsio_inventory_engine/inventory_contracts/robustness_verification.py)와
[JSON Schema](../../schemas/robustness_verification_view.schema.json)의 `inventory-robustness-verification-view` `1.0.0`이다.

- `artifact_integrity_status`는 성공 응답에서 `VERIFIED`다.
- `source_recalculation_status`, `psi_recalculation_status`, `metrics_recalculation_status`는 위 표의 조합만 허용한다.
- 실행·입력·Manifest·원 결과 Hash, Company/Subsidiary/Site/Plan/Run/W0와 전체 후보·성공·실패·PSI Row·Leaf 건수를 포함한다.
- 원 결과의 `result_hash`와 새 응답의 `content_hash`를 구분한다. 검증 수준이 다르면 새 응답 Hash는 달라도 원 결과 Hash는 같다.
- 모든 승인·자동 게시·자동 발주·DB Write 값은 `false`다. 다른 상태 조합·추가 Key·잘못된 Version/Hash는 거부한다.
- 건수 합계와 `PSI Row = 65 × 성공 후보 수`, 응답 Hash는 Python 검증에서도 확인한다. JSON Schema만으로 이 산술·Hash 검증을 대신하지 않는다.

## 3. 빠른 무결성 조회의 확인 범위

[조회기](../../src/dsio_inventory_engine/evaluate_replenishment/robustness/integrity_read.py)는 Repository 읽기 기능만 받는다.
Source Producer·전략·Model·PSI 실행 기능은 필요하지 않으며 파일을 생성하거나 복구하지 않는다.

1. 입력과 완료 포인터, Manifest·Header의 실행/입력/결과 Binding을 확인한다.
2. V1 원문, V2 압축, V3 공유 참조를 기존 Reader로 읽는다. 물리 파일·프레임·복원 크기·원문·내용 Hash 검증을 유지한다.
3. 부모 분류·품목 Projection·전체 Catalog·실현 경로·비용 Profile의 저장 Binding을 확인한다.
4. 후보 32개 또는 88개 전체의 순서·ID·중복·누락, Leaf Checkpoint와 성공/실패 건수를 확인한다.
5. 성공 후보마다 65주 PSI·판단 증적 건수, Calendar Bucket과 저장된 Trace·Scorecard·원 결과 연결을 확인한다.
   실패 후보는 실패 사유와 함께 전체 분모에 남긴다.
6. Leaf를 하나씩 읽어 기존 정규 JSON과 같은 최종 결과 Hash를 계산한다. 88개 후보의 큰 부모 결과를 한 문서로 펼치지 않는다.
7. 조회 끝에 입력·완료 포인터를 다시 확인한다. 누락·손상·불일치이면 성공 응답을 만들지 않는다.

Source의 권위·실제 생성 규칙, 정책 적정성, 재고 이월 산술, 서비스/비용의 옳고 그름을 확인한 것은 아니다.
예를 들어 잘못된 비용과 관련 Hash를 일관되게 다시 봉인하면 무결성만 통과할 수 있다.
이 경우 재계산 상태는 여전히 `NOT_RUN`이며 실제 재계산에서 차이를 탐지해야 한다.

## 4. 전체 재계산과 기존 호환성

`verify_recalculation`은 이전 검증 응답을 증거로 받아들이지 않고, 매번 기존 `read`를 실행한다.
현재 Context에서 Source/Canonical 준비와 전체 후보의 전략·공통 Guard·PSI·평가를 실제 재계산하고
저장된 Leaf·Header·Manifest를 대조한다. [불변 준비 재사용](IO_ROBUSTNESS_IMMUTABLE_PREPARATION_CONTRACT.md)은
같은 Context의 고정 입력에만 적용하며 후보별 상태나 계산 결과를 캐시하지 않는다.

재계산 전후 무결성 응답이 다르면 `DURABLE_VERIFICATION_BINDING_CHANGED`로 거부한다.
실패 후보까지 동일하게 재현될 수 있으므로 전체 `VERIFIED`가 모든 후보의 성공이나 서비스 목표 달성을 뜻하지 않는다.
기존 `read`·`recover`·게시 전 실제 재생의 구현 강도와 기존 Receipt는 유지한다.

검증 응답은 조회 시점의 로컬 응답이며 저장된 원 결과에 검증 상태를 덧붙이지 않는다.
전역 검증 캐시·Build 간 응답 재사용·서명된 승인 증명서는 없다. 새 호출은 파일을 다시 읽고,
전체 재계산 호출은 그 프로세스의 현재 구현으로 다시 계산한다. 응답을 직접 만들거나 Hash를 맞춘 사실만으로
계산 실행 또는 운영 적격을 증명할 수 없으며, 운영 게시/발주 권한 판정에 사용해서는 안 된다.

## 5. Offline 확인 범위

- 수작업 W0 `2055-01-04`, 한 경로·65주에서 ABC/XYZ 32개·LEGACY_STYLE 32개·참조 88개 전체를 확인한다.
  원 결과 Hash와 각각 2,080/2,080/5,720 PSI Row를 보존한다. 전체 재계산의 전략 실행 횟수는 각각 후보 수 × 65다.
- 빠른 조회에서 Source 준비·전략·PSI·지표 재계산 호출이 발생하지 않는지 확인한다.
- V1/V2/V3 파일과 기존 Receipt를 쓰기 없이 읽고, 기존 복구도 같은 결과를 반환하는지 확인한다.
- 잘린 압축 파일·공유 파일 손상/누락·완료 포인터/Leaf 누락·건수·순서·Binding/Version 변조를 거부한다.
  이전 성공 응답이 있어도 현재 손상을 숨기지 않는다.
- 비용과 관련 Hash를 일관되게 바꾼 사례는 무결성만 통과하고 실제 재생에서 실패한다.
  구현 변경과 재계산 도중 Manifest 변경도 전체 검증을 통과하지 못한다.
- FSN `BLOCK`/`REVIEW`와 자동 게시·발주 금지, 실패 후보 분모를 유지한다.

실제 테스트 수치와 보존 결과는 [검증 기록](evidence/robustness-verification-read-20261020.json)에 남긴다.
이것은 공식 2037/2044 경로 생성·실험이나 업무 DB 검증이 아니다.

## 6. 성능과 남은 작업

빠른 조회는 계산을 생략하지만 전체 파일을 읽고 압축 해제·복원·Hash를 확인한다. Header만 읽는 상수 시간 조회가 아니다.
전체 재계산은 검증 전후 조회까지 하므로 I/O가 추가된다. 이번 작업으로 일정 비율의 속도·최대 RSS 개선을 주장하지 않는다.
기존 문서당 128MB·원문 20GB·호출당 3,600초 조건을 유지하며, 압축이나 빠른 조회로 예산을 할인하지 않는다.

**현재 기준선 — 후속 로컬 자원 제어 경로 구현 · InventoryEngine**

- [자원 통제 계약](IO_ROBUSTNESS_RESOURCE_CONTROL_CONTRACT.md)에 관리 루트 전체 예산·3,600초 제어 호출·중단/재개·임시 파일 정책을 추가했다.
  기존 두 검증 상태·원문/결과 Hash·실제 재계산은 유지하며, 기존 임의 직접 호출/공용 Runtime을 자동 통제하는 변경은 아니다.

**다음 작업 — 전체 Split 조립 사전 검증 · InventoryEngine**

- 새 제어기와 같은 관리 루트를 사용하도록 진입점을 고정한 뒤 전체 예상 용량·시간·완전성을 확인한다.
  공식 경로 생성·본실험은 이번에 진행하지 않는다.
- 기존 FSN·운영 Gate·DB·Platform·공용 Runtime·실제 발주·Commit/Push는 변경/수행하지 않는다.
