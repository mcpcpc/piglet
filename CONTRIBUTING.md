# Contributing to piglet

Thanks for your interest. Bug reports, fixes and focused improvements are welcome. For a new
feature or a change in behaviour, open an issue first so we can agree on the approach before you
spend time on it.

Report security issues privately as described in [SECURITY.md](SECURITY.md), not in an issue.

## Scope

piglet re-implements the behaviour of the [Pi coding agent](https://github.com/earendil-works/pi)
as a GUI and keeps Pi's on-disk formats (sessions, settings, `auth.json`, `models.json`), so
data can move between the two. Changes that break that compatibility need a strong reason.

Sign-in flows are added only for providers whose terms allow a third-party app to use them;
piglet does not reuse other applications' OAuth client IDs.

## Setup

You need Python 3.12 or newer and Node.js 22.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
npm --prefix web ci
npm --prefix web run build         # writes src/piglet/server/static
```

See the README's Development section for running the UI dev server and the desktop app.

## Checks

CI runs these on every pull request; run them before you push:

```bash
ruff format .
ruff check .
python -m unittest discover

npm --prefix web run check         # biome + tsc
npm --prefix web test              # vitest
npm --prefix web run build
```

If you change `src/piglet/server/protocol.py`, regenerate the TypeScript types with
`python scripts/gen_protocol_ts.py` and commit the result. If you change `desktop/`, run
`npm --prefix desktop ci && npm --prefix desktop run check`.

## Conventions

- Keep core logic separate from I/O (network, filesystem, subprocesses) so it can be tested
  without live services.
- Public Python functions get type hints and a Google-style docstring; ruff enforces this.
- Tests use `unittest` and `unittest.mock`. They must not use the network, real API keys, your
  home directory or the wall clock, and must pass in any order.
- Pin exact versions for any new dependency, and explain in the pull request why it is needed.
- Keep personal data out of code, tests and fixtures; use `example.invalid` addresses.

## Pull requests

- One logical change per pull request, branched from `main` as `feat/…`, `fix/…` or `chore/…`.
- Title it `<prefix>: <imperative summary>`, for example `fix: keep the drawer open on reload`.
  Pull requests are squash-merged, so the title becomes the commit message.
- For UI changes, include before and after screenshots, including a phone-width one.

By contributing, you agree that your contributions are licensed under the project's
[BSD 3-Clause License](LICENSE).
