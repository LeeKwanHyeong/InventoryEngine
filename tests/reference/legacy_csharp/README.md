# DB 없는 Legacy C# 참조 실행

이 폴더는 테스트 전용이다. InventoryEngine 전략·계산 코드나 Legacy 원본을 수정하지 않는다.
별도 Python 재현 함수가 후속 추가됐지만 기존 개선형 계산·Runtime에는 연결하지 않는다.

## 별도 Python 기준 구현 대조

[재현 계약](../../../docs/architecture/IO_LEGACY_PYTHON_REFERENCE_CONTRACT.md)에 따라 기존 계산 27회와 추가 65회를 대조한다.
추가 입력은 `tests/fixtures/legacy_replay_vectors.json`, 실제 출력은 `native_legacy_replay_vectors_output.json`이다.
`ReferenceHarness`는 `replay_cases` 입력일 때 원문 PSI와 .NET 숫자 Primitive를 실행한다. 기존 Golden 모드 출력은 유지한다.
추가 원문 실행 시 준비한 Stage 외에 해당 입력 파일 한 개만 `/vectors.json`으로 읽기 전용 Mount하고
같은 빌드 결과를 `dotnet /tmp/out/Reference.dll /vectors.json`으로 실행한다. DB·네트워크·Secret은 사용하지 않는다.

```sh
PYTHONPATH=.:src:tests/support python tests/reference/legacy_csharp/verify_python_replay.py
```

수치 비교는 C# round-trip 문자열을 binary64로 읽은 값과 `float.hex()`를 대조한다. 날짜·순서도 같아야 한다.
전체 서비스/최종 선정·운영 배포본의 동등성이나 성능 개선을 뜻하지 않는다.

## 실제 실행한 범위

- 제공 원본 7개 파일의 지문을 먼저 확인한다. 계산 구간과 실제 DTO 선언 21곳을 줄 단위로 추출하며 줄바꿈만 LF로 정규화한다.
- 원본 `Program.cs`의 주차 계산 Loop, 재고 할당 구간, BaseMonth 결정, Lot, LT 환산,
  `SetBucket/SetDuein`, `ApplyFixedCondition`을 그대로 C#으로 컴파일한다.
- 입력/출력 연결과 메서드 Wrapper만 테스트 코드다. HorizonCalendar의 List 컨테이너와 무출력 로그만 보조물로 제공한다.
- 원본 Main·라이선스 진입점·DB 전후처리·연결 설정·전체 Optimizer는 실행하지 않는다.
  `BA59-04709A@V101` 테스트 필터도 계산 Loop 앞에 있어 추출 범위에서 제외했다.
- 재고 전이 시험은 할당 구간, 발주/Trigger 시험은 전체 주차 Loop를 실행한다. 선정 시험은 적격 조건만 실행하며 최종 Winner가 아니다.

## 재실행

저장소 루트에서 Python 테스트 환경을 사용한다. `--source-root`는 확보한 원본 `RootIO` 디렉터리다.
`--output`은 저장소/원본 바깥의 **존재하지 않는 새 임시 하위 폴더**여야 한다.
예시의 `io-reference-UNIQUE` 부모 폴더는 먼저 `mktemp -d` 등으로 별도 준비한다.

```sh
python tests/reference/legacy_csharp/prepare_legacy_reference.py \
  --source-root '/path/to/DSIO_postgresql_20250908/RootIO' \
  --output /private/tmp/io-reference-UNIQUE/stage
```

공식 이미지 `mcr.microsoft.com/dotnet/sdk`의 확인한 Digest:
`sha256:f7e28a0a3f6f9235929ec19274fd0ddf8534e9159f99fac602ac73304f1ce99f`.
실제 실행은 SDK 8.0.426 / .NET 8.0.32 / Linux Arm64였다.

```sh
docker run --rm --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges --pids-limit 256 --cpus 2 --memory 1g \
  --tmpfs /tmp:rw,exec,nosuid,size=1g \
  --mount type=bind,source=/private/tmp/io-reference-UNIQUE/stage,target=/source,readonly \
  --workdir /source --env DOTNET_CLI_HOME=/tmp/dotnet-cli \
  --env DOTNET_CLI_TELEMETRY_OPTOUT=1 --env DOTNET_SKIP_FIRST_TIME_EXPERIENCE=1 \
  mcr.microsoft.com/dotnet/sdk@sha256:f7e28a0a3f6f9235929ec19274fd0ddf8534e9159f99fac602ac73304f1ce99f \
  sh -c 'dotnet build Reference.csproj --configfile NuGet.Config -o /tmp/out -p:BaseIntermediateOutputPath=/tmp/obj/ -p:MSBuildProjectExtensionsPath=/tmp/obj/ >&2 && dotnet /tmp/out/Reference.dll /source/golden.json'
```

원본/Golden Mount는 읽기 전용이고 빌드 결과는 컨테이너 임시 메모리 파일시스템에만 쓴다.
DB·Secret·Docker Socket을 Mount하지 않으며 실행 시 네트워크가 없다. NuGet 외부 Source도 비워 뒀다.
종료한 컨테이너는 제거되지만 공식 SDK 이미지 캐시와 호스트 임시 준비 파일은 남는다.

출력의 `NATIVE_REFERENCE_JSON:` 뒤 JSON이 실제 계산 출력이다. 수작업 기대값으로 이 출력을 생성하지 않는다.
보존 출력은 [native_legacy_reference_output.json](../../fixtures/native_legacy_reference_output.json)이다.
일반 CI는 C# 설치 없이 이 캡처와 현재 Python을 다시 비교한다. 실제 C# 재실행과 캡처 회귀를 구분한다.

```sh
PYTHONPATH=.:src:tests/support python tests/reference/legacy_csharp/compare_reference.py
PYTHONPATH=.:src:tests/support python -m pytest -q tests/integration/test_native_legacy_reference_offline.py
```

## 확인 결과와 한계

- C# 대응 20개 사례·29회 실행, Python 21개 사례, 수작업 Golden 불일치 0건이다.
  네 정책 코드의 주문식을 확인하려고 발주 사례 3개를 각각 4번 실행했다.
- 같은 입력은 비교 구간에 필요한 BOH·수요·입고·ROP/S·LT·Lot·Calendar를 맞춘다는 뜻이다.
  Legacy DB의 전체 Inbound와 Python Canonical 문서를 같은 형식으로 적재한 E2E가 아니다.
  C# 발주 시험의 MOS=1·MA_DMD_QTY=10은 명시적 보조 입력이며, 부가 ExpMOS 출력을 Python 평균 MOS와 같다고 검증하지 않는다.
- 새 격리 컨테이너에서 반복한 원시 JSON 출력도 같았다. 이 결과는 배포 Legacy 바이너리의 전체 동등성을 의미하지 않는다.
- 서비스 집계 `SVC_LV`, 그룹→품목 전개, 원본 RMOS/EMOS 생성과 경제적 EOQ는 아직 미검증이다.
  비용 미확인 사례는 대응하는 Legacy 비용 계약이 없어 Python만 검증했다.
- Integer/주차 경계의 작은 사례다. 모든 double 오차·월별 Source·간헐수요·성능·운영 실적을 검증한 것은 아니다.
- 원본 DTO의 nullable 주석 경고 CS8632와 읽기 전용 SDK의 workload 확인 안내가 있었으나 빌드·실행은 성공했다.
  이를 없애려고 원본을 고치거나 workload를 설치하지 않았다.
