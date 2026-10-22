"""Verify development Source projection; realized demand is a separate, closed environment input."""

from dataclasses import dataclass
import json

from dsio_inventory_engine.inventory_contracts.replenishment import RecommendationRequest
from dsio_inventory_engine.inventory_contracts.values import (
    canonical_json,
    digest,
    hash_value,
    identifier,
    integer,
    quantity_text,
    require,
)
from .contracts import clone, input_errors, quantity, shape
from .development_source import DevelopmentSourceProducer, GENERATOR_VERSION


@input_errors
def validate_development_projection(
    result: dict,
    source_request: dict,
    request: RecommendationRequest,
    *,
    expected_source_hash: str,
) -> dict:
    hash_value(expected_source_hash)
    source = clone(result)
    DevelopmentSourceProducer().verify(
        source, source_request, expected_source_hash=expected_source_hash
    )
    data = RecommendationRequest.from_dict(request.to_dict()).to_dict()["canonical_input"]
    ctx, snaps = data["context"], data["snapshots"]
    require(
        all(source["scope"][k] == ctx[k] for k in ("company_cd", "subs_cd", "site_cd"))
        and source["w0"] == ctx["plan_start_date"] == ctx["master_as_of_date"]
        and source["master_revision"] == ctx["master_snapshot_revision"]
        and source["calendar_revision"] == snaps["calendar"]["snapshot_id"],
        "WARMUP_SOURCE_CONTEXT_MISMATCH",
    )
    item = source["scope"]["item_id"]
    master = snaps["master"]["rows"]
    require(
        len(master) == 1 and master[0]["item_id"] == item and master[0]["uom"] == "EA",
        "WARMUP_SOURCE_UNIVERSE_MISMATCH",
    )
    calendar = sorted(snaps["calendar"]["rows"], key=lambda r: r["seq"])
    require(len(calendar) in (52, 65), "CAUSAL_SCORE_OR_TAIL_HORIZON_REQUIRED")
    require(
        [(r["yyyyww"], r["start_date"]) for r in calendar]
        == [(r["yyyyww"], r["start_date"]) for r in source["planning_forecast"][: len(calendar)]],
        "WARMUP_SOURCE_CALENDAR_MISMATCH",
    )
    position = snaps["inventory"]["rows"]
    require(
        len(position) == 1 and all(position[0][k] == v for k, v in source["w0_position"].items()),
        "WARMUP_W0_POSITION_MISMATCH",
    )
    for kind in ("inventory", "prior_inventory", "receipts"):
        meta = snaps[kind]["metadata"]
        require(
            meta["generator_version"] == GENERATOR_VERSION
            and meta["simulation_run_id"] == "WU-" + expected_source_hash,
            "WARMUP_CANONICAL_LINEAGE_MISMATCH",
        )
    require(
        snaps["inventory"]["metadata"]["position_source_type"] == "SYNTHETIC_BOH"
        and not snaps["inventory"]["metadata"]["adjustments"]
        and not snaps["inventory"]["metadata"]["source_events"],
        "WARMUP_CANONICAL_SOURCE_INVALID",
    )
    forecast = {r["yyyyww"]: r for r in snaps["forecast"]["rows"]}
    customers = {
        r["yyyyww"]: r["confirmed_customer_order_qty"] for r in snaps["customer_orders"]["rows"]
    }
    weeks = {r["yyyyww"] for r in calendar}
    require(set(forecast) == weeks and set(customers) <= weeks, "WARMUP_FORECAST_COVERAGE_MISMATCH")
    for row in source["planning_forecast"][: len(calendar)]:
        f = forecast[row["yyyyww"]]
        require(
            f["forecast_qty"] == row["gross_forecast_qty"]
            and f["forecast_netting_mode"] == "SAME_BUCKET_CONSUMPTION"
            and customers.get(row["yyyyww"], "0") == row["confirmed_customer_order_qty"],
            "WARMUP_PLANNING_DEMAND_MISMATCH",
        )
    expected_receipts = {
        r["receipt_id"]: (r["due_date"], r["quantity"]) for r in source["w0_due_in"]
    }
    actual = snaps["receipts"]["rows"]
    require(
        len(actual) == len(expected_receipts)
        and {r["receipt_id"]: (r["due_date"], r["due_qty"]) for r in actual} == expected_receipts
        and all(
            r["status"] == "CONFIRMED" and r["supply_type"] == "SYNTHETIC_PURCHASE_ORDER"
            for r in actual
        ),
        "WARMUP_PIPELINE_PROJECTION_MISMATCH",
    )
    require(
        all(
            all(row[k] == v for k, v in source["physical_constraints"].items())
            for row in snaps["policies"]["rows"]
        ),
        "WARMUP_PHYSICAL_CONSTRAINT_MISMATCH",
    )
    return source


@dataclass(frozen=True)
class RealizedDemandPath:
    document_json: str

    @classmethod
    @input_errors
    def from_dict(cls, document: dict) -> "RealizedDemandPath":
        value = clone(document)
        shape(
            value,
            (
                "contract_id",
                "contract_version",
                "planning_request_hash",
                "path_id",
                "split",
                "seed",
                "rows",
            ),
        )
        require(
            value["contract_id"] == "inventory-robustness-realized-demand"
            and value["contract_version"] == "1.0.0",
            "CAUSAL_REALIZATION_CONTRACT_INVALID",
        )
        hash_value(value["planning_request_hash"])
        identifier(value["path_id"])
        require(
            value["split"] in ("NOMINAL", "SELECTION", "VALIDATION", "HOLDOUT", "STRESS"),
            "CAUSAL_REALIZATION_SPLIT_INVALID",
        )
        require(
            type(value["seed"]) is int and 0 < value["seed"] < 2**63,
            "CAUSAL_REALIZATION_SEED_INVALID",
        )
        require(
            type(value["rows"]) is list and len(value["rows"]) in (52, 65),
            "CAUSAL_REALIZATION_COVERAGE_INVALID",
        )
        for row in value["rows"]:
            shape(row, ("seq", "realized_gross_demand_qty"))
            integer(row["seq"])
            row["realized_gross_demand_qty"] = quantity_text(
                quantity(row["realized_gross_demand_qty"])
            )
        require(
            [r["seq"] for r in value["rows"]] == list(range(len(value["rows"]))),
            "CAUSAL_REALIZATION_COVERAGE_INVALID",
        )
        return cls(canonical_json(value))

    def to_dict(self) -> dict:
        return clone(json.loads(self.document_json))

    @property
    def content_hash(self) -> str:
        return digest(self.to_dict())
