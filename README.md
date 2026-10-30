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
| [schema/registry.sql](schema/registry.sql) | Reference schema (SQLite) |
| [examples/demo.py](examples/demo.py) | Runnable walk-through of the whole flow |

Diagrams are written in Mermaid. They render on GitHub, in VS Code with a Mermaid extension, and in most Markdown viewers.

## Run the demo

Requires Python 3.9+, no packages.

```bash
python3 examples/demo.py
```

The demo runs this scenario: Acme Paving registers an excavator, a dealer attests its identity, and an appraiser values it at $120,000. First Bank lends against it with an $80,000 cap. Acme then tries to register the same machine under a different description and is rejected. River Credit searches the registry, sees First Bank's pledge, and checks the contract Acme shows it. A doctored copy fails the check. River lends $30,000 in second position and moves up to first when First Bank's pledge is released. Finally, the ledger's hash chain is verified and an attempt to edit history is blocked.

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

The demo simulates signatures with HMAC so it has no dependencies. A real system would use public-key signatures such as Ed25519.

## Status

Concept and reference model only. It isn't legal advice and doesn't replace UCC filing, which lenders still need for legal priority.
