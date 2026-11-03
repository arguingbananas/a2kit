"""Tiny helpers so every test script fails loudly (non-zero exit) when a check fails."""
import sys

FAILS = []


def expect(cond, what):
    """Record a check. Prints FAILED and remembers it when cond is false."""
    if not cond:
        FAILS.append(what)
        print("  FAILED: " + what)
    return bool(cond)


def finish(name):
    """Print a one-line summary and exit 1 if anything failed."""
    if FAILS:
        print("%s: %d check(s) FAILED: %s" % (name, len(FAILS), "; ".join(FAILS)))
        sys.exit(1)
    print("%s: all checks passed" % name)
