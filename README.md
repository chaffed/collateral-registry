# Collateral Registry

A design sketch for a shared registry, organized by asset, that lets lenders check instantly whether an asset is already pledged, fully or in part, before lending against it.

Third parties attest that assets exist and assign standard asset IDs. Borrower and lender both sign each pledge. Contracts stay private, but any lender can verify a copy against its recorded hash. All history is append-only and hash-chained.

## Contents

| Path | What it is |
|---|---|
| [docs/01-concept.md](docs/01-concept.md) | The problem, the idea, what it does and doesn't guarantee, whether blockchain is needed |
| [docs/02-data-model.md](docs/02-data-model.md) | Entities, relationships, fingerprints, priority rules, lifecycles, who sees what |
| [docs/03-workflows.md](docs/03-workflows.md) | Sequence diagrams: register, attest, pledge, check, release, receivables, key compromise |
| [docs/04-risks-and-open-questions.md](docs/04-risks-and-open-questions.md) | Threats and mitigations, legal and product questions |
| [docs/05-valuation.md](docs/05-valuation.md) | Valuation models (depreciation, market price, appraisal) and covenant tests |
| [docs/06-partial-pledges.md](docs/06-partial-pledges.md) | Pledging shares and other divisible assets in blocks by quantity |
| [schema/registry.sql](schema/registry.sql) | Reference schema (SQLite) |
| [examples/registry.py](examples/registry.py) | Reference implementation used by both demos |
| [examples/demo.py](examples/demo.py) | Walk-through: register, attest, pledge, check, release |
| [examples/valuation_demo.py](examples/valuation_demo.py) | Walk-through: depreciation, market prices, covenant tests, partial pledges of shares |

Diagrams are written in Mermaid. They render on GitHub, in VS Code with a Mermaid extension, and in most Markdown viewers.

## Run the demos

Requires Python 3.9+, no packages.

```bash
python3 examples/demo.py             # pledging and checking
python3 examples/valuation_demo.py   # valuation and covenants
```

`demo.py` runs this scenario: Acme Paving registers an excavator, a dealer attests its identity, and an appraiser values it at $120,000. First Bank lends against it with an $80,000 cap. Acme then tries to register the same machine under a different description and is rejected. River Credit searches the registry, sees First Bank's pledge, and checks the contract Acme shows it. A doctored copy fails the check. River lends $30,000 in second position and moves up to first when First Bank's pledge is released. Finally, the ledger's hash chain is verified and an attempt to edit history is blocked.

```
== 5. Acme tries to register the same excavator again, worded differently
   REJECTED: asset already registered as ast_9db8eabd2c

== 6. River Credit searches by asset ID before lending
   rank 1: First Bank  recorded 2026-10-30T22:32:53+00:00  UCC-1 IA #2026-118842
   valuation $120,000, pledged caps $80,000, indicative headroom $40,000

== 7. Acme shows River the First Bank contract
   genuine copy matches registered hash: True
   doctored copy (cap changed to $20,000) matches: False
```

`valuation_demo.py` values the same excavator on a 7-year depreciation schedule and shows the second-position lender breaching its covenant a year before the first. It then pledges 100,000 restricted shares in blocks to two lenders, rejects a third lender asking for more shares than are free, and tests each block against its own loan-to-value covenant as the price falls. Finally it runs a 2-for-1 split, a margin-call top-up, a partial release, and catches an overpledge:

```
== 3. A third lender asks for 20,000 shares
   REJECTED: only 10,000 units are unpledged; 20,000 requested

== 4. Daily tests as the share price falls — each block is tested on its own shares
   ...
   2026-11-04  value $2,500,000  (100,000 × $25.00, priced 2026-11-04)  [OK]
      60,000 sh  Harbor Bank              57.1% vs max 50% LTV   BREACH  exposure $600,000 cap, breaks below $1,714,286
      30,000 sh  Summit Private Credit    51.3% vs max 50% LTV   BREACH  exposure $250,000 cap, breaks below $769,231
   Harbor Bank breaches below $28.57 per share
   Summit Private Credit breaches below $25.64 per share
```

The demos simulate signatures with HMAC so they have no dependencies. A real system would use public-key signatures such as Ed25519.

## Status

Concept and reference model only. It isn't legal advice and doesn't replace UCC filing, which lenders still need for legal priority.

## License

Licensed under the [Apache License 2.0](LICENSE).
