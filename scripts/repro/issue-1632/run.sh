#!/usr/bin/env bash
# A/B runner for issue #1632 — see README.md.
#
#   ./run.sh                 both variants, REPRO_DURATION_S each (default 1800)
#   ./run.sh binary          just the psycopg2-binary (prod) variant
#   ./run.sh source          just the psycopg2-from-source variant
#
# Exit status is 0 whether or not a crash happened: a crash IS the result, and
# a non-repro is a result too. Read the verdict block it prints at the end.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${HERE}/../../.." && pwd)"
LOGDIR="${REPRO_LOGDIR:-${HERE}/.logs}"
CERTDIR="${HERE}/.certs"
DURATION_S="${REPRO_DURATION_S:-1800}"
PROJECT="repro1632"

mkdir -p "${LOGDIR}"

step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

# ---------------------------------------------------------------- TLS cert --
# postgres:18-alpine has no openssl CLI, so the self-signed pair is made here
# and mounted; initdb-ssl.sh copies it into PGDATA with the perms postgres
# demands. Regenerated only when missing.
if [[ ! -f "${CERTDIR}/server.key" ]]; then
  step "generating self-signed TLS cert for the repro postgres"
  mkdir -p "${CERTDIR}"
  openssl req -new -x509 -days 30 -nodes -text \
    -out "${CERTDIR}/server.crt" -keyout "${CERTDIR}/server.key" \
    -subj "/CN=postgres" >/dev/null 2>&1 || { echo "openssl failed"; exit 1; }
  chmod 644 "${CERTDIR}/server.crt" "${CERTDIR}/server.key"
fi

# ------------------------------------------------------------------ images --
build_binary() {
  step "building the prod image (psycopg2-binary) — backend/Dockerfile, repo root context"
  docker build -f "${REPO_ROOT}/backend/Dockerfile" -t archimedes-repro1632:binary "${REPO_ROOT}" \
    >"${LOGDIR}/build-binary.log" 2>&1 \
    || { echo "build failed — see ${LOGDIR}/build-binary.log"; exit 1; }
}

build_source() {
  step "building the A/B image (psycopg2 from source)"
  local ver
  ver="$(docker run --rm archimedes-repro1632:binary python -c 'import psycopg2; print(psycopg2.__version__.split()[0])' | tr -d '\r')"
  echo "matching psycopg2 version: ${ver}"
  docker build --progress=plain -f "${HERE}/Dockerfile.psycopg2-source" \
    --build-arg BASE_IMAGE=archimedes-repro1632:binary \
    --build-arg "PSYCOPG2_VERSION=${ver}" \
    -t archimedes-repro1632:source "${HERE}" \
    >"${LOGDIR}/build-source.log" 2>&1 \
    || { echo "build failed — see ${LOGDIR}/build-source.log"; exit 1; }
}

# ------------------------------------------------------------------- run 1 --
run_variant() {
  local variant="$1"
  local image="archimedes-repro1632:${variant}"
  local log="${LOGDIR}/run-${variant}.log"
  local proj="${PROJECT}-${variant}"

  step "running variant: ${variant}  (${DURATION_S}s budget) → ${log}"

  # Drop this variant's previous verdict BEFORE running. Without it, a run that
  # dies before writing one leaves the old file in place and the A/B summary
  # reports a stale result as if it were this run's — the exact "trusted a
  # signal for something it does not measure" failure this harness exists to
  # avoid. Sibling variants' verdicts are deliberately left alone so `run.sh
  # source` can still be summarised next to an earlier `run.sh binary`.
  rm -f "${LOGDIR}/verdict-${variant}.txt"

  REPRO_IMAGE="${image}" docker compose -p "${proj}" -f "${HERE}/docker-compose.repro.yml" down -v --remove-orphans >/dev/null 2>&1

  local started ended rc
  started="$(date +%s)"
  REPRO_IMAGE="${image}" REPRO_DURATION_S="${DURATION_S}" \
    docker compose -p "${proj}" -f "${HERE}/docker-compose.repro.yml" \
    run --rm --no-TTY harness >"${log}" 2>&1
  rc=$?
  ended="$(date +%s)"

  REPRO_IMAGE="${image}" docker compose -p "${proj}" -f "${HERE}/docker-compose.repro.yml" down -v --remove-orphans >/dev/null 2>&1

  local elapsed=$(( ended - started ))
  local writes execmany
  writes="$(grep -o 'writes=[0-9]*' "${log}" | tail -1 | cut -d= -f2)"
  writes="${writes:-0}"
  execmany="$(grep -o 'do_executemany calls: [0-9]*' "${log}" | tail -1 | grep -o '[0-9]*$')"
  execmany="${execmany:-$(grep -o 'executemany=[0-9]*' "${log}" | tail -1 | cut -d= -f2)}"
  execmany="${execmany:-0}"

  {
    echo "variant        : ${variant}"
    # Dated, because `run.sh source` prints this next to a binary verdict that
    # may have been produced hours earlier.
    echo "run finished   : $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "exit code      : ${rc}   $( [[ ${rc} -eq 134 || ${rc} -eq 139 ]] && echo '<-- ABORT/SEGV' )"
    echo "wall clock     : ${elapsed}s of ${DURATION_S}s budget"
    echo "cache writes   : ${writes}"
    # The load-bearing number: prod aborted INSIDE do_executemany. Zero here
    # means the run never entered the frame under test, so "no crash" proves
    # nothing at all.
    echo "do_executemany : ${execmany}"
    [[ "${execmany}" -eq 0 ]] && echo "                 ^^ ZERO — crash frame never entered; this run is a NON-RESULT"
    # Read the count the harness itself computed. Counting the indented path
    # lines instead over-reports 3x, because the fingerprint is printed three
    # times per run (before import, after import, end of run) — a wrong number
    # that looks authoritative.
    echo -n "libssl copies  : "
    grep -o 'mappings loaded: [0-9]*' "${log}" | tail -1 | grep -o '[0-9]*$' || echo "?"
    if grep -q 'Fatal Python error' "${log}"; then
      echo "CRASHED        : YES"
      grep -n 'Fatal Python error' -A 25 "${log}" | head -40
    else
      echo "CRASHED        : no"
    fi
  } | tee "${LOGDIR}/verdict-${variant}.txt"
}

TARGET="${1:-both}"

case "${TARGET}" in
  binary) build_binary; run_variant binary ;;
  source) build_binary; build_source; run_variant source ;;
  both)   build_binary; build_source; run_variant binary; run_variant source ;;
  *) echo "usage: $0 [binary|source|both]"; exit 2 ;;
esac

step "A/B summary"
cat "${LOGDIR}"/verdict-*.txt
