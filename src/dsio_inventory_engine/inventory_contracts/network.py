"""Canonical network-master v1 consumer contract, independent of dsai-platform.

This is a wire contract implementation, not a copy of the producer's seed/review
workflow. Producer parity is checked against versioned fixtures and all ten seeds.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

CONTRACT_VERSION = "1.0.0"
REQUEST_CONTRACT = "io-network-input-request-v1"
SOURCE_CONTRACT = "inventory.network_master"
HEADER_KEYS = (
    "network_revision_id",
    "network_id",
    "revision_code",
    "revision_no",
    "company_cd",
    "subs_cd",
    "network_type",
    "environment_scope",
    "effective_from",
    "effective_to",
)
NODE_KEYS = (
    "site_cd",
    "country_name",
    "node_role",
    "echelon_level",
    "parent_site_cd",
    "reference_city",
    "latitude",
    "longitude",
    "location_source_type",
)
NUMERIC_LANE = (
    "great_circle_km",
    "route_factor",
    "route_distance_km",
    "speed_km_per_day",
    "origin_handling_days",
    "departure_wait_days",
    "customs_days",
    "destination_handling_days",
    "linehaul_days",
    "p50_lead_time_days",
    "variability_factor",
    "p90_lead_time_days",
    "planning_lead_time_days",
)
LANE_KEYS = (
    "lane_id",
    "from_site_cd",
    "to_site_cd",
    "transport_mode",
    "route_priority",
    *NUMERIC_LANE,
    "source_type",
    "calculation_method",
    "confidence",
    "calculation_reference_url",
)


class NetworkInputError(ValueError):
    """Stable error code only; never include source rows or connection secrets."""


def require(condition: bool, code: str) -> None:
    if not condition:
        raise NetworkInputError(code)


def canonical_json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def sha256(document: str) -> str:
    return hashlib.sha256(document.encode("utf-8")).hexdigest()


def json_object(document: str) -> dict:
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "DUPLICATE_JSON_KEY")
            result[key] = value
        return result

    try:
        require(
            isinstance(document, str) and len(document.encode("utf-8")) <= 8_000_000,
            "JSON_SIZE_LIMIT",
        )
        value = json.loads(document, object_pairs_hook=pairs)
        require(isinstance(value, dict), "JSON_OBJECT_REQUIRED")
        return value
    except (ValueError, TypeError) as exc:
        if isinstance(exc, NetworkInputError):
            raise
        raise NetworkInputError("INVALID_JSON") from None


def identifier(value: str) -> str:
    require(
        isinstance(value, str)
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value) is not None,
        "INVALID_IDENTIFIER",
    )
    return value


def content_hash(value: str) -> str:
    require(
        isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None,
        "INVALID_CONTENT_HASH",
    )
    return value


def iso_date(value: str) -> date:
    try:
        require(
            isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is not None,
            "INVALID_DATE",
        )
        return date.fromisoformat(value)
    except ValueError:
        raise NetworkInputError("INVALID_DATE") from None


def decimal_text(value: Any, *, minimum: str = "0", maximum: str = "999999999999") -> str:
    try:
        number = Decimal(str(value))
        require(number.is_finite(), "NON_FINITE_NUMBER")
        require(Decimal(minimum) <= number <= Decimal(maximum), "NUMERIC_RANGE")
        require(number.as_tuple().exponent >= -6, "NUMERIC_PRECISION")
        return format(number.normalize(), "f")
    except (InvalidOperation, TypeError):
        raise NetworkInputError("INVALID_NUMBER") from None


@dataclass(frozen=True)
class DeploymentScope:
    company_cd: str
    environment: str

    def __post_init__(self):
        identifier(self.company_cd)
        require(self.environment in {"DEVELOPMENT", "PRODUCTION"}, "INVALID_ENVIRONMENT")


@dataclass(frozen=True)
class NetworkReference:
    network_revision_id: str
    network_content_hash: str
    contract_version: str = CONTRACT_VERSION

    def __post_init__(self):
        try:
            require(
                str(UUID(self.network_revision_id)) == self.network_revision_id,
                "INVALID_REVISION_ID",
            )
        except (ValueError, TypeError, AttributeError):
            raise NetworkInputError("INVALID_REVISION_ID") from None
        content_hash(self.network_content_hash)
        require(self.contract_version == CONTRACT_VERSION, "UNSUPPORTED_NETWORK_CONTRACT")


@dataclass(frozen=True)
class NetworkInputContext:
    engine_run_id: str
    planning_cycle_id: str
    planning_cycle_revision_id: str
    cycle_site_execution_id: str
    company_cd: str
    subs_cd: str
    site_cd: str
    plan_type: str
    master_snapshot_revision: str
    master_as_of_date: str

    def __post_init__(self):
        for key, value in asdict(self).items():
            if key != "master_as_of_date":
                identifier(value)
        require(self.plan_type in {"POSM", "TGSM"}, "UNSUPPORTED_PLAN_TYPE")
        iso_date(self.master_as_of_date)


@dataclass(frozen=True)
class NetworkInputRequest:
    context: NetworkInputContext
    network: NetworkReference

    @classmethod
    def from_dict(cls, data: dict) -> NetworkInputRequest:
        try:
            require(
                set(data) == {"contract_id", "contract_version", "context", "network"},
                "REQUEST_FIELDS",
            )
            require(
                data["contract_id"] == REQUEST_CONTRACT
                and data["contract_version"] == CONTRACT_VERSION,
                "UNSUPPORTED_INPUT_CONTRACT",
            )
            require(
                set(data["context"]) == set(NetworkInputContext.__dataclass_fields__),
                "CONTEXT_FIELDS",
            )
            require(
                set(data["network"]) == set(NetworkReference.__dataclass_fields__),
                "REFERENCE_FIELDS",
            )
            return cls(NetworkInputContext(**data["context"]), NetworkReference(**data["network"]))
        except (KeyError, TypeError):
            raise NetworkInputError("INVALID_REQUEST") from None

    def to_dict(self) -> dict:
        return {
            "contract_id": REQUEST_CONTRACT,
            "contract_version": CONTRACT_VERSION,
            "context": asdict(self.context),
            "network": asdict(self.network),
        }


@dataclass(frozen=True)
class NetworkRevisionRecord:
    canonical_content: str
    stored_content_hash: str
    status: str
    row_version: int
    approval_evidence_json: str


def normalize_network(data: dict) -> str:
    """Normalize Decimal/date representations exactly as network-master v1 specifies."""
    try:
        require(set(data) == {"header", "nodes", "lanes"}, "NETWORK_FIELDS")
        header = dict(data["header"])
        require(set(header) == set(HEADER_KEYS), "HEADER_FIELDS")
        for key in ("company_cd", "subs_cd", "network_id", "revision_code"):
            identifier(header[key])
        require(header["network_type"] == "HUB_SPOKE_2_ECHELON", "NETWORK_TYPE")
        require(type(header["revision_no"]) is int and header["revision_no"] > 0, "REVISION_NUMBER")
        expected_id = str(
            uuid5(
                NAMESPACE_URL,
                canonical_json(
                    [
                        "dsim.network.v1",
                        header["company_cd"],
                        header["network_id"],
                        header["revision_code"],
                    ]
                ),
            )
        )
        require(header["network_revision_id"] == expected_id, "REVISION_ID_CONTENT_MISMATCH")
        require(header["environment_scope"] in {"DEVELOPMENT", "PRODUCTION"}, "ENVIRONMENT_SCOPE")
        start = iso_date(header["effective_from"])
        require(
            header["effective_to"] is None or iso_date(header["effective_to"]) > start,
            "EFFECTIVE_RANGE",
        )
        require(
            isinstance(data["nodes"], (list, tuple)) and isinstance(data["lanes"], (list, tuple)),
            "NETWORK_ROW_ARRAYS",
        )
        require(
            2 <= len(data["nodes"]) <= 500 and 1 <= len(data["lanes"]) <= 2000, "NETWORK_SIZE_LIMIT"
        )
        nodes = sorted((dict(row) for row in data["nodes"]), key=lambda n: n["site_cd"])
        lanes = sorted((dict(row) for row in data["lanes"]), key=lambda n: n["lane_id"])
        require(all(set(n) == set(NODE_KEYS) for n in nodes), "NODE_FIELDS")
        require(all(set(lane) == set(LANE_KEYS) for lane in lanes), "LANE_FIELDS")
        sites = {identifier(n["site_cd"]) for n in nodes}
        require(
            len(sites) == len(nodes)
            and len({identifier(lane["lane_id"]) for lane in lanes}) == len(lanes),
            "DUPLICATE_NETWORK_IDENTITY",
        )
        hubs = [n for n in nodes if n["node_role"] == "HUB_WITH_LOCAL_DEMAND"]
        require(len(hubs) == 1, "EXACTLY_ONE_HUB_REQUIRED")
        hub = hubs[0]["site_cd"]
        for node in nodes:
            is_hub = node["site_cd"] == hub
            require(
                type(node["echelon_level"]) is int
                and node["echelon_level"] == (1 if is_hub else 2)
                and node["node_role"] == ("HUB_WITH_LOCAL_DEMAND" if is_hub else "SPOKE")
                and node["parent_site_cd"] == (None if is_hub else hub),
                "INVALID_TOPOLOGY",
            )
            node["latitude"] = decimal_text(node["latitude"], minimum="-90", maximum="90")
            node["longitude"] = decimal_text(node["longitude"], minimum="-180", maximum="180")
            require(
                node["location_source_type"] in {"SYNTHETIC_REFERENCE_CITY", "VERIFIED_FACILITY"},
                "LOCATION_SOURCE",
            )
            require(
                all(
                    isinstance(node[k], str) and 0 < len(node[k].strip()) <= 256
                    for k in ("country_name", "reference_city")
                ),
                "LOCATION_TEXT",
            )
        modes, priorities, primary = set(), set(), set()
        for lane in lanes:
            pair = (lane["from_site_cd"], lane["to_site_cd"])
            require(pair[0] == hub and pair[1] in sites and pair[1] != hub, "LANE_ENDPOINT")
            require(
                lane["transport_mode"] in {"SEA", "AIR", "ROAD", "MULTIMODAL"}
                and lane["source_type"]
                in {
                    "SYNTHETIC_CALCULATED",
                    "ROUTING_ESTIMATED",
                    "CONTRACTED_LANE",
                    "HISTORICAL_OBSERVED",
                },
                "LANE_ENUM",
            )
            require(
                type(lane["route_priority"]) is int and lane["route_priority"] > 0, "ROUTE_PRIORITY"
            )
            mode, priority = (*pair, lane["transport_mode"]), (*pair, lane["route_priority"])
            require(mode not in modes and priority not in priorities, "DUPLICATE_ROUTE")
            modes.add(mode)
            priorities.add(priority)
            if lane["route_priority"] == 1:
                primary.add(pair[1])
            for key in NUMERIC_LANE:
                lane[key] = decimal_text(lane[key])
            p50, p90, planning = (
                Decimal(lane[k])
                for k in ("p50_lead_time_days", "p90_lead_time_days", "planning_lead_time_days")
            )
            require(
                0 < p50 <= p90 and planning >= p50 and Decimal(lane["speed_km_per_day"]) > 0,
                "LEAD_TIME_ORDER",
            )
            require(lane["confidence"] in {"LOW", "MEDIUM", "HIGH"}, "CONFIDENCE")
            require(
                all(
                    isinstance(lane[k], str) and 0 < len(lane[k].strip()) <= 2048
                    for k in ("calculation_method", "calculation_reference_url")
                ),
                "PROVENANCE",
            )
        require(primary == sites - {hub}, "PRIMARY_LANE_MISSING")
        return canonical_json({"header": header, "nodes": nodes, "lanes": lanes})
    except NetworkInputError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError):
        raise NetworkInputError("INVALID_NETWORK_CONTENT") from None
