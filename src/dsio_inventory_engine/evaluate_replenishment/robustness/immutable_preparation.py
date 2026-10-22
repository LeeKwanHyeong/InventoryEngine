"""One bounded preparation slot owned by one local Catalog execution context.

Only internally validated JSON strings are retained. No candidate, observation,
inventory state, admission or result is cached. Keys compare actual complete
inputs, not a caller's claimed hash or Python object identity. A new execution
or durable replay gets a new slot; this is not a persisted approval receipt.
"""

from dataclasses import dataclass
import json

from dsio_inventory_engine.inventory_contracts.canonical import CanonicalInputRequest
from dsio_inventory_engine.inventory_contracts.replenishment import RecommendationRequest
from dsio_inventory_engine.inventory_contracts.values import canonical_json, digest, require
from dsio_inventory_engine.prepare_inventory.application.inventory_input import (
    PrepareInventoryInputUseCase,
)
from .source_binding import validate_development_projection

MAX_RETAINED_BYTES = 128_000_000


@dataclass(frozen=True)
class PreparedCausalInput:
    request_json: str
    canonical_json: str
    prepared_json: str
    input_hash: str
    source_hash: str

    def checkout(self) -> tuple[dict, CanonicalInputRequest, dict]:
        # Each caller receives detached policy rows, receipts and initial stock.
        # These strings were normalized/validated inside prepare(), never supplied
        # as a trusted precomputed payload by an external caller.
        return (
            json.loads(self.request_json),
            CanonicalInputRequest(self.canonical_json),
            json.loads(self.prepared_json),
        )


class ImmutableCausalPreparation:
    def __init__(self, *, enabled: bool = True):
        require(type(enabled) is bool, "CAUSAL_REUSE_FLAG_INVALID")
        self.enabled = enabled
        self._slot = None
        self.hits = 0
        self.preparations = 0

    def prepare(
        self,
        deployment,
        request: RecommendationRequest,
        source: dict,
        source_request: dict,
        expected_source_hash: str,
    ) -> PreparedCausalInput:
        require(deployment.environment == "DEVELOPMENT", "CAUSAL_DEVELOPMENT_ONLY")
        require(type(request) is RecommendationRequest, "CAUSAL_REQUEST_TYPE_INVALID")
        require(type(source) is dict and type(source_request) is dict, "CAUSAL_SOURCE_INVALID")
        key = (
            deployment.company_cd,
            deployment.environment,
            request.document_json,
            canonical_json(source),
            canonical_json(source_request),
            expected_source_hash,
        )
        require(
            all(type(part) is str and len(part.encode()) <= MAX_RETAINED_BYTES for part in key),
            "CAUSAL_PREPARATION_SIZE_LIMIT",
        )
        if self.enabled and self._slot is not None and self._slot[0] == key:
            self.hits += 1
            return self._slot[1]
        # A changed or invalid request can never borrow an earlier preparation.
        self._slot = None
        self.preparations += 1
        normalized = RecommendationRequest.from_dict(request.to_dict())
        data = normalized.to_dict()
        execution = data["execution"]
        require(
            execution["contract_version"] == "2.0.0"
            and execution["execution_mode"] == "LOCAL_SHADOW"
            and execution["execution_purpose"] == "SHADOW"
            and execution["strategy"]["strategy_type"] == "MATHEMATICAL",
            "CAUSAL_LOCAL_MATHEMATICAL_V2_SHADOW_ONLY",
        )
        verified = validate_development_projection(
            source,
            source_request,
            normalized,
            expected_source_hash=expected_source_hash,
        )
        canonical = CanonicalInputRequest.from_dict(data["canonical_input"])
        prepared = PrepareInventoryInputUseCase(deployment).execute(canonical).to_dict()
        value = PreparedCausalInput(
            normalized.document_json,
            canonical.document_json,
            canonical_json(prepared),
            digest(execution),
            verified["content_hash"],
        )
        retained = sum(len(part.encode()) for part in (*key, *vars(value).values()))
        # Large valid inputs still compute; they simply do not occupy the slot.
        if self.enabled and retained <= MAX_RETAINED_BYTES:
            self._slot = (key, value)
        return value
