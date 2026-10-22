"""Closed read-time views; never a replacement for a result or an approval."""

from .values import (
    canonical_json,
    choice,
    day,
    digest,
    hash_value,
    identifier,
    integer,
    read_json,
    require,
    shape,
    yyyyww,
)

CONTRACT_ID = "inventory-robustness-verification-view"
CONTRACT_VERSION = "1.0.0"
LEVELS = ("ARTIFACT_INTEGRITY", "FULL_PSI_RECALCULATION")
CONTEXT = {
    **{k: identifier for k in ("company_cd", "subs_cd", "site_cd", "plan_id", "engine_run_id")},
    "plan_yyyyww": yyyyww,
    "plan_start_date": day,
}
COUNTS = ("candidate_count", "successful_count", "failed_count", "psi_row_count", "leaf_count")
STATUSES = (
    "source_recalculation_status",
    "psi_recalculation_status",
    "metrics_recalculation_status",
)
SAFETY = (
    "automatic_publish_allowed",
    "automatic_order_allowed",
    "operational_policy_approved",
    "database_writes",
)


def normalize_verification_view(document: dict) -> dict:
    value = shape(
        document,
        {
            "contract_id": choice(CONTRACT_ID),
            "contract_version": choice(CONTRACT_VERSION),
            "verification_level": choice(*LEVELS),
            "artifact_integrity_status": choice("VERIFIED"),
            **{k: choice("NOT_RUN", "VERIFIED") for k in STATUSES},
            "execution_id": identifier,
            **{
                k: hash_value
                for k in ("input_hash", "manifest_hash", "result_hash", "content_hash")
            },
            "context": lambda v: shape(v, CONTEXT),
            "counts": lambda v: shape(v, {k: integer for k in COUNTS}),
            **{k: lambda v: v for k in SAFETY},
        },
    )
    expected = "NOT_RUN" if value["verification_level"] == LEVELS[0] else "VERIFIED"
    require(all(value[k] == expected for k in STATUSES), "VERIFICATION_LEVEL_STATUS_MISMATCH")
    require(all(value[k] is False for k in SAFETY), "VERIFICATION_CANNOT_GRANT_APPROVAL")
    counts = value["counts"]
    require(
        counts["candidate_count"] in (32, 88)
        and counts["candidate_count"] == counts["successful_count"] + counts["failed_count"]
        and counts["psi_row_count"] == 65 * counts["successful_count"]
        and counts["leaf_count"] == (counts["candidate_count"] + 15) // 16,
        "VERIFICATION_COUNTS_INVALID",
    )
    require(
        digest({k: v for k, v in value.items() if k != "content_hash"}) == value["content_hash"],
        "VERIFICATION_VIEW_HASH_MISMATCH",
    )
    return read_json(canonical_json(value))


def verification_view(
    *,
    level: str,
    execution_id: str,
    input_hash: str,
    manifest_hash: str,
    result_hash: str,
    context: dict,
    counts: dict,
) -> dict:
    status = "NOT_RUN" if level == LEVELS[0] else "VERIFIED"
    value = {
        "contract_id": CONTRACT_ID,
        "contract_version": CONTRACT_VERSION,
        "verification_level": level,
        "artifact_integrity_status": "VERIFIED",
        **{k: status for k in STATUSES},
        "execution_id": execution_id,
        "input_hash": input_hash,
        "manifest_hash": manifest_hash,
        "result_hash": result_hash,
        "context": {k: context[k] for k in CONTEXT},
        "counts": counts,
        **{k: False for k in SAFETY},
    }
    return normalize_verification_view({**value, "content_hash": digest(value)})
