"""Pure, pinned feature producer for LEGACY_STYLE; never reads or writes a database."""

from datetime import date, datetime, timedelta
from decimal import Context, Decimal, DecimalException, ROUND_HALF_EVEN, localcontext
import json
import re
from typing import Any

from dsio_inventory_engine.classify_inventory.legacy_rules import (
    LegacyStyleRules,
    SealedLegacyDocument,
)
from dsio_inventory_engine.inventory_contracts.segmentation import (
    SearchGroupAssignment,
    SegmentationResult,
)
from dsio_inventory_engine.inventory_contracts.values import (
    InventoryInputError,
    canonical_json,
    day,
    digest,
    hash_value,
    item_identifier,
    quantity_text,
    require,
    text,
    timestamp,
)
from dsio_inventory_engine.recommend_replenishment.application.math_policy import (
    calculate_policy,
    history_statistics,
)

DOMAINS = ("history", "lead_time", "unit_cost")
SCOPE_KEYS = ("company_cd", "subs_cd", "site_cd")
CONTEXT_KEYS = {
    *SCOPE_KEYS,
    "decision_yyyyww",
    "master_as_of_date",
    "input_cutoff",
    "calendar_snapshot_id",
    "master_snapshot_revision",
    "base_configuration_hash",
    "classification_snapshot_id",
    "classification_content_hash",
    "reference_calculator_build_hash",
}
SOURCE_EXTRA_KEYS = {
    "history": {"quantity_uom", "quantity_semantics", "closed_yyyyww"},
    "lead_time": {"time_uom"},
    "unit_cost": {"price_uom"},
}
ROW_KEYS = {
    "history": {"item_id", "yyyyww", "demand_qty"},
    "lead_time": {"item_id", "lead_time_days"},
    "unit_cost": {"item_id", "unit_cost", "currency", "price_as_of"},
}


def _keys(value: Any, keys: set[str], code: str = "LS_SOURCE_BINDING_MISMATCH") -> None:
    require(isinstance(value, dict) and set(value) == keys, code)


def _week_start(value: str) -> date:
    try:
        require(
            isinstance(value, str) and len(value) == 6 and value.isdigit(),
            "LS_SCOPE_OR_W0_MISMATCH",
        )
        return date.fromisocalendar(int(value[:4]), int(value[4:]), 1)
    except ValueError:
        raise InventoryInputError("LS_SCOPE_OR_W0_MISMATCH") from None


def abc_result_from_dict(value: dict) -> SegmentationResult:
    _keys(
        value,
        {
            "contract_version",
            "method_id",
            "implementation_version",
            "source_content_hash",
            "configuration_hash",
            "assignments",
        },
    )
    require(
        value["contract_version"] == "1.0.0" and value["method_id"] == "ABC_XYZ",
        "LS_SOURCE_BINDING_MISMATCH",
    )
    require(isinstance(value["assignments"], list), "LS_SOURCE_BINDING_MISMATCH")
    rows = []
    for row in value["assignments"]:
        _keys(row, {"item_id", "search_group_id", "status", "reason_code", "abc_xyz_class"})
        require(row["search_group_id"] == row["abc_xyz_class"], "LS_SOURCE_BINDING_MISMATCH")
        rows.append(SearchGroupAssignment(**row))
    return SegmentationResult(
        value["method_id"],
        value["implementation_version"],
        value["source_content_hash"],
        value["configuration_hash"],
        tuple(rows),
    )


def normalize_feature_request(request: dict) -> dict:
    """Reject malformed JSON-shaped values with the documented run error contract."""
    try:
        return _normalize_feature_request(request)
    except InventoryInputError as error:
        if str(error).startswith("LS_"):
            raise
        raise InventoryInputError("LS_SOURCE_BINDING_MISMATCH") from None
    except (KeyError, TypeError, ValueError, OverflowError, RecursionError):
        raise InventoryInputError("LS_SOURCE_BINDING_MISMATCH") from None


def _normalize_feature_request(request: dict) -> dict:
    """Canonical row order, closed shapes and temporal/scope integrity, not an approval."""
    _keys(
        request,
        {
            "contract_version",
            "context",
            "universe",
            "calendar",
            "sources",
            "abc_result",
            "item_exceptions",
        },
    )
    require(request["contract_version"] == "1.0.0", "LS_SOURCE_BINDING_MISMATCH")
    value = json.loads(canonical_json(request))
    context = value["context"]
    _keys(context, CONTEXT_KEYS)
    for key in CONTEXT_KEYS - {"decision_yyyyww", "master_as_of_date", "input_cutoff"}:
        text(context[key])
    for key in (
        "base_configuration_hash",
        "classification_content_hash",
        "reference_calculator_build_hash",
    ):
        hash_value(context[key])
    w0 = _week_start(context["decision_yyyyww"])
    day(context["master_as_of_date"])
    require(date.fromisoformat(context["master_as_of_date"]) <= w0, "LS_SCOPE_OR_W0_MISMATCH")
    context["input_cutoff"] = timestamp(context["input_cutoff"])
    require(
        isinstance(value["universe"], list) and bool(value["universe"]),
        "LS_SOURCE_BINDING_MISMATCH",
    )
    ids = set()
    for row in value["universe"]:
        _keys(row, {"item_id", "uom"})
        item_identifier(row["item_id"])
        require(row["item_id"] not in ids, "LS_DUPLICATE_ITEM")
        ids.add(row["item_id"])
    value["universe"].sort(key=lambda row: row["item_id"])
    require(
        isinstance(value["calendar"], list) and len(value["calendar"]) == 26,
        "LS_SCOPE_OR_W0_MISMATCH",
    )
    for row in value["calendar"]:
        _keys(row, {"yyyyww", "start_date"})
        require(
            _week_start(row["yyyyww"]).isoformat() == row["start_date"], "LS_SCOPE_OR_W0_MISMATCH"
        )
    value["calendar"].sort(key=lambda row: row["start_date"])
    require(
        [row["start_date"] for row in value["calendar"]]
        == [(w0 - timedelta(weeks=26 - i)).isoformat() for i in range(26)],
        "LS_SCOPE_OR_W0_MISMATCH",
    )
    weeks = {row["yyyyww"] for row in value["calendar"]}
    abc = abc_result_from_dict(value["abc_result"])
    require({row.item_id for row in abc.assignments} == ids, "LS_SOURCE_BINDING_MISMATCH")
    require(
        abc.configuration_hash == context["base_configuration_hash"], "LS_SOURCE_BINDING_MISMATCH"
    )
    value["abc_result"] = abc.to_dict()
    _keys(value["sources"], set(DOMAINS))
    for domain, source in value["sources"].items():
        if source is None:
            continue
        _keys(
            source,
            {
                "source_id",
                "revision",
                "scope",
                "decision_yyyyww",
                "evidence_kind",
                "available_at",
                "generator",
                "rows",
            }
            | SOURCE_EXTRA_KEYS[domain],
        )
        text(source["source_id"])
        text(source["revision"])
        require(
            source["scope"] == {key: context[key] for key in SCOPE_KEYS}
            and source["decision_yyyyww"] == context["decision_yyyyww"],
            "LS_SCOPE_OR_W0_MISMATCH",
        )
        source["available_at"] = timestamp(source["available_at"])
        require(isinstance(source["rows"], list), "LS_SOURCE_BINDING_MISMATCH")
        seen = set()
        for row in source["rows"]:
            _keys(row, ROW_KEYS[domain])
            item_identifier(row["item_id"])
            require(row["item_id"] in ids, "LS_ORPHAN_ITEM")
            key = (row["item_id"], row["yyyyww"]) if domain == "history" else row["item_id"]
            require(key not in seen, "LS_DUPLICATE_ITEM")
            seen.add(key)
            if domain == "history":
                require(row["yyyyww"] in weeks, "LS_SCOPE_OR_W0_MISMATCH")
        source["rows"].sort(key=lambda row: (row["item_id"], row.get("yyyyww", "")))
        if domain == "history":
            closed = source["closed_yyyyww"]
            require(
                isinstance(closed, list) and all(isinstance(w, str) for w in closed),
                "LS_SOURCE_BINDING_MISMATCH",
            )
            require(
                len(set(closed)) == len(closed) and set(closed) <= weeks, "LS_SCOPE_OR_W0_MISMATCH"
            )
            source["closed_yyyyww"] = sorted(closed)
    exceptions = value["item_exceptions"]
    require(isinstance(exceptions, list), "LS_OVERRIDE_INVALID")
    intervals: dict[str, list[tuple[str, str]]] = {}
    for row in exceptions:
        _keys(
            row,
            {
                *SCOPE_KEYS,
                "item_id",
                "profile_id",
                "reason",
                "source_revision",
                "effective_from",
                "effective_to",
            },
            "LS_OVERRIDE_INVALID",
        )
        require(
            all(row[key] == context[key] for key in SCOPE_KEYS) and row["item_id"] in ids,
            "LS_OVERRIDE_INVALID",
        )
        require(
            isinstance(row["profile_id"], str)
            and row["profile_id"] in {"SMALL", "MEDIUM", "LARGE"}
            and isinstance(row["reason"], str)
            and 0 < len(row["reason"].strip()) <= 500,
            "LS_OVERRIDE_INVALID",
        )
        require(
            isinstance(row["source_revision"], str)
            and 0 < len(row["source_revision"].strip()) <= 512,
            "LS_OVERRIDE_INVALID",
        )
        try:
            day(row["effective_from"])
            day(row["effective_to"])
        except InventoryInputError:
            raise InventoryInputError("LS_OVERRIDE_INVALID") from None
        require(row["effective_from"] < row["effective_to"], "LS_OVERRIDE_INVALID")
        previous = intervals.setdefault(row["item_id"], [])
        require(
            all(
                row["effective_from"] >= end or start >= row["effective_to"]
                for start, end in previous
            ),
            "LS_OVERRIDE_DUPLICATE",
        )
        previous.append((row["effective_from"], row["effective_to"]))
    exceptions.sort(key=lambda row: (row["item_id"], row["effective_from"]))
    return value


def feature_input_binding(request: dict) -> dict:
    """Author an Offline pin; callers must retain it separately. Does not approve a Source."""
    value = normalize_feature_request(request)
    return {
        "context_hash": digest(value["context"]),
        "universe_hash": digest(value["universe"]),
        "calendar_hash": digest(value["calendar"]),
        "abc_result_hash": digest(value["abc_result"]),
        "item_exceptions_hash": digest(value["item_exceptions"]),
        "source_bindings": {
            domain: None
            if source is None
            else {
                "source_id": source["source_id"],
                "revision": source["revision"],
                "content_hash": digest(source),
            }
            for domain, source in value["sources"].items()
        },
    }


def _number(value: Any, reasons: set[str], *, positive: bool = False) -> Decimal | None:
    if value is None:
        reasons.add("LS_MISSING_REQUIRED_FACTOR")
        return None
    try:
        require(
            isinstance(value, str)
            and 0 < len(value) <= 40
            and re.fullmatch(r"-?[0-9]+(?:\.[0-9]+)?", value) is not None,
            "LS_INVALID_FEATURE_VALUE",
        )
        number = Decimal(value)
        require(
            number.is_finite() and number.copy_abs() <= Decimal("1000000000000"),
            "LS_INVALID_FEATURE_VALUE",
        )
        if positive and number <= 0:
            reasons.add("LS_NON_POSITIVE_UNIT_COST")
            return None
        require(number >= 0, "LS_INVALID_FEATURE_VALUE")
        return number
    except (DecimalException, InventoryInputError):
        reasons.add("LS_INVALID_FEATURE_VALUE")
        return None


def _source_reasons(source: dict | None, context: dict) -> set[str]:
    if source is None:
        return {"LS_MISSING_REQUIRED_FACTOR"}
    reasons = set()
    kind, generator = source["evidence_kind"], source["generator"]
    valid_generator = (
        isinstance(generator, dict)
        and set(generator) == {"version", "seed", "input_revision"}
        and all(
            isinstance(generator[k], str) and bool(generator[k].strip())
            for k in ("version", "input_revision")
        )
        and type(generator["seed"]) is int
    )
    if kind not in ("OBSERVED_PINNED", "SYNTHETIC_PINNED") or (
        kind == "SYNTHETIC_PINNED" and not valid_generator
    ):
        reasons.add("LS_SOURCE_UNVERIFIED")
    if datetime.fromisoformat(
        source["available_at"].replace("Z", "+00:00")
    ) > datetime.fromisoformat(context["input_cutoff"].replace("Z", "+00:00")):
        reasons.add("LS_FUTURE_OR_STALE_FEATURE")
    return reasons


class LegacyFeatureSnapshot(SealedLegacyDocument):
    __slots__ = ()


class LegacyFeatureProducer:
    """Create an immutable feature artifact from explicit, externally pinned input only."""

    implementation_version = "1.0.0"

    def __init__(self, rules: LegacyStyleRules, *, reference_calculator_build_hash: str):
        rules.to_dict()
        self.rules = rules
        self.reference_calculator_build_hash = hash_value(reference_calculator_build_hash)

    def produce(self, request: dict, *, expected_binding: dict) -> LegacyFeatureSnapshot:
        value = normalize_feature_request(request)
        require(feature_input_binding(value) == expected_binding, "LS_SOURCE_BINDING_MISMATCH")
        require(
            value["context"]["reference_calculator_build_hash"]
            == self.reference_calculator_build_hash,
            "LS_SOURCE_BINDING_MISMATCH",
        )
        with localcontext(Context(prec=40, rounding=ROUND_HALF_EVEN)):
            features = self._features(value)
        return LegacyFeatureSnapshot.seal(
            {
                "contract_id": "inventory-legacy-style-features",
                "contract_version": "1.0.0",
                "implementation_version": self.implementation_version,
                "feature_snapshot_id": "LSF-"
                + digest(
                    {
                        "input_binding": expected_binding,
                        "rules_profile_hash": self.rules.content_hash,
                        "implementation_version": self.implementation_version,
                    }
                ),
                "rules_profile_hash": self.rules.content_hash,
                "input": value,
                "input_binding": expected_binding,
                "features": features,
            }
        )

    def verify(self, snapshot: LegacyFeatureSnapshot) -> LegacyFeatureSnapshot:
        value = snapshot.to_dict()
        _keys(
            value,
            {
                "contract_id",
                "contract_version",
                "implementation_version",
                "feature_snapshot_id",
                "rules_profile_hash",
                "input",
                "input_binding",
                "features",
                "content_hash",
            },
        )
        require(
            value["contract_id"] == "inventory-legacy-style-features"
            and value["contract_version"] == "1.0.0",
            "LS_SOURCE_BINDING_MISMATCH",
        )
        require(value["rules_profile_hash"] == self.rules.content_hash, "LS_RULES_HASH_MISMATCH")
        rebuilt = self.produce(value["input"], expected_binding=value["input_binding"])
        require(rebuilt.content_hash == snapshot.content_hash, "LS_SOURCE_BINDING_MISMATCH")
        return rebuilt

    def _features(self, request: dict) -> list[dict]:
        sources, context = request["sources"], request["context"]
        reason_order = self.rules.to_dict()["missing_data"]["primary_reason_order"]
        source_reasons = set().union(*(_source_reasons(s, context) for s in sources.values()))
        history, lead_source, price_source = (sources[key] for key in DOMAINS)
        expected_weeks = {row["yyyyww"] for row in request["calendar"]}
        if history is not None:
            if history["quantity_uom"] != "EA":
                source_reasons.add("LS_UNSUPPORTED_UOM")
            if history["quantity_semantics"] != "UNCENSORED_DEMAND":
                source_reasons.add("LS_SOURCE_UNVERIFIED")
            if set(history["closed_yyyyww"]) != expected_weeks:
                source_reasons.add("LS_HISTORY_INCOMPLETE")
        for source, field, unit in (
            (lead_source, "time_uom", "DAY"),
            (price_source, "price_uom", "EA"),
        ):
            if source is not None and source[field] != unit:
                source_reasons.add("LS_UNSUPPORTED_UOM")
        by_item = {row["item_id"]: [] for row in request["universe"]}
        for row in [] if history is None else history["rows"]:
            by_item[row["item_id"]].append(row)
        leads = {r["item_id"]: r for r in ([] if lead_source is None else lead_source["rows"])}
        prices = {r["item_id"]: r for r in ([] if price_source is None else price_source["rows"])}
        result = []
        for item in request["universe"]:
            reasons = set(source_reasons)
            if item["uom"] != "EA":
                reasons.add("LS_UNSUPPORTED_UOM")
            observations = by_item[item["item_id"]]
            if {r["yyyyww"] for r in observations} != expected_weeks:
                reasons.add("LS_HISTORY_INCOMPLETE")
            values = [_number(row["demand_qty"], reasons) for row in observations]
            lead = leads.get(item["item_id"], {}).get("lead_time_days")
            if lead is None:
                reasons.add("LS_MISSING_REQUIRED_FACTOR")
            elif type(lead) is not int or not 0 <= lead <= 3660:
                reasons.add("LS_INVALID_FEATURE_VALUE")
            price = prices.get(item["item_id"])
            cost = _number(None if price is None else price["unit_cost"], reasons, positive=True)
            if price is not None:
                if price["currency"] != "USD":
                    reasons.add("LS_CURRENCY_MISMATCH")
                try:
                    age = (
                        date.fromisoformat(context["master_as_of_date"])
                        - date.fromisoformat(day(price["price_as_of"]))
                    ).days
                    if not 0 <= age <= 91:
                        reasons.add("LS_FUTURE_OR_STALE_FEATURE")
                except (InventoryInputError, ValueError, TypeError):
                    reasons.add("LS_FUTURE_OR_STALE_FEATURE")
            numeric_values = [v for v in values if v is not None]
            if len(numeric_values) == 26 and sum(numeric_values) == 0:
                reasons.add("LS_ZERO_MEAN_DEMAND")
            feature = {
                "item_id": item["item_id"],
                "physical_lead_time_days": lead,
                "unit_cost_usd": None if cost is None else quantity_text(cost),
                "history_observation_count": len(observations),
                "reference_policy": None,
                "reference_statistics": None,
                "reference_trace": None,
                "rop_cover_numerator": None,
                "rop_cover_denominator": None,
                "rop_cover_months": None,
            }
            if not reasons:
                try:
                    stats = history_statistics(
                        {
                            "profile": {"stddev_ddof": 0},
                            "calendar": request["calendar"],
                            "history": observations,
                        },
                        [item],
                    )[item["item_id"]]
                    policy, trace = calculate_policy(
                        stats,
                        {"lead_time_days": lead, "approved_service_level": "0.95"},
                        {"replenishment_cycle_weeks": 1},
                        0,
                    )
                    # Source strings have <=40 characters. Extra precision keeps
                    # their sum/product exact; the diagnostic division stays at 40.
                    with localcontext(Context(prec=80, rounding=ROUND_HALF_EVEN)):
                        numerator = Decimal(policy["rop_qty"]) * 26 * 12
                        denominator = sum(numeric_values) * 52
                    feature.update(
                        reference_policy=policy,
                        reference_statistics=stats,
                        reference_trace=trace,
                        rop_cover_numerator=format(numerator, "f"),
                        rop_cover_denominator=format(denominator, "f"),
                        rop_cover_months=quantity_text(numerator / denominator),
                    )
                except (InventoryInputError, DecimalException):
                    reasons.add("LS_REFERENCE_ROP_UNAVAILABLE")
            ordered = [r for r in reason_order if r in reasons]
            feature.update(
                status="UNCLASSIFIED" if ordered else "READY",
                reason_codes=ordered,
                primary_reason=None if not ordered else ordered[0],
            )
            result.append(feature)
        return result
