# 정책 성과 기반 ML Segmentation 소규모 Offline 실험

기준일: 2026-10-15. 대상: InventoryEngine `inventory_engine_dev`.
개발 합성 Fixture에서 ABC·XYZ 후보, ML Profile 후보, 넓은 후보 탐색을 비교한다.
운영 Segmentation 계약 교체, 실제 V100 Source Backtest, 정책 승인 또는 성능 개선의 보장이 아니다.

## 1. 구현 경계

- `training/policy_segmentation.py`: 외부 학습 라이브러리 없이 학습하는 작은 비용 민감 의사결정나무다.
  불충족 건수를 먼저 줄이고, 다음으로 서비스 충족 Profile 대비 정규화된 비용 손실을 줄인다.
  그룹은 `SMALL/MEDIUM/LARGE` 후보 Profile이며 최종 발주정책이나 발주량이 아니다.
- `evaluate_replenishment/policy_segmentation.py`: 기존 `RunPolicySearchUseCase`, 공통 Guard·PSI,
  `RunPolicySelectionUseCase`를 실제 실행하는 평가 Runner다. 합성 데이터 생성 영역은 실제 계산기를
  import하지 않는 기존 경계를 유지한다.
- `scripts/verify_policy_segmentation.py`: 명시적인 테스트 Fixture를 실행하는 Repository 전용 Harness다.
  기존 PSR/PSEL Append-only Writer·Reader로 중간 PSI·행동·선정 결과를 보존하고 재검증한다.
- 기존 Config Schema, 분류 Snapshot, FSN Parameter·Operational 모드, Effective Policy V2,
  Platform Claim·게시·발주 Gate는 수정하지 않는다. 기본 Runtime·HTTP Route·DB 연결도 추가하지 않는다.
- Profile 선택을 기존 개발용 `item_overrides`에 메모리 조립하는 호환 실험이다. 신규 Config/API,
  독립적인 정책 그룹 Snapshot, 모델 Registry와 운영 Model Approval을 구현한 것은 아니다.
  미래 계약에서는 이 실험의 후보 설정을 기존 운영 Effective Policy와 구분해야 한다.

## 2. 미리 고정한 자료·후보·비용 조건

| 항목 | 개발 실험 조건 |
|---|---|
| 자료 성격 | 독립·비반복 합성 Window. 실제 ERP 또는 하나의 연속된 운영 이력 Backtest가 아님 |
| TRAIN | 2023-01-02, 04-03, 07-03 시작, 각 13주. 4개 수요 패턴, 총 12 품목-기간 |
| VALIDATION | 2023-10-02 시작 13주, 4 품목-기간 |
| HOLDOUT | 2024-01-01, 04-01 시작, 각 13주. 일반 품목 4개와 예외 품목 2개씩, 총 12 품목-기간 |
| 과거 자료 | 정책 계산·ML 특성 26주, 기존 기본 분류·FSN Fixture는 52주 |
| 수요 패턴 | 안정 소량, 안정 대량, 간헐, 고변동. 기간별 고정 Seed로 독립 생성 |
| 계획 Forecast | 과거 평균을 올림한 주간 수요. 평가 실적에서 만들지 않음 |
| 시작재고 | 과거 평균과 물리 Lead Time으로 만든 개발 합성 초기값. 52주 BOH Warm-up의 운영 검증 아님 |
| 공급·제약 | 동일 시작재고·확정 입고·Lead Time·MOQ 5·발주배수 5·Calendar를 세 방식에 공유 |
| 정책 후보 | `ROP_FIXED_Q`, `PERIODIC_ORDER_UP_TO`; RMOS 0, CSL 0.95, 점검주기 1주 고정 |
| SMALL | EMOS 0.15, 0.35 → 정책 종류 포함 품목당 4개 |
| MEDIUM | EMOS 0.75, 1 → 품목당 4개 |
| LARGE | EMOS 1.5, 2 → 품목당 4개 |
| 넓은 참조 집합 | 위 3 Profile의 정확한 합집합, 품목당 12개. 전역 최적 또는 무제한 탐색 아님 |
| ABC·XYZ 기준선 | AX/BX/CX→SMALL, AY/BY/CY→MEDIUM, AZ/BZ/CZ→LARGE인 고정 개발 가정 |
| 비용 | USD, EA·주당 보관비 1, BO 비용 10, 품목별 주문 고정비는 기간별 5/50/100/20/10/80 |
| 발주 변동비 | 명시적 개발 가정 0. 미확인 원가를 0으로 대체한 것이 아님 |
| 서비스 자격 | 최소 수량 충족률 0.90 + 종료 Backorder 0. 명목 CSL 0.95와 다른 지표 |
| 종료 조건 | 기존 유한 Horizon·종료 잔존가치 없음·Horizon 밖 입고 주문 거부. 종료 편향을 제거한 실험 아님 |

Fixture가 완전한 수요·무이동 근거를 제공해 기존 Validator를 검사하더라도 실제 DB 마감이나
운영 Source Authority를 확보했다는 뜻은 아니다. 비용도 실제 원가 또는 Legacy EOQ의 재현값이 아니다.

## 3. 학습과 Holdout을 분리한다

1. TRAIN의 넓은 후보를 당시 Forecast로 Simulation한다.
2. 각 Profile에서 Forecast 기준 후보 하나를 선택한 뒤, 별도 합성 실적 경로에서 고정 정책을 평가한다.
   실제 후보 ID의 동점 규칙까지 ML Profile의 개발 후보 예외 방식과 맞춘다.
3. 모든 Profile이 실적 서비스 조건을 만족하지 못한 품목-기간은 학습에서 제외하되 사유를 보존한다.
4. TRAIN만으로 깊이 0/1/2의 트리를 학습한다. VALIDATION으로 깊이를 고르고 동점이면 얕은 모델을 선택한다.
5. 모델을 고정한 뒤 HOLDOUT에서 ABC·XYZ, ML, 넓은 참조의 계획 탐색·선택을 독립 실행한다.
6. 세 방식의 선택이 모두 고정된 이후에만 HOLDOUT 실적 Replay Bank를 계산한다.
   계획 Forecast를 별도 실적 수량으로 교체한 평가용 PSI이며, 실제 주문 실행이나 신규 Forecast Source가 아니다.
7. 선택 정책을 Replay Bank의 같은 정책 모수에 연결한다. 실적을 보고 다른 정책을 재선정하지 않는다.
   전체 실적 후보의 사후 최선은 진단용 Reference로만 사용한다.

특성은 과거 평균·CV²·수요 발생 비율, 물리 Lead Time·MOQ, 초기재고 커버,
사전에 알려진 발주비/보관비 가정, ABC·XYZ One-hot이다. 미래 실적, 품목 ID, 선택 결과는 특성이 아니다.
원본 Canonical·Prepared Hash는 같은 기간의 세 방식에서 동일해야 한다. 후보 설정이 달라 생기는
Config·분류 Header Hash는 별도 보존하고, 실제 운영 의미가 같은 Gate Projection은 완전히 대조한다.

## 4. 지표와 측정 방법

- 서비스: 실적 PSI 수량 충족률·종료 BO·서비스 자격 충족 건수·미선정 건수를 함께 기록한다.
  무수요 품목의 충족률은 `null`이며 100%로 채우지 않는다.
- 비용·재고: 전체 기술 통계는 미선정 Baseline을 포함한다. 낮은 비용을 개선으로 주장하지 않도록
  ABC와 ML 모두 실적 서비스 자격을 충족한 동일 품목-기간의 비용·평균재고를 별도 비교한다.
  제외한 Pair 수를 반드시 표시한다.
- 좋은 후보: 넓은 집합에서 계획 서비스 조건을 만족하는 최소비용 후보의 집합이다.
  `range_source`에 따라 달라지는 Candidate ID 대신 정책 종류·RMOS·EMOS·CSL·점검주기를 비교한다.
  최저비용 후보 포함 여부·Recall과, 실적의 사후 최선 대비 비용 Regret·실현 가능한 기회를 놓친 건수를 보존한다.
- 비용 Regret는 선택 정책도 실적 서비스 자격을 충족한 경우에만 계산한다. 더 싼 결품 정책을 개선으로 세지 않는다.
- 시간: Profile 추론·설정/분류 조립·실제 계획 탐색·선택을 3번 반복한다. 실행 순서를 회전하고
  반복 간 결과 Hash 일치를 검증한다. 기간별 중앙값을 합산한다. Warm-up 없는 작은 CPU 측정이며
  통계적 속도 개선이나 운영 처리량을 입증한 것이 아니다.
- 학습·Validation 시간, 공통 실적 Replay 시간, Artifact 저장·재읽기 시간은 계획 탐색 시간과 분리한다.
  평가 Bank를 재사용하는 시간을 ML의 탐색 속도 개선으로 계산하지 않는다.
- `semantic_content_hash`는 수치·정책·모델·입력·근거를 봉인하며 재현 불가능한 wall-clock 측정값은 제외한다.

## 5. 재현과 증적

Repository의 개발 테스트 의존성이 있는 Python으로 실행한다. 새로운 학습 라이브러리는 필요하지 않다.

```bash
PYTHONPATH=.:src:tests:tests/support:tests/unit python scripts/verify_policy_segmentation.py \
  --artifact-root /absolute/existing/experiment-directory \
  --report /absolute/existing/experiment-directory/new-report.json
```

- `--artifact-root`는 존재하는 일반 Directory여야 한다. 생성한 입력 원문 Archive와 PSR/PSEL을 보존한다.
- PSR/PSEL은 기존 Append-only 저장·Hash 검사·선택 재실행 Verifier로 재읽는다.
- 기존 결과 파일은 덮어쓰지 않는다. 원 Source·Config·Gate·FSN 변경과 DB 연결 없이 실행한다.
- 원래 개인 보고서·환경 설정·임시 출력은 Commit 대상이 아니다. 공용 Runtime·DB·MR 변경도 없다.

## 6. 결과와 다음 판단

**이 실험에서는 ML 우월성이 확인되지 않았다. 운영 승격·기존 기준선 대체를 하지 않는다.**

독립 실행 결과(다른 테스트와 동시 실행하지 않음)는 다음과 같다.

| 지표 | ABC·XYZ | ML Profile | 넓은 후보 참조 |
|---|---:|---:|---:|
| 계획 탐색 후보/1회 | 40 | 40 | 120 |
| 수요 가중 실적 충족률 | 91.71% | 89.63% | 88.34% |
| 일반 평가대상 8 품목-기간 중 실적 서비스 자격 충족 | 5 | 4 | 3 |
| 계획 기준 좋은 후보를 모두 놓친 건수/비교 가능 8건 | 5 | 4 | 0 |
| 실적상 서비스 자격 충족 가능 후보가 있지만 놓친 건수 | 2 | 3 | 4 |
| 전체 12 품목-기간 기술 통계 비용(개발 USD 가정) | 4,877 | 5,491 | 4,188 |
| 전체 12 품목-기간의 평균 On-hand EOH(EA) | 22.54 | 24.94 | 16.59 |
| 두 기간의 계획 탐색 중앙값 합계(초, 3회 반복) | 0.613 | 0.611 | 1.757 |

전체 12건에는 과거 수요 0/미래 수요 0 예외가 각 2건 포함된다. 기술 통계 비용·재고는 미선정
Baseline도 포함하므로 서비스 미달을 무시하고 낮은 비용을 우수하다고 판단하지 않는다.
넓은 탐색도 Forecast에서 선택한 정책을 고정하므로 실적의 사후 최선과 다르다. 더 낮은 계획 비용을
찾았다는 사실이 미래 실적의 높은 서비스수준을 보장하지 않는다.

ABC와 ML 모두 실적 서비스 조건을 만족한 공통 **3건**에서는 비용이 ABC 1,168 → ML 1,577로
**35.02% 증가**했다. 같은 3건의 품목별 평균재고 합도 62.15 → 105.92 EA로 증가했다.
나머지 9건은 이 Pair 비용 비교에서 제외한 사실을 보존한다. 전체 서비스 결과까지 함께 보면
좋은 후보의 누락 5→4건 개선만으로 ML을 더 낫다고 평가할 수 없다.

TRAIN 12건 중 9건, VALIDATION 4건 중 3건을 학습·검증 손실 계산에 사용했다. 제외한 4건은
어떤 Profile도 실적 서비스 조건을 만족하지 못했고 사유를 보존했다. 깊이 1 트리가 선택됐으며
발주 고정비/보관비 비율 27.5를 경계로 SMALL/LARGE를 추천했다. 표본이 작고 단순한 비용 가정의
영향이 크므로 일반적인 품목 Segmentation 성능을 입증하지 않는다. 학습·Validation 및 기준 자료
생성은 5.58초였으며 이는 계획 탐색 속도 수치에 포함하지 않았다.

### 검증과 재현 기준선

- 신규 단위·Offline 통합: **19 passed**. 실적 2배 변경 시 모델·계획 선택 불변, 시점·기간 중복·
  미래 특성·모델 변조·미확인 비용·재봉인한 제약 변조·Operational FSN 미검증 차단을 검증했다.
- 전체 Unit·Contract·Offline Integration: **874 passed / 34 skipped / 655 subtests passed**,
  기존 Starlette 경고 1건. 선택 의존성 등의 Skip을 포함한 현재 검증 환경 결과이며 이전 환경의
  총 건수와 차이로 기능 증감을 주장하지 않는다.
- Ruff·Format·`git diff --check`, 기존 JSON Schema **36개** 구문 검증을 통과했다.
- 기존 합성 데이터 생성 독립성 테스트를 유지하기 위해 실제 계산기를 import하는 Runner를
  `training`이 아니라 `evaluate_replenishment`에 배치했다. 기존 테스트나 계산기는 수정하지 않았다.
- 입력 Archive 1개와 검색/선택 Artifact **16쌍**을 저장·재읽기 검증했다. 동일 ID/Hash 저장은
  기존 Append-only 멱등 동작을 사용한다. 입력·결과는 개발 Fixture이며 운영 승인 증적이 아니다.
- 의미 결과 Hash: `b8b8d7423dafdbcf717da2919820e863dedb570f3ad7c1561bd10c9107332d80`
- 모델 Hash: `0e54f17d8f6c7c71301c1985198407cbdbbf05cbfaf285ee80f5741194ebf0b4`
- 입력 Archive Hash: `c03c04232597d300e962d35776ab26c4fa90de2fedac35b669969d82a9b01d2a`
- 로컬 재현 결과: `/private/tmp/inventory-policy-segmentation-20261015.WJ5ugz/report-final.json`.
  같은 디렉터리의 XML과 PSR/PSEL·입력 원문은 임시 개발 증적이며 Commit 대상이 아니다.
  앞선 동시 실행 시험의 `report.json`은 보존하되 최종 시간 측정 근거로 사용하지 않는다.

### 후속 판단

다음 판단은 서비스 손실, 비용·재고, 좋은 후보 누락과 학습 유지 비용을 함께 보고 내린다.
독립 연속 이력·다중 Seed·다른 비용/수요 구간은 후속 검증이며 현재 실험으로 대체하지 않는다.
실험 실패를 이유로 기존 FSN·Source 제약·Claim Gate를 완화하거나 Holdout으로 모델을 재조정하지 않는다.
평균 Forecast·짧은 종료 조건·고정 비용 가정에 따른 서비스 손실 원인을 먼저 분해한다.
개선 실험을 하더라도 이번 Holdout은 최종 재평가용으로 재사용하지 않고 새로운 미사용 구간을 확보한다.
Config/API/UI·Runtime의 필수 ML 경로 확대와 DB 적용은 이 실험으로 승인·완료되지 않는다.
