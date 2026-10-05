"""Weighted performance index (spec §5.3).

    Index = 100 · (0.72 · (F − P)/(F − 1) + 0.28 · min(log10 F / log10 300, 1)) · T

P placement, F field size, T the tier weight (configurable per school). Placement alone is
not comparable across events, so a missing field size yields no index and a data-quality
flag instead of a silent default.
"""

import math
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

PLACEMENT_WEIGHT = 0.72
FIELD_WEIGHT = 0.28
REFERENCE_FIELD = 300

FLAG_MISSING_FIELD_SIZE = "missing_field_size"
FLAG_MISSING_PLACEMENT = "missing_placement"
FLAG_PLACEMENT_EXCEEDS_FIELD = "placement_exceeds_field_size"
FLAG_FIELD_SIZE_DEFAULTED_FROM_EXPECTED = "field_size_from_expected"


@dataclass
class IndexResult:
    value: Decimal | None
    flags: list[str] = field(default_factory=list)
    components: dict = field(default_factory=dict)


def performance_index(placement: int | None, field_size: int | None, tier_weight: Decimal) -> IndexResult:
    flags: list[str] = []
    if field_size is None or field_size < 1:
        flags.append(FLAG_MISSING_FIELD_SIZE)
    if placement is None or placement < 1:
        flags.append(FLAG_MISSING_PLACEMENT)
    if flags:
        return IndexResult(value=None, flags=flags)
    assert placement is not None and field_size is not None
    if placement > field_size:
        return IndexResult(value=None, flags=[FLAG_PLACEMENT_EXCEEDS_FIELD])

    placement_score = 1.0 if field_size == 1 else (field_size - placement) / (field_size - 1)
    field_score = min(math.log10(field_size) / math.log10(REFERENCE_FIELD), 1.0)
    raw = 100 * (PLACEMENT_WEIGHT * placement_score + FIELD_WEIGHT * field_score) * float(tier_weight)
    value = Decimal(str(raw)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return IndexResult(
        value=value,
        flags=[],
        components={
            "placement": placement,
            "field_size": field_size,
            "placement_score": round(placement_score, 4),
            "field_score": round(field_score, 4),
            "tier_weight": float(tier_weight),
            "formula": "100 · (0.72 · (F−P)/(F−1) + 0.28 · min(log10 F / log10 300, 1)) · T",
        },
    )
