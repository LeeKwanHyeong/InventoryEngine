"""Pure, explicit Source -> Canonical mappings. No missing-value imputation."""

from decimal import Decimal, localcontext

from dsio_inventory_engine.inventory_contracts.canonical import ROW_FIELDS, SCOPE
from dsio_inventory_engine.inventory_contracts.source import forecast_selector
from dsio_inventory_engine.inventory_contracts.values import (
    choice,
    day,
    decimal_string,
    digest,
    hash_value,
    identifier,
    optional,
    quantity_text,
    records,
    require,
    shape,
    text,
    yyyyww,
)

FORECAST_ROW = {
    **SCOPE,
    **{
        k: identifier
        for k in (
            "result_id",
            "model_run_id",
            "plant_cd",
            "plan_id",
            "oper_part_no",
            "target_cd",
            "bukt_cd",
            "snrio_id",
            "snrio_grp",
            "regul_type",
            "forecast_stat_cd",
        )
    },
    "plan_yyyyww": yyyyww,
    "fcst_w0_yyyyww": yyyyww,
    "fcst_yyyyww": yyyyww,
    "fcst_qty": decimal_string,
    "source_profile_sha256": hash_value,
    "input_manifest_sha256": hash_value,
    "model_run_status": choice("PENDING", "RUNNING", "SUCCEEDED", "FAILED", "CANCELED"),
}


def item(row: dict, master: dict) -> dict:
    code = row["oper_part_no"]
    require(code in master, "ORPHAN_ITEM", [{"item_id": code}])
    return {**{k: row[k] for k in SCOPE}, "item_id": code, "uom": master[code]["uom"]}


def map_rows(source: dict, master: dict, context: dict) -> list[dict]:
    adapter, kind, semantics = source["adapter"], source["snapshot_type"], source["semantics"]
    if adapter == "CANONICAL_V1":
        require(not semantics, "UNEXPECTED_SOURCE_SEMANTICS")
        return records(source["rows"], ROW_FIELDS[kind])
    if adapter == "DSDM_MASTER_V1":
        require(not semantics, "UNEXPECTED_SOURCE_SEMANTICS")
        rows = records(
            source["rows"],
            {
                **SCOPE,
                "oper_part_no": identifier,
                "use_flag": choice("Y", "N"),
                "stock_flag": choice("Y", "N"),
            },
        )
        return [
            {
                **{k: r[k] for k in SCOPE},
                "item_id": r["oper_part_no"],
                "uom": "EA",
                "active": r["use_flag"] == "Y",
                "stock_managed": r["stock_flag"] == "Y",
            }
            for r in rows
        ]
    if adapter == "DSDM_FORECAST_V1":
        return map_forecast(source, master, context)
    if adapter == "ERP_POSITION_V1":
        shape(semantics, {"boh_qty_basis": choice("ON_HAND"), "approval_reference": text})
        fields = {
            **SCOPE,
            "oper_part_no": identifier,
            "position_date": day,
            **{
                k: decimal_string
                for k in ("boh_qty", "reserved_qty", "available_qty", "backorder_qty")
            },
        }
        return [
            {
                **item(r, master),
                "position_date": r["position_date"],
                "on_hand_qty": r["boh_qty"],
                **{k: r[k] for k in ("reserved_qty", "available_qty", "backorder_qty")},
            }
            for r in records(source["rows"], fields)
        ]
    if adapter == "UNVERIFIED_DUE_IN_V1":
        require(not semantics, "UNEXPECTED_SOURCE_SEMANTICS")
        fields = {
            **SCOPE,
            "oper_part_no": identifier,
            "receipt_id": identifier,
            "due_date": day,
            "due_qty": decimal_string,
        }
        return [
            {
                **item(r, master),
                **{k: r[k] for k in ("receipt_id", "due_date", "due_qty")},
                "status": "UNVERIFIED_DUE_IN",
                "supply_type": "PURCHASE_ORDER",
            }
            for r in records(source["rows"], fields)
        ]
    require(adapter == "SOURCE_POLICY_V1", "UNKNOWN_SOURCE_ADAPTER")
    return map_policies(source, master)


def map_forecast(source: dict, master: dict, context: dict) -> list[dict]:
    semantics = shape(
        source["semantics"],
        {
            "selector": forecast_selector,
            "netting_mode": choice("SAME_BUCKET_CONSUMPTION"),
            "approval_reference": text,
        },
    )
    selector = semantics["selector"]
    require(
        all(selector[k] == context[k] for k in (*SCOPE, "demand_run_id")), "FORECAST_SCOPE_MISMATCH"
    )
    require(
        selector["fcst_w0_yyyyww"] == context["plan_yyyyww"]
        and selector["forecast_from_yyyyww"] == context["plan_yyyyww"],
        "FORECAST_W0_MISMATCH",
    )
    require(
        source["metadata"]["demand_run_id"] == selector["demand_run_id"], "FORECAST_RUN_MISMATCH"
    )
    source_rows = records(source["rows"], FORECAST_ROW)
    inventory_items = {
        item_id for item_id, row in master.items() if row["active"] and row["stock_managed"]
    }
    require(bool(inventory_items), "EMPTY_BUFFER_UNIVERSE")
    forecast_items = {row["oper_part_no"] for row in source_rows}
    ineligible_items = forecast_items - inventory_items
    require(
        not ineligible_items,
        "ORPHAN_ITEM",
        [
            {
                "contract_id": "io-inventory-universe-comparison-v1",
                "status": "REJECTED_INELIGIBLE_FORECAST_ITEMS",
                "inventory_eligible_item_count": len(inventory_items),
                "forecast_item_count": len(forecast_items),
                "ineligible_forecast_item_count": len(ineligible_items),
                "ineligible_forecast_item_hash": digest(sorted(ineligible_items)),
                "ineligible_forecast_item_sample": sorted(ineligible_items)[:100],
                "uom_rule": "FIXED_EA_V1",
            }
        ],
    )
    result = []
    for row in source_rows:
        require(
            all(
                row[k] == value
                for k, value in selector.items()
                if k
                not in (
                    "tenant_id",
                    "project_id",
                    "demand_run_id",
                    "forecast_from_yyyyww",
                    "forecast_to_yyyyww",
                )
            ),
            "FORECAST_SELECTOR_MISMATCH",
        )
        require(row["model_run_status"] == "SUCCEEDED", "FORECAST_MODEL_NOT_SUCCEEDED")
        result.append(
            {
                **item(row, master),
                "yyyyww": row["fcst_yyyyww"],
                "forecast_qty": row["fcst_qty"],
                "forecast_netting_mode": semantics["netting_mode"],
                "upstream_gross_forecast_qty": None,
                "upstream_consumed_qty": None,
            }
        )
    return result


def map_policies(source: dict, master: dict) -> list[dict]:
    semantics = shape(
        source["semantics"],
        {
            "lead_time_unit": choice("DAY", "WEEK"),
            "service_level_unit": choice("FRACTION", "PERCENT"),
            "max_qty_meaning": choice("LEGACY_TARGET_INVENTORY"),
            "approval_reference": text,
        },
    )
    fields = {
        **SCOPE,
        "oper_part_no": identifier,
        "policy_id": identifier,
        "effective_from": day,
        "effective_to": day,
        **{k: decimal_string for k in ("min_po_qty", "po_lot_qty", "stock_lt", "svc_lv")},
        **{k: optional(decimal_string) for k in ("physical_max_capacity", "max_qty", "rop_qty")},
    }
    result = []
    with localcontext() as ctx:
        ctx.prec = 40
        for row in records(source["rows"], fields):
            lead = Decimal(row["stock_lt"]) * (7 if semantics["lead_time_unit"] == "WEEK" else 1)
            require(
                lead == lead.to_integral_value() and lead <= 1_000_000,
                "LEAD_TIME_CALENDAR_MAPPING_REQUIRED",
            )
            level = Decimal(row["svc_lv"]) / (
                100 if semantics["service_level_unit"] == "PERCENT" else 1
            )
            result.append(
                {
                    **item(row, master),
                    **{
                        k: row[k]
                        for k in (
                            "policy_id",
                            "effective_from",
                            "effective_to",
                            "physical_max_capacity",
                        )
                    },
                    "lead_time_days": int(lead),
                    "moq": row["min_po_qty"],
                    "order_multiple": row["po_lot_qty"],
                    "approved_service_level": quantity_text(level),
                    "source_target_inventory_qty": row["max_qty"],
                    "source_rop_qty": row["rop_qty"],
                    "policy_source_type": "SOURCE_MASTER",
                    "source_semantics_cd": "LEGACY_TARGET_INVENTORY",
                }
            )
    return result
