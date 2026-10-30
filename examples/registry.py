"""Reference implementation of the collateral registry, using SQLite and the standard library only.

Signatures are simulated with HMAC so the examples have no dependencies. A real
registry would use public-key signatures (e.g. Ed25519) so anyone can verify a
signature without holding the signer's secret.
"""
import hashlib
import hmac
import json
import math
import re
import secrets
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

SCHEMA = Path(__file__).resolve().parent.parent / "schema" / "registry.sql"

# Held only by the registry operator. Using a keyed hash for fingerprints stops
# outsiders guessing invoice details by hashing likely values and comparing.
OPERATOR_FINGERPRINT_KEY = b"demo-operator-fingerprint-key"

SCHEDULE_METHODS = ("STRAIGHT_LINE", "DECLINING_BALANCE")


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_ts(value):
    ts = datetime.fromisoformat(value) if isinstance(value, str) else value
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def new_id(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def sha256(data):
    return hashlib.sha256(data if isinstance(data, bytes) else data.encode()).hexdigest()


def usd(minor):
    return f"${minor / 100:,.0f}" if minor is not None else "—"


@dataclass
class Party:
    party_id: str
    name: str
    key_id: str
    secret: bytes

    def sign(self, payload_hash):
        return hmac.new(self.secret, payload_hash.encode(), hashlib.sha256).hexdigest()


@dataclass
class Valuation:
    value_minor: Optional[int]
    status: str                    # OK, STALE, NO_MODEL, NO_PRICE
    method: Optional[str]
    detail: str
    price_observation_id: Optional[str] = None


@dataclass
class CovenantResult:
    covenant_id: str
    pledge_id: str
    secured_party: str
    rank: int
    kind: str
    threshold_bp: int
    exposure_minor: int
    exposure_basis: str
    available_minor: Optional[int]
    ratio: Optional[float]         # coverage multiple or LTV fraction; inf when unbounded
    breach_value_minor: Optional[int]
    result: str

    def ratio_text(self):
        if self.ratio is None:
            return "—"
        if math.isinf(self.ratio):
            return "∞"
        return f"{self.ratio:.2f}x" if self.kind == "MIN_COVERAGE" else f"{self.ratio:.1%}"

    def threshold_text(self):
        t = self.threshold_bp / 10000
        return f"min {t:.2f}x" if self.kind == "MIN_COVERAGE" else f"max {t:.0%} LTV"


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

    def _require_accreditation(self, attester, asset_id, attestation_type):
        asset_class = self.db.execute("SELECT asset_class FROM asset WHERE asset_id = ?", (asset_id,)).fetchone()[0]
        if not self.db.execute(
                "SELECT 1 FROM attester_accreditation WHERE party_id = ? AND asset_class = ?"
                " AND attestation_type = ? AND revoked_at IS NULL",
                (attester.party_id, asset_class, attestation_type)).fetchone():
            raise PermissionError(f"{attester.name} is not accredited for {attestation_type} on {asset_class}")

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
        self._require_accreditation(attester, asset_id, attestation_type)
        statement_hash = sha256(canonical(statement))
        seq = self._append("ATTESTED", asset_id,
                           {"type": attestation_type, "statement_hash": statement_hash,
                            "valuation_minor": valuation_minor}, [attester])
        attestation_id = new_id("att")
        self.db.execute(
            "INSERT INTO attestation (attestation_id, asset_id, attester_party_id, attestation_type,"
            " statement_hash, valuation_minor, currency, attested_seq) VALUES (?,?,?,?,?,?,?,?)",
            (attestation_id, asset_id, attester.party_id, attestation_type, statement_hash,
             valuation_minor, "USD" if valuation_minor else None, seq),
        )
        return attestation_id

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
        self._rerank(row["asset_id"])

    def _rerank(self, asset_id):
        # Default rule: first recorded, first in line. (Subordination events would
        # reorder this; left out of the examples to keep them short.)
        active = self.db.execute("SELECT pledge_id FROM pledge WHERE asset_id = ? AND status = 'ACTIVE'"
                                 " ORDER BY recorded_seq", (asset_id,)).fetchall()
        for rank, row in enumerate(active, start=1):
            self.db.execute("UPDATE pledge SET priority_rank = ? WHERE pledge_id = ?", (rank, row[0]))

    def verify_contract(self, pledge_id, contract, salt):
        stored = self.db.execute("SELECT contract_hash FROM pledge WHERE pledge_id = ?", (pledge_id,)).fetchone()[0]
        return hmac.compare_digest(stored, sha256(salt + contract))

    # -- valuation -----------------------------------------------------------

    def set_valuation_model(self, attester, asset_id, method, params):
        """Attest how an asset's value is worked out. Replaces any earlier model."""
        self._require_accreditation(attester, asset_id, "VALUATION")
        statement = {"method": method, "params": params}
        seq = self._append("VALUATION_MODEL_SET", asset_id, statement, [attester])
        attestation_id = new_id("att")
        self.db.execute(
            "INSERT INTO attestation (attestation_id, asset_id, attester_party_id, attestation_type,"
            " statement_hash, attested_seq) VALUES (?,?,?,'VALUATION',?,?)",
            (attestation_id, asset_id, attester.party_id, sha256(canonical(statement)), seq),
        )
        self.db.execute("UPDATE valuation_model SET superseded_seq = ? WHERE asset_id = ? AND superseded_seq IS NULL",
                        (seq, asset_id))
        self.db.execute(
            "INSERT INTO valuation_model (model_id, asset_id, method, params, attestation_id, recorded_seq)"
            " VALUES (?,?,?,?,?,?)",
            (new_id("val"), asset_id, method, canonical(params), attestation_id, seq),
        )

    def add_price_source(self, operator, name, max_staleness_seconds):
        source_id = new_id("src")
        self.db.execute("INSERT INTO price_source VALUES (?,?,?,?)",
                        (source_id, name, operator.party_id, max_staleness_seconds))
        return source_id

    def record_price(self, operator, source_id, instrument_id, price_minor, observed_at):
        payload = {"source_id": source_id, "instrument_id": instrument_id,
                   "price_minor": price_minor, "observed_at": observed_at}
        seq = self._append("PRICE_RECORDED", instrument_id, payload, [operator])
        self.db.execute("INSERT INTO price_observation VALUES (?,?,?,?,'USD',?,?)",
                        (new_id("prc"), source_id, instrument_id, price_minor, observed_at, seq))

    def value_at(self, asset_id, as_of):
        """Value an asset at any date from its current model. Pure function of ledger inputs."""
        as_of = parse_ts(as_of)
        model = self.db.execute("SELECT method, params FROM valuation_model"
                                " WHERE asset_id = ? AND superseded_seq IS NULL", (asset_id,)).fetchone()
        if not model:
            return Valuation(None, "NO_MODEL", None, "no valuation model attested")
        method, p = model["method"], json.loads(model["params"])

        if method == "APPRAISAL":
            age_days = (as_of - parse_ts(p["as_of"])).days
            status = "STALE" if age_days > p["valid_days"] else "OK"
            return Valuation(p["value_minor"], status, method, f"appraised {p['as_of']}, {age_days} days old")

        if method in SCHEDULE_METHODS:
            years = max(0.0, (as_of - parse_ts(p["basis_date"])).days / 365.25)
            basis, salvage = p["basis_minor"], p["salvage_minor"]
            if method == "STRAIGHT_LINE":
                value = basis - (basis - salvage) * min(1.0, years / p["useful_life_years"])
                detail = f"{years:.1f} of {p['useful_life_years']} years"
            else:
                value = max(salvage, basis * (1 - p["annual_rate_bp"] / 10000) ** years)
                detail = f"{years:.1f} years at {p['annual_rate_bp'] / 100:.0f}%/yr"
            return Valuation(round(value), "OK", method, detail)

        # MARKET_PRICE: latest observation at or before as_of.
        source = self.db.execute("SELECT * FROM price_source WHERE source_id = ?", (p["source_id"],)).fetchone()
        obs = self.db.execute(
            "SELECT * FROM price_observation WHERE source_id = ? AND instrument_id = ? AND observed_at <= ?"
            " ORDER BY observed_at DESC LIMIT 1",
            (p["source_id"], p["instrument_id"], as_of.isoformat())).fetchone()
        if not obs:
            return Valuation(None, "NO_PRICE", method, f"no price for {p['instrument_id']}")
        age = (as_of - parse_ts(obs["observed_at"])).total_seconds()
        status = "STALE" if age > source["max_staleness_seconds"] else "OK"
        price = obs["price_minor"] / 100
        detail = f"{p['quantity']:,} × ${price:,.2f}, priced {obs['observed_at'][:10]}"
        return Valuation(p["quantity"] * obs["price_minor"], status, method, detail, obs["observation_id"])

    # -- covenants -----------------------------------------------------------

    def set_covenant(self, borrower, lender, pledge_id, kind, threshold_bp, haircut_bp=0, warn_margin_bp=1000):
        payload = {"pledge_id": pledge_id, "kind": kind, "threshold_bp": threshold_bp,
                   "haircut_bp": haircut_bp, "warn_margin_bp": warn_margin_bp}
        seq = self._append("COVENANT_SET", pledge_id, payload, [borrower, lender])
        covenant_id = new_id("cov")
        self.db.execute("INSERT INTO covenant VALUES (?,?,?,?,?,?,?)",
                        (covenant_id, pledge_id, kind, threshold_bp, haircut_bp, warn_margin_bp, seq))
        return covenant_id

    def test_covenants(self, asset_id, as_of, reported_balances=None):
        """Run every covenant on an asset's active pledges, senior first.

        reported_balances maps pledge_id -> outstanding balance a lender chose to
        report for its own test. Without one, the pledge's cap is used.
        """
        as_of = parse_ts(as_of)
        reported = reported_balances or {}
        val = self.value_at(asset_id, as_of)
        pledges = self.db.execute(
            "SELECT p.pledge_id, p.priority_rank, p.max_secured_minor, s.legal_name"
            " FROM pledge p JOIN party s ON s.party_id = p.secured_party_id"
            " WHERE p.asset_id = ? AND p.status = 'ACTIVE' ORDER BY p.priority_rank", (asset_id,)).fetchall()
        results, senior_claims = [], 0
        for p in pledges:
            exposure = reported.get(p["pledge_id"], p["max_secured_minor"])
            basis = "REPORTED" if p["pledge_id"] in reported else "CAP"
            for c in self.db.execute("SELECT * FROM covenant WHERE pledge_id = ?", (p["pledge_id"],)).fetchall():
                results.append(self._evaluate(c, p, val, senior_claims, exposure, basis, as_of))
            # Juniors count every senior pledge at its full cap: a revolving senior loan
            # can be drawn back up to the cap, and juniors can't see senior balances.
            senior_claims += p["max_secured_minor"]
        return val, results

    def _evaluate(self, c, p, val, senior_claims, exposure, basis, as_of):
        threshold = c["threshold_bp"] / 10000
        margin = c["warn_margin_bp"] / 10000
        keep = 1 - c["haircut_bp"] / 10000
        lending = available = ratio = breach_value = None

        if val.value_minor is None:
            outcome = val.status
        else:
            lending = round(val.value_minor * keep)
            available = max(0, lending - senior_claims)
            if c["kind"] == "MIN_COVERAGE":
                ratio = available / exposure if exposure else math.inf
                needed = exposure * threshold
                breached, warned = ratio < threshold, ratio < threshold * (1 + margin)
            else:
                ratio = exposure / available if available else (math.inf if exposure else 0.0)
                needed = exposure / threshold
                breached, warned = ratio > threshold, ratio > threshold * (1 - margin)
            # The asset value at which this covenant sits exactly on its threshold.
            breach_value = round((needed + senior_claims) / keep) if keep else None
            outcome = "STALE" if val.status == "STALE" else "BREACH" if breached else "WARN" if warned else "PASS"

        ratio_bp = round(ratio * 10000) if ratio is not None and not math.isinf(ratio) else None
        self.db.execute(
            "INSERT INTO covenant_test VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (new_id("tst"), c["covenant_id"], as_of.isoformat(), val.value_minor, lending, senior_claims,
             available, exposure, basis, ratio_bp, breach_value, outcome, val.price_observation_id, now()),
        )
        return CovenantResult(c["covenant_id"], p["pledge_id"], p["legal_name"], p["priority_rank"], c["kind"],
                              c["threshold_bp"], exposure, basis, available, ratio, breach_value, outcome)

    def projected_breach_date(self, asset_id, result, start, horizon_years=30):
        """For depreciating assets, the first month the value drops below a covenant's breach value."""
        model = self.db.execute("SELECT method FROM valuation_model WHERE asset_id = ? AND superseded_seq IS NULL",
                                (asset_id,)).fetchone()
        if not model or model["method"] not in SCHEDULE_METHODS or result.breach_value_minor is None:
            return None
        t = parse_ts(start)
        if self.value_at(asset_id, t).value_minor < result.breach_value_minor:
            return t
        for _ in range(horizon_years * 12):
            t += timedelta(days=30.44)
            if self.value_at(asset_id, t).value_minor < result.breach_value_minor:
                return t
        return None

    # -- lookups -------------------------------------------------------------

    def public_search(self, asset_id):
        return self.db.execute("SELECT * FROM public_pledge WHERE asset_id = ? AND status = 'ACTIVE'"
                               " ORDER BY priority_rank", (asset_id,)).fetchall()

    def lender_position(self, asset_id):
        return self.db.execute("SELECT * FROM lender_asset_position WHERE asset_id = ?", (asset_id,)).fetchone()
