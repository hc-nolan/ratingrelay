#!/usr/bin/env bash
# Serve the demo database: API on :8199, Vite on :5174. Nothing here is real —
# scripts/demo.py seeds a throwaway database and stubs the two status checks.
#
#     scripts/demo-stack.sh                  # serve until Ctrl-C
#     scripts/demo-stack.sh --wait-ready CMD # serve, wait for both ports, run CMD
#
# `just demo` and `just screenshots` are the two callers, so the demo has one
# definition of its ports rather than one per recipe.
#
# Each server starts in its own session, and cleanup kills that session's
# process group by negative pid. `kill 0`, which Ctrl-C-then-clean recipes
# usually reach for, signals the *caller's* group: run this from an agent shell
# or `bash -c` and that takes the shell with it.
set -euo pipefail

ROOT="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
cd "$ROOT"

port_busy() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }

# Refuse to start on top of something else, rather than let the caller's probes
# answer from a stack this script did not start: `just screenshots` on top of a
# demo already running used to photograph the stale instance and exit 0.
for port in 8199 5174; do
    if port_busy "$port"; then
        echo "demo-stack: something is already listening on :$port, stop it first" >&2
        exit 1
    fi
done

setsid uv run python scripts/demo.py >/dev/null 2>&1 &
api_pid=$!
setsid bash -c "cd '$ROOT/frontend' && BACKEND_URL='http://localhost:8199' pnpm exec vite dev --port 5174 --strictPort" >/dev/null 2>&1 &
web_pid=$!

alive() { kill -0 "$api_pid" 2>/dev/null && kill -0 "$web_pid" 2>/dev/null; }

cleanup() {
    kill -- "-$api_pid" "-$web_pid" 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if [ "${1:-}" = "--wait-ready" ]; then
    shift
    ready=0
    for _ in $(seq 1 60); do
        if ! alive; then
            echo "demo-stack: a server exited; is :8199 or :5174 in use?" >&2
            exit 1
        fi
        if curl -fsS http://localhost:8199/api/auth/status >/dev/null 2>&1 &&
            curl -fsS http://localhost:5174/ >/dev/null 2>&1; then
            # Both answered, but a server that is about to die (a bind it lost) has
            # not exited yet; give it the moment it needs before trusting them.
            sleep 1
            alive || exit 1
            ready=1
            break
        fi
        sleep 1
    done
    if [ "$ready" -eq 0 ]; then
        echo "demo-stack: the demo stack did not come up within 60s" >&2
        exit 1
    fi
fi

if [ "$#" -gt 0 ]; then
    "$@"
else
    wait
fi
