"""Shared verification of immutable, development-only candidate evaluation artifacts."""

from dsio_inventory_engine.inventory_contracts.values import digest, require


def verify_policy_search_result(value: dict) -> dict:
    require(
        type(value) is dict
        and value.get("contract_id") == "inventory-policy-search-result"
        and value.get("contract_version") == "1.0.0",
        "POLICY_SEARCH_RESULT_CONTRACT_INVALID",
    )
    require(
        value.get("selected_policies") is None
        and value.get("automatic_publish_allowed") is False
        and value.get("automatic_order_allowed") is False,
        "POLICY_SEARCH_PUBLICATION_FORBIDDEN",
    )
    require(
        value.get("content_hash")
        == digest({key: item for key, item in value.items() if key != "content_hash"}),
        "POLICY_SEARCH_RESULT_HASH_MISMATCH",
    )
    for child in value["candidate_evaluations"]:
        require(
            child["canonical_input_hash"] == value["canonical_input_hash"]
            and child["prepared_input_hash"] == value["prepared_input_hash"],
            "POLICY_SEARCH_CHILD_INPUT_MISMATCH",
        )
        require(
            child["automatic_publish_allowed"] is False
            and child["automatic_order_allowed"] is False,
            "POLICY_SEARCH_PUBLICATION_FORBIDDEN",
        )
        require(
            child["content_hash"]
            == digest({key: item for key, item in child.items() if key != "content_hash"}),
            "POLICY_SEARCH_CHILD_HASH_MISMATCH",
        )
        if child["status"] == "SUCCEEDED":
            require(
                type(child["row_count"]) is int
                and child["row_count"] > 0
                and len(child["psi_rows"]) == child["row_count"]
                and child["metrics"] is not None,
                "POLICY_SEARCH_EMPTY_SUCCESS",
            )
        else:
            require(
                child["status"] == "FAILED"
                and all(
                    child[key] is None
                    for key in ("row_count", "psi_rows", "decision_evidence", "orders", "metrics")
                ),
                "POLICY_SEARCH_FAILED_CHILD_INVALID",
            )
    return value
