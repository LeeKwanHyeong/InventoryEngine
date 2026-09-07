"""Bounded, seeded local learning. No model publication or operational approval."""

import math
from decimal import localcontext
from typing import Any

from dsio_inventory_engine.evaluate_replenishment.application import (
    EvaluateMathematicalStrategyUseCase,
)
from dsio_inventory_engine.evaluate_replenishment.demo import build_evaluation_request
from dsio_inventory_engine.inventory_contracts.learning import LearningRequest
from dsio_inventory_engine.inventory_contracts.mathematical import MathematicalPolicyRequest
from dsio_inventory_engine.inventory_contracts.model import FAMILIES, ModelArtifact
from dsio_inventory_engine.inventory_contracts.training import TrainingRequest
from dsio_inventory_engine.inventory_contracts.values import digest, require
from dsio_inventory_engine.learned.application import compile_strategy
from dsio_inventory_engine.learned.features import (
    FEATURE_CONTRACT,
    ML_INDICES,
    fit_normalizer,
    normalized,
    vector,
)
from dsio_inventory_engine.training.evaluation import ReferenceEpisode
from dsio_inventory_engine.training.reference import NUMBERS
from .data import supervised, training_fingerprint
from .preflight import preflight

PROFILE: dict[str, Any] = {
    "algorithm_version": "1.0.0",
    "dtype": "float64",
    "device": "cpu",
    "ml_lr": 0.03,
    "ppo_lr": 0.0003,
    "gamma": 0.99,
    "gae_lambda": 0.95,
    "clip": 0.2,
    "value_coefficient": 0.5,
    "entropy_coefficient": 0.01,
    "max_grad_norm": 0.5,
    "minibatch_size": 128,
    "target_kl": 0.03,
    "reward_cost_divisor": 100.0,
    "terminal_bootstrap": 0,
    "action_count": 4,
    "scenario_sampling": "SORTED_ROUND_ROBIN",
    "checkpoint_selection": "FINAL_TRAIN_ONLY",
}


def envelope(recipe, family, normalizer, model, runtime) -> ModelArtifact:
    from .torch_models import weights

    train = recipe["training_request"]
    data = {
        "contract_id": "io-replenishment-model-v1",
        "contract_version": "1.0.0",
        "model_id": family + "-" + digest(recipe["training_run_id"])[:24],
        "version": recipe["model_version"],
        "strategy_type": family,
        "algorithm": FAMILIES[family],
        "feature_contract_id": FEATURE_CONTRACT,
        "scope": {
            k: train["context"][k]
            for k in ("company_cd", "subs_cd", "site_cd", "configuration_revision", "plan_type")
        },
        "training_data_hash": digest(training_fingerprint(train)),
        "trained_through_date": train["calendar"][train["w0_index"] + train["train_weeks"] - 1][
            "end_date"
        ],
        "training_run_id": recipe["training_run_id"],
        "training_profile_hash": digest(
            {
                "profile": PROFILE,
                "recipe": {k: v for k, v in recipe.items() if k != "training_request"},
            }
        ),
        "runtime_version": runtime,
        "normalizer": normalizer,
        "weights": weights(model),
    }
    data["policy_contract"] = {
        "lookback_weeks": train["lookback_weeks"],
        "stddev_ddof": 1,
        "replenishment_cycle_weeks": train["replenishment_cycle_weeks"],
    }
    data["content_hash"] = digest(data)
    return ModelArtifact.from_dict(data)


class TrainLearnedStrategiesUseCase:
    def __init__(self, deployment):
        self.deployment = deployment

    def execute(self, request: LearningRequest) -> dict:
        require(self.deployment.environment == "DEVELOPMENT", "LOCAL_TRAINING_ONLY")
        recipe = LearningRequest.from_dict(request.to_dict()).to_dict()
        require(
            self.deployment.company_cd == recipe["training_request"]["context"]["company_cd"],
            "DEPLOYMENT_COMPANY_MISMATCH",
        )
        training = TrainingRequest.from_dict(recipe["training_request"])
        readiness = preflight(training, self.deployment)
        # Optional dependency loaded only after all admission/sensitivity checks.
        import torch
        from .torch_models import ActorCritic, QuantileModel, pinball, weights

        threads = torch.get_num_threads()
        try:
            torch.set_num_threads(1)
            with torch.random.fork_rng(devices=[]):
                torch.default_generator.manual_seed(recipe["seed"])
                labels = supervised(recipe["training_request"])
                normalizer = fit_normalizer([r["features"] for r in labels])
                x = torch.tensor(
                    [
                        [normalized(r["features"], normalizer)[i] for i in ML_INDICES]
                        for r in labels
                    ],
                    dtype=torch.float64,
                    device="cpu",
                )
                target = torch.tensor(
                    [r["target"] for r in labels], dtype=torch.float64, device="cpu"
                )
                quantile = torch.tensor(
                    [r["quantile"] for r in labels], dtype=torch.float64, device="cpu"
                )
                ml = QuantileModel()
                initial_ml = digest(weights(ml))
                optimizer = torch.optim.Adam(ml.parameters(), lr=PROFILE["ml_lr"])
                initial_loss = float(pinball(ml(x), target, quantile).detach())
                for _ in range(recipe["ml_epochs"]):
                    loss = pinball(ml(x), target, quantile)
                    require(bool(torch.isfinite(loss)), "NONFINITE_TRAINING_LOSS")
                    optimizer.zero_grad()
                    loss.backward()
                    optimizer.step()
                ml_artifact = envelope(recipe, "PREDICTIVE_ML", normalizer, ml, torch.__version__)
                final_loss = float(pinball(ml(x), target, quantile).detach())

                # PPO normalizer is fit on TRAIN mathematical observations, not held-out data.
                scenarios = sorted(
                    s["scenario_id"] for s in recipe["training_request"]["scenarios"]
                )
                eval_request = build_evaluation_request(
                    training, self.deployment, split="TRAIN", scenario_id=scenarios[0]
                )
                math_result = EvaluateMathematicalStrategyUseCase(self.deployment).execute(
                    eval_request
                )
                policies = {
                    r["decision_id"]: r
                    for r in math_result["mathematical_policy_report"]["policy_evidence"]
                }
                stats = {
                    r["item_id"]: r
                    for r in math_result["mathematical_policy_report"]["history_statistics"]
                }
                observations = [
                    vector(
                        r["observation"],
                        policies[r["observation"]["decision_id"]],
                        stats[r["observation"]["item_id"]],
                    )[0]
                    for r in math_result["decision_evidence"]
                ]
                ppo_normalizer = fit_normalizer(observations)
                ppo = ActorCritic()
                initial_ppo = digest(weights(ppo))
                ppo_log = self._ppo(recipe, training, scenarios, ppo, ppo_normalizer, torch)
                ppo_artifact = envelope(recipe, "DEEP_RL", ppo_normalizer, ppo, torch.__version__)
                ml_log = {
                    "samples": len(labels),
                    "labels_hash": digest(labels),
                    "initial_pinball_loss": initial_loss,
                    "final_pinball_loss": final_loss,
                    "optimizer_steps": recipe["ml_epochs"],
                    "initial_weights_hash": initial_ml,
                    "final_weights_hash": digest(weights(ml)),
                }
                ppo_log.update(
                    initial_weights_hash=initial_ppo, final_weights_hash=digest(weights(ppo))
                )
        finally:
            torch.set_num_threads(threads)
        result = {
            "contract_id": "io-learned-training-result-v1",
            "contract_version": "1.0.0",
            "status": "TRAINED_LOCALLY",
            "input_content_hash": request.input_hash,
            "training_run_id": recipe["training_run_id"],
            "models": [ml_artifact.to_dict(), ppo_artifact.to_dict()],
            "model_references": [ml_artifact.reference, ppo_artifact.reference],
            "preflight": readiness,
            "training_profile": PROFILE,
            "seed": recipe["seed"],
            "runtime_version": torch.__version__,
            "ml_training": ml_log,
            "ppo_training": ppo_log,
            "model_trained": True,
            "test_actuals_used_for_training": False,
            "artifact_sealed": False,
            "database_writes": False,
            "evidence_persisted": False,
            "operational_model_approval_verified": False,
            "convergence_claimed": False,
        }
        result["content_hash"] = digest(result)
        return result

    def _ppo(self, recipe, training, scenarios, model, normalizer, torch) -> dict:
        from .torch_models import advantages, clipped_objective

        optimizer = torch.optim.Adam(model.parameters(), lr=PROFILE["ppo_lr"])
        logs = []
        steps = 0
        for iteration in range(recipe["ppo_iterations"]):
            scenario = scenarios[iteration % len(scenarios)]
            request = build_evaluation_request(
                training, self.deployment, split="TRAIN", scenario_id=scenario
            )
            data = request.to_dict()
            samples = []

            def sample(features):
                x = normalized(features, normalizer)
                with torch.no_grad():
                    logits, value = model(torch.tensor(x, dtype=torch.float64, device="cpu"))
                    distribution = torch.distributions.Categorical(logits=logits)
                    action = distribution.sample()
                samples.append(
                    {
                        "x": x,
                        "action": int(action),
                        "log_prob": float(distribution.log_prob(action)),
                        "value": float(value),
                    }
                )
                return int(action)

            artifact = envelope(recipe, "DEEP_RL", normalizer, model, torch.__version__)
            rec, prepared, _, strategy = compile_strategy(
                MathematicalPolicyRequest.from_dict(data["mathematical_request"]),
                artifact,
                self.deployment,
                training_predictor=sample,
            )
            world = ReferenceEpisode(
                training, self.deployment, "TRAIN", scenario, delay_scope="EPISODE_RECEIPTS_ONLY"
            )

            def reward(transition):
                rows = {r["item_id"]: r for r in transition["rows"]}
                count = len(rows)
                for saved, record in zip(samples[-count:], strategy.records[-count:], strict=True):
                    saved.update(
                        item_id=record["item_id"],
                        reward=-float(rows[record["item_id"]]["total_cost"])
                        / PROFILE["reward_cost_divisor"],
                    )

            with localcontext(NUMBERS):
                decisions = EvaluateMathematicalStrategyUseCase._simulate(
                    data, world, rec, prepared, strategy, on_transition=reward
                )
            for item in prepared["master"]:
                path = [s for s in samples if s["item_id"] == item["item_id"]]
                adv, returns = advantages([s["reward"] for s in path], [s["value"] for s in path])
                for saved, a, r in zip(path, adv, returns, strict=True):
                    saved.update(advantage=a, returns=r)
            x = torch.tensor([s["x"] for s in samples], dtype=torch.float64, device="cpu")
            actions = torch.tensor([s["action"] for s in samples], device="cpu")
            old = torch.tensor([s["log_prob"] for s in samples], dtype=torch.float64, device="cpu")
            adv = torch.tensor([s["advantage"] for s in samples], dtype=torch.float64, device="cpu")
            adv = (adv - adv.mean()) / (adv.std(unbiased=False) + 1e-8)
            returns = torch.tensor(
                [s["returns"] for s in samples], dtype=torch.float64, device="cpu"
            )
            updates = []
            for _ in range(recipe["ppo_epochs"]):
                for indices in torch.randperm(len(samples), device="cpu").split(
                    PROFILE["minibatch_size"]
                ):
                    logits, values = model(x[indices])
                    distribution = torch.distributions.Categorical(logits=logits)
                    log_ratio = distribution.log_prob(actions[indices]) - old[indices]
                    ratio = log_ratio.exp()
                    policy_loss = -clipped_objective(ratio, adv[indices]).mean()
                    value_loss = (values - returns[indices]).square().mean()
                    entropy = distribution.entropy().mean()
                    loss = (
                        policy_loss
                        + PROFILE["value_coefficient"] * value_loss
                        - PROFILE["entropy_coefficient"] * entropy
                    )
                    require(bool(torch.isfinite(loss)), "NONFINITE_TRAINING_LOSS")
                    kl = float(((ratio - 1) - log_ratio).mean().detach())
                    if kl > PROFILE["target_kl"]:
                        break
                    optimizer.zero_grad()
                    loss.backward()
                    torch.nn.utils.clip_grad_norm_(model.parameters(), PROFILE["max_grad_norm"])
                    optimizer.step()
                    steps += 1
                    updates.append(
                        {
                            "policy_loss": float(policy_loss.detach()),
                            "value_loss": float(value_loss.detach()),
                            "entropy": float(entropy.detach()),
                            "approx_kl": kl,
                            "clip_fraction": float(
                                ((ratio - 1).abs() > PROFILE["clip"]).double().mean().detach()
                            ),
                        }
                    )
                if kl > PROFILE["target_kl"]:
                    break
            require(
                all(math.isfinite(v) for row in updates for v in row.values()),
                "NONFINITE_PPO_METRICS",
            )
            logs.append(
                {
                    "iteration": iteration + 1,
                    "scenario_id": scenario,
                    "transitions": len(samples),
                    "rollout_cost": world.result()["total_cost"],
                    "rollout_decision_hash": digest(decisions),
                    "optimizer_updates": updates,
                    "kl_early_stop": kl > PROFILE["target_kl"],
                }
            )
        return {
            "iterations": logs,
            "optimizer_steps": steps,
            "transitions": sum(r["transitions"] for r in logs),
            "action_log_prob_semantics": "SAMPLED_LATENT_ACTION_BEFORE_COMMON_GUARD",
            "bootstrap": "ZERO_AT_FINITE_EPISODE_TERMINAL",
            "checkpoint_selection": "FINAL_TRAIN_ONLY",
        }
