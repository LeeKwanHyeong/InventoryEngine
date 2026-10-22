# C# 재현 기준과 Python 개선 유지·수작업 Golden 계약

기준일: 2026-10-21 · InventoryEngine `inventory_engine_dev` · HEAD `99f2f12`와 기존 미커밋.

**완료:** DB 없는 C# 원문 추출 실행·20개 대응 사례/29회 계산과 동일 범위 Python 비교. Golden 불일치 0건.
**미완료:** 원본 전체 엔진·운영 배포본 확인, 서비스 집계·전체 Optimizer/품목 전개·전체 Legacy 수치 동등성.
기존 개선형 계산 코드·기존 ABC/XYZ 결과/Hash·FSN 설정·운영 Gate·DB·Runtime은 변경하지 않는다.
이후 [별도 Python 재현 기준](IO_LEGACY_PYTHON_REFERENCE_CONTRACT.md)에서 기존 원문 계산 27회와 추가 65회를 대조했다.
원래 수작업 Golden과 실제 C# 캡처는 덮어쓰지 않았다. 새 재현 함수는 진단 전용이며 성능 비교는 아직 하지 않았다.

### 실제 C# 실행으로 확인한 현재 기준선

- [테스트 전용 참조기](../../tests/reference/legacy_csharp/README.md)는 원본 7파일·21개 구간을 지문 확인 후 그대로 추출한다.
  계산식을 새로 C#으로 작성한 대체 모델이 아니다. 원문 계산문·DTO는 줄바꿈 정규화 외에 바꾸지 않았다.
- 공식 SDK 8.0.426 / .NET 8.0.32 / Linux Arm64, 네트워크 없는 읽기 전용 컨테이너에서 실제 실행했다.
  새 컨테이너 반복 실행의 결과 JSON도 바이트가 같았다. Mac 전역 SDK·패키지·운영 Runtime은 설치/변경하지 않았다.
- [실제 C# 출력](../../tests/fixtures/native_legacy_reference_output.json),
  [추출 근거](evidence/legacy-native-extraction-manifest-20261021.json),
  [Python 나란히 비교](evidence/legacy-native-python-comparison-20261021.json),
  [실행·검증 기록](evidence/legacy-native-execution-verification-20261021.json)을 보존한다.
- 수작업 Golden의 출처·내용은 바꾸지 않았다. **그중 C# 대응 20건의 기대값이 실제 실행으로 검증됐다.**
  비용 미확인 1건은 Legacy 비용 계약이 없어 비교 불가이며 Python 검증만 수행했다.
- 공통 필드 일치 6건·문서화한 의미 차이 12건·선정 적격 구성요소 비교 2건이다.
  6건 일치는 해당 비교 필드에 한정하며 전체 PSI·정책 의미가 모두 같다는 뜻이 아니다.

## 1. 이번에 확정하는 것

[원본 대사](IO_LEGACY_CSHARP_PYTHON_RECONCILIATION.md)의 권장 분리를 **Offline 비교 계약**으로 고정한다.
선택지는 C# 동작으로 목표 Python을 즉시 덮어쓰기, 또는 원본 기대값과 현재 Python을 구분하는 것이다.
전자는 기존 수요·제약·Gate 계약을 바꾸므로 채택하지 않는다. 후자를 채택해 차이를 측정하되 운영 기본 계산은 그대로 둔다.

- **C# 재현 기준:** 동결한 원본 메서드가 같은 명시적 입력에서 만들어야 할 결과를 비교용으로 보존한다.
  원본 진입점·DB 전후처리·단일 품목 테스트 필터·라이선스 처리를 실행하거나 우회하지 않는다.
- **Python 개선 유지:** 이미 구현·합의한 입력 검증, 수요 구분, 제약, 미선정·REVIEW/BLOCK 규칙을 유지한다.
  ‘개선’은 이 안전 계약을 유지한다는 의미이지 모든 비용·서비스 성과가 C#보다 높다고 입증했다는 뜻이 아니다.
- **동작 변경 금지:** 이번 계약을 근거로 기말 판단·Forecast BO 이월·새 정책 코드·자동 게시 권한을 활성화하지 않는다.
- **분류와 계산 분리:** ABC/XYZ·LEGACY_STYLE은 후보 묶음/범위다. 어느 Adapter를 사용해도 Simulator의 계산 의미는 별도로 명시해야 한다.

이름은 문서의 비교 구분일 뿐 새 Runtime 모드나 API 필드가 아니다. 기존 Run·Snapshot·Config·정책·후보·결과 Reference와 Hash를 재사용한다.
실제 계산을 바꾸는 후속 구현에서는 기존 Simulator/Config Version과 입력 Binding의 변경 범위를 먼저 정한다.
과거 결과를 수정하거나 바뀐 결과에 예전 Hash를 강제로 붙이지 않는다.

## 2. 규칙별 재현과 개선 경계

| 규칙 | 원본대로 보존할 비교 동작 | 현재 Python에서 유지할 동작 |
|---|---|---|
| 재고 흐름 | 전주 EOH→당주 BOH, 입고→과거 BO→당주 Demand | 같은 수량 보존, 고객주문·순 Forecast를 분리 |
| 발주 판단 | 당주 입고·수요 처리 **후** `EOH+미도착 PO−BO<ROP`이면 OUTL까지 | 주차 시작에 관측. 고정 Q는 `Position<=ROP`, 주기적 S는 `Position<S`와 점검일 조건 |
| 정책 코드 | OUTL/ROPQ/MINQ/SFST의 공통 발주식과 ExpMOS 분기 | 현재 두 정책 종류를 유지. ROPQ 이름을 고정 Q와 자동 매핑하지 않음 |
| 부족 수요 | 단일 Demand의 부족량을 누적 BO에 추가 | 실제 고객 BO만 이월, Forecast Shortage는 별도 증적 |
| LT=0 | 수요 처리 뒤 주문을 재고에 추가하고 BO 재충족. DemandAllocation은 다시 증가하지 않는 원문도 보존 | 주문 입고 후 당주 수요 처리. 각 충족·부족 지표 보존 |
| 월별 정책 | 명시된 BASE_MONTH의 첫 일치 행·포함 종료일. BASE_MONTH 누락 시 시작일 월의 1일 | 판단 주차 시작일, 시작 포함/종료 제외인 정확히 한 정책 |
| Lot | Min/Max 기준점 + 증가분. Max 초과 발주 가능 | MOQ+절대 배수, 승인 한도·물리 용량, 불가능하면 거부 |
| 납기 | `int(내부 LT/28800)`일을 Bucket 시작일에 더하고 납기를 포함한 Bucket에 배정 | 물리 `lead_time_days`, 납기 이상 첫 Bucket 시작일. SDE는 보호기간만 보정 |
| 기간 밖 입고 | 새 PO는 남고 Calendar에 DueIn이 없을 수 있음 | 기간 밖 권고 거부. 비교 시 이 차이를 종료 조건에 명시 |
| BOH·복수 행·품목 범위 | 첫 행·없으면 0·수요 Buffer 선택을 별도 재현 대상으로 기록 | 누락≠0, Canonical 총량/중복·Master Coverage·봉인·Cut-off·Watermark 검증 |
| 서비스·MOS | 원문의 주차 할당률·ExpMOS 반올림 식 | 실제 PSI의 현재 수요 수량 충족률·관측 평균재고 MOS·무수요 상태 |
| 후보 적격·선정 | 개별 조건의 엄격 부등호와 강제 **적격** 표시를 보존 | 서비스 하한 이상·종료 고객 BO=0·비용 존재. 탈락 사유·미선정 보존 |
| 안전 경계 | 이상 동작도 비교 결과로 드러내되 운영 승인하지 않음 | 미확약 공급 제외, Scope/Hash 검증, FSN·REVIEW/BLOCK·자동 게시/발주 차단 유지 |

재현 범위에 기록했다고 모든 경계에 Golden이나 실행기가 완성된 것은 아니다. 작은 Golden에 포함한 범위는 다음 절과 같다.
원본 첫 행 선택·잘못된 Lot 증가량·중첩 정책 같은 추가 경계는 별도 시험으로 남긴다.

## 3. 비교 입력과 판정 기준

- Fixture는 개발용 1품목·EA·3주다. W0 `2026-09-28`, 종료 `2026-10-18`이며 Calendar 주차는 `202640~202642`다.
  월 경계 시험만 `2026-08-31~09-06`을 별도로 쓴다. 실제 Plan 202512나 V100 DB 입력으로 표시하지 않는다.
- 주문 시험의 과거 이력은 W0 이전 13주, 매주 10EA·변동성 0, 월 환산은 `10×52/12`, 요청 RMOS=0·EMOS=0.3이다.
  Q=`ceil((10×52/12)×0.3)=13`. LT=7이면 ROP=10·S=23, LT=0이면 ROP=0·S=13이다.
  수량·기준일·제약을 맞춘 뒤 판단 시점/정책식의 차이를 비교한다. 넓은 후보 성과 실험은 이번 범위가 아니다.
- Legacy 내부 LT는 이 시험에서 **역으로 지정한 수치**다. 예를 들어 201600/28800=7이다.
  28800을 물리적인 하루의 초 수라고 단정하거나 `FN_GET_SEC_TIME_UOM`의 실제 변환을 확인한 것으로 표시하지 않는다.
- 기간 밖 주문은 Legacy 미도착 PO와 Python 거부를 모두 보존한다. 한쪽 주문만 지워서 결과를 같게 만들지 않는다.
- 이 작은 Golden의 정수 수량·날짜·플래그는 완전 일치로 확인한다. C#의 double 전반에 적용할 오차 기준은 아직 미확정이다.
  기존에 유보한 전체 회귀 합격 기준 P0-19를 이 문서로 확정하지 않는다.
- 결과는 **공통 불변식 / 의도한 차이 / 지표 직접 비교 불가 / 미확인**으로 구분한다.
  불일치를 일괄 허용오차로 흡수하거나 출처 없는 차이를 ‘개선’으로 분류하지 않는다.

## 4. 수작업 Golden 21개

원문은 [수작업 Fixture](../../tests/fixtures/golden_legacy_python_semantics.json)다.
값은 직접 적었으며 현재 Python 출력으로 Legacy 기대값을 생성·갱신하지 않는다.
`MANUAL_FROM_FROZEN_CSHARP_SOURCE`, `native_csharp_verified=false`를 유지한다.
후속 C# 실행 결과는 별도 검증 기록으로 연결한다. 이 수작업 Fixture의 출처를 실행 결과로 바꿔 쓰지 않는다.

| 범위 | 수 | 고정한 내용 |
|---|---:|---|
| 재고 전이 | 4 | 입고/과거 BO/당주 수요 순서, Forecast 부족 미이월, 고객 BO 이월, 무수요≠서비스 100% |
| 주차별 발주·재고 | 3 | 주기적 S·고정 Q·LT=0의 주문량/일자·입고·EOH·조정/거부 사유, Legacy 기간말 미도착 PO |
| Trigger 경계 | 1 | 같은 Position=ROP에서 Legacy `<`와 Python 고정 Q `<=` 구분 |
| Lot 경계 | 4 | 기준점 차이, 최소 미만, 최대 일치·초과. Legacy Max를 물리 용량으로 매핑하지 않음 |
| 납기 | 4 | LT=0/3/7/21일, 포함 Bucket과 다음 시작일, 기간 밖 도착 |
| 월별 정책 | 2 | 8/31 시작 주의 명시 9월 BaseMonth와 누락 시 8월 fallback |
| 적격·선정 경계 | 3 | 서비스 하한 일치, 전 후보 미달, 비용 미확인≠0비용 |

### 동일 ROP=10·S=23·LT=7일의 차이

BOH=10, 매주 Demand=10, 미도착 PO=BO=0, Lot=1인 C# 수작업 계산이다.

| 주차 | BOH | DueIn | EOH | 기말 신규 PO | 마감 미도착 PO |
|---|---:|---:|---:|---:|---:|
| W0 | 10 | 0 | 0 | 23 | 23 |
| W1 | 0 | 23 | 13 | 0 | 0 |
| W2 | 13 | 0 | 3 | 20 | 20 |

W2 주문 20개는 10/19 납기여서 현재 3주 Calendar에 도착하지 않는다.
Python 주기적 S는 기초 Position에서 판단해 W0=13개·W1=10개를 주문하고 EOH=0→3→3이다.
고정 Q는 W0=13개만 수락돼 EOH=0→3→0이다. 마지막 주 두 정책의 기간 밖 주문은 Guard가 거부한다.
따라서 첫 주문 23 대 13만 보고 오류라고 단정할 수 없으며, 판단 시점·정책·종료 처리가 모두 고정돼야 한다.

### LT=0에서는 재고뿐 아니라 할당률도 달라진다

ROP=0·S=13, BOH=10·매주 Demand=10인 C# 수작업 기대값은 EOH=0→13→3, 주문=0→23→0이다.
W1 수요 10이 먼저 BO가 됐다가 당주 주문으로 충족돼도 원문 DemandAllocation은 0으로 남는다.
Python은 주차 시작 주문=3→10→10, EOH=3→3→3으로 각 당주 수요를 충족한다.
이 표는 최초 수작업 기대값이며 이후 C# Harness에서도 같은 수치와 할당률을 확인했다.
실제 필드는 위의 원시 C# 출력에 보존한다. 종료재고만 같다고 서비스 지표까지 같다고 판단하지 않는다.

### 선정 Golden은 전체 Legacy 최적화 결과를 만들어내지 않는다

선정 3건은 **개별 적격 조건·Python 선정 구성요소**의 작은 시험이다.
Legacy 입력은 인위적인 비교 숫자이며 실제 DB `SVC_LV`의 단위·분모·환산을 증명하지 않는다.
서비스 하한 미달 시 `INDV_TARGET_FLAG_FORCE`는 강제 **적격**이지 최종 선택/발주 승인이 아니다.
Fixture의 Legacy 최종 Winner는 `null`이다. Segment 가중 최적화→품목 전개 결과를 임의로 채우지 않는다.

## 5. 검증 방식과 완료 조건

- [계약 테스트](../../tests/contract/test_legacy_python_semantics_contract.py)는 수작업 출처·21건 완전성·기존 근거 Register를 확인한다.
  native 검증 완료·새 운영 승인·임의 오차 허용·LEGACY_STYLE=PSI 재현으로 바꾼 Fixture를 거부한다.
- [단위 테스트](../../tests/unit/test_legacy_python_semantics.py)는 고정 기대값과 현재 PSI/Guard/정책 선택 구성요소를 대조한다.
  Legacy 부분은 장부 수량 보존을 확인할 뿐 C# 메서드를 실행하거나 Python 참조 엔진을 새로 만들지 않는다.
- [Offline 통합 테스트](../../tests/integration/test_legacy_python_semantics_offline.py)는 기존 분류/이력 Fixture에서 실제
  CandidateStrategy→Guard→PSI를 실행한다. 입력 불변·ROP/S·수량·발주/도착일·거부 사유·게시 차단을 확인한다.
- 기존 FSN REVIEW/BLOCK, 후보/선정·ABC/XYZ 고정 Hash와 선정 정책 실행 회귀를 함께 확인한다.
  실제 Snapshot 승인·Claim·DB 적재·Runtime 배포·ERP는 시험하지 않는다.
- **이번 완료 조건:** 수작업 기대값이 고정되고 현재 Python과의 알려진 차이가 재현되며 보호한 파일이 변하지 않는다.
  **C# 대응 범위 완료:** 별도 Harness의 실제 실행으로 20건을 확인했다. 비용 비교 불가 1건은 검증 완료로 승격하지 않는다.
  기대값을 Python 출력에 맞춰 자동 수정해 통과시키지 않는다.

### 2026-10-21 수작업 고정 시점의 검증 이력

- 신규 단위·계약·Offline 통합 **39 passed**(0.12초), 기존 보충·수학 정책·후보/선정·LEGACY_STYLE·분류 호환·
  선정 정책 실행을 포함한 대상 회귀 **362 passed / 124 subtests passed**(22.00초)다. 전체 회귀 재실행 수치는 아니다.
- 새 Python 테스트 3개의 Ruff·Format을 확인했다. 캐시 쓰기 권한 오류 이후 `--no-cache`로 재실행해 통과했다.
  전체 `src/tests/scripts`의 Ruff·Format도 376개 Python 파일 기준으로 통과했다.
  그 과정에서 기존 계산 코드를 고치거나 실행 환경의 패키지를 설치하지 않았다.
- 원본 ZIP 포함 기존 근거 19개 지문·원문 위치 45곳은 동일하다.
  정확한 검증 범위와 미실행 항목은 [검증 기록](evidence/legacy-replay-golden-verification-20261021.json)에 남긴다.
- C# 원문 전체 실행·.NET 설치·배포본·DB·Runtime·ERP, 전체 성능 실험·Coverage·독립 에이전트 검토는 수행하지 않았다.

## 6. 다음 순서와 남은 확인

**현재 기준선 — 완료 · InventoryEngine 계약·테스트**

- 재현/개선 경계와 21개 수작업 Golden을 고정하고 C# 대응 20건을 실제 실행으로 검증했다.
  Python 21건도 같은 경계 입력으로 다시 계산했다. 전체 원본 엔진·운영 배포본 동등성은 미검증이다.
- 계산 코드·기존 Hash·FSN·운영 Gate·DB는 유지하며 발주 계획 저장은 보류한다.

**DB 없는 C# 참조 실행 — 완료 · C# 원본·InventoryEngine 테스트**

- 원문 계산 구간·DTO를 추출해 실제 .NET으로 실행했고 현재 Python과 비교했다. 반복 실행과 변조/누락 차단도 확인했다.
- 전체 Main·DB 전후처리·라이선스 진입점을 실행하지 않았다. 완료한 참조기 준비를 다음 작업으로 반복하지 않는다.

**1. 확인된 차이의 목표 적용 방향을 정한다 — 다음 작업 · InventoryEngine 계약**

- 기말/기초 판단, 공통 `(s,S)`/기존 두 정책, 납기 Bucket·Lot 등 확인한 차이를 원본 재현과 목표 개선으로 나눠 적용 범위를 정한다.
- 현재 계산을 자동 변경하지 않는다. 완료 조건은 결과별 유지/변경·예상 차이가 명확하고 필요한 Version/Binding 범위가 닫히는 것이다.

**2. 서비스 집계·그룹에서 품목으로의 전개를 확인한다 — 병렬 가능 다음 작업 · Legacy Source·InventoryEngine 문서**

- `SVC_LV` 생성식·단위, Segment 공통 선택과 품목 개별 선택 모드, RMOS/EMOS 생성과 실제 LT 환산을 조사한다.
- 1번의 정책 적용 판단과 독립적으로 조사 가능하지만, 근거 없이 전체 Optimizer Golden을 만들지 않는다.

**3. 차이를 판정한 뒤 필요한 구현과 저장을 재개한다 — 후속 직렬 작업 · InventoryEngine → dsai-platform**

- 원본 결과·목표 개선을 대조해 필요한 계산 수정만 정하고 회귀 검증한다. 그 뒤 기존 Writer·Reference/Receipt로 저장·조회·복구를 연결한다.
- 독립 IO 검증 후 Platform 연결로 진행한다. **실제 Migration·DB Write·공용 Runtime 배포는 승인 필요**이며 지금 수행하지 않는다.
- 발주요청 확정·ERP·Demand 최종 연결·추가 자원 고도화·대규모 실험과 Commit/Push는 이번 범위가 아니다.
