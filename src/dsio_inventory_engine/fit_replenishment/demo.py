"""A bounded synthetic recipe, not an operational training configuration."""

from dsio_inventory_engine.inventory_contracts.learning import LearningRequest
from dsio_inventory_engine.training.demo import build_demo_request


def build_learning_request(*, quick=False) -> LearningRequest:
    return LearningRequest.from_dict(
        {
            "contract_id": "io-learned-training-v1",
            "contract_version": "1.0.0",
            "training_run_id": "LEARN-DEMO-v1",
            "model_version": "1.0.0",
            "seed": 20260903,
            "ml_epochs": 20 if quick else 300,
            "ppo_iterations": 1 if quick else 12,
            "ppo_epochs": 2 if quick else 4,
            "training_request": build_demo_request().to_dict(),
        }
    )
