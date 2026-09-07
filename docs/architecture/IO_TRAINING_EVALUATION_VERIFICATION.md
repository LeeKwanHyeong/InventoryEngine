# 합성 학습·평가 기반 검증 기록

기준일: 2026-09-03. 대상: `/Users/igwanhyeong/PycharmProjects/InventoryEngine`, Python 3.12.10, 패키지 `0.5.0`.

이 문서는 0.5.0 당시 기록을 보존한다. 이후 생산 전략 연결과 최신 검증은 [0.6.0 검증 기록](IO_PRODUCTION_EVALUATION_VERIFICATION.md)을 따른다.

판정: **합성 학습·평가 기반 로컬 구현 PASS**. 운영 정확성, 실제 Forecast/공급 Source 수집, 생산 전략 성능·ML/PPO 학습 또는 DB 저장의 합격 판정이 아니다.

## 1. 검증 범위와 결과

| 검증 | 결과 |
|---|---|
| 단위 테스트 | 140건 통과: 기존111 + 학습/평가29 |
| 계약 테스트 | 10건 통과, Skip 없음: 기존7 + Schema/BOH·Canonical 호환3 |
| 오프라인 프로세스 통합 | 11건 통과: 기존7 + 학습 CLI/예제/거부4 |
| 기존 Golden | Baseline14개/45 Row, 수학적14개/42 Row 유지 |
| 독립 합성 Golden | 기존 G01~G14 대응, 직접 계산한 Ledger/정책/납기/비용과 거부 조건 |
| 시간·무결성 | 미래 수요 변경에도 과거 BOH·첫 관측/행동 불변, 미래 입고일 비노출, 공급/재고/BO 보존, 다품목 실패 원자성 |
| Coverage | Unit+Contract 전체98%(Statement/Branch 합산). 새 핵심 모듈 측정99~100%. 모든 입력 조합의 완전성 증명은 아님 |
| Ruff | 0.15.22 Lint/Format 통과 |
| Mypy | 2.3.1, 변경 Source8개 통과 (`--follow-imports=silent --ignore-missing-imports`, 선택 asyncpg 타입 제외) |
| Bandit | 1.9.4, 추가 학습/평가·입력 계약·CLI Finding 없음 |
| Wheel | 0.5.0 빌드 성공, 소스/Wheel CLI 전체 JSON 동일 |

합계161개 테스트이며 이전 개발 DB 읽기 전용 테스트5건을 합산하지 않는다. 이번에 DB/Neo4j 연결, Migration, 실제 E2E Write, 공용 Runtime 배포, 모델 학습은 실행하지 않았다. 격리된 임시 복사본에서 검증한 변경 파일만 실제 프로젝트에 반영하고 실제 경로에서 테스트를 재실행한다. 원본 `main.py`/Notebook과 다른 프로젝트의 변경은 보존한다.

## 2. 실행 가능한 예제

```bash
IO_COMPANY_CD=DSE IO_ENVIRONMENT=DEVELOPMENT PYTHONPATH=src python3 -m dsio_inventory_engine prepare-training --request examples/training_input.json
```

- 130주 × 5품목 = Demand650 Row
- 26주 TRAIN +13주 VALIDATION +13주 TEST = Feature260/Label260 Row
- 3개 구간 ×6시나리오 = 독립 초기화18개. 각52주 ×5품목 = Ledger260 Row
- TEST ×6시나리오 ×2 Reference Benchmark = 평가12개
- 전체 결과 Hash: `b8b481db3fd9c935853c3c5ff6ba09b49d7e0afac768df9060c7647ae102b5b6`
- 소스+Wheel의 동일 예제 CLI 두 번 약1.04초(현재 로컬 1회 관측, 성능 SLA 아님)

| 합성 Scenario | HOLD 비용 | REFERENCE_R_S 비용 |
|---|---:|---:|
| BASE | 13,343 | 3,337 |
| HIGH_SHORTAGE_COST | 43,133 | 6,697 |
| SERVICE_99 | 12,951 | 3,625 |
| DELAY_1W | 13,269 | 4,075 |
| DELAY_2W | 13,473 | 5,499 |
| DISRUPTION | 13,343 | 3,621 |

USD로 표시한 합성 단위 비용이며 실제 금액이 아니다. `REFERENCE_R_S`는 생산 수학적 전략이 아닌 독립 과거 기반 Benchmark다. HOLD 대비 낮은 비용이 운영 절감 효과나 세 전략 중 최적 전략을 뜻하지 않는다. Scenario별 초기재고가 달라질 수 있으므로 같은 Scenario 안에서 비교한다.

## 3. 한계와 다음 검증

- Reference World는 실제 수요의 BO를 이월한다. 생산 PSI의 Forecast Shortage 규칙을 바꾸지 않는다.
- 주말 발주 Warm-up, 주초 발주 평가, FROZEN_PRE_W0 생산 수학적 정책은 각각 구분한다. 다음 구현은 생산 전략 평가 Adapter다.
- 합성 과거 평균 Forecast는 DemandEngine의 실제 Forecast Vintage가 아니다. 실측 공급지연·원가·ERP 확정 시점도 검증하지 않았다.
- 미래 Label 누락은 거부한다. 명시적 과거 0 보정, 단일 연속 Active 기간, Reserved0, 고정 UOM별 단위 비용, Terminal Salvage0의 범위를 따른다.
- 평가 종료 후 공급/재고/BO는 보존하지만 잔존 자산 가치는 보상에 넣지 않는다. PPO 본학습 전 종료 효과와 긴 Horizon 민감도를 검토한다.
- JSON Hash·Lineage는 반환하지만 Artifact/TB_IO에 저장하지 않는다. `run_claimed`, `database_writes`, `artifact_sealed`, `evidence_persisted`, `model_trained`는 모두 false다.
- `oma-backend`의 실행 경계·입력 검증, `oma-qa`의 독립 수작업 기대값/실행 검증을 적용했다. `oma-docs` 검증 CLI는 설치되지 않아 파일 참조·명령·완료/후속 상태를 직접 대조했다.
- InventoryEngine은 Git 미초기화 상태로 Branch/Remote가 없어 Commit/Push/MR을 만들지 않았다. P0-14/18/19의 보류와 DB/배포 승인 경계는 유지한다.
