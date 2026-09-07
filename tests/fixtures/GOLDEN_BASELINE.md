# Baseline PSI 독립 Golden v1

입력과 기대값은 [golden_baseline.json](golden_baseline.json)에 사람이 검산 가능한 작은 수량으로 고정했다. 작성자는 Codex이며 사용자가 직접 엑셀을 작성해야 하는 절차가 아니다. 운영 Source 승인이나 정식 Legacy 회귀 합격 기준(P0-19)과는 별개다.

| 사례 | 수작업 핵심 근거 / 검증 목적 |
|---|---|
| normal_consumption | 100+40−80−(110−80)=30 → 30−20=10 → 10 |
| zero_boh_backorder | W0 주문 BO10·Forecast Shortage10 → W1 20−BO10−Forecast5=5. Shortage는 이월하지 않음 |
| backorder_priority | 30으로 기존 BO20을 먼저 처리하고 주문25 중10만 충족. 다음 주 BO15 |
| upstream_netted | 순 Forecast30을 주문80과 별도로 차감. 주문80을 Forecast에서 다시 소비하지 않음 |
| supply_states | 8개 상태 중 CONFIRMED5+IN_TRANSIT10만 입고. 원본 40/50의 미확약 공급은 제외 Evidence |
| order_exceeds_forecast | max(30−80,0)=0. 음수 Forecast 없이 실제 주문 BO30 이월 |
| reserved_unavailable | 물리100−비가용 Reserved30=가용70. Demand80에서 Shortage10, 물리 EOH30 보존 |
| decimal_stock | Decimal로 0.3+0.2−0.1−0.3=0.1. 부동소수 오차 없이 다음 주0 |
| approved_adjustment | 전기80+승인20=현재100, Forecast20 차감 후80 |
| negative_adjustment | 전기120+승인(−20)=현재100. 감소 조정도 동일 대사 |
| year_boundary | Calendar 202653→202701→202702 순서. YYYYWW 정수 증가 금지 |
| site_item_isolation | 같은 Site의 A/B 재고를 공유하지 않고 각자 10−2=8, 20−3=17 |
| tgsm_target_not_boh | 목표재고1000을 BOH로 사용하지 않음. 합성 BOH7에서5를 차감해2 |
| intermittent_and_excess | 수요0/10/0에서 EOH20/10/10. 누락 수요를 임의 추정하지 않음 |

각 사례는 세 주차의 BOH·입고·주문·순 Forecast·충족량·EOH·Backorder·Shortage 11개 기대 수량을 명시한다. 두 Item 사례를 포함해 PSI 45개 Row의 기대값을 비교한다. 별도로 봉인/Hash/Scope/고아/정책누락/Watermark/Late Posting 등의 오류 코드를 테스트한다.

`fixtures.py`는 Scope/Calendar/Metadata를 확장하고 Hash를 계산하는 **테스트 전용 포장기**다. 기대 PSI 값은 JSON의 Literal이며 포장기나 Engine이 생성하지 않는다. 예제 파일의 SEALED 표시는 로컬 계약 검증용이고 실제 ERP 승인·Artifact 봉인 증거가 아니다.

현재 Golden SHA-256:

```text
14d7ea7343f59a125aa37154c3a2689cf353744c5930a40ff66b150ed75c226c
```

범위 밖: 안전재고/ROP/MOQ·배수 보충 계산, 실제 52주 합성 Generator, 일중 입고·출고 순서, 운영 데이터 정확성, 실제 Demand→IO DB Write E2E. 다음 정책 단계에서 권고량 기대값을 따로 추가한다.
