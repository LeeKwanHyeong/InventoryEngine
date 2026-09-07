"""Local input preparation and Baseline PSI; no Run Claim or implicit DB access."""

import argparse
import asyncio
import json
import os
from pathlib import Path

from dsio_inventory_engine.inventory_contracts.canonical import CanonicalInputRequest
from dsio_inventory_engine.inventory_contracts.mathematical import MathematicalPolicyRequest
from dsio_inventory_engine.inventory_contracts.training import TrainingRequest
from dsio_inventory_engine.inventory_contracts.evaluation import ProductionEvaluationRequest
from dsio_inventory_engine.evaluate_replenishment.application import (
    EvaluateMathematicalStrategyUseCase,
)
from dsio_inventory_engine.training.prepare import PrepareTrainingFoundationUseCase
from dsio_inventory_engine.recommend_replenishment.application.mathematical import (
    RunMathematicalReplenishmentUseCase,
)
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError, read_json
from dsio_inventory_engine.simulate_inventory.application.baseline import RunPsiSimulationUseCase

from dsio_inventory_engine.inventory_contracts.network import (
    DeploymentScope,
    NetworkInputError,
    NetworkInputRequest,
    json_object,
    require,
)
from dsio_inventory_engine.prepare_inventory.application.network_input import (
    PrepareNetworkInputUseCase,
)


async def prepare(request: NetworkInputRequest, deployment: DeploymentScope, dsn: str) -> dict:
    require(request.context.company_cd == deployment.company_cd, "DEPLOYMENT_COMPANY_MISMATCH")
    require(bool(dsn.strip()), "IO_POSTGRES_DSN_REQUIRED")
    import asyncpg

    from dsio_inventory_engine.infrastructure.postgresql.network_snapshot import (
        PostgresNetworkSnapshotReader,
    )

    connection = await asyncpg.connect(
        dsn, timeout=10, command_timeout=15, server_settings={"default_transaction_read_only": "on"}
    )
    try:
        prepared = await PrepareNetworkInputUseCase(
            PostgresNetworkSnapshotReader(connection), deployment
        ).execute(request)
        return prepared.to_payload()
    finally:
        await connection.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=[
            "prepare-source",
            "prepare-network",
            "simulate-baseline",
            "recommend-mathematical",
            "prepare-training",
            "evaluate-mathematical",
            "train-learned",
            "recommend-learned",
        ],
    )
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--read-postgres", action="store_true")
    parser.add_argument("--snapshot-root", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.action == "prepare-source":
            from dsio_inventory_engine.inventory_contracts.source import SourceInputRequest
            from dsio_inventory_engine.entrypoints.source_input import prepare_source

            require(args.snapshot_root is not None, "SOURCE_ROOT_REQUIRED")
            require(args.request.stat().st_size <= 65536, "REQUEST_SIZE_LIMIT")
            source_request = SourceInputRequest.from_dict(
                read_json(args.request.read_text(encoding="utf-8"))
            )
            deployment = DeploymentScope(
                os.environ.get("IO_COMPANY_CD", ""), os.environ.get("IO_ENVIRONMENT", "")
            )
            result = asyncio.run(
                prepare_source(
                    source_request,
                    deployment,
                    args.snapshot_root,
                    read_postgres=args.read_postgres,
                    dsn=os.environ.get("IO_POSTGRES_DSN", ""),
                )
            )
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            return 0
        if args.action in ("train-learned", "recommend-learned"):
            require(not args.read_postgres, "LEARNED_DATABASE_ACCESS_FORBIDDEN")
            require(args.request.stat().st_size <= 8_000_000, "INPUT_SIZE_LIMIT")
            data = read_json(args.request.read_text(encoding="utf-8"))
            deployment = DeploymentScope(
                os.environ.get("IO_COMPANY_CD", ""), os.environ.get("IO_ENVIRONMENT", "")
            )
            if args.action == "train-learned":
                from dsio_inventory_engine.inventory_contracts.learning import LearningRequest
                from dsio_inventory_engine.fit_replenishment.train import (
                    TrainLearnedStrategiesUseCase,
                )

                result = TrainLearnedStrategiesUseCase(deployment).execute(
                    LearningRequest.from_dict(data)
                )
            else:
                from dsio_inventory_engine.learned.application import (
                    LearnedInferenceRequest,
                    RunLearnedPsiUseCase,
                )

                result = RunLearnedPsiUseCase(deployment).execute(
                    LearnedInferenceRequest.from_dict(data)
                )
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            return 0
        if args.action == "evaluate-mathematical":
            require(not args.read_postgres, "EVALUATION_DATABASE_ACCESS_FORBIDDEN")
            require(args.request.stat().st_size <= 8_000_000, "INPUT_SIZE_LIMIT")
            request_data = ProductionEvaluationRequest.from_dict(
                read_json(args.request.read_text(encoding="utf-8"))
            )
            deployment = DeploymentScope(
                os.environ.get("IO_COMPANY_CD", ""), os.environ.get("IO_ENVIRONMENT", "")
            )
            result = EvaluateMathematicalStrategyUseCase(deployment).execute(request_data)
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            return 0
        if args.action == "prepare-training":
            require(not args.read_postgres, "TRAINING_DATABASE_ACCESS_FORBIDDEN")
            require(args.request.stat().st_size <= 8_000_000, "INPUT_SIZE_LIMIT")
            training_request = TrainingRequest.from_dict(
                read_json(args.request.read_text(encoding="utf-8"))
            )
            deployment = DeploymentScope(
                os.environ.get("IO_COMPANY_CD", ""), os.environ.get("IO_ENVIRONMENT", "")
            )
            result = PrepareTrainingFoundationUseCase(deployment).execute(training_request)
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            return 0
        if args.action in ("simulate-baseline", "recommend-mathematical"):
            require(
                not args.read_postgres,
                "BASELINE_DATABASE_ACCESS_FORBIDDEN"
                if args.action == "simulate-baseline"
                else "MATHEMATICAL_DATABASE_ACCESS_FORBIDDEN",
            )
            require(args.request.stat().st_size <= 8_000_000, "INPUT_SIZE_LIMIT")
            data = read_json(args.request.read_text(encoding="utf-8"))
            deployment = DeploymentScope(
                os.environ.get("IO_COMPANY_CD", ""), os.environ.get("IO_ENVIRONMENT", "")
            )
            result = (
                RunPsiSimulationUseCase(deployment).execute(CanonicalInputRequest.from_dict(data))
                if args.action == "simulate-baseline"
                else RunMathematicalReplenishmentUseCase(deployment).execute(
                    MathematicalPolicyRequest.from_dict(data)
                )
            )
            print(
                json.dumps(
                    result,
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 0
        require(args.request.stat().st_size <= 65536, "REQUEST_SIZE_LIMIT")
        request = NetworkInputRequest.from_dict(
            json_object(args.request.read_text(encoding="utf-8"))
        )
        if not args.read_postgres:
            print(
                json.dumps(
                    {
                        "status": "REQUEST_VALIDATED_ONLY",
                        "network_admitted": False,
                        "database_access": False,
                        "run_claimed": False,
                    },
                    sort_keys=True,
                )
            )
            return 0
        deployment = DeploymentScope(
            os.environ.get("IO_COMPANY_CD", ""), os.environ.get("IO_ENVIRONMENT", "")
        )
        result = asyncio.run(prepare(request, deployment, os.environ.get("IO_POSTGRES_DSN", "")))
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except InventoryInputError as exc:
        print(
            json.dumps(
                {
                    "status": "REJECTED",
                    "error_code": str(exc),
                    "evidence": json.loads(exc.evidence_json),
                },
                ensure_ascii=False,
            )
        )
        return 2
    except NetworkInputError as exc:
        print(json.dumps({"status": "REJECTED", "error_code": str(exc)}))
        return 2
    except Exception:
        print(
            json.dumps(
                {
                    "status": "FAILED",
                    "error_code": "NETWORK_INPUT_PREPARATION_FAILED"
                    if args.action == "prepare-network"
                    else "SOURCE_INPUT_PREPARATION_FAILED"
                    if args.action == "prepare-source"
                    else "LEARNED_EXECUTION_FAILED"
                    if args.action in ("train-learned", "recommend-learned")
                    else "TRAINING_PREPARATION_FAILED"
                    if args.action == "prepare-training"
                    else "PRODUCTION_EVALUATION_FAILED"
                    if args.action == "evaluate-mathematical"
                    else "MATHEMATICAL_REPLENISHMENT_FAILED"
                    if args.action == "recommend-mathematical"
                    else "BASELINE_PSI_FAILED",
                }
            )
        )
        return 1
