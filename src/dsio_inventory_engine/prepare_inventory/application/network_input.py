"""Admit one approved network as immutable context for a single-site IO input."""

from dataclasses import asdict, dataclass
from datetime import datetime

from dsio_inventory_engine.inventory_contracts.network import (
    CONTRACT_VERSION,
    SOURCE_CONTRACT,
    DeploymentScope,
    NetworkInputRequest,
    canonical_json,
    iso_date,
    json_object,
    normalize_network,
    require,
    sha256,
)
from dsio_inventory_engine.prepare_inventory.ports.network_snapshot import NetworkSnapshotReader


@dataclass(frozen=True)
class PreparedNetworkInput:
    """Prepared in memory, not a persisted/SEALED Run Evidence receipt."""

    manifest_json: str
    network_content_json: str
    site_context_json: str

    @property
    def binding_hash(self) -> str:
        return json_object(self.manifest_json)["network_input_binding_hash"]

    def to_payload(self) -> dict:
        return {
            "status": "NETWORK_INPUT_PREPARED",
            "run_claimed": False,
            "psi_computed": False,
            "database_writes": False,
            "manifest": json_object(self.manifest_json),
            "network_snapshot": json_object(self.network_content_json),
            "site_network_context": json_object(self.site_context_json),
        }


class PrepareNetworkInputUseCase:
    def __init__(self, reader: NetworkSnapshotReader, deployment: DeploymentScope):
        self.reader = reader
        self.deployment = deployment

    async def execute(self, request: NetworkInputRequest) -> PreparedNetworkInput:
        request = NetworkInputRequest.from_dict(request.to_dict())
        context, reference = request.context, request.network
        require(context.company_cd == self.deployment.company_cd, "DEPLOYMENT_COMPANY_MISMATCH")
        record = await self.reader.read_revision(reference.network_revision_id)
        require(record is not None, "NETWORK_REVISION_NOT_FOUND")
        require(record.status == "APPROVED", "NETWORK_NOT_APPROVED")
        require(type(record.row_version) is int and record.row_version >= 1, "APPROVAL_VERSION")
        document = normalize_network(json_object(record.canonical_content))
        digest = sha256(document)
        require(
            digest == record.stored_content_hash == reference.network_content_hash,
            "NETWORK_CONTENT_HASH_MISMATCH",
        )
        data = json_object(document)
        header = data["header"]
        require(
            header["network_revision_id"] == reference.network_revision_id,
            "NETWORK_REVISION_MISMATCH",
        )
        require(
            header["company_cd"] == context.company_cd and header["subs_cd"] == context.subs_cd,
            "NETWORK_BUSINESS_SCOPE_MISMATCH",
        )
        require(
            header["environment_scope"] == self.deployment.environment,
            "NETWORK_ENVIRONMENT_MISMATCH",
        )
        as_of = iso_date(context.master_as_of_date)
        require(
            iso_date(header["effective_from"]) <= as_of
            and (header["effective_to"] is None or as_of < iso_date(header["effective_to"])),
            "NETWORK_OUTSIDE_EFFECTIVE_RANGE",
        )
        evidence = json_object(record.approval_evidence_json)
        require(evidence.get("content_sha256") == digest, "APPROVAL_HASH_MISMATCH")
        require(
            all(
                evidence.get(k) is True
                for k in ("location_reviewed", "route_reviewed", "lead_time_reviewed")
            ),
            "APPROVAL_REVIEW_MISSING",
        )
        require(
            all(
                isinstance(evidence.get(k), str) and evidence[k].strip()
                for k in ("approved_by", "evidence_reference", "approved_at")
            ),
            "APPROVAL_EVIDENCE_MISSING",
        )
        try:
            stamp = datetime.fromisoformat(evidence["approved_at"].replace("Z", "+00:00"))
            require(stamp.tzinfo is not None, "APPROVAL_TIME_INVALID")
        except ValueError:
            require(False, "APPROVAL_TIME_INVALID")
        synthetic = any(
            n["location_source_type"] != "VERIFIED_FACILITY" for n in data["nodes"]
        ) or any(
            lane["source_type"] not in {"CONTRACTED_LANE", "HISTORICAL_OBSERVED"}
            for lane in data["lanes"]
        )
        if synthetic:
            require(self.deployment.environment == "DEVELOPMENT", "SYNTHETIC_NETWORK_FORBIDDEN")
            require(
                evidence.get("review_purpose") == "DEVELOPMENT_SCENARIO"
                and evidence.get("synthetic_assumptions_accepted") is True,
                "SYNTHETIC_NETWORK_NOT_ACCEPTED",
            )
        selected = [n for n in data["nodes"] if n["site_cd"] == context.site_cd]
        require(len(selected) == 1, "SITE_NOT_IN_NETWORK")
        candidates = [
            lane
            for lane in data["lanes"]
            if context.site_cd in (lane["from_site_cd"], lane["to_site_cd"])
        ]
        neighbors = sorted(
            {
                s
                for lane in candidates
                for s in (lane["from_site_cd"], lane["to_site_cd"])
                if s != context.site_cd
            }
        )
        site_context = {
            "usage": "CONTEXT_ONLY",
            "calculation_site_cd": context.site_cd,
            "site_node": selected[0],
            "neighbor_site_cds": neighbors,
            "candidate_lanes": candidates,
            "selected_lane_ids": [],
        }
        binding = {
            "source_contract_key": SOURCE_CONTRACT,
            "source_contract_version": CONTRACT_VERSION,
            "source_snapshot_id": reference.network_revision_id,
            "source_content_hash": digest,
            "approval_row_version": record.row_version,
            "deployment_environment": self.deployment.environment,
            "context": {k: v for k, v in asdict(context).items() if k != "engine_run_id"},
            "site_context_hash": sha256(canonical_json(site_context)),
        }
        manifest = {
            **binding,
            "engine_run_id": context.engine_run_id,
            "input_type": "INVENTORY_NETWORK",
            "network_input_binding_hash": sha256(canonical_json(binding)),
            "admission_status": "APPROVED_AT_PREPARATION",
            "persistence_status": "PREPARED_IN_MEMORY",
        }
        return PreparedNetworkInput(
            canonical_json(manifest), document, canonical_json(site_context)
        )
