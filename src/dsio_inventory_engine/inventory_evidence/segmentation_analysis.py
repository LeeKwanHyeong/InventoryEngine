"""Original analysis Artifact -> exact two-table projection -> atomic append-only writer.

No DB discovery, forecast, PSI, Run Claim, policy publication or operational Gate
mutation. The Artifact store, trusted classifier and transaction adapter are injected.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import date
from decimal import Decimal, InvalidOperation

from dsio_inventory_engine.classify_inventory.legacy_style import LegacySegmentationResult
from dsio_inventory_engine.infrastructure.postgresql.classification_snapshot import (
    _validate_publishable_snapshot,
)
from dsio_inventory_engine.inventory_contracts.segmentation_storage import (
    HEADER_TABLE,
    ITEM_TABLE,
    PROJECTION_KEY,
    SOURCE_KEY,
    TABLE_COLUMNS,
    VERSION,
)
from dsio_inventory_engine.inventory_contracts.values import (
    InventoryInputError,
    canonical_json,
    digest,
    require,
)
from .contracts import ArtifactObject


def exact_number(value):
    """NUMERIC comparison without context-precision rounding (coverage may exceed 40 digits)."""
    if value is None:
        return None
    require(
        isinstance(value, (str, Decimal, int)) and not isinstance(value, bool),
        "SEGMENTATION_NUMERIC_INVALID",
    )
    try:
        number = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise InventoryInputError("SEGMENTATION_NUMERIC_INVALID") from exc
    require(number.is_finite(), "SEGMENTATION_NUMERIC_INVALID")
    result = format(number, "f")
    return "0" if number == 0 else result.rstrip("0").rstrip(".") if "." in result else result


def analysis_binding(result, classification_snapshot):
    """Construct an explicit Offline pin. This helper is not a Source approval service."""
    value = result.to_dict()
    feature = value["feature_snapshot"]
    context = feature["input"]["context"]
    return {
        "tenant_id": classification_snapshot["tenant_id"],
        "project_id": classification_snapshot["project_id"],
        "scope": dict(classification_snapshot["scope"]),
        "classification_snapshot_id": classification_snapshot["classification_snapshot_id"],
        "classification_content_hash": classification_snapshot["content_hash"],
        "config_hash": classification_snapshot["config_hash"],
        "analysis_content_hash": value["content_hash"],
        "feature_content_hash": feature["content_hash"],
        "universe_content_hash": digest(feature["input"]["universe"]),
        "context_hash": digest(context),
    }


def _seal(key, body, reference):
    return ArtifactObject.from_document(
        reference=reference,
        contract_key=key,
        document={**body, "content_hash": digest(body)},
    )


def _pointer(obj):
    return {
        "reference": obj.reference,
        "content_hash": obj.content_hash,
        "byte_content_hash": obj.byte_content_hash,
        "contract_key": obj.contract_key,
    }


def _column_shape(row, table):
    columns = TABLE_COLUMNS[table]
    require(set(row) == {name for name, _ in columns}, "SEGMENTATION_COLUMNS_MISMATCH")
    for name, kind in columns:
        value = row[name]
        if value is None:
            require(
                table == ITEM_TABLE
                and name
                not in {
                    "segmentation_analysis_id",
                    "classification_snapshot_id",
                    "item_id",
                    "feature_status",
                    "assignment_status",
                    "history_observation_count",
                    "reason_codes",
                    "row_content_hash",
                },
                "SEGMENTATION_REQUIRED_VALUE_MISSING",
            )
            continue
        match = re.fullmatch(r"(?:VAR)?CHAR\((\d+)\)", kind)
        if match:
            require(
                isinstance(value, str)
                and value == value.strip()
                and 1 <= len(value) <= int(match[1]),
                "SEGMENTATION_COLUMN_LENGTH_INVALID",
            )
            if kind == "CHAR(64)":
                require(
                    re.fullmatch(r"[0-9a-f]{64}", value) is not None, "SEGMENTATION_HASH_INVALID"
                )
        elif kind in {"BIGINT", "SMALLINT", "INTEGER"}:
            require(type(value) is int, "SEGMENTATION_INTEGER_INVALID")
        elif kind == "BOOLEAN":
            require(type(value) is bool, "SEGMENTATION_BOOLEAN_INVALID")


class SegmentationAnalysisProjector:
    def __init__(self, *, classifier):
        self.classifier = classifier

    def validate(self, result, snapshot, binding):
        result = self.classifier.verify(LegacySegmentationResult.from_dict(result.to_dict()))
        require(
            _validate_publishable_snapshot(snapshot) == "2.0.0",
            "SEGMENTATION_CLASSIFICATION_V2_REQUIRED",
        )
        require(
            binding == analysis_binding(result, snapshot), "SEGMENTATION_INPUT_BINDING_MISMATCH"
        )
        value = result.to_dict()
        source = value["feature_snapshot"]["input"]
        context = source["context"]
        require(
            context["classification_snapshot_id"] == snapshot["classification_snapshot_id"]
            and context["classification_content_hash"] == snapshot["content_hash"]
            and context["base_configuration_hash"] == snapshot["config_hash"]
            and all(
                context[k] == snapshot["scope"][k] for k in ("company_cd", "subs_cd", "site_cd")
            ),
            "SEGMENTATION_CLASSIFICATION_BINDING_MISMATCH",
        )
        w0 = date.fromisocalendar(
            int(context["decision_yyyyww"][:4]), int(context["decision_yyyyww"][4:]), 1
        )
        require(snapshot["window_end_exclusive"] == w0.isoformat(), "SEGMENTATION_W0_MISMATCH")
        original = {i["item_id"]: i for i in snapshot["items"]}
        assignments = source["abc_result"]["assignments"]
        require(
            source["abc_result"]["source_content_hash"] == snapshot["source_content_hash"]
            and source["abc_result"]["configuration_hash"] == snapshot["config_hash"]
            and {i["item_id"] for i in assignments} <= original.keys(),
            "SEGMENTATION_ORIGINAL_ITEMS_MISMATCH",
        )
        for item in assignments:
            old = original[item["item_id"]]
            require(
                item["abc_xyz_class"] == old["segment_key"]
                and item["status"]
                == ("ASSIGNED" if old["classification_status"] == "CLASSIFIED" else "UNCLASSIFIED")
                and item["reason_code"] == old["unclassified_reason_code"],
                "SEGMENTATION_ORIGINAL_ITEMS_MISMATCH",
            )
        return result

    def source(self, *, result, classification_snapshot, binding):
        self.validate(result, classification_snapshot, binding)
        root = f"artifact:inventory:segmentation:{binding['classification_snapshot_id']}:{binding['analysis_content_hash']}"
        return _seal(
            SOURCE_KEY,
            {
                "contract_id": "inventory-segmentation-analysis-source-v1",
                "contract_version": VERSION,
                "binding": binding,
                "classification_snapshot": classification_snapshot,
                "analysis": result.to_dict(),
            },
            root + ":source",
        )

    def project(self, source, *, binding):
        document = source.document()
        require(
            source.contract_key == SOURCE_KEY
            and set(document)
            == {
                "contract_id",
                "contract_version",
                "binding",
                "classification_snapshot",
                "analysis",
                "content_hash",
            }
            and document["contract_id"] == "inventory-segmentation-analysis-source-v1"
            and document["contract_version"] == VERSION
            and document["binding"] == binding,
            "SEGMENTATION_SOURCE_CONTRACT_MISMATCH",
        )
        result = self.validate(
            LegacySegmentationResult.from_dict(document["analysis"]),
            document["classification_snapshot"],
            binding,
        ).to_dict()
        feature = result["feature_snapshot"]
        request = feature["input"]
        context = request["context"]
        identity = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                "inventory-segmentation-analysis-v1:"
                + binding["classification_snapshot_id"]
                + ":"
                + result["content_hash"],
            )
        )
        features = {r["item_id"]: r for r in feature["features"]}
        items = []
        for row in sorted(result["items"], key=lambda r: r["item_id"]):
            f = features[row["item_id"]]
            stats, policy = f["reference_statistics"] or {}, f["reference_policy"] or {}
            codes = row["factor_codes"] or [None, None, None]
            valid = f["status"] == "READY"
            item = dict(
                segmentation_analysis_id=identity,
                classification_snapshot_id=binding["classification_snapshot_id"],
                item_id=row["item_id"],
                feature_status=f["status"],
                assignment_status=row["status"],
                history_observation_count=f["history_observation_count"],
                mean_weekly_demand_qty=stats.get("mean_weekly_demand"),
                weekly_demand_stddev_qty=stats.get("stddev"),
                physical_lead_time_days=f["physical_lead_time_days"] if valid else None,
                unit_cost_usd=f["unit_cost_usd"] if valid else None,
                reference_rop_qty=policy.get("rop_qty"),
                rop_cover_numerator=f["rop_cover_numerator"],
                rop_cover_denominator=f["rop_cover_denominator"],
                rop_cover_months=f["rop_cover_months"],
                rop_factor_code=codes[0],
                lead_factor_code=codes[1],
                price_factor_code=codes[2],
                factor_group_id=row["factor_group_id"],
                search_group_id=row["search_group_id"],
                routing_score=row["score"],
                candidate_profile_id=row["profile_id"],
                range_source=row["range_source"],
                item_exception=row["item_exception"],
                primary_reason_code=row["primary_reason"],
                reason_codes=row["reason_codes"],
                reference_evidence={
                    "statistics": stats,
                    "policy": policy,
                    "trace": f["reference_trace"],
                }
                if valid
                else None,
            )
            for name, kind in TABLE_COLUMNS[ITEM_TABLE]:
                if kind == "NUMERIC":
                    item[name] = exact_number(item[name])
            item["row_content_hash"] = digest(item)
            _column_shape(item, ITEM_TABLE)
            items.append(item)
        sources = {}
        for domain, raw in request["sources"].items():
            sources[domain] = (
                None
                if raw is None
                else {**{k: v for k, v in raw.items() if k != "rows"}, "content_hash": digest(raw)}
            )
        header = dict(
            segmentation_analysis_id=identity,
            classification_snapshot_id=binding["classification_snapshot_id"],
            classification_content_hash=binding["classification_content_hash"],
            analysis_method_id="LEGACY_STYLE",
            analysis_contract_version=VERSION,
            implementation_version=result["implementation_version"],
            rules_profile_id=self.classifier.rules.to_dict()["profile_id"],
            rules_profile_hash=result["rules_profile_hash"],
            feature_snapshot_id=feature["feature_snapshot_id"],
            feature_content_hash=feature["content_hash"],
            analysis_content_hash=result["content_hash"],
            reference_calculator_build_hash=context["reference_calculator_build_hash"],
            decision_yyyyww=context["decision_yyyyww"],
            history_start_yyyyww=request["calendar"][0]["yyyyww"],
            history_end_yyyyww=request["calendar"][-1]["yyyyww"],
            master_as_of_date=context["master_as_of_date"],
            master_snapshot_revision=context["master_snapshot_revision"],
            calendar_snapshot_id=context["calendar_snapshot_id"],
            input_cutoff=context["input_cutoff"],
            universe_content_hash=binding["universe_content_hash"],
            feature_source_bindings=sources,
            source_artifact_reference=source.reference,
            source_artifact_content_hash=source.content_hash,
            source_artifact_byte_hash=source.byte_content_hash,
            item_count=len(items),
            assigned_item_count=sum(r["assignment_status"] == "ASSIGNED" for r in items),
            unclassified_item_count=sum(r["assignment_status"] == "UNCLASSIFIED" for r in items),
            development_only=True,
            operational_approved=False,
        )
        header["projection_content_hash"] = digest({"header": header, "items": items})
        _column_shape(header, HEADER_TABLE)
        return {"header": header, "items": items}


class PersistSegmentationAnalysisUseCase:
    def __init__(self, *, projector, store):
        self.projector, self.store = projector, store

    def execute(self, *, result, classification_snapshot, binding):
        source = self.projector.source(
            result=result, classification_snapshot=classification_snapshot, binding=binding
        )
        tables = self.projector.project(source, binding=binding)
        manifest = _seal(
            PROJECTION_KEY,
            {
                "contract_id": "inventory-segmentation-analysis-projection-v1",
                "contract_version": VERSION,
                "binding": binding,
                "source": _pointer(source),
                "tables": tables,
            },
            source.reference.removesuffix(":source") + ":projection",
        )
        replayed = self.store.commit_artifacts((source, manifest))
        return {**_pointer(manifest), "replayed": replayed}


class VerifySegmentationAnalysisUseCase:
    def __init__(self, *, projector, store):
        self.projector, self.store = projector, store

    def execute(self, *, binding, reference, expected_hash):
        manifest = self.store.read_artifact(reference)
        require(
            manifest.contract_key == PROJECTION_KEY and manifest.content_hash == expected_hash,
            "SEGMENTATION_PROJECTION_HASH_MISMATCH",
        )
        body = manifest.document()
        require(
            set(body)
            == {"contract_id", "contract_version", "binding", "source", "tables", "content_hash"}
            and body["contract_id"] == "inventory-segmentation-analysis-projection-v1"
            and body["contract_version"] == VERSION
            and body["binding"] == binding,
            "SEGMENTATION_PROJECTION_CONTRACT_MISMATCH",
        )
        source = self.store.read_artifact(body["source"]["reference"])
        require(_pointer(source) == body["source"], "SEGMENTATION_SOURCE_POINTER_MISMATCH")
        expected = self.projector.project(source, binding=binding)
        require(body["tables"] == expected, "SEGMENTATION_PROJECTION_REPLAY_MISMATCH")
        return expected


class WriteSegmentationAnalysisUseCase:
    def __init__(self, *, verifier, unit_of_work):
        self.verifier, self.unit_of_work = verifier, unit_of_work

    async def execute(self, *, binding, reference, expected_hash):
        # A fresh defensive JSON value prevents mutation of an in-flight binding.
        binding = json.loads(canonical_json(binding))
        tables = self.verifier.execute(
            binding=binding, reference=reference, expected_hash=expected_hash
        )
        header = tables["header"]
        async with self.unit_of_work.transaction(binding) as tx:
            authority = await tx.read_authority([r["item_id"] for r in tables["items"]])
            require(
                authority
                == {
                    k: binding[k]
                    for k in (
                        "tenant_id",
                        "project_id",
                        "scope",
                        "classification_snapshot_id",
                        "classification_content_hash",
                        "config_hash",
                    )
                },
                "SEGMENTATION_DATABASE_BINDING_MISMATCH",
            )
            old = await tx.read_header(header)
            rows = await tx.read_items(header["segmentation_analysis_id"])
            replayed = old is not None
            if replayed:
                require(
                    old == header and sorted(rows, key=lambda r: r["item_id"]) == tables["items"],
                    "SEGMENTATION_PERSISTED_CONFLICT",
                )
            else:
                require(not rows, "SEGMENTATION_PARTIAL_STATE")
                await tx.insert_rows(HEADER_TABLE, [header])
                for start in range(0, len(tables["items"]), 500):
                    await tx.insert_rows(ITEM_TABLE, tables["items"][start : start + 500])
                require(
                    await tx.read_header(header) == header
                    and sorted(
                        await tx.read_items(header["segmentation_analysis_id"]),
                        key=lambda r: r["item_id"],
                    )
                    == tables["items"],
                    "SEGMENTATION_WRITE_READBACK_MISMATCH",
                )
        return {
            "segmentation_analysis_id": header["segmentation_analysis_id"],
            "projection_content_hash": header["projection_content_hash"],
            "item_count": header["item_count"],
            "replayed": replayed,
            "operational_approved": False,
            "automatic_publish_allowed": False,
            "automatic_order_allowed": False,
        }
