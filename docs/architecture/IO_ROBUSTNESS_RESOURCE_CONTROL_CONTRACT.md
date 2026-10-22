# 강건성 Catalog의 전역 원문 예산·실행 중단·임시 파일 정책

## 1. 적용 범위와 변경하지 않는 계약

2026-10-20, InventoryEngine `inventory_engine_dev`의 로컬 개발 Catalog 제어 경로에 적용한다.
한 서비스/실험에서 같은 관리 루트를 사용하는 모든 저장 폴더와 프로세스가 예산을 공유한다.
프로세스나 Retry마다 새 루트를 만들어 예산을 초기화하는 사용법은 허용하지 않는다.
다른 관리 루트·임의 디렉터리·호스트 전체 디스크·다른 서비스까지 통제하는 OS 보안 Sandbox는 아니다.
분산 파일시스템이 아닌 POSIX 로컬 파일시스템과 신뢰하는 설치 Factory를 전제로 한다.

기존 `RunDurableItemCatalogUseCase`, 원문/결과 Hash, V1/V2/V3 형식과 기존 직접 호출은 바꾸지 않는다.
새 [ControlledCatalogExecutor](../../src/dsio_inventory_engine/evaluate_replenishment/robustness/resource_execution.py)가
실제 영속 조립기와 [자원 통제 저장 Adapter](../../src/dsio_inventory_engine/infrastructure/robustness_resource_store.py)를 연결한다.
새 제어 경로를 사용하지 않는 과거 직접 호출에 제한이 자동 적용됐다고 표현하지 않는다.
공용 업무 Runtime·Platform API·운영 게시/발주를 활성화하거나 배포한 작업이 아니다.

## 2. 닫힌 자원 정책과 예산 단위

[정책 계약](../../src/dsio_inventory_engine/inventory_contracts/robustness_resources.py)과
[Schema](../../schemas/robustness_resource_policy.schema.json)는 별도 `inventory-robustness-local-resource-policy` `1.0.0`이다.
계산 Config·분류·원 Artifact에 이 메타데이터를 추가하지 않는다.

| 항목 | 기준 |
|---|---|
| 관리 루트 전체 원문 예산 | 최대 20,000,000,000 Byte, 십진 20GB |
| 원 문서/압축 파일 상한 | 기존 128,000,000 Byte 유지 |
| 호출별 Deadline | 최대 3,600초, 대기·기동·계산에 같은 단조 시계 기준 적용 |
| 계산 Worker | 관리 루트당 1개, 다른 호출은 OS Lock을 기다림 |
| 파일 수 | 관리 범위 최대 100,000개, 게시 전 임시/예약 여유도 확보 |
| 미완성 임시 파일 | 격리 후 최소 86,400초 보존, 명시적 정리 호출만 허용 |
| 승인·자동 게시·자동 발주·DB Write | 모두 `false` |

작은 예산/짧은 Deadline은 테스트에 사용할 수 있지만 최대값을 늘리거나 0/문자열/Boolean/무한대로 비활성화할 수 없다.
원문 예산은 Input·Leaf·Header·Manifest·Slot·공유 값 Wrapper와 자원 메타데이터를 합산한다.
기동 전달용 Job 파일도 같은 예산에 포함한다. Job을 별도 디렉터리에 숨겨 예산에서 제외하지 않는다.
Gzip은 제한된 크기로 실제 복원한 원문 길이를 사용한다. 압축률로 예산을 할인하지 않는다.
V3 공유 파일은 물리적으로 저장된 원문을 한 번 계산하고, 공유 값을 펼친 큰 논리 문서 크기와 구분한다.
게시 직후 남은 임시 Hard Link는 같은 Inode이므로 같은 바이트를 두 번 합산하지 않는다.
실제 파일 점유량은 별도 값으로 보고하며, 프로세스 전체 RSS나 호스트 디스크 할당량 제한을 구현한 것은 아니다.

## 3. 예약·게시·재시작 대사

[LocalRobustnessResourcePool](../../src/dsio_inventory_engine/infrastructure/robustness_resource_pool.py)은
[POSIX 파일 규칙](../../src/dsio_inventory_engine/infrastructure/robustness_resource_files.py)에 따라 동작한다.

1. 비어 있는 전용 폴더에 정책을 고정한다. 재시작 시 같은 정책이어야 하며 기존 임의 사용자 폴더는 채택하지 않는다.
2. 모든 Writer는 같은 `resource.lock`으로 예산 확인·예약·실제 파일 게시를 직렬화한다.
3. 실제 파일을 대사하고 신규 원문·예약 메타데이터·복구 메타데이터 4,096 Byte 여유를 게시 전에 확보한다.
4. `pending.json`을 먼저 원자 저장·fsync한 뒤 기존 Writer의 임시 파일 → Hard Link → fsync 게시를 수행한다.
5. 정상 완료 시 실제 파일을 다시 대사하고 예약을 해제한다. 같은 참조의 멱등 저장은 다시 원문을 할당하지 않는다.
6. 프로세스 종료 후 OS Lock은 해제된다. 새 프로세스는 남은 파일에서 사용량을 다시 계산하고 임시 파일을 격리한다.

원문 길이의 프로세스별 캐시는 Device/Inode/크기/mtime/ctime이 같은 파일에만 사용한다.
재시작에서는 새로 계산하며, 이 캐시는 Source 권위·PSI 계산·파일 무결성 검증을 대체하지 않는다.
원래 Reader와 전체 재계산은 계속 원 바이트·Hash·실제 계산을 검증한다.
비정규 파일명·Symlink·FIFO·잘못된 Gzip/복원 크기·다른 정책·바뀐 관리 루트는 차단한다.
이미 존재하는 데이터가 한도를 넘으면 사용량은 진단할 수 있지만 새 게시를 허용하지 않는다.
후보·기간·PSI Row를 줄여 합격시키거나 게시된 데이터를 자동 삭제해 공간을 만들지 않는다.

## 4. 3,600초 중단과 호출 수명

제어기는 `run`, `recover`, `inspect`, `verify_recalculation`만 허용한다.
Factory는 통제된 Repository에 연결된 실제 영속 조립기를 만들어야 한다.
Factory·Source 검증·공통 Guard·전략·PSI·평가는 새 Worker 안에서 실행하고, IPC에는 Receipt 또는 검증 요약만 반환한다.
큰 PSI를 잘라 결과를 줄이거나 별도 계산 결과로 대체하는 방식이 아니다.

큰 입력을 `multiprocessing` 시작 파이프로 보내지 않는다. 예산 확인과 fsync를 거친 임시 Job 파일의 이름/Hash만
[독립 Worker 진입점](../../src/dsio_inventory_engine/evaluate_replenishment/robustness/resource_worker.py)에 전달한다.
Factory는 설치 모듈에서 다시 찾을 수 있는 이름이어야 한다. 대기뿐 아니라 Python 시작·입력 로딩이 멈춘 경우에도
감독 프로세스의 Deadline이 적용된다. 응답 채널은 비차단 방식으로 읽으며 일부 응답 뒤 중단도 성공으로 처리하지 않는다.

호출마다 최대 3,600초의 단조 시계 Deadline을 만든다. Worker는 별도 Process Group에서 실행한다.
호출자가 취소하면 감독 프로세스가 소유한 그룹을 중단하고, 별도 Watchdog도 Deadline과 감독 프로세스 소멸을 감시한다.
따라서 계산 코드가 중단 예외를 잡아 실패 후보로 삼더라도 제한을 무력화할 수 없다.
정상 완료 시 Watchdog도 정리한다. 다른 서비스의 Process Group을 대상으로 삼지 않는다.

시간 초과·취소·Worker 비정상 종료는 `RESOURCE_INVOCATION_TIMED_OUT`, `RESOURCE_INVOCATION_CANCELLED`,
`RESOURCE_WORKER_EXITED` 등으로 반환한다. 성공·서비스 미달·후보 실패로 바꿔 기록하지 않는다.
이미 종료된 그룹의 정리 오류가 원래 시간 초과를 덮지 않게 하되 살아 있는 그룹의 종료 거부는 성공으로 숨기지 않는다.
정확히 3,600초 시각에 응답을 완료하는 실시간 보장은 아니며, Worker 중단·회수·파일 대사 후 반환한다.
신뢰하지 않는 코드의 고의적 세션 이탈이나 외부 프로세스까지 제어하는 Sandbox는 아니다.

## 5. 중단 후 재개와 임시 파일 정리

- 봉인된 Input과 게시된 Leaf/Checkpoint는 그대로 남긴다. 새 `recover`는 정확한 입력 Hash로 기존 Source와
  저장된 Leaf의 실제 계산을 검증하고 미완료 Leaf를 계산한다. 최신 Source·다른 Hash로 대체하지 않는다.
- 이 복구는 같은 로컬 계산 증적의 기술적 재개다. Planning Cycle/Attempt/새 `engine_run_id` 생성 계약은 바꾸지 않는다.
- 게시되지 않은 `.robustness-*` 파일은 격리 폴더로 옮기고 원 위치·원문 예약 크기·물리 Hash·격리 시각을 기록한다.
  예약이 있으면 그 원문 크기, 없으면 문서 상한 128MB로 보수적으로 계산한다. 격리 파일도 예산에 포함한다.
- 게시 직후의 Hard Link를 격리해도 원 Artifact는 남는다. 게시됐지만 아직 Checkpoint가 없는 객체·공유 파일도
  복구에 필요할 수 있으므로 자동 정리하지 않는다.
- `purge_expired_temporaries`는 24시간이 지난 격리 파일과 그 메타데이터만 명시적으로 정리한다.
  원 위치·Hash·구조를 검증한 뒤 격리된 이름만 삭제하며, 게시된 원본·Slot·완료 결과는 대상에 포함하지 않는다.
- 격리 자체가 중단되어 메타데이터가 없는 파일은 128MB로 계산하고 자동 삭제하지 않는다.
  임의 파일·손상된 메타데이터를 발견하면 정리를 중단한다. 외부 자료를 조용히 삭제하지 않는다.
- 기동 Job은 원본 PSI Artifact와 구분한다. 호출 종료·Owner 소멸·Deadline 후에는 같은 24시간 격리 정책으로 보존한다.
  Worker는 전달받은 Job Hash를 다시 검증하며, Job 정리로 원 Input·Checkpoint를 삭제하지 않는다.

Artifact의 `inspect`는 원 계산 파일에 쓰지 않는다. 다만 제어기 진입/종료 시 자원 계층의 예약 대사·임시 파일 격리는
별도 관리 동작으로 수행될 수 있다. 이 동작을 Source/PSI 재계산 검증이나 운영 승인으로 표시하지 않는다.

## 6. Offline 검증과 미수행 범위

- 실제 2055년 수작업 입력으로 ABC/XYZ 32개·LEGACY_STYLE 32개·참조 88개 전체를 새 제어 경로로 실행한다.
  기존 결과 Hash와 2,080/2,080/5,720 Row, 조회/전체 재계산 구분을 확인한다.
- V1/V2/V3 원문·참조·멱등성과 기존 형식의 읽기 호환성을 확인한다.
- 서로 다른 두 프로세스가 같은 작은 원문 예산에 경합해도 한쪽만 게시할 수 있는지 확인한다.
  물리 압축 파일이 작아도 원문 예산을 우회하지 못하며 새 인스턴스가 예산을 초기화하지 않는지 확인한다.
- 응답하지 않는 Worker·대기 중 Deadline·취소·하위 프로세스·감독 프로세스 소멸·임시 파일 작성 중 중단을 실제 프로세스로 검증한다.
- Python 시작을 일부러 지연시키고 큰 입력을 전달한 경우에도 Deadline이 동작하는지 확인한다.
- 기동 Job도 파일을 쓰기 전에 원문 예산과 파일 수 여유를 확인한다. 상한 직전의 거부가 새 파일을 남기지 않는지 확인한다.
- 실제 첫 Leaf Checkpoint 직후 중단한 뒤 기존 파일/Hash를 보존하면서 재개하고, 다른 입력 Hash는 차단한다.
- 24시간 전 정리 금지·만료 후 격리 파일만 정리·손상/미확인 파일 보존·FSN BLOCK/REVIEW·자동 게시/발주 금지를 확인한다.

기본 20GB/3,600초 값은 계약으로 확인하고 실제 경합/중단 테스트에는 작은 예산과 짧은 Deadline을 사용한다.
20GB 데이터를 생성하거나 3,600초짜리 시험을 실행해 자원을 소모한 검증은 아니다.
실제 수치와 지문은 [검증 기록](evidence/robustness-resource-control-20261020.json)에 남긴다.
공식 2037/2044 경로 생성·본실험·DB·공용 Runtime·실제 발주·Commit/Push는 수행하지 않는다.

## 7. 남은 작업

**현재 기준선 — 전체 작업 목록과 부분 자원 사전 검증 · InventoryEngine**

- [후속 사전 검증 계약](IO_ROBUSTNESS_SPLIT_PREFLIGHT_CONTRACT.md)에 421개 작업 상한과 같은 관리 루트의 수작업 측정을 추가했다.
- 미측정 항목이 남아 있으므로 부분 용량 추정만으로 전체 자원 합격을 표시하지 않는다. 기존 예산·중단·복구·원문/결과 Hash는 유지한다.
- 실제 두 차례 순환 측정의 기존 Hash·전체 후보/PSI가 일치했다. 알려진 부분은 25% 여유 포함 원문 약 17.455GB이지만
  전체 Source·집계·종료 재생·누적 대사·중단 여유가 미확인이므로 공식 실험은 허용하지 않는다.

**다음 작업 — 미측정 계산·집계 경로와 전체 자원 합계 확인 · InventoryEngine**

- 잠긴 단일 정책의 52주 종료 재생, 범위/비용/선정·Manifest, 대표 수작업 Source와 누적 루트/중단 여유를 측정한다.
- 공통 계약 확정 후 순수 검증은 분리할 수 있지만 같은 Pool의 성능 측정은 직렬로 한다. 공식 경로 생성·본실험은 이번에 진행하지 않는다.

**승인 필요 — 실제 환경 연결 · InventoryEngine·Platform·개발 PostgreSQL**

- 실제 DB 적재·Migration·공용 배포·Demand P12 연결은 별도 승인 범위다.
