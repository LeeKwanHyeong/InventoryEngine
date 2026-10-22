"""Compare the separate Python reference with actual C# captures, no timing."""

from dataclasses import fields
from datetime import date
import hashlib
import json
from pathlib import Path

from dsio_inventory_engine.simulate_inventory.application.legacy_numbers import (
    decimal_ceiling_of_double,
    dotnet_round,
)
from dsio_inventory_engine.simulate_inventory.application.legacy_reference import (
    LegacyBucket,
    LegacyDatedQuantity,
    LegacyLot,
    LegacyPolicy,
    LegacyPsiRequest,
    legacy_arrival_bucket,
    legacy_lead_time_days,
    run_legacy_reference,
)
from tests.support.legacy_reference_fixtures import (
    assert_native_numbers,
    columns,
    replay_request,
)

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "tests/fixtures"


def verify_extended(vectors, captured):
    assert vectors == json.loads((FIXTURES / "legacy_replay_vectors.json").read_text())
    assert (
        captured["input_sha256"]
        == hashlib.sha256((FIXTURES / "legacy_replay_vectors.json").read_bytes()).hexdigest()
    )
    assert captured["scope"] == "EXTRACTED_ORIGINAL_PSI_AND_DOTNET_NUMERIC_PRIMITIVES"
    assert captured["runtime"].startswith(".NET 8.")
    counts = {}
    for source, family in (
        ("replay_cases", "replays"),
        ("round_cases", "rounds"),
        ("decimal_ceiling_cases", "ceilings"),
        ("lot_cases", "lots"),
    ):
        indexed = {case["case"]: case for case in vectors[source]}
        assert len(indexed) == len(vectors[source])
        assert len(captured[family]) == len(indexed)
        assert {row["case"] for row in captured[family]} == set(indexed)
        for row in captured[family]:
            given, expected = indexed[row["case"]], row["actual"]
            if family == "replays":
                request = replay_request(given["input"])
                rows = run_legacy_reference(request)
                actual = columns(rows, legacy_lead_time_days(request.internal_lead_time))
                assert set(expected) == set(actual) - {"terminal_pending_po"}
                for key, value in expected.items():
                    assert_native_numbers(actual[key], value)
            elif family == "rounds":
                assert_native_numbers(
                    dotnet_round(float(given["value"]), given["digits"]), expected
                )
            elif family == "ceilings":
                assert_native_numbers(decimal_ceiling_of_double(float(given["value"])), expected)
            else:
                lot = LegacyLot(*(float(given[k]) for k in ("min", "max", "step")))
                assert_native_numbers(lot.size(float(given["raw"])), expected)
        counts[family] = len(indexed)
    return counts


def verify_original(pack, captured):
    assert (
        captured["input_sha256"]
        == hashlib.sha256(
            (FIXTURES / "golden_legacy_python_semantics.json").read_bytes()
        ).hexdigest()
    )
    assert captured["scope"] == "EXTRACTED_ORIGINAL_METHODS_NO_DB_NO_ORIGINAL_ENTRYPOINT"
    for family, count in (
        ("orders", 12),
        ("transitions", 4),
        ("lots", 4),
        ("arrivals", 4),
        ("policies", 2),
    ):
        assert len(captured[family]) == count
        if family == "orders":
            assert len({(row["case"], row["stock_policy"]) for row in captured[family]}) == count
        else:
            assert len({row["case"] for row in captured[family]}) == count
    buckets = tuple(
        LegacyBucket(
            row["yyyyww"],
            date.fromisoformat(row["start_date"]),
            date.fromisoformat(row["end_date"]),
            date(2026, 9, 1),
        )
        for row in pack["calendar"]
    )
    orders = {item["case"]: item["input"] for item in pack["order_cases"]}
    for row in captured["orders"]:
        given = orders[row["case"]]
        request = LegacyPsiRequest(
            buckets,
            demands=tuple(
                LegacyDatedQuantity(b.start, float(q))
                for b, q in zip(buckets, given["forecast"], strict=True)
            ),
            policies=(
                LegacyPolicy(
                    date(2026, 1, 1),
                    date(2026, 12, 31),
                    float(given["rop"]),
                    float(given["target"]),
                    1,
                    10,
                ),
            ),
            boh=float(given["boh"]),
            internal_lead_time=float(given["legacy_internal_lead_time"]),
            stock_policy=row["stock_policy"],
            lot=LegacyLot(0, 1000000, 1),
        )
        actual = columns(
            run_legacy_reference(request), legacy_lead_time_days(request.internal_lead_time)
        )
        for key, expected in row["actual"].items():
            assert_native_numbers(actual[key], expected)
    transitions = {row["case"]: row["input"] for row in pack["transition_cases"]}
    for row in captured["transitions"]:
        given = transitions[row["case"]]
        result = run_legacy_reference(
            LegacyPsiRequest(
                buckets[:1],
                boh=float(given["boh"]),
                backorder=float(given["backorder"]),
                pending_po=float(given["receipt"]),
                internal_lead_time=201600,
                demands=(
                    LegacyDatedQuantity(
                        buckets[0].start, float(given["confirmed"]) + float(given["forecast"])
                    ),
                ),
                receipts=(LegacyDatedQuantity(buckets[0].start, float(given["receipt"])),),
            )
        )[0]
        actual = {field.name: getattr(result, field.name) for field in fields(result)}
        actual["weekly_demand_allocation_rate"] = result.weekly_allocation_rate
        for key, expected in row["actual"].items():
            assert_native_numbers(actual[key], expected)
    lots = {row["case"]: row for row in pack["lot_cases"]}
    for row in captured["lots"]:
        given = lots[row["case"]]
        lot = LegacyLot(*(float(given[k]) for k in ("min", "max", "step")))
        assert_native_numbers(lot.size(float(given["raw"])), row["actual"])
    arrivals = {row["case"]: row for row in pack["arrival_cases"]}
    for row in captured["arrivals"]:
        from datetime import timedelta

        days = legacy_lead_time_days(float(arrivals[row["case"]]["legacy_internal_lead_time"]))
        due = buckets[0].start + timedelta(days=days)
        index = legacy_arrival_bucket(buckets, due)
        assert row["actual"] == {
            "lead_time_days": days,
            "due_date": due.isoformat(),
            "containing_bucket_start": buckets[index].start.isoformat()
            if index is not None
            else None,
        }
    for row in captured["policies"]:
        given = next(c for c in pack["policy_cases"] if c["case"] == row["case"])
        month = given["base_month"]
        bucket = LegacyBucket(
            "POLICY",
            date.fromisoformat(given["bucket_start"]),
            date.fromisoformat(given["bucket_end"]),
            date(int(month[:4]), int(month[4:]), 1) if month else None,
        )
        result = run_legacy_reference(
            LegacyPsiRequest(
                (bucket,),
                internal_lead_time=201600,
                policies=tuple(
                    LegacyPolicy(
                        date.fromisoformat(p["effective_from"]),
                        date.fromisoformat(p["legacy_end_inclusive"]),
                        float(p["rop"]),
                        0,
                    )
                    for p in pack["policy_schedule"]
                ),
            )
        )[0]
        assert_native_numbers(result.rop, row["actual"]["rop"])
        assert (bucket.base_month or bucket.start.replace(day=1)).strftime("%Y%m") == row["actual"][
            "base_month"
        ]
    trigger = pack["trigger_case"]
    result = run_legacy_reference(
        LegacyPsiRequest(
            buckets,
            boh=float(trigger["position"]),
            internal_lead_time=201600,
            policies=(
                LegacyPolicy(
                    date(2026, 1, 1),
                    date(2026, 12, 31),
                    float(trigger["rop"]),
                    float(trigger["target"]),
                ),
            ),
        )
    )
    assert (result[0].purchase_order > 0) == captured["trigger"]["actual"]["order_triggered"]
    return {
        **{
            name: len(captured[name])
            for name in ("orders", "transitions", "lots", "arrivals", "policies")
        },
        "trigger": 1,
    }


def verify():
    def load(name):
        return json.loads((FIXTURES / name).read_text())

    return {
        "original": verify_original(
            load("golden_legacy_python_semantics.json"), load("native_legacy_reference_output.json")
        ),
        "extended": verify_extended(
            load("legacy_replay_vectors.json"), load("native_legacy_replay_vectors_output.json")
        ),
        "comparison": "EXACT_BINARY64_AFTER_CSHARP_ROUNDTRIP_PARSE_NO_TOLERANCE",
        "full_legacy_engine_equivalence": False,
        "runtime_binding": False,
        "performance_measurement": False,
    }


if __name__ == "__main__":
    print(json.dumps(verify(), indent=2))
