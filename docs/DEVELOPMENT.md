# Development

Install `requirements-dev.txt`, then run `python scripts/check.py`. The script
never logs into Discord. Tests use synthetic accounts and temporary files.
Python 3.9 and 3.12 are the locally tested versions for this candidate; CI also
checks 3.13. A passing offline suite does not replace a test-server smoke test.

Keep source in `cogs/` and `szofie/`, regressions in `tests/`, and ordinary tools
in `scripts/`. `bot.py` contains the extension list and lifecycle handling.
The generated catalogue can be refreshed with:

```sh
python scripts/render_public_catalog.py
```

Every gameplay change should keep defaults, configuration validation, guides,
catalogue entries and tests consistent. Prices must be integer donut amounts.
Preserve atomic writes and restart-safe deadlines. Acknowledge Discord interactions
before potentially slow I/O, and finish the acknowledged response exactly once.
Limit total embed payloads, bind actionable pagers to their requesters, and
revalidate inventory at commitment rather than only at preview time.

Use the same odds and costs for every account. Do not introduce identity-specific
gameplay exceptions. Administrative balance adjustments must remain explicit and
audited. Protect tokens and player records: never commit `.env`, `data/`, `logs/`,
`backups/`, local environments or generated reports.

Add your own legally distributable artwork with provenance; tests must continue
to pass without optional images. Do not add generated visual references containing
private deployment information.
