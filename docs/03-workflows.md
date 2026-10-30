# Workflows

Each workflow below matches a step in [`examples/demo.py`](../examples/demo.py).

## 1. Register and attest an asset

```mermaid
sequenceDiagram
    actor B as Borrower
    participant R as Registry
    actor AT as Attester (dealer)
    actor AP as Appraiser

    B->>R: register(class=EQUIPMENT, manufacturer, serial, description)
    R->>R: normalize identity fields, compute keyed fingerprint
    alt fingerprint already exists
        R-->>B: rejected: already registered as ast_…
    else new
        R->>R: append ASSET_REGISTERED (signed by borrower)
        R-->>B: asset_id
    end
    AT->>AT: inspect machine, check serial plate
    AT->>R: attest(asset_id, IDENTITY, statement) — signed
    R->>R: check attester is accredited for EQUIPMENT/IDENTITY
    R->>R: append ATTESTED
    AP->>R: attest(asset_id, VALUATION, $120,000) — signed
```

An asset can't be pledged until its class's required attestation exists (`asset_class.required_attestation`).

## 2. Record a pledge

```mermaid
sequenceDiagram
    actor B as Borrower
    actor LA as Lender A
    participant R as Registry
    participant U as State UCC office

    B->>LA: loan request for asset_id
    LA->>R: search(asset_id)
    R-->>LA: no active pledges, valuation $120k
    B->>LA: sign security agreement
    LA->>LA: generate salt, contract_hash = sha256(salt ‖ contract)
    B->>R: sign PLEDGE_RECORDED payload
    LA->>R: sign PLEDGE_RECORDED payload
    R->>R: append event, insert pledge, recompute ranks
    R-->>LA: pledge_id, rank 1
    LA->>U: file UCC-1 (still needed for legal perfection)
    LA->>R: record UCC-1 file number on pledge
```

The registry needs both signatures before it accepts a pledge. That alone stops bogus filings.

## 3. A second lender checks before lending

This is the step the registry exists for.

```mermaid
sequenceDiagram
    actor B as Borrower
    actor LB as Lender B
    participant R as Registry

    B->>LB: loan request, describes the excavator
    LB->>R: search by asset_id (or by identity fields → fingerprint)
    R-->>LB: rank 1: Lender A, recorded 2026-10-30, UCC-1 IA #2026-118842
    R-->>LB: valuation $120k, pledged caps $80k, headroom ~$40k
    LB->>B: show me Lender A's agreement
    B->>LB: contract + salt
    LB->>LB: sha256(salt ‖ contract)
    LB->>R: compare with recorded contract_hash
    R-->>LB: match ✓ (a doctored copy would fail)
```

The search must be by asset, not by the contract the borrower hands over. A borrower who hides a pledge simply won't show that contract. Searching by asset ID returns every pledge anyway.

If the borrower gives a new description instead of the asset ID, Lender B can submit the identity fields (manufacturer + serial). The registry computes the fingerprint and finds the existing record.

## 4. Second-position pledge

```mermaid
sequenceDiagram
    actor B as Borrower
    actor LB as Lender B
    participant R as Registry

    B->>R: sign PLEDGE_RECORDED (cap $30k)
    LB->>R: sign PLEDGE_RECORDED (cap $30k)
    R->>R: rank by recorded order → Lender B is rank 2
    R-->>LB: rank 2 behind Lender A, headroom now ~$10k
```

To jump ahead of Lender A, Lender B would need Lender A to sign a `SUBORDINATED` event.

## 5. Release

```mermaid
sequenceDiagram
    actor LA as Lender A
    participant R as Registry

    LA->>R: sign PLEDGE_RELEASED(pledge_id)
    R->>R: check signer is the secured party
    R->>R: append event, pledge → RELEASED, recompute ranks
    Note over R: Lender B moves up to rank 1
```

The released pledge stays in the history. Only its status changes.

## 6. Receivables variant

For invoices, the attester is the **customer who owes the money**. That's the only party that can confirm the invoice is real.

```mermaid
sequenceDiagram
    actor S as Seller (borrower)
    actor C as Customer (obligor)
    participant R as Registry
    actor F as Factor / lender

    S->>R: register RECEIVABLE(seller, customer, invoice #, amount, date)
    R->>C: please confirm this invoice
    C->>R: OBLIGOR_CONFIRMATION — signed
    F->>R: search → unpledged, confirmed by customer
    S->>R: sign pledge
    F->>R: sign pledge
    Note over S,F: A second factor submitting the same invoice gets a duplicate rejection
```

This blocks the two classic factoring frauds: **fake invoices** (the customer won't confirm) and **the same invoice sold twice** (duplicate fingerprint).

## 7. Compromised attester key

```mermaid
sequenceDiagram
    actor AT as Attester
    participant R as Registry
    actor L as Lenders

    AT->>R: report key stolen since 2026-09-01
    R->>R: append KEY_REVOKED, set compromised_since
    R->>R: find attestations signed with that key after that date
    R-->>L: flag affected assets for re-attestation
```

Nothing is rewritten. The affected attestations are flagged, and lenders decide whether to require a new one.
