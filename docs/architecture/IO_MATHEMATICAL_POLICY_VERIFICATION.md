# 수학적 정책 산출기·권고 Golden 검증 기록

기준일: 2026-09-03. 대상: `/Users/igwanhyeong/PycharmProjects/InventoryEngine`, Python 3.12.10, 패키지 `0.4.0`.

판정: **수학적 기준 전략의 로컬 구현 PASS**. 실제 Source의 정확성, 서비스수준 달성, 운영 승인·DB 저장·배포의 합격 판정은 아니다.

## 1. 실행 결과

| 검증 | 결과 |
|---|---|
| 단위 테스트 | 111건 통과. 기존 86건 + 수학적 정책/경계 25건 |
| 계약 테스트 | 7건 통과, Skip 없음. 기존 5건 + 정책 Schema/DTO·실행 1.1 호환 2건 |
| 오프라인 프로세스 통합 | 7건 통과. 기존 4건 + 실제 수학적 CLI 3건 |
| 수작업 Golden | 14개 정책/권고 사례, 42개 PSI Row. 기대값은 구현에서 생성하지 않음 |
| 기존 회귀 | Baseline 14개/45 Row와 HOLD·세 전략 Probe·Cut-off 테스트 유지 |
| Coverage | Unit+Contract 기준 전체 98%(Statement/Branch 합산). 추가 정책 계약·입력 Gate·산출·Adapter는 측정된 Line/Branch 100% |
| Ruff | 0.15.22, Source/Test/Probe Lint·Format 통과 |
| Mypy | 2.3.1, 변경 핵심 10개 Source 파일 통과. `--follow-imports=silent --ignore-missing-imports` 사용; 선택 의존성 asyncpg 타입 검증은 제외 |
| Bandit | 1.9.4, 정책/전략 계약·권고 모듈·CLI 검사 Finding 없음 |
| Wheel | 0.4.0 빌드 성공. 소스와 Wheel로 실행한 수학적 CLI 전체 JSON 응답 동일 |

위 125건은 이번 실행 결과다. 이전 개발 Network 읽기 전용 DB 통합 5건을 합산하지 않는다. 이번에 DB 연결·데이터 변경·Model Training을 수행하지 않았다. Coverage 수치는 모든 분기 조건/입력 조합·업무 적합성·성능의 완전성을 증명하지 않는다.

## 2. 실제 CLI 관측

```bash
IO_COMPANY_CD=DSE IO_ENVIRONMENT=DEVELOPMENT PYTHONPATH=src python3 -m dsio_inventory_engine recommend-mathematical --request examples/mathematical_input.json
```

| 항목 | 관측값 |
|---|---|
| 결과 | `RECOMMENDED_PSI_COMPUTED_LOCALLY`, 결과 계약 1.1.0 |
| 정책 | 안전재고 4, ROP 14, 목표재고 24 |
| 권고 주문 | 14, 10 |
| EOH | 0 → 4 → 4 |
| 정책 Coverage | COMPLETE |
| 정책 Evidence Hash | `66b084c527ea752f514fb2d8d5b24ea2ca0f12ca65d0ae5abebf2d0985318672` |
| PSI Hash | `ac5ea622275b81181c192a8780c3bff1bada9b2d4324c25453790c713599326d` |
| 운영 효과 | DB Write·Run Claim·Artifact 봉인·Evidence 영속 저장·승인 인증 모두 false |

예제는 수작업 합성 입력이다. 이 결과는 실제 알고리즘 실행이지만 실제 운영 데이터나 52주 재고 Generator 실행은 아니다. 기대값과 계산 근거는 [수학적 Golden](../../tests/fixtures/GOLDEN_MATHEMATICAL.md)에 별도로 기록했다.

## 3. 위험 기반 검증

- 평균/표본분산/정규 분위수, 26주·모집단분산, EA/KG 순차 올림, 0일/1일/8일/28일 Lead Time을 확인했다.
- ROP 경계에서 주문하고 미도착 확정/권고 공급을 Position에 포함한다. 실제 BO를 해결하며 Forecast Shortage를 이월하지 않는다.
- Source 기존 목표를 Python 결과로 덮어쓰지 않는다. Source/Python/Effective와 차이, 승인 레코드를 구분한다.
- Override 만료, Python 우선, 이력 부족 시 Source/명시 Legacy Fallback, 승인 정책이 없을 때 제외 및 다품목 격리를 확인했다.
- Override가 MOQ·용량·승인 목표 범위를 우회하지 못하고 제약 충돌 시 자동 Fallback하지 않음을 확인했다.
- 누락 주차와 0수요를 구분한다. 중복·고아·UOM·범위 밖 이력·검열된 판매량·부분 주차를 거부한다.
- 소수 초를 포함해 Cut-off 이후 정보·승인을 거부한다. 기존 Cut-off 오류는 정책 산출 이전에 차단한다.
- 입력/Configuration/추가 Snapshot Binding 불일치를 거부한다. 이력·Profile 변경은 새 Hash, 같은 입력 Retry는 같은 정책/PSI/주문 Hash다.
- 외부 Decimal precision·rounding·Inexact trap 변경에도 정상 입력의 결과가 동일했다.
- 실제 CLI에서 미봉인 입력·미래 정보·다른 Company·Production 환경·`--read-postgres`를 거부하고 PSI를 내보내지 않았다.

## 4. 문서·환경 확인과 한계

`oma-backend`의 계약/UseCase 분리와 `oma-qa`의 독립 기대값·실패 주입·실제 프로세스 검증을 적용했다. 일부 공유 보조 참고 파일이 설치돼 있지 않아 스킬 본문의 실행 프로토콜/체크리스트와 프로젝트 규칙을 사용했다. `oma-docs`의 대조 기준을 사용했으나 `oma` CLI가 없어 문서 파일 링크·CLI 명령·계획 의존성을 직접 검증했다. 보호된 `.agents/`는 수정하지 않았다.

임시 작업 복사본과 도구 환경에서 검증하고 변경 대상 파일만 프로젝트에 반영한다. 사용자 Runtime 의존성·Notebook·dsai-platform 기존 작업은 변경하지 않는다. InventoryEngine은 Git 미초기화 상태이므로 Commit/Push/MR을 수행하지 않는다.

정규근사 `(s,S)` 정책은 기준 전략이며 비용 최적해나 목표 Cycle Service Level 달성을 보장하지 않는다. 미래 Forecast의 추세/계절성, 가변 운송시간, 간헐수요 적합성은 후속 공통 평가 환경에서 검증해야 한다. 완전한 7일 Bucket·W0 이전 고정 이력·전체 값 승인 정책을 지원하며 부분 필드 Override 병합·일별 재계산은 이번 범위가 아니다.

## 5. 남은 작업 순서

1. **다음 작업 — InventoryEngine 학습/평가 기반:** 독립 52주 합성 BOH·과거 Forecast/실제 수요·비용/서비스/입고지연 시나리오를 준비한다.
2. **다음 작업 — InventoryEngine ML/DRL:** 공통 평가 계약 이후 분리된 Adapter를 병렬 구현하고 수학적 기준과 비교한다.
3. **다음 작업 / 승인 필요 — Source·공통 Run·Evidence:** 실제 연결과 이중 기록을 구현한다. Migration·실제 DB Write·공용 Runtime 배포는 대상별 승인 후 진행한다.

P0-14/18/19 보류는 유지하며 Multi-Echelon·DSIM은 별도 범위다.
