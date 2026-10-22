# V100 대표 품목 개발 입력 준비

## 2026-10-22 완료 범위

InventoryEngine `inventory_engine_dev`와 dsai-platform Backend `codex/inventory-psi-strategy-v2`에서
대표 품목용 Canonical·26주 정책 이력·탐색 설정·분류·Catalog를 준비했다.
이번에는 실제 DB 접속/Write, Forecast·PSI·C# 실행, Claim, 설정 활성화, M3 배포를 하지 않았다.
Legacy Feature의 통계·참조 정책 계산과 Catalog 전개만 입력 준비에 포함된다.

기존 Run `4f7c3cac-9264-4b34-bf77-0eda92f0b6b2`, 원본 봉인 파일, FSN·정책 Matrix·운영 Gate는 보존한다.
새 개발 Draft의 Config/분류 Hash는 바뀌지만 원본을 수정하지 않으며 운영 승인을 의미하지 않는다.

## 입력과 대표 품목

- DSE/C100/V100, Plan `20250317_DSE_P12`, 판단 주차 `202512`, 직전 마감 `202511`.
- 기존 Demand Run `e5b43229-c69e-4e08-a8ab-5515bfb0f2f3`의 Forecast·Calendar·Master를 참조한다.
- 계산 가능 3,619품목에서 기존 ABC·XYZ 분류별 최소 Item ID와 첫 REVIEW 품목을 고른다.
  미래 Forecast 수량·후보 성과는 선택에 사용하지 않는다. AY는 원본에 없어 만들지 않았다.

| Item | 기존 분류 | 기존 주문 Gate |
| --- | --- | --- |
| DS_A000003 | BX | ALLOW |
| DS_A000005 | CZ | ALLOW |
| DS_A000016 | CY | ALLOW |
| DS_A000024 | CX | ALLOW |
| DS_A000027 | AX | ALLOW |
| DS_A000401 | BZ | ALLOW |
| DS_A000992 | CZ | REVIEW |
| DS_A003335 | BY | ALLOW |
| DS_A003502 | AZ | ALLOW |

원 Master 7,000품목·전체 분류 3,622품목을 보존한다. 기존 차단 3품목과 수요창 종료 3,378품목은
원문 사유를 유지하며, 나머지 계산 가능 3,610품목은 개발 표본 제외로 구분한다.
기존 BOH·입고·정책 수량을 투영할 뿐 Warm-up·PSI를 다시 계산하지 않는다. 고객주문은 원본에서 0행이다.

## 사전 이력과 개발 비용

- 기존 봉인 `target.parquet`의 SHA-256을 Source Receipt와 대조했다:
  `9fbf2a9bcb13207374c3dfdeb35794cb5fb49a32455f39fe2cfd6a96d4cc4f81`.
- `202438`~`202511`, 26주 × 9품목 = 234행이다. 누락·중복·미래 주차·음수·Cut-off 이후 가용 자료를 거부한다.
- 출처는 `SYNTHETIC_PINNED` / 정책 이력 `SYNTHETIC_DEMAND`다. 실제 운영 실적으로 표시하지 않는다.
- 새 Draft만 `policy_search.enabled=true`, `lookback_weeks=26`, `max_candidates_per_item=88`로 설정한다.
  기존 FSN·전체 Segmentation·정책 Matrix·Overlay·서비스 평가 기준은 바꾸지 않는다.
- 공통 비용은 `DEVELOPMENT_ASSUMPTION`, `PER_ITEM_ORDER`, USD다.

| 비용 | 값 | 단위 |
| --- | --- | --- |
| 보유비 | 0.02 | USD/EA/주 |
| 미납비 | 5 | USD/EA/주 |
| 품목별 주문 고정비 | 20 | USD/회 |
| 수량 비례 발주비 | 1 | USD/EA |

Legacy Feature 단가는 개발 가정 1 USD/EA이며 기준일은 Plan Master 기준일이다.
기존 ABC 매출·분류·HML을 덮어쓰지 않는다. 실제 원가·공동 선적비·Landed Cost를 검증한 것은 아니다.

## Binding과 저장

- 새 Config Hash: `f1ad2c81a140163a02cd56e79dcfe9ce2461db0f3a0b73858d08ac8500739225`.
- 로컬 Draft Revision: `7adb1acf-f0ba-5303-ab51-57f548be3e76`.
- 새 분류 ID: `345c5cf5-4040-586c-808c-86adcac43ded`.
- 새 분류 Content Hash: `4030c502513b1ca8cd364b3f37fda903d6552ed3d8e36d05ff0f439cac2ea9f4`.
- 전체 유효 정책 Hash는 원본과 동일하다:
  `73510cf0d5ce3cf48799ecc0dc1cbbf93de05862f8da3d42ecd0b2f83998e27c`.

등급·축 결과·계산/자동 게시/자동 발주 Gate는 그대로다. Config 연결만 새 Draft를 가리킨다.
원 Master·Calendar는 유지하고 표본 Forecast·재고 등은 원문 수량으로 별도 봉인한다. 상위 Forecast 참조는 그대로다.

Canonical 외곽 계약 2.1.0 안의 개발 Universe v3에 `EXCLUDED_DEVELOPMENT_SAMPLE`을 추가했다.
계산 가능한 품목에만 표본 제외를 허용하며 BLOCK을 숨길 수 없다. 전체 Master 포함/제외 합집합을 확인한다.
기존 v1/v2 읽기·Hash와 Production 입장 차단은 유지한다. 구버전 계약에 표본 제외를 섞으면 Python·Schema에서 거부한다.
이 Schema를 포함한 새 설치 Build는 후속 작업이다. 현재 설치 Runtime이 새 입력을 지원한다고 간주하지 않는다.

## 검증 결과와 경계

- Catalog: ABC·XYZ 288개, LEGACY_STYLE 288개, 넓은 참조 792개. 품목당 32/32/88개다.
- Feature·Catalog 생산과 실제 Catalog 입력 검증기를 통과했다. 후보 PSI·평가·선정·발주 계획은 아직 없다.
- Engine 관련 76 passed / 2 subtests, Backend 관련 18 passed. 전체 회귀는 아니다. 기존 Starlette 경고 5건.
- Ruff Check/Format, JSON Schema, 두 저장소 `git diff --check`를 확인했다.
- 실제 V100 준비를 네트워크·Baseline PSI·Target Pipeline 호출 금지 상태로 반복했다.
  원본과 출력 7개 파일의 지문이 같았다. DB·Runtime·권한·활성 설정은 변경하지 않았다.
- Platform `.tmp/inventory-v100-catalog-preparation-20261022-r2/`가 최종 준비본이다.
  앞선 초안은 보존한다. 원본·표본 데이터와 환경·개인 보고서는 Commit에 포함하지 않는다.

## 다음 작업

**설치 후보와 실행 Binding 조립 — 다음 작업 · InventoryEngine·Platform Backend**

- 새 설치 Build·실제 DLL Hash·Calculation Binding·Plan/Runtime Manifest를 고정한다.
- 현재는 로컬 Draft이며 DB 승인·Claim·C# 실행 연결이 완료된 상태가 아니다.

**실제 DLL Offline 검증 — 조립 후 직렬 작업**

- 9품목의 전체 후보 평가·품목별 선정·발주 계획과 저장·조회·재시도·복구를 확인한다.
- REVIEW 자동 게시/발주는 허용하지 않으며 일반 조회에 전체 PSI 재계산을 숨겨 넣지 않는다.

**개발 PostgreSQL 적재 — 승인 필요**

- 검증 후 별도 개발 Run을 기존 결과 테이블에 적재한다. 기존 Run을 덮어쓰지 않는다.
- M3·공용 Runtime·실제 발주·ERP 연계는 이번 범위 밖이다. Commit/Push도 수행하지 않았다.
