# InventoryEngine

Python 3.12 기반 Inventory Optimization 프로젝트다. **0.12.0은 Platform이 Claim한 단일 Site Attempt를 닫힌 계약으로 접수하고, 단계 Event와 최종 Publication을 Platform API로 반환하는 Runtime 경계를 제공한다.** 0.11.0의 ABC-XYZ 분류 Snapshot Lifecycle과 기존 Canonical·PSI·전략 계약을 유지한다. HTTP 양방향 계약과 Mock/Offline 통합은 검증했지만 영속 Queue/Worker, 실제 계산 Handler 조립, Migration 074 적용과 Runtime 배포는 아직 수행하지 않았다.

## Source 읽기·변환 예제

```sh
IO_COMPANY_CD=DSE IO_ENVIRONMENT=DEVELOPMENT \
  inventory-engine prepare-source --request examples/source_input_request.json \
  --snapshot-root examples/source_snapshots
```

예제는 `DEVELOPMENT_FIXTURE`이며 실제 Demand Export가 아니다. `--read-postgres`와 `IO_POSTGRES_DSN`은 실제 상위 봉인 Snapshot이 준비된 경우에만 지정한다. 현재 DB의 재고/정책 미준비·소수 EA·출력 봉인 연결 조건은 [Source 계약](docs/architecture/IO_SOURCE_READ_CONTRACT.md), 검증 결과는 [Source 검증 기록](docs/architecture/IO_SOURCE_READ_VERIFICATION.md)을 따른다. 출력 JSON은 원본·매핑·제외 사유를 포함하지만 DB/Artifact를 저장하거나 봉인하지 않는다.

- [개발 기준선과 작업 순서](docs/architecture/IO_DEVELOPMENT_BASELINE.md)
- [Runtime Lifecycle 계약](docs/architecture/IO_RUNTIME_LIFECYCLE_CONTRACT.md)
- [ABC-XYZ 분류 Snapshot Lifecycle](docs/architecture/IO_CLASSIFICATION_SNAPSHOT_LIFECYCLE.md)
- [Network 입력 계약](docs/architecture/IO_NETWORK_INPUT_CONTRACT.md)
- [Canonical 입력·Cut-off·Baseline PSI 계약](docs/architecture/IO_CANONICAL_PSI_CONTRACT.md)
- [세 전략 공통 계약·주간 PSI·행동 검증](docs/architecture/IO_REPLENISHMENT_STRATEGY_CONTRACT.md)
- [전략 공통 기반 검증 기록](docs/architecture/IO_REPLENISHMENT_STRATEGY_VERIFICATION.md)
- [수학적 정책 계약](docs/architecture/IO_MATHEMATICAL_POLICY_CONTRACT.md)
- [수학적 정책 검증 기록](docs/architecture/IO_MATHEMATICAL_POLICY_VERIFICATION.md)
- [수학적 권고 Golden](tests/fixtures/GOLDEN_MATHEMATICAL.md)
- [학습·평가 기반 계약](docs/architecture/IO_TRAINING_EVALUATION_CONTRACT.md)
- [학습·평가 검증 기록](docs/architecture/IO_TRAINING_EVALUATION_VERIFICATION.md)
- [합성 재고·비용 Golden](tests/fixtures/GOLDEN_TRAINING.md)
- [생산 수학적 전략 평가 계약](docs/architecture/IO_PRODUCTION_EVALUATION_CONTRACT.md)
- [생산 전략 평가 검증 기록](docs/architecture/IO_PRODUCTION_EVALUATION_VERIFICATION.md)
- [ML/PPO 학습·추론 계약](docs/architecture/IO_LEARNED_STRATEGIES_CONTRACT.md)
- [ML/PPO 실험·검증 결과](docs/architecture/IO_LEARNED_STRATEGIES_VERIFICATION.md)
- [학습 안정성·후보 선정 계약](docs/architecture/IO_LEARNING_STABILITY_CONTRACT.md)
- [다중 Seed·신규 홀드아웃 실행 결과](docs/architecture/IO_LEARNING_STABILITY_VERIFICATION.md)
- [독립 Golden 사례와 계산 근거](tests/fixtures/GOLDEN_BASELINE.md)
- [목표 아키텍처](docs/architecture/IO_ENGINE_TARGET_ARCHITECTURE.md)

## 실행

프로젝트 루트에서 실행한다. 아래 기본 명령은 요청 파일만 검증하며 DB에 접속하지 않는다.

```bash
PYTHONPATH=src python3 -m dsio_inventory_engine prepare-network --request examples/network_input_request.json
```

실제 Network 입력 준비는 `--read-postgres`를 명시한다. `IO_COMPANY_CD`, `IO_ENVIRONMENT`, `IO_POSTGRES_DSN`을 환경에서 주입하고 선택 의존성 `asyncpg`가 설치된 Python을 사용한다. 기본 접속 정보나 비밀번호는 없다. `.env`를 자동 로딩하지 않는다.

```bash
PYTHONPATH=src python3 -m dsio_inventory_engine prepare-network --request examples/network_input_request.json --read-postgres
```

결과는 stdout의 JSON이다. `NETWORK_INPUT_PREPARED`는 입력 준비 성공일 뿐 Run 성공이나 PSI 계산 완료가 아니다. 예제의 Cycle/Run/Master Revision ID는 설명용이며 실제 공통 Run이나 Planning Cycle Row를 생성하지 않는다.

### ABC-XYZ 분류 Snapshot

정확한 Site 범위의 활성 Configuration과 최신 Actual Close로 분류한다. `--apply`가 없으면
읽기와 계산만 수행한다. 실제 게시 시에도 동일 content hash가 이미 있으면 기존 Revision을
반환하고 새 행을 만들지 않는다.

```bash
IO_POSTGRES_DSN=<external-profile> PYTHONPATH=src \
  python3 -m dsio_inventory_engine.entrypoints.classification_snapshot \
  --project-id <project> --company-cd DSE --subs-cd C100 \
  --plant-cd V100 --site-cd V100 --approved-by <service-user>
```

게시를 승인받은 실행에서만 마지막에 `--apply`를 추가한다. Receipt에는 자재별 원천이나
분류 행이 아니라 조직 범위, Revision/hash와 Segment 집계만 포함된다.

공통 Runner에서는 `InventoryClassificationStageUseCase`를 사용한다. 이 경로는 이미 Claim된
Inventory Run만 받으며 분류 Snapshot 게시 전후에 append-only 단계 Event를 기록한다. 독립
CLI는 Run을 만들지 않으므로 기존처럼 `run_claimed=false`를 유지한다.

## 검증

### 로컬 PSI 실행

다음 명령은 예제 파일을 검증해 주차별 EOH `30 → 10 → 10`을 반환한다. PostgreSQL과 Neo4j에 접속하지 않고 DB의 Run을 만들지 않는다. `--read-postgres`와 함께 사용할 수 없다.

```bash
IO_COMPANY_CD=DSE IO_ENVIRONMENT=DEVELOPMENT PYTHONPATH=src python3 -m dsio_inventory_engine simulate-baseline --request examples/baseline_input.json
```

출력 JSON에는 원본 Canonical Snapshot, Hash/Binding, Cut-off 대사, Forecast Consumption, 입고 제외 사유와 PSI Row가 포함된다. `BASELINE_PSI_COMPUTED_LOCALLY`는 계산 완료이며, Artifact/DB에 영속 저장됐다는 뜻은 아니다. 예제의 `SEALED` Snapshot은 수작업 테스트 입력이고 운영 승인 데이터가 아니다.

### 수학적 정책과 Recommended PSI 실행

다음은 실제 수학적 산출기다. 예제의 수작업 과거 이력에서 SS=4, ROP=14, 목표재고=24와 권고 주문 14·10, EOH `0 → 4 → 4`를 계산한다. 운영 데이터·52주 Generator가 아니며 DB/Artifact에 저장하지 않는다.

```bash
IO_COMPANY_CD=DSE IO_ENVIRONMENT=DEVELOPMENT PYTHONPATH=src python3 -m dsio_inventory_engine recommend-mathematical --request examples/mathematical_input.json
```

입력은 기존 Canonical과 실행 설정 1.1.0, Hash로 고정한 정책/이력 Snapshot이다. 누락 이력은 0으로 채우지 않으며 승인 Fallback이 없으면 권고 제외 이유를 반환한다. 자세한 가정·정규근사 한계는 수학적 정책 계약을 따른다.

### 독립 합성 BOH·학습/평가 데이터 준비

130주·5품목·6시나리오를 결정론적으로 생성하고, 26주 학습/13주 검증/13주 평가 구간과 각 구간 직전 52주 Warm-up을 만든다. 출력은 stdout JSON이며 DB·파일에 자동 저장하지 않는다. `HOLD`와 `REFERENCE_R_S` 비교는 연구 환경 점검이며 생산 수학적 전략이나 ML/PPO 성능 검증이 아니다.

```bash
PYTHONPATH=src python3 examples/training_foundation.py
PYTHONPATH=src python3 examples/training_foundation.py --request-only
IO_COMPANY_CD=DSE IO_ENVIRONMENT=DEVELOPMENT PYTHONPATH=src python3 -m dsio_inventory_engine prepare-training --request examples/training_input.json
```

`examples/training_input.json`은 같은 Recipe로 미리 생성한 입력이다. 실제 Forecast/입고지연 자료가 아닌 합성 Profile이며, 운영 정확성/모델 학습·Artifact 봉인 완료를 뜻하지 않는다. `prepare-training`은 Production과 `--read-postgres`를 거부한다.

### 생산 수학적 전략의 독립 평가

기존 수학적 정책·승인 선택·공통 행동 검증기를 실제로 호출하고, 매주 실현된 재고/BO를 받아 다시 판단한다. 연구용 `REFERENCE_R_S`와 별도 결과로 반환한다. 생산 코드를 사용하는 **로컬 합성 평가**이며 운영 배포가 아니다.

```bash
PYTHONPATH=src python3 examples/production_evaluation.py
PYTHONPATH=src python3 examples/production_evaluation.py --scenario DELAY_1W
PYTHONPATH=src python3 examples/production_evaluation.py --request-only | IO_COMPANY_CD=DSE IO_ENVIRONMENT=DEVELOPMENT PYTHONPATH=src python3 -m dsio_inventory_engine evaluate-mathematical --request /dev/stdin
```

정책/Forecast는 평가 시작 이전 이력으로 고정한다. 평가 시작 이후 예정 입고에 지연을 적용하며, 주문의 기간형 정책·MOQ/Lot·납기·Horizon 거부 규칙은 생산 Guard를 따른다. 비용/서비스 결과는 운영 우열 판정이 아니다. DB/Artifact 저장·학습은 하지 않는다.

### 세 전략 공통 계약 Probe

다음은 고정 목표 200을 제출하는 인터페이스 예제이며 실제 수학적 산출기나 학습 모델이 아니다. `probe_only_not_a_trained_strategy=true`를 반환한다. DB·학습 실행이 아니다.

```bash
PYTHONPATH=src python3 examples/strategy_contract_probe.py --strategy MATHEMATICAL
PYTHONPATH=src python3 examples/strategy_contract_probe.py --strategy PREDICTIVE_ML
PYTHONPATH=src python3 examples/strategy_contract_probe.py --strategy DEEP_RL
```

### 실제 ML/PPO의 로컬 학습·추론·비교

학습에는 선택 의존성 `python3 -m pip install -e '.[learning]'`이 필요하다. 추론은 표준 라이브러리만 사용한다. 아래는 유한 기간·합성 Forecast로 수행하는 개발 실험이며 운영 성능 합격이나 모델 봉인을 의미하지 않는다.

```bash
PYTHONPATH=src python3 examples/learned_strategies.py
PYTHONPATH=src python3 examples/learned_strategies.py --quick --train-only
PYTHONPATH=src python3 examples/learned_inference.py --strategy DEEP_RL
PYTHONPATH=src python3 examples/learned_inference.py --request-only | IO_COMPANY_CD=DSE IO_ENVIRONMENT=DEVELOPMENT PYTHONPATH=src python3 -m dsio_inventory_engine recommend-learned --request /dev/stdin
```

학습 전 13/26주 TRAIN·52/104주 TRAIN 반복 스트레스로 종료 효과를 점검한다. 이후 실제 ML/PPO를 학습하고 수학적 전략·무신규공급 대조군과 같은 TEST 초기재고·입고지연·종료 비용으로 비교한다. 기본 실행은 ML300회/PPO12 Episode의 소규모 실험이다. 서비스·비용 저성능 사례도 결과에 보존하며 자동 운영 전환하지 않는다. [실행 증적](docs/architecture/evidence/learned-strategies-smoke-20260903.json)의 JSON 모델을 `learned_inference.py`가 명시적으로 읽는다.

### 다중 Seed 후보 선정과 새 홀드아웃

학습 Seed3개×예산 후보2개를 TRAIN52주에서 학습하고 VALIDATION52주에서 비용·서비스를 함께 평가한다. 선택 파일 Hash를 고정한 뒤 별도 프로세스에서 자료 Seed3개의 새104주 홀드아웃을 연다. 기존 TEST를 반복한 장기 자료가 아니다. 상세 사용법은 [계약](docs/architecture/IO_LEARNING_STABILITY_CONTRACT.md)을 따른다.

```bash
PYTHONPATH=src python3 examples/learning_stability.py request
PYTHONPATH=src python3 examples/learning_stability.py select --output /tmp/io-selection-new.json
PYTHONPATH=src python3 examples/learning_stability.py verify-selection --selection docs/architecture/evidence/learning-stability-selection-20260903.json --selection-hash db4d8f24a85e65876b7eba60811208900bbebf3b714f883c7a787b1cb3bbaa76
```

`holdout`에는 `--selection`과 별도로 기록한 `--selection-hash`가 필수다. 출력 파일은 덮어쓰지 않는다. 모든 Seed와 실패 지표를 보존하며, 후보 지명/Hash 검증은 운영 승인이나 DB Run 생성이 아니다.

### 테스트

순수 계산·추론 테스트는 표준 라이브러리로 실행한다. 실제 학습 테스트는 `learning` Extra가 필요하며 미설치 환경에서는 해당 테스트만 Skip한다. Schema·Lint·Coverage·Type Check 도구는 별도 개발 환경에서 `python3 -m pip install -e '.[dev]'`로 설치할 수 있다.

```bash
PYTHONPATH=src python3 -m unittest discover -s tests/unit -v
PYTHONPATH=src DSAI_PLATFORM_ROOT=/absolute/path/to/dsai-platform python3 -m unittest discover -s tests/contract -v
PYTHONPATH=src python3 -m unittest discover -s tests/integration -p '*offline.py' -v
python3 -m ruff check src tests
python3 -m ruff format --check src tests
PYTHONPATH=src python3 -m coverage run --branch --source=dsio_inventory_engine -m unittest discover -s tests/unit
python3 -m coverage report -m
```

Network 실제 DB 통합 테스트는 별도다. `IO_READONLY_INTEGRATION=1`과 연결 설정을 주입한 뒤 `-p test_network_input_readonly.py`로 실행한다. 모든 연결은 `default_transaction_read_only=on`이며 Opt-in 없이는 Skip한다. 이번 Canonical/PSI 작업에서는 이 외부 DB 테스트를 재실행하지 않았다.

기존 `main.py`는 PyCharm 샘플로 보존했다. `sample_jupyter/`의 연구 Notebook은 Engine 실행 경로가 아니다. Git 초기화·배포 설정·공용 Runtime 재배포는 이번 변경에 포함하지 않는다.
