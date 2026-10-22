"""Bounded, lossless parts of the existing target result; no new result identity."""

from .values import canonical_json, read_json, require

MAX_ARTIFACT_BYTES = 128_000_000
MAX_RAW_RESULT_BYTES = 20_000_000_000
LIST_FIELDS = {
    "search_result": ("baseline_items", "candidate_evaluations"),
    "order_plan": (
        "baseline_psi_rows",
        "item_plans",
        "psi_rows",
        "decision_evidence",
        "recommended_orders",
    ),
}
RESULT_FIELDS = {
    "calculation_binding",
    "execution_bindings",
    "completed_transition_calls",
    "automatic_publish_allowed",
    "automatic_order_allowed",
    "database_writes",
    "run_claimed",
    "runtime_registered",
    "search_result",
    "selection_result",
    "order_plan",
}


def result_parts(result):
    require(type(result) is dict and set(result) == RESULT_FIELDS, "TARGET_STORAGE_RESULT_INVALID")
    yield (
        "evidence",
        {
            k: v
            for k, v in result.items()
            if k not in {"search_result", "selection_result", "order_plan"}
        },
        None,
    )
    yield "selection_result", result["selection_result"], None
    for section, fields in LIST_FIELDS.items():
        yield section, {k: v for k, v in result[section].items() if k not in fields}, None
        for field in fields:
            rows = result[section][field]
            require(type(rows) is list, "TARGET_STORAGE_ROWS_INVALID")
            batch = 1 if field == "candidate_evaluations" else 128
            for index in range(0, len(rows), batch):
                yield section + "." + field, rows[index : index + batch], index


def verify_partitioned_result(actual, expected):
    """JSON-valid bounded pieces and full replay equality, not a whole-result JSON cap."""
    actual_parts, expected_parts = iter(result_parts(actual)), iter(result_parts(expected))
    total = 0
    while True:
        a, e = next(actual_parts, None), next(expected_parts, None)
        require((a is None) == (e is None), "TARGET_PIPELINE_REPLAY_MISMATCH")
        if a is None:
            return
        role, value, index = a
        payload = canonical_json({"role": role, "index": index, "value": value})
        size = len(payload.encode())
        require(size <= MAX_ARTIFACT_BYTES, "TARGET_STORAGE_SIZE_LIMIT")
        total += size
        require(total <= MAX_RAW_RESULT_BYTES, "TARGET_STORAGE_RESULT_SIZE_LIMIT")
        normalized = read_json(payload, max_bytes=MAX_ARTIFACT_BYTES)
        require(
            normalized == {"role": e[0], "index": e[2], "value": e[1]},
            "TARGET_PIPELINE_REPLAY_MISMATCH",
        )
