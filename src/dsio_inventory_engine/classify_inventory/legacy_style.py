"""Development-only factor segmentation, not Legacy replay or an admission decision."""

from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext
from fractions import Fraction

from dsio_inventory_engine.classify_inventory.legacy_features import (
    LegacyFeatureProducer,
    LegacyFeatureSnapshot,
    _keys,
    _week_start,
)
from dsio_inventory_engine.classify_inventory.legacy_rules import (
    LegacyStyleRules,
    SealedLegacyDocument,
)
from dsio_inventory_engine.inventory_contracts.values import digest, require


class LegacySegmentationResult(SealedLegacyDocument):
    __slots__ = ()


class LegacyFactorSegmentation:
    method_id = "LEGACY_STYLE"
    implementation_version = "1.0.0"

    def __init__(self, rules: LegacyStyleRules, *, reference_calculator_build_hash: str):
        self.rules = rules
        self.producer = LegacyFeatureProducer(
            rules, reference_calculator_build_hash=reference_calculator_build_hash
        )

    def route_factors(
        self,
        *,
        rop_cover_numerator: str,
        rop_cover_denominator: str,
        physical_lead_time_days: int,
        unit_cost_usd: str,
    ) -> dict:
        """Pure routing primitive for hand-written boundary Golden, not a sealed input."""
        with localcontext(Context(prec=40, rounding=ROUND_HALF_EVEN)):
            numerator, denominator, price = map(
                Decimal, (rop_cover_numerator, rop_cover_denominator, unit_cost_usd)
            )
            require(
                numerator.is_finite()
                and denominator.is_finite()
                and price.is_finite()
                and numerator >= 0
                and denominator > 0
                and 0 < price <= Decimal("1e12")
                and type(physical_lead_time_days) is int
                and 0 <= physical_lead_time_days <= 3660,
                "LS_INVALID_FEATURE_VALUE",
            )
            factors = self.rules.to_dict()["factors"]
            r = sum(
                Fraction(numerator) >= Fraction(denominator) * Fraction(cut)
                for cut in factors["rop_cover_months"]["cut_values"]
            )
            lead = sum(
                physical_lead_time_days >= cut
                for cut in factors["physical_lead_time_days"]["cut_values"]
            )
            p = sum(price >= Decimal(cut) for cut in factors["unit_cost"]["cut_values"])
        score = r + lead - p
        return {
            "factor_group_id": f"LS1:R{r + 1}-L{lead + 1}-P{p + 1}",
            "factor_codes": [f"R{r + 1}", f"L{lead + 1}", f"P{p + 1}"],
            "score": score,
            "profile_id": "SMALL" if score <= 0 else "MEDIUM" if score <= 2 else "LARGE",
        }

    def classify(self, snapshot: LegacyFeatureSnapshot) -> LegacySegmentationResult:
        value = self.producer.verify(snapshot).to_dict()
        request = value["input"]
        context = request["context"]
        abc = {row["item_id"]: row for row in request["abc_result"]["assignments"]}
        decision_date = _week_start(context["decision_yyyyww"]).isoformat()
        exceptions = {
            row["item_id"]: row
            for row in request["item_exceptions"]
            if row["effective_from"] <= decision_date < row["effective_to"]
        }
        rows = []
        for feature in value["features"]:
            item_id = feature["item_id"]
            row = {
                "item_id": item_id,
                "abc_xyz_class": abc[item_id]["abc_xyz_class"],
                "abc_classification_status": abc[item_id]["status"],
                "status": "UNCLASSIFIED",
                "search_group_id": None,
                "factor_group_id": None,
                "factor_codes": None,
                "score": None,
                "profile_id": None,
                "range_source": None,
                "item_exception": None,
                "reason_codes": feature["reason_codes"],
                "primary_reason": feature["primary_reason"],
            }
            if feature["status"] == "READY":
                routed = self.route_factors(
                    **{
                        key: feature[key]
                        for key in (
                            "rop_cover_numerator",
                            "rop_cover_denominator",
                            "physical_lead_time_days",
                            "unit_cost_usd",
                        )
                    }
                )
                row.update(
                    routed,
                    status="ASSIGNED",
                    search_group_id=routed["factor_group_id"],
                    range_source="GROUP_PROFILE",
                )
                exception = exceptions.get(item_id)
                # An item range exception is not an ABC eligibility override.
                if exception is not None and abc[item_id]["status"] == "ASSIGNED":
                    row.update(
                        search_group_id="LS1:ITEM:"
                        + digest(
                            [context[key] for key in ("company_cd", "subs_cd", "site_cd")]
                            + [item_id]
                        ),
                        profile_id=exception["profile_id"],
                        range_source="ITEM_OVERRIDE",
                        item_exception=exception,
                    )
            rows.append(row)
        return LegacySegmentationResult.seal(
            {
                "contract_id": "inventory-legacy-style-segmentation",
                "contract_version": "1.0.0",
                "method_id": self.method_id,
                "implementation_version": self.implementation_version,
                "rules_profile_hash": self.rules.content_hash,
                "configuration_hash": digest(
                    {
                        "base_configuration_hash": context["base_configuration_hash"],
                        "rules_profile_hash": self.rules.content_hash,
                        "item_exceptions_hash": value["input_binding"]["item_exceptions_hash"],
                    }
                ),
                "feature_snapshot": value,
                "items": rows,
            }
        )

    def verify(self, result: LegacySegmentationResult) -> LegacySegmentationResult:
        value = result.to_dict()
        _keys(
            value,
            {
                "contract_id",
                "contract_version",
                "method_id",
                "implementation_version",
                "rules_profile_hash",
                "configuration_hash",
                "feature_snapshot",
                "items",
                "content_hash",
            },
        )
        require(value["rules_profile_hash"] == self.rules.content_hash, "LS_RULES_HASH_MISMATCH")
        rebuilt = self.classify(LegacyFeatureSnapshot.from_dict(value["feature_snapshot"]))
        require(rebuilt.content_hash == result.content_hash, "LS_SOURCE_BINDING_MISMATCH")
        return rebuilt
