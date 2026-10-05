from dataclasses import dataclass


SEC_SOURCE = "sec_edgar"

# Single-quarter facts are about 13 weeks. Year-to-date Q2/Q3 facts are
# ~180 and ~270 days and must not be stored as quarterly results.
QUARTER_DAYS = (70, 120)
# 52/53-week fiscal years land around 363-371 days.
ANNUAL_DAYS = (300, 380)

ANNUAL_FORMS = frozenset({"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"})
QUARTERLY_FORMS = frozenset({"10-Q", "10-Q/A"})
ALLOWED_FORMS = ANNUAL_FORMS | QUARTERLY_FORMS

# Revenue is the anchor used to decide which column of a filing is the
# current period. Net income is the fallback when revenue is untagged.
ANCHOR_CONCEPTS = (
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "SalesRevenueNet",
    "NetIncomeLoss",
)


@dataclass(frozen=True)
class ConceptSpec:
    """XBRL concepts for one Sentinel field, in priority order.

    The first concept that has a fact for the fiscal period wins.
    Later concepts are fallbacks, not values to add together.
    """

    field: str
    concepts: tuple[str, ...]
    namespaces: tuple[str, ...]
    unit: str
    kind: str  # "duration" or "instant"


DURATION_METRICS: tuple[ConceptSpec, ...] = (
    ConceptSpec(
        "revenue",
        (
            "Revenues",
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "SalesRevenueNet",
        ),
        ("us-gaap",),
        "USD",
        "duration",
    ),
    ConceptSpec("gross_profit", ("GrossProfit",), ("us-gaap",), "USD", "duration"),
    ConceptSpec("operating_income", ("OperatingIncomeLoss",), ("us-gaap",), "USD", "duration"),
    ConceptSpec("net_income", ("NetIncomeLoss",), ("us-gaap",), "USD", "duration"),
    ConceptSpec(
        "eps_basic",
        ("EarningsPerShareBasic",),
        ("us-gaap",),
        "USD/shares",
        "duration",
    ),
    ConceptSpec(
        "eps_diluted",
        ("EarningsPerShareDiluted",),
        ("us-gaap",),
        "USD/shares",
        "duration",
    ),
    ConceptSpec(
        "operating_cash_flow",
        ("NetCashProvidedByUsedInOperatingActivities",),
        ("us-gaap",),
        "USD",
        "duration",
    ),
    ConceptSpec(
        "capital_expenditure",
        (
            "PaymentsToAcquirePropertyPlantAndEquipment",
            "PaymentsForAdditionsToPropertyPlantAndEquipment",
            # Several issuers, including NVIDIA, stopped tagging cash capex with
            # the PP&E payment concepts and use this broader productive-assets tag.
            # It can include intangible assets. The parser records when it is used.
            "PaymentsToAcquireProductiveAssets",
        ),
        ("us-gaap",),
        "USD",
        "duration",
    ),
)

INSTANT_METRICS: tuple[ConceptSpec, ...] = (
    ConceptSpec(
        "cash_and_equivalents",
        ("CashAndCashEquivalentsAtCarryingValue",),
        ("us-gaap",),
        "USD",
        "instant",
    ),
    ConceptSpec("total_assets", ("Assets",), ("us-gaap",), "USD", "instant"),
    ConceptSpec("total_liabilities", ("Liabilities",), ("us-gaap",), "USD", "instant"),
    ConceptSpec(
        "shareholders_equity",
        ("StockholdersEquity",),
        ("us-gaap",),
        "USD",
        "instant",
    ),
    ConceptSpec(
        "shares_outstanding",
        (
            # Balance-sheet share count. Later filings restate it on the same
            # period-end date (stock splits included). Preferred over the DEI
            # cover-page count, whose date is not the fiscal period end.
            "CommonStockSharesOutstanding",
            "EntityCommonStockSharesOutstanding",
        ),
        ("us-gaap", "dei"),
        "shares",
        "instant",
    ),
)

# Namespaces that are not us-gaap. Every other mapped concept is read from us-gaap.
CONCEPT_NAMESPACES: dict[str, tuple[str, ...]] = {
    "EntityCommonStockSharesOutstanding": ("dei",),
}

EXACT_CAPEX_CONCEPTS = frozenset(
    {
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsForAdditionsToPropertyPlantAndEquipment",
    }
)
CAPEX_FALLBACK_CONCEPT = "PaymentsToAcquireProductiveAssets"
OPERATING_CASH_FLOW_CONCEPT = "NetCashProvidedByUsedInOperatingActivities"
SHARES_PRIMARY_CONCEPT = "CommonStockSharesOutstanding"
SHARES_FALLBACK_CONCEPT = "EntityCommonStockSharesOutstanding"
DEBT_COMPREHENSIVE_CONCEPT = (
    "LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities"
)
DEBT_FALLBACK_CONCEPT = "LongTermDebt"
DEBT_PARTIAL_CONCEPTS = frozenset(
    {"LongTermDebtNoncurrent", "LongTermDebtCurrent", "DebtCurrent"}
)

# total_debt is not one tag. See resolve_total_debt in company_facts.py.
# Order of preference:
# 1. LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities
# 2. LongTermDebt, when it already equals noncurrent + current portion
# 3. LongTermDebt + current portion, when LongTermDebt equals the noncurrent part only
# 4. LongTermDebtNoncurrent + LongTermDebtCurrent (or DebtCurrent)
# LongTermDebt is never added on top of LongTermDebtCurrent without that check:
# for NVIDIA, LongTermDebt is already the sum, and adding the current portion
# would double-count it. Operating leases and ShortTermBorrowings are not folded in.
DEBT_CONCEPTS: tuple[str, ...] = (
    "LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities",
    "LongTermDebt",
    "LongTermDebtNoncurrent",
    "LongTermDebtCurrent",
    "DebtCurrent",
)

CONCEPT_UNITS: dict[str, str] = {}
for _spec in (*DURATION_METRICS, *INSTANT_METRICS):
    for _concept in _spec.concepts:
        CONCEPT_UNITS[_concept] = _spec.unit
        CONCEPT_NAMESPACES.setdefault(_concept, _spec.namespaces)
for _concept in DEBT_CONCEPTS:
    CONCEPT_UNITS[_concept] = "USD"
    CONCEPT_NAMESPACES.setdefault(_concept, ("us-gaap",))

DURATION_CONCEPTS = frozenset(
    concept for spec in DURATION_METRICS for concept in spec.concepts
)
INSTANT_CONCEPTS = frozenset(
    concept
    for spec in INSTANT_METRICS
    for concept in spec.concepts
) | frozenset(DEBT_CONCEPTS)
