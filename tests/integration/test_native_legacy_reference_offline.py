"""Replay actual captured C# outputs without requiring Docker in normal CI."""

import copy
import json

import pytest

from tests.reference.legacy_csharp.compare_reference import CAPTURE, PACK, compare, verify_native
from tests.reference.legacy_csharp.prepare_legacy_reference import FRAGMENTS, ROOT, stage


def test_actual_native_capture_and_current_python_validate_the_same_golden():
    result = compare()
    assert result["native_case_count"] == 20
    assert result["native_execution_count"] == 29
    assert result["python_case_count"] == 21
    assert result["golden_mismatches"] == []
    assert result["legacy_full_engine_equivalence_verified"] is False
    assert result["production_calculation_changed"] is False
    assert result["database_writes"] is False
    cases = {row["case"]: row for row in result["comparisons"]}
    assert cases["receipts_then_previous_bo_then_current_demand"]["status"] == "SCOPED_FIELDS_MATCH"
    assert (
        cases["forecast_shortage_is_not_customer_backorder"]["status"]
        == "DOCUMENTED_SEMANTIC_DIFFERENCE"
    )
    assert cases["missing_cost_is_not_zero_cost"]["status"] == "NO_COMPARABLE_LEGACY_CONTRACT"
    assert cases["weekly_periodic_target"]["legacy_actual"]["purchase_order"] == ["23", "0", "20"]
    assert cases["weekly_periodic_target"]["python_actual"]["orders"][0][-1] == "13"


@pytest.mark.parametrize(
    "mutation",
    ["quantity", "missing_case", "duplicate_policy", "stale_input", "invented_cost", "winner"],
)
def test_changed_native_capture_is_not_accepted_as_golden_proof(mutation):
    pack, native = json.loads(PACK.read_text()), json.loads(CAPTURE.read_text())
    changed = copy.deepcopy(native)
    if mutation == "quantity":
        changed["orders"][0]["actual"]["purchase_order"][0] = "13"
    elif mutation == "missing_case":
        changed["lots"].pop()
    elif mutation == "duplicate_policy":
        changed["orders"][1]["stock_policy"] = changed["orders"][0]["stock_policy"]
    elif mutation == "stale_input":
        changed["input_sha256"] = "0" * 64
    elif mutation == "invented_cost":
        changed["unsupported"] = []
    else:
        changed["selections"][0]["actual"]["final_selected_label"] = "A"
    with pytest.raises(AssertionError):
        verify_native(pack, changed)


def test_unchanged_source_fingerprints_are_required_before_staging(tmp_path):
    source = tmp_path / "source"
    for relative, _, _ in FRAGMENTS.values():
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("// altered or unavailable original source\n")
    output = tmp_path / "output"
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        stage(source, output)
    assert not output.exists()


def test_stager_does_not_overwrite_directories_or_generate_inside_repository(tmp_path):
    with pytest.raises(ValueError, match="fresh staging"):
        stage(tmp_path, tmp_path)
    target = ROOT / "tests/reference/legacy_csharp/not-a-generation-target"
    with pytest.raises(ValueError, match="fresh staging"):
        stage(tmp_path, target)
    assert not target.exists()
    with pytest.raises(ValueError, match="fresh staging"):
        stage(tmp_path, tmp_path / "do-not-generate-in-original-source")
