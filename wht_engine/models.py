"""
Data model for the withholding-tax determination engine.

These types capture the fact pattern the "withholding-tax-foreign" analysis
needs to gather before it can classify a payment (see the skill's
"Before you start: gather the facts" checklist) and the structured result
of running that analysis, including an audit trail suitable for SOX-style
evidence (which rule fired, which citation, which rate-table version).

Scope note (inherited from the underlying tax analysis): this engine only
covers payments to FOREIGN payees. It does not implement backup withholding
under IRC 3406 (U.S. payees who fail to provide a valid W-9/TIN) or FATCA
(IRC 1471-1474). A payment that turns out to involve a U.S. payee, or a
foreign financial institution / NFFE that may need a FATCA analysis, is
flagged by the engine rather than silently mis-routed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Optional


class PayeeType(str, Enum):
    INDIVIDUAL = "individual"
    CORPORATION = "corporation"
    PARTNERSHIP = "partnership"
    TRUST = "trust"
    DISREGARDED_ENTITY = "disregarded_entity"
    GOVERNMENT = "government"


class PaymentType(str, Enum):
    DIVIDEND = "dividend"
    INTEREST = "interest"
    INTEREST_PORTFOLIO = "interest_portfolio"  # portfolio interest exception candidate
    INTEREST_BANK_DEPOSIT = "interest_bank_deposit"
    ROYALTY_PATENT = "royalty_patent"
    ROYALTY_COPYRIGHT = "royalty_copyright"
    ROYALTY_INDUSTRIAL_EQUIPMENT = "royalty_industrial_equipment"
    ROYALTY_KNOW_HOW = "royalty_know_how"
    ROYALTY_FILM_TV = "royalty_film_tv"
    RENT = "rent"
    COMPENSATION_SERVICES = "compensation_services"
    SCHOLARSHIP_FELLOWSHIP = "scholarship_fellowship"
    REAL_PROPERTY_SALE = "real_property_sale"
    PARTNERSHIP_DISTRIBUTIVE_SHARE_ECI = "partnership_distributive_share_eci"
    PARTNERSHIP_DISTRIBUTION = "partnership_distribution"
    PARTNERSHIP_INTEREST_SALE = "partnership_interest_sale"
    OTHER_FDAP = "other_fdap"


class IntermediaryType(str, Enum):
    NONE = "none"
    NQI = "nonqualified_intermediary"
    QI_PWR = "qualified_intermediary_primary_wh"
    QI_NON_PWR = "qualified_intermediary_non_primary_wh"
    WFP = "withholding_foreign_partnership"
    US_BRANCH_ELECTION = "us_branch_election"
    DOMESTIC_PARTNERSHIP = "domestic_partnership"
    FOREIGN_PARTNERSHIP = "foreign_partnership"


class DocForm(str, Enum):
    W8BEN = "W-8BEN"
    W8BEN_E = "W-8BEN-E"
    W8ECI = "W-8ECI"
    W8IMY = "W-8IMY"
    W8EXP = "W-8EXP"
    FORM_8233 = "Form 8233"
    NONE = "none_on_file"


# Which W-8 family is even eligible for which payee type. Used by the
# documentation validator (Step 5) to catch a mismatched form before it's
# relied on for a reduced rate.
VALID_FORMS_BY_PAYEE_TYPE = {
    PayeeType.INDIVIDUAL: {DocForm.W8BEN, DocForm.W8ECI, DocForm.FORM_8233, DocForm.NONE},
    PayeeType.CORPORATION: {DocForm.W8BEN_E, DocForm.W8ECI, DocForm.W8IMY, DocForm.NONE},
    PayeeType.PARTNERSHIP: {DocForm.W8BEN_E, DocForm.W8ECI, DocForm.W8IMY, DocForm.NONE},
    PayeeType.TRUST: {DocForm.W8BEN_E, DocForm.W8IMY, DocForm.NONE},
    PayeeType.DISREGARDED_ENTITY: {DocForm.W8BEN_E, DocForm.W8IMY, DocForm.NONE},
    PayeeType.GOVERNMENT: {DocForm.W8EXP, DocForm.NONE},
}


@dataclass
class Documentation:
    """What's on file for the payee, per Step 5 of the analysis."""

    form: DocForm = DocForm.NONE
    treaty_country_claimed: Optional[str] = None
    treaty_article: Optional[str] = None
    expiration_date: Optional[date] = None
    signed_date: Optional[date] = None

    def is_expired(self, as_of: date) -> bool:
        return self.expiration_date is not None and self.expiration_date < as_of

    def is_present(self) -> bool:
        return self.form != DocForm.NONE


@dataclass
class Payee:
    payee_id: str
    name: str
    payee_type: PayeeType
    country: str  # country of tax residence, as claimed/on file
    is_foreign: bool = True
    documentation: Documentation = field(default_factory=Documentation)
    intermediary: IntermediaryType = IntermediaryType.NONE
    # For partnership look-through / tiered structures, and for FATCA-relevant
    # entities. Left as free-form flags rather than fully modeled — the engine
    # flags these for manual review instead of guessing.
    is_financial_institution: bool = False
    is_tiered_partnership: bool = False


@dataclass
class RealPropertyFacts:
    is_usrpi: bool = False
    amount_realized: Optional[Decimal] = None
    seller_has_us_affidavit: bool = False
    corp_not_usrphc_affidavit: bool = False
    is_personal_residence_le_300k: bool = False
    is_qualified_foreign_pension_fund: bool = False


@dataclass
class PartnershipFacts:
    partner_type: Optional[PayeeType] = None  # for rate purposes: corp vs individual
    seller_certifies_non_foreign: bool = False
    net_gain_eci_pct_of_total: Optional[Decimal] = None  # for the <10% de minimis test
    partnership_is_foreign: bool = False


@dataclass
class Payment:
    payment_id: str
    payee: Payee
    payment_type: PaymentType
    gross_amount: Decimal
    is_us_source: bool = True
    is_eci: bool = False  # effectively connected with a US trade or business
    payment_date: date = field(default_factory=lambda: date(2026, 1, 1))
    real_property: Optional[RealPropertyFacts] = None
    partnership: Optional[PartnershipFacts] = None
    notes: str = ""


@dataclass
class AuditEvent:
    """One entry in the determination's audit trail."""

    rule: str
    detail: str
    citation: Optional[str] = None


@dataclass
class Determination:
    payment_id: str
    regime: str  # "FDAP" | "FIRPTA" | "PARTNERSHIP_ECI_1446" | "PARTNERSHIP_ECI_1446F"
    #             | "PAYROLL_NOT_FDAP" | "SCHOLARSHIP_871C" | "ECI_SELF_REPORTED"
    #             | "NEEDS_MANUAL_REVIEW" | "NOT_FOREIGN_OUT_OF_SCOPE"
    withholding_required: bool
    rate: Optional[Decimal]
    withholding_amount: Optional[Decimal]
    citation: str
    rationale: str
    documentation_required: list[DocForm] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)  # e.g. LOB, FATCA, review-needed
    audit_trail: list[AuditEvent] = field(default_factory=list)
    rate_table_version: Optional[str] = None
    confidence: str = "high"  # "high" | "needs_review"

    def add_event(self, rule: str, detail: str, citation: Optional[str] = None) -> None:
        self.audit_trail.append(AuditEvent(rule=rule, detail=detail, citation=citation))
