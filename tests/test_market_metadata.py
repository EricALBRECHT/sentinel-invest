"""Market cap provenance stays offline. Filing shares and aliases are local rows."""

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.models.company import Company
from app.models.company_market_snapshot import CompanyMarketSnapshot
from app.models.financial_metric import FinancialMetric
from app.models.market_provider_symbol import MarketProviderSymbol
from app.models.opportunity_profile import OpportunityProfile
from app.models.opportunity_score import OpportunityScore
from app.services.market.metadata import (
    ShareCount,
    assess_market_cap,
    registered_share_sources,
    resolve_market_cap,
)
from app.services.market.provider import MARKET_SOURCE, DailyBar, MarketQuote
from app.services.market.calculations import build_snapshot
from app.services.market.symbols import normalize_yahoo_symbol, resolve_provider_symbol
from app.services.market.sync import execute_market_sync
from tests.test_market import FakeMarket, _bar, _series


def _count(shares: str, as_of: date, source: str = "sec_edgar") -> ShareCount:
    return ShareCount(shares=Decimal(shares), as_of=as_of, source=source, filed_at=as_of)


def _priced(day: date, close: str, adjusted: str) -> DailyBar:
    return DailyBar(
        trade_date=day,
        open=Decimal(close),
        high=Decimal(close),
        low=Decimal(close),
        close=Decimal(close),
        adjusted_close=Decimal(adjusted),
        volume=100,
    )


def test_provider_market_cap_is_preferred_over_shares():
    assessment = assess_market_cap(
        provider_cap=Decimal("2000000000"),
        provider_source="yahoo",
        price=Decimal("10"),
        currency="USD",
        market_date=date(2026, 10, 5),
        shares=[_count("10", date(2026, 9, 1))],
        bars=[],
    )
    assert assessment.method == "PROVIDER"
    assert assessment.source == "yahoo"
    assert assessment.confidence == "HIGH"
    assert assessment.as_of == date(2026, 10, 5)
    assert assessment.market_cap == Decimal("2000000000.00")
    assert assessment.updates_company is True
    assert assessment.shares_outstanding is None


def test_price_times_recent_shares():
    assessment = assess_market_cap(
        provider_cap=None,
        provider_source="yahoo",
        price=Decimal("3"),
        currency="USD",
        market_date=date(2026, 10, 5),
        shares=[_count("2000000000", date(2026, 9, 1))],
        bars=[_priced(date(2026, 9, 1), "3", "3"), _priced(date(2026, 10, 5), "3", "3")],
    )
    assert assessment.method == "PRICE_X_SHARES"
    assert assessment.source == "calculated"
    assert assessment.confidence == "HIGH"
    assert assessment.as_of == date(2026, 9, 1)
    assert assessment.market_cap == Decimal("6000000000.00")
    assert assessment.shares_outstanding == Decimal("2000000000")
    assert assessment.updates_company is True


def test_old_shares_are_refused():
    assessment = assess_market_cap(
        provider_cap=None,
        provider_source="yahoo",
        price=Decimal("10"),
        currency="USD",
        market_date=date(2026, 10, 5),
        shares=[_count("2000000000", date(2020, 1, 1))],
        bars=[_priced(date(2026, 10, 5), "10", "10")],
    )
    assert assessment.market_cap is None
    assert assessment.updates_company is False
    assert "older than" in assessment.reason


def test_missing_shares_are_refused():
    assessment = assess_market_cap(
        provider_cap=None,
        provider_source="yahoo",
        price=Decimal("10"),
        currency="USD",
        market_date=date(2026, 10, 5),
        shares=[],
        bars=[_priced(date(2026, 10, 5), "10", "10")],
    )
    assert assessment.market_cap is None
    assert assessment.confidence is None
    assert "No shares outstanding" in assessment.reason


def test_low_confidence_does_not_update_company_cap():
    assessment = assess_market_cap(
        provider_cap=None,
        provider_source="yahoo",
        price=Decimal("10"),
        currency="USD",
        market_date=date(2024, 6, 20),
        shares=[
            _count("100", date(2024, 1, 1)),
            _count("1000", date(2024, 6, 1)),
        ],
        bars=[_priced(date(2024, 1, 1), "10", "10"), _priced(date(2024, 6, 20), "10", "10")],
    )
    assert assessment.confidence == "LOW"
    assert assessment.method == "PRICE_X_SHARES"
    assert assessment.source == "calculated"
    assert assessment.market_cap == Decimal("10000.00")
    assert assessment.updates_company is False


def test_pre_split_share_count_is_not_multiplied_by_the_new_price():
    refused = assess_market_cap(
        provider_cap=None,
        provider_source="yahoo",
        price=Decimal("120"),
        currency="USD",
        market_date=date(2024, 7, 29),
        shares=[_count("2460000000", date(2024, 4, 28))],
        bars=[
            _priced(date(2024, 4, 26), "1200", "120"),
            _priced(date(2024, 7, 29), "120", "120"),
        ],
    )
    assert refused.market_cap is None
    assert "predates a split" in refused.reason

    post_split = Decimal("24100000000")
    accepted = assess_market_cap(
        provider_cap=None,
        provider_source="yahoo",
        price=Decimal("237.055"),
        currency="USD",
        market_date=date(2026, 10, 5),
        shares=[
            _count("2460000000", date(2024, 4, 28)),
            _count(str(post_split), date(2026, 7, 26)),
        ],
        bars=[
            _priced(date(2024, 4, 26), "1200", "120"),
            _priced(date(2024, 7, 29), "130", "130"),
            _priced(date(2026, 7, 26), "200", "200"),
            _priced(date(2026, 10, 5), "237.055", "237.055"),
        ],
    )
    assert accepted.shares_outstanding == post_split
    assert accepted.confidence == "HIGH"
    assert accepted.market_cap == (Decimal("237.055") * post_split).quantize(Decimal("0.01"))
    assert accepted.updates_company is True


def test_only_sec_source_is_registered_and_non_usd_cap_stays_off_the_company():
    assert [source.name for source in registered_share_sources()] == ["sec_edgar"]
    assessment = assess_market_cap(
        provider_cap=Decimal("50000000000"),
        provider_source="yahoo",
        price=Decimal("50"),
        currency="EUR",
        market_date=date(2026, 10, 5),
        shares=[],
        bars=[],
    )
    assert assessment.confidence == "HIGH"
    assert assessment.currency == "EUR"
    assert assessment.updates_company is False
    assert "USD" in assessment.reason


async def test_company_without_cik_does_not_use_stored_shares(session_factory):
    async with session_factory() as session:
        company = Company(
            name="European Listed",
            ticker="EURL",
            country="NL",
            market_symbol="EURL.PA",
            market_cap=Decimal("10.00"),
        )
        session.add(company)
        await session.commit()
        await session.refresh(company)
        session.add(
            FinancialMetric(
                company_id=company.id,
                fiscal_year=2026,
                fiscal_period="FY",
                period_end=date(2026, 7, 1),
                shares_outstanding=Decimal("1000000000"),
                source="sec_edgar",
            )
        )
        await session.commit()
        bars = [_priced(date(2026, 10, 5), "20", "20")]
        metrics = build_snapshot(bars, MarketQuote(price=Decimal("20"), currency="EUR"))
        assessment = await resolve_market_cap(
            session,
            company,
            MarketQuote(price=Decimal("20"), currency="EUR"),
            metrics,
            bars,
        )
    assert assessment.market_cap is None
    assert assessment.updates_company is False
    assert "CIK" in assessment.reason


class _EuropeanShares:
    name = "european_filing"

    async def load(self, session, company):
        return [_count("1000000000", date(2026, 9, 1), source="european_filing")]


async def test_a_later_share_source_can_supply_the_fallback(session_factory):
    async with session_factory() as session:
        company = Company(name="Future Europe", ticker="FUTR", country="FR", market_symbol="FUTR.PA")
        session.add(company)
        await session.commit()
        await session.refresh(company)
        bars = [_priced(date(2026, 9, 1), "4", "4"), _priced(date(2026, 10, 5), "4", "4")]
        metrics = build_snapshot(bars, MarketQuote(price=Decimal("4"), currency="EUR"))
        assessment = await resolve_market_cap(
            session,
            company,
            MarketQuote(price=Decimal("4"), currency="EUR"),
            metrics,
            bars,
            sources=[_EuropeanShares()],
        )
    assert assessment.method == "PRICE_X_SHARES"
    assert assessment.source == "calculated"
    assert assessment.shares_outstanding == Decimal("1000000000")
    assert assessment.market_cap == Decimal("4000000000.00")
    assert assessment.updates_company is False


async def test_share_fallback_recalculates_opportunity_and_low_confidence_does_not(session_factory):
    market_day = date(2024, 1, 6)
    bars = _series(6)
    quote = MarketQuote(price=Decimal("1"), currency="USD", volume=10)
    async with session_factory() as session:
        company = Company(
            name="Share Cap",
            ticker="SHCAP",
            market_symbol="SHCAP",
            sec_cik="0000000099",
        )
        session.add(company)
        await session.commit()
        await session.refresh(company)
        session.add_all(
            [
                OpportunityProfile(
                    company_id=company.id,
                    method_version="opportunity_v1",
                    market_growth_score=Decimal("50"),
                ),
                FinancialMetric(
                    company_id=company.id,
                    fiscal_year=2024,
                    fiscal_period="Q2",
                    period_end=market_day - timedelta(days=20),
                    shares_outstanding=Decimal("2000000000"),
                    source="sec_edgar",
                ),
            ]
        )
        await session.commit()
        result = await execute_market_sync(session, company.id, provider=FakeMarket(bars, quote))
        await session.refresh(company)
        score = (
            await session.execute(select(OpportunityScore).where(OpportunityScore.company_id == company.id))
        ).scalar_one()
        snapshot = (
            await session.execute(
                select(CompanyMarketSnapshot).where(CompanyMarketSnapshot.company_id == company.id)
            )
        ).scalar_one()

        doubtful = Company(
            name="Low Cap",
            ticker="LOWCAP",
            market_symbol="LOWCAP",
            sec_cik="0000000098",
        )
        session.add(doubtful)
        await session.commit()
        await session.refresh(doubtful)
        session.add_all(
            [
                OpportunityProfile(
                    company_id=doubtful.id,
                    method_version="opportunity_v1",
                    market_growth_score=Decimal("50"),
                ),
                FinancialMetric(
                    company_id=doubtful.id,
                    fiscal_year=2023,
                    fiscal_period="FY",
                    period_end=date(2024, 1, 1),
                    shares_outstanding=Decimal("100"),
                    source="sec_edgar",
                ),
                FinancialMetric(
                    company_id=doubtful.id,
                    fiscal_year=2024,
                    fiscal_period="Q2",
                    period_end=date(2024, 6, 1),
                    shares_outstanding=Decimal("1000"),
                    source="sec_edgar",
                ),
            ]
        )
        await session.commit()
        low_bars = [
            _bar(0, 10, 100),
            DailyBar(
                trade_date=date(2024, 6, 20),
                open=Decimal("10"),
                high=Decimal("10"),
                low=Decimal("10"),
                close=Decimal("10"),
                adjusted_close=Decimal("10"),
                volume=100,
            ),
        ]
        low = await execute_market_sync(
            session,
            doubtful.id,
            provider=FakeMarket(low_bars, MarketQuote(price=Decimal("10"), currency="USD")),
        )
        await session.refresh(doubtful)
        low_snapshot = (
            await session.execute(
                select(CompanyMarketSnapshot).where(CompanyMarketSnapshot.company_id == doubtful.id)
            )
        ).scalar_one()
        low_score = (
            await session.execute(select(OpportunityScore).where(OpportunityScore.company_id == doubtful.id))
        ).scalar_one_or_none()

    assert result["market_cap_updated"] is True
    assert result["opportunity_recalculated"] is True
    assert result["priority_recalculated"] is True
    assert company.market_cap == Decimal("2000000000.00")
    assert score.size_runway_score == Decimal("100.00")
    assert snapshot.market_cap_method == "PRICE_X_SHARES"
    assert snapshot.market_cap_source == "calculated"
    assert snapshot.market_cap_confidence == "HIGH"
    assert snapshot.market_cap_as_of == market_day - timedelta(days=20)
    assert low["market_cap_updated"] is False
    assert low["opportunity_recalculated"] is False
    assert doubtful.market_cap is None
    assert low_snapshot.market_cap_confidence == "LOW"
    assert low_snapshot.market_cap == Decimal("10000.00")
    assert low_score is None
    assert MARKET_SOURCE == "yahoo"


async def test_provider_alias_replaces_the_requested_symbol(session_factory):
    seen: list[str] = []

    class RecordingMarket(FakeMarket):
        async def get_history(self, symbol: str, start: date, end: date):
            seen.append(symbol)
            return await super().get_history(symbol, start, end)

        async def get_snapshot(self, symbol: str):
            seen.append(symbol)
            return await super().get_snapshot(symbol)

    async with session_factory() as session:
        listed = Company(name="Alias Co", ticker="ALIAS", market_symbol="STM.PA")
        plain = Company(name="Plain Co", ticker="PLAIN", market_symbol="NVDA")
        session.add_all([listed, plain])
        await session.commit()
        await session.refresh(listed)
        await session.refresh(plain)
        session.add(
            MarketProviderSymbol(company_id=listed.id, provider="yahoo", provider_symbol="STMPA.PA")
        )
        await session.commit()
        assert await resolve_provider_symbol(session, listed, "yahoo") == "STMPA.PA"
        assert await resolve_provider_symbol(session, plain, "yahoo") == "NVDA"
        await execute_market_sync(
            session,
            listed.id,
            provider=RecordingMarket(_series(2), MarketQuote(currency="EUR", price=Decimal("1"))),
        )
    assert seen == ["STMPA.PA", "STMPA.PA"]


def test_yahoo_share_class_symbols_use_hyphen():
    assert normalize_yahoo_symbol("BRK.B") == "BRK-B"
    assert normalize_yahoo_symbol("BF.B") == "BF-B"
    assert normalize_yahoo_symbol("BRK.A") == "BRK-A"
    assert normalize_yahoo_symbol("brk.b") == "BRK-B"
    # Exchange suffixes stay dotted for Yahoo.
    assert normalize_yahoo_symbol("STM.PA") == "STM.PA"
    assert normalize_yahoo_symbol("NVDA") == "NVDA"


async def test_resolve_provider_symbol_normalizes_yahoo_share_class(session_factory):
    async with session_factory() as session:
        brk = Company(name="Berkshire", ticker="BRK.B", market_symbol="BRK.B")
        bf = Company(name="Brown Forman", ticker="BF.B", market_symbol="BF.B")
        session.add_all([brk, bf])
        await session.commit()
        await session.refresh(brk)
        await session.refresh(bf)
        assert await resolve_provider_symbol(session, brk, "yahoo") == "BRK-B"
        assert await resolve_provider_symbol(session, bf, "yahoo") == "BF-B"
        # Sentinel storage unchanged.
        assert brk.market_symbol == "BRK.B"
        assert bf.ticker == "BF.B"
        # Explicit alias still wins.
        session.add(
            MarketProviderSymbol(company_id=brk.id, provider="yahoo", provider_symbol="BRK-B.ALIAS")
        )
        await session.commit()
        assert await resolve_provider_symbol(session, brk, "yahoo") == "BRK-B.ALIAS"
