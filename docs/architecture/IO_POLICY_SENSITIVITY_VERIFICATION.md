# 장기 평가·비용 민감도 개발 검증 기록

검증일: 2026-10-06. 대상: InventoryEngine `inventory_engine_dev`.
시작 HEAD: `53869a9a05d04d5ca7174e998f75e54a9df63a50` + 기존 미커밋 정책 탐색·선택 변경.
이번에는 InventoryEngine만 변경했다. dsai-platform, DemandEngine, DB와 공용 Runtime은 변경하지 않았다.

## 완료 범위

- Sensitivity Request/Result `1.0.0`, 기간별 실제 Simulation 결과 비교, 비용 재평가,
  Validation에서 고정한 정책의 새 Seed Holdout, 공통 Prefix 종료 효과 진단
- 같은 Scope·기초재고·분류/Config·정책·제약·후보 집합 확인, Trace Prefix와 Holdout 재사용 차단
- 비용 미가용·무수요·서비스 미달의 명시적 처리, 주당/수요 단위당 비용
- 별도 PSENS Append-only Artifact·부모 PSR 존재 확인·저장/읽기 Replay·멱등 처리
- 제품 CLI `inventory-policy-sensitivity`, 저장소 개발 재현 도구 `scripts/verify_policy_sensitivity.py`

최종 전체 회귀는 **705 passed, 22 skipped, 631 subtests passed**였다(114.34초).
신규 Sensitivity 테스트 26건과 기존 선택기 43건을 모두 포함한다.
Skip은 기존 DB·외부 환경 의존 테스트이며 DB 검증 성공으로 해석하지 않는다.
Ruff·Format(221개 Python 파일), JSON Schema 28개, 계획 JSON과 `git diff --check`도 통과했다.
현재 검증 환경에 `coverage` 모듈이 없어 Coverage 비율은 측정하지 않았다.
기존 환경에 추가 패키지를 설치하지 않았고 수치 Coverage를 통과했다고 주장하지 않는다.

## 실제 개발 실험

1 SKU × 4 후보 × 7 기간/Path Case = **28개 실제 후보 Simulation**을 생성했다.
이를 비용 Profile 6개로 재평가해 실험당 **168개 후보 지표, 42개 품목/Case/비용 비교 행**을 만들었다.
기준 정책을 13주에서 고정한 실험과 52주에서 고정한 실험은 각각 별도 Hash로 봉인했다.
두 실험은 같은 Holdout을 비교용으로 재사용한다. 최종 정책 선택에 Holdout을 되먹이지 않았다.

| 항목 | 개발 Fixture 기준 |
|---|---|
| 품목/위치 | `ITEM-A`, `DSE/C100/V101`, EA |
| 시작 재고 | 30 EA(명시적 Fixture, 새 Warm-up BOH로 주장하지 않음) |
| 역사 수요 | 고정된 사전 13주, 평균 10 EA/주 |
| 물리 Lead Time | 7일 |
| 후보 | 고정 Q/정기 Order-up-to × EMOS 0.25/1, RMOS 0, 검토주기 1주 |
| 정책 CSL / 선정 Fill Rate | 0.95 / 0.90(서로 다른 지표) |
| Validation | Seed 7, 동일 미래 Path의 13·26·52·104·108주 Prefix |
| Holdout | Seed 17/29, 각 104주, 새로운 계획 수요 Path |
| 기본 비용(USD 가정) | 보관 1/EA·주, 고객 BO 10/EA·주, 발주 고정 5, 변동 2/EA |
| 민감도 | 보관×5, 고정 발주×20, 변동×2, 고객 BO×5, 비용 미가용 |

## 관측 결과

### 기간과 비용에 따른 선정 변경

| 평가기간 | 기본 비용에서 선정 | 고정 발주비×20에서 선정 |
|---|---|---|
| 13주 | 정기 Order-up-to, EMOS 0.25 | 고정 Q, EMOS 1 |
| 26주 | 정기 Order-up-to, EMOS 0.25 | 고정 Q, EMOS 1 |
| 52주 | 정기 Order-up-to, EMOS 1 | 정기 Order-up-to, EMOS 1 |
| 104주 | 정기 Order-up-to, EMOS 1 | 정기 Order-up-to, EMOS 1 |
| 108주 | 정기 Order-up-to, EMOS 1 | 정기 Order-up-to, EMOS 1 |

비용이 없는 모든 Case는 미선정이다. 고객 BO가 없는 이 Path에서 BO 단가 증가 효과는 없다.
이 사례에서는 기간이 길어질수록 작은 정책이 서비스 조건을 벗어나 후보 선정이 바뀌었다.
모든 변경을 종료 편향 하나로 설명하지 않는다.

### 고정 정책의 104주 Holdout

| 고정 기준 | Seed | 수요 충족 수량/수요 | Fill Rate | 주말 평균재고 | 주당 가정 비용 | 90% 조건 |
|---|---|---|---|---|---|---|
| 13주 기본 비용 선정 | 17 | 948/1,137 | 83.38% | 3.07 EA | 25.625 USD | 미달 |
| 13주 기본 비용 선정 | 29 | 976/1,174 | 83.13% | 2.46 EA | 25.558 USD | 미달 |
| 52주 기본 비용 선정 | 17 | 1,137/1,137 | 100% | 32.06 EA | 58.548 USD | 충족 |
| 52주 기본 비용 선정 | 29 | 1,174/1,174 | 100% | 31.25 EA | 58.702 USD | 충족 |

해석: **짧은 평가에서 비용이 낮았던 후보는 새 장기 자료에서 서비스 조건을 통과하지 못했다.**
큰 정책은 이 개발 Path에서 서비스 조건을 충족했지만 재고와 비용이 늘었다.
100%는 이 1 SKU·2 Seed Fixture의 관측치일 뿐 실측 서비스 보장, 통계적 우월성 또는 자동 승격 근거가 아니다.

### 종료 효과

4개 기간×4개 후보의 동일 Prefix 비교 16건 중 13건에서 행동 차이가 있었다.
차이는 짧은 평가의 마지막 주에 발생했다. 짧은 Case에서는 기간 밖 입고 때문에 주문이 거부되지만,
108주 Case에서는 같은 판단이 허용돼 짧은 경계 이후의 미도착 주문으로 남는다.
차이가 없는 후보도 보존했다. Tail 전체는 계속 발주한 결과이며 잔존가치·Drain-down 검증이 아니다.

## 수식 검증과 재현성 검증의 구분

- 독립 3주 Golden: 고정 Q의 EOH `[0,3,0]`, 13 EA 주문 1회 → 기본비용 `3+5+26=34`,
  보관비×5 → `15+5+26=46`으로 실제 재평가 결과를 확인했다.
- 공통 2주 Golden: 정기 정책의 EOH `[0,3]`, 주문 13+10, 두 번째 주문은 3주차 입고.
  경계의 미도착 10 EA와 비용 `3+2×5+23×2=59`, 주당 `29.5`를 확인했다.
- 장기 Hash 고정 테스트는 **재현성 회귀**다. 같은 계산기로 두 번 계산한 값을 독립 수식 증명으로 쓰지 않았다.
- 결과 숫자를 변조한 후 Hash를 다시 계산해도 Artifact Replay에서 실패했다.
  원본 PSR 손상·부모 누락·통화/Scope/기초재고/Lead Time/Trace 불일치도 차단했다.
- Holdout Source 입장 검증에서 선택 함수가 호출되지 않는 테스트를 추가했다.

## 봉인 증적

13주 기준:

- Input Hash: `3b8a2ef183748e49d112b8534f4532e6acb9acdb0098c195850d842d9fd093dc`
- PSENS Hash: `7549c32c95e59267407a82b89be93b3c7121fc85783c4da11875202ecb3082d3`

52주 기준:

- Input Hash: `e48c34b02aafa760b127f3c786fc7d06fc170b6c4d99ca8d337d045da950d1c8`
- PSENS Hash: `1edcc1c0fc129e80b01614639007c02fb802c07c605d3bd819dcde0f41982f15`

실행 시 `/private/tmp/inventory-policy-sensitivity.PYbqPp`에 부모 PSR 7개와 PSENS 2개를 저장하고,
각 실험의 전체 Replay 읽기를 완료했다. 임시 로컬 경로는 운영 Archive가 아니므로 장기 보존을
보장하지 않는다. 저장소에는 재현 도구·Golden 테스트·이 검증 기록을 남겼다.

## 남은 작업 순서

1. **Platform Run·결과 조회 계약 연결 — 다음 작업 · dsai-platform Backend/Frontend, InventoryEngine**
   탐색·선택·평가 사양과 Artifact를 Run에 연결하고, 장기 서비스 미달·비용 가정·종료 효과를 결과 화면에 표시한다.
   API 계약 이후 Backend와 Frontend의 독립 파일 구현은 병렬 가능하고 통합 검증은 직렬이다.
2. **개발 배포·실제 DB 통합 검증 — 승인 필요 · 개발 PostgreSQL·dsai-platform 개발 서버·공용 Runtime**
   대상과 Write 범위를 별도로 승인한 뒤 수행한다. 이번 로컬 합성 실험은 이를 대체하지 않는다.
3. **변경 증적과 커밋 정리 — 다음 작업 · InventoryEngine `inventory_engine_dev`, dsai-platform `codex/inventory-psi-strategy-v2`**
   기존 Source Producer와 개인 보고서를 분리해 검증된 변경만 독립 Commit 후보로 정리한다.
   Commit·Push는 이번 요청에서 수행하지 않았다.

승격 수치의 운영 확정, 실측 장기 Backtest, PPO/MEIO 개선은 이번 완료 범위 밖이다.
이미 개발 합성 실험이 가능하므로 실제 Source Owner 미지정을 현재 구현 차단 사유로 사용하지 않는다.
