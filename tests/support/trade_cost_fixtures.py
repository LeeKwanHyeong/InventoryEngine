"""Deterministic sealed Trade Cost projection fixtures."""

from __future__ import annotations

import copy

from dsio_inventory_engine.inventory_contracts.values import digest


REVISION_SET_ID = "70000000-0000-4000-8000-000000000001"
SOURCE_REVISION_ID = "71000000-0000-4000-8000-000000000001"


def sealed_trade_cost_projection(
    *,
    environment_scope: str = "DEVELOPMENT",
    valuation_date: str = "2026-09-28",
    origin_site_cd: str = "V100",
) -> dict:
    source_header = {
        "source_revision_id": SOURCE_REVISION_ID,
        "tenant_id": "tenant-a",
        "project_id": "project-a",
        "source_domain": "CUSTOMS_TARIFF",
        "revision_no": 1,
        "company_cd": "DSE",
        "subs_cd": "C100",
        "origin_site_cd": origin_site_cd,
        "jurisdiction_country_code": "JP",
        "environment_scope": environment_scope,
        "valid_from": "2026-01-01",
        "valid_to": None,
        "source_manifest_hash": "1" * 64,
    }
    documents = [
        {
            "source_revision_id": SOURCE_REVISION_ID,
            "source_document_id": "DOC-1",
            "document_type": "DEVELOPMENT_FIXTURE",
            "source_owner": "DEVELOPMENT_FIXTURE",
            "source_system": "INVENTORY_ENGINE_TEST",
            "external_reference": "fixture://trade-cost/customs-tariff/jp",
            "published_at": None,
            "collected_at": "2026-09-16T00:00:00+00:00",
            "effective_from": "2026-01-01",
            "effective_to": None,
            "raw_content_hash": "2" * 64,
            "license_review_status": "NOT_REQUIRED",
        }
    ]
    records = [
        {
            "ad_valorem_rate": "0.05",
            "source_revision_id": SOURCE_REVISION_ID,
            "tariff_rule_id": "JP-MFN-1",
        }
    ]
    source_hash = digest({"header": source_header, "documents": documents, "records": records})
    member = {
        "source_domain": "CUSTOMS_TARIFF",
        "jurisdiction_country_code": "JP",
        "source_revision_id": SOURCE_REVISION_ID,
        "content_hash": source_hash,
    }
    revision_set = {
        "revision_set_id": REVISION_SET_ID,
        "tenant_id": "tenant-a",
        "project_id": "project-a",
        "revision_set_code": "TC-C100-DEV",
        "revision_no": 1,
        "company_cd": "DSE",
        "subs_cd": "C100",
        "origin_site_cd": origin_site_cd,
        "environment_scope": environment_scope,
        "valid_from": "2026-01-01",
        "valid_to": None,
        "source_manifest_hash": "1" * 64,
    }
    body = {
        "contract_id": "inventory-trade-cost-revision-set-projection-v1",
        "contract_version": "1.0.0",
        "revision_set": revision_set,
        "status": "APPROVED",
        "revision_set_content_hash": digest({"header": revision_set, "members": [member]}),
        "row_version": 1,
        "valuation_date": valuation_date,
        "members": [member],
        "sources": [
            {
                "header": source_header,
                "status": "APPROVED",
                "content_hash": source_hash,
                "documents": documents,
                "records": records,
            }
        ],
        "development_eligible": environment_scope == "DEVELOPMENT",
        "operational_eligible": environment_scope == "PRODUCTION",
    }
    return {**copy.deepcopy(body), "projection_content_hash": digest(body)}
