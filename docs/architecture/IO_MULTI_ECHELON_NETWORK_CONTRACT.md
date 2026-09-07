# IO Multi-Echelon Network 계약

## 1. 문서 목적

본 문서는 `tb_mst_site_country`의 Site를 Subsidiary별 2단계 Hub-Spoke Network로 연결하고, 운송 Lead Time을 IO Engine과 DSIM이 재현 가능하게 사용하는 확장 계약을 정의한다.

초기 IO Core는 단일 Site PSI와 보충 권고를 유지한다. 본 계약은 Site별 계산 결과를 Network 단계에서 연결하는 후속 Multi-Echelon Capability의 기준선이다. 2026-09-03 사용자 승인으로 개발 PostgreSQL/Neo4j에 Network Master와 Projection을 적용했으며 운영 활성화나 Multi-Echelon Solver 구현을 의미하지 않는다.

---

## 2. 확정 결정

### 2.1 `tb_mst_site_country`에는 Network 관계를 넣지 않는다

`tb_mst_site_country`는 한 행에 한 Site의 국가·시장·기후 등 정적 속성을 저장한다. 공급 관계와 운송 Lead Time은 다음 이유로 별도 Table에 둔다.

- 한 Site는 여러 Site로 공급하거나 여러 Site에서 공급받을 수 있다.
- 같은 Origin-Destination에도 해상, 항공, 육로 등 여러 운송수단이 존재할 수 있다.
- 운송시간, 비용, Capacity와 계약은 Site 속성보다 자주 변경된다.
- Network Revision과 유효기간을 기준으로 과거 Run을 재현해야 한다.
- Site Table에 `parent_site`, `mode_1`, `mode_2`, JSON Lane 목록을 넣으면 관계 무결성과 FK 검증이 약해진다.

`tb_mst_site_country`에는 향후 정확한 경로 산정에 필요한 Site 자체 속성만 추가할 수 있다.

- 업무 Timezone
- 기준 물류 도시 또는 좌표
- 기본 공항·항만·도로 거점 코드

이 값도 Network 관계가 아니라 Site의 기준 위치 속성이다.

### 2.2 PostgreSQL을 권위 저장소로 사용한다

Network Revision, Node, Lane과 운송 Lead Time의 System of Record는 PostgreSQL `dsim` Schema다. 기존 Site 기준정보는 `dsdm.tb_mst_site_country`에 유지하고 신규 Node는 `site_cd` Cross-schema FK로 참조한다. IO 실행 시 선택된 Revision을 Snapshot과 Hash로 봉인한다.

`dsdm`은 Demand, `dsim`은 Inventory 업무정보를 소유한다. 여러 Run이 공유하는 Versioned Master이므로 신규 세 Table에 실행별 Evidence용 `TB_IO_*` 이름을 사용하지 않는다. 이번 변경은 Schema 계약과 코드 작성이며 실제 DDL 적용은 아니다.

Neo4j는 다음 용도의 파생 Graph Projection이다.

- Hub에서 Spoke까지 공급 경로 탐색
- Site 또는 Lane 장애 영향 범위 조회
- 대체 운송수단과 경로 비교
- DSIM의 Network 근거 설명과 시각화

애플리케이션이 PostgreSQL과 Neo4j를 동시에 직접 갱신하지 않는다. PostgreSQL Transaction에서 Outbox Event를 생성하고 Projector가 Neo4j를 멱등 갱신한다. Neo4j 동기화 실패는 Projection Retry로 복구하며 PostgreSQL 원본을 수정하지 않는다.

### 2.3 현재 Seed는 개발용 합성 기준이다

첨부된 `tb_mst_site_country.csv`는 `create_id=synthetic_demand_parts`인 개발 데이터다. 여기서 계산한 운송시간도 운영 계약시간이 아니라 `SYNTHETIC_CALCULATED` Source다.

- 기준 위치: 국가별 대표 물류 도시의 위·경도
- 거리: 두 기준점의 대권거리
- 경로거리: 대권거리 × 운송수단별 우회계수
- P50: 출발 처리 + 출발 대기 + Line-haul + 통관 + 도착 처리
- P90: P50 × 운송수단별 변동계수
- 계획 Lead Time: 보수적인 P90

현재 P50/P90은 운송 실적의 통계적 백분위가 아니라 합성 시나리오의 가정값이다. 임의 배율만으로 실제 90% 납기 충족을 보장한다고 해석하지 않는다. 대표 도시, 공항·항만과 실제 창고 위치도 서로 동일하다고 간주하지 않는다.

운영 전환 시에는 계약 Lane 또는 실제 운송 이력으로 교체하고 Source Type과 Revision을 변경한다. 합성 Revision을 덮어쓰지 않는다.

---

## 3. 현재 2-Echelon Network 기준선

각 Subsidiary는 대표 Site 하나와 하위 Site 네 개를 가진다. 대표 Site도 자체 수요와 재고를 가지는 `HUB_WITH_LOCAL_DEMAND`다.

| Subsidiary | 대표 Site | 국가 | 하위 Site |
|---|---|---|---|
| `C100` | `V100` | 대한민국 | `V101`, `V102`, `V103`, `V104` |
| `C110` | `V110` | 베트남 | `V111`, `V112`, `V113`, `V114` |
| `C120` | `V120` | 러시아 | `V121`, `V122`, `V123`, `V124` |
| `C130` | `V130` | 폴란드 | `V131`, `V132`, `V133`, `V134` |
| `C140` | `V140` | 영국 | `V141`, `V142`, `V143`, `V144` |
| `C150` | `V150` | 이탈리아 | `V151`, `V152`, `V153`, `V154` |
| `C160` | `V160` | 스웨덴 | `V161`, `V162`, `V163`, `V164` |
| `C170` | `V170` | 미국 | `V171`, `V172`, `V173`, `V174` |
| `C180` | `V180` | 멕시코 | `V181`, `V182`, `V183`, `V184` |
| `C190` | `V190` | 브라질 | `V191`, `V192`, `V193`, `V194` |

초기 Seed는 대표 Site에서 하위 Site로 향하는 40개 방향성 공급 관계를 생성한다. 각 관계에는 계획용 주 운송수단과 비상용 대체 운송수단을 두어 총 80개 Lane Mode Row를 생성한다.

검토 가능한 Seed와 계산 워크북은 [`data/`](data/README.md)에 보관한다.

---

## 4. PostgreSQL 물리 계약

### 4.1 `dsim.tb_mst_inventory_network`

Network와 Revision의 수명주기를 소유한다.

| Column | 의미 |
|---|---|
| `network_revision_id` | 불변 Revision PK |
| `network_id` | Revision을 묶는 논리 Network ID |
| `revision_code` | Seed의 `network_revision` 문자열을 보존하는 식별값 |
| `company_cd` | 초기 단일 Company Binding |
| `subs_cd` | 현재 2-Echelon Network Scope |
| `network_type` | `HUB_SPOKE_2_ECHELON` |
| `revision_no` | Network별 증가 Revision |
| `status` | `DRAFT`, `APPROVED`, `SUPERSEDED` |
| `effective_from/to` | 업무 유효기간 |
| `environment_scope` | `DEVELOPMENT`, `PRODUCTION`. 현재 Seed/CLI는 개발 전용 |
| `content_sha256` | Canonical Header·Node·Lane 전체의 SHA-256 |
| `row_version` | 승인·폐기 상태 변경의 CAS Version |
| `approval_evidence` | 승인자·시각·검토 근거·승인한 Content Hash |

Unique Key는 `(company_cd, network_id, revision_no)`와 `(company_cd, network_id, revision_code)`다. V1은 `(company_cd, network_id)`별 `APPROVED` Revision을 최대 하나만 허용하는 Partial Unique Index를 사용한다. 새 Revision 승인 전에 기존 승인 Revision의 명시적 `SUPERSEDED` 전환이 필요하다. 미래 유효기간별 다중 승인 예약과 자동 전환은 이번 CLI 범위에 없다.

### 4.2 `dsim.tb_mst_inventory_network_node`

Site가 특정 Network Revision에서 어떤 역할을 하는지 저장한다.

| Column | 의미 |
|---|---|
| `network_revision_id` | Network Revision FK |
| `site_cd` | `dsdm.tb_mst_site_country.site_cd` FK |
| `node_role` | `HUB_WITH_LOCAL_DEMAND` 또는 `SPOKE` |
| `echelon_level` | Hub `1`, Spoke `2` |
| `parent_site_cd` | Spoke의 직접 공급 Hub. Hub는 `NULL` |
| `country_name`, `reference_city`, `latitude`, `longitude` | 실행 재현을 위한 Revision별 기준 위치 Snapshot |
| `location_source_type` | `SYNTHETIC_REFERENCE_CITY`, `VERIFIED_FACILITY` |

PK는 `(network_revision_id, site_cd)`다. `parent_site_cd`도 같은 Revision의 Node를 참조해야 하며 자기 자신을 참조할 수 없다. 현재 계약은 Revision별 Hub를 정확히 한 개만 허용한다.

### 4.3 `dsim.tb_mst_inventory_network_lane`

방향성 공급 관계와 운송수단별 계획 Lead Time을 저장한다.

| Column | 의미 |
|---|---|
| `lane_id` | Revision 내 Lane 식별자. PK는 `(network_revision_id, lane_id)` |
| `network_revision_id` | Network Revision FK |
| `from_site_cd/to_site_cd` | 같은 Revision에 속한 Node FK |
| `transport_mode` | `SEA`, `AIR`, `ROAD`, `MULTIMODAL` |
| `route_priority` | `1` 주 경로, `2+` 대체 경로 |
| `route_distance_km` | 계산 또는 운영 경로 거리 |
| `p50_lead_time_days` | 중앙 수준 Lead Time |
| `p90_lead_time_days` | 보수적 Lead Time |
| `planning_lead_time_days` | IO 계획에 실제 적용할 값 |
| `source_type` | `SYNTHETIC_CALCULATED`, `ROUTING_ESTIMATED`, `CONTRACTED_LANE`, `HISTORICAL_OBSERVED` |
| `calculation_method` | 산식·라우팅 Version |
| `calculation_reference_url` | 계약, API, 이력 또는 산식 근거 |

권장 Unique Key는 `(network_revision_id, from_site_cd, to_site_cd, transport_mode)`다. `from_site_cd != to_site_cd`, 거리와 시간은 0 이상이어야 한다. 주 경로는 같은 Origin-Destination에서 하나만 허용한다.

V1의 Node/Lane은 상태와 유효기간을 Header에서 상속하며 개별 `active_flag`, `status`, `effective_from/to`를 중복 저장하지 않는다. Lane별 중지나 위치 수정은 새 Network Revision으로 표현한다. 이미 적재한 DRAFT도 UPDATE/DELETE로 고치지 않고 새 Revision을 만든다.

운송 비용, 일별 Capacity, MOQ, 발주배수 또는 출발 Calendar가 필요해지면 Lane Header에 반복 컬럼을 추가하지 않고 별도 Versioned Constraint/Schedule Table로 확장한다.

### 4.4 IO Run Snapshot

IO Engine은 실행 중 현재 Master를 재조회하지 않는다. Planning Cycle Revision에 승인된 `network_revision_id`를 Binding하고 다음을 Run Manifest에 봉인한다.

- `network_revision_id`
- `network_content_hash`
- 선택된 Site와 상·하위 Node 범위
- 적용 Lane ID와 운송수단
- `planning_lead_time_days`
- Source Type과 Calculation Version

Network가 바뀌면 기술적 Retry가 아니라 새 Planning Cycle Revision이다.

현재 구현은 [`IO_NETWORK_INPUT_CONTRACT.md`](IO_NETWORK_INPUT_CONTRACT.md)의 **입력 준비**까지다. 정확한 Revision을 PostgreSQL에서 읽기 전용으로 검증하고 전체 Network Snapshot·Site 후보 경로·Manifest를 메모리에 만든다. `PREPARED_IN_MEMORY`는 봉인 Artifact나 Run Claim 완료가 아니다. 현재 `selected_lane_ids=[]`, `usage=CONTEXT_ONLY`이며 위 적용 Lane/운송수단/계산 Lead Time 결정은 후속 Capability에 속한다. 입력 ID/Hash를 Cycle·Run에 영속 Binding하고 Retry에서 봉인 Artifact를 재사용하는 기능은 아직 구현되지 않았다.

### 4.5 `dsim.inventory_network_projection_outbox`

Master는 세 Table이며 Outbox는 별도 운영 지원 Table 한 개다. 기존 Agent→Redis 전용 Outbox와 공유하지 않는다. Header 승인/폐기 상태 변경 Trigger가 같은 PostgreSQL Transaction 안에 Event를 기록한다. DRAFT Seed Load만으로는 Event가 생성되지 않는다.

- Unique: `(network_revision_id, source_row_version)`
- 상태: `PENDING → PROCESSING → PUBLISHED`, 실패 시 `RETRY`, 최대 5회 실패 또는 마지막 Lease 만료 시 `FAILED`
- Claim: `FOR UPDATE SKIP LOCKED`, 시도별 `lease_token`, 120초 Lease
- ACK/Retry: 현재 Lease Token과 만료시각을 조건으로 CAS
- Event는 Revision ID·Content Hash를 참조하며 Master 원문을 중복 복제하지 않는다.
- Neo4j 반영 후 DB ACK가 유실되면 동일 Revision을 재처리한다. 두 DB를 하나의 원자 트랜잭션처럼 취급하지 않는다.

---

## 5. Neo4j Projection 계약

### 5.1 권장 Graph Model

Lane은 단순 Edge가 아니라 Revision, Source, 대체 운송수단과 향후 실제 운송 관측치를 갖는 관리 대상이다. 따라서 Relationship Property만 사용하는 구조보다 Lane Node를 권장한다.

```text
InventoryNetworkRevision -HAS_NODE-> InventoryNetworkNode -AT_SITE-> InventorySite
InventoryNetworkRevision -HAS_LANE-> InventoryTransportLane
InventoryNetworkNode -ORIGIN_OF-> InventoryTransportLane -DESTINATION_OF-> InventoryNetworkNode
```

기존 DSDM Graph의 `Site`, `Subsidiary` Label과 섞이지 않게 `Inventory*` Label을 사용한다. Hub/Spoke 역할과 위치 Snapshot은 Revision별 `InventoryNetworkNode`에 저장한다. 공유 `InventorySite`에는 Company·Site 식별값만 둔다.

`InventoryTransportLane`에는 다음 Projection 속성을 둔다.

```text
lane_id
lane_key
network_revision_id
transport_mode
route_priority
planning_lead_time_days
p50_lead_time_days
p90_lead_time_days
source_type
calculation_method
from_node_key
to_node_key
```

Graph `lane_key`는 Revision ID와 `lane_id`를 묶은 결정론적 UUID다. `lane_id` 단독 MERGE는 금지한다. 상태·유효기간·`content_sha256`·`projection_sha256`·`row_version`·`verified`는 Revision Node에 둔다.

### 5.2 동기화와 복구

```text
PostgreSQL 승인 Transaction
  -> 이미 적재한 DRAFT의 전체 내용과 검토 증거 확인
  -> Header APPROVED 전환
  -> Outbox Event 저장
  -> Commit
  -> Neo4j Projector가 lane_id + revision 기준 MERGE
  -> Row Count와 Content Hash 대사
```

- 동일 Outbox Event 재처리는 같은 Graph 결과를 만들어야 한다.
- Neo4j에는 Revision ID, Source Row Version, 원본 Hash와 Projection Hash를 저장한다.
- Revision 전체를 하나의 Graph Transaction에서 MERGE하고 실제 Node/Lane 속성과 연결관계를 재조회해 대사한 뒤 `verified=true`로 게시한다. 실패하면 전체 Graph Transaction을 Rollback한다.
- 이전 Event가 늦게 도착해도 더 최신 Graph Row Version을 낮추거나 `SUPERSEDED`를 다시 `APPROVED`로 바꾸지 않는다.
- 불일치는 Outbox `RETRY/FAILED`로 남긴다. 조회자는 PostgreSQL의 승인 Revision/Version과 Graph의 `verified`를 확인해야 하며, 과거에 승인됐던 Graph만 보고 현재 유효성을 판단하지 않는다. 실제 DSIM Read API 연동은 후속 작업이다.
- 승인 없이 Graph 삭제·전체 교체를 수행하지 않는다. 구조가 오염돼 재처리만으로 복구되지 않으면 별도 복구 승인이 필요하다.
- IO 계산과 Publication은 Neo4j 가용성에 의존하지 않는다.

---

## 6. 합성 운송시간 산식

현재 Seed의 계산은 실제 Door-to-Door 약속이 아니라 개발용 보수 추정이다.

```text
great_circle_km = 기준 물류 도시 좌표 간 대권거리
route_distance_km = great_circle_km × mode_route_factor
linehaul_days = route_distance_km / mode_speed_km_per_day
p50 = ceil(origin_handling + departure_wait + linehaul + customs + destination_handling)
p90 = ceil(p50 × variability_factor)
planning_lead_time_days = p90
```

모든 값은 Mode별 Assumption, Calculation Version과 Source Reference를 함께 보존한다. 실제 경로 검증 시 다음 종류의 Source로 교체할 수 있다.

- 도로: Google Routes Route Matrix 같은 Routing Source
- 해상: SeaRoutes 같은 Port-to-Port Routing Source
- 항공: OAG 같은 Schedule Source
- 실적: ERP/TMS의 출발·도착 Timestamp 기반 관측 분포

운영 Baseline 승인은 `CONTRACTED_LANE` 또는 충분한 `HISTORICAL_OBSERVED` 근거를 우선한다. 합성 값은 개발, 민감도 분석과 데이터 계약 E2E에만 사용한다.

---

## 7. 검증 규칙

- `tb_mst_site_country`의 50개 Site가 Node에 정확히 한 번씩 존재한다.
- Subsidiary마다 Hub 1개, Spoke 4개가 존재한다.
- 40개 Hub-to-Spoke 관계에 주 경로가 정확히 하나 존재한다.
- Lane Endpoint는 모두 같은 Network Revision의 Node다.
- 주 경로와 대체 경로는 같은 Mode를 사용할 수 없다.
- `planning_lead_time_days >= p50_lead_time_days > 0`이다.
- DRAFT 또는 `environment_scope=DEVELOPMENT` Revision은 운영 Planning Cycle에 Binding할 수 없다. 개발용 APPROVED도 운영 승인이 아니다.
- 승인 Revision과 Neo4j Projection의 Node/Lane 수 및 Content Hash가 일치한다.

---

## 8. 작업 상태와 승인 경계

### 완료

- Site Master와 Network 관계를 분리하기로 확정
- PostgreSQL을 권위 저장소, Neo4j를 파생 Projection으로 확정
- Subsidiary별 대표 Site와 2-Echelon Hub-Spoke 기준선 확정
- 개발용 운송 Lead Time 산식과 Source Type 분리 원칙 확정
- 신규 Master 소유권을 `dsim`으로 정정하고 `dsdm` Site Master와 Cross-schema FK 정의
- 50개 Node·80개 Lane Seed 및 계산 워크북 생성 완료. 원본 CSV/워크북은 이번 Schema 정정으로 재작성하지 않음
- `dsai-platform`에 Migration DDL 초안, 기본 읽기 전용 Seed CLI, 승인 검증, Neo4j Constraint/Index와 Outbox Projector 구현
- 10/50/80건 오프라인 계획 검증, 중복·고아·Hash·Rollback·CAS·재처리 단위 테스트 완료
- 2026-09-03 개발 PostgreSQL `192.168.0.46:5432/dsai`의 `dsim`에 Master 3개와 Outbox 1개 적용, Network 10/Node 50/Lane 80 적재·Replay 검증 완료
- 10개 Header를 `APPROVED`/`DEVELOPMENT_SCENARIO`로 승인. 원본 CSV/워크북은 DRAFT 그대로 보존하고 운영 Source로 승격하지 않음
- 사용자 지정 DSDM 개발 Neo4j `192.168.0.46:7689/neo4j`에 Inventory 전용 Constraint 4/조회 Index 3, Graph Node 190/관계 340 반영
- 실제 FK·전체 Rollback·동시 승인 CAS·ACK 장애 복구 및 DB–Graph Hash 대사 완료. Outbox 10개 전부 PUBLISHED, 기존 DSDM Label별 건수·스키마 변경 없음
- InventoryEngine에 승인 Revision ID/Hash·Scope·Plan 기준일을 검증하는 입력 UseCase·CLI 구현. Unit 27건, Producer 호환 1건, 개발 PostgreSQL 읽기 전용 통합 5건 통과(10개 Network·50개 Site). Neo4j에 의존하지 않음

코드 위치·실행 명령·승인 양식·검증 범위는 [`dsim-inventory-network-v1.md`](../../../dsai-platform/docs/backend/dsim/dsim-inventory-network-v1.md)에서 관리한다.
실제 적용·오류 수정·재시도 증거는 [개발 적용 기록](../../../dsai-platform/docs/backend/dsim/dsim-inventory-network-development-acceptance-2026-09-03.md)에 보존한다.

### 외부 작업 대기 — 실제 물류 계약 확인

- 50개 Site의 실제 창고·항만·공항 ID 및 좌표가 제공되지 않아 대표 도시를 실제 시설로 승인할 수 없음
- 80개 주/대체 Mode의 실제 서비스·국경 통과·출발 Calendar와 납기 근거가 미확인
- 현재 P50/P90은 가정값이므로 실적 백분위 검증 미완료
- 합성 개발 시나리오는 사용자 승인 근거를 기록해 승인 완료. 실제 시설·운송 계약 검토는 별개로 미완료

### 다음 작업

- P0-13 `TB_IO_*` 물리 Schema에서 Network Snapshot Binding Column 반영
- 공통 Run/Planning Cycle 구현에서 Network 입력 준비 UseCase를 연결하고 Artifact·DB Binding으로 봉인. 현재 메모리 준비 결과를 Run 성공으로 처리하지 않음
- Multi-Echelon Capability를 초기 단일 Site Core 이후 별도 Application UseCase로 설계
- DSIM Agent·조회 API는 아직 미구현. 향후 DSIM 개발 시 PostgreSQL 승인/Graph VERIFIED 대사를 연결하며 현재 IO 개발의 선행 조건으로 두지 않음

### 승인 필요

- 상시 Outbox Worker의 공용 Runtime 배포와 기능 전체 활성화. DSIM 서비스는 구현 이후 별도 승인 대상으로 관리
- 외부 Routing/Schedule API Key 사용 및 유료 호출
- 운영 Planning Cycle에서 Network Revision 활성화
