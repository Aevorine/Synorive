# Contributing to Synorive

Thanks for taking a look. This file is short on purpose — it only covers the things that are
genuinely non-obvious about this repository.

## Getting a working checkout

```bash
npm install                                   # Node ≥20
py -3.13 -m venv engine/.venv                 # Python ≥3.11 (3.13 is what is tested)
engine/.venv/Scripts/python.exe -m pip install -e engine
npm run dev
```

**You do not need to generate fonts first.** `npm run dev` and `npm run build` run
`scripts/ensure-fonts.mjs`, which writes a fallback `fonts.css` if the real one is missing.
Headings fall back to a system serif until you run `python scripts/build_fonts.py` — the real
subsetted fonts are 6 MB and deliberately not committed.

## Before you open a pull request

Run these three. They are fast and they are what CI-equivalent checks would run:

```bash
npm run typecheck                             # all workspaces
node scripts/check-hardcoded-style.mjs        # design tokens + the typography anchor
engine/.venv/Scripts/python.exe -m py_compile <files you changed>
```

## Things that will get a change sent back

- **Hard-coded colours or font sizes.** Everything goes through design tokens in
  `packages/design-tokens`. `check-hardcoded-style.mjs` enforces this, including a check that the
  tokens themselves still say "body = 16px, Times New Roman first in the family".
- **A performance claim without a measurement.** Numbers in the README come from the benchmark
  scripts in `engine/tests/`. If you change something that moves a number, re-run the benchmark
  and update the table — including if the number got worse.
- **Silently dropping data.** Filtered search results are folded with a reason and stay
  recoverable; they are never deleted. The same applies to engine failures: an adapter that fails
  must say *why*, not return an empty list.
- **Collapsing "rate limited" into "broken".** A search engine returning a captcha and a search
  engine whose HTML changed need opposite responses. `ParseOutcome` has four states for this
  reason; please keep using them.

## Reporting a bug

Please include: your OS, whether you installed the setup exe / portable exe / built from source,
and — if the engine failed to start — the text of the error panel. That panel is written to name
the actual failing step, so pasting it verbatim saves a round trip.

## Security

Do not open a public issue for a security problem. See [SECURITY.md](SECURITY.md).

## License

By contributing you agree that your contributions are licensed under
[AGPL-3.0-or-later](LICENSE), the same as the project.
