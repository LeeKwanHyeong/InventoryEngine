"""Explicit binding of a production policy request to a synthetic evaluation World."""

from dataclasses import dataclass

from .mathematical import MathematicalPolicyRequest
from .training import TrainingRequest
from .values import canonical_json, choice, digest, identifier, read_json, shape


@dataclass(frozen=True)
class ProductionEvaluationRequest:
    document_json: str

    @classmethod
    def from_dict(cls, value: dict) -> "ProductionEvaluationRequest":
        data = shape(
            value,
            {
                "contract_id": choice("io-production-evaluation-v1"),
                "contract_version": choice("1.0.0"),
                "evaluation_id": identifier,
                "split": choice("TRAIN", "VALIDATION", "TEST"),
                "scenario_id": identifier,
                "delay_scope": choice("EPISODE_RECEIPTS_ONLY"),
                "forecast_mode": choice("SYNTHETIC_FROZEN_MEAN"),
                "training_request": lambda v: TrainingRequest.from_dict(v).to_dict(),
                "mathematical_request": lambda v: MathematicalPolicyRequest.from_dict(v).to_dict(),
            },
        )
        return cls(canonical_json(data))

    def to_dict(self) -> dict:
        return read_json(self.document_json)

    @property
    def input_hash(self) -> str:
        return digest(self.to_dict())
