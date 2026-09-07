"""Bounded JSON-only model envelope. Hash verification is not operational approval."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from .values import (
    InventoryInputError,
    canonical_json,
    choice,
    day,
    digest,
    hash_value,
    identifier,
    integer,
    read_json,
    require,
    shape,
)

FEATURE_CONTRACT = "io-learned-observation-v1"
FAMILIES = {
    "PREDICTIVE_ML": "positive-error-quantile-linear-v1",
    "DEEP_RL": "ppo-clip-target-mlp-v1",
}
SHAPES = {
    "PREDICTIVE_ML": {"linear.weight": (1, 4), "linear.bias": (1,)},
    "DEEP_RL": {
        "fc1.weight": (16, 14),
        "fc1.bias": (16,),
        "fc2.weight": (16, 16),
        "fc2.bias": (16,),
        "actor.weight": (4, 16),
        "actor.bias": (4,),
        "critic.weight": (1, 16),
        "critic.bias": (1,),
    },
}


def number(value: str) -> str:
    require(type(value) is str and 0 < len(value) <= 64, "MODEL_NUMBER")
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise InventoryInputError("MODEL_NUMBER") from None
    require(number.is_finite() and number.copy_abs() <= Decimal("1e9"), "MODEL_NUMBER")
    return value


def numbers(value: list, dimensions: tuple[int, ...]) -> list:
    require(type(value) is list and len(value) == dimensions[0], "MODEL_WEIGHT_SHAPE")
    return [number(v) if len(dimensions) == 1 else numbers(v, dimensions[1:]) for v in value]


def training_scope(value: dict) -> dict:
    return shape(
        value,
        {k: identifier for k in ("company_cd", "subs_cd", "site_cd", "configuration_revision")}
        | {"plan_type": choice("POSM", "TGSM")},
    )


def model_content(data: dict) -> dict:
    return {k: v for k, v in data.items() if k != "content_hash"}


@dataclass(frozen=True)
class ModelArtifact:
    document_json: str

    @classmethod
    def from_dict(cls, value: dict) -> "ModelArtifact":
        require(type(value) is dict and value.get("strategy_type") in FAMILIES, "MODEL_FAMILY")
        family = value["strategy_type"]
        fields = {
            "contract_id": choice("io-replenishment-model-v1"),
            "contract_version": choice("1.0.0"),
            "model_id": identifier,
            "version": identifier,
            "content_hash": hash_value,
            "strategy_type": choice(family),
            "algorithm": choice(FAMILIES[family]),
            "feature_contract_id": choice(FEATURE_CONTRACT),
            "scope": training_scope,
            "training_data_hash": hash_value,
            "trained_through_date": day,
            "training_run_id": identifier,
            "training_profile_hash": hash_value,
            "runtime_version": identifier,
            "policy_contract": lambda v: shape(
                v,
                {
                    "lookback_weeks": integer,
                    "stddev_ddof": integer,
                    "replenishment_cycle_weeks": integer,
                },
            ),
            "normalizer": lambda v: shape(
                v, {"mean": lambda r: numbers(r, (14,)), "std": lambda r: numbers(r, (14,))}
            ),
            "weights": lambda v: shape(
                v, {k: lambda r, d=d: numbers(r, d) for k, d in SHAPES[family].items()}
            ),
        }
        data = shape(value, fields)
        require(
            all(Decimal(s) >= Decimal("0.000001") for s in data["normalizer"]["std"]),
            "MODEL_NORMALIZER_RANGE",
        )
        require(
            data["policy_contract"]["stddev_ddof"] == 1
            and data["policy_contract"]["lookback_weeks"] in (13, 26)
            and 1 <= data["policy_contract"]["replenishment_cycle_weeks"] <= 52,
            "MODEL_POLICY_CONTRACT",
        )
        require(data["content_hash"] == digest(model_content(data)), "MODEL_HASH_MISMATCH")
        return cls(canonical_json(data))

    def to_dict(self) -> dict:
        return read_json(self.document_json)

    @property
    def reference(self) -> dict:
        data = self.to_dict()
        return {
            "model_id": data["model_id"],
            "version": data["version"],
            "content_hash": data["content_hash"],
        }

    @property
    def descriptor(self) -> dict:
        data = self.to_dict()
        return {
            "strategy_type": data["strategy_type"],
            "implementation_id": data["algorithm"],
            "version": "1.0.0",
            "model": self.reference,
        }

    def validate_for(self, expected: dict, context: dict) -> None:
        require(expected == self.reference, "MODEL_REFERENCE_MISMATCH")
        data = self.to_dict()
        require(all(context[k] == v for k, v in data["scope"].items()), "MODEL_SCOPE_MISMATCH")
        require(
            data["trained_through_date"] < context["plan_start_date"], "MODEL_TRAIN_TEST_LEAKAGE"
        )
