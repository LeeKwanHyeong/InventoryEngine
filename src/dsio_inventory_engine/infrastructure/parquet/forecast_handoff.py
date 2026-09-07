"""Read the Demand exchange contract without importing the Demand implementation.

All parts are verified before rows are exposed. Memory admission is bounded at
200,000 rows / 128 MB encoded data, not the legacy 8 MB JSON transport limit.
"""

import hashlib
from pathlib import Path

from dsio_inventory_engine.inventory_contracts.values import (
    canonical_json,
    decimal_string,
    digest,
    hash_value,
    identifier,
    read_json,
    require,
    yyyyww,
)

CONTRACT = "demand-io-forecast-export-v1"
FIELDS = (
    "item_id",
    "yyyyww",
    "uom",
    "forecast_qty",
    "result_id",
    "model_run_id",
    "selection_decision_id",
)
MAX_BYTES = 128_000_000
MAX_ROWS = 200_000


class ParquetForecastHandoffReader:
    def read(
        self, manifest_path: Path, *, snapshot_id: str, content_hash: str, manifest_hash: str
    ) -> dict:
        import polars as pl

        path = Path(manifest_path)
        require(not path.is_symlink() and path.is_file(), "UNSAFE_MANIFEST_PATH")
        require(path.stat().st_size <= 8_000_000, "MANIFEST_SIZE_LIMIT")
        encoded = path.read_bytes()
        require(
            hashlib.sha256(encoded).hexdigest() == hash_value(manifest_hash),
            "MANIFEST_HASH_MISMATCH",
        )
        manifest = read_json(encoded.decode())
        require(
            set(manifest)
            == {
                "contract_id",
                "status",
                "snapshot_id",
                "request",
                "row_count",
                "rows_hash",
                "content_hash",
                "parts",
            },
            "HANDOFF_SCHEMA",
        )
        require(
            manifest["contract_id"] == CONTRACT and manifest["status"] == "SEALED",
            "UNSEALED_FORECAST_EXPORT",
        )
        require(
            manifest["snapshot_id"] == identifier(snapshot_id)
            and manifest["content_hash"] == hash_value(content_hash),
            "FORECAST_EXPORT_BINDING",
        )
        request = manifest["request"]
        require(
            request["contract_id"] == CONTRACT
            and request["selector"]["forecast_stat_cd"] == "POINT",
            "POINT_REQUIRED",
        )
        require(snapshot_id == "FCST-" + digest(request), "EXPORT_IDENTITY")
        require(
            request["shared"]["month_rule"] == "SOURCE_WEEK_MONDAY_V1"
            and request["shared"]["quantity_rule"] == "PLANNING_6_PHYSICAL_EA_0_V1",
            "HANDOFF_SEMANTICS",
        )
        require(
            type(manifest["row_count"]) is int and 0 < manifest["row_count"] <= MAX_ROWS,
            "HANDOFF_ROW_LIMIT",
        )
        require(
            type(manifest["parts"]) is list and 0 < len(manifest["parts"]) <= MAX_ROWS,
            "HANDOFF_PART_LIMIT",
        )
        rows: list[dict] = []
        previous: tuple[str, str] | None = None
        result_ids: set[str] = set()
        selected: dict[str, dict] = {}
        logical, encoded_size, byte_size = hashlib.sha256(), 0, 0
        for index, part in enumerate(manifest["parts"]):
            require(
                set(part)
                == {"path", "byte_hash", "byte_count", "row_count", "first_key", "last_key"},
                "PART_SCHEMA",
            )
            require(part["path"] == f"part-{index:05d}.parquet", "PART_ORDER_OR_PATH")
            part_path = path.parent / part["path"]
            require(not part_path.is_symlink() and part_path.is_file(), "MISSING_OR_UNSAFE_PART")
            size = part_path.stat().st_size
            byte_size += size
            require(size == part["byte_count"] and byte_size <= MAX_BYTES, "PART_BYTE_COUNT")
            require(
                type(part["row_count"]) is int and 0 < part["row_count"] <= 20_000, "PART_ROW_LIMIT"
            )
            require(len(rows) + part["row_count"] <= MAX_ROWS, "HANDOFF_ROW_LIMIT")
            # Read bytes once: hashing and decoding must observe the same immutable bytes.
            payload = part_path.read_bytes()
            require(
                hashlib.sha256(payload).hexdigest() == hash_value(part["byte_hash"]),
                "PART_HASH_MISMATCH",
            )
            frame = pl.read_parquet(payload, n_rows=part["row_count"] + 1)
            require(
                set(frame.columns) == set(FIELDS) and all(t == pl.String for t in frame.dtypes),
                "PARQUET_SCHEMA",
            )
            require(frame.height == part["row_count"], "PART_ROW_COUNT")
            batch = frame.to_dicts()
            require(
                [batch[0]["item_id"], batch[0]["yyyyww"]] == part["first_key"]
                and [batch[-1]["item_id"], batch[-1]["yyyyww"]] == part["last_key"],
                "PART_KEY_RANGE",
            )
            for row in batch:
                for field in FIELDS:
                    (
                        decimal_string
                        if field == "forecast_qty"
                        else yyyyww
                        if field == "yyyyww"
                        else identifier
                    )(row[field])
                key = row["item_id"], row["yyyyww"]
                require(previous is None or previous < key, "DUPLICATE_OR_UNSORTED_FORECAST")
                require(row["result_id"] not in result_ids, "DUPLICATE_RESULT_ID")
                decision = {k: row[k] for k in ("item_id", "model_run_id", "selection_decision_id")}
                require(selected.setdefault(row["item_id"], decision) == decision, "MIXED_WINNER")
                previous = key
                result_ids.add(row["result_id"])
                line = (canonical_json(row) + "\n").encode()
                encoded_size += len(line)
                require(encoded_size <= MAX_BYTES, "HANDOFF_DECODED_SIZE_LIMIT")
                logical.update(line)
            rows.extend(batch)
        require(
            len(rows) == manifest["row_count"] and logical.hexdigest() == manifest["rows_hash"],
            "LOGICAL_FORECAST_HASH",
        )
        require(
            digest({"request": request, "rows_hash": manifest["rows_hash"], "row_count": len(rows)})
            == content_hash,
            "FORECAST_CONTENT_HASH",
        )
        require(
            digest(sorted(selected.values(), key=canonical_json))
            == request["selection_content_hash"],
            "WINNER_EVIDENCE_HASH",
        )
        return {"manifest": manifest, "rows": rows, "manifest_hash": manifest_hash}
