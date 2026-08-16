# archimedes-cli

Command-line access to the Archimedes rigor gate.

A backtest with a Sharpe of 2.1 tells you very little on its own. If it was the best of
four hundred variants you tried, most of that number is selection, not skill. The gate
here is the standard correction for that: a deflated Sharpe ratio that prices in how many
variants were tried, a probability of backtest overfitting, a walk-forward out-of-sample
pass, and a look-ahead audit. Point it at a returns series and it tells you which of those
four the series survives.

## Status

**0.0.1 is a name reservation.** The command tree is in place and the flags are settled,
but every subcommand exits 3 with a message saying so. Nothing here opens a network
connection.

The first working release is 0.1.0, with `login`, `meter`, and `verify` against the hosted
API. `verify --local` and `backtest` follow once the local execution engine is published
separately.

## Install

```bash
pip install archimedes-cli
```

Python 3.10 or newer. Two dependencies, both small, so this is a seconds-long install
rather than a compiler-and-a-coffee one.

## Commands

```
archimedes login                      authenticate with a wallet signature
archimedes meter                      show calls used and USDC spent this period
archimedes verify RETURNS_CSV         run the gate over a returns series
archimedes backtest --strategy-path   run a strategy locally and print its returns
```

`RETURNS_CSV` is two columns, date and daily return, or `-` to read from stdin. So the
whole loop is one line:

```bash
archimedes backtest --strategy-path mine.py --strategy-class Mine | archimedes verify -
```

Every command takes `--json` and prints a single object on stdout, including on its error
paths. A script never has to parse prose to find out what happened.

`verify --local` runs the gate on your machine with no network, no account, and no charge.

## Exit codes

These are stable from 0.0.1 onward.

| Code | Meaning |
| --- | --- |
| 0 | The gate passed |
| 1 | The gate ran and returned a failing verdict |
| 2 | Bad arguments or a missing file |
| 3 | Subcommand not implemented in this release |

The line worth drawing is 1 against everything else. Exit 1 is a real answer about the
strategy. Any other non-zero code means no verdict was produced at all, so a CI job that
treats every failure as "strategy rejected" would report a network timeout as a research
finding. Branch on 1 specifically:

```bash
archimedes verify returns.csv
case $? in
  0) echo "gate passed" ;;
  1) echo "gate failed, not deploying"; exit 1 ;;
  *) echo "verify did not run"; exit 2 ;;
esac
```

## Your code stays on your machine

Archimedes never executes strategy code it received over the wire, and `backtest` is local
for that reason rather than as a performance choice.

Producing returns from a strategy file means importing and running it. Any server that
offered to do that for you would be running arbitrary code from strangers. So the split is
drawn at the file boundary: `backtest` runs your Python on your machine and emits a returns
series, and only that series is ever sent anywhere.

The two places the hosted service touches code at all are read-only. The look-ahead audit
parses the file to an AST and inspects it. Strategy constants are read with `ast.parse` and
`literal_eval`. Neither one imports the module or evaluates an expression.

If you would rather nothing leave your machine at all, `verify --local` closes that loop
too.

## Links

- [Repository](https://github.com/a-apin/archimedes)
- [Issues](https://github.com/a-apin/archimedes/issues)
- [archimedes-arc.com](https://archimedes-arc.com)

Released into the public domain under the [Unlicense](https://unlicense.org).
