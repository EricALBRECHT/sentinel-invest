from decimal import Decimal

def compute_free_cash_flow(
    operating_cash_flow: Decimal | None,
    capital_expenditure: Decimal | None,
) -> Decimal | None:
    """Operating cash flow minus capital spending.

    Sentinel does not read a free-cash-flow concept from the source.
    SEC payment tags such as ``PaymentsToAcquirePropertyPlantAndEquipment``
    and ``PaymentsToAcquireProductiveAssets`` are cash outflows, but filers
    usually report them as positive amounts. Some filers report a negative
    amount. The absolute value removes that sign convention:

        free_cash_flow = operating_cash_flow - abs(capital_expenditure)

    The stored ``capital_expenditure`` keeps the sign reported by SEC.
    """
    if operating_cash_flow is None or capital_expenditure is None:
        return None
    return operating_cash_flow - abs(capital_expenditure)
