# Risks and open questions

## Threats and mitigations

| Threat | Example | Mitigation | Remaining risk |
|---|---|---|---|
| Fake asset | Invoice for goods never shipped; equipment that doesn't exist | Required attestation per asset class; customer confirms receivables | Attester lies or colludes |
| Same asset pledged twice in the registry | Same excavator described two ways | Normalized keyed fingerprint + `UNIQUE` constraint | Identity fields themselves forged (re-stamped serial plate) |
| Pledge made outside the registry | Borrower goes to a lender that doesn't check | Lender agreement to check before funding; borrower confirms in the contract that no other pledges exist | Real until checking is market practice or required by law |
| Hidden pledge | Borrower doesn't show a lender the earlier contract | Search by asset ID returns every pledge | None within the registry |
| Doctored contract | Cap changed from $80k to $20k | Salted contract hash check | None, if the lender actually checks |
| Bogus filing | Someone pledges an asset they don't own | Borrower and lender must both sign; owner must match | Stolen borrower key |
| Rewritten history | Operator backdates a pledge to change who ranks first | Hash chain, append-only triggers, public anchoring | Short window between anchors |
| Dishonest attester | Paid by the borrower to approve | Accreditation, bonds, liability, payment by lenders or the registry, two attesters for large assets | Collusion between several parties |
| Stolen key | Attacker signs attestations | `compromised_since`, revocation, re-attestation | Damage done before discovery |
| Guessing pledged invoices | Hash likely invoice values, compare | Fingerprint uses a secret key held by the operator | Operator compromise exposes the key |
| Stale valuation | Equipment worth less a year later | Depreciation schedules; appraisals expire; STALE overrides PASS | Real-world wear differs from the schedule |
| Bad or manipulated price | Feed publishes a wrong price, or a thinly traded stock is pushed up | Signed prices, accredited sources, staleness limits, haircuts | One source is a single point of failure; use the median of several |
| Pledged shares sold outside the registry | Holder sells 50,000 shares already pledged | Transfer agent re-attests the holding; overpledge freezes the asset and opens a dispute | Lag until the next attestation |
| Inflated depreciation schedule | Appraiser sets an unrealistically long useful life | Accreditation, bonds, re-appraisal requirements | Collusion between borrower and appraiser |

## Open questions

### Legal

1. **Priority.** Recording in this registry doesn't give priority under UCC Article 9. Lenders must still file a UCC-1. Could a state, or a group of states, recognize the registry as a filing office for specific asset classes? The Cape Town Convention aircraft registry is the model.
2. **Article 12.** For digital and tokenized assets, UCC Article 12 already lets a lender perfect its interest by holding control of the asset. Does the registry act as the control mechanism for those?
3. **Attester liability.** What standard of care applies to an attester? Who can sue them, and for how much?
4. **Disputes.** Who decides contested pledges, and how quickly? Arbitration under registry rules is the likely answer.

### Product

5. **Should caps be public, or visible to admitted lenders only?** Public caps help everyone price risk. Borrowers may object to showing their credit lines.
6. **How should headroom be shown?** As a number, a band, or not at all? A number invites lenders to over-rely on it.
7. **Who pays?** A per-pledge fee from lenders keeps attesters independent of borrowers.
8. **Search by fingerprint.** Should anyone be able to search using identity fields, or only admitted lenders? Open search makes it easier to check whether specific invoices are pledged.
9. **Should seniors share balances with juniors?** Junior tests count senior pledges at their full cap, which is safe but harsh. A senior could choose to share its balance with named juniors through an access grant.
10. **Should breaches be ledger events?** A signed breach notice proves when each party was told, which matters for cure periods.

### Operating model

11. **Who runs it?** An industry association, a utility like DTCC, a state, or a startup. Anchoring to a public chain reduces how much participants have to trust the operator.
12. **What gets lenders to join?** It's useless until the major lenders in one asset class all check it. Recent double-pledging losses are the strongest pitch.
13. **Which asset class first?** Equipment finance (serial numbers, existing dealer networks) or receivables finance (where double-pledging hurts most).

## Not legal advice

This is a design sketch for discussion. Check legal questions with a secured-transactions lawyer.
