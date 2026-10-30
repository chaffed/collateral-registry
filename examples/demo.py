#!/usr/bin/env python3
"""Walk-through of the collateral registry, using SQLite and the standard library only.

Scenario: a paving company pledges an excavator to one bank, tries to register
the same machine again under a different description, then borrows a second,
smaller loan against the remaining value from another lender.

Signatures are simulated with HMAC so the demo has no dependencies. A real
registry would use public-key signatures (e.g. Ed25519) so anyone can verify a
signature without holding the signer's secret.
"""
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = Path(__file__).resolve().parent.parent / "schema" / "registry.sql"

# Held only by the registry operator. Using a keyed hash for fingerprints stops
# outsiders guessing invoice details by hashing likely values and comparing.
OPERATOR_FINGERPRINT_KEY = b"demo-operator-fingerprint-key"


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def sha256(data):
    return hashlib.sha256(data if isinstance(data, bytes) else data.encode()).hexdigest()


def usd(minor):
    return f"${minor / 100:,.0f}"


@dataclass
class Party:
    party_id: str
    name: str
    key_id: str
    secret: bytes

    def sign(self, payload_hash):
        return hmac.new(self.secret, payload_hash.encode(), hashlib.sha256).hexdigest()


class DuplicateAsset(Exception):
    def __init__(self, asset_id):
        super().__init__(f"asset already registered as {asset_id}")
        self.asset_id = asset_id


class Registry:
    def __init__(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA.read_text())

    # -- ledger --------------------------------------------------------------

    def _append(self, event_type, subject_id, payload, signers):
        """Append one event to the hash chain, signed by every party that must agree."""
        last = self.db.execute("SELECT seq, event_hash FROM ledger_event ORDER BY seq DESC LIMIT 1").fetchone()
        seq = last["seq"] + 1 if last else 1
        prev_hash = last["event_hash"] if last else "0" * 64
        body = canonical(payload)
        payload_hash = sha256(body)
        recorded_at = now()
        event_hash = sha256(f"{seq}|{prev_hash}|{payload_hash}|{recorded_at}")
        self.db.execute(
            "INSERT INTO ledger_event VALUES (?,?,?,?,?,?,?,?,?)",
            (seq, new_id("evt"), event_type, subject_id, body, payload_hash, prev_hash, event_hash, recorded_at),
        )
        for p in signers:
            self.db.execute(
                "INSERT INTO event_signature VALUES (?,?,?,?)",
                (seq, p.party_id, p.key_id, p.sign(payload_hash)),
            )
        return seq

    def verify_chain(self):
        prev = "0" * 64
        for e in self.db.execute("SELECT * FROM ledger_event ORDER BY seq"):
            expected = sha256(f"{e['seq']}|{prev}|{sha256(e['payload'])}|{e['recorded_at']}")
            if e["prev_hash"] != prev or e["event_hash"] != expected:
                return False, e["seq"]
            prev = e["event_hash"]
        return True, None

    # -- parties -------------------------------------------------------------

    def admit(self, name, roles):
        party = Party(new_id("pty"), name, new_id("key"), secrets.token_bytes(32))
        ts = now()
        self.db.execute("INSERT INTO party (party_id, legal_name, created_at) VALUES (?,?,?)",
                        (party.party_id, name, ts))
        for role in roles:
            self.db.execute("INSERT INTO party_role VALUES (?,?,?,NULL)", (party.party_id, role, ts))
        self.db.execute(
            "INSERT INTO party_key (key_id, party_id, algorithm, public_key, valid_from) VALUES (?,?,?,?,?)",
            (party.key_id, party.party_id, "demo-hmac", f"demo:{party.key_id}", ts),
        )
        self._append("PARTY_ADMITTED", party.party_id, {"name": name, "roles": roles}, [])
        return party

    def accredit(self, attester, asset_class, attestation_type, bond_minor):
        self.db.execute(
            "INSERT INTO attester_accreditation (accreditation_id, party_id, asset_class, attestation_type,"
            " bond_minor, currency, valid_from) VALUES (?,?,?,?,?,'USD',?)",
            (new_id("acc"), attester.party_id, asset_class, attestation_type, bond_minor, now()),
        )

    # -- assets --------------------------------------------------------------

    def fingerprint(self, asset_class, identity):
        fields = json.loads(self.db.execute(
            "SELECT identity_fields FROM asset_class WHERE code = ?", (asset_class,)).fetchone()[0])
        # Normalize so "Caterpillar / CAT-0320 GXK" and "caterpillar cat0320gxk" match.
        normalized = {f: re.sub(r"[^A-Z0-9]", "", str(identity[f]).upper()) for f in fields}
        return hmac.new(OPERATOR_FINGERPRINT_KEY, canonical([asset_class, normalized]).encode(),
                        hashlib.sha256).hexdigest()

    def register_asset(self, owner, asset_class, identity, description):
        fp = self.fingerprint(asset_class, identity)
        existing = self.db.execute("SELECT asset_id FROM asset WHERE asset_class = ? AND fingerprint = ?",
                                   (asset_class, fp)).fetchone()
        if existing:
            raise DuplicateAsset(existing["asset_id"])
        asset_id = new_id("ast")
        seq = self._append("ASSET_REGISTERED", asset_id,
                           {"asset_class": asset_class, "fingerprint": fp, "owner": owner.party_id}, [owner])
        self.db.execute(
            "INSERT INTO asset (asset_id, asset_class, fingerprint, owner_party_id, description, registered_seq)"
            " VALUES (?,?,?,?,?,?)",
            (asset_id, asset_class, fp, owner.party_id, description, seq),
        )
        return asset_id

    def attest(self, attester, asset_id, attestation_type, statement, valuation_minor=None):
        asset_class = self.db.execute("SELECT asset_class FROM asset WHERE asset_id = ?", (asset_id,)).fetchone()[0]
        accredited = self.db.execute(
            "SELECT 1 FROM attester_accreditation WHERE party_id = ? AND asset_class = ?"
            " AND attestation_type = ? AND revoked_at IS NULL",
            (attester.party_id, asset_class, attestation_type),
        ).fetchone()
        if not accredited:
            raise PermissionError(f"{attester.name} is not accredited for {attestation_type} on {asset_class}")
        statement_hash = sha256(canonical(statement))
        seq = self._append("ATTESTED", asset_id,
                           {"type": attestation_type, "statement_hash": statement_hash,
                            "valuation_minor": valuation_minor}, [attester])
        self.db.execute(
            "INSERT INTO attestation (attestation_id, asset_id, attester_party_id, attestation_type,"
            " statement_hash, valuation_minor, currency, attested_seq) VALUES (?,?,?,?,?,?,?,?)",
            (new_id("att"), asset_id, attester.party_id, attestation_type, statement_hash,
             valuation_minor, "USD" if valuation_minor else None, seq),
        )

    # -- pledges -------------------------------------------------------------

    def record_pledge(self, borrower, lender, asset_id, contract, salt, max_secured_minor, ucc):
        required = self.db.execute(
            "SELECT c.required_attestation FROM asset a JOIN asset_class c ON c.code = a.asset_class"
            " WHERE a.asset_id = ? AND a.status = 'ACTIVE'", (asset_id,)).fetchone()
        if not required:
            raise ValueError("asset is not pledgeable")
        if not self.db.execute(
                "SELECT 1 FROM attestation WHERE asset_id = ? AND attestation_type = ? AND revoked_at IS NULL",
                (asset_id, required[0])).fetchone():
            raise ValueError(f"asset needs a {required[0]} attestation before it can be pledged")

        pledge_id = new_id("plg")
        contract_hash = sha256(salt + contract)
        payload = {"asset_id": asset_id, "borrower": borrower.party_id, "secured_party": lender.party_id,
                   "max_secured_minor": max_secured_minor, "contract_hash": contract_hash}
        seq = self._append("PLEDGE_RECORDED", pledge_id, payload, [borrower, lender])
        self.db.execute(
            "INSERT INTO pledge (pledge_id, asset_id, borrower_party_id, secured_party_id, max_secured_minor,"
            " currency, contract_hash, ucc_state, ucc_file_number, effective_at, recorded_seq)"
            " VALUES (?,?,?,?,?,'USD',?,?,?,?,?)",
            (pledge_id, asset_id, borrower.party_id, lender.party_id, max_secured_minor, contract_hash,
             ucc[0], ucc[1], now(), seq),
        )
        self._rerank(asset_id)
        return pledge_id

    def release(self, lender, pledge_id):
        row = self.db.execute("SELECT asset_id, secured_party_id FROM pledge WHERE pledge_id = ?",
                              (pledge_id,)).fetchone()
        if row["secured_party_id"] != lender.party_id:
            raise PermissionError("only the secured party can release a pledge")
        seq = self._append("PLEDGE_RELEASED", pledge_id, {"pledge_id": pledge_id}, [lender])
        self.db.execute("UPDATE pledge SET status = 'RELEASED', priority_rank = NULL, released_seq = ?"
                        " WHERE pledge_id = ?", (seq, pledge_id))
        self._rerank(asset_id=row["asset_id"])

    def _rerank(self, asset_id):
        # Default rule: first recorded, first in line. (Subordination events would
        # reorder this; left out of the demo to keep it short.)
        active = self.db.execute("SELECT pledge_id FROM pledge WHERE asset_id = ? AND status = 'ACTIVE'"
                                 " ORDER BY recorded_seq", (asset_id,)).fetchall()
        for rank, row in enumerate(active, start=1):
            self.db.execute("UPDATE pledge SET priority_rank = ? WHERE pledge_id = ?", (rank, row[0]))

    def verify_contract(self, pledge_id, contract, salt):
        stored = self.db.execute("SELECT contract_hash FROM pledge WHERE pledge_id = ?", (pledge_id,)).fetchone()[0]
        return hmac.compare_digest(stored, sha256(salt + contract))

    # -- lookups -------------------------------------------------------------

    def public_search(self, asset_id):
        return self.db.execute("SELECT * FROM public_pledge WHERE asset_id = ? AND status = 'ACTIVE'"
                               " ORDER BY priority_rank", (asset_id,)).fetchall()

    def lender_position(self, asset_id):
        return self.db.execute("SELECT * FROM lender_asset_position WHERE asset_id = ?", (asset_id,)).fetchone()


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
