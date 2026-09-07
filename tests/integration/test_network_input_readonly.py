"""Opt-in consumer integration. No migrations, claims, result writes or Graph access."""

import json
import os
import unittest
from pathlib import Path

from dsio_inventory_engine.entrypoints.cli import prepare
from dsio_inventory_engine.infrastructure.postgresql.network_snapshot import (
    PostgresNetworkSnapshotReader,
)
from dsio_inventory_engine.inventory_contracts.network import (
    DeploymentScope,
    NetworkInputError,
    NetworkInputRequest,
)
from dsio_inventory_engine.prepare_inventory.application.network_input import (
    PrepareNetworkInputUseCase,
)

ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(
    os.environ.get("IO_READONLY_INTEGRATION") == "1", "explicit read-only integration not enabled"
)
class ReadonlyNetworkInputIntegration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import asyncpg

        self.dsn = os.environ["IO_POSTGRES_DSN"]
        self.connection = await asyncpg.connect(
            self.dsn,
            timeout=10,
            command_timeout=15,
            server_settings={
                "default_transaction_read_only": "on",
                "application_name": "io_network_input_readonly_test",
            },
        )
        self.assertEqual(await self.connection.fetchval("SHOW default_transaction_read_only"), "on")
        self.deployment = DeploymentScope(os.environ["IO_COMPANY_CD"], os.environ["IO_ENVIRONMENT"])
        self.reader = PostgresNetworkSnapshotReader(self.connection)

    async def asyncTearDown(self):
        await self.connection.close()

    def example(self):
        return json.loads((ROOT / "examples/network_input_request.json").read_text())

    async def test_pinned_ten_networks_all_fifty_sites(self):
        bindings = json.loads((ROOT / "tests/fixtures/network_bindings.json").read_text())
        count = 0
        for binding in bindings:
            for site in binding["sites"]:
                req = self.example()
                req["context"].update(subs_cd=binding["subs_cd"], site_cd=site["site_cd"])
                req["network"].update(
                    network_revision_id=binding["network_revision_id"],
                    network_content_hash=binding["network_content_hash"],
                )
                with self.subTest(site=site["site_cd"]):
                    output = await PrepareNetworkInputUseCase(self.reader, self.deployment).execute(
                        NetworkInputRequest.from_dict(req)
                    )
                    payload = output.to_payload()
                    expected = 8 if site["node_role"] == "HUB_WITH_LOCAL_DEMAND" else 2
                    self.assertEqual(
                        len(payload["site_network_context"]["candidate_lanes"]), expected
                    )
                    self.assertEqual(
                        payload["manifest"]["source_content_hash"], binding["network_content_hash"]
                    )
                    self.assertEqual(
                        payload["manifest"]["persistence_status"], "PREPARED_IN_MEMORY"
                    )
                    self.assertFalse(
                        payload["database_writes"]
                        or payload["run_claimed"]
                        or payload["psi_computed"]
                    )
                count += 1
        self.assertEqual(count, 50)

    async def test_wrong_hash_does_not_resolve_another_revision(self):
        req = self.example()
        req["network"]["network_content_hash"] = "0" * 64
        with self.assertRaisesRegex(NetworkInputError, "NETWORK_CONTENT_HASH_MISMATCH"):
            await PrepareNetworkInputUseCase(self.reader, self.deployment).execute(
                NetworkInputRequest.from_dict(req)
            )

    async def test_unknown_revision_fails_closed(self):
        req = self.example()
        req["network"]["network_revision_id"] = "00000000-0000-0000-0000-000000000001"
        with self.assertRaisesRegex(NetworkInputError, "NETWORK_REVISION_NOT_FOUND"):
            await PrepareNetworkInputUseCase(self.reader, self.deployment).execute(
                NetworkInputRequest.from_dict(req)
            )

    async def test_production_consumer_cannot_use_development_seed(self):
        with self.assertRaisesRegex(NetworkInputError, "NETWORK_ENVIRONMENT_MISMATCH"):
            await PrepareNetworkInputUseCase(
                self.reader, DeploymentScope(self.deployment.company_cd, "PRODUCTION")
            ).execute(NetworkInputRequest.from_dict(self.example()))

    async def test_cli_preparation_path_reads_without_claiming_run(self):
        output = await prepare(
            NetworkInputRequest.from_dict(self.example()), self.deployment, self.dsn
        )
        self.assertEqual(output["status"], "NETWORK_INPUT_PREPARED")
        self.assertFalse(
            output["run_claimed"] or output["psi_computed"] or output["database_writes"]
        )


if __name__ == "__main__":
    unittest.main()
