"""Exit codes.

These are an API. Someone will write ``archimedes verify returns.csv`` into a CI
job and branch on the result, so the meaning of each number has to stay fixed
from 0.0.1 onward. New conditions get new codes; existing codes do not get
redefined.

The split that matters is 1 vs everything else. Exit 1 means the command ran to
completion and the answer was "this does not pass" — a real verdict about the
strategy. Any other non-zero code means the command did not produce a verdict at
all, so a CI job that treats every non-zero as "strategy rejected" would be
reporting a network timeout as a research finding.
"""

OK = 0
"""The command did what it was asked. For ``verify``, the gate passed."""

GATE_FAILED = 1
"""The gate ran and returned a failing verdict. This is a real answer, not an error."""

USAGE = 2
"""Bad arguments or a missing file. Click exits with this on its own; it is named here
so the full set is documented in one place."""

NOT_IMPLEMENTED = 3
"""The subcommand exists in the command tree but has no implementation in this
release. Every subcommand returns this in 0.0.1."""

__all__ = ["GATE_FAILED", "NOT_IMPLEMENTED", "OK", "USAGE"]
