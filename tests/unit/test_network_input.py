import asyncio
import contextlib
import copy
import io
import json
import unittest
from dataclasses import FrozenInstanceError, replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

from dsio_inventory_engine.entrypoints.cli import main, prepare
from dsio_inventory_engine.infrastructure.postgresql.network_snapshot import (
    HEADER_SQL,
    LANES_SQL,
    NODES_SQL,
    PostgresNetworkSnapshotReader,
)
from dsio_inventory_engine.inventory_contracts.network import (
    NUMERIC_LANE,
    DeploymentScope,
    NetworkInputError,
    NetworkInputRequest,
    NetworkRevisionRecord,
    canonical_json,
    json_object,
    normalize_network,
    sha256,
)
from dsio_inventory_engine.prepare_inventory.application.network_input import (
    PrepareNetworkInputUseCase,
)

ROOT = Path(__file__).resolve().parents[2]
EXPECTED_HASH = "46f520b888c619050812ba21c4e66f052ada71d4cf581ab021dfc8b6d809938a"


def fixture():
    return json.loads((ROOT / "tests/fixtures/network_c100.json").read_text())


def request_dict():
    return json.loads((ROOT / "examples/network_input_request.json").read_text())


def record_for(data=None, **changes):
    document = normalize_network(fixture() if data is None else data)
    evidence = {
        "content_sha256": sha256(document),
        "approved_by": "test-approver",
        "approved_at": "2026-09-03T00:00:00+00:00",
        "evidence_reference": "test-evidence",
        "location_reviewed": True,
        "route_reviewed": True,
        "lead_time_reviewed": True,
        "review_purpose": "DEVELOPMENT_SCENARIO",
        "synthetic_assumptions_accepted": True,
    }
    record = NetworkRevisionRecord(
        document, sha256(document), "APPROVED", 1, canonical_json(evidence)
    )
    return replace(record, **changes)


class Reader:
    def __init__(self, record):
        self.record, self.calls = record, []

    async def read_revision(self, revision_id):
        self.calls.append(revision_id)
        return self.record


class InputTests(unittest.IsolatedAsyncioTestCase):
    async def execute(self, record=None, request=None, deployment=None):
        source = Reader(record or record_for())
        usecase = PrepareNetworkInputUseCase(
            source, deployment or DeploymentScope("DSE", "DEVELOPMENT")
        )
        return await usecase.execute(NetworkInputRequest.from_dict(request or request_dict()))

    async def test_spoke_receives_only_its_two_candidate_lanes(self):
        result = (await self.execute()).to_payload()
        context = result["site_network_context"]
        self.assertEqual(context["neighbor_site_cds"], ["V100"])
        self.assertEqual(len(context["candidate_lanes"]), 2)
        self.assertEqual(context["selected_lane_ids"], [])
        self.assertEqual(context["usage"], "CONTEXT_ONLY")
        self.assertFalse(
            result["run_claimed"] or result["psi_computed"] or result["database_writes"]
        )
        self.assertEqual(result["manifest"]["source_content_hash"], EXPECTED_HASH)

    async def test_hub_receives_context_without_aggregating_spoke_demand(self):
        req = request_dict()
        req["context"]["site_cd"] = "V100"
        result = (await self.execute(request=req)).to_payload()["site_network_context"]
        self.assertEqual(result["calculation_site_cd"], "V100")
        self.assertEqual(len(result["neighbor_site_cds"]), 4)
        self.assertEqual(len(result["candidate_lanes"]), 8)

    async def test_posm_and_tgsm_use_the_same_network_contract(self):
        for plan_type in ("POSM", "TGSM"):
            req = request_dict()
            req["context"]["plan_type"] = plan_type
            self.assertEqual(
                (await self.execute(request=req)).to_payload()["manifest"]["context"]["plan_type"],
                plan_type,
            )

    async def test_one_read_then_snapshot_is_immutable_and_detached(self):
        reader = Reader(record_for())
        snapshot = await PrepareNetworkInputUseCase(
            reader, DeploymentScope("DSE", "DEVELOPMENT")
        ).execute(NetworkInputRequest.from_dict(request_dict()))
        reader.record = None
        exposed = snapshot.to_payload()
        exposed["network_snapshot"]["lanes"][0]["planning_lead_time_days"] = "99999"
        self.assertNotEqual(snapshot.to_payload()["network_snapshot"], exposed["network_snapshot"])
        with self.assertRaises(FrozenInstanceError):
            snapshot.manifest_json = "{}"
        self.assertEqual(reader.calls, [request_dict()["network"]["network_revision_id"]])

    async def test_retry_run_id_changes_do_not_change_binding_hash(self):
        req = request_dict()
        first = await self.execute(request=req)
        req["context"]["engine_run_id"] = "retry-run-2"
        second = await self.execute(request=req)
        self.assertEqual(first.binding_hash, second.binding_hash)
        self.assertNotEqual(first.manifest_json, second.manifest_json)

    async def test_new_cycle_or_scope_or_master_changes_binding_hash(self):
        first = (await self.execute()).binding_hash
        for key, value in (
            ("planning_cycle_revision_id", "revision-2"),
            ("master_snapshot_revision", "master-2"),
            ("site_cd", "V102"),
            ("master_as_of_date", "2026-09-04"),
        ):
            with self.subTest(key=key):
                req = request_dict()
                req["context"][key] = value
                self.assertNotEqual(first, (await self.execute(request=req)).binding_hash)

    async def test_unknown_revision_has_no_latest_fallback(self):
        reader = Reader(None)
        with self.assertRaisesRegex(NetworkInputError, "NETWORK_REVISION_NOT_FOUND"):
            await PrepareNetworkInputUseCase(reader, DeploymentScope("DSE", "DEVELOPMENT")).execute(
                NetworkInputRequest.from_dict(request_dict())
            )
        self.assertEqual(len(reader.calls), 1)

    async def test_deployment_company_rejected_before_source_access(self):
        reader = Reader(record_for())
        with self.assertRaisesRegex(NetworkInputError, "DEPLOYMENT_COMPANY_MISMATCH"):
            await PrepareNetworkInputUseCase(
                reader, DeploymentScope("OTHER", "DEVELOPMENT")
            ).execute(NetworkInputRequest.from_dict(request_dict()))
        self.assertEqual(reader.calls, [])

    async def test_draft_and_superseded_never_admitted(self):
        for status in ("DRAFT", "SUPERSEDED"):
            with (
                self.subTest(status=status),
                self.assertRaisesRegex(NetworkInputError, "NETWORK_NOT_APPROVED"),
            ):
                await self.execute(record=record_for(status=status))

    async def test_approval_row_version_must_be_positive_integer(self):
        for version in (0, True, -1, "1"):
            with (
                self.subTest(version=version),
                self.assertRaisesRegex(NetworkInputError, "APPROVAL_VERSION"),
            ):
                await self.execute(record=record_for(row_version=version))

    async def test_request_hash_mismatch(self):
        req = request_dict()
        req["network"]["network_content_hash"] = "0" * 64
        with self.assertRaisesRegex(NetworkInputError, "NETWORK_CONTENT_HASH_MISMATCH"):
            await self.execute(request=req)

    async def test_persisted_hash_mismatch(self):
        with self.assertRaisesRegex(NetworkInputError, "NETWORK_CONTENT_HASH_MISMATCH"):
            await self.execute(record=record_for(stored_content_hash="0" * 64))

    async def test_tampered_content_is_not_trusted(self):
        record = record_for()
        data = json_object(record.canonical_content)
        data["lanes"][0]["planning_lead_time_days"] = "999"
        with self.assertRaisesRegex(NetworkInputError, "NETWORK_CONTENT_HASH_MISMATCH"):
            await self.execute(record=replace(record, canonical_content=normalize_network(data)))

    async def test_wrong_subsidiary_and_orphan_site_rejected(self):
        for key, value, code in (
            ("subs_cd", "C110", "NETWORK_BUSINESS_SCOPE_MISMATCH"),
            ("site_cd", "V999", "SITE_NOT_IN_NETWORK"),
        ):
            req = request_dict()
            req["context"][key] = value
            with self.subTest(key=key), self.assertRaisesRegex(NetworkInputError, code):
                await self.execute(request=req)

    async def test_development_network_never_enters_production(self):
        with self.assertRaisesRegex(NetworkInputError, "NETWORK_ENVIRONMENT_MISMATCH"):
            await self.execute(deployment=DeploymentScope("DSE", "PRODUCTION"))

    async def test_synthetic_even_if_mislabelled_production_is_rejected(self):
        data, req = fixture(), request_dict()
        data["header"]["environment_scope"] = "PRODUCTION"
        record = record_for(data)
        req["network"]["network_content_hash"] = record.stored_content_hash
        with self.assertRaisesRegex(NetworkInputError, "SYNTHETIC_NETWORK_FORBIDDEN"):
            await self.execute(
                record=record, request=req, deployment=DeploymentScope("DSE", "PRODUCTION")
            )

    async def test_effectivity_uses_plan_date_inclusive_start_exclusive_end(self):
        data, req = fixture(), request_dict()
        data["header"]["effective_to"] = "2026-09-04"
        record = record_for(data)
        req["network"]["network_content_hash"] = record.stored_content_hash
        req["context"]["master_as_of_date"] = data["header"]["effective_from"]
        await self.execute(record=record, request=req)
        for at in ("2020-01-01", "2026-09-04"):
            req["context"]["master_as_of_date"] = at
            with (
                self.subTest(at=at),
                self.assertRaisesRegex(NetworkInputError, "NETWORK_OUTSIDE_EFFECTIVE_RANGE"),
            ):
                await self.execute(record=record, request=req)

    async def test_approval_evidence_is_validated(self):
        for key, value, code in (
            ("content_sha256", "0" * 64, "APPROVAL_HASH_MISMATCH"),
            ("route_reviewed", "true", "APPROVAL_REVIEW_MISSING"),
            ("approved_by", "", "APPROVAL_EVIDENCE_MISSING"),
            ("approved_at", "not-a-time", "APPROVAL_TIME_INVALID"),
            ("approved_at", "2026-09-03T00:00:00", "APPROVAL_TIME_INVALID"),
            ("synthetic_assumptions_accepted", False, "SYNTHETIC_NETWORK_NOT_ACCEPTED"),
        ):
            record = record_for()
            evidence = json_object(record.approval_evidence_json)
            evidence[key] = value
            with (
                self.subTest(key=key, value=value),
                self.assertRaisesRegex(NetworkInputError, code),
            ):
                await self.execute(
                    record=replace(record, approval_evidence_json=canonical_json(evidence))
                )


class ContractTests(unittest.TestCase):
    def test_fixture_hash_and_decimal_order_normalization(self):
        data = fixture()
        original = copy.deepcopy(data)
        self.assertEqual(sha256(normalize_network(data)), EXPECTED_HASH)
        self.assertEqual(data, original)
        data["nodes"].reverse()
        data["lanes"].reverse()
        for row in data["lanes"]:
            for key in NUMERIC_LANE:
                row[key] = Decimal(row[key]).quantize(Decimal("0.000001"))
        self.assertEqual(sha256(normalize_network(data)), EXPECTED_HASH)

    def test_invalid_numeric_and_topology_rejected(self):
        for value in ("NaN", "Infinity", "-1", "1.0000001", True):
            data = fixture()
            data["lanes"][0]["planning_lead_time_days"] = value
            with self.subTest(value=value), self.assertRaises(NetworkInputError):
                normalize_network(data)
        data = fixture()
        data["nodes"][1]["parent_site_cd"] = "V999"
        with self.assertRaisesRegex(NetworkInputError, "INVALID_TOPOLOGY"):
            normalize_network(data)

    def test_duplicate_nodes_lanes_and_extra_fields_rejected(self):
        for key in ("nodes", "lanes"):
            data = fixture()
            data[key].append(dict(data[key][0]))
            with (
                self.subTest(key=key),
                self.assertRaisesRegex(NetworkInputError, "DUPLICATE_NETWORK_IDENTITY"),
            ):
                normalize_network(data)
        data = fixture()
        data["lanes"][0]["unexpected"] = 1
        with self.assertRaisesRegex(NetworkInputError, "LANE_FIELDS"):
            normalize_network(data)

    def test_missing_or_injected_request_fields_rejected(self):
        for section, key, value in (
            ("context", "site_cd", "V100;DELETE"),
            ("context", "plan_type", "OTHER"),
            ("network", "contract_version", "2.0.0"),
            ("network", "network_revision_id", "latest"),
            ("network", "network_content_hash", "bad"),
        ):
            req = request_dict()
            req[section][key] = value
            with self.subTest(key=key), self.assertRaises(NetworkInputError):
                NetworkInputRequest.from_dict(req)
        for section in ("context", "network"):
            req = request_dict()
            req[section]["extra"] = "ignored?"
            with self.subTest(section=section), self.assertRaises(NetworkInputError):
                NetworkInputRequest.from_dict(req)

    def test_duplicate_json_keys_rejected(self):
        with self.assertRaisesRegex(NetworkInputError, "DUPLICATE_JSON_KEY"):
            json_object('{"network":{},"network":{}}')

    def test_cli_default_never_connects_or_claims_run(self):
        stream = io.StringIO()
        with patch.dict("os.environ", {}, clear=True), contextlib.redirect_stdout(stream):
            code = main(
                ["prepare-network", "--request", str(ROOT / "examples/network_input_request.json")]
            )
        self.assertEqual(code, 0)
        self.assertFalse(json.loads(stream.getvalue())["database_access"])

    def test_missing_dsn_rejected_before_importing_optional_driver(self):
        with self.assertRaisesRegex(NetworkInputError, "IO_POSTGRES_DSN_REQUIRED"):
            asyncio.run(
                prepare(
                    NetworkInputRequest.from_dict(request_dict()),
                    DeploymentScope("DSE", "DEVELOPMENT"),
                    "",
                )
            )


class FakeConnection:
    def __init__(self, missing=False):
        self.calls, self.options, self.missing = [], None, missing

    @contextlib.asynccontextmanager
    async def transaction(self, **options):
        self.options = options
        yield

    async def execute(self, sql):
        self.calls.append((sql, ()))

    async def fetchrow(self, sql, key):
        self.calls.append((sql, (key,)))
        if self.missing:
            return None
        data, record = fixture(), record_for()
        header = data["header"]
        header["network_revision_id"] = UUID(header["network_revision_id"])
        header["effective_from"] = date.fromisoformat(header["effective_from"])
        return {
            **header,
            "content_sha256": record.stored_content_hash,
            "status": record.status,
            "row_version": record.row_version,
            "approval_evidence": record.approval_evidence_json,
        }

    async def fetch(self, sql, key):
        self.calls.append((sql, (key,)))
        return fixture()["nodes" if sql == NODES_SQL else "lanes"]


class AdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_id_readonly_repeatable_read_and_full_hash(self):
        connection = FakeConnection()
        record = await PostgresNetworkSnapshotReader(connection).read_revision(
            request_dict()["network"]["network_revision_id"]
        )
        self.assertEqual(connection.options, {"isolation": "repeatable_read", "readonly": True})
        self.assertEqual(record.stored_content_hash, sha256(record.canonical_content))
        self.assertEqual(
            [sql for sql, _ in connection.calls][1:], [HEADER_SQL, NODES_SQL, LANES_SQL]
        )
        self.assertTrue(
            all("$1" in sql and isinstance(args[0], UUID) for sql, args in connection.calls[1:])
        )

    async def test_missing_header_does_not_query_children(self):
        connection = FakeConnection(missing=True)
        self.assertIsNone(
            await PostgresNetworkSnapshotReader(connection).read_revision(
                request_dict()["network"]["network_revision_id"]
            )
        )
        self.assertEqual(len(connection.calls), 2)


if __name__ == "__main__":
    unittest.main()
