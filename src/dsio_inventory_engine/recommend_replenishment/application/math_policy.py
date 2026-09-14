"""Frozen historical mean/normal (s,S) reference policy, not a service guarantee."""

from decimal import Context, Decimal, ROUND_CEILING, ROUND_HALF_EVEN, localcontext
from statistics import NormalDist

from dsio_inventory_engine.inventory_contracts.values import quantity_text, require, digest
from .guard import policy_at

EMPTY_POLICY = {"safety_stock_qty": None, "rop_qty": None, "target_inventory_qty": None}


def history_statistics(snapshot: dict, master: list[dict]) -> dict:
    """Missing weeks are not zero demand. No planning-period realized demand is read."""
    by_item: dict[str, dict] = {r["item_id"]: {} for r in master}
    for row in snapshot["history"]:
        by_item[row["item_id"]][row["yyyyww"]] = Decimal(row["demand_qty"])
    weeks = [r["yyyyww"] for r in sorted(snapshot["calendar"], key=lambda r: r["start_date"])]
    result = {}
    for item, observations in by_item.items():
        missing = [w for w in weeks if w not in observations]
        stats = {
            "item_id": item,
            "observation_count": len(observations),
            "missing_weeks": missing,
            "mean_weekly_demand": None,
            "variance": None,
            "stddev": None,
            "stddev_ddof": snapshot["profile"]["stddev_ddof"],
            "positive_demand_weeks": sum(q > 0 for q in observations.values()),
        }
        if not missing:
            values = [observations[w] for w in weeks]
            mean = sum(values, Decimal(0)) / len(values)
            variance = sum(((q - mean) ** 2 for q in values), Decimal(0)) / (
                len(values) - stats["stddev_ddof"]
            )
            stats.update(
                mean_weekly_demand=quantity_text(mean),
                variance=quantity_text(variance),
                stddev=quantity_text(variance.sqrt()),
            )
        result[item] = stats
    return result


def calculate_policy(stats: dict, source: dict, profile: dict, scale: int) -> tuple[dict, dict]:
    """Ceil each derived quantity to its UOM; never round it to the order multiple here."""
    if stats["missing_weeks"]:
        return dict(EMPTY_POLICY), {}
    mean, deviation = Decimal(stats["mean_weekly_demand"]), Decimal(stats["stddev"])
    # SDE quantiles widen/narrow the demand-protection window.  They must not
    # alter the physical supplier due date used by the shared order guard.
    physical_lead_time_days = source["lead_time_days"]
    protection_lead_time_days = source.get(
        "effective_protection_lead_time_days", physical_lead_time_days
    )
    # Match the common forward boundary mapping for complete 7-day buckets.
    lead = Decimal((protection_lead_time_days + 6) // 7)
    cycle = Decimal(
        source.get(
            "effective_review_cycle_weeks",
            profile["replenishment_cycle_weeks"],
        )
    )
    factor = Decimal(str(NormalDist().inv_cdf(float(source["approved_service_level"])))).quantize(
        Decimal("0.000000000001"), rounding=ROUND_HALF_EVEN
    )
    quantum = Decimal(1).scaleb(-scale)
    raw_safety = factor * deviation * lead.sqrt()
    safety = raw_safety.quantize(quantum, rounding=ROUND_CEILING)
    raw_rop = mean * lead + safety
    rop = raw_rop.quantize(quantum, rounding=ROUND_CEILING)
    raw_target = rop + mean * cycle
    target = raw_target.quantize(quantum, rounding=ROUND_CEILING)
    require(target <= Decimal("1000000000000"), "CALCULATED_POLICY_RANGE")
    result = {
        "safety_stock_qty": quantity_text(safety),
        "rop_qty": quantity_text(rop),
        "target_inventory_qty": quantity_text(target),
    }
    trace = {
        "service_level_factor": quantity_text(factor),
        "lead_time_weeks": quantity_text(lead),
        "source_lead_time_days": physical_lead_time_days,
        "replenishment_cycle_weeks": int(cycle),
        "raw_safety_stock_qty": quantity_text(raw_safety),
        "raw_rop_qty": quantity_text(raw_rop),
        "raw_target_inventory_qty": quantity_text(raw_target),
        "rounding": "SEQUENTIAL_UOM_CEILING",
    }
    if "source_approved_service_level" in source:
        trace.update(
            source_approved_service_level=source["source_approved_service_level"],
            effective_target_service_level=source["approved_service_level"],
            source_master_lead_time_days=physical_lead_time_days,
            physical_due_lead_time_days=physical_lead_time_days,
            effective_protection_lead_time_basis=source.get("effective_protection_lead_time_basis"),
            effective_protection_lead_time_days=protection_lead_time_days,
            effective_policy_hash=source["classification_effective_policy_hash"],
        )
    return result, trace


def select_policy(
    calculated: dict, adjustments: list[dict], decision_date: str
) -> tuple[dict | None, str, str | None]:
    active = {
        r["kind"]: r
        for r in adjustments
        if r["effective_from"] <= decision_date < r["effective_to"]
    }
    if "APPROVED_OVERRIDE" in active:
        row = active["APPROVED_OVERRIDE"]
        return row["values"], row["kind"], row["adjustment_id"]
    if calculated["rop_qty"] is not None:
        return calculated, "PYTHON_CALCULATED", None
    for kind in ("SOURCE_FALLBACK", "LEGACY_FALLBACK"):
        if kind in active:
            row = active[kind]
            return row["values"], kind, row["adjustment_id"]
    return None, "EXCLUDED", None


def build_policy_report(snapshot: dict, prepared: dict, input_hash: str) -> dict:
    with localcontext(Context(prec=40, rounding=ROUND_HALF_EVEN)):
        stats = history_statistics(snapshot, prepared["master"])
        policies: dict[str, list] = {r["item_id"]: [] for r in prepared["master"]}
        adjustments: dict[str, list] = {item: [] for item in policies}
        for row in prepared["policies"]:
            policies[row["item_id"]].append(row)
        for row in snapshot["adjustments"]:
            adjustments[row["item_id"]].append(row)
        rules = {
            r["uom"]: r.get("planning_scale", r.get("scale"))
            for r in prepared["manifest"]["quantity_rules"]
        }
        evidence = []
        for item in prepared["master"]:
            item_id = item["item_id"]
            for bucket in prepared["calendar"]:
                source = policy_at(policies[item_id], bucket["start_date"])
                calculated, trace = calculate_policy(
                    stats[item_id], source, snapshot["profile"], rules[item["uom"]]
                )
                effective, selected, adjustment_id = select_policy(
                    calculated, adjustments[item_id], bucket["start_date"]
                )
                row = {
                    "decision_id": "DEC-" + digest([input_hash, item_id, bucket["yyyyww"]]),
                    "item_id": item_id,
                    "uom": item["uom"],
                    "yyyyww": bucket["yyyyww"],
                    "decision_date": bucket["start_date"],
                    "source_policy_id": source["policy_id"],
                    "source_policy": source,
                    "python_calculated_policy": calculated,
                    "effective_policy": effective,
                    "effective_policy_source": selected,
                    "adjustment_id": adjustment_id,
                    "history_status": "INSUFFICIENT_DEMAND_HISTORY"
                    if stats[item_id]["missing_weeks"]
                    else "COMPLETE",
                    "calculation_trace": trace,
                    "source_comparison": {
                        key: None
                        if source[source_key] is None or calculated[key] is None
                        else quantity_text(Decimal(calculated[key]) - Decimal(source[source_key]))
                        for key, source_key in (
                            ("rop_qty", "source_rop_qty"),
                            ("target_inventory_qty", "source_target_inventory_qty"),
                        )
                    },
                }
                evidence.append(row)
        return {"history_statistics": list(stats.values()), "policy_evidence": evidence}
