# Canonical 입력·Cut-off·Baseline PSI 검증 기록

기준일: 2026-09-03. 대상: `/Users/igwanhyeong/PycharmProjects/InventoryEngine`, Package `0.2.0`, Python `3.12.10`.

판정: **로컬 구현 범위 PASS**. 운영 활성화·실제 ERP 정확성·공통 Run/Evidence 영속화의 합격 판정이 아니다.

이 기록은 0.2.0 검증 당시 상태를 보존한다. 공통 PSI·세 전략 계약과 로컬 Recommended PSI가 추가된 현재 상태는 [0.3.0 검증 기록](IO_REPLENISHMENT_STRATEGY_VERIFICATION.md)을 참조한다.

## 1. 실행 결과

| 검증 | 결과 |
|---|---|
| Unit | 58건 통과. 기존 Network 27건 포함 |
| Contract | 3건 통과. 기존 Producer 호환 1건, Canonical Schema/Golden 2건. Skip 없음 |
| Offline CLI Integration | 3건 통과. 실제 예제 파일→PSI, 미봉인 거부, 배포 Company 불일치 거부 |
| 독립 Golden | 14개 사례, 45 PSI Row × 11개 명시 수량. 재고/주문/Forecast 보존식과 연속 이월 검증 |
| Coverage | Unit 기준 전체 Package 96%(Statement/Branch 합산 표시). Canonical·Cut-off·입력 준비·Baseline 계산 모듈 각각 100%; 값 파서 93%, CLI 70% |
| Ruff | 전체 Source/Test Lint·Format 통과 (`0.15.22`) |
| Mypy | 신규 핵심 6개 모듈 통과 (`2.3.1`, follow-imports=silent). 전체 기존 Network 모듈의 엄격 타입 검증을 주장하지 않음 |
| Bandit | 신규 핵심 6개 모듈 및 CLI 검사에서 Finding 없음 (`1.9.4`) |
| Package | Wheel 빌드·Wheel에서 직접 Import해 동일 PSI 결과 산출 확인 |

Coverage는 오류 주입의 완전성이나 업무 정확성을 대신하지 않는다. 공개 UseCase의 잘못된 입력 거부 테스트에서는 계산 함수를 감시해 PSI가 호출되지 않음을 확인했다. 실제 DB 읽기/쓰기는 이 작업에서 수행하지 않았다. 이전 Network DB 통합 5건은 이전 작업의 기준선이며 위 64건에 더해 집계하지 않는다.

## 2. Runtime 검증 증거

| 기능 | 방법 | 기대 | 관측 |
|---|---|---|---|
| 정상 Baseline | 실제 JSON 파일을 CLI에 전달 | EOH 30→10→10 | 일치, Exit 0 |
| 미봉인 Snapshot | COLLECTING 입력 파일을 CLI에 전달 | PSI 이전 거부 | UNSEALED_INPUT, Exit 2, PSI Row 없음 |
| 다른 Company | 프로세스 Company와 요청 Company 불일치 | 거부 | DEPLOYMENT_COMPANY_MISMATCH, Exit 2 |
| DB 비의존 | CLI 환경에 사용할 수 없는 DSN 설정 | 로컬 계산 성공 | 성공. DB 접속 기능을 호출하지 않음 |
| 일요일 출고·월요일 ERP 확정 | 이벤트 발생/확정/수집시각이 다른 Fixture | 새 Revision 필요 | LATE_POSTING_DETECTED와 Event Evidence |
| 시작재고 과대계상 | 전기90, 현재100, 승인 조정0 | 계산 전 실패 | EOH_BOH_RECONCILIATION_FAILED, 차이10 보존 |

[관측 CLI 전체 응답](evidence/baseline-cli-observed-20260903.json)에 Canonical 입력, Hash/Binding, 대사, 입고 포함/제외, Forecast 소비와 PSI를 보존했다. 이 파일은 실행 증적이며 **Golden 정답을 생성한 파일이 아니다**. 테스트 정답은 별도 [수작업 Golden](../../tests/fixtures/GOLDEN_BASELINE.md)이다.

## 3. 보안 정적 검사 해석

전체 Source Bandit 스캔에서는 기존 `infrastructure/postgresql/network_snapshot.py` 14~16행의 SQL 문자열 구성 3건이 B608(Medium/Low confidence)으로 표시됐다. 해당 문자열의 Column은 코드에 고정된 HEADER/NODE/LANE 상수이고 사용자 Revision ID는 `$1` Parameter로 전달된다. 사용자 문자열을 SQL Identifier나 Predicate에 보간하지 않아 이 세 건은 검토 후 오탐으로 분류했다. 숨김 주석이나 전역 검사 제외를 추가하지 않았다. 전체 스캔 Exit Code가 0이었다고 기록하지 않는다.

신규 Core에는 DB/HTTP/Shell 호출이 없다. Source 범위·UOM·유한 Decimal·필수 필드·중복 JSON Key·행 수·Calendar·입력 Hash를 검증한다. 출력에는 입력 업무 데이터가 포함되므로 향후 외부 API는 별도 인증/권한·마스킹 계약이 필요하다.

## 4. 한계와 후속 경계

- Source가 제공하지 않은 미수집 ERP 거래를 탐지하지 못한다. Watermark·이벤트·이전 Snapshot을 실제 Source에서 증명하는 Adapter와 봉인 저장은 후속 작업이다.
- Late Posting 검출·거부를 구현했으며, 새 Snapshot 생성/대체·조정 반영·복구 E2E는 아직 구현하지 않았다.
- Reserved는 명시된 비가용 보류 재고만 지원한다. 고객 주문 배정/예약 해제는 미구현이다.
- 정책은 구조·유효성·Grain 검증까지다. 안전재고/ROP/MOQ·발주배수 권고 계산과 Recommended PSI는 다음 작업이다.
- 합성 TGSM Golden은 수작업으로 만든 작은 Fixture다. 52주 Warm-up Generator·실제 운영 재고 정확성은 검증하지 않았다.
- 주간 집계이므로 주중 입출고 순서에 따른 정확한 품절 시각을 보장하지 않는다.
- Local JSON 응답은 `TB_IO_*` 또는 봉인 Artifact가 아니다. `evidence_persisted=false`, `artifact_sealed=false`, `run_claimed=false`를 유지한다.
- 당시 InventoryEngine은 Git 미초기화 상태였다. 현재 dsai-platform 기준선은 `develop`이며 해당 검증에서는 Commit/Push/Migration/배포를 하지 않았다.

## 5. 재검증 방법

[README](../../README.md)의 Unit·Contract·Offline CLI·Coverage 명령을 사용한다. 개발 도구는 `.[dev]`, 실제 Network 조회 의존성은 `.[postgres]`로 분리했다. 이번 검증 도구는 임시 격리 환경에 설치했으며 사용자 Runtime 환경과 Secret을 변경하지 않았다.

`oma-backend`의 계약/UseCase 경계와 `oma-qa`의 독립 기대값·오류 주입·실제 CLI 검증을 적용했다. 공유 보조 참고 폴더가 설치돼 있지 않아 스킬 본문의 실행 프로토콜/체크리스트와 프로젝트 규칙을 기준으로 검증했고, 보호된 `.agents/` 대신 이 문서에 증적을 기록했다.
