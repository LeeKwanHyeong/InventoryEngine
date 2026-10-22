# 강건성 Catalog 실행의 불변 입력 준비·직렬화 재사용

## 1. 범위와 책임

`inventory_engine_dev`의 로컬 개발 Catalog 실행에만 적용한다. 같은 준비 입력의 원천 검증·Canonical 준비와
준비 결과의 정규 JSON 직렬화를 실행 Context 안에서 재사용한다. 정책 후보를 줄이거나 PSI Row·관측·행동·Guard를
생략하는 최적화가 아니다. 기본 업무 Runtime·FSN 설정·운영 적격 조건·DB 계약은 변경하지 않는다.

[준비 슬롯](../../src/dsio_inventory_engine/evaluate_replenishment/robustness/immutable_preparation.py)은
[관측 분리 PSI](../../src/dsio_inventory_engine/evaluate_replenishment/robustness/causal_simulation.py)에 연결한다.
[품목 조립기](../../src/dsio_inventory_engine/evaluate_replenishment/robustness/item_execution_steps.py)는 실행마다 새 PSI Runner를 만든다.
영속 실행·읽기·복구도 같은 조립기를 사용한다. 여러 호출/Build 사이에서 재사용하는 전역 캐시나 승인 Receipt는 없다.
현재 실행 계약은 CPU Worker 1개이며 슬롯을 여러 Worker의 공유 서비스로 사용하지 않는다.

## 2. 동일 입력 판정과 수명

재사용 Key는 다음 **실제 내용 전체의 동일성**으로 판정한다. 호출자가 준 Hash나 객체 ID만으로 일치시키지 않는다.

- 배포 Scope의 `company_cd`와 `environment`
- `RecommendationRequest.document_json` 전체: Canonical Snapshot·Scope·W0·Config·전략 구현/입력 Binding·Effective Policy·제약을 포함한다.
- 원 개발 Source와 Source 생성 요청의 정규 JSON 전체
- 기대 Source Hash

처음 일치하는 입력은 기존 Request 검증, Source 재계산·대사, Cut-off·Snapshot 검증과 Canonical 준비를 모두 거친다.
다른 입력은 기존 슬롯을 비우고 같은 검증을 다시 수행한다. 같은 Hash를 주장한 변조 입력도 새 검증에서 차단한다.
재사용이 꺼진 대조 실행은 후보마다 준비한다. 이 옵션은 개발 비교용이며 Gate를 완화하지 않는다.

슬롯에는 검증·정규화한 요청/Canonical/Prepared JSON 문자열과 입력·Source Hash만 보존한다.
키와 값의 UTF-8 크기를 중복 포함해 보수적으로 합산하고 최대 128,000,000 Byte 한 슬롯만 유지한다.
각 입력은 기존 문서 크기 제한도 통과해야 한다. 합산 보존 한도만 넘은 유효 입력은 저장하지 않고 정상 계산한다.
이 한도는 전체 프로세스 RSS 상한이나 전체 20GB 원문 예산의 구현을 뜻하지 않는다.

Build는 장수 캐시의 별도 문자열 Key가 아니라 해당 프로세스의 설치 코드와 Context 수명으로 격리된다.
새 실행·새 Build·새 프로세스는 새로운 슬롯을 만든다. 런타임 코드 교체나 교차 프로세스 재사용은 지원하지 않는다.
비교 실행은 사용한 Source 파일 목록·Hash도 시작/종료에 고정한다.

## 3. 후보별 상태와 미래 정보 분리

슬롯에서 매번 JSON을 새로 읽어 분리된 Dict/List를 반환한다. 고정된 시작재고·원 Source 정책·입고예정 값 자체는
불변 입력으로 보존하되, 다음 값은 절대로 계산 완료 값으로 재사용하지 않는다.

- 후보별 `CandidateStrategy`, RMOS/EMOS·ROP·Q·목표재고와 제안 행동
- Effective Policy 적용·FSN Admission, 제약 검증과 승인/자동 게시·발주 판단
- 현재 재고·Backorder, 신규 주문과 미도착 공급, 관측·Guard·PSI Row
- 실현 수요와 경로·Seed·선정/평가 결과

물리 Lead Time·정책 제약·초기 미도착 공급은 후보마다 분리된 입력에서 시작한다.
실현 수요는 매 실행에서 Hash·계획 Binding·길이·확정 주문 하한을 검증하고, 행동/Guard 뒤에만 해당 주차 값을 공개한다.
현재 요청의 입력 Hash는 한 번 계산해 사용하지만 원래 `digest(execution)` 의미를 바꾸지 않는다.
후보 간 비교에 포함되는 Source·관측·행동·PSI·평가·결과의 필드와 Hash는 변경하지 않는다.

## 4. 저장·검증 계약은 유지한다

V1 JSON·V2 Gzip·V3 공유 입력의 형식과 Writer/Reader는 변경하지 않는다.
원문 정규 JSON UTF-8+LF와 결과 Hash, 기존 완료 Receipt를 그대로 보존한다.
새 Context의 읽기·복구·게시 검증은 Source를 처음부터 확인한 뒤 전체 후보의 실제 전략·Guard·PSI를 재생한다.
한 Context 안의 Leaf 재생은 불변 준비만 재사용하며 후보 실행 결과를 캐시 성공으로 대신하지 않는다.
이 계약 확정 당시 후속이던 별도 조회·검증 호출은
[무결성 조회·전체 재계산 계약](IO_ROBUSTNESS_VERIFICATION_READ_CONTRACT.md)에서 추가했다.
`inspect`는 전체 Artifact 무결성만 확인하고 재계산을 `NOT_RUN`으로 반환한다.
`verify_recalculation`은 기존 `read`의 실제 재생을 수행해야 `VERIFIED`를 반환한다.
기존 `read`·`recover`의 실제 재생과 이 문서의 준비 슬롯 수명·Hash·후보 상태 분리는 유지한다.

## 5. 검증과 성능 비교 기준

- 단위 검증: 같은 내용의 다른 객체, 반환 값 변조, Source/요청/Scope/정책 제약 변경, 잘못된 Hash/Version,
  슬롯 크기와 미보존 대체 경로, Production·잘못된 재사용 스위치 거부.
- Offline 검증: 같은 수작업 입력의 재사용 ON/OFF 전체 32/32/88 후보 원문·Hash 일치,
  Leaf 역순 실행·반환 PSI 변조 후 재실행, FSN `BLOCK`·`REVIEW`, 실패 후보 보존,
  후보 16개가 든 Leaf 한 개 재생의 실제 전략 1,040회 실행과 잘못된 실현 경로 Binding 차단.
- 기존 전체 회귀로 V1/V2/V3 읽기·멱등·실제 프로세스 종료 후 복구·고정 ABC/XYZ Hash와 Gate를 다시 확인한다.

[비교 스크립트](../../scripts/benchmark_robustness_preparation.py)는 2055-01-04 수작업 한 경로와 Seed 71,
65주·32/32/88 전체 후보에서 ON/OFF를 각각 3회 새 프로세스로 실행한다. 기존 V2/V3 비교 기록이나
공식 강건성 실험 조건·경로·선정 결과를 덮어쓰지 않는다. 대조군에도 동일한 입력 Hash 지역 변수 재사용은 적용하므로
이번 차이는 준비 슬롯 유무를 비교한 것이며 과거 Commit 전체와의 속도 비교가 아니다.

Context 준비·후보 계산(불변 준비 포함)·조립/평가 시간을 구분한다. 준비 슬롯 처리 시간은 후보 계산 안에 포함된
하위 구간이므로 합산해 이중 계산하지 않는다. 프로세스 최대 RSS도 기록한다.
파일 저장/조회/재생 검증의 시간이나 OS 캐시를 비운 조건은 측정하지 않는다.
당시 비교 스크립트는 각 호출 3,600초·측정 파일당 128MB·총 원문 20GB를 확인했지만 전역 자원 통제는 구현하지 않았다.
이후 추가한 로컬 관리 루트의 제어 경로와 기존 직접 호출/공용 Runtime의 적용 경계는 제6절에 구분한다.
실측 결과와 전체 회귀 상태는 [검증 기록](evidence/robustness-immutable-preparation-20261020.json)에 구분해 기록한다.

## 6. 남은 작업

**현재 기준선 — 빠른 조회와 실제 재생 검증 분리 완료 · InventoryEngine**

- [후속 계약](IO_ROBUSTNESS_VERIFICATION_READ_CONTRACT.md)에 호출·상태·전체 후보/Hash 확인 범위와 Offline 검증을 기록했다.
  검증 응답을 계산 결과·승인 증명서로 대체하지 않으며 기존 재사용 비교 기록과 원문/결과 Hash는 보존한다.

**현재 기준선 — 후속 로컬 자원 제어 경로 구현 · InventoryEngine**

- [자원 통제 계약](IO_ROBUSTNESS_RESOURCE_CONTROL_CONTRACT.md)에 같은 관리 루트 전체의 원문 예산·3,600초 중단·재개·임시 파일 정책을 추가했다.
  이 문서의 불변 준비 재사용·후보 상태 분리·원문/결과 Hash와 기존 성능 비교 기록은 유지한다.

**다음 작업 — 전체 Split 조립 사전 검증 · InventoryEngine**

- 새 자원 제어 경로로 전체 예상 시간·용량·완전성을 확인한다. 공통 실행/저장 계약에 의존하므로 직렬로 진행한다.
- 그 이후에만 공식 경로 생성·본실험을 진행한다. 이번에는 공식 경로·본실험·DB·공용 배포·실제 발주·Commit/Push를 수행하지 않는다.
