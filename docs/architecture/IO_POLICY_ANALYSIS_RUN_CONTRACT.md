# Run에 연결된 정책 탐색·선택·평가 결과 계약

기준일: 2026-10-06. 대상: InventoryEngine `inventory_engine_dev`, dsai-platform
`codex/inventory-psi-strategy-v2`. **로컬 구현과 Offline 검증 기준선**이다.
개발 DB 게시·실제 Claim 생성·공용 Runtime 배포 완료를 뜻하지 않는다.

## 1. 완료한 범위와 적용 경계

- 실제 Run의 Mathematical 입력과 같은 Config·분류 Snapshot에서 만든 정책 탐색 결과를 검증하고,
  품목별 선택 및 선택적으로 장기 평가를 Run 부속 Artifact로 봉인한다.
- 탐색 결과는 [정책 탐색 계약](IO_POLICY_SEARCH_CONTRACT.md), 선택은
  [선택 계약](IO_POLICY_SELECTION_CONTRACT.md), 평가는
  [장기 평가 계약](IO_POLICY_SENSITIVITY_CONTRACT.md)을 그대로 사용한다.
- 기존 PSI Result Bundle 및 운영 Mathematical 결과를 개발 선정 정책으로 교체하지 않는다.
  부속 결과의 `operational_policy_approved`, `automatic_publish_allowed`,
  `automatic_order_allowed`는 모두 `false`다. 기존 부모 PSI의 승인 규칙도 변경하지 않는다.
- `PsiRunCommand.policy_analysis_inputs`를 명시적으로 전달했을 때만 연결한다.
  아직 이를 실제 Source에서 자동 생성하는 Producer·Command Resolver 연결은 없다.
- Platform의 실제 Claim은 여전히 Config `1.1.0`/`2.0.0`만 허용한다. Config `2.1.0`을
  자동 Run에서 사용하도록 허용하거나 정책 탐색 기능을 무시한 채 실행하는 변경은 하지 않았다.
- UI와 조회 API는 구현했지만 서비스에 배포하지 않았다. 과거 Run에 부속 결과가 없으면
  `NOT_BOUND`로 표시한다. 과거 장기 실험 결과를 새 Run 결과로 자동 연결하지 않는다.

## 2. 봉인 구조

Manifest 계약은 `inventory-policy-analysis-manifest`, Version `1.0.0`, Contract Key
`inventory.policy_analysis`다. Schema는
[`inventory-policy-analysis-manifest.schema.json`](../../schemas/inventory-policy-analysis-manifest.schema.json)이다.

Reference는 `artifact:inventory:<engine_run_id>:<attempt_no>:policy-analysis`다.
Child Reference는 그 아래 `<role>:<content_hash>`로 고정한다.

| Role | 내용 | 필수 여부 |
|---|---|---|
| `ACTUAL_INPUT` | 원 Mathematical 요청·정규화된 Config·전체 분류 Snapshot | 필수 |
| `SEARCH` | 같은 Run 입력에서 실제 PSI/공통 행동 검증기로 계산한 탐색 결과 | 필수 |
| `SELECTION` | 원 탐색 결과에서 재계산한 품목별 선택 결과 | 필수 |
| `EVALUATION` | 원본 탐색들을 이용한 장기 평가·비용 재평가·종료 효과 | 선택 |
| `EVALUATION_SOURCE` | 장기 평가의 다른 기간·수요 경로에 대한 원본 탐색 결과 | 평가에 따라 필수 |

Manifest에 Run·Attempt·Tenant·Project·Company·Subsidiary·Site, Config Hash,
Site Binding Hash, 분류 Snapshot ID·전체 Hash·Effective Policy Hash,
Canonical Input Hash, Strategy Plan Hash, 부모 PSI Bundle Hash와 Child Hash를 보존한다.

**전체 분류 Snapshot Hash와 Effective Policy Hash는 다르다.** 기존 Run Input Binding의
`INVENTORY_CLASSIFICATION.source_content_hash`는 Effective Policy Hash다. 이를 전체
Snapshot Hash와 혼동하지 않도록 Manifest에 두 필드를 별도로 둔다.

## 3. 연결 및 복구 검증

1. 실제 Run의 봉인된 Mathematical 입력에서 로컬 Shadow 사본을 만들고 탐색 결과를 재계산한다.
   실행 목적만 Shadow로 바꾸며 원 입력·제약·승인 상태는 수정하지 않는다.
2. 전달받은 탐색 결과와 완전히 일치하는지 검사한 후 기존 선택기로 선택을 재계산한다.
3. 평가가 있으면 Reference Case는 이 Run의 탐색 Hash와 같아야 한다. 기준 비용도 Run의
   탐색 비용과 같아야 한다. 다른 비용 Profile은 민감도 Case로만 허용한다.
4. 평가의 모든 Source Hash·Reference를 닫힌 집합으로 확인하고 기존 평가기로 Replay한다.
5. Manifest·Child들을 PSI Bundle과 **같은 기존 Evidence 트랜잭션**에 Append-only 저장한다.
   Durable Outbox에는 `policy_analysis_reference`와 `policy_analysis_content_hash`를 추가한다.
6. 같은 Reference·동일 Artifact는 멱등 처리한다. 다른 Hash·내용은 충돌한다.
7. 복구는 저장된 Bundle·Outbox를 읽는다. Run PSI를 새로 실행하지 않지만, 저장된 분석의
   탐색·선택·평가 Replay는 무결성 검증을 위해 수행한다. 최신 Source를 다시 탐색하지 않는다.

새 PostgreSQL 테이블·Migration은 추가하지 않았다. 저장 대상은 현재 로컬 SQLite Evidence
Adapter다. 운영 Object Storage/PostgreSQL Adapter 및 큰 탐색 결과의 성능 검증은 별도다.
검증 과정에는 여러 차례 Replay가 포함되므로 대규모 품목의 실행 시간·메모리 보장은 하지 않는다.

## 4. Platform 조회와 화면

Platform API는 프로젝트 범위의 Run 조회 권한을 먼저 검사한다. 사용자가 Hash나 Artifact
Reference를 입력하는 화면·API는 만들지 않는다. Backend가 Run ID·Attempt로 정해진 Reference를 읽는다.

- `GET /api/v1/engine-studio/inventory/runs`: 실제 Ledger 목록, 안정적인 정렬·페이지 처리
- `GET /api/v1/engine-studio/inventory/runs/{engine_run_id}/policy-analysis`: 품목별 결과 페이지

Backend는 Hash·Run·Scope·입력 관계를 검증하고 표시용 결과를 만든다. 재고 계산을 다시 하지 않으며
`SEALED_LINEAGE_VERIFIED_NOT_RECALCULATED`로 검증 수준을 표시한다.
Artifact Reader가 미설정이면 `503`, 변조·누락 Child는 `422`다. Manifest 자체가 권위 Reader에서
존재하지 않는다고 확인된 경우만 `NOT_BOUND`이며, 조회 장애를 빈 성공으로 처리하지 않는다.

Engine Studio Inventory 실행 탭은 실제 Ledger를 표시하고 다음을 구분한다.

- 후보별 정책 종류·RMOS·EMOS·검토주기·CSL, 서비스·비용·평균재고·발주횟수·미선정 사유
- 비용 단위·통화·개발 Source·배부 가정과 비용 미연결 상태(0원으로 표시하지 않음)
- 고정 정책의 새 수요 경로 평가, 장기 서비스 미달·Backorder 및 기간·비용별 선정 변화
- 종료 구간 행동 변화·미도착 주문, 미연결·비교 불가 상태
- Run/Manifest/Child의 읽기 전용 Reference와 Hash

`CSL`은 정책 입력이고 수요 충족률은 결과 지표다. 개발 가정 비용은 실제 Landed Cost·관세와
동일하지 않다. 평가가 연결됐어도 Holdout 또는 종료 비교가 없으면 각각 미검증으로 표시한다.

## 5. 최초 연결 검증 (2026-10-06 이력)

- InventoryEngine 전체 Unit·Contract·Offline Integration: **712 passed, 22 skipped,
  631 subtests passed**(112.28초). 이번 분석 연결 테스트는 7건이다.
- Backend 최신 변경과 함께 실제 PSI → 분석 봉인 → 로컬 SQLite → Platform Reader의 교차
  저장소 Offline 테스트를 다시 실행했다. 작은 단일 품목·평가 Reference Case 기준이며
  실제 개발 DB Claim·장기 다품목 화면 E2E로 해석하지 않는다.
- dsai-platform Inventory 관련 Backend: **335 passed**, 기존 Starlette 경고 24건.
- Frontend 결과·정책 탐색 테스트: **9 passed**. SSR 표시와 상태·번역·Query 구조를 검증했다.
- 320/768/1024/1440px 로컬 SSR에서 카드 표시·본문 가로 넘침을 확인했다.
  이는 실제 DB·로그인·브라우저 상호작용 통합 검증이 아니다.
- Frontend Source 타입 검사는 통과했다. 일반 `tsc`는 기존 `.next` Validator가 삭제된 Route를
  참조해 실패했다. 해당 생성 파일을 제외한 Source 검사 결과를 전체 Next 빌드 통과로 주장하지 않는다.
- Ruff·Format, JSON Schema 29개, `git diff --check`를 확인했다. Coverage 비율은 측정하지 않았다.
- DB 접속·Write, Migration 적용, 서비스 기능 활성화·배포, Commit·Push는 수행하지 않았다.

### 5.1. 로컬 기준선 재검증 (2026-10-12)

- Engine 전체 Unit·Contract·Offline Integration: **727 passed, 7 skipped,
  653 subtests passed**(118.36초). Platform·Demand 소스 경로를 명시해 교차 저장소 검증을 포함했다.
  Skip 7건은 PostgreSQL 읽기 전용 통합 검증을 명시적으로 비활성화한 결과다.
- Platform Inventory·Demand Handoff·공통 Run/RBAC 관련 Backend **417 passed**, 기존 경고 24건.
  Frontend 정책 탐색·결과·7축·Matrix·VED·Runtime controls **28 passed**.
- Engine Ruff·Format(223개 Python 파일), Platform 변경 Python 28개 Ruff·Format,
  JSON Schema 29개, 양쪽 Config `2.1.0` Schema 일치와 `git diff --check`를 확인했다.
- Frontend 전체 `next build`가 타입 검사·정적 페이지 생성까지 통과했다. 기존 `.next/dev/types`가
  삭제된 Portal 경로를 참조해 처음 실패했고, 실행 중인 Next 서버가 없는지 확인한 뒤 생성 캐시만
  복구 가능한 임시 경로에 보관했다. Source/환경 설정 파일이나 의존성은 변경하지 않았다.
- Python 3.12.10, Polars 1.41.2, Torch 2.12.1의 기존 검증 환경을 사용했다.
  Coverage·Linux/CUDA·실제 브라우저 로그인/DB E2E·공용 배포 검증은 아니다.
- 이번 승인 범위는 관련 코드·Schema·테스트·문서를 저장소별 독립 로컬 Commit으로 고정하는 것이다.
  개인 보고서·환경·IDE·임시 파일은 제외한다. Push·DB 접속/Write·Migration·배포는 수행하지 않는다.

재현 명령(저장소 루트, 테스트 의존성이 있는 Python):

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests:tests/support:. \
DSAI_PLATFORM_ROOT=/absolute/path/to/dsai-platform \
DEMAND_ENGINE_ROOT=/absolute/path/to/DemandEngine \
IO_READONLY_INTEGRATION=0 IO_SOURCE_READONLY_INTEGRATION=0 \
python -m pytest -p no:cacheprovider tests/unit tests/contract tests/integration -q
```

## 6. 남은 작업 순서

**1. 실제 Run의 분석 입력 자동 생성 연결 — 다음 작업**

- 대상: InventoryEngine Command Resolver, dsai-platform Backend Claim/Config 계약.
- 승인된 Config `2.1.0`과 분류 Snapshot을 정확히 읽어 탐색 결과 및 평가 Source를 생성한다.
  Run 생성 시 탐색·평가 사양을 봉인하고, 누락을 조용히 무시하지 않는다.
- 완료 조건: API로 만든 Claim 한 건이 같은 입력 Hash의 분석 Artifact를 자동 생성한다.
  공통 계약 확정까지 직렬, 이후 Producer와 UI 상태 테스트는 독립 검증 가능하다.

**2. 개발 배포 및 실제 Run·화면 통합 검증 — 승인 필요**

- 대상: 확정할 dsai-platform 개발 Backend/Frontend 서버, 공용 Inventory Runtime, 개발 PostgreSQL.
- Artifact Reader 접근·재시도 복구·권한과 화면 표시를 검증한다. 신규 Migration이 필요한지는
  1번 계약에서 판단하며 이미 적용된 Migration을 재실행하지 않는다.
- 실제 DB Write·공용 Runtime 재배포는 별도 명시적 승인 후 수행한다.

**3. 원격 전달 — 별도 요청 후**

- 대상: InventoryEngine `inventory_engine_dev`, dsai-platform `codex/inventory-psi-strategy-v2`.
- 로컬 기준선은 Source Producer와 정책 분석을 분리한 독립 Commit 대상으로 고정한다.
- InventoryEngine `origin/inventory_engine_dev`와 Platform 원격 작업 브랜치·`develop` MR을
  각각 명시한 후 Push/MR을 진행한다. 이번 요청은 Push를 포함하지 않는다.
