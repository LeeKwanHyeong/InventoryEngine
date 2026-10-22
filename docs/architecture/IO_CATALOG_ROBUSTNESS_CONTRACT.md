# 간헐수요·장기 Lead Time의 강건성 비교 조건

기준일: 2026-10-19. 대상: InventoryEngine `inventory_engine_dev`, HEAD `99f2f12`와 보존한 미커밋 기준선.
**비교 조건·순수 평가기·개발 Source/관측 분리·품목별 실행·영속 저장/복구·조회/재계산 분리·로컬 자원 통제·전체 작업 목록/부분 자원 사전 검증 구현 / 전체 자원 합격·공식 입력 생성·실험 실행 미완료**다.
합성 개발 실험의 계약이며 운영 정책 승인, 실제 Source 검증, ML/PPO 학습·승격이 아니다.

기계 판독 사양은 [catalog-robustness-protocol-v1.json](data/catalog-robustness-protocol-v1.json)이다.
정규화 SHA-256은 `a3e35fcf23915e87823883ea8c1f09448b245ae7ea1fffa8c77482bab0433280`로 고정한다.
최종 수요를 생성하기 전에 구현/원 입력 Manifest와 함께 봉인하며, 조건 변경은 새 Revision·새 Holdout을 요구한다.
JSON의 `NOT_IMPLEMENTED`는 조건 확정 당시의 기록이다. 해당 원문과 Hash는 바꾸지 않고 구현 상태를 이 문서와
검증 기록에 추가한다. 순수 평가기 전체 회귀는 **1,300 passed / 9 skipped / 669 subtests passed**다.
[순수 평가기 검증 기록](evidence/catalog-robustness-pure-evaluator-20261019.json)을 참조한다.

## 1. 이번에 구분할 문제

[직전 비교](IO_CATALOG_HOLDOUT_COMPARISON.md)는 세 방법 모두 간헐·고가의 두 창에서 서비스 미달이었다.
88개 후보 안에는 실적의 적격 정책이 있었지만 일정 평균 Forecast에서 고른 정책은 이를 선택하지 못했다.
따라서 다음 두 효과를 따로 비교한다.

- **범위 효과:** 같은 선정 기준으로 ABC·XYZ 32개 / LEGACY_STYLE 32개 / 참조 88개를 비교한다.
- **선정 효과:** 같은 후보 범위 안에서 단일 Forecast 선정 / 다중 경로 위험 평가 선정을 비교한다.

| 대안 | 장점 | 한계·결정 |
|---|---|---|
| 단일 평균 Forecast의 서비스 통과 후 최소 비용 | 기존 방식과 가깝고 계산이 적음 | 평균 밖의 위험을 놓칠 수 있어 비교 대조군으로 유지 |
| 과거 정보로 만든 여러 경로의 실패 빈도·심한 부족을 검사 | 품목별 위험과 비용의 상충을 드러냄 | 계산·증적 증가를 감수하고 개발 Challenger로 비교 |
| 모든 Stress의 최악 결과를 무조건 만족 | 극단 상황의 방어가 강함 | 고정 후보 88개에서 전부 미선정되거나 과잉재고로 흐를 수 있어 주 선정 기준으로 채택하지 않음 |

이번 계약은 두 번째의 개발 비교 조건만 정한다. 기존 단일 경로 선택기·PSR/PSEL·Config/API 계약은 바꾸지 않는다.
현재 ABC 필수 Gate, FSN·VED·SDE·HML·PLC와 Source 제약·승인 Override 우선순위를 그대로 유지한다.
실제 V100/P12 분류의 FSN 차단이나 Claim 입장 조건을 해결한 것으로 표현하지 않는다.

## 2. 기간·품목·시작 상태

| 조건 | 고정값 |
|---|---|
| 가상 W0 A | 2037-01-05; 주 평가 종료 2038-01-03, 전체 종료 2038-04-04 |
| 가상 W0 B | 2044-01-04; 주 평가 종료 2045-01-01, 전체 종료 2045-04-02 |
| 주 평가 / 추가 관찰 | 52주 / 이후 13주, 총 65주 연속 계산 |
| 과거 자료 | W0 전 104주, 마지막 52주 분류·마감, 마지막 26주 수학 정책 통계 |
| 품목 1 | 안정수요, 물리 LT 49일, 개발 단가 2 USD/EA, 알려진 고객주문 매주 2EA |
| 품목 2·3 | 같은 간헐수요·단가 250 USD/EA, LT만 7일 / 49일, 알려진 고객주문 0EA |
| 제약 | EA 정수, MOQ 5·배수 5, 같은 Source·Calendar·수요 소진·공통 Guard |
| 범위 | 개발 Fixture DSE/C100/V101, 세 품목을 하나의 원 분류 모집단으로 유지 |

날짜는 이전 실험·기술 Smoke의 **이력까지 포함한 사용 기간**과 겹치지 않는 합성 Calendar다.
2038년 W0는 이전 2040 Smoke의 이력과 겹치므로 사용하지 않는다. 실제 미래 수요 예측을 뜻하지 않는다.
각 창의 이력과 미래를 모두 따로 생성하며 두 창 사이에 재고를 이어 붙이지 않는다.
반대로 한 창의 65주 계산은 끊지 않는다. 13/26주마다 BOH·미도착 주문을 초기화하면 무효다.

### 52주 Warm-up으로 시작재고와 입고예정을 함께 만든다

1. 실제 공식 입력 생성 단계에서 W0−104~W0−1의 검열되지 않은 합성 수요를 생성한다.
2. W0−78~W0−53의 26주 평균 μ·모표준편차 σ를 고정한다. 그 뒤 Warm-up 수요로 정책을 재조정하지 않는다.
3. Warm-up 시작 BOH는 `ceil(μ × (ceil(LT/7)+2))`, Reserved와 최초 고객 BO는 0이다.
4. W0−52부터 52주간 독립 재고 Generator로 계산한다. 고정 참조 정책은 주간 점검 ROP와 월평균 Q다.
   `L=ceil(LT/7)`, `SS=ceil(z(.95)×σ×√L)`, `ROP=ceil(μ×L+SS)`, `Q=ceil(μ×52/12)`를 사용한다.
   z값은 기존 참조처럼 소수 12자리 HALF_EVEN, 연산은 Decimal 정밀도 40이다.
   Position≤ROP이면 Q에 MOQ·배수 5를 적용해 주문하고 물리 LT로 입고한다. 공통 고객 우선 차감·예약 제외 의미를 지킨다.
5. W0−1 EOH·고객 BO·미도착 주문의 수량/납기를 그대로 W0 Source로 넘긴다. W0 도착분도 별도 Receipt로 남긴다.
   임의 BOH나 추가 ‘4주차 입고’를 붙이지 않는다. 과거 출고 부족으로 수요 이력을 줄이지 않는다.

참조 평균이 0이면 Warm-up은 HOLD하며 양의 수요·주문을 만들지 않는다. W0 정책 이력이 0/누락이면
기존 탐색 제외·Baseline·사유를 보존하고 다른 Seed를 뽑지 않는다.
Warm-up의 끝이 W0라는 이유로 그 이후 도착 주문을 거부하지 않는다. 별도 Generator의 연속 공급 상태로 보존하며,
이는 생산 PSI의 기간 밖 도착 Guard를 완화하는 변경이 아니다.

Warm-up은 어느 후보 방법도 우대하지 않는 공통 개발 Source 생성이다. 생산 전략을 import해 정답을 만드는 Generator는 허용하지 않는다.
Warm-up Ledger·발주/입고·W0 연결 Hash를 독립 수작업 사례로 먼저 검증해야 한다.
완전한 합성 마감은 테스트 계약의 입력일 뿐 실제 PostgreSQL의 VERIFIED 마감·승인 Snapshot이 아니다.

주어진 이력을 소비하는 `DevelopmentSourceProducer`와 수작업 검증은 구현했다.
이 모듈은 생산 전략·Guard·PSI 함수를 import하지 않으며, 난수나 공식 이력/수요 경로를 만들지 않는다.
현재 개발 Source는 주 단위 LT(0~49일 중 7일 배수)만 받는다. 기존 confirmed Receipt의 주차 매핑과
다른 날짜를 조용히 적용하지 않도록 중간 일수 LT는 거부하며, 공식 대상 LT 7/49일은 모두 지원한다.
Warm-up Q는 월평균 수요의 참조 수량이며 EOQ 최적값이 아니다.

## 3. 선정·검증·최종 경로를 분리한다

각 창·품목에서 초기재고·확정 공급·정책 통계·가격·분류는 고정하고 미래 경로만 바꾼다.
Seed와 Version은 JSON에 명시한다. 같은 간헐수요의 LT 7/49일 품목은 이력·미래 경로까지 공유해 LT 효과를 분리한다.
이 두 LT 결과를 서로 독립인 통계 표본 2개로 세지 않는다. 비용 Profile을 늘려도 수요 표본 수가 늘지는 않는다.

| 입력 | 경로 수 | 생성 정보·용도 |
|---|---:|---|
| Nominal Forecast | 1 | W0 이전 26주 평균 올림을 65주에 적용, 대조군 선정 |
| SELECTION | 8 | 과거 마지막 52주만으로 4주 Moving Block Bootstrap, 위험 기준의 후보 선정 |
| VALIDATION | 8 | 별도 Seed의 새 실현 수요, 선택을 바꾸지 않고 중간 검증 |
| HOLDOUT | 16 | 또 다른 Seed의 새 실현 수요, 모든 선택 Hash를 고정한 뒤 생성·평가 |
| 진단 Stress | 간헐 품목당 4 | 별도 새 경로에 무수요·초반 공백·중간/말기 급증을 적용, 주 합격률과 분리 |

Bootstrap은 길이 4의 비순환 블록 시작점 0~48을 독립 추출해 17개를 이어 붙이고 처음 65주를 쓴다.
미래 정답을 재표본하거나 품목의 생성 분포 라벨을 선정기에 넘기지 않는다.
평가용 Truth Generator는 안정수요 6~10EA, 간헐수요 발생 35%·발생 시 20~40EA로 고정한다.
Hash의 별도 역할 채널로 발생/수량을 추출한다. 35% 비교는 정수 `draw < floor(35×2^64/100)`이며 부동소수점 판정을 쓰지 않는다.
확정 고객주문은 사전 고정한 값만 사용하고, 실현 총수요는 알려진 주문과 생성 수량 중 큰 값이다.
현재 PSI의 같은 주차 Forecast 소진을 유지한다. Forecast 부족을 고객 BO로 자동 이월하지 않는다.

이력/선정/검증/Holdout/Stress의 Seed·채널을 분리하고 경로 원문·순서·Seed·Hash를 보존한다.
우연히 내용이 같은 경로도 삭제하고 다시 뽑지 않는다. 자연 무수요 경로도 마찬가지다.
반면 같은 Seed/Path ID를 다른 Split으로 재등록하면 중복 Source로 거부한다.

### 미래 정답은 전략 관측에 노출하지 않는다

현재 `run.py`는 Canonical 수요로 `future_demand` 관측을 만든다. 수학 후보가 이를 직접 사용하지 않는다는
사실만으로 앞으로도 안전하다고 가정하지 않는다. 새 경로는 **환경의 실현 수요 Binding과 전략의 계획 Forecast Binding**을 구분해야 한다.
관측에는 판단 시점의 재고·고객 BO·미도착 공급과 사전 고정 Forecast만 보이고, 아직 일어나지 않은 Truth 수량은 보이지 않아야 한다.
선정 후에도 상태 피드백으로 주문량은 달라질 수 있지만 정책 종류·RMOS·EMOS·CSL·검토주기는 고정한다.
미래 Suffix만 바꿨을 때 그 전의 제안·Guard 수량·납기는 같아야 한다. 관측 Hash 전체는 Source Binding 때문에 달라질 수 있다.
새 개발 경로 `RunCausalPsiUseCase`에 이 분리를 구현했다. 기존 `run.py`와 기존 결과 Hash는 보존한다.
새 경로는 V2 `LOCAL_SHADOW`·`MATHEMATICAL` 전용이다. 전략과 Guard는 고정 계획의 관측·Hash만 받고,
실현 수요의 Path ID·Seed·Hash·미래 수량은 받지 않는다. 행동·Guard 검증 후 당주 실현 수요만 공통 PSI에 전달한다.
확정 고객주문은 계획 입력에 고정하고 실현 총수요에서 한 번만 소진한다. 불확정 수요는 PSI 호환 필드
`net_forecast_qty`로 전달하되 결과의 `demand_role=REALIZED_ENVIRONMENT_DEMAND`와
`planned_gross_forecast_qty`로 실제 Forecast와 구분한다. 기존 Source/PSEL 결과로 위장하지 않는다.

미래 Suffix·Path ID·Seed·Hash를 변경해도 변경 주차의 차감 전까지 관측·제안·Guard가 동일한 것을
수작업 Probe와 실제 수학적 전략 모두에서 검증했다. 이미 관측한 수요로 다음 주 상태·행동이 달라지는 것은 허용한다.
이는 신뢰된 Python 전략 Adapter에 전달하는 데이터 경계다. 악성 Python 코드를 격리하는 보안 Sandbox는 아니다.
수작업 입력의 품목별 Catalog 실행 조립은 제12절에 추가했다. 공식 입력 조립·Runtime 연결은 미완료이며
PPO/ML로 비교 범위를 넓히지도 않는다.

## 4. 서비스 위험을 판정하는 닫힌 기준

품목·창·후보별로 따로 계산하며 다른 품목의 좋은 성과로 상쇄하지 않는다.

```text
경로 Fill = Σ(당주 고객주문 충족 + 순 Forecast 충족) / Σ(당주 고객주문 + 순 Forecast)
경로 성공 = 수요 존재 AND Fill≥0.95 AND 52주차 종료 고객 BO=0 AND 기존 계산 Gate 통과
경로 손실 = 1 − Fill

N = 수요가 있는 경로 수
경험적 성공비율 = 성공 경로 수 / N
K = ceil(0.10 × N)
나쁜 10% 평균 부족률 = 가장 큰 K개 경로 손실의 산술평균

위험 적격 = 최소 수요 경로 수 충족 AND 성공비율≥0.90 AND 나쁜 10% 평균 부족률≤0.10
```

선정/검증의 최소 수요 경로는 각각 6개, Holdout은 12개다. 부족하면 `INSUFFICIENT_DEMAND_PATHS`로 평가 불가다.
8개 모두 수요가 있다면 선정·검증은 8/8 성공이 필요하다. Holdout 16개는 최소 15/16 성공이 필요하지만,
한 경로의 심한 실패도 나쁜 10% 평균 부족률에서 별도로 걸러진다.

예를 들어 15경로 Fill=.99·1경로 Fill=.94이면 성공 15/16, 나쁜 2개 평균 부족률=.035다.
같은 15/16이라도 나머지 하나가 Fill=0이면 나쁜 2개 평균은 .505이므로 위험 기준에 미달한다.
P10 Fill은 오름차순 `ceil(.1×N)`번째 값으로 같이 표시한다. 모든 비율은 표시 반올림 전의 Decimal로 비교한다.
이산 표본의 올림 때문에 16경로에서는 나쁜 2개, 즉 12.5%를 평균한다. 정확히 10% 확률의 연속 분포를 추정했다고 주장하지 않는다.

- 무수요: 서비스 `null / NO_DEMAND_IN_HORIZON`, 성공비율 분모에서 제외한다. 다만 실제 보관비 등은 기대 비용에서 제외하지 않는다.
  무수요 경로에 종료 고객 BO가 남으면 단순 분모 제외로 숨기지 않고 후보를 위험 미적격으로 처리한다.
- 계산 실패: 실패 경로를 분모에서 빼지 않는다. 해당 후보 전체를 기술 실패로 미선정 처리하고 원인을 보존한다.
- 입력·Scope·Hash·Gate 변조/불일치: `INVALID_EXPERIMENT`로 중단하며 성과 실패와 구분한다.
- 적격 후보 없음: `NO_ROBUST_CANDIDATE`. 더 느슨한 위험 기준·Nominal·Legacy로 조용히 대체하지 않는다.
  미선정 Baseline은 설명용으로만 저장하며 선택 성공이나 비용 개선에 포함하지 않는다.
- 검증 실패: 이미 고른 정책과 실패를 보존한다. 다음 순위로 갈아타지 않고 Holdout도 고정 정책의 진단으로만 진행한다.

90%·95%·10%와 경로 수는 **개발 가정**이다. 16경로로 실제 운영의 90% 신뢰도나 통계적 우월성을 보장하지 않는다.
주차를 독립 표본 52개로 세지 않고 한 연속 경로를 표본 하나로 센다. 고객 BO=0만으로 Forecast 충족을 성공 처리하지 않는다.

## 5. 비용 가정과 선정 순서

주 비교는 `PRICE_BASE`로 사전 고정한다. 나머지는 민감도이며 Holdout을 본 뒤 유리한 Profile을 주 기준으로 승격하지 않는다.
`c`는 Source에 고정한 개발 단가 USD/EA다. 모든 비용은 실제 ERP 원가·이익률이 아닌 합성 가정이다.

| Profile | 보관비 / EA·주 | 고객 BO / EA·주 | 고정비 / 발주 | 변동비 / 발주 EA | 미충족 순 Forecast / EA |
|---|---:|---:|---:|---:|---:|
| FIXED_COMPATIBILITY | 1 | 10 | 25 | 2 | 0 |
| PRICE_BASE | c×20%÷52 | c | 25 | c | c |
| PRICE_HOLDING_HIGH | c×40%÷52 | c | 25 | c | c |
| PRICE_ORDER_HIGH | c×20%÷52 | c | 100 | c | c |
| PRICE_NO_FORECAST_PENALTY | c×20%÷52 | c | 25 | c | 0 |

각 민감도는 PRICE_BASE의 한 항만 바꾼다. FIXED_COMPATIBILITY는 이전 비용 구조 대조군이지 이전 결과의 재현 주장은 아니다.
미충족 순 Forecast 비용은 별도 가정 손실이다. 관측 매출총이익·고객 BO·확정 발주비로 바꾸어 부르지 않는다.
이 항이 0인 두 Profile도 ‘미확인 값을 0 대체’한 것이 아니라 명시적 가정이다. 가격/필수 비용이 누락되면 계산 불가다.

```text
평가 비용 = 보관비 × Σ52주 On-hand EOH
          + 고객 BO 단가 × Σ52주 마감 고객 BO
          + 고정 발주비 × 52주 안에 수락한 발주 건수
          + 변동 발주비 × 52주 안에 수락한 발주 수량
          + Forecast 부족 단가 × Σ52주 미충족 순 Forecast
```

52주 안에 발주하고 Tail에 도착하는 주문도 발주 시 비용을 센다. Tail 보관비나 미도착 재고가치를 주 비용에 더하지 않는다.
관세·Landed Cost·공동 Shipment·잔존가치·할인율을 추가하지 않는다. 평균재고의 수량과 단가 평가금액은 별도 표시한다.
Decimal 정밀도 40·HALF_EVEN으로 계산하고 순위 전 금액 반올림을 하지 않는다. 화면 표현만 소수 2자리다.

두 선정기 모두 같은 비용 Profile을 적용한다. Nominal은 한 Forecast의 서비스 조건 후 비용,
Risk는 선정용 8경로의 위험 적격 후 평균 비용으로 선정한다. 동점은 평균 EOH → 평균 주문 횟수 → 정책 의미 키로 해소한다.
의미 키는 `(정책종류 순서, 숫자 RMOS, 숫자 EMOS, 숫자 CSL, 검토주기)`며 종류 순서는 JSON에 고정한다.
출처에 따라 달라지는 Candidate ID로 두 실험군의 선택을 흔들지 않도록 새 실험의 두 선정기에만 같은 규칙을 적용한다.
**기존 선택기의 Candidate ID 동점 규칙이나 Hash는 변경하지 않는다.** 새 실험 결과를 기존 PSEL의 재현으로 위장하지 않는다.

Source PSI의 기존 `total_cost`를 수정하거나 새 비용에 더하지 않는다. 검증한 원 Row/주문을
별도 Scorecard에서 재가격화하고 Source/Window/Cost Profile Hash와 성분별 계산을 보존한다.
따라서 기존 선택기만 호출하면 이 실험이 자동 구현되는 것은 아니다. 별도 Offline Scorecard와 두 선정기는 구현했지만,
공식 Catalog 전체 검증과 결과를 평가기에 전달하는 연결부는 여전히 필요하다.

## 6. 13주 Tail·기간 말 진단

원 PSI는 65주 내내 같은 정책으로 발주한다. Tail은 주문 중지 후 소진하는 Drain-down이 아니다.
주 성과는 첫 52주만 다시 계산하고, 이후 13주의 서비스·비용과 52주 말 EOH/BO/미도착 수량·가치를 따로 기록한다.
최대 물리 LT 49일이므로 주 평가 마지막 발주의 도착이 65주 안에 들어올 수 있다. 기존 기간 밖 납기 거부·물리 LT를 바꾸지 않는다.

13/26/52주 Prefix 성과도 **동일한 65주 경로·이미 고른 정책**에서 산출한다. 기간마다 재선정하지 않는다.
추가로 각 창·품목의 첫 VALIDATION Seed에서 잠긴 정책만 52주/65주로 실행해 첫 52주의
제안·Guard·도착·수량·서비스·비용을 비교한다. 마지막 납기 거부 차이를 기간 말 효과로 분리한다.
65주 결과의 기본 `metrics`는 전체 65주 지표이므로 이를 52주 성과라고 그대로 사용하면 안 된다.
Row·결정일·주문일 기준 Window Reducer와 5종 비용 재계산은 순수 평가기에 구현했다.
Tail만으로 모든 종료 편향이 제거됐다고 주장하지 않는다.

## 7. 최종 비교·미선정·좋은 후보 누락

각 창×품목×PRICE_BASE 셀에서 범위 효과와 선정 효과를 따로 표시한다. 전체 합산 성공률은 설명용이다.
Risk 개선 근거는 어느 셀에서도 성공비율이 떨어지거나 나쁜 10% 부족률이 커지지 않으면서,
최소 한 셀에서 엄격한 서비스 위험 개선이 있는지로 구분한다. 적격 여부 자체도 함께 보여준다.
두 방법이 모두 Holdout 위험 적격인 동일 셀에서만 비용·재고 우열을 비교한다.
비용 +10% 초과는 개발 비용 상충 경고이며 자동 탈락·업무 승인·운영 제약이 아니다.
분모 비용 0이면 상대 변화는 `null`; 절대 차이와 사유를 남긴다.

좋은 후보 집합은 같은 선정기·같은 비용 Profile의 참조 88개 중 적격인 최소 비용/평균재고/주문 횟수 동점 집합이다.
마지막 의미 키 동점 해소는 집합 정의에서 제외한다. 좁은 범위의 포함 여부·재현율을 기록한다.
Holdout의 사후 좋은 후보·기회 누락·Regret는 모든 선택을 고정한 뒤 진단용으로만 계산한다.
참조에도 적격 후보가 없으면 `REFERENCE_NOT_FEASIBLE`, Regret `null`이며 좋은 후보 비교 분모에서 제외한다.
기술 실패/입력 오류, 무수요 평가 불가, 운영 Gate 차단은 성과 미달과 각각 구분한다.
Stress 네 종류는 주 분모에 섞지 않고, 실패하면 어느 Stress에 취약한지 표시한다. 모든 상황에 강건하다고 표현하지 않는다.

## 8. 실행·저장 경계와 선행 구현

현재 Catalog Holdout Runner는 13주 상한이고 실제 탐색 상한은 `품목 수×후보 수×H(H+1)/2≤200,000`이다.
65주·88개는 품목 하나에 **188,760**, 두 품목은 **377,520**이므로 통째로 실행할 수 없다.
새 전용 Runner에서만 품목별로 나눈다. 세 품목의 ABC/XYZ 원 분류는 먼저 한 번 계산·봉인하고 재분류하지 않는다.
각 실행 Projection은 원 분류 ID/Hash·실행 Item·Projection Hash를 묶는다. 원 Feature/Catalog는 부모 모집단 전체를
보존하고, 실제 선택한 Feature·후보 행과 Canonical/수학 입력의 Universe만 정확히 한 품목으로 대사한다.
부모 분류·원본을 잘라 같은 Hash로 위장하거나 품목마다 ABC 매출 순위를 다시 정하면 무효다.

Candidate Evidence는 최대 16개씩 Leaf에 저장하고 각 128MB 제한을 유지한다. 부모 Manifest는
기대 32/32/88개 전체 ID/의미 키/Leaf Hash·건수·입력 Binding을 검증한 뒤에만 성공한다.
16개 Leaf를 16개짜리 후보 Catalog로 위장하거나 일부 후보를 생략하지 않는다. 현재 Flat Store의 Reader가
새 Manifest를 이미 지원한다고 표현하지 않는다. 현재 메모리 Leaf·전체 후보 목록과 실제 실행 재생 검증은 구현했다.
Append-only 영속 Manifest/Writer/Reader·중단 후 재시작 복구는 로컬 구현했으며, 범위와 검증 방식은 13절에 구분한다.

Source Bank는 창·품목별 Nominal 1+선정 8+검증 8+최종 16로 주 198개, Stress 16개다.
모든 비용·좁은 범위에서 검증된 동일 정책 Bank를 재사용할 수 있지만 별도 32개 Source를 실행한 것처럼 기록하지 않는다.
원 Canonical/Prepared/수학 입력/계획 Forecast/Truth/Warm-up/분류/Config/규칙/Catalog/Cost/Window/Risk Hash를 모두 묶는다.
재봉인 Hash만으로 성공시키지 않고 실제 Guard·PSI·성분/Window 재계산을 요구한다.

비용 재평가의 캐시 절약을 방법의 속도 개선으로 주장하지 않는다. 성능용 표본은 A창·선정 첫 Seed·세 품목·세 방법을
서로의 Bank를 빌리지 않고 3회 회전 실행한다. 공통 준비·실현 평가·저장/검증·캐시 재사용은 별도 측정한다.
실행은 CPU Worker 1개, 호출당 최대 3,600초, 전체 로컬 원문 최대 20GB다. 구체적 시간/용량은 기술 Preflight에서 확인한다.
공식 경로를 만들기 전에 Smoke의 파일 크기·공유/분할 구조로 전체 예상 용량이 예산에 들어오는지 확인한다.
안 들어오면 증적을 약화하거나 Holdout 수를 줄이지 않고 저장 구조/자원 범위를 먼저 해결한다.
한도 초과는 `INCOMPLETE_RESOURCE_LIMIT`로 남기고 동일 봉인 입력으로 재개한다. Seed·기간·후보·경로를 줄이지 않는다.
부분 결과를 보고 조건을 조정하거나 일부만으로 합격을 발표하지 않는다.

## 9. 확인한 사실·가정·미구현을 분리한다

| 구분 | 상태 |
|---|---|
| 확인한 현재 구현 | 실제 공통 Guard/PSI, 32/32/88 후보, 기존 서비스/비용 정의, 작업 상한·128MB 제한 |
| 개발 가정 확정 | 새 날짜·Seed·분포/Bootstrap·Warm-up·52+13주·5비용·경험 위험 기준·새 실험 동점 규칙 |
| 구현 완료 | 순수 Window·5성분 비용/위험 Scorecard, Nominal/Risk 선정·결과 Hash·원 Row 재계산 검증 |
| 구현 완료 | 주어진 이력의 독립 Warm-up Source·W0 이월, 계획 Forecast/실현 수요 관측 분리·공통 Guard/PSI 실행 |
| 구현 완료 | 원 분류·품목 Projection, 전체 Catalog 후보→실제 전략/Guard/관측 분리 PSI→경로 평가기, 메모리 Leaf·전체 재실행 검증 |
| 구현 완료 | 로컬 영속 Chunk Manifest/Writer/Reader·동일 Hash 멱등 처리·봉인 입력 기반 재시작·실제 Leaf 재생 검증 |
| 구현 완료 | 수작업 32/32/88 후보의 18회 저장 성능 비교·선택 가능한 V2 무손실 압축·V1 읽기/혼합 복구 |
| 구현 완료 | 분류·Calendar·Forecast 공통 입력 V3 참조 저장·원문/결과 Hash 복원·V1/V2 호환/혼합 복구 |
| 구현 완료 | 실행별 동일 불변 입력의 준비/정규 JSON 재사용·후보 상태 분리·수작업 18회 ON/OFF 비교 |
| 구현 완료 | 전체 Artifact 무결성 조회와 실제 Source/PSI/지표 재계산의 별도 호출·상태, 기존 read/recover의 실제 재생 유지 |
| 구현 완료 | 같은 관리 루트 전체 원문 20GB 예약·3,600초 제어 호출·임시 파일 격리·기존 Checkpoint 재개 |
| 구현 완료 | 공식 경로 없이 421개 작업 상한 대사·같은 관리 루트의 수작업 자원 측정·미측정 전체 합계 차단 |
| 구현 필요 | 잠긴 단일 정책의 52주 종료 재생·범위/비용/선정·전체 Split 조립·Source 생성과 누적 루트/중단 여유의 전체 자원 합격 |
| 이번 미수행 | 공식 경로 생성·본실험·DB 접속/Write·승인 설정 변경·Runtime 배포·실제 발주·Commit/Push |

조건 확정 당시의 정적 검증은 Seed/기간·상한·비용 격리·수작업 위험 예제·고정 Hash를 확인했다.
이를 52주 PSI 계산 성공이나 새 실험 성과로 보고하지 않는다. 일부 스킬 공통 참조가 설치되지 않아 저장소의 계약·QA 절차로 보완했다.
새 조건/수작업 검증 25건·기존 Hash 호환성 4건·이전 비교 Offline 회귀 16건은 합계 **45 passed**다.
당시 전체 회귀는 재실행하지 않았다. 그 단계의 보호 파일 381개 불변과 검사 범위는
[조건 검증 기록](evidence/catalog-robustness-contract-20261019.json)에 보존한다.
`oma-architecture`로 대안·평가/실행 책임을 분리하고 `oma-qa`로 음성 조건·수치 검증 항목을 정했다.
저장소의 문서 관례에 맞춰 `.agents` 대신 이 `docs/architecture`에 결정 기록을 유지한다.

## 10. 남은 작업 순서

**현재 기준선 — 품목별 실행·로컬 저장/복구·V2/V3·불변 준비 재사용·조회/재계산 분리·자원 제어 경로 구현 · InventoryEngine**

- 52주 평가와 독립 Warm-up·W0 이월·미래 Suffix 변경 전 행동 불변, 부모 분류 보존·전체 후보의 실제 실행을 수작업 사례로 검증했다.
  봉인 입력·Leaf·전체 후보 Manifest의 로컬 저장과 중단 복구를 추가했다. V1을 보존하는 V2 압축과 18회 수작업 비교는 제14절이다.
  V3 공통 입력 참조·원문 복원·V1/V2 호환·저장량 검증은 제15절이다.
  실행별 불변 준비/직렬화 재사용·후보 상태 분리와 수작업 비교는 제16절이다.
  전체 파일 무결성 조회와 실제 재계산의 별도 호출·상태는 제17절이다.
  로컬 관리 루트의 예산·Deadline·임시 파일·중단 재개는 제18절이다.
  공식 경로 생성·본실험·운영 Gate 변경은 완료 범위에 포함하지 않는다.

**현재 기준선 — 전체 작업 목록과 부분 자원 사전 검증 · InventoryEngine**

- 제19절과 [사전 검증 계약](IO_ROBUSTNESS_SPLIT_PREFLIGHT_CONTRACT.md)에 전체 421개 작업 상한과 수작업 측정을 추가했다.
  후보/기간을 줄이거나 미측정 값을 0으로 채워 한도를 통과시키지 않는다. 전체 자원 합격은 보류한다.

**다음 작업 — 미측정 계산·집계 경로와 전체 합계 검증 · InventoryEngine**

- 잠긴 정책의 52주 재생을 추가하고 기존 순수 평가기·Source를 재사용해 범위/비용/선정·Manifest, 누적 루트/중단 여유를 검증한다.
- 전체 자원을 확인한 뒤에만 공식 입력 생성·선정·선택 Hash 봉인·Holdout/Stress·근거 작성을 진행한다.
  공식 경로 생성과 본실험은 이번에 실행하지 않는다.

**승인 필요 — 독립 DB 적재·복구 후 Demand P12 연결 · InventoryEngine·Platform·개발 PostgreSQL**

- 실제 승인 원본과 소량 Write 범위를 확인한 뒤 진행한다. 이번 조건 확정으로 Migration·업무 DB Write·공용 배포를 승인한 것은 아니다.

## 11. 개발 Source와 관측 분리의 확인 범위

- [독립 Source](../../src/dsio_inventory_engine/evaluate_replenishment/robustness/development_source.py)는
  정확한 104주 이력 중 W0−78~−53의 통계를 고정해 마지막 52주만 Warm-up한다. 이력의 부족 수요를
  실제 출고량으로 줄이거나 누락 값을 0으로 채우지 않는다. 마지막 26주 평균의 올림으로 별도 계획 Forecast를 만든다.
- [Source Binding](../../src/dsio_inventory_engine/evaluate_replenishment/robustness/source_binding.py)은
  원 Source를 재계산한 뒤 Canonical의 Scope·W0·Master/Calendar·재고·입고·Forecast·물리 제약을 대사한다.
  `calendar_revision`은 이 개발 계약에서 정확한 Calendar Snapshot ID를 가리킨다.
  Canonical Snapshot의 Hash만 다시 만들었다고 변경한 BOH·입고·Forecast를 허용하지 않는다.
- [관측 분리 실행기](../../src/dsio_inventory_engine/evaluate_replenishment/robustness/causal_simulation.py)는
  기존 Cut-off·Effective Policy V2/FSN·공통 Guard·공통 PSI를 사용한다. `REVIEW`는 자동 게시/발주 불가를 유지하며,
  `BLOCK` 또는 계산 불가 Gate를 무시한 비-HOLD 제안은 오류다. Warm-up의 기간 밖 공급 보존은
  신규 주문의 `ARRIVAL_OUTSIDE_PLAN_HORIZON` Guard를 완화하지 않는다.
- 확인한 수작업 예: 주당 1EA·LT35일은 W0 BOH 0/당일 입고 5/당주 EOH 4다. LT49일은 BOH 2/2주 후 입고 5다.
  마지막 Warm-up 주의 확정 수요 10EA 사례는 W0 고객 BO 9를 보존하고 당일 입고 5로 BO 4를 남긴다.
- 새 검증은 Warm-up 23건·Source/관측 Offline 통합 28건이며 기존 평가기·사양·호환성과 합계 **164 passed**다.
  이번 최종 전체 회귀는 **1,351 passed / 9 skipped / 669 subtests passed**(195.51초), 기존 Starlette 경고 3건이다.
  Ruff·Format 313개 파일·기존 JSON Schema 37개·Diff Check·로컬 참조 검증을 통과했고 보호 파일 392개는 동일하다.
  공식 32/32/88개 Catalog/분류 Projection·전체 Manifest·실제 Source 권위 승인·Runtime 설치/배포가 완료된 것은 아니다.
  [이번 검증 기록](evidence/catalog-robustness-source-causal-20261019.json)에 전체 회귀와 보호 파일 대사를 기록했다.

## 12. 부모 분류·전체 후보를 보존한 품목별 실행 조립

- [품목 조립기](../../src/dsio_inventory_engine/evaluate_replenishment/robustness/item_projection.py)는
  부모 분류의 원 ID/Hash·UUID·Config Revision/Hash·Site/W0·전체 품목·V2 정책 Hash를 검증한다.
  원 분류를 자르지 않고 선택 품목의 원 정책과 Canonical의 V2 정책을 비교한다. 다른 품목의 미분류/제외 사유도 원문에 남긴다.
- 전체 Catalog는 기존 Producer로 재생 검증한다. 부모 ABC 결과·전체 Universe·원 Feature Binding을 확인하고,
  실행 품목의 26주 수학 이력/개발 Source 이력·물리 LT·가격을 대사한다. 후보 하나의 누락·치환도 허용하지 않는다.
  원 모집단에서 BX인 품목을 품목별 AX로 다시 분류하지 않는 수작업 사례를 포함한다.
- [품목 실행기](../../src/dsio_inventory_engine/evaluate_replenishment/robustness/item_execution.py)는
  기존 `CandidateStrategy`를 내부에서 만들고 실제 공통 Guard·`RunCausalPsiUseCase`로 65주를 연속 계산한다.
  외부에서 PSI·Guard·서비스·비용 값을 주입하는 Runner는 받지 않는다. 메모리 Leaf는 최대 16후보·128MB이며
  부모 메모리 Manifest가 32/32/88개 전체 후보 ID·실행/Leaf Hash를 묶는다. 파일 저장·재시작 지원을 의미하지 않는다.
  88개 부모 결과 전체는 단일 JSON의 128MB보다 클 수 있으므로 새 메모리 Verifier도 Header와 최대 6개 Leaf를
  각각 제한·검증한 뒤 재실행한다. 기존 Flat Reader나 `clone`의 128MB 상한을 늘리지 않는다.
- 원 수작업 경로의 ID/Seed/Hash를 보존하고 계획 요청 Hash만 새 Catalog 전략 Binding에 맞춰 내부적으로 연결한다.
  실현 수요를 새로 만들거나 전략 관측에 넣지 않는다. 같은 입력의 세 후보 방법에서 원 경로 정체성을 유지한다.
- 모든 성공 후보의 원 관측·제안·Guard·수락 주문·PSI와 실패 후보의 상태·사유를 보존한다.
  전략 내부 예외는 기존 공통 실행기 정책에 따라 `STRATEGY_EXECUTION_FAILED`로 감싸지며 판단 ID·전략 증거를 남긴다.
  이를 감싸기 전의 내부 예외 메시지를 확보했다고 주장하지 않는다. Source/Scope/Hash/Guard 계약 오류는 실행 전체를 중단한다.
- 경로 평가는 실제 PSI·수락 주문·W0 미도착 공급에서 52주/추가 13주·중간 지표·5종 비용을 재계산한다.
  원 결과 Hash만 검사하지 않고 Verifier가 Catalog·전략·Guard·PSI부터 재실행한다. 산술상 맞는 HOLD 전용 결과도
  원 Catalog 행동과 다르면 거부한다. 무수요 서비스는 `null`, 비용은 별도 계산하며 실패 후보를 0비용 성공으로 대체하지 않는다.
- 단일 호출은 한 품목·제공된 한 경로다. 출력의 `evaluator_bank_fragment`는 Split 전체 Bank가 아니며
  `risk_aggregation_status=NOT_RUN_REQUIRES_COMPLETE_SPLIT`, `selection_status=NOT_RUN`을 유지한다.
  기존 순수 집계기의 최소 경로 수·실패 분모·선정 잠금 규칙을 완화하지 않는다.
- 주당 1EA·LT35일·RMOS 1.5·EMOS 1·CSL .95·매주 점검 수작업 사례는 ROP 7/Q 5,
  W0 BOH 0·당일 확정 입고 5·첫 6주 EOH 4/3/2/1/0/4다. 평가 52주의 재고 합은 197 EA·주,
  수락 발주는 11건/55EA이며 PRICE_BASE 보관비는 `197×2×.2/52`, 고정비 275·변동비 110 USD다.
  실제 운영 원가나 EOQ 최적값을 의미하지 않는다.
- 검증 기록: [품목별 실행 조립 Offline 검증](evidence/catalog-robustness-item-execution-20261019.json).
  공식 기간/Seed·이전 결과/Golden/Hash·FSN 설정·운영 Gate·DB·공용 Runtime은 변경하지 않았고 본실험도 실행하지 않았다.
- 새 단위 28건·Offline 통합 23건은 **51 passed**다. 최종 전체 회귀는 **1,402 passed / 9 skipped / 669 subtests passed**
  (304.79초), 기존 Starlette 경고 3건이다. Ruff·Format 318개 파일·기존 JSON Schema 37개·Diff Check와
  승인된 두 문서의 로컬 링크 72개를 확인했다. 두 문서를 제외한 기존 보호 파일 459개와 개인 보고서는 동일하다.

## 13. 로컬 분할 결과 저장·검증·중단 복구

- [영속 조립기](../../src/dsio_inventory_engine/evaluate_replenishment/robustness/durable.py)의 `run`은
  원 분류·Config·전체 Catalog·Source·주어진 실현 경로를 먼저 검증한다. 후보 경계는 원 목록에서 고정하며 외부의 부분 후보 목록을 받지 않는다.
  한 호출은 한 품목·기존 한 경로다. 새 공식 이력·경로·Seed를 생성하거나 Split 전체를 선정하지 않는다.
- [공통 실행 단계](../../src/dsio_inventory_engine/evaluate_replenishment/robustness/item_execution_steps.py)를
  기존 메모리 Runner와 공유한다. 실제 `CandidateStrategy`·FSN/V2 Gate·공통 Guard·관측 분리 PSI·비용 재계산은 동일하다.
  Hash 호환성은 원 계산 결과로 검증하며, 코드를 수정하지 않았다고 표현하지 않는다.
- 저장 순서는 `Input → Leaf/Checkpoint → Header → Manifest → COMPLETE`다.
  `INPUT`은 실행 ID가 가리키는 불변 입력, `LEAF:n`은 정해진 조각의 불변 포인터, `COMPLETE`는 모든 조각의 검증·저장이 끝난 Manifest 포인터다.
  원 결과 전체를 하나의 파일에 넣지 않는다. 각 파일은 128MB 이하이며, 88개 후보는 16/16/16/16/16/8로 저장한다.
- Manifest는 기대 후보 전체 ID와 각 Leaf의 후보 ID·실행 Hash·성공/실패 건수·PSI Row 수를 묶는다.
  부모 분류·품목 Projection·전체 Catalog·Assembly·실현 경로·비용 Profile·최종 결과 Hash도 고정한다.
  실패 후보를 삭제하거나 성공한 후보만으로 분모를 줄이지 않는다. 무수요·서비스 미달은 기존 평가 계약을 따른다.
- [POSIX 로컬 저장기](../../src/dsio_inventory_engine/infrastructure/robustness_artifacts.py)는
  정규 JSON 원문의 Hash·Byte Count·계약 ID를 검사한다. 전용 임시 파일의 `fsync` 후 배타적 Link와 디렉터리 `fsync`를 사용한다.
  같은 참조·같은 원문은 덮지 않고, 다른 원문은 충돌로 중단한다. 원문과 포인터의 Symlink·FIFO·디렉터리·중복 JSON Key·비정규 원문도 거부한다.
  호출자가 소유하는 비공개 로컬 폴더를 전제로 하며 악성 OS/동일 권한 프로세스로부터 보호하는 Sandbox를 뜻하지 않는다.
- `recover(execution_id, expected_input_hash=...)`는 저장된 봉인 입력만 읽는다. 최신 DB/Source를 찾거나 새 입력으로 대체하지 않는다.
  저장된 Leaf는 원 Hash와 실제 재계산을 대조하고, 미완료 Leaf를 계산한 뒤 마지막에 완료 표시를 기록한다.
  따라서 기존 Leaf도 계산 비용이 들며, 재계산 없는 복구나 모든 Leaf를 동시에 메모리에 두지 않는 Streaming은 아니다.
- `read`는 완료 표시가 없으면 차단한다. 완료된 Manifest의 파일/Checkpoint 누락·손상도 오류이며 자동으로 새 성공 결과를 만들지 않는다.
  명시적 `write`는 원 결과 전체를 실제 재생 검증한 뒤 같은 Hash의 누락 원문을 복원할 수 있다.
  기존 파일의 손상을 조용히 덮거나 다른 Hash로 갈아끼우는 복구는 허용하지 않는다.
- 원 계산 문서의 `durable_artifact_persisted=false`는 과거 Hash 호환성을 위해 유지한다.
  저장 완료는 별도 Receipt의 `durable_local_artifact=true`와 정확한 Manifest 참조로 확인한다.
  자동 게시·자동 발주·운영 승인·DB Write는 계속 `false`이며, 이 Receipt로 운영 입장을 허용하지 않는다.
- 당시 전역 20GB/호출당 3,600초 예산 통제와 부분 임시 파일 보존·청소는 후속 작업이었다. 로컬 제어 경로는 제18절에 추가했다.
  전체 Split Bank 조립·공식 경로·본실험은 여전히 미수행이다.
  이번 구현은 전원 차단 복구·분산 파일시스템·실제 운영 Source 승인·Platform/공용 Runtime 배포의 검증을 포함하지 않는다.
- 검증은 [저장기 단위 테스트](../../tests/unit/test_robustness_artifact_store.py)와
  [영속 복구 Offline 테스트](../../tests/integration/test_robustness_durable_offline.py)로 수행한다.
  실제 파일 손상·동시 쓰기·저장 경계의 예외·첫 Checkpoint 직후 프로세스 종료를 포함한다.
  새 단위 28건·Offline 통합 35건은 **63건**이며 최종 전체 회귀는 **1,465 passed / 9 skipped / 669 subtests passed**
  (430.34초), 기존 Starlette 경고 3건이다. Ruff·Format 323개 파일·기존 JSON Schema 37개·Diff Check·로컬 링크 81개도 확인했다.
  기존 32후보 결과 Hash `fbd77610f2019fb44b68dbf12a3667dbe6f31238e2d428909355bc7e7f1c67c6`는 저장·조회·재시작 후 동일하다.
  참조 88후보는 6개 Leaf·5,720 PSI Row와 Manifest 21,899 Byte이며 각 파일은 128MB 이하다.
  보호 파일 464개와 개인 보고서는 동일하고, 확인 범위와 지문은
  [영속 저장·복구 검증 기록](evidence/catalog-robustness-durable-recovery-20261019.json)에 보존한다.

## 14. 성능 비교 기준과 선택 가능한 V2 무손실 압축

- [저장·측정 계약](IO_ROBUSTNESS_STORAGE_COMPRESSION_CONTRACT.md)과
  [고정 비교 사양](data/robustness-storage-benchmark-v1.json)을 추가했다. 사양 Hash는
  `f96f089823d5c747b5cb09da516fa87ef4b0d7460f2d5033c28c44d68a11f06f`다.
  기존 강건성 사양·기간·Seed·규칙·Golden·선정 기준은 바꾸지 않는다.
- [V2 저장기](../../src/dsio_inventory_engine/infrastructure/robustness_compression.py)는 Gzip/level 1과
  물리 참조 `2.0.0`을 명시적으로 선택할 때만 사용한다. 기존 V1 Writer·기본 Runtime은 변경하지 않으며,
  새 Reader는 V1 및 V1/V2 혼합 Manifest를 읽는다. 과거 Reader가 V2를 읽는다는 뜻은 아니다.
- 계산 문서의 논리 `content_hash`·원 Result Hash를 보존한다. 정규 JSON+LF 원문은 `raw_sha256`/`byte_count`,
  실제 압축 파일은 `stored_sha256`/`stored_byte_count`로 구분한다. 코덱/버전/수준도 참조에 고정한다.
  저장 Manifest/Receipt는 물리 참조를 포함하므로 V1과 Hash가 달라질 수 있고, 이는 계산 결과 변경이 아니다.
- 원문 128,000,000 Byte와 전체 20,000,000,000 Byte 예산은 압축률로 할인하지 않는다.
  선언된 원문 크기+1 Byte로 복원을 제한하고 실제 파일 길이/Hash → 프레임/CRC → 원문 길이/Hash → JSON/논리 Hash 순서로 검증한다.
  잘림·추가 Member·Trailing Byte·복원 초과·변조·누락은 차단하며 다른 파일로 자동 대체하지 않는다.
- 기존 V1 객체/포인터는 검증 후 그대로 재사용하고 덮거나 삭제하지 않는다. 부분 V1 실행은 기존 조각을 보존한 채
  새 V2 조각으로 재개할 수 있다. 저장된 유효 압축 파일도 재압축하지 않아 Python/zlib 변경에 따른 멱등 충돌을 피한다.
  같은 포인터의 다른 참조는 여전히 충돌이다. 저장된 Leaf의 실제 전략·Guard·PSI 재생 검증도 유지한다.
- 2055년 수작업 한 경로·32/32/88 후보·V1/V2·각 3회, 총 18회를 새 프로세스/빈 폴더·Worker 1개로 비교했다.
  모든 입력·부모 분류·Catalog·결과·Leaf Hash와 2,080/2,080/5,720 PSI Row가 일치했다.
  파일 점유량은 각각 50.805→3.884 / 50.801→3.884 / 139.081→10.584 MB로 약 92.4% 줄었다.
- 준비·계산·게시 전 재생·저장·무결성 조회·재생 검증을 분리해 측정했다. 저장 중앙값은 약 3.1~9.1% 늘었고,
  속도·메모리 개선은 일관되지 않았다. ABC·XYZ/V2 첫 반복의 높은 RSS도 제외하지 않았다.
  원문 예산은 측정 Metadata까지 1,444,270,123 Byte, 실제 점유량은 777,238,696 Byte다.
  OS 캐시를 비운 실험이나 순간 최대 임시 디스크 측정은 아니며 공식 전체 경로의 자원 합격도 아니다.
- FSN·운영 Gate·DB·Platform·공용 Runtime·Commit/Push는 변경/수행하지 않는다.
  당시 공통 입력 정규화·반복 처리 재사용·빠른 조회/재생 검증 분리·전체 Streaming·전역 자원 통제는 후속 작업이었다.
  공통 입력 참조화의 현재 상태는 제15절을 우선한다.
- 새 단위 48건·계약 4건·Offline 통합 10건으로 **62건**을 추가했다. 최종 전체 회귀는
  **1,527 passed / 9 skipped / 669 subtests passed**(529.72초), 기존 Starlette 경고 3건이다.
  Ruff·Format 330개 파일·기존 JSON Schema 37개·Diff Check를 확인했다. 승인된 두 문서 외에 기존 보호 파일 471개와 개인 보고서는 동일하다.
  수치·개별 반복·Hash·코드 지문·검증 범위는 [압축 저장·비교 기록](evidence/robustness-storage-compression-20261020.json)에 보존한다.

## 15. V3 공통 입력 참조와 정확한 원문 복원

- [공유 입력 계약](IO_ROBUSTNESS_SHARED_INPUT_CONTRACT.md)과
  [V3 저장기](../../src/dsio_inventory_engine/infrastructure/robustness_shared_artifacts.py)를 추가했다.
  기존 Writer·기본 Runtime·영속 조립기의 계산/재생 검증은 변경하지 않고 Repository를 명시적으로 선택할 때만 연결한다.
- 512 Byte 이상의 분류·Calendar·Forecast Dict/List를 내용 Hash의 불변 공유 파일로 저장한다.
  주차별 다른 Suffix는 서로 다른 값이며, 같은 값을 후보/문서 사이에서 재사용한다. 공유 파일은 다른 참조를 실행하지 않는 평평한 원 값이다.
  원 관측·행동·정책·Guard·PSI를 요약/삭제하거나 재분류하지 않는다.
- V3 물리 참조는 원 계산의 논리 Hash·원문 크기/Hash와 정규화 Manifest 크기·압축 파일 크기/Hash를 구분한다.
  참조 경로·중복/겹침·4,096개/깊이 64 제한을 확인하고, 공유 파일을 읽기 전에 원문 복원 크기를 계산해 128MB 상한을 검사한다.
  파일·프레임·Wrapper·값 Hash를 확인해 원문 바이트를 복원한 뒤 기존 봉인과 실제 Guard/PSI를 재생 검증한다.
- 공유 파일 → 문서 → Input/Leaf 포인터 → 전체 Manifest → COMPLETE 순서다.
  공유 파일만 쓴 중단은 같은 봉인 입력으로 다시 요청하며 Input 포인터 이후에는 정확한 입력 Hash로 새 프로세스에서 복구한다.
  V1/V2 완료 결과는 원 파일과 Receipt를 그대로 읽고, 부분 결과는 기존 조각을 보존한 V1/V2/V3 혼합 복구를 지원한다.
  과거 Reader가 V3를 읽는다는 뜻은 아니며 누락·손상·변조를 다른 형식이나 최신 Source로 대체하지 않는다.
- 비압축 저장량은 각 실제 공유 Wrapper·정규화 Manifest·객체·포인터를 한 번씩 합산한다. 압축률로 원문 예산을 할인하지 않는다.
  모든 원 문서를 펼친 논리 크기는 별도 기록하며 복원 문서당 128MB 상한을 유지한다.
  당시 후속이던 20GB/3,600초 예약/취소·임시 파일 격리는 제18절에 로컬 제어 경로로 추가했다.
  게시된 미참조 파일은 자동 삭제하지 않으며 여러 경로의 전체 Bank 조립은 여전히 후속이다.
- 2055년 수작업 32/32/88 후보를 실제 실행·저장·복구했다. V2/V3의 Input/Header/Leaf 원문·Hash·크기와
  기존 최종 Result Hash·2,080/2,080/5,720 PSI Row가 일치했다. 비압축 저장량은
  50.807→22.781 / 50.803→22.777 / 139.085→60.455 MB, 실제 파일은 3.884→2.137 / 3.884→2.150 / 10.584→5.675 MB다.
  각 V3 폴더의 공유 파일은 129개다. 파일 수는 늘었으며 속도·메모리 개선을 판정한 실험이 아니다.
- FSN `BLOCK`/`REVIEW`·자동 게시/발주 금지·실패 후보 전체 분모를 유지했다.
  DB·Platform·공용 Runtime·공식 경로·본실험·실제 발주·Commit/Push는 변경/수행하지 않았다.
- 새 단위 48건·계약 3건·Offline 통합 13건으로 **64건**을 추가했다. 최종 전체 회귀는
  **1,591 passed / 9 skipped / 669 subtests passed**(761.16초), 기존 Starlette 경고 3건이다.
  Ruff·Format 337개 파일·기존 JSON Schema 37개·Diff Check를 확인했다. Coverage·Bandit·Mypy·독립 검토는 수행하지 않았다.
  승인된 두 문서 외에 기존 보호 파일 481개와 개인 보고서는 동일하며,
  [공유 입력 검증 기록](evidence/robustness-shared-inputs-20261020.json)에 정확한 참조·지문·검증 범위를 보존한다.

## 16. 불변 준비·직렬화 재사용과 후보 상태 격리 — 2026-10-20

- [재사용 계약](IO_ROBUSTNESS_IMMUTABLE_PREPARATION_CONTRACT.md)과
  [실행별 준비 슬롯](../../src/dsio_inventory_engine/evaluate_replenishment/robustness/immutable_preparation.py)을 연결했다.
  전체 요청 JSON·Source/생성 요청의 실제 정규 내용·배포 Scope·기대 Source Hash가 모두 같아야 재사용한다.
  호출자가 같은 Hash를 주장하더라도 원문이 달라지면 다시 검증한다. 설정·Snapshot·전략 Binding·물리 제약 변경도 동일 기준이다.
- 처음에는 기존 Source 재계산·Cut-off/Canonical 준비를 수행한다. 내부에서 검증한 불변 JSON 문자열만 저장하고 매번 새 Dict/List를 반환한다.
  고정 BOH/Source 정책은 입력 값으로만 보존하며 후보별 정책·FSN Admission·현재 재고/BO·신규 주문·미도착 공급·Guard/PSI를 재사용하지 않는다.
  실현 경로는 별도로 검증하고 미래 실현 수요를 전략에 전달하지 않는다. 원 관측·Leaf·결과 Hash 의미를 바꾸지 않는다.
- 최대 128,000,000 Byte 한 슬롯만 유지한다. 입력 문서 자체의 상한은 별도로 적용하며 합산 보존 한도만 넘으면 재사용 없이 계산한다.
  Context는 로컬 Worker 1개 실행에 한정하고 새 실행·새 Build·새 읽기/복구에서는 새 슬롯을 만든다. 교차 프로세스/Build 캐시는 없다.
  이 보존 한도를 프로세스 전체 RSS나 서비스 전역 자원 통제라고 표현하지 않는다.
- 기존 V1/V2/V3 파일·읽기·멱등·재시작 계약은 유지한다. 저장 Leaf 재생에서도 모든 후보의 전략·Guard·PSI를 실제로 실행하며
  준비 슬롯 적중을 계산 재생 완료 증거로 대체하지 않는다. 당시 후속이던 조회/실제 재생 분리는 제17절에서 완료했다.
- 2055년 수작업 한 경로로 32/32/88개 전체 후보를 재사용 ON/OFF 각각 세 번, 총 18회 새 프로세스로 비교했다.
  공통 준비 32/88회가 각각 1회로 줄었고 전체 계산시간 중앙값은 4.145→3.728 / 4.257→3.824 / 11.352→10.130초다.
  두 비교군 모두 입력 Hash 지역 변수 재사용을 포함하므로 과거 Commit 전체와의 비교나 수학 전략의 정책 성과 개선은 아니다.
- 최대 RSS 중앙값 380.7→341.4 / 376.9→490.7 / 971.4→783.0 MB는 일관된 감소가 아니다.
  저장·조회 속도나 공식 전체 실험의 자원 합격은 이번 측정 대상이 아니다. 이전 저장 비교 기록은 보존한다.
- 새 단위 10건·계약 1건·Offline 통합 7건은 18건이다. 세 모드 원문 바이트·결과 Hash, 역순 Leaf·반환 결과 변조·입력 변경,
  FSN BLOCK/REVIEW·실패 후보 보존, 후보 16개 Leaf 한 개의 실제 전략 1,040회 재생과 잘못된 실현 경로 Binding 차단을 검증했다.
  최종 전체 회귀 결과·Source 지문·측정 파일 지문과 보존 결과는
  [준비 재사용 검증 기록](evidence/robustness-immutable-preparation-20261020.json)에 기록한다.
- 최종 전체 회귀는 **1,609 passed / 9 skipped / 669 subtests passed**(735.40초), 기존 Starlette 경고 3건이다.
  Ruff·Format 342개 Python 파일·기존 JSON Schema 37개·Diff Check·변경 부분의 로컬 문서 참조 6개를 확인했다.
  Coverage·Bandit·Mypy·독립 검토는 수행하지 않았다. 연결 코드 4개와 승인된 기존 문서 2개 외에 기존 보호 파일 509개와
  개인 보고서 Hash는 동일하다. 새 구현·테스트·측정/문서 파일은 7개다.
- ABC/XYZ 결과/Hash·분류/규칙 Golden·FSN 설정·운영 Gate·DB·Platform·공용 Runtime은 변경하지 않았다.
  공식 경로 생성·본실험·실제 발주·Commit/Push는 수행하지 않았다.

## 17. 전체 Artifact 무결성 조회와 실제 PSI 재계산 분리 — 2026-10-20

- [조회·재계산 계약](IO_ROBUSTNESS_VERIFICATION_READ_CONTRACT.md)과 별도 Python/JSON Schema 응답 `1.0.0`을 추가했다.
  `inspect`는 `ARTIFACT_INTEGRITY`, Source/PSI/지표 재계산은 모두 `NOT_RUN`이다.
  `verify_recalculation`은 무결성 확인·기존 `read`의 실제 재생·전후 Binding 대사 후 `FULL_PSI_RECALCULATION`과 모두 `VERIFIED`를 반환한다.
- 입력·Header·Manifest·공유 파일·전체 Leaf·후보 32/88개·순서·중복/누락·성공/실패/PSI 건수·Trace/Scorecard 연결과 최종 결과 Hash를 확인한다.
  큰 부모 원문은 한 번에 직렬화하지 않는다. 원 결과 Hash·V1/V2/V3 파일·Receipt는 유지하며, 기존 `read`·`recover`의 실제 계산을 생략하지 않는다.
- 검증 상태는 원 결과의 새 필드나 영속 승인 포인터가 아니다. 매 호출 현재 파일을 다시 확인하고, 전체 검증은 현재 구현으로 재계산한다.
  잘못된 비용을 관련 Hash와 함께 일관되게 다시 봉인한 사례는 무결성만 통과하고 실제 재계산에서 거부된다.
  전체 `VERIFIED`는 실패 후보가 없어졌거나 서비스 목표를 만족했다는 의미도 아니다.
- 승인·자동 게시·발주·DB Write 값은 모두 `false`다. FSN BLOCK/REVIEW·운영 Gate는 그대로다.
  별도 응답의 형식/Hash를 맞추는 것만으로 운영 적격이나 계산 실행을 증명할 수 없다.
- 새 단위 26건·계약 23건·Offline 통합 16건을 확인했다. 32/32/88개 결과 Hash와 2,080/2,080/5,720 Row,
  후보 수 × 65 실제 전략 실행, 파일 손상/누락·구현 변경·검증 중 Manifest 변경·기존 읽기/복구 호환성을 검증했다.
  전체 회귀는 **1,674 passed / 9 skipped / 669 subtests passed**(925.76초), 기존 Starlette 경고 3건이다.
  Ruff·Format 348개 파일·JSON Schema 38개·Diff Check·네 문서의 로컬 참조 117개를 확인했다.
  코드 1개·승인된 기존 문서 3개 외에 기존 보호 파일 518개와 개인 보고서 Hash는 동일하다.
  [검증 기록](evidence/robustness-verification-read-20261020.json)에 파일 지문과 확인 범위를 보존한다.
- 전체 파일을 읽고 복원·Hash 확인하므로 상수 시간 조회나 일정 속도/RSS 개선을 보장하지 않는다.
  원문 20GB·문서당 128MB·호출당 3,600초 조건은 유지한다. 당시 다음 작업이던 자원 제어 경로는 제18절에 추가했다.
  DB·Platform·공용 Runtime·공식 경로 생성·본실험·실제 발주·Commit/Push는 변경/수행하지 않았다.

## 18. 관리 루트 전역의 원문 예산·실행 중단·임시 파일 정책 — 2026-10-20

- [자원 통제 계약](IO_ROBUSTNESS_RESOURCE_CONTROL_CONTRACT.md)의 별도 정책 `1.0.0`과
  제어기를 로컬 영속 조립기·V1/V2/V3 저장 Adapter에 연결했다. 기존 직접 호출·공용 Runtime의 자동 활성화는 아니다.
- 같은 관리 루트의 모든 폴더/프로세스가 최대 20GB 복원 원문 예산을 공유한다. 예약·메타데이터·격리 파일도 계산하고
  게시 전 OS Lock/예약 fsync로 경합을 막는다. 공유 파일/같은 Inode는 한 번 계산하며 압축률로 원문 예산을 할인하지 않는다.
- 호출별 최대 3,600초를 대기·기동·계산에 적용한다. Worker Process Group과 독립 Watchdog으로 시간 초과·취소·감독 프로세스 소멸을 처리한다.
  자원 중단을 성공이나 실패 후보로 바꾸지 않고, 새 호출은 같은 입력 Hash의 저장 Leaf도 실제 재생한 뒤 이어간다.
  큰 입력은 같은 예산의 임시 Job 파일에 봉인하고 작은 참조만 독립 Worker에 전달한다. 기동 파이프 교착·Python 시작 지연도 검증한다.
- 미완성 임시 파일은 원문 크기·물리 Hash·시각을 기록해 최소 24시간 격리한다. 명시적 정리 호출만 만료된 격리 파일을 지운다.
  원 Artifact·Checkpoint·게시된 미참조 객체는 자동 삭제하지 않는다. 미확인 임시 파일은 128MB로 보수적으로 계산한다.
- 실제 수작업 32/32/88 후보·두 프로세스 예산 경합·시간 초과/대기/취소/하위 프로세스·감독 프로세스 종료·첫 Leaf 후 재개·
  V1/V2/V3 원문/참조·정리 범위·FSN BLOCK/REVIEW를 Offline으로 확인했다.
  새 단위 25건·계약 20건·Offline 통합 18건을 추가했다. 기동 Job의 파일 수 여유 검사까지 포함한 최종 대상 회귀는
  **63 passed**(139.98초)다. Job을 쓰기 전에 원문 예산과 파일 수 상한의 여유를 확인한다.
  전체 회귀는 **1,736 passed / 9 skipped / 669 subtests passed**(1,042.46초), 기존 Starlette 경고 3건이다.
  전체 수집은 마지막 Job 파일 수 검사 1건 추가 전이며, 최종 보완은 위 63건에서 별도로 확인했다.
  Ruff·Format 358개 파일·JSON Schema 39개·Diff Check·다섯 문서의 로컬 참조 136건과 파일 지문을
  [검증 기록](evidence/robustness-resource-control-20261020.json)에 남겼다. 기존 보호 파일 527개와 개인 보고서는 동일하다.
- 원 계산/결과 Hash·FSN 설정·운영 Gate·DB·Platform·공용 Runtime은 유지한다.
  공식 경로 생성·본실험·실제 발주·Commit/Push는 수행하지 않는다. 당시 후속이던 전체 Split 자원 사전 검증은 제19절에 구분한다.

## 19. 전체 작업 목록과 부분 자원 사전 검증 — 2026-10-20

- [Split 사전 검증 계약](IO_ROBUSTNESS_SPLIT_PREFLIGHT_CONTRACT.md)에 기존 Protocol Hash를 보존한 작업 목록·측정·판정 경계를 추가했다.
  기본 WIDE 198개·Stress 16개·독립 시간 비교 27개·잠긴 단일 정책 52주 종료 재생 상한 180개로 총 421개다.
  기본 후보×주차 작업량은 1,132,560이며 비용 Profile과 범위마다 PSI를 다시 실행한다고 계산하지 않는다.
- 실제 측정은 같은 Pool의 기존 제어 경로에서 2055년 수작업 사례만 사용한다. 기존 원문/결과 Hash·32/32/88 후보·
  전체 PSI와 Guard·무결성 조회/전체 재계산 구분·24시간 임시 파일 격리를 유지한다.
- 부분 추정과 개발 여유 25%는 전체 자원 합격이나 하드 상한이 아니다. 종료 재생·전체 집계·누적 루트 대사·경로 크기·
  중단 여유·Source 생성/전체 분류가 미측정이므로 전체 합계는 `null`, 상태는 `BLOCKED_INCOMPLETE_MEASUREMENTS`다.
- 새 대상 회귀는 **59 passed**(115.60초)다. 별도 수작업 2회 순환 실측 6회에서 기존 결과 Hash·PSI Row를 보존했다.
  원문 증가는 ABC/XYZ 22.996MB·LEGACY_STYLE 22.993MB·WIDE 60.763MB, 호출 시간은 각각
  23.64~27.99/24.69~26.54/65.75~66.38초였다. 계산·조회·전체 재계산과 자원 대사를 포함한다.
- 알려진 Catalog 부분 추정은 원문 13.964GB·물리 1.377GB·약 4.25시간이다. 25% 여유 포함 시 원문 17.455GB·
  물리 1.721GB·45,622개 파일·약 5.31시간이다. 미측정 항목을 포함한 실제 전체 합계나 하드 상한은 아니다.
  [검증 기록](evidence/robustness-split-preflight-20261020.json)에 실제 표본과 미측정 항목을 구분한다.
- 소스를 고정한 최종 전체 회귀는 **1,796 passed / 9 skipped / 669 subtests passed**(1,196.83초), 기존 Starlette 경고 3건이다.
  Ruff·Format 365개 파일·JSON Schema 40개·선택한 여섯 문서의 로컬 참조 149건·Diff Check를 확인했다.
  승인된 기존 문서 3개 외에 기존 보호 파일 541개·개인 보고서와 기존 코드/규칙/Schema/Golden/측정 기록은 동일하다. 새 파일은 10개다.
- FSN·운영 Gate·DB·Platform·공용 Runtime·공식 경로·본실험·실제 발주·Commit/Push는 변경/수행하지 않는다.
