# Source 읽기 Adapter 검증 — 0.9.1

기준일: 2026-09-03. [소비 계약·매핑·준비상태](IO_SOURCE_READ_CONTRACT.md)를 따른다. 구현 완료와 실제 운영 입력 준비를 구분한다.

## 0.9.1 — DB 연결 전 입력 검증 보강

- 0.9.0에서는 요청 Hash 불일치나 뒤쪽 BOH/Cut-off 오류가 최종 거부되더라도 그 전에 DB 연결·Forecast 조회가 가능했다. 변경 전 회귀 테스트에서 이를 재현했다.
- `prepare_source`는 8개 Source를 먼저 읽고 기존 UseCase 전체 검증을 완료한다. DB 대조를 요청한 경우에만 검증된 Forecast 메모리 Snapshot으로 연결·조회한다. 파일을 다시 읽거나 Source/Canonical Hash를 재발급하지 않는다.
- 내부 `PostgresVerifiedSourceReader`는 제거했다. CLI·교환 Schema·UseCase·수량/PSI·PostgreSQL SQL은 변경하지 않았다. DB 조회 자체는 여전히 읽기 전용이다.
- 단위 **229건**, 계약 **19건**, 오프라인 통합 **23건**, 총 **271건** 통과. 신규 사전검증 테스트 6건에는 14개 오류 하위 사례가 포함된다. Hash·Run·Scope·봉인·누락·고아·Reserved·Watermark·EOH–BOH 오류 시 연결과 조회가 0회임을 확인했다.
- 정상 DB 대조와 오프라인 준비의 전체 반환값 일치, 검증 후 파일 변경 시 검증된 Snapshot 유지, DB 불일치 시 오류 전파·연결 종료를 검증했다. DB 대조는 이 회귀 테스트에서 Mock을 사용한다.
- Ruff 검사·포맷 117개 파일, 변경 생산 Module 2개의 Mypy·Bandit 통과. 해당 2개 Module의 Coverage는 42 Statement/4 Branch, 100%다. 전체 패키지 Coverage나 운영 인증·ERP 정확성 보증이 아니다.
- 0.9.1 Wheel을 빌드하고 Wheel에서 직접 Source→Canonical→PSI를 실행했다. `asyncpg/torch/numpy` import 차단 상태에서 EOH `30→10→10`과 아래 0.9.0의 두 Hash가 그대로 일치했다.
- [0.9.1 로컬 검증 요약](evidence/source-preflight-verification-20260903.json)을 추가했다. 이번 보강에서는 **실제 DB 접속·변경·Runtime 배포를 하지 않았다**. 아래 개발 DB 수치는 0.9.0 당시 증적이며 새로 수행한 E2E 결과가 아니다.

## 0.9.0 — 완료한 범위

- Source Request/Snapshot DTO, Schema 2개, 로컬 파일 Reader, Parameterized PostgreSQL Reader, Source→Canonical UseCase, `prepare-source` CLI를 추가했다.
- Run/ID/Hash·Scope·Scenario·통계량·주차를 고정하고 Source/Canonical Hash를 모두 반환한다. 현재 DB 값으로 봉인을 새로 만들지 않는다.
- 원본 행과 미확약 공급 제외 사유를 JSON으로 반환한다. DB에 없는 Legacy Collector, Artifact/DB 저장, 봉인·Run Claim·PSI 자동 실행은 구현하지 않았다.

## 0.9.0 — 로컬 검증 기록

- 단위 223건·계약 19건·오프라인 통합 23건: 총 265건. 기존 236건에 단위 23·계약 3·오프라인 통합 3건 추가.
- 기존 독립 Golden 14개/45 PSI Row 유지. 매핑 Golden EOH `30→10→10`, 정책 DAY/WEEK·FRACTION/PERCENT 변환, 목표재고 MAX_QTY 분리, 미확인 Due-in 40개 제외 확인.
- 독립 52주 Generator BOH/Prior/공급의 계보·수량을 유지한 Source→Canonical 호환 테스트 통과.
- Source 행 순서 변경·새 IO Retry Run ID에도 Canonical Hash 동일. ID/Hash/행수/Parent Binding·누락·고아·미봉인·소수 EA·정책 NULL·Coverage·Cut-off/EOH–BOH/Reserved 오류를 차단.
- 로컬 파일 Path Traversal·Symlink·크기 상한, SQL 값 Parameter Binding·읽기 전용 트랜잭션·다른 Run/Scope·조회 초과·실패 시 연결 종료 검증.
- Ruff, 신규 6개 Python Module Mypy/Bandit 통과. 신규 Source Module Coverage 275 Statement/40 Branch, 100%. 이는 코드 실행 범위이며 ERP 의미·승인 인증·운영 성능 보증이 아니다. 전체 패키지/기존 CLI Coverage 수치가 아니다.
- 0.9.0 Wheel 빌드 후 Wheel 자체를 import하여 예제 Source→Canonical→Baseline EOH `30→10→10`을 재검증했다. `asyncpg/torch/numpy` import를 차단한 오프라인 경로가 통과했다. Canonical Hash는 `66b43660114db375d7000f0731b7d9f6d8a6828da9eaf34492a846fbd0523459`, Source Binding Hash는 `81f86d67f26b567d2fc7e5b42901e829d5913227f3357994fc1385c1816c9785`다.

## 0.9.0 — 실제 개발 PostgreSQL 읽기 전용 2건 통과

대상 `192.168.0.46:5432/dsai`의 개발 환경 설정을 확인하고 인증정보는 메모리에서만 사용했다. `default_transaction_read_only=on`, 개별 `repeatable_read/readonly`, 10초 Statement Timeout을 적용했다.

[고정 Run·Selector·수치 증적](evidence/source-readonly-verification-20260903.json):

- Run `91930a93-8601-4b71-9736-1d404f44c6ff`, DSE/C100/V100, `202510~202512`, P50 진단. 운영 기본 통계량 선택이나 Snapshot 생성이 아니다.
- 4,270품목·12,810 Forecast Row를 두 번 읽어 Hash 일치. Wrong Project·Wrong Site·없는 Run 차단.
- 현재 Master 7,000행·Calendar 3주. MOQ·Lot·STOCK_LT·SVC_LV는 각각 7,000행 모두 NULL. 진단 Forecast 중 소수 EA 9,213행.
- 현재 Master 대비 고아 Forecast 품목 0. 과거 Master Revision/전체 Universe Coverage를 검증한 것은 아니다.
- 재고/입고/고객 주문·정책 Source와 Forecast 출력 봉인이 미준비이므로 `canonical_ready=false`, `snapshot_sealed=false`.

실제 asyncpg Record는 값 순회 방식이므로 Run Scope를 명시적 필드 목록으로 대조하고 회귀 테스트를 추가했다. 전체 Forecast 조회는 행 상한에 걸려 요청 주차 범위를 명시했다. 초과 시 실패하며 조용한 잘라내기·최신 결과 탐색은 없다.

재현 테스트는 `tests/integration/test_source_readonly.py`다. `IO_SOURCE_READONLY_INTEGRATION=1`과 운영자가 지정한 `IO_POSTGRES_DSN`이 있어야 실행한다. 저장된 진단 Hash와 다르면 자동 갱신하지 않고 실패한다.

## 변경·승인 경계와 다음 작업

| 대상 | 변경·제한 |
|---|---|
| InventoryEngine | 0.9.0 Source 기반에 0.9.1 사전검증·회귀 테스트·문서 반영. 독립 Git 미초기화, Commit/Push/MR 없음 |
| DemandEngine `demand_engine_v3` | Producer 코드 읽기만 수행. 수정·재배포 없음 |
| dsai-platform `dsdm_engine_studio_dev` | 기존 설정 참조만 수행. 기존 변경 파일 보존 |
| PostgreSQL/Neo4j | 0.9.0 당시 PostgreSQL 읽기 전용 검증. 0.9.1에서는 DB 접속 없음. Migration·DML·Full Load·E2E Write·Neo4j 변경 없음 |
| 공용 Runtime | 변경·배포 없음 |

Backend·DB 스킬에 따라 I/O와 UseCase, Source 의미와 계산 정책을 분리했다. QA 스킬에 따라 기존 Golden 회귀·실패 경로·실제 Driver 조회를 분리해 검증했다.

1. **다음 작업 — Demand/Studio Export 접점 연결:** Output Snapshot·공유 Calendar/Master를 전달하고 대표 통계량·소수 EA 처리 기준을 명시한다. P50/반올림/BASE_MONTH를 임의 결정하지 않는다.
2. **외부 작업 대기 — 실제 재고·주문·정책:** Cut-off/Watermark/Reserved·Coverage·공급 확약·정책 NULL 해결. 합성 BOH/정책으로 개발 지속 가능.
3. **다음 작업 — 공통 Run/Cycle·Evidence:** Source/Canonical 두 Binding과 원본·제외 사유 저장 연결. ML/PPO 개선은 DTO를 보존하면 독립 진행 가능.
4. **승인 필요 — Migration/실제 E2E DB Write/공용 Runtime 배포:** 별도 대상별 승인. P0-14/18/19 보류 유지.
