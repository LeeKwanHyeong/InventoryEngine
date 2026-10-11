# 품목별 정책 탐색 로컬 검증 기록

기준일: 2026-10-06. [업무·계산 계약](IO_POLICY_SEARCH_CONTRACT.md)의 세 작업을 검증했다.
검증은 개발 Shadow·로컬 API·Offline CLI 범위다. 실제 DB 또는 배포 환경의 성공 기록이 아니다.

## 1. 코드 기준선과 변경 경계

| 저장소 | 브랜치 | 변경 전 HEAD |
|---|---|---|
| InventoryEngine | `inventory_engine_dev` | `53869a9a05d04d5ca7174e998f75e54a9df63a50` |
| dsai-platform | `codex/inventory-psi-strategy-v2` | `2b5a0d31835f732b8adb8e8b586436b6890d0cac` |

이번 변경은 미커밋 상태다. 기존 Source Producer·Run Lifecycle 미커밋 변경과 개인 보고서를 보존했다.
DemandEngine은 수정하지 않았다. Migration 074~077을 재실행하지 않았고 078도 적용하지 않았다.
Git 원격 최신성 재조사, DB 접속·Write, 공용 Runtime 배포, Commit·Push는 수행하지 않았다.

## 2. 검증 결과

| 검증 | 결과 | 범위와 한계 |
|---|---|---|
| InventoryEngine 전체 Unit·Contract·Integration | 636 passed, 22 skipped, 631 subtests passed | 현재 환경의 조건부 Optional/DB 테스트는 제외. Offline 결과를 실제 DB E2E로 간주하지 않는다 |
| 신규 정책 탐색 집중 회귀 | 23 passed | 수작업 Golden, Hash, Gate, 다품목 독립 계산, 실패 격리, 로컬 저장·CLI |
| Platform Inventory 관련 Unit/API 및 Demand Binding | 314 passed | In-memory Repository/TestClient. 기존 Starlette 422 코드 Deprecation Warning 23건 |
| Frontend 후보 탐색·7축·Matrix·VED 관련 테스트 | 16 passed | 후보 검증, JSX 통합 위치, SSR 접근성 구조. 실제 브라우저 클릭 E2E는 아님 |
| Frontend 소스 TypeScript 검사 | 통과 | 기존 `.next` 생성 캐시를 제외한 소스 검사 |
| Ruff / Format | 변경 Python 파일 통과 | 기존 무관한 전체 파일을 재작성하지 않음 |
| JSON Schema | 25개 유효 | 신규 Config 두 저장소 바이트 동일; 실제 성공 결과·빈 성공 거부 검증 포함 |
| Git diff whitespace | 두 저장소 통과 | Commit을 수행한 것은 아님 |

일반 Frontend TypeScript 검사에는 기존 `.next`/`.next/dev` 생성 타입이 삭제된 Portal Route를
참조하는 오류가 남아 있다. 생성 캐시를 임의로 삭제하지 않고 임시 tsconfig에서 해당 캐시만 제외해
전체 소스 TypeScript 검사를 수행했다. 일반 빌드 전체가 성공했다고 표현하지 않는다.

주요 재현 명령:

```text
# InventoryEngine (PYTHONDONTWRITEBYTECODE=1, PYTHONPATH=.:src:tests:tests/support)
pytest -p no:cacheprovider -q tests/unit tests/contract tests/integration
pytest -p no:cacheprovider -q tests/unit/test_policy_search.py tests/integration/test_policy_search_offline.py

# dsai-platform
pytest -p no:cacheprovider -q tests/unit/platform/test_inventory*.py tests/unit/platform/test_demand_inventory_run_binding.py

# dsai-platform/frontend
node --test tests/inventory-policy-search.test.mjs tests/inventory-seven-axis-configuration.test.mjs tests/inventory-policy-matrix-presentation.test.mjs tests/inventory-ved-version-selection.test.mjs
```

실행 Python은 dsai-platform `backend/.venv`의 Python 3.12다. 기존 환경·의존성 선언을 바꾸지 않았다.

## 3. 독립 수작업 Golden

13주 과거 수요가 매주 10 EA, BOH 10 EA, 물리 Lead Time 7일, 미래 수요가 3주간 매주 10 EA다.
RMOS=0, EMOS=0.3, CSL=0.95, 점검주기 1주를 사용한다.
`Dm=10×52/12`, `ROP=10`, `Q=13`, `S=23`이며 주기 목표 S의 R+L 하한 20보다 크다.
비용은 보관 1/EA·주, 고정 발주 5/건, 변동 발주 2/EA의 명시적 개발 가정이다.

| 후보 | 주말 EOH | 현재 수요 충족량 | 발주횟수/총량 | 평균 EOH | 총비용 | 최소 충족률 0.95 만족 |
|---|---|---|---|---|---|---|
| Baseline | 0, 0, 0 | 10/30 | 0/0 | 0 | 0 | 아니오 |
| ROP_FIXED_Q | 0, 3, 0 | 23/30 | 1/13 | 1 | 34 | 아니오 |
| PERIODIC_ORDER_UP_TO | 0, 3, 3 | 30/30 | 2/23 | 2 | 62 | 예 |

이 숫자는 최적 정책을 선택했다는 뜻이 아니다. 실제 PSI Row에서 지표를 산출하는 작은 Golden이다.
짧은 Horizon, Forecast Shortage 비이월, 종료 잔존가치 없음이라는 현재 계약을 그대로 따른다.

## 4. 추가 방어 검증

- 두 품목의 초기재고가 10/100 EA일 때 첫 품목만 보충하며 두 번째 품목은 90→80→70으로 이월된다.
  품목·후보별 주문 ID와 공급 대기열이 섞이지 않는다.
- SDE P90 21일은 보호기간에 적용하지만 물리 납기는 계속 7일이다.
- Shadow CSL 0.5/0.95는 Matrix 값으로 덮어쓰지 않는다. VED V 하한 0.99는 모두 적용한다.
- FSN BLOCK은 HOLD, REVIEW는 증적 계산만 허용하며 자동 주문·게시가 불가능하다.
- MOQ·배수·용량 조합이 불가능하면 공통 Guard가 주문을 거부하고 사유를 기록한다.
- 목표 상한 위반 후보 하나는 FAILED로 격리하고 다른 성공 후보와 Baseline은 보존한다.
- 무수요 비율은 null, 미연결 비용은 null이며 0으로 위장하지 않는다.
- 분류 Hash/ID 변조, 이력 부족, 승인 Override, 고아 품목 예외를 명시적으로 차단·제외한다.
- 같은 결과 로컬 저장은 멱등이며 파일 변조와 빈 성공 Child는 Reader/Verifier·Schema에서 거부한다.

## 5. 교차 저장소 정합성

공유 Golden Search Hash:
`6d2425d38ac012e416d1bdfaf378f9f66d823c904f5b55adb2a49cfdb31b7547`.
동일 Golden Search의 후보 목록·Candidate ID가 두 저장소에서 일치한다.

Engine 테스트의 7축 Config + Search 전체를 Backend 모델로 검증한 Hash:
`671fb650f8f2c82735d5f56e54200904cc7cd2e9d99f9f45d64f24ad0f6adbc3`.
신규 2.1.0에서는 축별 숫자 타입을 정규화해 `28`과 `28.0`을 동일하게 처리한다.
기존 1.1.0/2.0.0 Hash는 변경하지 않는다.

Config 2.1.0 Schema Hash:
`f39e51436cc6efdec2a4c9cb5a9f1508db7aa269f09a3b0d05c0680f51723dae`.
Backend와 Engine Schema는 바이트까지 동일하다. Schema의 모양 검증과 Python의 수치 범위·
Cartesian 후보 수·중복 검증을 함께 사용하며 Schema 단독으로 모든 업무 제약을 검증했다고 주장하지 않는다.

## 6. 남은 작업

품목별 후보 선택기는 후속 로컬 작업에서 구현했다. [선택기 검증 기록](IO_POLICY_SELECTION_VERIFICATION.md)을 따른다.
장기/민감도 평가 → Platform Run·Result Bundle/결과 화면 연결 → 별도 승인 후
개발 배포·실제 DB E2E 순서다. 이번 로컬 파일 보존은 PostgreSQL Evidence 게시 완료가 아니다.
현재 Run 접수는 2.1.0을 지원하지 않으므로 신규 설정을 기존 자동 Run에서 조용히 무시하지 않는다.

기존 Mathematical/PPO·Source Producer 개발 기준선은 유지하며 독립 Commit·Push도 후속 범위다.
