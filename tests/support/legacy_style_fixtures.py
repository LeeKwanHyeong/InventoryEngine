"""Hand-authored, synthetic-only feature input. Not an approved Runtime Snapshot."""

from datetime import date, timedelta
import json
from pathlib import Path

from dsio_inventory_engine.classify_inventory.legacy_features import LegacyFeatureProducer
from dsio_inventory_engine.classify_inventory.legacy_rules import LegacyStyleRules
from dsio_inventory_engine.classify_inventory.legacy_style import LegacyFactorSegmentation
from dsio_inventory_engine.inventory_contracts.segmentation import (
    SearchGroupAssignment,
    SegmentationResult,
)

DATA = Path(__file__).resolve().parents[2] / "docs/architecture/data"
RULES_DOCUMENT = json.loads((DATA / "legacy-style-development-rules-v1.json").read_text())
GOLDEN = json.loads((DATA / "legacy-style-development-rules-golden-v1.json").read_text())
BUILD_HASH = "f" * 64


def components():
    rules = LegacyStyleRules.from_document(RULES_DOCUMENT)
    return (
        rules,
        LegacyFeatureProducer(rules, reference_calculator_build_hash=BUILD_HASH),
        LegacyFactorSegmentation(rules, reference_calculator_build_hash=BUILD_HASH),
    )


def feature_request(*, item_ids=("ITEM-A",), reference_case=None):
    case = reference_case or GOLDEN["reference_feature_cases"][0]
    context = {
        "company_cd": "DSE",
        "subs_cd": "C100",
        "site_cd": "V100",
        "decision_yyyyww": "202512",
        "master_as_of_date": "2025-03-17",
        "input_cutoff": "2025-03-17T00:00:00Z",
        "calendar_snapshot_id": "SHARED-CALENDAR",
        "master_snapshot_revision": "MASTER-REV-1",
        "base_configuration_hash": "b" * 64,
        "classification_snapshot_id": "ORIGINAL-ABC-SNAPSHOT",
        "classification_content_hash": "c" * 64,
        "reference_calculator_build_hash": BUILD_HASH,
    }
    calendar = []
    for i in range(26):
        start = date(2025, 3, 17) - timedelta(weeks=26 - i)
        iso = start.isocalendar()
        calendar.append({"yyyyww": f"{iso.year}{iso.week:02d}", "start_date": start.isoformat()})
    base = {
        "revision": "REV-1",
        "scope": {key: context[key] for key in ("company_cd", "subs_cd", "site_cd")},
        "decision_yyyyww": "202512",
        "evidence_kind": "SYNTHETIC_PINNED",
        "available_at": "2025-03-16T23:00:00Z",
        "generator": {"version": "HAND-WRITTEN-V1", "seed": 0, "input_revision": "GOLDEN-V1"},
    }
    abc = SegmentationResult(
        "ABC_XYZ",
        "1.0.0",
        "a" * 64,
        "b" * 64,
        tuple(SearchGroupAssignment(item, "AX", "ASSIGNED", None, "AX") for item in item_ids),
    )
    return {
        "contract_version": "1.0.0",
        "context": context,
        "calendar": calendar,
        "universe": [{"item_id": item, "uom": "EA"} for item in item_ids],
        "abc_result": abc.to_dict(),
        "item_exceptions": [],
        "sources": {
            "history": {
                **base,
                "source_id": "HISTORY-26",
                "quantity_uom": "EA",
                "quantity_semantics": "UNCENSORED_DEMAND",
                "closed_yyyyww": [r["yyyyww"] for r in calendar],
                "rows": [
                    {"item_id": item, "yyyyww": week["yyyyww"], "demand_qty": qty}
                    for item in item_ids
                    for week, qty in zip(calendar, case["history_values"], strict=True)
                ],
            },
            "lead_time": {
                **base,
                "source_id": "PHYSICAL-LT",
                "time_uom": "DAY",
                "rows": [
                    {"item_id": item, "lead_time_days": case["physical_lead_time_days"]}
                    for item in item_ids
                ],
            },
            "unit_cost": {
                **base,
                "source_id": "PRICE-USD",
                "price_uom": "EA",
                "rows": [
                    {
                        "item_id": item,
                        "unit_cost": case["unit_cost_usd"],
                        "currency": "USD",
                        "price_as_of": "2025-03-01",
                    }
                    for item in item_ids
                ],
            },
        },
    }


def item_exception(*, profile="LARGE", start="2025-03-17", end="2025-04-01"):
    return {
        "company_cd": "DSE",
        "subs_cd": "C100",
        "site_cd": "V100",
        "item_id": "ITEM-A",
        "profile_id": profile,
        "reason": "개발 비교 전용",
        "source_revision": "EXCEPTION-REV-1",
        "effective_from": start,
        "effective_to": end,
    }
