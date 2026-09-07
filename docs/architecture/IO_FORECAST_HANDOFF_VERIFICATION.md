# Forecast Handoff 오프라인 검증 기록

2026-09-04. InventoryEngine 0.10.0 / DemandEngine `demand_engine_v3`. Python 3.12.10, Polars 1.41.2. 기존 프로젝트의 사용자 변경은 보존하며 검증은 임시 Stage에서 수행했다. 실제 DB 연결·Mutation·Migration·배포는 하지 않았다.

## 1. 결과

| 검증 | 결과 | 범위 |
|---|---:|---|
| IO 단위 테스트 | 236 통과 | 기존 Network/Source/PSI/정책/ML/PPO/안정성 회귀와 새 Canonical v2 7건 |
| IO 계약 테스트 | 19 통과 | 기존 Schema/Golden/Producer 호환성 |
| IO 오프라인 통합 | 31 통과 | 기존 23건 + 실제 Demand Writer→독립 IO Reader 교차 8건 |
| Demand 선택 회귀 | 59 통과 | 새 Export 20건, 기존 Master Artifact Wiring·Winner 선택·Forecast 계약 39건 |
| IO Wheel | 0.10.0 빌드 성공, 교차 8건 재통과 | Wheel import로 Part 읽기/Canonical 준비/소규모 PSI |
| 타입 검사 | 대상 9개 모듈 통과 | IO 3개·Demand 6개 핵심 신규/계약 경계. 전체 저장소 strict typing 주장이 아님 |
| Ruff / Bandit | 지정 범위 통과 | IO src 및 새 테스트, Demand 변경 Python / 신규 전달 모듈 보안 검사 |

Coverage 7.16.0의 branch 포함 측정: Demand 신규 전달 6개 모듈 **95%**, IO Parquet Reader/Handoff UseCase 2개 모듈 **100%**. 이는 선택 모듈 Coverage이며 전체 엔진의 Coverage가 아니다. ML/PPO 새 본학습이나 실 DB Source 성능 검증은 수행하지 않았다.

## 2. 독립 기대값과 대형 입력

- 시작재고 2, 각 주 Forecast 0.4의 3주 EOH: **1.6 → 1.2 → 0.8**.
- 13주 Forecast 총량: **5.2 EA**, 주별 반올림으로 0 또는 13으로 바뀌지 않는다.
- 202627 월 귀속: **202606**. Request의 Demand Plan 주차와 Forecast W0를 별도로 보존한다.
- **4,270품목 × 26주 = 111,020행**을 분할 Parquet로 봉인하고 IO Canonical 입력 준비까지 완료했다. Canonical JSON이 8MB를 넘는 것을 확인했다. 행 자르기·Horizon 축소는 하지 않았다.
- 대형 검증의 BOH/정책/Forecast는 명시적 테스트 Fixture다. 대형 Recommended PSI·운영 처리량·ERP 재고 정확성의 검증 결과가 아니다.
- 실제 수학적 계산과 분석적으로 고정한 ML/PPO 가중치의 추론 경로를 v2로 실행했다. 최종 권고 입고는 정수/Lot를 유지했다. 모델의 운영 성능 합격을 뜻하지 않는다.

## 3. 방어·복구 경계

POINT 외 통계량, 잘못된 Shared Hash/Revision, 누락된 실행 전 Receipt, 실패 Run, 잘못된 Winner, 중복/누락/정렬 오류, Part 변조/누락/경로 탈출을 거부했다. v2에서도 물리 EA 소수와 잘못된 BOH/Cut-off 입력을 거부한다.

같은 Key·같은 데이터의 Export Retry는 원래 Manifest/Receipt를 반환했다. Part 크기를 바꿔 재시도해도 원래 봉인을 사용한다. 같은 Key에서 수량이 달라지거나 기존 파일이 손상되면 거부한다. 전체 검증 실패 시 최종 봉인 폴더가 생기지 않는 것을 확인했다.

PostgreSQL Adapter는 가짜 Connection으로 Read Only·Repeatable Read·정확한 Run/선정/결과 대조·Rollback/Close를 검증했다. CLI는 로컬 파일/Hash 검증 실패 시 DB 연결을 하지 않고 비밀정보를 출력하지 않는다. **실제 DB Transaction/Index/EXPLAIN·서버 장애·DB 게시 복구·Crash Fault Injection은 아직 검증하지 않았다.**

## 4. 기존 v1 동일성

변경 전 0.9.1과 변경 후 0.10.0에서 같은 `examples/baseline_input.json`을 계산했다.

| 값 | 전후 동일 Hash / 결과 |
|---|---|
| Canonical Input | `2b44a573926dae00a8462a4fa56367f65fffe4aa672def7c3ecb785d62afe19d` |
| PSI Rows | `57aa8092469d4c2103b15b3d5cdedf231a9b7b7b2a07ae6c13eef2abde34a09b` |
| EOH | `30 → 10 → 10` |

기존 독립 Baseline Golden 14개/45 Row와 수학적 Golden 14개/42 Row도 전체 회귀에서 유지했다. 기존 v1 Schema·Fixture 파일은 수정하지 않았다.

## 5. 재현 명령과 상태

InventoryEngine 루트에서 개발 의존성과 선택 `handoff` 의존성을 갖춘 Python으로 실행한다.

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m unittest discover -s tests/unit -q
DSAI_PLATFORM_ROOT=/Users/igwanhyeong/PycharmProjects/dsai-platform PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m unittest discover -s tests/contract -q
DEMAND_ENGINE_ROOT=/Users/igwanhyeong/PycharmProjects/DemandEngine PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m unittest discover -s tests/integration -p '*offline.py' -q
```

`DEMAND_ENGINE_ROOT`가 없으면 교차 테스트 8건은 Skip이므로 통과로 세면 안 된다. 이번에는 실제 Stage 경로를 명시해 실행했다. Demand 테스트는 `PYTHONPATH=src python -m pytest tests/deliver_demand/test_forecast_export.py tests/run_demand/test_postgresql_full_pipeline_master_artifact_wiring.py tests/deliver_demand/test_weekly_performance_selection.py tests/deliver_demand/test_weekly_forecast_contracts.py -q -p no:cacheprovider`로 재현한다.

`oma docs` CLI가 없어 변경 문서의 상대 경로를 별도 로컬 검사로 대조했다. 승인 이력 문서의 옛 제안 상태는 역사 기록으로 표시하고 개발 기준선/계획을 현재 구현 상태에 맞췄다. 실제 Shared Artifact Mapping·실행 전 Receipt 저장·Studio/Run/DB Publication은 [남은 작업](IO_FORECAST_HANDOFF_CONTRACT.md#6-남은-순서)이다. P0-14/18/19는 계속 보류한다. Commit/Push/MR은 수행하지 않았다.
