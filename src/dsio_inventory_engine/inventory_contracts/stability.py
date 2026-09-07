"""Predeclared, bounded local experiment; thresholds are not operational approval."""

from dataclasses import dataclass

from .learning import seed_value
from .values import canonical_json, choice, digest, identifier, integer, read_json, require, shape


def seeds(value: list) -> list[int]:
    require(type(value) is list and 3 <= len(value) <= 5, "STABILITY_SEED_COUNT")
    result = sorted(seed_value(v) for v in value)
    require(len(set(result)) == len(result), "DUPLICATE_STABILITY_SEED")
    return result


def candidates(value: list) -> list[dict]:
    require(type(value) is list and 2 <= len(value) <= 3, "STABILITY_CANDIDATE_COUNT")
    rows = [
        shape(
            v,
            {
                "candidate_id": identifier,
                "ml_epochs": integer,
                "ppo_iterations": integer,
                "ppo_epochs": integer,
            },
        )
        for v in value
    ]
    require(len({r["candidate_id"] for r in rows}) == len(rows), "DUPLICATE_CANDIDATE")
    for row in rows:
        require(
            1 <= row["ml_epochs"] <= 600
            and 1 <= row["ppo_iterations"] <= 24
            and 1 <= row["ppo_epochs"] <= 4,
            "STABILITY_TRAINING_BUDGET",
        )
    return sorted(rows, key=lambda r: r["candidate_id"])


@dataclass(frozen=True)
class StabilityRequest:
    document_json: str

    @classmethod
    def from_dict(cls, value: dict) -> "StabilityRequest":
        data = shape(
            value,
            {
                "contract_id": choice("io-learning-stability-v1"),
                "contract_version": choice("1.0.0"),
                "study_id": identifier,
                "generator_version": choice("nonrepeating-hash-demand-v1"),
                "selection_rule": choice("cost-service-guardrails-v1"),
                "development_data_seed": seed_value,
                "training_seeds": seeds,
                "holdout_data_seeds": seeds,
                "candidates": candidates,
            },
        )
        require(
            data["development_data_seed"] not in data["holdout_data_seeds"], "HOLDOUT_SEED_REUSE"
        )
        require(
            len(data["candidates"]) * len(data["training_seeds"]) <= 9, "STABILITY_STUDY_BUDGET"
        )
        return cls(canonical_json(data))

    def to_dict(self) -> dict:
        return read_json(self.document_json)

    @property
    def input_hash(self) -> str:
        return digest(self.to_dict())


def default_request() -> StabilityRequest:
    return StabilityRequest.from_dict(
        {
            "contract_id": "io-learning-stability-v1",
            "contract_version": "1.0.0",
            "study_id": "IO-STABILITY-20260903-v1",
            "generator_version": "nonrepeating-hash-demand-v1",
            "selection_rule": "cost-service-guardrails-v1",
            "development_data_seed": 17011,
            "training_seeds": [101, 202, 303],
            "holdout_data_seeds": [81001, 81002, 81003],
            "candidates": [
                {
                    "candidate_id": "BUDGET_06",
                    "ml_epochs": 150,
                    "ppo_iterations": 6,
                    "ppo_epochs": 4,
                },
                {
                    "candidate_id": "BUDGET_18",
                    "ml_epochs": 300,
                    "ppo_iterations": 18,
                    "ppo_epochs": 4,
                },
            ],
        }
    )
