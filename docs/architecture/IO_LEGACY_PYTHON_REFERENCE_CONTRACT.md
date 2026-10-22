# C# 동작을 재현하는 별도 Python 기준 구현

2026-10-21 · InventoryEngine `inventory_engine_dev` · **제한 범위 구현·실제 C# 대조 완료**.
기존 개선형 Python, FSN, 운영 Gate, DB, Runtime Binding은 변경하지 않았다.
이 재현 단계에서는 성능을 측정하지 않았고, 이후 [별도 성능 기준선](IO_LEGACY_PSI_PERFORMANCE_BASELINE.md)을 측정했다.
재현 계산 코드 자체의 변경이나 최적화는 하지 않았다.

## 1. 목적과 호출 경계

[기존 대사](IO_LEGACY_CSHARP_PYTHON_RECONCILIATION.md)에서 확인한 차이를 덮어쓰지 않고,
같은 입력에서 C# 동작을 재현하는 Python 기준을 추가했다.
`simulate_inventory/application/legacy_reference.py`의 `run_legacy_reference(LegacyPsiRequest)`를 명시적으로 호출한다.
기존 Baseline·Recommended PSI, 전략 Registry, Platform Claim/API에는 연결하지 않는다.

- 입력은 frozen DTO와 tuple이다. 실행마다 새 로컬 상태를 만들고 frozen 결과 tuple을 반환한다.
- 별도 Run ID·Snapshot ID·Hash나 자동 게시 권한은 만들지 않는다. 기존 증적 Reference·Hash를 재사용한다.
- 이것은 진단용 계산 경로다. FSN·공통 Guard를 우회해 운영 발주를 내는 새 전략이 아니다.
- 기존 Python의 고객주문/Forecast 구분, 절대 발주배수, 다음 주차 도착 처리 등은 그대로 유지한다.

## 2. 재현한 원문 동작

| 대상 | 별도 Python 기준에서 보존한 동작 |
| --- | --- |
| 준비 | 수요·입고·정책의 입력 순서를 보존하고 각 주차의 첫 일치 행 선택. 임의 합산·정렬하지 않음 |
| 월별 정책 | 명시적 BaseMonth, 누락 시 시작일 월의 1일. 정책 종료일 포함 |
| 재고 흐름 | 입고 → 과거 BO → 당주 단일 Demand → 기말 발주 판단 |
| Trigger | `EOH + 누적 PO - 누적 BO < ROP`, 일치 시 발주하지 않음 |
| 수량 | OUTL까지 보충. OUTL/ROPQ/MINQ/SFST의 공통 발주식과 서로 다른 ExpMOS 식 |
| Lot | Min/Max 기준점과 증가분. Max는 물리 상한이 아니며 음수 필요량은 Min 반환 |
| 납기 | `int(내부 LT / 28800)`, 납기를 포함하는 첫 Bucket. 계산된 과거/현재 Bucket의 DueIn도 원문처럼 변경 |
| LT 0 | 수요 처리 뒤 보충·BO 재충족. 당주 DemandAllocation을 다시 늘리지 않는 원문 동작 보존 |
| 소수 LT | 원본 LT가 0인지와 환산 일수가 0인지를 구분. 소수 일수는 버림 |
| 기간 밖 주문 | PO·미도착량은 남기되 대응 DueIn Bucket은 없음 |
| 수요·지표 | 단일 Demand 부족은 BO 이월. 주차 DemandAllocationRate와 ExpMOS의 원문 반올림 |
| 누락 행 | SetBucket의 기본 0을 재현하되 운영 Source 누락을 0으로 승인하는 계약으로 사용하지 않음 |

독립 호출·후보 사이에 재고·정책·입고 상태가 섞이지 않는지도 검증했다.

## 3. 숫자 처리

- 입력 문자열을 테스트 Adapter에서 binary64 `float`로 변환한다. 기존 개선형의 `Decimal` 계산은 바꾸지 않는다.
- `legacy_numbers.dotnet_round`는 .NET의 배율 곱셈 → 중간값 짝수 반올림 → 배율 나눗셈을 재현한다.
  사례 `2.675`의 소수 둘째 자리 결과는 C#/재현 Python `2.68`, 기본 Python `round`는 `2.67`이다.
- Lot의 비율은 .NET double→decimal 변환의 15자리·최대 scale 28 과정을 거친 뒤 올림한다.
  `10.000000000000002`는 이 변환을 거치면 `10`이며, 단순 `math.ceil`의 `11`과 다르다.
- Python 전역 Decimal Context와 무관하다. 음수 0의 부호도 대조한다.
- 참고한 .NET 공개 구현은 v8.0.0의 `Math.Round`, `VarDecFromR8`다. 실제 대조 Runtime은 .NET 8.0.32다.
  공개 구현을 읽었다는 사실만으로 다른 버전·다른 CPU의 동등성을 주장하지 않는다.

## 4. 실제 C# 대조와 증적

- 기존 [C# 캡처](../../tests/fixtures/native_legacy_reference_output.json) 중 PSI/발주·전이·Lot·납기·월별 정책·Trigger **27회 계산**을 대조했다.
  선정 적격 2건과 비용 계약 미확인 사례는 이 Python PSI 구현의 대조 대상이 아니다.
- 추가 [입력 벡터](../../tests/fixtures/legacy_replay_vectors.json)는 기대값이 없는 개발 시험 입력이다.
  PSI **27건**, 반올림 **16건**, double→decimal 올림 **14건**, Lot **8건**, 총 **65건**을 원문 C# 및 .NET Primitive로 실행했다.
- [추가 실제 출력](../../tests/fixtures/native_legacy_replay_vectors_output.json)을 별도 보존했다.
  새 격리 컨테이너의 반복 출력도 같았고, 새 Harness에서 기존 Golden을 실행한 결과도 이전 캡처와 같았다.
- 합계 **92회 계산 대조**에서 숫자는 C# round-trip 문자열을 float로 읽은 뒤 `float.hex()`로 비교했다.
  허용 오차를 사용하지 않았고 날짜·순서·행/사례 건수도 확인했다. 불일치 0건이다.
- 신규 Unit·Offline 통합 **54 passed**, 기존 계산·분류·정책·발주 계획 포함 대상 회귀
  **425 passed / 124 subtests passed**(21.48초), 전체 Python 385파일의 Ruff·Format 통과다.
  전체 회귀는 재실행하지 않았고 Coverage 모듈이 없어 비율은 측정하지 않았다. 이 시간은 Benchmark가 아니다.
- 중복 첫 일치, 중첩 정책·Calendar, 월/연도 경계, 누적 BO/PO, 음수 누적 PO,
  ROP 경계, 소수 수량·LT, 기간 밖 주문과 반복 호출 상태 분리를 포함한다.
- [추출 근거](evidence/legacy-python-reference-extraction-20261021.json)와
  [검증 기록](evidence/legacy-python-reference-verification-20261021.json)에 원본/입출력 지문과 실행 범위를 남긴다.

원본 7파일·21개 추출 구간은 바꾸지 않았다. 원본 Main·라이선스·DB 전후처리를 실행하거나 우회하지 않았다.
공식 SDK 8.0.426 / .NET 8.0.32 / Linux Arm64의 네트워크 없는 읽기 전용 컨테이너를 사용했다.
CS8632·읽기 전용 SDK 안내는 있었으나 빌드·실행은 성공했다. Mac 전역 SDK나 새 Python 의존성은 설치하지 않았다.

## 5. 입력 계약과 미검증 범위

- 기준 입력은 시각이 없는 `date` Calendar와 유한한 비음수 수량·정책·초기 BO/PO다.
  시각이 있는 DateTime, NaN/Infinity, 0 이하 Lot 증가량, Min>Max 등은 명시적으로 거부한다.
  이는 비교 구현의 지원 범위이며 Legacy 오류·예외를 전부 똑같이 이식했다는 뜻이 아니다.
- Calendar/정책이 중첩되면 원문 순서의 첫 일치를 보존한다. 운영 입력의 품질 승인으로 해석하지 않는다.
- 환산 LT의 Int32 범위, Python 날짜 범위, decimal 변환 범위 초과와 비유한 계산 결과는 실패한다.
- Source SQL→Canonical 전체 변환, `FN_GET_SEC_TIME_UOM` upstream 변환, 실제 PO 통계,
  경제적 EOQ/RMOS/EMOS 생성, `SVC_LV` 집계, 전체 최종 Winner·그룹→품목 전개는 미검증이다.
- 모든 binary64 값·운영 Plan·장기 확률 경로·배포 Legacy 바이너리의 전체 동등성을 입증한 것은 아니다.
  테스트 전체 소요시간을 C# 대비 계산 성능으로 인용하지 않는다.

## 6. 재검증 명령

저장소 루트의 기존 Python 테스트 환경에서 실행한다. 일반 회귀는 Docker 없이 실제 캡처와 다시 비교한다.

```sh
PYTHONPATH=.:src:tests/support python tests/reference/legacy_csharp/verify_python_replay.py
PYTHONPATH=.:src:tests/support python -m pytest -q tests/unit/test_legacy_reference.py tests/integration/test_legacy_reference_offline.py
```

## 7. 다음 작업 순서

**현재 기준선 — 제한된 C# 재현과 기존 개선형 보존 · InventoryEngine**

- 위 계산 구간의 기준 구현과 실제 출력 대조는 완료했다. 별도 성능 측정도 끝났고 최적화 후 개선량은 미측정이다.

**현재 기준선 — 비교용 성능 입력·측정 구간 고정과 실측 완료 · InventoryEngine Offline**

- [성능 비교 계획](IO_LEGACY_COMPATIBLE_PERFORMANCE_PLAN.md)에 따라 입력 규모·출력 책임·Release·동등 자원 조건을 고정한다.
  동등성 검사를 통과한 입력을 측정했고 결과 전체 136개 파일도 일치했다. 계산 최적화는 하지 않았다.

**1. 비용 분해 후 적용 범위 선택 — 다음/후속 직렬 작업 · InventoryEngine**

- 프로파일로 비용을 확인한 뒤 적용할 최적화를 고른다. 구현 시 매 단계 같은 결과를 유지하는지 확인한다.
  근거 없는 속도 개선 배수를 적지 않는다.
- 전체 서비스·선정 Source 조사는 독립 진행 가능하며, 전체 엔진 동등성/성능 주장에는 그 확인이 선행한다.
  운영 연결·DB Write·공용 배포·실제 발주는 이 작업의 승인 범위에 포함하지 않는다.

## 공식 숫자 처리 참고

- [.NET Math 구현](https://github.com/dotnet/runtime/blob/v8.0.0/src/libraries/System.Private.CoreLib/src/System/Math.cs)
- [.NET Decimal 변환 구현](https://github.com/dotnet/runtime/blob/v8.0.0/src/libraries/System.Private.CoreLib/src/System/Decimal.DecCalc.cs)
