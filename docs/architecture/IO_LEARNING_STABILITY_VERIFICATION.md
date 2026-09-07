# 학습 안정성 실행·검증 결과

기준일: 2026-09-03. 대상: `/Users/igwanhyeong/PycharmProjects/InventoryEngine`, 0.8.0. Python3.12.10 / PyTorch2.12.1 / CPU float64. [실험 계약](IO_LEARNING_STABILITY_CONTRACT.md)을 따른다.

## 1. 결론

**다중 Seed·비반복 장기 평가 구현과 실제 실행은 완료했지만 ML/PPO 모두 연구용 성능 Gate 미달이다.** 수학적 전략을 개발 기준선으로 유지한다. ML/PPO를 운영 사용하거나 모델이 수렴했다고 선언하지 않는다.

TRAIN에서 후보 2개×Seed 3개를 실제 학습했다. 총 모델12개(ML6/PPO6), ML1350 Optimizer Step, PPO72 Episode·18,720 Item-step이다. 모델별 학습 전20개 TRAIN Preflight를 수행했다. VALIDATION48개 모델 평가에서 지명한 ML `BUDGET_18`, PPO `BUDGET_06`의 모든 Seed 모델6개를 고정했다. 후보 기준은 결과를 보기 전에 코드에 정의했다.

| VALIDATION 지명 후보 | 평균 비용 비율 | 평균 정시 충족률 차이 | 결과 |
|---|---:|---:|---|
| ML BUDGET_18 | 0.9899 | +7.87%p | 평균 개선만으로는 부족: 일부 비용·서비스·품목 기준 미달 |
| PPO BUDGET_06 | 8.6807 | −13.47%p | 비용·서비스 및 Seed 편차 기준 미달 |

비용은 같은 조건의 수학적 전략=1인 비율이고, 서비스 차이는 %가 아닌 %p다. 가족별 연구 후보만 지명했으며 승격 후보는 없다.

## 2. 신규 104주 홀드아웃 결과

홀드아웃은 선택 파일 Hash를 고정한 뒤 별도 프로세스에서 생성했다. 2029-07-02~2031-06-29는 합성 Calendar이며 미래 운영 실측이 아니다. 세 데이터 Seed 각각에서 모델 Seed 3개×시나리오 4개의 지표를 동일 가중 평균했다.

| 전략 | TEST 자료 Seed | 평균 비용 비율 | 평균 정시 충족률 차이 | Gate |
|---|---:|---:|---:|---|
| ML | 81001 | 1.0604 | +7.17%p | 미달 |
| ML | 81002 | 1.0029 | +8.30%p | 미달 |
| ML | 81003 | 1.0746 | +6.72%p | 미달 |
| PPO | 81001 | 16.8187 | −15.88%p | 미달 |
| PPO | 81002 | 12.0583 | −10.70%p | 미달 |
| PPO | 81003 | 15.9688 | −15.91%p | 미달 |

ML의 평균 서비스 개선에도 최악 Cell은 −21.06%p 저하했다. PPO 최악 Cell은 비용126.95배, 서비스−77.74%p로 나타났다(각 최악 값이 같은 Cell이라는 뜻은 아님). Seed별·시나리오별·품목별 수치를 원본 JSON에 남겨 평균에 가려지지 않도록 했다.

기본104주12개 조건과 종료 민감도4개 조건, 총16개 조건을 실행했다. 선정 모델96회와 수학적/무신규공급32회, 합계128개 Episode·62,400 Item-week다. 같은 조건의 초기 상태·수요·지연·종료 규칙을 공유했다.

## 3. 종료 효과·분포 이탈

TEST Seed81001 / 모델 Seed101 / BASE의 사례:

| 전략 | 기간·종료 BO 비용 | 총비용 | 정시 충족률 | 종료 BO |
|---|---|---:|---:|---:|
| PPO | 52주 / Source | 213,715 | 32.74% | 1,588 |
| PPO | 104주 / Source | 1,115,680 | 16.42% | 4,293 |
| PPO | 104주 / 0 | 1,072,750 | 16.42% | 4,293 |
| ML | 52주 / Source | 17,714 | 98.16% | 0 |
| ML | 104주 / Source | 35,001 | 99.03% | 0 |
| ML | 104주 / 0 | 35,001 | 99.03% | 0 |

PPO의 해당 사례는 종료 벌점을 제거해도 BO·서비스 결과가 같다. 따라서 이 실패를 종료 벌점만의 효과로 설명할 수 없다. 이는 사후 진단이며 모델/선정 규칙은 수정하지 않았다. 다른 Seed/조건 결과도 JSON에서 확인한다.

평가 전체에서 소비 Feature clipping이 ML4,680/23,400, PPO11,273/23,400 Decision에 나타났다. 0.7.0 정규화/관측 계약을 유지한 상태에서 SERVICE_99와 장기 상태·BO 분포의 이탈을 확인했다. 인과 원인은 별도 TRAIN/VALIDATION Ablation으로 확인해야 한다. 물리 용량 초과는0이지만 이 합성 자료에는 실제 용량 상한이 없다.

## 4. 고정 증적과 재현 범위

- [선정·전체 모델·Optimizer/VALIDATION 증적](evidence/learning-stability-selection-20260903.json): `db4d8f24a85e65876b7eba60811208900bbebf3b714f883c7a787b1cb3bbaa76`
- [새 홀드아웃·종료 민감도 증적](evidence/learning-stability-holdout-20260903.json): `6766a53615a263eb1ffc9a8378b40cc1081df0a2c35efa9a19b1864f6653fce8`

위 값은 파일 전체 바이트 Hash가 아니라 자기 `content_hash`를 제외한 Canonical JSON SHA-256이다. Holdout은 선정 Hash를 참조하며 선택·모델을 바꾸지 않는다. 원본0.7.0 실험 파일도 보존했다. 로컬 요약 JSON이며 Artifact 서비스 봉인·TB_IO 이중 기록 완료는 아니다.

## 5. 코드 검증

실제 InventoryEngine 경로에 변경22개 파일을 반영한 뒤 아래 검증을 다시 실행해 통과했다. 점검 범위의 기존 미변경139개 파일은 바이트 단위로 동일하다. 기존0.7.0 단위184건도 변경 전 별도로 재실행해 통과했다. 패키지 메타데이터와 모듈 `__version__`의 일치도 계약 테스트로 확인한다.

| 검증 | 결과 |
|---|---|
| 단위 테스트 | 200건, 기존184+신규16; 실제 대상51.21초 |
| 계약 테스트 | 16건, 기존14+신규2; Producer 계약 포함 |
| 오프라인 CLI 통합 | 20건, 기존17+신규3; 실제 대상25.29초 |
| 합계 | 236건, Skip 없음; 외부 DB 테스트는 미실행 |
| Ruff | src/tests/examples의103개 Python 파일 Lint·Format 통과 |
| Mypy | 신규 계약/실행기/예제6개 파일 통과; 기존 Network까지 전체 타입 검증했다는 뜻은 아님 |
| Bandit | 신규 코드 범위 통과 |
| Coverage | Stage 단위200건 실행, 신규 안정성 계약/모듈280 Statement·76 Branch 100% |
| 실제 재현 | Wheel과 설치된 소스에서 각각 TEST81001/BASE104주의 수학적·무신규공급·ML/PPO Seed6개, 총4,160 Item-week 재실행 → 저장된 모든 요약/World/Decision Hash와 동일 |
| 재현 방어 | 위 재실행은 Torch·NumPy·asyncpg import를 차단한 별도 프로세스; 각 원장 수량 보존과 MOQ/Lot도 재검증 |
| 문서/계획 | 변경 Markdown 링크·실제 CLI 명령·JSON 계획 의존관계 확인 |

학습6회와 전체 홀드아웃은 Stage에서 실제 실행했으며 결과·코드를 대상에 그대로 복사했다. 대상에서 전체 학습을 다시 수행한 것은 아니다. Unit의 실행기 재현 테스트는 저장된 실제 학습/평가 증적을 주입하며, 실제 World 재실행 검증과 구분한다. `oma-docs` CLI/lychee가 없어 수동 참조 검증으로 대체했고 URL 전체 검사는 하지 않았다. `.agents/`, DB, 다른 저장소와 공용 Runtime은 변경하지 않았다.

빌드 Wheel: `dsio_inventory_engine-0.8.0-py3-none-any.whl`, 파일 SHA-256 `c6579154ee48852ba9f6cce211f5d54f7980cd3e834620950037bc5e72e9137e`. 현재 Wheel은 임시 빌드 디렉터리에 있으며 배포하지 않았다.

## 6. 남은 작업 순서

1. **다음 작업 — InventoryEngine 실제 Source 계약·읽기 Adapter:** Forecast 발행 이력, 수요 실현값, 공급 확약·지연·비용·Cut-off 의미를 실제 Source와 대응한다. 현재 계산/학습 코드는 유지하고 먼저 무변경 읽기 및 계약 테스트로 확인한다. 누락된 운영 의미는 외부 검토 대기다.
2. **다음 작업 — ML/PPO 개선 실험:** TRAIN/VALIDATION에서 정규화 범위·무수요 품목·PPO 행동/보상/학습량 영향을 분리한다. 수학적 기준을 유지하며 운영 승격하지 않는다. 이번 두 TEST를 재사용한 튜닝 없이 새 Revision·홀드아웃으로 재검증한다. 1번과 기존 계약을 공유하는 범위에서 독립 진행 가능하다.
3. **다음 작업 — 공통 Run·모델 Evidence 연결:** dsai-platform Backend와 InventoryEngine에 실제 Run/모델 승인/입력 Binding을 연결하고 Artifact/DB 이중 기록 계약을 구현한다. 공통 계약 확정 후 연결 테스트는 직렬 진행한다.
4. **승인 필요 — 개발 DB 적용·Runtime 배포·실제 E2E Write:** 대상 PostgreSQL/공용 Runtime과 변경 범위를 특정해 별도로 승인받는다. 현재 수행하지 않았다. P0-14/18/19 보류도 유지한다.

InventoryEngine은 Git 미초기화 상태여서 Commit/Push/MR을 하지 않았다. 다른 저장소·DB·Runtime·Secret은 변경하지 않았다.
