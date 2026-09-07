"""Bounded local learning recipe; no remote jobs, device selection or arbitrary optimizers."""

from dataclasses import dataclass

from .training import TrainingRequest
from .values import canonical_json, choice, digest, identifier, integer, read_json, require, shape


def seed_value(value: int) -> int:
    require(type(value) is int and 0 <= value < 2**31, "TRAINING_SEED_RANGE")
    return value


@dataclass(frozen=True)
class LearningRequest:
    document_json: str

    @classmethod
    def from_dict(cls, value: dict) -> "LearningRequest":
        data = shape(
            value,
            {
                "contract_id": choice("io-learned-training-v1"),
                "contract_version": choice("1.0.0"),
                "training_run_id": identifier,
                "model_version": identifier,
                "seed": seed_value,
                "ml_epochs": integer,
                "ppo_iterations": integer,
                "ppo_epochs": integer,
                "training_request": lambda v: TrainingRequest.from_dict(v).to_dict(),
            },
        )
        require(0 <= data["seed"] < 2**31, "TRAINING_SEED_RANGE")
        require(
            1 <= data["ml_epochs"] <= 2000
            and 1 <= data["ppo_iterations"] <= 64
            and 1 <= data["ppo_epochs"] <= 10,
            "TRAINING_BUDGET_RANGE",
        )
        training = data["training_request"]
        require(training["train_weeks"] >= 26, "TRAINING_HORIZON_PREFLIGHT_REQUIRES_26_WEEKS")
        require(
            len(training["items"]) * training["train_weeks"] * data["ppo_iterations"] <= 20000,
            "PPO_TRANSITION_LIMIT",
        )
        require(
            all(
                i["lead_time_weeks"] + training["replenishment_cycle_weeks"]
                <= training["train_weeks"]
                for i in training["items"]
            ),
            "PROTECTION_HORIZON_EXCEEDS_TRAIN",
        )
        return cls(canonical_json(data))

    def to_dict(self) -> dict:
        return read_json(self.document_json)

    @property
    def input_hash(self) -> str:
        return digest(self.to_dict())
