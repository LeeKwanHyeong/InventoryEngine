# C# 계산 DLL·Python.NET 소규모 검증

후속 상태(2026-10-21): Python.NET 채택과 [계산 경계 계약](IO_CALCULATION_BOUNDARY_CONTRACT.md)을 확정했다.
아래 측정 이력은 그대로다. 기존 Python 계산·실험 DLL은 변경하지 않았다.
후속 [정식 DLL·Canonical Adapter](IO_CANONICAL_DLL_ADAPTER.md)의 Legacy 투영·별도 목표 전이와 Baseline은 구현·검증했다.
목표 Recommended의 [전략·Guard·정책 선정·발주 계획](IO_TARGET_GUARDED_EXECUTION.md)도 Offline 연결했다.
저장·Runtime 연결은 아직 없으며 아래 성능 수치는 새 Adapter나 새 Pipeline의 측정값이 아니다.

## 현재 결론 — 2026-10-21

**완료 — 제한된 Offline 입력에서 결과 동등성·변환 포함 속도·메모리·중단 확인**

기존 Python 계산은 수정하지 않고, 원본 C# 계산문을 담은 별도 DLL과 명시적으로 호출하는
Python.NET Adapter를 추가했다. 16품목·32후보·52주에서 입력 준비·입출력 변환·전체 결과 Hash를
포함한 호출 중앙값은 Python.NET **9.918ms**, 기존 재현 Python **167.237ms**다.
이 입력의 반복 호출에서는 약 **16.86배** 빨랐다. C# 직접 호출 **7.858ms**보다는 약 **1.26배** 오래 걸렸다.

Python.NET의 프로세스 최대 RSS 중앙값은 **119.97MiB**로, Python **36.83MiB**보다 높다.
Adapter 생성에는 별도로 약 **144ms**가 들었다. 작은 작업마다 새 CLR을 시작하면 이득이 줄거나 사라질 수 있다.
계산 전용 Worker를 재사용하되 호출별 가변 상태는 분리하는 구성이 후속 검토 대상이다.

이 결과는 운영 Runtime 교체 승인이나 전체 Legacy 동등성 판정이 아니다. CPU/GPU 서버·V100 실제 입력·최종 정책
선정·DB 게시 성능을 측정한 것도 아니다. 현재 운영 경로와 FSN·운영 Gate는 그대로다.

## 구현 경계

| 부분 | 구현·역할 | 변경하지 않은 부분 |
| --- | --- | --- |
| C# 계산 DLL | `Inventory.Legacy.Kernel.dll`, `PsiKernel.Prepare` → `PreparedBatch.Run` | 원본 PSI·Lot·LT·반올림 계산문 |
| Python.NET Adapter | 입력 bytes → 관리 배열 → DLL 일괄 호출 → 전체 결과 bytes | 기존 개선형 Python·`run_legacy_reference`·전략 Registry |
| 실험 Worker | 반복 측정·프로세스 메모리·취소·새 Worker 재계산 | Platform API·Run Claim·발주 확정·공용 배포 |

- [C# Facade](../../tests/reference/legacy_csharp/PythonNetFacade.cs)는 기존
  [참조 추출기](../../tests/reference/legacy_csharp/prepare_legacy_reference.py)의 지문 검증을 통과한 원본 계산 구간을 사용한다.
  DB·라이선스 초기화·원본 Main·단일 품목 테스트 필터를 실행하지 않는다.
- 입력은 이번 고정 Benchmark 형식만 지원한다. 최대 16품목·32후보·52주이며 운영 Canonical 입력 Adapter가 아니다.
  DLL은 JSON을 메모리에서 읽고 결과를 메모리로 반환한다. DB·파일 저장·Python 콜백 의존성이 없다.
- [Adapter](../../src/dsio_inventory_engine/infrastructure/legacy_pythonnet.py)는 기본 설치에서 CLR을 로딩하지 않는다.
  명시적 생성 시 DLL SHA-256·Runtime Config를 확인하고 CoreCLR을 선택한다. 오류 시 기존 Python으로 조용히 대체하지 않는다.
- 입력 최대 1MiB, 반환 최대 3,514,424bytes를 제한한다. `Marshal.Copy`로 버퍼를 일괄 복사하며
  포인터는 동기 복사 동안만 사용한다. 행별 CLR 속성 순회나 zero-copy를 사용했다고 주장하지 않는다.
- 호출마다 `BufferPSIObject`와 주차 상태를 새로 만든다. 준비된 입력의 재사용은 결과·취소 상태 재사용을 뜻하지 않는다.
- 실행 중인 C# 메서드를 Python 예외로 강제 중단한다고 가정하지 않는다. 강제 시간 제한은 Worker 프로세스 경계에서 처리한다.

## 비교 조건과 측정 범위

- 기준 브랜치 `inventory_engine_dev`, HEAD `99f2f1286d90dfe0e26025e603786a813ea51dac`와 기존 미커밋을 보존했다.
- 기존 [고정 입력](data/legacy-psi-benchmark-input-20261021.json)을 그대로 사용한다.
  입력 SHA-256은 `68e6c84e4812b0b3c8fa63757c658c91188317425f743585ff138b56471283c0`이다.
- [실험 프로토콜](data/legacy-pythonnet-experiment-protocol-v1.json)은 본측정 전에 동결했다.
  C# 직접 호출·Python.NET·기존 Python의 순서를 교대하며, 조건별 새 프로세스 3회에 최초 1·Warm-up 3·본측정 7회를 수행했다.
  조건별 본측정은 21개, 합계 126개다. 최초·Warm-up을 포함한 결과 파일은 198개다. 표본을 제거하지 않았다.
- 세 방식 모두 같은 로컬 Linux Arm64 이미지, CPU quota 1, 메모리·Swap 합계 1GiB, PID 256, 직렬 Worker 1을 사용했다.
  실행 중 네트워크를 차단하고 소스·DLL·입력은 읽기 전용으로 Mount했다. DB·Secret·Docker Socket을 Mount하지 않았다.
- CPython 3.12.15, .NET 8.0.32 / SDK 8.0.426, Python.NET 3.0.5,
  clr_loader 0.2.8, cffi 1.17.1, pycparser 2.22다. 의존성은 실험용 Docker 이미지에만 설치했다.
  C#은 Release이고 `DOTNET_TieredCompilation=0`, `DOTNET_PROCESSOR_COUNT=1`, `PYTHONHASHSEED=0`이다.
  GC를 강제 실행하거나 비활성화하지 않았다. Docker Server 29.8.0 / kernel 7.0.12-linuxkit다.
- **호출 전체:** 입력 변환·입력 확인, JSON 해석/준비, 실제 계산, 고정 binary 직렬화, 결과 변환, 전체 결과 Hash를 포함한다.
  준비된 입력만 재사용하는 최고 속도 수치가 아니다. Python.NET의 입력 Hash Guard도 포함한다.
- **별도 측정:** 새 파일 배타 생성·전체 쓰기·파일 fsync. 실제 Evidence Writer·Parquet·DB·Receipt 게시 시간이 아니다.
- **제외:** Python 프로세스/컨테이너 시작, Adapter 생성·DLL 확인·CoreCLR 로딩, 빌드, 원천 조회.
  최초 호출은 Adapter 생성 후 첫 계산이다. 전체 cold-start와 동일시하지 않는다.
- `managed_call_seconds`는 계산과 직렬화 등을 포함하는 상위 구간이다. 내부 계산/직렬화와 중복 합산하지 않는다.
  각 열의 중앙값을 더해 전체 중앙값을 만들지 않는다. C# 직접 호출 대비 차이 전부를 순수 경계 호출 비용으로 단정하지 않는다.

## 실제 결과

단위는 시간 ms, 메모리 MiB다. 시간은 조건별 21표본 중앙값, RSS는 새 프로세스 3개의 최대 RSS 중앙값이다.

| 품목 | 방식 | 계산 | 준비·변환·Hash 포함 전체 | 전체 P95 | 파일 저장 포함 전체 | 프로세스 최대 RSS |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 1 | C# DLL 직접 | 0.135 | 1.015 | 1.774 | 1.377 | 49.80 |
| 1 | Python.NET | 0.097 | 1.162 | 4.063 | 1.587 | 83.32 |
| 1 | 기존 재현 Python | 9.091 | 10.829 | 10.940 | 11.268 | 23.85 |
| 16 | C# DLL 직접 | 1.496 | 7.858 | 10.832 | 10.164 | 75.89 |
| 16 | Python.NET | 1.393 | 9.918 | 11.461 | 12.375 | 119.97 |
| 16 | 기존 재현 Python | 144.953 | 167.237 | 209.955 | 170.419 | 36.83 |

16품목 Python.NET의 입력 변환/확인 중앙값은 **0.163ms**, 준비 **1.658ms**,
관리 코드 호출 전체 **5.768ms**, 결과 복사 **0.983ms**, 결과 확인/Hash **1.067ms**다.
Adapter 생성 중앙값은 **144.253ms**, 생성 후 최초 호출 중앙값은 **35.759ms**다.
1품목 Adapter 생성은 **140.502ms**, 최초 호출은 **23.201ms**였다.

최대 RSS는 CLR·Python·준비 입력·결과 버퍼·JIT·런타임 할당을 포함한 프로세스 전체 지표다.
품목당 메모리나 순수 계산 메모리, 장기 메모리 누수 검증 수치로 해석하지 않는다.
이전 [성능 기준선](IO_LEGACY_PSI_PERFORMANCE_BASELINE.md)은 준비를 제외했으므로 해당 전체 시간과 직접 혼합하지 않는다.

## 결과 동등성과 상태 분리

- 1품목 1,664행·16품목 26,624행의 전체 결과를 모두 저장했다. 198파일·2,800,512행, 총 369,678,672bytes다.
  전 표본의 Header·입력 Hash·건수·전체 바이트가 같았으며 불일치 0건이다.
- 품목/후보/주차 Index, 발주일/도착일 ordinal, PSI 수치 14개를 포함한다.
  전체 파일 비교 후 행 순서·누락·비유한 수치를 확인했다. 이번 지원 범위의 날짜는 date-only다.
- 이전 실제 C#/Python 출력 Hash도 그대로다.
  - 1품목: `8bec8190823c6eafd525220c6b1c2100a30ad5728d6fd1049ae518c949da0ebe`
  - 16품목: `13f50b6ffcf5294cd6ebc21368bf25634d198ad8ddd37698ff9c5208b5101ec7`
- 동일 준비 입력을 반복 실행하고 중간에 1품목 실행·잘못된 입력 예외를 끼워 넣어도 16품목 결과가 같았다.
  협력적 취소 이후의 재계산도 같았다. 비용·서비스 최종 집계나 최적 정책 선정의 Legacy 동등성은 이 검사의 대상이 아니다.

## 실제 중단·복구 확인

| 경우 | 실제 확인 | 경계 |
| --- | --- | --- |
| 이미 취소된 Token | 결과를 반환하지 않고 `OperationCanceledException` | 운영 Run 상태와 연결하지 않음 |
| Timer 취소 | 100ms 요청 후 약 105.970ms에 관측, 취소 후 결과 Hash 동일 | 품목·후보 경계에서 확인; 원본 주차 루프 내부에는 취소문을 삽입하지 않음 |
| SIGTERM | 계산 반복 Worker가 약 113.479ms에 종료, 종료 코드 143 | CoreCLR 처리 후 `128 + SIGTERM` 종료도 정상 중단으로 인정 |
| 강제 시간 초과 | 준비 완료 후 150ms 제한, SIGKILL 뒤 약 3.910ms에 회수 | Python 스레드 중단이 아니라 프로세스 종료 |
| SIGKILL | 약 3.393ms에 종료 확인, 종료 코드 -9 | 프로세스 내부 CLR 객체는 재사용하지 않음 |
| 새 Worker 복구 | 세 종료 조건 모두 새 프로세스가 기존 Hash를 재현 | 후보 중간 Checkpoint 재개가 아닌 동일 입력 재계산 |

종료 검증은 최소 1회 실제 계산을 끝낸 뒤 계산을 반복하는 Worker에서 수행했다.
정확히 어떤 C# 명령에서 종료됐는지를 주장하지 않는다. 중단 Worker가 성공 결과를 반환하지 않는 것도 확인했다.
Artifact 저장 도중의 원자 게시·Durable Outbox·Run Ledger 복구는 이번에 실행하지 않았다.

## 검증과 증적

- 신규 의존성 없는 테스트 **25 passed**. DLL/입력 변조·건수·경로·시간 표본 오류·사전 Guard를 검증했다.
- 관련 Unit·Contract·Offline Integration **489 passed / 51 subtests passed**(27.11초)다.
  전체 저장소 테스트 수치는 아니다. Python 392파일 Ruff·Format, `git diff --check`도 통과했다.
- 독립 에이전트 검토·Coverage·장기 부하·여러 OS/CPU 검증은 수행하지 않았다.
- C# Release 빌드는 성공했다. 원본 추출 DTO의 nullable annotation에 대한 CS8632 경고 1건이 남으며
  계산문을 바꾸거나 경고를 숨기지 않았다. 사전 검증에서 SIGTERM을 -15만 인정하던 테스트를 실제 CoreCLR 종료 코드 143도 인정하도록 보정했다.
- [원시 측정과 중단 결과](evidence/legacy-pythonnet-experiment-results-20261021.json),
  [Source/Build 준비](evidence/legacy-pythonnet-experiment-preparation-20261021.json),
  [재검증·보존 확인](evidence/legacy-pythonnet-experiment-verification-20261021.json)을 연결한다.

고정 실험 이미지 ID:
`sha256:a0b6ba2e267b11262a0f42d0f67ebf64f1b9b14d7c2dab38c923713142d372c0`

실제 계산 DLL SHA-256:
`b010b2ed96690f9030a075b96d26a274a57727d0098d9fca349492926bf671bd`

소스·입력·출력 binary와 DLL은 로컬 임시 작업 폴더 `/private/tmp/io-pythonnet.p88wdK/final`에 보존했다.
저장소에는 바이너리 대신 소스·정확한 입력 참조·지문·원시 측정 JSON을 남긴다. 임시 파일이 없어지면 같은 조건으로 재실행해야 한다.

## 재실행

기존 로컬 기준 이미지 태그 `inventory-legacy-psi-benchmark:20261021`의 ID가 프로토콜의 `base_image_id`와 같은지 먼저 확인한다.
`PythonNet.Dockerfile`로 별도 실험 이미지를 만들고 실제 이미지 ID를 고정한다. Mac 전역 설치는 필요 없다.

```bash
PYTHONPATH=.:src python scripts/benchmark_legacy_pythonnet.py prepare \
  --source-root '/Users/igwanhyeong/NSPM 2/SupplyEngine/DSIO_postgresql_20250908/RootIO' \
  --output <fresh-private-temp-directory>
```

준비된 `build`만 쓰기 가능하게 Mount한 격리 컨테이너에서 `build/source/PythonNet.Direct.csproj`를
`-c Release -o /build/out --configfile NuGet.Config`로 빌드한다. `PythonNet.Directory.Build.props`는 준비기가
`Directory.Build.props`로 복사하며 중간 산출물을 프로젝트별로 분리한다.

```bash
PYTHONPATH=.:src python scripts/run_legacy_pythonnet_experiment.py \
  --root <prepared-private-temp-directory> --image sha256:<verified-local-image-id>
```

기존 결과 폴더를 재사용하지 않는다. 위 도구의 새 파일 배타 생성은 덮어쓰기 방지이지 원자 게시 구현은 아니다.
공식 참고: [Runtime 로딩·Assembly 호출](https://pythonnet.github.io/pythonnet/python.html),
[스레드와 GIL](https://pythonnet.github.io/pythonnet/threading.html).

## 남은 작업 순서

**현재 기준선 — Python.NET 채택·계산 경계 계약 완료 · InventoryEngine**

- [후속 계약](IO_CALCULATION_BOUNDARY_CONTRACT.md)에서 Canonical 대응, Legacy/Target 의미와 Guard 순서를 정했다.
  구조 검증 통과는 현재 DLL의 Canonical 지원이나 운영 입장 승인을 의미하지 않는다.

**1. 정식 DLL·Canonical Adapter 확장 — 다음 작업 · InventoryEngine**

- 고정 실험 입력을 실제 입력/후보/Calendar로 확장하고 필요한 원 인자와 Target 전이 인터페이스를 구현한다.
  재현 계산에 Guard가 적용됐다고 표시하거나 주문 결과만 사후 수정하지 않는다.

**2. 실제 Guard·발주 계획·저장 연결 — 1번 이후 · InventoryEngine**

- 기존 ID/Hash·공통 결과 계약에 연결하고 허용 행동에 따른 후속 상태, Writer/Verifier의 중단·복구를 검증한다.
  이번 소규모 측정이나 참조 계약 완료를 실제 IO 연결 완료로 표시하지 않는다.

**3. 실제 DB 적재·공용 배포 — 승인 필요 · InventoryEngine·Platform**

- 별도 명시적 승인 전에는 Migration·실제 E2E DB Write·공용 Runtime 배포·발주를 수행하지 않는다.
  Commit·Push는 이번에 요청받지 않았으므로 하지 않았다.
