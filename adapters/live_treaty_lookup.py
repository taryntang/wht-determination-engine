"""
Live fallback for treaty-rate lookups: when the static, versioned sample
table (wht_engine/treaty_rates.py) has no row for a given country/income-
type pair, this fetches the real IRS treaty tables and asks Claude to
read them -- closer to what the withholding-tax-foreign skill this
engine is built from actually specifies ("always retrieve [the rate]
live from the IRS's own tables... never assume a typical rate"), which
the static CSV approximates for audit-trail/reproducibility reasons
rather than implements.

Deliberately kept OUTSIDE wht_engine/: the engine's core determination
logic has no model in the loop by design (see wht_engine/engine.py's
docstring) -- this is the LLM-and-network-assisted layer that feeds it
a TreatyRateResult, the same shape the static table produces, so
fdap.py doesn't know or care which source it came from.

Every result is stamped with the date it was fetched (not a fixed
"table version" name, since there isn't one -- it's live) and still
never verifies Table 4 (Limitation on Benefits); fdap.py's existing
CONFIRM_LOB_TABLE_4 flag on every treaty-rate result covers that
regardless of which source produced the rate.

NO RATE LIMIT (deliberate, explicit choice -- 2026-09-07): each live
lookup is a real, billable Claude API call, and VendorHub's public
intake form is only rate-limited at 30 inserts/5min per table, not per
treaty-lookup-miss -- so a burst of submissions using countries outside
the static sample can trigger real API spend with no cap here. An
earlier version of this file capped it at 2/hour via a persistent
Supabase ledger; that cap was removed by explicit user request after
being told this exact risk. If cost becomes a real problem, the ledger
table (live_treaty_lookup_calls) still exists in VendorHub's migrations
and can be reintroduced.
"""

from __future__ import annotations

import base64
import json
import re
import urllib.request
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Optional

from wht_engine.models import PaymentType
from wht_engine.treaty_rates import TreatyRateResult

TABLE_1_URL = "https://www.irs.gov/pub/irs-lbi/tax-treaty-table-1.pdf"
TABLE_3_URL = "https://www.irs.gov/pub/irs-lbi/table-3-list-of-tax-treaties.pdf"

# Table 1's columns are keyed to specific income sub-types -- matching the
# skill's own instruction to match the specific column, not just "royalty"
# generically (patents vs. copyrights are frequently different rates).
_INCOME_TYPE_DESCRIPTIONS = {
    PaymentType.DIVIDEND: "dividends (the general portfolio rate column, not the direct-dividend/ownership-threshold column)",
    PaymentType.INTEREST: "interest",
    PaymentType.ROYALTY_PATENT: "royalties -- patents",
    PaymentType.ROYALTY_COPYRIGHT: "royalties -- copyrights (not industrial equipment, know-how, or film/TV)",
    PaymentType.ROYALTY_INDUSTRIAL_EQUIPMENT: "royalties -- industrial equipment rental",
    PaymentType.ROYALTY_KNOW_HOW: "royalties -- know-how / other industrial royalties",
    PaymentType.ROYALTY_FILM_TV: "royalties -- motion picture and television",
    PaymentType.RENT: "rents",
}


def _download(url: str) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url), timeout=30) as resp:
        return resp.read()


def _not_found(country: str, payment_type: PaymentType, table_version: str, footnote: Optional[str] = None) -> TreatyRateResult:
    return TreatyRateResult(
        found=False, rate=None, treaty_article=None, footnote=footnote,
        table_version=table_version, country=country, payment_type=payment_type.value,
    )


def fetch_live_treaty_rate(
    country: str,
    payment_type: PaymentType,
    anthropic_api_key: str,
    as_of: Optional[date] = None,
) -> TreatyRateResult:
    """Downloads the real IRS Table 1 + Table 3 PDFs and asks Claude to
    find the specific country/income-type cell, per the process the
    withholding-tax-foreign skill specifies. Returns a TreatyRateResult
    with found=False (not an exception) when Claude can't confirm the
    treaty is in force or can't locate the specific income type -- this
    is a lookup, not a guess, so "not found" is a legitimate answer, not
    a failure.
    """
    as_of = as_of or date.today()
    table_version = f"IRS-LIVE-{as_of.isoformat()}"

    income_desc = _INCOME_TYPE_DESCRIPTIONS.get(payment_type)
    if income_desc is None:
        return _not_found(country, payment_type, table_version, "Income type has no Table 1 column mapping.")

    table1_bytes = _download(TABLE_1_URL)
    table3_bytes = _download(TABLE_3_URL)

    prompt = (
        "Attached are the IRS's official Tax Treaty Table 1 (FDAP withholding "
        "rates by country and income type) and Table 3 (list of tax treaties "
        "currently in force). I need the withholding rate for:\n"
        f"  Country: {country}\n"
        f"  Income type: {income_desc}\n\n"
        "Steps:\n"
        "1. In Table 3, confirm a treaty with this country is currently in force.\n"
        "2. In Table 1, find this country's row and the specific column for "
        "this income type -- match the specific sub-type exactly (e.g. patents "
        "vs. copyrights are frequently different rates in the same treaty).\n"
        "3. Read any footnote letter(s) attached to that cell and look up their full text.\n\n"
        "Respond with strict JSON only, no prose before or after:\n"
        "{\n"
        '  "treaty_in_force": true or false,\n'
        '  "found_in_table1": true or false,\n'
        '  "rate_pct": number or null,\n'
        '  "treaty_article": string or null,\n'
        '  "footnote_text": string or null (the full text of any footnote(s) '
        "attached to the cell, not just the letter),\n"
        '  "notes": string explaining anything uncertain, illegible, or ambiguous\n'
        "}\n"
        "If the country has no treaty in force, or isn't listed for this "
        "specific income type, say so plainly (found_in_table1 or "
        "treaty_in_force = false) rather than guessing a nearby value."
    )

    body = {
        "model": "claude-sonnet-5",
        "max_tokens": 1024,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {"type": "base64", "media_type": "application/pdf", "data": base64.b64encode(table1_bytes).decode("ascii")},
                    },
                    {
                        "type": "document",
                        "source": {"type": "base64", "media_type": "application/pdf", "data": base64.b64encode(table3_bytes).decode("ascii")},
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ],
    }
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        method="POST",
        headers={
            "x-api-key": anthropic_api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        data=json.dumps(body).encode("utf-8"),
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        result = json.loads(resp.read())

    text = "".join(b.get("text", "") for b in result.get("content", []) if b.get("type") == "text")
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return _not_found(country, payment_type, table_version, "Claude's response wasn't parseable JSON.")

    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return _not_found(country, payment_type, table_version, "Claude's response wasn't valid JSON.")

    if not parsed.get("treaty_in_force") or not parsed.get("found_in_table1") or parsed.get("rate_pct") is None:
        print(
            f"live_treaty_lookup: not found for {country}/{payment_type.value} -- "
            f"treaty_in_force={parsed.get('treaty_in_force')} found_in_table1={parsed.get('found_in_table1')} "
            f"rate_pct={parsed.get('rate_pct')!r} notes={parsed.get('notes')!r}"
        )
        return _not_found(country, payment_type, table_version, parsed.get("notes"))

    try:
        rate = Decimal(str(parsed["rate_pct"]))
    except (InvalidOperation, TypeError):
        return _not_found(country, payment_type, table_version, parsed.get("notes"))

    return TreatyRateResult(
        found=True,
        rate=rate,
        treaty_article=parsed.get("treaty_article"),
        footnote=parsed.get("footnote_text") or parsed.get("notes"),
        table_version=table_version,
        country=country,
        payment_type=payment_type.value,
    )


class StaticThenLiveTreatyTable:
    """Same .lookup() interface as wht_engine.treaty_rates.TreatyRateTable
    (duck-typed -- fdap.py just calls .lookup(), doesn't care which this
    is), so it's a drop-in treaty_table argument to determine_withholding().

    Tries the static CSV first (fast, free, gives every determination a
    stable version stamp when it has the row); falls back to a live
    IRS+Claude lookup only on a miss, and only if an API key was supplied.
    No rate limit -- see the module docstring. A live-lookup failure
    (network, API, unparseable response) falls back to the static table's
    own "not found" result rather than raising -- a treaty-rate lookup
    failing should degrade to the existing statutory-default path, not
    break the determination.
    """

    def __init__(self, static_table, anthropic_api_key: Optional[str]):
        self._static_table = static_table
        self._anthropic_api_key = anthropic_api_key

    def lookup(self, country: str, payment_type: PaymentType) -> TreatyRateResult:
        result = self._static_table.lookup(country, payment_type)
        if result.found or not self._anthropic_api_key:
            return result
        try:
            return fetch_live_treaty_rate(country, payment_type, self._anthropic_api_key)
        except Exception as e:
            print(f"live_treaty_lookup: live fetch raised for {country}/{payment_type.value}, falling back to static result: {e}")
            return result
