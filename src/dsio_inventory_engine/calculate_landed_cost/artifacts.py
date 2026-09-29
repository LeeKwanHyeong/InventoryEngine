"""Append-only development Landed Cost child evidence and independent calculation replay."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Any, Protocol

from dsio_inventory_engine.inventory_contracts.landed_cost_artifact import (
    CHILD_CONTRACT_ID,
    CONTRACT_VERSION,
    LandedCostShipmentInput,
    landed_cost_run_binding,
)
from dsio_inventory_engine.inventory_contracts.trade_cost_projection import (
    TradeCostRevisionSetProjection,
)
from dsio_inventory_engine.inventory_contracts.values import (
    choice,
    digest,
    hash_value,
    identifier,
    integer,
    quantity_text,
    require,
    shape,
)
from dsio_inventory_engine.inventory_evidence.contracts import ArtifactObject, evidence_reference

from .projection import calculate_projection_shipment

CALCULATOR_KEY = "inventory.landed_cost.development-calculator"
CHILD_KEY = "inventory.landed_cost.child-artifact"
SHIPMENT_KEY = "inventory.landed_cost.shipment-input"
PROJECTION_KEY = "inventory.landed_cost.projection-source"
TOTAL_FIELDS = (
    "customs_value_amount",
    "gross_landed_cost_amount",
    "recoverable_tax_amount",
    "net_landed_cost_amount",
)


class LandedCostArtifactStore(Protocol):
    def commit_artifacts(self, artifacts: Sequence[ArtifactObject]) -> bool: ...
    def read_artifact(self, reference: str) -> ArtifactObject: ...


@dataclass(frozen=True, slots=True)
class LandedCostArtifactReceipt:
    reference: str
    content_hash: str
    input_content_hash: str
    row_count: int
    calculation_status: str
    replayed: bool


def calculator_implementation_hash() -> str:
    """Bind the source resolver and arithmetic implementation, not a caller-supplied label."""
    folder = Path(__file__).parent
    contracts = folder.parent / "inventory_contracts"
    paths = [
        folder / "application.py",
        folder / "projection.py",
        folder / "artifacts.py",
        contracts / "landed_cost_artifact.py",
        contracts / "trade_cost.py",
        contracts / "trade_cost_projection.py",
        contracts / "values.py",
    ]
    return digest({path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths})


def _seal(body: Mapping[str, Any]) -> dict:
    return {**body, "content_hash": digest(body)}


def _references(shipment: dict) -> dict[str, str]:
    binding = shipment["binding"]
    identity = {
        "engine_run_id": binding["engine_run_id"],
        "attempt_no": binding["attempt_no"],
        "shipment_id": shipment["shipment_id"],
        "lane_id": shipment["lane_id"],
    }
    reference = "IO-LC-" + digest(identity)[:40]
    return {
        "child": reference,
        "shipment": reference + ".shipment",
        "projection": reference + ".projection",
    }


def build_landed_cost_child(
    projection: TradeCostRevisionSetProjection,
    shipment: LandedCostShipmentInput,
) -> dict:
    inputs = shipment.to_dict()
    rows = calculate_projection_shipment(projection, shipment)
    require(bool(rows), "LANDED_COST_CHILD_EMPTY")
    all_calculable = all(row["calculation_status"] == "CALCULABLE" for row in rows)
    # Incomplete shipments do not expose a misleading partial grand total.
    with localcontext() as context:
        context.prec = 48
        totals = {
            field: quantity_text(
                sum((Decimal(row["assessment"][field]) for row in rows), Decimal(0))
            )
            if all_calculable
            else None
            for field in TOTAL_FIELDS
        }
    refs = _references(inputs)
    return _seal(
        {
            "contract_id": CHILD_CONTRACT_ID,
            "contract_version": CONTRACT_VERSION,
            "artifact_reference": refs["child"],
            "binding": inputs["binding"],
            "shipment_reference": refs["shipment"],
            "shipment_content_hash": inputs["content_hash"],
            "projection_reference": refs["projection"],
            "projection_content_hash": projection.to_dict()["projection_content_hash"],
            "calculator_binding": {
                "contract_key": CALCULATOR_KEY,
                "contract_version": CONTRACT_VERSION,
                "implementation_content_hash": calculator_implementation_hash(),
            },
            "calculation_purpose": "DEVELOPMENT_FIXTURE",
            "operational_eligible": False,
            "automatic_publish_allowed": False,
            "automatic_order_allowed": False,
            "cost_currency": inputs["cost_currency"],
            "row_count": len(rows),
            "calculation_status": "CALCULABLE" if all_calculable else "NOT_CALCULABLE",
            "rows": rows,
            "totals": totals,
        }
    )


def _child_header(document: dict) -> dict:
    result = shape(
        document,
        {
            "contract_id": choice(CHILD_CONTRACT_ID),
            "contract_version": choice(CONTRACT_VERSION),
            "artifact_reference": evidence_reference,
            "binding": landed_cost_run_binding,
            "shipment_reference": evidence_reference,
            "shipment_content_hash": hash_value,
            "projection_reference": evidence_reference,
            "projection_content_hash": hash_value,
            "calculator_binding": lambda value: shape(
                value,
                {
                    "contract_key": choice(CALCULATOR_KEY),
                    "contract_version": choice(CONTRACT_VERSION),
                    "implementation_content_hash": hash_value,
                },
            ),
            "calculation_purpose": choice("DEVELOPMENT_FIXTURE"),
            "operational_eligible": lambda value: require(
                value is False, "LANDED_COST_DEVELOPMENT_ONLY"
            ),
            "automatic_publish_allowed": lambda value: require(
                value is False, "LANDED_COST_PUBLICATION_FORBIDDEN"
            ),
            "automatic_order_allowed": lambda value: require(
                value is False, "LANDED_COST_ORDER_FORBIDDEN"
            ),
            "cost_currency": identifier,
            "row_count": integer,
            "calculation_status": choice("CALCULABLE", "NOT_CALCULABLE"),
            "rows": lambda value: value,
            "totals": lambda value: value,
            "content_hash": hash_value,
        },
    )
    require(
        type(result["rows"]) is list
        and result["row_count"] == len(result["rows"])
        and result["row_count"] > 0,
        "LANDED_COST_CHILD_ROW_COUNT_INVALID",
    )
    return document


class PersistLandedCostChildArtifactUseCase:
    def __init__(self, store: LandedCostArtifactStore):
        self.store = store

    def execute(
        self, *, projection: TradeCostRevisionSetProjection, shipment: LandedCostShipmentInput
    ) -> LandedCostArtifactReceipt:
        inputs = shipment.to_dict()
        child = build_landed_cost_child(projection, shipment)
        projected = _seal(
            {
                "contract_id": "io-landed-cost-projection-source-v1",
                "contract_version": CONTRACT_VERSION,
                "projection": projection.to_dict(),
            }
        )
        artifacts = (
            ArtifactObject.from_document(
                reference=child["shipment_reference"], contract_key=SHIPMENT_KEY, document=inputs
            ),
            ArtifactObject.from_document(
                reference=child["projection_reference"],
                contract_key=PROJECTION_KEY,
                document=projected,
            ),
            ArtifactObject.from_document(
                reference=child["artifact_reference"], contract_key=CHILD_KEY, document=child
            ),
        )
        replayed = self.store.commit_artifacts(artifacts)
        verified = ReadAndVerifyLandedCostChildArtifactUseCase(self.store).execute(
            reference=child["artifact_reference"],
            expected_content_hash=child["content_hash"],
            expected_binding=inputs["binding"],
            expected_shipment_hash=inputs["content_hash"],
        )
        return LandedCostArtifactReceipt(
            child["artifact_reference"],
            verified["content_hash"],
            inputs["content_hash"],
            verified["row_count"],
            verified["calculation_status"],
            replayed,
        )


class ReadAndVerifyLandedCostChildArtifactUseCase:
    def __init__(self, store: LandedCostArtifactStore):
        self.store = store

    def execute(
        self,
        *,
        reference: str,
        expected_content_hash: str,
        expected_binding: Mapping[str, Any],
        expected_shipment_hash: str,
    ) -> dict:
        """Check externally pinned identity, then recompute from saved source bytes."""
        child_object = self.store.read_artifact(evidence_reference(reference))
        require(child_object.contract_key == CHILD_KEY, "LANDED_COST_ARTIFACT_CONTRACT_MISMATCH")
        require(
            child_object.content_hash == hash_value(expected_content_hash),
            "LANDED_COST_ARTIFACT_HASH_MISMATCH",
        )
        child = _child_header(child_object.document())
        require(child["artifact_reference"] == reference, "LANDED_COST_REFERENCE_MISMATCH")
        require(
            child["binding"] == landed_cost_run_binding(dict(expected_binding)),
            "LANDED_COST_RUN_BINDING_MISMATCH",
        )
        require(
            child["shipment_content_hash"] == hash_value(expected_shipment_hash),
            "LANDED_COST_INPUT_HASH_MISMATCH",
        )
        input_object = self.store.read_artifact(child["shipment_reference"])
        require(
            input_object.contract_key == SHIPMENT_KEY
            and input_object.content_hash == expected_shipment_hash,
            "LANDED_COST_INPUT_HASH_MISMATCH",
        )
        inputs = LandedCostShipmentInput.from_dict(input_object.document())
        require(inputs.to_dict()["binding"] == child["binding"], "LANDED_COST_RUN_BINDING_MISMATCH")
        projected_object = self.store.read_artifact(child["projection_reference"])
        require(
            projected_object.contract_key == PROJECTION_KEY,
            "LANDED_COST_ARTIFACT_CONTRACT_MISMATCH",
        )
        projected = shape(
            projected_object.document(),
            {
                "contract_id": choice("io-landed-cost-projection-source-v1"),
                "contract_version": choice(CONTRACT_VERSION),
                "projection": lambda value: value,
                "content_hash": hash_value,
            },
        )
        projection = TradeCostRevisionSetProjection.from_dict(projected["projection"])
        require(
            projection.to_dict()["projection_content_hash"] == child["projection_content_hash"],
            "LANDED_COST_PROJECTION_HASH_MISMATCH",
        )
        require(
            child["calculator_binding"]["implementation_content_hash"]
            == calculator_implementation_hash(),
            "LANDED_COST_CALCULATOR_VERSION_UNAVAILABLE",
        )
        expected = build_landed_cost_child(projection, inputs)
        require(child == expected, "LANDED_COST_REPLAY_MISMATCH")
        return child


__all__ = [
    "LandedCostArtifactReceipt",
    "PersistLandedCostChildArtifactUseCase",
    "ReadAndVerifyLandedCostChildArtifactUseCase",
    "build_landed_cost_child",
]
