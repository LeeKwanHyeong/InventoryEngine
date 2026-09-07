"""Offline foundation composition; no training, persistent evidence, or DB access."""

from decimal import Decimal, localcontext

from dsio_inventory_engine.inventory_contracts.training import TrainingRequest, VERSION
from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.values import digest, quantity_text as q, require
from .dataset import build_supervised, splits
from .evaluation import ReferenceEpisode
from .reference import NUMBERS, demand_series, generate_warmup, reference_policy


def run_reference_benchmark(
    request: TrainingRequest,
    deployment: DeploymentScope,
    split: str,
    scenario_id: str,
    benchmark: str,
) -> dict:
    require(benchmark in ("HOLD", "REFERENCE_R_S"), "UNKNOWN_BENCHMARK")
    episode = ReferenceEpisode(request, deployment, split, scenario_id)
    data = request.to_dict()
    scenario = next(s for s in data["scenarios"] if s["scenario_id"] == scenario_id)
    items = {i["item_id"]: i for i in data["items"]}
    series = {k: demand_series(data, i) for k, i in items.items()}
    with localcontext(NUMBERS):
        while not episode.done:
            observation = episode.observe()
            actions = {}
            for row in observation["items"]:
                item_id = row["item_id"]
                policy = reference_policy(
                    series[item_id], observation["week_index"], items[item_id], data, scenario
                )
                position = Decimal(row["inventory_position_qty"])
                qty = (
                    max(Decimal(0), Decimal(policy["target_inventory_qty"]) - position)
                    if benchmark == "REFERENCE_R_S" and position <= Decimal(policy["rop_qty"])
                    else Decimal(0)
                )
                actions[item_id] = q(qty)
            episode.step(actions)
    result = episode.result()
    result["benchmark"] = benchmark
    result["production_strategy_executed"] = False
    result["trained_model"] = False
    result["content_hash"] = digest({k: v for k, v in result.items() if k != "content_hash"})
    return result


class PrepareTrainingFoundationUseCase:
    def __init__(self, deployment: DeploymentScope):
        self.deployment = deployment

    def execute(self, request: TrainingRequest) -> dict:
        data = request.to_dict()
        require(self.deployment.environment == "DEVELOPMENT", "SYNTHETIC_PRODUCTION_FORBIDDEN")
        require(
            self.deployment.company_cd == data["context"]["company_cd"],
            "DEPLOYMENT_COMPANY_MISMATCH",
        )
        supervised = build_supervised(data)
        warmups, evaluations = [], []
        for scenario in data["scenarios"]:
            for split, start, _ in splits(data):
                warmups.append(
                    {
                        "split": split,
                        "scenario_id": scenario["scenario_id"],
                        **generate_warmup(data, start, scenario),
                    }
                )
            for benchmark in ("HOLD", "REFERENCE_R_S"):
                evaluations.append(
                    run_reference_benchmark(
                        request, self.deployment, "TEST", scenario["scenario_id"], benchmark
                    )
                )
        imputed = []
        keys = {(r["item_id"], r["yyyyww"]) for r in data["demand"]}
        for item in data["items"]:
            for i in range(item["active_from_index"], data["w0_index"]):
                week = data["calendar"][i]["yyyyww"]
                if (item["item_id"], week) not in keys:
                    imputed.append(
                        {
                            "item_id": item["item_id"],
                            "yyyyww": week,
                            "demand_qty": "0",
                            "reason": "EXPLICIT_MISSING_ACTIVE_WEEK_IS_ZERO",
                        }
                    )
        result = {
            "status": "TRAINING_FOUNDATION_PREPARED_LOCALLY",
            "contract_version": VERSION,
            "input_hash": data["content_hash"],
            "input_snapshot": data,
            "context": data["context"],
            "dataset_id": data["dataset_id"],
            "simulation_run_id": data["simulation_run_id"],
            "source_snapshot_hashes": {
                "calendar": digest(data["calendar"]),
                "demand": digest(data["demand"]),
                "policy": digest(data["items"]),
            },
            "supervised": supervised,
            "warmups": warmups,
            "evaluations": evaluations,
            "imputation_evidence": imputed,
            "scenario_profiles": data["scenarios"],
            "manifest": {
                "generator_version": VERSION,
                "random_seed": None,
                "warmup_weeks": data["warmup_weeks"],
                "lookback_weeks": data["lookback_weeks"],
                "source_type": "SYNTHETIC_DEMAND",
                "operational_accuracy_verified": False,
                "generator_independent_of_production_psi": True,
                "database_writes": False,
                "artifact_sealed": False,
                "evidence_persisted": False,
                "model_trained": False,
                "run_claimed": False,
                "evaluation_is_reference_benchmark_only": True,
            },
        }
        result["content_hash"] = digest(result)
        return result
