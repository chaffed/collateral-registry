-- Collateral Registry — reference data model (SQLite dialect).
--
-- Two layers:
--   1. ledger_event / event_signature: the append-only, hash-chained source of truth.
--   2. Everything else: current-state tables derived from the ledger, kept for fast lookup.
-- If the state tables are ever lost or disputed, they can be rebuilt by replaying the ledger.
--
-- Money is stored in minor units (cents) as integers. Timestamps are ISO 8601 UTC text.

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------
-- Participants
-- ---------------------------------------------------------------------------

CREATE TABLE party (
    party_id         TEXT PRIMARY KEY,
    legal_name       TEXT NOT NULL,
    registration_id  TEXT,                 -- LEI, EIN, or state entity number
    jurisdiction     TEXT,                 -- e.g. 'US-DE'
    status           TEXT NOT NULL DEFAULT 'ACTIVE'
                     CHECK (status IN ('ACTIVE', 'SUSPENDED', 'REVOKED')),
    created_at       TEXT NOT NULL
);

-- One party can hold several roles (a bank can borrow and lend).
CREATE TABLE party_role (
    party_id    TEXT NOT NULL REFERENCES party(party_id),
    role        TEXT NOT NULL CHECK (role IN ('BORROWER', 'LENDER', 'ATTESTER', 'OPERATOR')),
    granted_at  TEXT NOT NULL,
    revoked_at  TEXT,
    PRIMARY KEY (party_id, role)
);

-- Signing keys. Keys rotate and can be compromised, so they are never stored on the party row.
-- compromised_since lets verifiers flag every signature made after a key was stolen.
CREATE TABLE party_key (
    key_id             TEXT PRIMARY KEY,
    party_id           TEXT NOT NULL REFERENCES party(party_id),
    algorithm          TEXT NOT NULL,      -- 'ed25519' in production; 'demo-hmac' in the demo
    public_key         TEXT NOT NULL,
    valid_from         TEXT NOT NULL,
    revoked_at         TEXT,
    compromised_since  TEXT
);

-- Which attesters may vouch for which asset classes, and the bond backing their word.
CREATE TABLE attester_accreditation (
    accreditation_id  TEXT PRIMARY KEY,
    party_id          TEXT NOT NULL REFERENCES party(party_id),
    asset_class       TEXT NOT NULL REFERENCES asset_class(code),
    attestation_type  TEXT NOT NULL,
    bond_minor        INTEGER,             -- surety bond or insurance cover
    currency          TEXT,
    valid_from        TEXT NOT NULL,
    valid_to          TEXT,
    revoked_at        TEXT
);

-- ---------------------------------------------------------------------------
-- Assets and attestations
-- ---------------------------------------------------------------------------

-- Each class defines which fields identify an asset uniquely and which
-- attestation must exist before the asset can be pledged.
CREATE TABLE asset_class (
    code                  TEXT PRIMARY KEY,
    name                  TEXT NOT NULL,
    identity_fields       TEXT NOT NULL,   -- JSON array of field names used for the fingerprint
    required_attestation  TEXT NOT NULL
);

INSERT INTO asset_class (code, name, identity_fields, required_attestation) VALUES
    ('EQUIPMENT',     'Serial-numbered equipment',
                      '["manufacturer","serial_number"]', 'IDENTITY'),
    ('VEHICLE',       'Titled motor vehicle',
                      '["vin"]', 'IDENTITY'),
    ('RECEIVABLE',    'Account receivable (invoice)',
                      '["seller_registration_id","obligor_registration_id","invoice_number","amount_minor","currency","issue_date"]',
                      'OBLIGOR_CONFIRMATION'),
    ('WAREHOUSE_LOT', 'Inventory held under a warehouse receipt',
                      '["warehouse_registration_id","receipt_number"]', 'EXISTENCE');

-- The asset record. The fingerprint is a keyed hash of the normalized identity
-- fields: the same physical asset always produces the same fingerprint, however
-- the borrower words the description. UNIQUE(asset_class, fingerprint) is what
-- stops one asset being registered twice under different descriptions.
CREATE TABLE asset (
    asset_id         TEXT PRIMARY KEY,     -- the canonical asset ID lenders search by
    asset_class      TEXT NOT NULL REFERENCES asset_class(code),
    fingerprint      TEXT NOT NULL,
    owner_party_id   TEXT NOT NULL REFERENCES party(party_id),
    description      TEXT,                 -- free text for humans; never used for matching
    status           TEXT NOT NULL DEFAULT 'ACTIVE'
                     CHECK (status IN ('ACTIVE', 'DISPUTED', 'RETIRED')),
    registered_seq   INTEGER NOT NULL REFERENCES ledger_event(seq),
    UNIQUE (asset_class, fingerprint)
);

-- A signed statement by a third party about an asset.
CREATE TABLE attestation (
    attestation_id     TEXT PRIMARY KEY,
    asset_id           TEXT NOT NULL REFERENCES asset(asset_id),
    attester_party_id  TEXT NOT NULL REFERENCES party(party_id),
    attestation_type   TEXT NOT NULL CHECK (attestation_type IN
                       ('EXISTENCE', 'IDENTITY', 'OWNERSHIP', 'OBLIGOR_CONFIRMATION', 'VALUATION')),
    statement_hash     TEXT NOT NULL,      -- hash of the full statement kept off-ledger
    valuation_minor    INTEGER,            -- only for VALUATION
    currency           TEXT,
    attested_seq       INTEGER NOT NULL REFERENCES ledger_event(seq),
    expires_at         TEXT,               -- valuations and inspections go stale
    revoked_at         TEXT,
    revocation_reason  TEXT
);

-- ---------------------------------------------------------------------------
-- Pledges
-- ---------------------------------------------------------------------------

-- A security interest in one asset. The contract itself stays off-ledger;
-- only its salted hash is recorded, so outsiders can check a copy they are
-- shown without the registry publishing the terms.
CREATE TABLE pledge (
    pledge_id           TEXT PRIMARY KEY,
    asset_id            TEXT NOT NULL REFERENCES asset(asset_id),
    borrower_party_id   TEXT NOT NULL REFERENCES party(party_id),
    secured_party_id    TEXT NOT NULL REFERENCES party(party_id),
    max_secured_minor   INTEGER NOT NULL,  -- cap on what this pledge can claim
    currency            TEXT NOT NULL,
    contract_hash       TEXT NOT NULL,     -- sha256(salt || contract bytes)
    contract_uri        TEXT,              -- access-controlled off-ledger location
    ucc_state           TEXT,              -- matching UCC-1 filing, for legal perfection
    ucc_file_number     TEXT,
    effective_at        TEXT NOT NULL,
    maturity_at         TEXT,
    status              TEXT NOT NULL DEFAULT 'ACTIVE'
                        CHECK (status IN ('ACTIVE', 'RELEASED', 'DISPUTED', 'VOID')),
    recorded_seq        INTEGER NOT NULL REFERENCES ledger_event(seq),  -- sets default priority
    priority_rank       INTEGER,           -- derived: 1 = senior; NULL once released
    released_seq        INTEGER REFERENCES ledger_event(seq)
);

-- An earlier lender agreeing to rank behind a later one. Signed by the lender giving up rank.
CREATE TABLE subordination (
    subordination_id  TEXT PRIMARY KEY,
    asset_id          TEXT NOT NULL REFERENCES asset(asset_id),
    senior_pledge_id  TEXT NOT NULL REFERENCES pledge(pledge_id),  -- moves ahead
    junior_pledge_id  TEXT NOT NULL REFERENCES pledge(pledge_id),  -- agrees to rank behind
    recorded_seq      INTEGER NOT NULL REFERENCES ledger_event(seq)
);

-- Who may read a pledge's full contract (beyond the public summary).
CREATE TABLE access_grant (
    grant_id             TEXT PRIMARY KEY,
    pledge_id            TEXT NOT NULL REFERENCES pledge(pledge_id),
    grantee_party_id     TEXT NOT NULL REFERENCES party(party_id),
    granted_by_party_id  TEXT NOT NULL REFERENCES party(party_id),
    granted_at           TEXT NOT NULL,
    expires_at           TEXT,
    revoked_at           TEXT
);

CREATE TABLE dispute (
    dispute_id      TEXT PRIMARY KEY,
    subject_type    TEXT NOT NULL CHECK (subject_type IN ('ASSET', 'ATTESTATION', 'PLEDGE')),
    subject_id      TEXT NOT NULL,
    raised_by       TEXT NOT NULL REFERENCES party(party_id),
    reason          TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'UPHELD', 'REJECTED')),
    raised_seq      INTEGER NOT NULL REFERENCES ledger_event(seq),
    resolved_seq    INTEGER REFERENCES ledger_event(seq),
    resolution      TEXT
);

-- ---------------------------------------------------------------------------
-- Ledger (source of truth)
-- ---------------------------------------------------------------------------

-- Every change is an event. Each event's hash covers the previous event's hash,
-- so altering or removing any past event breaks every hash after it.
CREATE TABLE ledger_event (
    seq           INTEGER PRIMARY KEY,
    event_id      TEXT NOT NULL UNIQUE,
    event_type    TEXT NOT NULL CHECK (event_type IN (
                    'PARTY_ADMITTED', 'KEY_ADDED', 'KEY_REVOKED',
                    'ASSET_REGISTERED', 'ATTESTED', 'ATTESTATION_REVOKED',
                    'PLEDGE_RECORDED', 'PLEDGE_AMENDED', 'SUBORDINATED', 'PLEDGE_RELEASED',
                    'ACCESS_GRANTED', 'DISPUTE_RAISED', 'DISPUTE_RESOLVED')),
    subject_id    TEXT NOT NULL,           -- asset_id, pledge_id, party_id, ...
    payload       TEXT NOT NULL,           -- canonical JSON (sorted keys, no whitespace)
    payload_hash  TEXT NOT NULL,           -- sha256(payload)
    prev_hash     TEXT NOT NULL,           -- event_hash of seq - 1; 64 zeros for the first event
    event_hash    TEXT NOT NULL,           -- sha256(seq | prev_hash | payload_hash | recorded_at)
    recorded_at   TEXT NOT NULL
);

-- Each party that must agree to an event signs its payload_hash.
-- A pledge needs the borrower and the lender; a release needs the lender.
CREATE TABLE event_signature (
    seq        INTEGER NOT NULL REFERENCES ledger_event(seq),
    party_id   TEXT NOT NULL REFERENCES party(party_id),
    key_id     TEXT NOT NULL REFERENCES party_key(key_id),
    signature  TEXT NOT NULL,
    PRIMARY KEY (seq, party_id)
);

-- Optional: periodically publish a Merkle root of a block of events to a public
-- blockchain. That makes the operator's history independently checkable without
-- putting any registry data on-chain.
CREATE TABLE anchor (
    anchor_id    TEXT PRIMARY KEY,
    from_seq     INTEGER NOT NULL,
    to_seq       INTEGER NOT NULL,
    merkle_root  TEXT NOT NULL,
    chain        TEXT NOT NULL,            -- e.g. 'bitcoin', 'ethereum'
    tx_id        TEXT NOT NULL,
    anchored_at  TEXT NOT NULL
);

CREATE TRIGGER ledger_event_no_update BEFORE UPDATE ON ledger_event
BEGIN SELECT RAISE(ABORT, 'ledger_event is append-only'); END;
CREATE TRIGGER ledger_event_no_delete BEFORE DELETE ON ledger_event
BEGIN SELECT RAISE(ABORT, 'ledger_event is append-only'); END;
CREATE TRIGGER event_signature_no_update BEFORE UPDATE ON event_signature
BEGIN SELECT RAISE(ABORT, 'event_signature is append-only'); END;
CREATE TRIGGER event_signature_no_delete BEFORE DELETE ON event_signature
BEGIN SELECT RAISE(ABORT, 'event_signature is append-only'); END;

-- ---------------------------------------------------------------------------
-- Indexes and views
-- ---------------------------------------------------------------------------

CREATE INDEX pledge_by_asset      ON pledge (asset_id, status, priority_rank);
CREATE INDEX attestation_by_asset ON attestation (asset_id);
CREATE INDEX event_by_subject     ON ledger_event (subject_id);

-- What anyone can see: whether an asset is pledged, to whom, and in what order.
-- Amounts and contract terms are left out.
CREATE VIEW public_pledge AS
SELECT p.pledge_id,
       p.asset_id,
       p.priority_rank,
       sp.legal_name AS secured_party,
       p.status,
       e.recorded_at,
       p.ucc_state,
       p.ucc_file_number
FROM pledge p
JOIN party sp       ON sp.party_id = p.secured_party_id
JOIN ledger_event e ON e.seq = p.recorded_seq;

-- What admitted lenders can additionally see: caps, latest valuation, and the
-- indicative unpledged headroom. Headroom is a hint, not a guarantee: valuations
-- go stale and caps are maximums, not balances.
CREATE VIEW lender_asset_position AS
SELECT a.asset_id,
       a.asset_class,
       a.status,
       (SELECT v.valuation_minor FROM attestation v
         WHERE v.asset_id = a.asset_id AND v.attestation_type = 'VALUATION'
           AND v.revoked_at IS NULL
         ORDER BY v.attested_seq DESC LIMIT 1)                    AS latest_valuation_minor,
       COALESCE((SELECT SUM(p.max_secured_minor) FROM pledge p
                  WHERE p.asset_id = a.asset_id AND p.status = 'ACTIVE'), 0) AS pledged_caps_minor,
       (SELECT COUNT(*) FROM pledge p
         WHERE p.asset_id = a.asset_id AND p.status = 'ACTIVE')   AS active_pledges
FROM asset a;
