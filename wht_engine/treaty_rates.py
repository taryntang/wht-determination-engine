"""
Treaty rate lookup — backed by a versioned reference table, NOT hardcoded
logic and NOT an LLM guess.

This is deliberate: the withholding-tax-foreign skill this engine is built
from is explicit that a treaty rate must be retrieved from the IRS's own
published tables (Table 1 for FDAP income), never assumed from memory or
a "typical" rate. Encoding that as a maintained, versioned data file
(rather than as if/else rate literals in the decision code) is what gives
this system an audit trail: every determination records the table version
that was in effect when the rate was applied, so "why did we withhold X%
on this vendor eight months ago" has a real answer.

*** THIS SAMPLE TABLE IS ILLUSTRATIVE / DEMO DATA ONLY. ***
Rates here approximate real treaty patterns for demo purposes but are NOT
guaranteed current or complete (e.g. direct-dividend ownership thresholds,
LOB provisions, and many footnote conditions from the real IRS Table 1 are
simplified or omitted — see the `footnote` column). Before this engine (or
any fork of it) is used for a real determination, the table must be
refreshed from the live IRS tables:
  Table 1 (FDAP rates):        https://www.irs.gov/pub/irs-lbi/tax-treaty-table-1.pdf
  Table 3 (treaties in force): https://www.irs.gov/pub/irs-lbi/table-3-list-of-tax-treaties.pdf
  Table 4 (LOB):               https://www.irs.gov/pub/irs-lbi/Tax_Treaty_Table_4.pdf
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Optional

from .models import PaymentType

_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "treaty_rates_table1_sample.csv"


@dataclass
class TreatyRateResult:
    found: bool
    rate: Optional[Decimal]
    treaty_article: Optional[str]
    footnote: Optional[str]
    table_version: str
    country: str
    payment_type: str


class TreatyRateTable:
    """Loads the versioned rate table once and serves lookups from memory."""

    def __init__(self, csv_path: Path = _DATA_PATH):
        self._csv_path = csv_path
        self._rows: dict[tuple[str, str], dict] = {}
        self._load()

    def _load(self) -> None:
        with open(self._csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                key = (row["country"].strip().lower(), row["payment_type"].strip().lower())
                self._rows[key] = row

    def lookup(self, country: str, payment_type: PaymentType) -> TreatyRateResult:
        key = (country.strip().lower(), payment_type.value.strip().lower())
        row = self._rows.get(key)
        if row is None:
            return TreatyRateResult(
                found=False,
                rate=None,
                treaty_article=None,
                footnote=None,
                table_version="SAMPLE-DEMO-v1",
                country=country,
                payment_type=payment_type.value,
            )
        return TreatyRateResult(
            found=True,
            rate=Decimal(row["rate_pct"]),
            treaty_article=row["treaty_article"] or None,
            footnote=(row["footnote"] or None),
            table_version=row["source_table_version"],
            country=country,
            payment_type=payment_type.value,
        )


# Module-level singleton for convenience in the engine / tests.
DEFAULT_TABLE = TreatyRateTable()
