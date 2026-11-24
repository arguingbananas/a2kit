# Contributing to a2kit

## Set up

```bash
python3 -m venv .venv && source .venv/bin/activate
python3 -m pip install -r requirements-dev.txt -c constraints.txt   # exact tested versions
```

You also need cc65 and make to rebuild the 6502 programs and run the tests, and pandoc
with Chrome or Chromium to rebuild the PDF docs. See the
[Installation Guide](docs/INSTALL.md).

## Check your work

| Command | What it does | When |
|---|---|---|
| `make smoke` | Audio conversion and the command-line tests, under 30 s | After every change |
| `make lint` | `ruff` checks the Python for likely bugs, and `shellcheck` (if installed) the `a2kit` script | Before committing |
| `./a2kit test` | The whole suite: about 15 minutes on a 2011 desktop, most of it the video and examples tests (a CI job takes about 5) | Before opening a PR |
| `python3 -m pip_audit -r requirements-dev.txt` | Known vulnerabilities in the dependencies | When dependencies change |
| `make test PYTHON=/path/to/python3.11` | The whole suite on the oldest supported Python, from a separate environment with `requirements-dev.txt` (no constraints) | When changing imports or Python features |

Tests never need an Apple II: they use a 6502 simulator, fake serial ports and
pseudo-terminals. Anything tried on real hardware goes in the Installation Guide's
*Tested setup* section.

GitHub runs the same checks on every pull request and every push to `main`
(`.github/workflows/test.yml`), on Python 3.11, 3.12, 3.13 and 3.14: `doctor`, `make lint`, a clean
rebuild of the 6502 programs and disks, which must match the committed ones byte for
byte, and `make test`. The 3.12 job installs exactly `constraints.txt`; the others
install the newest packages, except ruff, which is pinned in every job so a new ruff
release can't fail lint by itself. A PR shows a tick or a cross for each Python. A cross that
says the binaries differ means a change in `runtime/` was committed without the files
`./a2kit build` makes from it.

There is no code formatter, on purpose: the code is formatted by hand, and `ruff format`
would rewrite 1,175 of the 8,400 lines, in every one of the 38 Python files (measured
24 November 2026), mostly hand-aligned comments and hand-wrapped long calls. Don't
reformat files wholesale; `.editorconfig` sets the indentation for new lines (4 spaces
in Python, tabs in the Makefile).

## Branches and pull requests

- Work on a branch off `main`: `feature/<name>` for code, `docs/<name>` for docs only.
- Open a pull request into `main`. PRs are merged with a merge commit, not squashed or rebased.
- Before opening a PR on a long-lived branch, merge `main` into it and rerun the tests there.

## Commits

- Start the subject with the area it touches, then what changed: `ping: switch on
  low-latency mode`, `Roadmap: merge order with the speech branch`, or `Add a2kit
  canary: …` for something new.
- Use the body to say why, and what was tested, including on real hardware.
- Commits made with an AI assistant end with a `Co-Authored-By:` line naming it.

## Built files and docs

- The built 6502 programs (`*.bin`) and boot disks (`*.dsk`) are committed, so people
  without cc65 can use a2kit. After changing anything in `runtime/`, run `./a2kit build`
  and commit the rebuilt files with the source. Never edit them by hand.
- The PDFs in `docs/` are generated. After changing a guide, run `python3 docs/make_pdfs.py`
  and commit the PDFs with it. Git can't merge PDFs, so if two branches both change them,
  rebuild them after the merge instead of choosing a side.
- If behaviour changes, update the README and the guides in the same PR.

## Versions and the changelog

a2kit's version is `VERSION` in `tools/a2hdr.py`, printed by `./a2kit --version` and `./a2kit doctor`.
Each PR that users would notice adds a line under *Unreleased* in [CHANGELOG.md](CHANGELOG.md).
To release, rename *Unreleased* to the new version and date, start a fresh *Unreleased*, and raise
`VERSION` in the same PR:

- the last number (1.0.**1**) for fixes that change no behaviour you'd rely on;
- the middle one (1.**1**.0) for new features, options or commands;
- the first (**2**.0.0) when something that worked before needs changing: a file format, an option, or a
  new receiver that old disks can't stand in for.

## Security

Report vulnerabilities privately, as described in [SECURITY.md](SECURITY.md). Never
commit secrets, credentials or personal data.
