#!/usr/bin/env python3
"""Valuation and covenant tests, for two very different kinds of collateral.

Part A: an excavator losing value on a straight-line depreciation schedule,
        pledged to two lenders. Shows the junior lender absorbing the loss first.
Part B: restricted shares of a (fictional) public company, valued from a market
        price feed. Shows a loan-to-value covenant moving through WARN and
        BREACH as the price falls, and going STALE when prices stop arriving.
"""
import secrets

from registry import Registry, usd


def step(title):
    print(f"\n== {title}")


def show_tests(reg, asset_id, as_of, reported=None):
    val, results = reg.test_covenants(asset_id, as_of, reported)
    print(f"   {as_of[:10]}  value {usd(val.value_minor):>10}  ({val.detail})  [{val.status}]")
    for r in results:
        exposure = f"{usd(r.exposure_minor)} {'reported balance' if r.exposure_basis == 'REPORTED' else 'cap'}"
        print(f"      rank {r.rank} {r.secured_party:<22} {r.ratio_text():>7} vs {r.threshold_text():<13}"
              f" {r.result:<6}  exposure {exposure}, breaks below {usd(r.breach_value_minor)}")
    return results


def equipment(reg):
    print("\n" + "=" * 78 + "\nPART A — Equipment on a depreciation schedule\n" + "=" * 78)
    acme = reg.admit("Acme Paving LLC", ["BORROWER"])
    first_bank = reg.admit("First Bank", ["LENDER"])
    river = reg.admit("River Credit Partners", ["LENDER"])
    dealer = reg.admit("Midwest Equipment Dealer", ["ATTESTER"])
    appraiser = reg.admit("Heartland Appraisals", ["ATTESTER"])
    reg.accredit(dealer, "EQUIPMENT", "IDENTITY", bond_minor=50_000_000)
    reg.accredit(appraiser, "EQUIPMENT", "VALUATION", bond_minor=25_000_000)

    excavator = reg.register_asset(
        acme, "EQUIPMENT", {"manufacturer": "Caterpillar", "serial_number": "CAT0320-GXK-01234"},
        "2022 Cat 320 hydraulic excavator")
    reg.attest(dealer, excavator, "IDENTITY", {"serial_number": "CAT0320GXK01234", "inspected": "2026-10-28"})

    step("1. Appraiser attests a depreciation schedule")
    reg.set_valuation_model(appraiser, excavator, "STRAIGHT_LINE", {
        "basis_minor": 12_000_000, "basis_date": "2026-10-28",
        "useful_life_years": 7, "salvage_minor": 2_000_000,
    })
    print("   $120,000 today, straight-line over 7 years to a $20,000 salvage value")

    step("2. Two loans with coverage covenants")
    pledge_a = reg.record_pledge(acme, first_bank, excavator, b"agreement A", secrets.token_bytes(16),
                                 8_000_000, ("IA", "2026-118842"))
    reg.set_covenant(acme, first_bank, pledge_a, "MIN_COVERAGE", threshold_bp=12_500)
    pledge_b = reg.record_pledge(acme, river, excavator, b"agreement B", secrets.token_bytes(16),
                                 3_000_000, ("IA", "2026-121907"))
    reg.set_covenant(acme, river, pledge_b, "MIN_COVERAGE", threshold_bp=10_000)
    print("   First Bank: cap $80,000, must stay covered 1.25x")
    print("   River Credit: cap $30,000 in second position, must stay covered 1.00x")

    step("3. Covenant tests over the next three years (exposure = full cap)")
    results = None
    for as_of in ("2026-10-30", "2027-10-30", "2028-10-30", "2029-10-30"):
        r = show_tests(reg, excavator, as_of)
        results = results or r

    step("4. Projected breach dates from the schedule")
    for r in results:
        when = reg.projected_breach_date(excavator, r, "2026-10-30")
        print(f"   {r.secured_party:<22} value falls below {usd(r.breach_value_minor)}"
              f" around {when.strftime('%b %Y') if when else 'never'}")

    step("5. Same test in 2028, with First Bank reporting its amortized balance of $50,000")
    show_tests(reg, excavator, "2028-10-30", {pledge_a: 5_000_000})
    print("   First Bank passes on its real balance. River still counts First Bank at its full")
    print("   $80,000 cap, because it can't see the balance and the loan could be redrawn.")


def securities(reg):
    print("\n" + "=" * 78 + "\nPART B — Restricted shares valued from a market price feed\n" + "=" * 78)
    northwind = reg.admit("Northwind Holdings LLC", ["BORROWER"])
    harbor = reg.admit("Harbor Bank", ["LENDER"])
    agent = reg.admit("Keystone Transfer Co.", ["ATTESTER"])
    feed = reg.admit("Consolidated Price Feed", ["PRICE_SOURCE"])
    reg.accredit(agent, "SECURITY_HOLDING", "OWNERSHIP", bond_minor=100_000_000)
    reg.accredit(agent, "SECURITY_HOLDING", "VALUATION", bond_minor=100_000_000)
    source = reg.add_price_source(feed, "Consolidated Price Feed — closing prices", max_staleness_seconds=3 * 86_400)

    holding = reg.register_asset(northwind, "SECURITY_HOLDING", {
        "issuer_registration_id": "US-DE-7712345", "share_class": "COMMON",
        "holder_registration_id": "US-DE-8800123", "position_id": "DRS-004417",
    }, "100,000 restricted common shares of Example Robotics Inc. (fictional)")

    step("1. Transfer agent attests ownership and links the holding to a price feed")
    reg.attest(agent, holding, "OWNERSHIP", {"quantity": 100_000, "restricted": True})
    reg.set_valuation_model(agent, holding, "MARKET_PRICE",
                            {"instrument_id": "EXRB", "quantity": 100_000, "source_id": source})
    print("   100,000 shares, priced from closing prices; stale after 3 days without a price")

    step("2. Harbor Bank lends up to $1,000,000 with a 50% LTV covenant and a 30% haircut")
    pledge = reg.record_pledge(northwind, harbor, holding, b"pledge agreement", secrets.token_bytes(16),
                               100_000_000, ("DE", "2026-7730112"))
    reg.set_covenant(northwind, harbor, pledge, "MAX_LTV", threshold_bp=5_000, haircut_bp=3_000)
    print("   Haircut: only 70% of market value counts, to allow for volatility and restrictions")

    step("3. Daily tests as the share price falls")
    prices = [("2026-11-02", 4_000), ("2026-11-03", 3_000), ("2026-11-04", 2_500)]
    for day, price in prices:
        reg.record_price(feed, source, "EXRB", price, f"{day}T21:00:00+00:00")
        results = show_tests(reg, holding, f"{day}T22:00:00+00:00")
    trigger = results[0].breach_value_minor / 100_000 / 100
    print(f"   Breach trigger price: ${trigger:,.2f} per share")

    step("4. The feed stops publishing")
    show_tests(reg, holding, "2026-11-09T22:00:00+00:00")
    print("   Last price is 5 days old, so the test reports STALE instead of trusting it")

    ok, _ = reg.verify_chain()
    print(f"\n   All schedules, prices and covenants are signed ledger events. Chain intact: {ok}")


def main():
    reg = Registry()
    equipment(reg)
    securities(reg)


if __name__ == "__main__":
    main()
