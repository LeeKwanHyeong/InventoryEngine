# 공통 입력 참조 저장과 원문 복원 계약

## 1. 범위와 변경하지 않는 기준

InventoryEngine `inventory_engine_dev`의 수작업 2055년 한 품목·한 경로 검증이다. 분류·Calendar·Forecast의
동일한 값을 저장 계층에서 공유한다. 기존 ABC/XYZ·LEGACY_STYLE·32/32/88 후보, 수식·FSN·운영 Gate·DB·공용 Runtime은 바꾸지 않는다.
공식 경로 생성·본실험·PPO/ML 학습·실제 발주·Commit/Push는 이 범위에 없다.

- 원 계산 문서·입력 Binding·Result Hash는 유지한다. 보장하는 원문은 기존 저장기의 **정규 JSON UTF-8+LF 바이트**다.
  임의 들여쓰기나 SQL 원문을 그대로 보존한다는 뜻은 아니다.
- V3는 선택 가능한 물리 저장 형식이다. 기존 V1/V2 저장기와 기본 Runtime을 변경하지 않고 같은 `put/read/bind/lookup` 인터페이스에 연결한다.
- 파일별 원문·복원·실제 압축 파일 128,000,000 Byte 상한을 유지한다. 전체 원문 예산 20,000,000,000 Byte와 호출당 3,600초 기준도 유지한다.
  전역 자원 예약/취소·보존/청소 정책이 이미 구현됐다는 의미는 아니다.

## 2. 공유하는 값과 파일 구조

[공유 계약](../../src/dsio_inventory_engine/inventory_contracts/robustness_shared_inputs.py)은
`parent_classification`, `calendar`, `forecast`, `future_demand`라는 입력 필드의 Dict/List 중 정규 JSON이 512 Byte 이상인 값만 선택한다.
분류 등 큰 블록을 선택하면 그 안을 다시 참조화하지 않는다. 공유 파일은 다른 공유 파일을 참조하지 않는 평평한 값이다.

| 파일 | 내용 |
|---|---|
| `shared-{value_hash}.json.gz` | 계약/버전과 원 입력 값. 같은 폴더의 동일 값은 한 파일을 재사용 |
| `object-{content_hash}.shared.json.gz` | 원 문서의 공유 부분을 `null`로 둔 본문과 별도 경로·참조 목록 |
| 기존 `slot-*.json` | 정확한 Input/Leaf/COMPLETE 물리 참조. 이름과 봉인 규칙은 유지 |

- 공유 값의 Hash는 그 값의 정규 JSON 바이트를 기준으로 한다. 물리 파일에는 계약 Wrapper가 있으므로 원문 파일 Hash와 구분한다.
- Calendar·미래 Forecast의 주차별 Suffix가 서로 다르면 별도 값으로 저장한다. **같은 Suffix의 후보 간 반복**을 공유하는 것이며,
  모든 Calendar가 무조건 하나의 파일이 된다고 표현하지 않는다. 행 순서·숫자 표현·한글 문자열도 그대로 복원한다.
- 원문 안의 `path/reference` 같은 필드나 `null`은 특별한 명령으로 해석하지 않는다.
  별도 참조 표의 정확한 경로만 사용하므로 원 데이터의 Marker 모양과 충돌하지 않는다.
- 정책·관측 State·행동·Guard·원 PSI를 요약하거나 생략하지 않는다. 부모 분류의 전체 모집단을 자르거나 다시 분류하지 않는다.

## 3. 닫힌 V3 물리 참조와 복원 순서

V3 문서 참조는 `storage_version=3.0.0`, `layout=SHARED_INPUTS_V1`이다. 기존 논리 `contract_id/content_hash`와 원문
`byte_count/raw_sha256`, 정규화 Manifest의 `manifest_byte_count`, 압축 파일의 `stored_byte_count/stored_sha256`,
Gzip 코덱·버전·level 1을 고정한다. 허용 필드 외 값이나 지원하지 않는 버전은 거부한다.

공유 참조는 `value_hash/value_byte_count`, Wrapper 원문의 `byte_count/raw_sha256`, 실제 파일의
`stored_byte_count/stored_sha256`만 허용한다. Boolean은 크기·배열 위치의 정수로 인정하지 않는다.

복원은 다음 순서다.

1. 실제 파일 길이/Hash와 제한된 Gzip 복원을 확인하고 정규화 Manifest의 닫힌 필드를 검사한다.
2. 참조 경로·위치·`null` Placeholder·중복·겹침을 확인한다. 최대 경로 깊이 64, 문서당 참조 4,096개이며 초과분을 생략하지 않는다.
3. 공유 파일을 읽기 전에 `정규화 본문 크기 + Σ(공유 값 크기−4)`로 복원 원문 크기를 계산한다.
   원 참조의 `byte_count`와 같고 128MB 이하여야 한다. 하나의 큰 값을 반복 참조하는 확장 공격도 여기서 차단한다.
4. 공유 파일의 물리 Hash·프레임/CRC·원문 Hash·값 크기/Hash·계약을 확인한다. 중첩 참조를 실행하지 않는다.
5. 각 위치에 독립된 원 값을 복원해 가변 객체 Alias가 생기지 않게 한다. 경로 목록이 정규 선택 규칙과 일치하는지 검사한다.
6. 복원 원문 길이/Hash와 기존 계산 문서의 논리 봉인을 확인한다. 실제 Catalog·전략·Guard·PSI 재생 검증은 기존 조립기에서 그대로 수행한다.

참조 파일의 Hash가 유효하다는 이유로 Source 승인·Scope 일치·운영 Gate 통과를 대신하지 않는다.
이 구현은 호출자가 소유하는 비공개 POSIX 폴더를 전제로 하며, 악성 OS/동일 권한 Python 코드의 격리 장치는 아니다.

## 4. 호환성·게시·중단 복구

- [새 저장기](../../src/dsio_inventory_engine/infrastructure/robustness_shared_artifacts.py)는 V1/V2/V3를 읽는다.
  기존 V1/V2 파일이 있으면 검증 후 원 참조를 재사용하며 압축 변환·삭제·덮어쓰기를 하지 않는다.
  기존 Reader가 V3를 읽는다고 주장하지 않는다.
- 참조가 V3이면 그 파일과 의존 파일만 읽는다. 누락·잘림·Hash 변조를 다른 형식/최신 Source/재계산 결과로 조용히 대체하지 않는다.
  기존 형식의 손상이 발견된 Write도 새 형식으로 우회하지 않는다.
- 저장 순서는 **공유 파일 → 문서 → 기존 Input/Leaf Checkpoint → 전체 Manifest → COMPLETE**다.
  같은 참조/Hash는 기존 파일을 재사용하고, 다른 참조는 충돌이다. 유효한 Gzip 파일을 재압축하지 않아 코덱 구현 변경 시에도 멱등성을 유지한다.
- 공유 파일만 저장된 시점의 중단은 입력 포인터가 없으므로 동일 원 봉인 입력으로 다시 요청해야 한다.
  Input이 봉인된 이후에는 정확한 `expected_input_hash`로 새 프로세스에서 복구한다. DB나 최신 Source를 다시 조회하지 않는다.
- 부분 V1/V2 실행은 기존 조각을 보존한 채 나머지를 V3로 저장할 수 있다. 완료된 실행의 공유 파일 누락·손상은 복구를 차단한다.
  명시적인 원문 전체 재생 검증 Write와 자동 복구를 혼동하지 않는다. 중단 후 남은 고아 공유 파일은 삭제하지 않고 재사용하며, 청소 정책은 후속이다.

## 5. 원문 예산과 검증 범위

- 비압축 저장량은 각 물리 공유 Wrapper·정규화 Manifest·기존 객체·포인터의 원문 바이트를 한 번씩 더한다.
  동일 폴더의 공유 값은 중복 계산하지 않지만 서로 다른 검증 폴더의 사본은 각각 센다. 이는 원문의 실제 중복 제거이며 **압축률 할인**이 아니다.
- 원래 문서를 전부 펼친 논리 Byte 수는 별도 기록한다. 복원 문서당 128MB 상한은 공유로 우회하지 않는다.
  참조 경로·Wrapper·측정 보고서 Metadata도 저장 예산에서 제외하지 않는다.
- [저장량 검증 도구](../../scripts/verify_robustness_shared_storage.py)는 전체 실제 후보를 실행·재생 검증한 뒤 V2와 V3의
  Input/Header/Leaf 원문·Hash·건수와 복구를 대조한다. Source 지문을 고정하고 3개 방법 모두 통과해야 완료 표시를 쓴다.
  저장량 진단은 원문을 검증한 뒤 수행하며 실행 승인 API나 전역 자원 Admission을 대체하지 않는다.
- [단위 테스트](../../tests/unit/test_robustness_shared_inputs.py),
  [계약 테스트](../../tests/contract/test_robustness_shared_input_contract.py),
  [Offline 복구 테스트](../../tests/integration/test_robustness_shared_storage_offline.py)로 검증한다.
  속도·메모리 개선은 아직 판정하지 않는다. 이번 저장량 확인은 3회 시간 비교를 다시 실행하는 벤치마크도 아니다.
- 공통 준비/직렬화 재사용, 빠른 조회와 실제 재생 검증의 분리, 전체 결과 Streaming,
  전역 20GB/3,600초 통제와 Split 전체 조립·공식 경로·본실험은 후속 작업이다.

## 6. 2026-10-20 수작업 저장량 검증

실제 공통 Catalog·Guard·관측 분리 PSI를 사용한 32/32/88개 후보가 모두 통과했다.
같은 입력의 V2/V3 Input·Header·Leaf는 정규 원문 바이트·논리 Hash·크기가 같았고, 최종 Result Hash도 기존 값과 같았다.
원 부모 분류와 전체 Catalog를 유지했으며 각 2,080/2,080/5,720 PSI Row를 보존했다.
`REVIEW`는 자동 게시/발주 금지를 유지하고, 별도 `BLOCK` 검증도 운영 Gate 우회를 허용하지 않았다.

MB는 1,000,000 Byte다. 비압축 저장량은 실제 저장한 공유 Wrapper·정규화 Manifest·객체·포인터를 모두 센 값이다.

| 방법 | 비압축 저장량 V2 → V3 MB | 실제 파일 V2 → V3 MB | 파일 수 V2 → V3 |
|---|---:|---:|---:|
| ABC·XYZ 32 | 50.807 → 22.781 | 3.884 → 2.137 | 9 → 138 |
| LEGACY_STYLE 32 | 50.803 → 22.777 | 3.884 → 2.150 | 9 → 138 |
| WIDE_REFERENCE 88 | 139.085 → 60.455 | 10.584 → 5.675 | 17 → 146 |

- 각 폴더의 공유 파일은 129개다. 동일한 Calendar/Forecast Suffix와 분류 값은 Input/Header/Leaf 사이에서도 재사용했다.
  중단/멱등 검증에서 기존 파일의 inode·mtime·크기는 유지됐다. 기존 V1/V2 완료 실행의 Receipt도 그대로 보존됐다.
- 원 Input/Header/Leaf를 모두 펼친 논리 크기는 각각 50,793,584 / 50,789,959 / 139,054,075 Byte로 V2와 같다.
  참조화가 계산 원문 자체를 축소하거나 후보를 삭제한 것은 아니다. 물리 저장 Manifest는 참조 Metadata가 달라 Hash도 달라질 수 있다.
- V2/V3 전체 검증 원문과 측정 Metadata의 합은 346,861,341 Byte, 실제 462파일·28,466,267 Byte다.
  Source 382개 파일의 지문 Hash는 `e713463d7d8037fac485f3def97da33af8b872ace6b07ea0089a9316af1d845c`이며 종료 때 재확인했다.
  결과 원문은 로컬 임시 전용 폴더에 보존한 개발 증적이지 중앙 Archive나 실제 DB 게시가 아니다.
- 저장량만 한 번씩 비교한 검증이다. 파일 수가 늘고 원문 복원·실제 재생 검증은 계속 필요하므로 속도·최대 메모리 개선을 주장하지 않는다.
  기존 18회 V1/V2 벤치마크와 이 기록을 섞어 3회 성능 비교가 완료된 것으로 표현하지 않는다.
- 새 단위 48건·계약 3건·Offline 통합 13건의 64건을 포함한 전체 회귀는
  **1,591 passed / 9 skipped / 669 subtests passed**(761.16초), 기존 Starlette 경고 3건이다.
  정확한 파일 참조·Source 지문·보존 결과와 미수행 도구는
  [검증 기록](evidence/robustness-shared-inputs-20261020.json)에 구분한다.
