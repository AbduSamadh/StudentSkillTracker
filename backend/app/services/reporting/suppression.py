"""Reporting principles, enforced in code rather than left to report authors (spec §6.1).

1. Every figure carries its denominator.
2. Suppress below base: no percentage on fewer than 20 students; no cell on fewer than 5.
   Withheld figures are listed as withheld *with the reason* — never silently dropped.
3. Measured and inferred claims are different categories and never render identically.

All report code builds numbers through ``Figure`` so the rules cannot be bypassed.
"""

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from app.tenancy import MIN_BASE_FOR_CELL, MIN_BASE_FOR_PERCENT, TenantSettings

ClaimType = Literal["measured", "inferred"]


@dataclass
class Figure:
    label: str
    kind: Literal["percent", "count", "money", "number"]
    value: float | int | None
    numerator: float | int | None
    denominator: float | int | None
    denominator_label: str | None
    withheld: bool = False
    withheld_reason: str | None = None
    claim_type: ClaimType = "measured"
    unit: str | None = None

    # ---- constructors ----
    @classmethod
    def percent(
        cls,
        numerator: int,
        denominator: int,
        settings: TenantSettings | None,
        *,
        label: str,
        denominator_label: str = "students",
        apply_base_rule: bool = True,
        claim_type: ClaimType = "measured",
    ) -> "Figure":
        """``apply_base_rule`` is False only when the denominator is not people (e.g. the
        number of skills in the taxonomy), where disclosure rules do not apply."""
        min_base = max(
            settings.min_base_for_percent if settings else MIN_BASE_FOR_PERCENT, MIN_BASE_FOR_PERCENT
        )
        min_cell = max(settings.min_base_for_cell if settings else MIN_BASE_FOR_CELL, MIN_BASE_FOR_CELL)
        f = cls(
            label=label,
            kind="percent",
            value=None,
            numerator=numerator,
            denominator=denominator,
            denominator_label=denominator_label,
            claim_type=claim_type,
            unit="%",
        )
        if denominator <= 0:
            return f._withhold("there is no base to calculate from")
        if apply_base_rule and denominator < min_base:
            return f._withhold(
                f"the base of {denominator} {denominator_label} is below the minimum of {min_base} "
                "for publishing a percentage"
            )
        if apply_base_rule and 0 < numerator < min_cell:
            return f._withhold(f"fewer than {min_cell} {denominator_label} are in this group")
        if apply_base_rule and 0 < denominator - numerator < min_cell:
            return f._withhold(f"fewer than {min_cell} {denominator_label} are outside this group")
        f.value = int(
            (Decimal(numerator) * 100 / Decimal(denominator)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        )
        return f

    @classmethod
    def count(
        cls,
        n: int,
        settings: TenantSettings | None,
        *,
        label: str,
        denominator: int | None = None,
        denominator_label: str | None = None,
        apply_cell_rule: bool = True,
        claim_type: ClaimType = "measured",
    ) -> "Figure":
        """``apply_cell_rule`` is False only where the audience already sees the named
        individuals behind the count (a coach's own squad list), so a small count discloses
        nothing new. Percentages keep the base-of-20 rule everywhere."""
        min_cell = max(settings.min_base_for_cell if settings else MIN_BASE_FOR_CELL, MIN_BASE_FOR_CELL)
        f = cls(
            label=label,
            kind="count",
            value=n,
            numerator=n,
            denominator=denominator,
            denominator_label=denominator_label,
            claim_type=claim_type,
        )
        if apply_cell_rule and 0 < n < min_cell:
            f.value = None
            return f._withhold(f"fewer than {min_cell} students — the cell could identify individuals")
        return f

    @classmethod
    def money(
        cls,
        amount: Decimal | float | None,
        *,
        label: str,
        currency: str = "AED",
        denominator: Decimal | float | None = None,
        denominator_label: str | None = None,
    ) -> "Figure":
        return cls(
            label=label,
            kind="money",
            value=None if amount is None else float(amount),
            numerator=None if amount is None else float(amount),
            denominator=None if denominator is None else float(denominator),
            denominator_label=denominator_label,
            unit=currency,
        )

    @classmethod
    def number(
        cls,
        value: float | None,
        *,
        label: str,
        denominator: int | None,
        denominator_label: str,
        settings: TenantSettings | None = None,
        claim_type: ClaimType = "measured",
    ) -> "Figure":
        """A statistic over a group of students (e.g. mean index); withheld on a small base."""
        min_cell = max(settings.min_base_for_cell if settings else MIN_BASE_FOR_CELL, MIN_BASE_FOR_CELL)
        f = cls(
            label=label,
            kind="number",
            value=value,
            numerator=value,
            denominator=denominator,
            denominator_label=denominator_label,
            claim_type=claim_type,
        )
        if denominator is not None and denominator < min_cell:
            f.value = None
            return f._withhold(f"based on fewer than {min_cell} {denominator_label}")
        return f

    def _withhold(self, reason: str) -> "Figure":
        self.withheld, self.withheld_reason, self.value = True, reason, None
        return self

    # ---- rendering ----
    @property
    def display(self) -> str:
        if self.withheld:
            return "Withheld"
        if self.value is None:
            return "—"
        if self.kind == "percent":
            return f"{self.value}% ({self.numerator} of {self.denominator} {self.denominator_label})"
        if self.kind == "money":
            base = f"{self.unit} {self.value:,.0f}"
            if self.denominator is not None:
                base += f" of {self.unit} {self.denominator:,.0f} {self.denominator_label or ''}".rstrip()
            return base
        if self.kind == "count" and self.denominator is not None:
            return f"{self.value} of {self.denominator} {self.denominator_label or ''}".rstrip()
        if self.kind == "number":
            v = f"{self.value:.1f}" if isinstance(self.value, float) else str(self.value)
            return f"{v} (n = {self.denominator} {self.denominator_label})"
        return str(self.value)

    def as_dict(self) -> dict:
        return {
            "label": self.label,
            "kind": self.kind,
            "value": self.value,
            "numerator": None if self.withheld else self.numerator,
            "denominator": self.denominator,
            "denominator_label": self.denominator_label,
            "display": self.display,
            "withheld": self.withheld,
            "withheld_reason": self.withheld_reason,
            "claim_type": self.claim_type,
            "unit": self.unit,
        }


@dataclass
class ReportBody:
    """Collects sections and automatically lists every withheld figure with its reason."""

    title: str
    subtitle: str | None = None
    sections: list[dict] = field(default_factory=list)
    withheld: list[dict] = field(default_factory=list)

    def figure(self, f: Figure) -> dict:
        d = f.as_dict()
        if f.withheld:
            self.withheld.append({"label": f.label, "reason": f.withheld_reason})
        return d

    def as_dict(self) -> dict:
        return {
            "title": self.title,
            "subtitle": self.subtitle,
            "sections": self.sections,
            "withheld": self.withheld,
        }
