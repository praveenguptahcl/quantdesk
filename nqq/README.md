# nqq — honest quant, rendered beautifully

The honesty spine of a rigorous quant arena (deflated Sharpe, multiplicity control,
capacity, EdgeCertainty, the human-AI Council) inside a gamified, zero-dependency shell.
Correct quant statistics that are also engaging and taught.

## Run (out of the box)
```bash
cd nqq && ./run.sh          # starts the server AND opens the browser at :8900
# or:  make run   |   make multiuser   |   make test   |   make doctor
```
That's it — **no install, no dependencies**. Python 3.9+ stdlib + one HTML file.
Real daily bars ship in `data/catalog/`, so it's honest ("real point-in-time") out of the
box; if the catalog is ever missing it falls back to deterministic synthetic data.

### Configure (all optional env vars)
| var | default | meaning |
|---|---|---|
| `NQQ_HOST` / `NQQ_PORT` | `127.0.0.1` / `8900` | bind address |
| `NQQ_MULTIUSER` | `0` | `1` = community mode (login required; seed admin `admin/changeme`) |
| `NQQ_DB` / `NQQ_HOME` | `~/.nqq/nqq.db` | sqlite path / state dir (holds the 0600 signing key) |
| `NQQ_NO_DEMO` | `0` | `1` = don't seed sample data |

### Verify the install
```bash
make doctor    # deep self-check: catalog present, honesty invariants hold,
               # signed bundle round-trips, tampered one fails, ledger verifies
make test      # 24 invariant unit tests
```
`GET /api/health` returns `{ok, ledger_ok, real_data, catalog[]}` for uptime checks.

### Deploy
It's a single stdlib process. Run it behind a reverse proxy (nginx/Caddy) with TLS; set
`NQQ_MULTIUSER=1`, `NQQ_TLS=1` (marks the session cookie `Secure`), and change the admin
password on first login — the default `admin/changeme` is blocked from all data routes
until you do (footer → "change password"). State is one sqlite file (`NQQ_DB`) — back it up.

**Scaling — read this before running more than one instance.** nqq is designed as a
**single writer**. The append-only stamp ledger hash-chains each row to the previous one,
so two processes appending to the same DB concurrently would *fork the chain* and
`verify_ledger` would (correctly) report it broken. For a shared deployment:

- Run **one** app instance (scale the reverse proxy, not the app), or shard by tenant with
  a DB per instance. The in-process lock + sqlite WAL/`busy_timeout` handle concurrency
  **within** one process, not across hosts.
- If you must run multiple instances, they **must** share one `NQQ_DEPLOY_KEY` (set it as a
  managed secret) so HMAC bundle-authenticity checks agree across nodes, and share the
  session store — otherwise a login on node A isn't recognized on node B.
- Sessions and the ledger live in the sqlite `kv`/`stamps` tables; there is no external
  session cache. Horizontal scale therefore needs a shared DB **and** single-writer
  discipline. Don't put nqq behind a naive round-robin LB with per-node sqlite files.

## What makes it honest (see the Learn screen in-app)
- **Deflated Sharpe, not raw PnL** — the durable ranking; falls as you try more (trial ledger).
- **EdgeCertainty** — an earned probability from six axes, hard-capped for synthetic data
  (≤0.50) and no forward record (≤0.70), with a **veto verdict** any fatal axis can trigger.
- **FDR control** — an IC that doesn't survive Benjamini-Hochberg is noise dressed as edge.
- **Capacity** — every edge's AUM ceiling before its own market impact eats it.
- **Point-in-time** — a lookahead guard turns silent optimism into a loud error.
- **Signed bundles** — every frozen week is HMAC-signed and replayable; a tampered bundle fails.
- **The Council** — before a trade, every signal voice testifies FOR/AGAINST/NEUTRAL with a
  track record and an evidence link; no combined probability while voices are unrated.

See `PLAN.md` for the module & screen map, and
`../PLATFORM_REVIEW_zingq_alphaforge_vs_quantdesk.md` for the comparison that motivated it.
