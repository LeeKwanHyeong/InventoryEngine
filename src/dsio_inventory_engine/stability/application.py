"""Two-stage study: seal TRAIN/VALIDATION selection before opening fresh TEST draws."""

from collections.abc import Callable
from decimal import localcontext

from dsio_inventory_engine.evaluate_replenishment.application import (
    EvaluateMathematicalStrategyUseCase,
)
from dsio_inventory_engine.evaluate_replenishment.demo import build_evaluation_request
from dsio_inventory_engine.fit_replenishment.train import TrainLearnedStrategiesUseCase
from dsio_inventory_engine.inventory_contracts.learning import LearningRequest
from dsio_inventory_engine.inventory_contracts.model import ModelArtifact
from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.stability import StabilityRequest
from dsio_inventory_engine.inventory_contracts.values import digest, hash_value, require
from dsio_inventory_engine.learned.application import evaluate_learned
from dsio_inventory_engine.learned.comparison import evaluate_hold, summarize
from dsio_inventory_engine.training.reference import NUMBERS
from .data import build_dataset, partition_hashes, sensitivity_dataset
from .selection import assess, select_candidates


def sealed(data: dict) -> dict:
    return {**data, "content_hash": digest(data)}


def recipe_for(plan: dict, candidate: dict, seed: int, training) -> LearningRequest:
    return LearningRequest.from_dict(
        {
            "contract_id": "io-learned-training-v1",
            "contract_version": "1.0.0",
            "training_run_id": "FIT-"
            + digest([plan["study_id"], candidate["candidate_id"], seed])[:32],
            "model_version": "0.8.0",
            "seed": seed,
            **{k: candidate[k] for k in ("ml_epochs", "ppo_iterations", "ppo_epochs")},
            "training_request": training.to_dict(),
        }
    )


class LearningStabilityUseCase:
    def __init__(self, deployment: DeploymentScope, progress: Callable[[dict], None] | None = None):
        self.deployment = deployment
        self.progress = progress or (lambda event: None)

    def _admit(self, request: StabilityRequest) -> StabilityRequest:
        require(self.deployment.environment == "DEVELOPMENT", "LOCAL_STABILITY_ONLY")
        require(self.deployment.company_cd == "DSE", "DEPLOYMENT_COMPANY_MISMATCH")
        return StabilityRequest.from_dict(request.to_dict())

    def select(self, request: StabilityRequest) -> dict:
        request = self._admit(request)
        plan = request.to_dict()
        training = build_dataset(request)  # TEST consists of unobserved placeholders.
        validation_rows, runs = [], []
        requests, mathematical = {}, {}
        for scenario in training.to_dict()["scenarios"]:
            key = scenario["scenario_id"]
            req = build_evaluation_request(
                training, self.deployment, split="VALIDATION", scenario_id=key
            )
            requests[key] = req
            with localcontext(NUMBERS):
                mathematical[key] = summarize(
                    EvaluateMathematicalStrategyUseCase(self.deployment).execute(req)
                )
        for candidate in plan["candidates"]:
            for seed in plan["training_seeds"]:
                self.progress(
                    {"phase": "TRAIN", "candidate_id": candidate["candidate_id"], "seed": seed}
                )
                recipe = recipe_for(plan, candidate, seed, training)
                trained = TrainLearnedStrategiesUseCase(self.deployment).execute(recipe)
                runs.append(
                    {
                        "candidate_id": candidate["candidate_id"],
                        "seed": seed,
                        "recipe_hash": recipe.input_hash,
                        "training_result": trained,
                    }
                )
                for artifact in trained["models"]:
                    model = ModelArtifact.from_dict(artifact)
                    for key, req in requests.items():
                        with localcontext(NUMBERS):
                            result = summarize(
                                evaluate_learned(req, model, model.reference, self.deployment)
                            )
                        validation_rows.append(
                            {
                                "candidate_id": candidate["candidate_id"],
                                "seed": seed,
                                "family": artifact["strategy_type"],
                                "scenario_id": key,
                                "model_reference": model.reference,
                                "evaluation_input_hash": req.input_hash,
                                "mathematical": mathematical[key],
                                "learned": result,
                            }
                        )
                self.progress(
                    {
                        "phase": "VALIDATION_COMPLETE",
                        "candidate_id": candidate["candidate_id"],
                        "seed": seed,
                    }
                )
        selection = select_candidates(plan, validation_rows)
        partitions = partition_hashes(training)
        partitions.pop("TEST")
        return sealed(
            {
                "contract_id": "io-stability-selection-v1",
                "contract_version": "1.0.0",
                "request": plan,
                "request_hash": request.input_hash,
                "dataset_partitions": partitions,
                "holdout_placeholder_not_observed": True,
                "runs": runs,
                "validation_rows": validation_rows,
                "selection": selection,
                "database_writes": False,
                "operational_approval": False,
            }
        )

    def validate_selection(
        self, document: dict, expected_hash: str
    ) -> tuple[StabilityRequest, list[dict]]:
        hash_value(expected_hash)
        require(
            type(document) is dict
            and document.get("content_hash") == expected_hash
            and digest({k: v for k, v in document.items() if k != "content_hash"}) == expected_hash,
            "SELECTION_HASH_MISMATCH",
        )
        require(
            document.get("contract_id") == "io-stability-selection-v1"
            and document.get("contract_version") == "1.0.0",
            "STABILITY_SELECTION_CONTRACT",
        )
        request = self._admit(StabilityRequest.from_dict(document["request"]))
        plan = request.to_dict()
        require(document["request_hash"] == request.input_hash, "STABILITY_REQUEST_HASH_MISMATCH")
        require(
            document["selection"] == select_candidates(plan, document["validation_rows"]),
            "SELECTION_RECOMPUTATION_MISMATCH",
        )
        training = build_dataset(request)
        partitions = partition_hashes(training)
        partitions.pop("TEST")
        require(
            document["dataset_partitions"] == partitions
            and document["holdout_placeholder_not_observed"] is True,
            "STABILITY_PARTITION_MISMATCH",
        )
        expected = {
            (c["candidate_id"], seed) for c in plan["candidates"] for seed in plan["training_seeds"]
        }
        require(
            len(document["runs"]) == len(expected)
            and {(r["candidate_id"], r["seed"]) for r in document["runs"]} == expected,
            "STABILITY_RUN_MATRIX",
        )
        selected = []
        for run in document["runs"]:
            candidate = next(
                c for c in plan["candidates"] if c["candidate_id"] == run["candidate_id"]
            )
            recipe = recipe_for(plan, candidate, run["seed"], training)
            result = run["training_result"]
            require(
                run["recipe_hash"] == recipe.input_hash
                and result["input_content_hash"] == recipe.input_hash,
                "STABILITY_RECIPE_BINDING",
            )
            require(
                result["content_hash"]
                == digest({k: v for k, v in result.items() if k != "content_hash"}),
                "STABILITY_TRAINING_RESULT_HASH",
            )
            models = [ModelArtifact.from_dict(m) for m in result["models"]]
            require(
                len(models) == 2
                and {m.to_dict()["strategy_type"] for m in models} == {"PREDICTIVE_ML", "DEEP_RL"},
                "STABILITY_MODEL_FAMILIES",
            )
            require(
                result["model_references"] == [m.reference for m in models],
                "MODEL_REFERENCE_MISMATCH",
            )
            for model in models:
                data = model.to_dict()
                require(
                    data["training_run_id"] == recipe.to_dict()["training_run_id"],
                    "STABILITY_MODEL_RUN_BINDING",
                )
                rows = [
                    r
                    for r in document["validation_rows"]
                    if r["candidate_id"] == candidate["candidate_id"]
                    and r["seed"] == run["seed"]
                    and r["family"] == data["strategy_type"]
                ]
                require(
                    all(r["model_reference"] == model.reference for r in rows),
                    "STABILITY_VALIDATION_MODEL_BINDING",
                )
                chosen = document["selection"]["families"][data["strategy_type"]]
                if chosen["candidate_id"] == candidate["candidate_id"]:
                    selected.append(
                        {
                            "candidate_id": candidate["candidate_id"],
                            "seed": run["seed"],
                            "family": data["strategy_type"],
                            "model": model,
                            "validation_gate_passed": chosen["validation_gate_passed"],
                        }
                    )
        return request, selected

    def holdout(self, document: dict, expected_hash: str) -> dict:
        # Validate the separately pinned selection BEFORE generating any TEST actual.
        request, selected = self.validate_selection(document, expected_hash)
        rows, cases, partitions = [], [], []
        for data_seed in request.to_dict()["holdout_data_seeds"]:
            training = build_dataset(request, holdout_seed=data_seed)
            partitions.append(
                {
                    "data_seed": data_seed,
                    **partition_hashes(training)["TEST"],
                    "training_request_hash": training.to_dict()["content_hash"],
                }
            )
            variants = [(104, "SOURCE")]
            if data_seed == request.to_dict()["holdout_data_seeds"][0]:
                variants += [(52, "SOURCE"), (104, "ZERO")]
            for horizon, terminal in variants:
                case = sensitivity_dataset(training, horizon, terminal)
                for scenario in case.to_dict()["scenarios"]:
                    sid = scenario["scenario_id"]
                    if (horizon, terminal) != (104, "SOURCE") and sid not in ("BASE", "DELAY_2W"):
                        continue
                    self.progress(
                        {
                            "phase": "HOLDOUT",
                            "data_seed": data_seed,
                            "horizon": horizon,
                            "terminal": terminal,
                            "scenario_id": sid,
                        }
                    )
                    req = build_evaluation_request(
                        case, self.deployment, split="TEST", scenario_id=sid
                    )
                    with localcontext(NUMBERS):
                        baseline = summarize(
                            EvaluateMathematicalStrategyUseCase(self.deployment).execute(req)
                        )
                        control = summarize(evaluate_hold(req, self.deployment))
                    require(
                        baseline["initial_state_hash"] == control["initial_state_hash"],
                        "UNPAIRED_STABILITY_INPUT",
                    )
                    cases.append(
                        {
                            "data_seed": data_seed,
                            "horizon": horizon,
                            "terminal": terminal,
                            "scenario_id": sid,
                            "evaluation_input_hash": req.input_hash,
                            "mathematical": baseline,
                            "no_new_supply": control,
                        }
                    )
                    for entry in selected:
                        model = entry["model"]
                        with localcontext(NUMBERS):
                            learned = summarize(
                                evaluate_learned(req, model, model.reference, self.deployment)
                            )
                        rows.append(
                            {k: v for k, v in entry.items() if k != "model"}
                            | {
                                "model_reference": model.reference,
                                "data_seed": data_seed,
                                "horizon": horizon,
                                "terminal": terminal,
                                "scenario_id": sid,
                                "evaluation_input_hash": req.input_hash,
                                "mathematical": baseline,
                                "learned": learned,
                            }
                        )
        assessments = []
        for family in ("PREDICTIVE_ML", "DEEP_RL"):
            for data_seed in request.to_dict()["holdout_data_seeds"]:
                main = [
                    r
                    for r in rows
                    if r["family"] == family
                    and r["data_seed"] == data_seed
                    and r["horizon"] == 104
                    and r["terminal"] == "SOURCE"
                ]
                assessments.append({"family": family, "data_seed": data_seed, **assess(main)})
        return sealed(
            {
                "contract_id": "io-stability-holdout-v1",
                "contract_version": "1.0.0",
                "selection_content_hash": expected_hash,
                "request_hash": request.input_hash,
                "dataset_partitions": partitions,
                "cases": cases,
                "rows": rows,
                "assessments": assessments,
                "selection_changed_after_holdout": False,
                "holdout_used_for_training": False,
                "all_selected_seeds_evaluated": True,
                "interval_estimates": "NOT_ESTIMATED_THREE_SEEDS_CORRELATED_SCENARIOS",
                "source_kind": "SYNTHETIC_FROZEN_MEAN_NOT_OPERATIONAL_SOURCE",
                "database_writes": False,
                "operational_approval": False,
            }
        )
