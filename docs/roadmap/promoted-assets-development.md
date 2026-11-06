# Reviewed immutable asset promotion: development evidence

- **Status:** completed
- **Owning queue item:** [PROMOTE-ASSETS-001](active-work.md#x-promote-assets-001---preserve-pinned-immutable-assets-through-reviewed-source-promotion)
- **Completion / archival evidence:** [Captured source and installed observations](../evidence/promoted-assets-r1.json)

PROMOTE-ASSETS-001 implements the signed local asset handoff within
[issue 74](https://github.com/jordanhubbard/literate-ai/issues/74).

The tested source candidate is `d45e47395d1ed14cf83768ae2e7894cfd6d2f422`.
Ten asset contract and CLI tests, nine existing promotion tests and four existing
CLI regression tests pass. The asset tests cover the published JSON Schema,
signed graph custody, exact nontext bytes, current lock bindings, CAS admission,
immutable assembly, legacy identities, rejection controls and atomic publication.

The offline wheel has SHA-256
`6d3853d78615a68ff7d53a1b11aa0912b7e3a8409654c09c50cae59dbc793b07`.
It was built from the exact commit archive with SHA-256
`470f2e22dacb5b4ff3dff3877c78b0097063ce216551e1dcd871bc227e7d51e1`.
A fresh private environment installed it using only captured local dependency
wheels. The installed distribution identity is
`sha256:2a9dc2d0ed02fb40af82534d9720822607f51de7dcf8f8305e0be10b7e416dee`.
All ten copied asset fixtures pass without skips. All 544 imported framework
modules originate inside that installed package, and its public CLI help command
passes. The existing fixture explicitly selects root project authority; it uses
the real installed signature, promotion, lock, source-exclusion and assembly code.
No source checkout is on the runtime import path.

The complete public-export scan exceeded its 120-second development bound.
The exact remaining process was observed and terminated. That failure is
preserved. A separate check of all changed content passes the same export rules
and finds no new em dashes. Parent source authority and driver review are current.
No full framework release, downstream native replacement or scientific
qualification is claimed by these development checks. Broader issue 74 work
remains open. No push, publication or external message was performed.
