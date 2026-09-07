# IO 승인 Network 입력 계약 v1

기준일: 2026-09-03. 상태: 독립 Python 입력 준비 Slice 구현, 오프라인 및 개발 PostgreSQL 읽기 전용 검증 완료. 전체 IO Pipeline 연동·Run Artifact 봉인·DB Materialization은 미구현이다.

## 1. 구현 경계

`prepare_inventory.application.PrepareNetworkInputUseCase`가 전달된 Network Revision을 **정확히 한 번의 일관된 Source 조회**로 읽고 검증해 `PreparedNetworkInput`을 반환한다. Header/Node/Lane 조회는 하나의 Repeatable Read, Read-only Transaction이다. 데이터가 고정된 후 계산 단계에서 현재 Master나 Neo4j를 다시 찾지 않는다.

- Request 계약: `io-network-input-request-v1`, Version `1.0.0`
- Source 계약: `inventory.network_master`, Version `1.0.0`
- 저장 원본: `dsim.tb_mst_inventory_network`, `..._node`, `..._lane`
- 공통 Input Binding의 제안 `input_type`: `INVENTORY_NETWORK`
- 계산 적용: `CONTEXT_ONLY`. 후보 Lane을 보존할 뿐 주/대체 Mode를 자동 선택하거나 Lead Time을 재고정책에 적용하지 않는다.

이 계약은 향후 Execution Request의 **Network 입력 부분**이다. 전체 Execution HTTP API, Plan Claim, Run 생성, Forecast 검증 또는 Cycle Row 존재 검증을 구현했다는 의미가 아니다. DSIM 구현과 무관하게 IO가 소비할 수 있다.

## 2. 전달할 값과 검증

| 구분 | 필수 값 | 검증 |
|---|---|---|
| Network | `network_revision_id`, `network_content_hash`, `contract_version` | UUID, SHA-256, Version 고정. 누락 시 최신값 탐색 금지 |
| 실행 참조 | `engine_run_id`, `planning_cycle_id`, `planning_cycle_revision_id`, `cycle_site_execution_id` | 비어 있지 않은 식별자. 실제 공통 Run/Cycle FK와 Claim은 향후 Runner 책임 |
| 업무 Scope | `company_cd`, `subs_cd`, `site_cd`, `plan_type` | 단일 Site, POSM/TGSM, Header의 Company/Subs 및 Node 소속 일치 |
| Master | `master_snapshot_revision`, `master_as_of_date` | 호출자가 고정한 Master Revision/Plan 기준일. 실행일이나 현재 주차로 보완하지 않음 |
| Deployment | `IO_COMPANY_CD`, `IO_ENVIRONMENT` | 요청 JSON이 아니라 신뢰된 프로세스 설정. 다른 Company 또는 개발/운영 Scope 혼입 거부 |

[요청 예제](../../examples/network_input_request.json)와 [JSON Schema](../../schemas/network_input_request.schema.json)를 제공한다. JSON의 알 수 없는 필드와 중복 Key도 거부한다. JSON Schema가 구조를, Python이 Source 상태·Hash·Scope·유효기간 같은 의미를 검증한다.

Admission은 다음을 모두 만족해야 한다.

1. 요청의 Company가 배포 Company와 일치한다. 불일치하면 DB 조회도 하지 않는다.
2. 지정 Revision이 존재하고 `status=APPROVED`, 양의 정수 `row_version`이다. DRAFT/SUPERSEDED는 새 입력 준비에 사용할 수 없다.
3. 전체 Header·Node·Lane Canonical Hash가 DB의 `content_sha256` 및 요청 Hash와 모두 일치한다. Site별 필터링은 전체 Hash 검증 **이후**에 수행한다.
4. `effective_from <= master_as_of_date < effective_to`다. 종료일 NULL은 상한 미정이다.
5. 승인 Evidence의 Hash·검토 여부·승인자/시각/근거가 존재한다. 합성 Source는 명시적 개발 시나리오 수락이 있어야 한다.
6. Production에서는 개발 Revision 또는 합성 위치/운송시간을 거부한다. Header만 PRODUCTION으로 바꿔도 합성 Source 검증으로 거부된다.
7. Hub 1개, 부모 관계, Endpoint, Mode/Priority 중복, 유효 숫자·Lead Time, 주 경로 존재를 재검증한다.

Control Plane 인증이나 Tenant/Project 권한을 이 검증으로 대체하지 않는다. Source Reader와 Deployment 설정은 신뢰된 Bootstrap에서 조립해야 한다. CLI는 DB Write를 하지 않으며 기본 동작은 파일 요청 검증뿐이다.

## 3. Hash·Snapshot·Retry

Canonical JSON은 UTF-8, Key 정렬, 공백 없음, Node `site_cd`/Lane `lane_id` 정렬, Decimal의 지수 없는 정규화 문자열을 사용한다. Contract V1의 필드 집합을 고정하며 기존 Producer와 10개 Seed Hash를 대조했다.

| Hash | 의미 |
|---|---|
| `source_content_hash` | Network 전체의 원본 내용. Producer·PostgreSQL과 동일 |
| `site_context_hash` | 선택 Site Node, 인접 Site, 후보 Lane, CONTEXT_ONLY의 내용 |
| `network_input_binding_hash` | Network 참조, 승인 Row Version, Plan/Master/Scope와 Site Context를 묶은 IO 입력 식별값 |

`engine_run_id`는 Manifest에 기록하지만 Binding Hash에서는 제외한다. 따라서 동일 입력 Retry의 새 Run ID가 입력 변경으로 오인되지 않는다. Cycle Revision, Site, Master 기준일·Revision, Network 내용이 바뀌면 Binding Hash도 달라진다. 다른 Forecast/BOH/Policy/Configuration Hash까지 묶는 전체 Run Input Hash는 후속 작업이다.

반환 객체는 문자열 기반 불변 Dataclass이며 호출자가 받은 Dict를 수정해도 내부 Snapshot은 변하지 않는다. 현재 상태는 `PREPARED_IN_MEMORY`이며 **SEALED Artifact 또는 VERIFIED DB Receipt가 아니다**. 향후 Run Evidence 저장 단계가 이 전체 Snapshot과 Manifest를 Run별 Artifact에 봉인하고 `TB_IO_*`에 Materialize해야 한다.

새 준비 시점에 Superseded된 Revision은 실패한다. 이미 봉인한 과거 Run의 Replay는 당시 Snapshot과 Hash를 사용하는 별도 Artifact Restore 계약으로 구현해야 하며, 이번 Slice는 이를 구현하거나 신규 DB 조회로 대체하지 않는다.

## 4. 단일 Site와 Multi-Echelon의 구분

- Spoke V101: V100→V101의 후보 Lane 2개와 상위 V100을 입력 Context로 보존한다.
- Hub V100: V101~V104로 향하는 후보 Lane 8개와 하위 Site 목록을 보존한다.
- 두 경우 모두 `calculation_site_cd`는 요청 Site 하나다. 하위 수요 합산·Hub 재고 배분·전송 주문·Network 안전재고 계산을 수행하지 않는다.
- `selected_lane_ids=[]`를 명시한다. 후보 운송수단의 Lead Time을 합산하거나 8개 Lane을 모두 공급으로 가산하면 안 된다.
- 초기 단일 Site Core에서 Network가 필요 없는 실행은 이 Slice를 호출하지 않는 방식으로 지원한다. Network 없는 실행을 구현한 Runner는 아직 없다.

## 5. 대안과 선택

| 대안 | 판단 |
|---|---|
| IO가 dsai-platform Python 모듈을 직접 import | 하나의 규칙을 공유하지만 Engine 독립 설치·배포가 Platform checkout에 종속되므로 선택하지 않음 |
| IO의 독립 Wire Contract + Producer 호환 테스트 | 선택. 명시적 v1 필드/Hash 규칙을 구현하고 고정 Fixture 및 Producer Seed 10건으로 Drift 검출 |
| Neo4j에서 입력을 구성 | 승인 원본·완전한 필드·정확한 Decimal의 권위가 아니므로 선택하지 않음 |

향후 양쪽 Consumer가 늘어나면 독립 배포 가능한 중립 계약 Package로 추출할 수 있다. 지금은 새 서비스/공유 Runtime/공통 DB Table을 늘리지 않는다.

## 6. 검증과 한계

- Unit Test 27건: Hash·Scope·기간·승인·운영 혼입 거부, 불변성, 같은 입력 Retry Hash, CLI 기본 미접속, Adapter 단일 읽기 Transaction.
- Producer 호환 Test 1건: 기존 10개 Network의 Canonical Hash와 필드 정의 일치.
- 개발 PostgreSQL 통합 Test 5건: 승인된 10개 Network의 Site 50개, 잘못된 Hash, 없는 ID, Production 혼입, CLI 입력 준비 경로 검증. 모든 연결이 Read-only이며 Skip 없이 통과.
- 기존 개발 DB/Neo4j 수정·Migration·Run 생성·실제 IO 계산은 이번 작업에서 수행하지 않았다.
- 기준 Source는 기존에 승인된 합성 Network다. 실제 물류 정확성, Full Forecast/BOH 계약 또는 전체 Run E2E를 검증한 것은 아니다.

현재 구현과 다음 개발 순서는 [개발 기준선](IO_DEVELOPMENT_BASELINE.md)을 따른다.

검증 범위·명령과 비수행 항목은 [검증 요약](evidence/network-input-validation-20260903.json), 프로젝트 [README](../../README.md)에 기록한다. 이 검증 요약 자체는 봉인된 Run Evidence가 아니다.
