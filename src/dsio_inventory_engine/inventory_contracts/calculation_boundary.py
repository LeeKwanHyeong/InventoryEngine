"""Declaration-only calculation boundary; no runner, conversion, Guard or admission.

Reuses existing input/build/config references. A valid declaration does not make
the experimental DLL capable of consuming Canonical input or executing a target
profile. No existing Plan, runtime registry or calculation module imports this.
"""

from dataclasses import dataclass

from .canonical import CanonicalInputRequest, ROW_FIELDS, snapshot_content
from .replenishment import descriptor, execution_config, replenishment_config_content_hash
from .values import (
    canonical_json,
    choice,
    digest,
    hash_value,
    identifier,
    optional,
    read_json,
    require,
    shape,
    text,
)

CONTRACT_ID = "inventory-calculation-boundary-binding"
VERSION = "1.0.0"
BACKENDS = ("CSHARP_PYTHONNET", "PYTHON_EXISTING", "PYTHON_REFERENCE")
PROFILES = ("LEGACY_REPLAY", "TARGET_GUARDED")
EXISTING_PSI_ID = "inventory.psi.weekly_roll_forward"
EXISTING_PSI_VERSION = "1.0.0"
LEGACY_PSI_ID = "inventory.psi.legacy_replay"
SIMULATOR_FIELDS = {
    "implementation_id": identifier,
    "version": identifier,
    "implementation_content_hash": hash_value,
}
SNAPSHOT_BINDING_FIELDS = {"snapshot_id": identifier, "content_hash": hash_value}
STRATEGY_INPUT_FIELDS = {
    "contract_id": identifier,
    "contract_version": identifier,
    **SNAPSHOT_BINDING_FIELDS,
}
EXECUTION_BINDING_FIELDS = {
    "strategy": descriptor,
    "strategy_input_binding": lambda v: shape(v, STRATEGY_INPUT_FIELDS),
    "replenishment_config_content_hash": hash_value,
}
LEGACY_PARAMETER_FIELDS = {"reference": text, "content_hash": hash_value}


def input_bindings(value: dict) -> dict:
    require(type(value) is dict and set(value) == set(ROW_FIELDS), "CALCULATION_SNAPSHOT_SET")
    return {kind: shape(value[kind], SNAPSHOT_BINDING_FIELDS) for kind in ROW_FIELDS}


BINDING_FIELDS = {
    "contract_id": choice(CONTRACT_ID),
    "contract_version": choice(VERSION),
    "execution_mode": choice("CALCULATION_ONLY"),
    "calculation_backend": choice(*BACKENDS),
    "semantics_profile": choice(*PROFILES),
    "psi_scenario_type": choice("BASELINE", "RECOMMENDED"),
    "engine_run_id": identifier,
    "canonical_input_hash": hash_value,
    "prepared_content_hash": hash_value,
    "input_bindings": input_bindings,
    "psi_simulator": lambda v: shape(v, SIMULATOR_FIELDS),
    "guard_order": choice(
        "DIAGNOSTIC_POSTCHECK_NO_MUTATION", "NO_NEW_ACTIONS", "VALIDATE_BEFORE_STATE_COMMIT"
    ),
    "replenishment_execution_binding": optional(lambda v: shape(v, EXECUTION_BINDING_FIELDS)),
    "legacy_parameters_binding": optional(lambda v: shape(v, LEGACY_PARAMETER_FIELDS)),
}


def _guard_order(profile: str, scenario: str) -> str:
    if profile == "LEGACY_REPLAY":
        return "DIAGNOSTIC_POSTCHECK_NO_MUTATION"
    return "NO_NEW_ACTIONS" if scenario == "BASELINE" else "VALIDATE_BEFORE_STATE_COMMIT"


def _validate_profile(value: dict) -> None:
    backend, profile = value["calculation_backend"], value["semantics_profile"]
    require(
        (profile == "LEGACY_REPLAY" and backend in {"CSHARP_PYTHONNET", "PYTHON_REFERENCE"})
        or (profile == "TARGET_GUARDED" and backend in {"CSHARP_PYTHONNET", "PYTHON_EXISTING"}),
        "CALCULATION_BACKEND_PROFILE_MISMATCH",
    )
    require(
        value["guard_order"] == _guard_order(profile, value["psi_scenario_type"]),
        "CALCULATION_GUARD_ORDER_MISMATCH",
    )
    execution = value["replenishment_execution_binding"]
    if profile == "LEGACY_REPLAY":
        # The unchanged kernel creates orders internally. ROP=0 is NOT a Baseline switch.
        require(value["psi_scenario_type"] == "RECOMMENDED", "LEGACY_BASELINE_UNSUPPORTED")
        require(execution is None, "LEGACY_EXTERNAL_STRATEGY_UNSUPPORTED")
        require(value["legacy_parameters_binding"] is not None, "LEGACY_PARAMETERS_REQUIRED")
        require(
            value["psi_simulator"]["implementation_id"] == LEGACY_PSI_ID,
            "CALCULATION_SIMULATOR_SEMANTICS_MISMATCH",
        )
    else:
        require(value["legacy_parameters_binding"] is None, "TARGET_LEGACY_PARAMETERS_FORBIDDEN")
        require(
            (execution is None) == (value["psi_scenario_type"] == "BASELINE"),
            "CALCULATION_STRATEGY_BINDING_REQUIRED",
        )
        require(
            value["psi_simulator"]["implementation_id"] != LEGACY_PSI_ID,
            "CALCULATION_SIMULATOR_SEMANTICS_MISMATCH",
        )
    simulator = value["psi_simulator"]
    old_identity = (simulator["implementation_id"], simulator["version"]) == (
        EXISTING_PSI_ID,
        EXISTING_PSI_VERSION,
    )
    require(
        not old_identity or backend == "PYTHON_EXISTING",
        "CALCULATION_EXISTING_VERSION_REUSE_FORBIDDEN",
    )
    if backend == "PYTHON_EXISTING":
        require(old_identity, "CALCULATION_EXISTING_IMPLEMENTATION_MISMATCH")


@dataclass(frozen=True)
class CalculationBoundaryBinding:
    """Reference consistency only; never an approval or implementation capability."""

    document_json: str

    @classmethod
    def from_dict(cls, value: dict) -> "CalculationBoundaryBinding":
        normalized = shape(value, BINDING_FIELDS)
        _validate_profile(normalized)
        return cls(canonical_json(normalized))

    @classmethod
    def from_sources(
        cls,
        canonical_input: CanonicalInputRequest,
        *,
        prepared_content_hash: str,
        psi_simulator: dict,
        calculation_backend: str,
        semantics_profile: str,
        psi_scenario_type: str,
        execution: dict | None = None,
        legacy_parameters_binding: dict | None = None,
    ) -> "CalculationBoundaryBinding":
        canonical = CanonicalInputRequest.from_dict(canonical_input.to_dict())
        source = canonical.to_dict()
        for kind, snapshot in source["snapshots"].items():
            require(snapshot["status"] == "SEALED", "CALCULATION_SEALED_INPUT_REQUIRED")
            require(
                snapshot["row_count"] == len(snapshot["rows"])
                and snapshot["content_hash"] == digest(snapshot_content(kind, snapshot))
                and source["input_bindings"][kind]
                == {k: snapshot[k] for k in SNAPSHOT_BINDING_FIELDS},
                "CALCULATION_SOURCE_BINDING_MISMATCH",
            )
        binding = None
        if execution is not None:
            normalized = execution_config(execution)
            require(
                normalized["canonical_input_hash"] == canonical.input_hash
                and normalized["configuration_revision"]
                == source["context"]["configuration_revision"],
                "CALCULATION_EXECUTION_SOURCE_MISMATCH",
            )
            require("strategy_input_binding" in normalized, "CALCULATION_BOUND_STRATEGY_REQUIRED")
            binding = {
                "strategy": normalized["strategy"],
                "strategy_input_binding": normalized["strategy_input_binding"],
                "replenishment_config_content_hash": replenishment_config_content_hash(normalized),
            }
        return cls.from_dict(
            {
                "contract_id": CONTRACT_ID,
                "contract_version": VERSION,
                "execution_mode": "CALCULATION_ONLY",
                "calculation_backend": calculation_backend,
                "semantics_profile": semantics_profile,
                "psi_scenario_type": psi_scenario_type,
                "engine_run_id": source["context"]["engine_run_id"],
                "canonical_input_hash": canonical.input_hash,
                "prepared_content_hash": hash_value(prepared_content_hash),
                "input_bindings": source["input_bindings"],
                "psi_simulator": psi_simulator,
                "guard_order": _guard_order(semantics_profile, psi_scenario_type),
                "replenishment_execution_binding": binding,
                "legacy_parameters_binding": legacy_parameters_binding,
            }
        )

    def to_dict(self) -> dict:
        return read_json(self.document_json)

    def verify_sources(self, canonical_input: CanonicalInputRequest, **expected) -> dict:
        """Expected references must come from the caller's trusted input/build binding.

        In particular prepared_content_hash is not a proof of an exact Canonical
        projection. The existing validate_prepared_projection remains mandatory
        at an execution boundary. This class does not run that application code.
        """
        actual = type(self).from_dict(self.to_dict())
        wanted = type(self).from_sources(canonical_input, **expected)
        require(actual == wanted, "CALCULATION_SOURCE_BINDING_MISMATCH")
        return actual.to_dict()
