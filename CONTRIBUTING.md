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
| `make smoke` | Audio conversion and the command-line tests, about 10 s | After every change |
| `make lint` | `ruff` checks the Python for likely bugs | Before committing |
| `./a2kit test` | The whole suite, about three minutes | Before opening a PR |
| `python3 -m pip_audit -r requirements-dev.txt` | Known vulnerabilities in the dependencies | When dependencies change |

Tests never need an Apple II: they use a 6502 simulator, fake serial ports and
pseudo-terminals. Anything tried on real hardware goes in the Installation Guide's
*Tested setup* section.

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

## Security

Report vulnerabilities privately, as described in [SECURITY.md](SECURITY.md). Never
commit secrets, credentials or personal data.
