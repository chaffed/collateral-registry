#!/usr/bin/env python3
"""Walk-through of the collateral registry, using SQLite and the standard library only.

Scenario: a paving company pledges an excavator to one bank, tries to register
the same machine again under a different description, then borrows a second,
smaller loan against the remaining value from another lender.

The registry itself lives in registry.py. See valuation_demo.py for
depreciation schedules, market prices and covenant tests.
"""
import secrets
import sqlite3

from registry import DuplicateAsset, Registry, usd


def step(title):
    print(f"\n== {title}")


def show_pledges(registry, asset_id):
    rows = registry.public_search(asset_id)
    if not rows:
        print("   (no active pledges)")
    for r in rows:
        print(f"   rank {r['priority_rank']}: {r['secured_party']}  recorded {r['recorded_at']}"
              f"  UCC-1 {r['ucc_state']} #{r['ucc_file_number']}")


def show_position(registry, asset_id):
    p = registry.lender_position(asset_id)
    headroom = p["latest_valuation_minor"] - p["pledged_caps_minor"]
    print(f"   valuation {usd(p['latest_valuation_minor'])}, pledged caps {usd(p['pledged_caps_minor'])},"
          f" indicative headroom {usd(headroom)}")


def main():
    reg = Registry()

    step("1. Participants join")
    acme = reg.admit("Acme Paving LLC", ["BORROWER"])
    first_bank = reg.admit("First Bank", ["LENDER"])
    river = reg.admit("River Credit Partners", ["LENDER"])
    dealer = reg.admit("Midwest Equipment Dealer", ["ATTESTER"])
    appraiser = reg.admit("Heartland Appraisals", ["ATTESTER"])
    reg.accredit(dealer, "EQUIPMENT", "IDENTITY", bond_minor=50_000_000)
    reg.accredit(appraiser, "EQUIPMENT", "VALUATION", bond_minor=25_000_000)
    print("   borrower, two lenders, and two accredited attesters admitted")

    step("2. Acme registers an excavator")
    excavator = reg.register_asset(
        acme, "EQUIPMENT", {"manufacturer": "Caterpillar", "serial_number": "CAT0320-GXK-01234"},
        "2022 Cat 320 hydraulic excavator")
    print(f"   canonical asset ID: {excavator}")

    step("3. Third parties attest")
    reg.attest(dealer, excavator, "IDENTITY",
               {"serial_number": "CAT0320GXK01234", "inspected": "2026-10-28", "location": "Des Moines, IA"})
    reg.attest(appraiser, excavator, "VALUATION", {"method": "orderly liquidation value"},
               valuation_minor=12_000_000)
    print("   dealer confirmed identity; appraiser valued it at $120,000")

    step("4. First Bank lends against it (cap $80,000)")
    contract_a = b"Security agreement: Acme Paving LLC grants First Bank a first-priority interest in asset ... cap USD 80,000"
    salt_a = secrets.token_bytes(16)
    pledge_a = reg.record_pledge(acme, first_bank, excavator, contract_a, salt_a, 8_000_000, ("IA", "2026-118842"))
    print("   pledge signed by Acme and First Bank, contract hash recorded")

    step("5. Acme tries to register the same excavator again, worded differently")
    try:
        reg.register_asset(acme, "EQUIPMENT",
                           {"manufacturer": "caterpillar", "serial_number": "cat 0320 gxk 01234"},
                           "Yellow excavator, lightly used")
    except DuplicateAsset as e:
        print(f"   REJECTED: {e}")

    step("6. River Credit searches by asset ID before lending")
    show_pledges(reg, excavator)
    show_position(reg, excavator)

    step("7. Acme shows River the First Bank contract")
    print(f"   genuine copy matches registered hash: {reg.verify_contract(pledge_a, contract_a, salt_a)}")
    doctored = contract_a.replace(b"80,000", b"20,000")
    print(f"   doctored copy (cap changed to $20,000) matches: {reg.verify_contract(pledge_a, doctored, salt_a)}")

    step("8. River lends $30,000 in second position")
    contract_b = b"Security agreement: Acme Paving LLC grants River Credit Partners a second-priority interest ... cap USD 30,000"
    reg.record_pledge(acme, river, excavator, contract_b, secrets.token_bytes(16), 3_000_000, ("IA", "2026-121907"))
    show_pledges(reg, excavator)
    show_position(reg, excavator)

    step("9. Acme repays First Bank, which releases its pledge")
    reg.release(first_bank, pledge_a)
    show_pledges(reg, excavator)

    step("10. Integrity checks")
    ok, bad_seq = reg.verify_chain()
    count = reg.db.execute("SELECT COUNT(*) FROM ledger_event").fetchone()[0]
    print(f"   hash chain over {count} events intact: {ok}" + (f" (breaks at {bad_seq})" if not ok else ""))
    try:
        reg.db.execute("UPDATE ledger_event SET payload = '{}' WHERE seq = 1")
    except sqlite3.IntegrityError as e:
        print(f"   attempt to edit history blocked: {e}")


if __name__ == "__main__":
    main()
