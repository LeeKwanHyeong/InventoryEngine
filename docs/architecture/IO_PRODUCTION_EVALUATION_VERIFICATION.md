# 생산 수학적 전략 평가 연결 검증 기록

기준일: 2026-09-03. 대상: `/Users/igwanhyeong/PycharmProjects/InventoryEngine`, Python 3.12.10, 패키지 `0.6.0`.
판정: **개발 로컬 연결·계산·계약 PASS**. 운영 재고/Forecast 정확성, 실제 서비스수준 달성, 성능 우월성 또는 ML/PPO 학습 완료 판정이 아니다.

## 1. 검증 결과

| 검증 | 결과 |
|---|---|
| 단위 테스트 | 159건 통과: 이전140 + 생산 평가19 |
| 계약 테스트 | 12건 통과, Skip 없음: 이전10 + 중첩 Schema/DTO2 |
| 오프라인 프로세스 통합 | 14건 통과: 이전11 + 새 CLI/거부/예제3 |
| 합계 | 185건. 이전 Network 실제 DB 읽기 전용5건은 합산하지 않음 |
| 독립 기대값 | 정상·1주 지연·2주 지연 3개 수작업 Golden/12개 주차 Row, 추가 Shock·기간형 정책/승인·용량 손계산 |
| 생산 코드 실행 | 실제 정책 준비기1회·주차별 실제 Guard 호출. 별도 생산 PSI 실행의 정책 Report와 정확히 일치 |
| 실험 조합 | 3개 구간×6시나리오=18회, 총1,560개 품목·주차 판단; POSM 소수 UOM은 별도 검사 |
| 정보/상태 방어 | 미래 실제 수요·실제 납기 비노출, 첫 판단 불변, 실제 BO 피드백, 기존 주문 중복 방지, 원래 납기 보존 |
| 업무 제약 | 기간별 Lead Time·MOQ/Lot·발주일·승인 목표/발주 한도, Override/이력 부족 Fallback/계산 제외, 연체 공급 용량 예약 |
| 입력 방어 | 기존 Cut-off/EOH–BOH·미봉인·고아 거부, BOH/공급/Forecast/이력/Source/Calendar/Scope 불일치 거부 |
| 재현성 | 외부 Decimal 설정 불변, 동일 입력 결과 일치, 다품목 무효 행동의 부분 상태 변경 없음 |
| Coverage | Unit+Contract 전체98%, 새 평가/계약/관측 모듈100%, 독립 World·Reference99~100% (측정한 문장/분기 기준, 모든 입력 조합 증명 아님) |
| 정적 검증 | Ruff0.15.22 Lint/Format 통과; Mypy2.3.1 대상 Source8개 통과; Bandit1.9.4 대상 모듈 Finding 없음 |
| 패키지 검증 | Wheel0.6.0 빌드, 소스/Wheel CLI의 전체 JSON 동일(기본 예제1,230,519자) |
| 기존 동작 | `prepare-training`, `recommend-mathematical`, `simulate-baseline` 예제의 변경 전후 전체 JSON 동일 |

`__version__`도 패키지 Metadata와 0.6.0으로 맞췄다. 기존 전략/Canonical/Training Wire Version은 유지한다. 입력/출력 봉인과 승인 **구조 검증**을 인증이나 실제 저장 완료로 취급하지 않는다.

## 2. 실행 근거

```bash
PYTHONPATH=src python3 -m unittest discover -s tests/unit -q
DSAI_PLATFORM_ROOT=/absolute/path/to/dsai-platform PYTHONPATH=src python3 -m unittest discover -s tests/contract -q
PYTHONPATH=src python3 -m unittest discover -s tests/integration -p '*offline.py' -q
PYTHONPATH=src python3 examples/production_evaluation.py --request-only | IO_COMPANY_CD=DSE IO_ENVIRONMENT=DEVELOPMENT PYTHONPATH=src python3 -m dsio_inventory_engine evaluate-mathematical --request /dev/stdin
```

기본 TEST 예제의 입력 Hash: `fba63f132c4901c1fbf4bc1f1181b7f42c8d177d83ed105be2cff28f4aa59980`.
결과 Hash: `3e80d9385c93b3d0d844a3d7b20052f9d1154f6fc15f999ab71110ae1e159430`.

| 합성 Scenario | 새 생산 전략 평가 비용 |
|---|---:|
| BASE | 3,235 |
| HIGH_SHORTAGE_COST | 7,420 |
| SERVICE_99 | 3,395 |
| DELAY_1W | 4,498 |
| DELAY_2W | 5,987 |
| DISRUPTION | 3,581 |

통화 표시는 USD이나 실비가 아닌 합성 가중치다. [기존 연구 Benchmark 표](IO_TRAINING_EVALUATION_VERIFICATION.md)의 `REFERENCE_R_S`와 별도 산출물이다. 정책 갱신/반올림, Horizon 밖 발주, 지연의 Warm-up 적용 조건이 다르므로 직접적인 성능 순위나 운영 절감률은 제시하지 않는다.

기존 `prepare-training` 전체 결과 Hash `b8b481db3fd9c935853c3c5ff6ba09b49d7e0afac768df9060c7647ae102b5b6`와 기존 Baseline14개/45 Row·수학적14개/42 Row Golden은 유지한다.

## 3. 적용 경계와 다음 작업

- 변경 전 복사본과 격리 작업본에서 먼저 검증하고 변경 파일만 실제 프로젝트에 반영한다. 반영 후 실제 경로에서 동일 단위/계약/오프라인 통합 테스트를 재실행한다. 원본 `main.py`·Notebook, 다른 프로젝트의 미커밋 변경은 보존한다.
- DB/Neo4j 접속, Migration, E2E DB Write, 공용 Runtime 배포, 실제 Run/Artifact 게시, 모델 학습은 수행하지 않았다.
- `oma-backend`의 실행 경계·승인 제약과 `oma-qa`의 독립 기대값/회귀 검증을 적용했다. `oma-docs` 검증 CLI가 없어 변경 문서의 실제 파일 링크·CLI 명령·완료/후속 상태를 직접 대조했다.
- InventoryEngine은 Git 미초기화로 Branch/Remote가 없으며 Commit/Push/MR을 만들지 않았다.
- 위 수치는 0.6.0 당시 기록이다. 이후 0.7.0에서 [ML/PPO 로컬 학습·추론·동일 조건 평가](IO_LEARNED_STRATEGIES_VERIFICATION.md)를 완료했다. 다음은 여러 Seed·비반복 장기 자료의 학습 안정성과 실제 Source/모델 Evidence 연결이다. P0-14/18/19 보류는 유지한다.
