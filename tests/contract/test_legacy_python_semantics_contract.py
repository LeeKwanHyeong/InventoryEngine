"""The manual comparison pack is evidence, never a new runtime or native C# verdict."""

import copy
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = json.loads((ROOT / "tests/fixtures/golden_legacy_python_semantics.json").read_text())
FAMILIES = (
    "transition_cases",
    "order_cases",
    "lot_cases",
    "arrival_cases",
    "policy_cases",
    "selection_cases",
)


def assert_manual_pack(value):
    assert value["spec_version"] == "1.0.0"
    assert value["legacy_evidence"] == "MANUAL_FROM_FROZEN_CSHARP_SOURCE"
    assert value["native_csharp_verified"] is False
    assert value["python_calculation_changed"] is False
    assert value["uom"] == "EA"
    assert value["comparison_policy"] == {
        "integer_quantities": "EXACT",
        "dates_flags_and_case_membership": "EXACT",
        "general_csharp_floating_tolerance": None,
        "upstream_svc_lv_mapping_verified": False,
        "legacy_style_is_psi_replay": False,
        "legacy_values_may_be_regenerated_from_python": False,
        "operational_approval_granted": False,
    }
    cases = [row for family in FAMILIES for row in value[family]] + [value["trigger_case"]]
    assert len(cases) == 21
    assert len({row["case"] for row in cases}) == len(cases)
    assert set(value["trigger_case"]["legacy_policy_codes"]) == {"OUTL", "ROPQ", "MINQ", "SFST"}
    assert all(row["legacy_final_selected_label"] is None for row in value["selection_cases"])
    assert value["deferred_not_inferred"]
    for row in value["order_cases"]:
        assert all(
            len(row["legacy_expected"][key]) == 3
            for key in (
                "boh",
                "due_in",
                "eoh",
                "backorder_close",
                "purchase_order",
                "pending_po_close",
            )
        )
        assert len(row["python_expected"]["eoh"]) == 3


def test_handwritten_pack_preserves_scope_and_native_unverified_status():
    assert_manual_pack(GOLDEN)


@pytest.mark.parametrize(
    "path,value",
    [
        (("native_csharp_verified",), True),
        (("python_calculation_changed",), True),
        (("comparison_policy", "integer_quantities"), "APPROXIMATE"),
        (("comparison_policy", "operational_approval_granted"), True),
        (("comparison_policy", "legacy_style_is_psi_replay"), True),
        (("comparison_policy", "legacy_values_may_be_regenerated_from_python"), True),
        (("comparison_policy", "general_csharp_floating_tolerance"), "0.01"),
        (("comparison_policy", "upstream_svc_lv_mapping_verified"), True),
    ],
)
def test_manual_fixture_cannot_silently_claim_runtime_or_native_approval(path, value):
    changed = copy.deepcopy(GOLDEN)
    target = changed
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(AssertionError):
        assert_manual_pack(changed)


def test_pack_uses_existing_source_register_without_a_new_run_identity():
    evidence = json.loads((ROOT / GOLDEN["source_evidence"]).read_text())
    assert evidence["legacy_source_acquired"] is True
    assert evidence["native_csharp_executed"] is False
    assert len(evidence["archive_equal_core_files"]) == 4
    assert (
        not {
            "engine_run_id",
            "order_plan_id",
            "binding_id",
            "content_hash",
            "automatic_publish_allowed",
            "automatic_order_allowed",
        }
        & GOLDEN.keys()
    )


def test_legacy_policy_names_do_not_substitute_current_policy_kinds():
    first, second = GOLDEN["order_cases"][:2]
    assert first["input"]["kind"] == "PERIODIC_ORDER_UP_TO"
    assert second["input"]["kind"] == "ROP_FIXED_Q"
    assert first["legacy_expected"] == second["legacy_expected"]
    assert first["python_expected"] != second["python_expected"]


def test_duplicate_case_or_missing_terminal_expectation_is_rejected():
    changed = copy.deepcopy(GOLDEN)
    changed["lot_cases"][1]["case"] = changed["lot_cases"][0]["case"]
    with pytest.raises(AssertionError):
        assert_manual_pack(changed)
    changed = copy.deepcopy(GOLDEN)
    changed["order_cases"][0]["legacy_expected"]["pending_po_close"].pop()
    with pytest.raises(AssertionError):
        assert_manual_pack(changed)
