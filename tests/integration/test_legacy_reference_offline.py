from dataclasses import FrozenInstanceError, replace
import copy
import hashlib
import json
from pathlib import Path

import pytest

from dsio_inventory_engine.simulate_inventory.application.legacy_reference import (
    run_legacy_reference,
)
from tests.reference.legacy_csharp.verify_python_replay import verify, verify_extended
from tests.support.legacy_reference_fixtures import replay_request

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
VECTORS = json.loads((FIXTURES / "legacy_replay_vectors.json").read_text())
CAPTURE = json.loads((FIXTURES / "native_legacy_replay_vectors_output.json").read_text())


def test_actual_original_and_extended_csharp_outputs_match_python_reference():
    report = verify()
    assert sum(report["original"].values()) == 27
    assert report["extended"] == {"replays": 27, "rounds": 16, "ceilings": 14, "lots": 8}
    assert report["runtime_binding"] is report["performance_measurement"] is False


@pytest.mark.parametrize("case", VECTORS["replay_cases"], ids=lambda row: row["case"])
def test_candidate_and_inventory_state_are_not_shared_between_calls(case):
    given = copy.deepcopy(case["input"])
    request = replay_request(given)
    first = run_legacy_reference(request)
    run_legacy_reference(replace(request, boh=request.boh + 3))
    assert run_legacy_reference(request) == first
    assert given == case["input"]
    with pytest.raises(FrozenInstanceError):
        first[0].eoh = 123


@pytest.mark.parametrize(
    "mutation", ["quantity", "missing", "duplicate", "input", "date", "rounding"]
)
def test_changed_capture_cannot_silently_pass_equivalence(mutation):
    changed = copy.deepcopy(CAPTURE)
    if mutation == "quantity":
        changed["replays"][0]["actual"]["purchase_order"][0] = "13"
    elif mutation == "missing":
        changed["replays"].pop()
    elif mutation == "duplicate":
        changed["replays"][-1] = changed["replays"][0]
    elif mutation == "input":
        changed["input_sha256"] = "0" * 64
    elif mutation == "date":
        changed["replays"][0]["actual"]["due_dates"][0] = "2026-10-12"
    else:
        changed["rounds"][0]["actual"] = "2.67"
    with pytest.raises(AssertionError):
        verify_extended(VECTORS, changed)


def test_legacy_manual_golden_and_earlier_native_evidence_are_not_rewritten():
    expected = {
        "golden_legacy_python_semantics.json": "309f86c8c035cde337160227cd6c93b895a023dead4d5995024c9afefdfa6046",
        "native_legacy_reference_output.json": "0427950c32527b5d53a75572f08964ac14b6aa085f9b07e47f2e0df7a8050f5d",
    }
    for name, digest in expected.items():
        assert hashlib.sha256((FIXTURES / name).read_bytes()).hexdigest() == digest


def test_input_object_must_be_the_exact_captured_source():
    changed = copy.deepcopy(VECTORS)
    changed["replay_cases"][0]["input"]["policies"][0]["target"] = "24"
    with pytest.raises(AssertionError):
        verify_extended(changed, CAPTURE)
