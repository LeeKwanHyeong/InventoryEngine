# Multi-Echelon 개발 Seed

이 디렉터리는 [`IO_MULTI_ECHELON_NETWORK_CONTRACT.md`](../IO_MULTI_ECHELON_NETWORK_CONTRACT.md)의 개발용 합성 기준선을 보관한다.

| 파일 | 내용 |
|---|---|
| `tb_mst_inventory_network_node_seed.csv` | `tb_mst_site_country.csv`의 50개 Site를 Subsidiary별 Hub 1개와 Spoke 4개로 분류한 Node Seed |
| `tb_mst_inventory_network_lane_seed.csv` | 40개 Hub→Spoke 관계에 주·대체 운송수단을 적용한 80개 Lane Mode Seed |
| `inventory_network_transport_seed.xlsx` | Assumption, 계산식과 검증 결과를 함께 보는 Review Workbook |

원본 Seed는 `network_revision=SYNTH-20260902-V1`, `source_type=SYNTHETIC_CALCULATED`, `status=DRAFT`를 유지한다. 2026-09-03 사용자 승인으로 개발 DB에 적재된 Header 10개만 `APPROVED`/`DEVELOPMENT_SCENARIO`로 전환했다. 운영 Planning Cycle 또는 실제 발주 의사결정에 사용하지 않는다.

파일은 기준 위치 또는 Mode Assumption이 바뀌면 기존 Revision을 수정하는 대신 새 Revision으로 다시 생성한다.

적재 대상은 `dsim.tb_mst_inventory_network`, `dsim.tb_mst_inventory_network_node`, `dsim.tb_mst_inventory_network_lane`이다. 기존 Site는 `dsdm.tb_mst_site_country`에서 검증한다. Header 10건은 Node/Lane CSV의 동일 Company·Subs·Revision을 검증해 Loader가 결정론적으로 구성한다.

Seed CSV의 `network_revision`은 DB `revision_code`로 보존하고 실제 `network_revision_id`는 Company·Network·Revision Code의 UUIDv5다. Lane PK는 `(network_revision_id, lane_id)`다. 현재 P50/P90은 실적 백분위가 아니라 합성 가정값이며 시설·운송 계약을 검증한 자료가 아니다.

CLI·검토 양식은 [구현 안내](../../../../dsai-platform/docs/backend/dsim/dsim-inventory-network-v1.md)를 참고한다. 개발 PostgreSQL `dsai.dsim`의 10/50/80건과 사용자 지정 DSDM Neo4j의 Inventory 전용 Projection, Outbox 10건 PUBLISHED를 검증했다. 상세 근거는 [개발 적용 기록](../../../../dsai-platform/docs/backend/dsim/dsim-inventory-network-development-acceptance-2026-09-03.md)에 보존한다. CSV/워크북은 수정하지 않았다.
