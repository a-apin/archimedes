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

AUTH = 2
"""No valid cached session (run ``archimedes login`` first), or the server rejected it.
Deliberately the SAME value as ``USAGE``, not a new code: a missing/expired session means
the command could not run to a verdict at all, exactly like a bad argument — it is not a
real answer about a strategy the way ``GATE_FAILED`` is. Kept as a separate name (rather
than spelling ``USAGE`` at every auth call site) purely so the reason is legible in the
code that raises it."""

NOT_IMPLEMENTED = 3
"""The subcommand exists in the command tree but has no implementation in this
release. 0.0.1 returned this for every subcommand; 0.1.0 narrows it to ``backtest``
and ``verify --local``, which still need the not-yet-published local execution engine."""

__all__ = ["AUTH", "GATE_FAILED", "NOT_IMPLEMENTED", "OK", "USAGE"]
