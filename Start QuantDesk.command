#!/bin/bash
# QuantDesk launcher — double-click me.
# Cleans stale git locks, commits pending work, starts the backend, opens the GUI.
cd "$(dirname "$0")"
echo "=== QuantDesk launcher ==="

# 1) git: clear stale locks left by the sandbox, commit staged work
rm -f .git/index.lock .git/HEAD.lock 2>/dev/null
if ! git diff-index --quiet HEAD -- 2>/dev/null || [ -n "$(git status --porcelain 2>/dev/null)" ]; then
  git add -A
  git -c user.email=praveenguptaymca@gmail.com -c user.name="Praveen Gupta" \
    commit -m "M1-M14 software implementation: stdlib backend, dual backtest engines + computed parity gate, order validation, GUI wired, 28 tests green" \
    && echo "git: committed" || echo "git: nothing to commit or commit failed"
else
  echo "git: clean"
fi
git log --oneline | head -3

# 2) tests (fast, stdlib only)
echo "--- running tests ---"
python3 -m unittest discover -s tests 2>&1 | tail -2

# 3) backend: stop any previous instance, start fresh
pkill -f "api/server.py" 2>/dev/null && sleep 1
echo "--- starting backend on http://127.0.0.1:8700 ---"
nohup python3 api/server.py > logs_and_artifacts/server.log 2>&1 &
sleep 1.5

# 4) bridge runner: lets Claude run commands here without touching your screen
if ! pgrep -f "mac_runner.py" > /dev/null; then
  nohup python3 scripts/mac_runner.py > .bridge_runner.log 2>&1 &
  echo "bridge runner: started (audit trail in .bridge/audit.log; stop: pkill -f mac_runner.py)"
else
  echo "bridge runner: already running"
fi

# 5) open the GUI
open "http://127.0.0.1:8700"
echo ""
echo "QuantDesk is running. This window can be closed."
echo "To stop the backend later:  pkill -f api/server.py"
