#!/usr/bin/env python3
"""QuantDesk bridge runner — lets Claude execute commands on this Mac without
touching your screen.

How it works: Claude (from its sandbox) writes shell scripts into
`.bridge/queue/` in this project folder (which is shared). This runner watches
that folder, executes each script with bash from the project root, and writes
the output to `.bridge/results/`. Claude reads the results back through the
shared folder. You keep full visibility and control:

  - every command + output is appended to  .bridge/audit.log  (human-readable)
  - it only runs scripts from this project's .bridge/queue directory
  - stop it any time:  pkill -f mac_runner.py   (or close its Terminal window)

Started automatically by "Start QuantDesk.command". Stdlib only.
"""
import json
import os
import subprocess
import sys
import time

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
BRIDGE = os.path.join(ROOT, ".bridge")
QUEUE = os.path.join(BRIDGE, "queue")
RESULTS = os.path.join(BRIDGE, "results")
DONE = os.path.join(BRIDGE, "done")
AUDIT = os.path.join(BRIDGE, "audit.log")
PIDFILE = os.path.join(BRIDGE, "runner.pid")
TIMEOUT = 300  # seconds per command


def log(msg):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(AUDIT, "a") as f:
        f.write(line + "\n")


def already_running():
    if not os.path.exists(PIDFILE):
        return False
    try:
        pid = int(open(PIDFILE).read().strip())
        os.kill(pid, 0)
        return True
    except (ValueError, ProcessLookupError, PermissionError):
        return False


def main():
    for d in (QUEUE, RESULTS, DONE):
        os.makedirs(d, exist_ok=True)
    if already_running():
        print("bridge runner already running — exiting")
        return
    open(PIDFILE, "w").write(str(os.getpid()))
    log(f"bridge runner started (pid {os.getpid()}) — watching {QUEUE}")
    log("stop with: pkill -f mac_runner.py")
    try:
        while True:
            for name in sorted(os.listdir(QUEUE)):
                if not name.endswith(".sh"):
                    continue
                path = os.path.join(QUEUE, name)
                script = open(path).read()
                log(f"RUN {name}:\n{script.strip()}")
                t0 = time.time()
                try:
                    p = subprocess.run(["bash", path], cwd=ROOT, timeout=TIMEOUT,
                                       capture_output=True, text=True)
                    result = {"name": name, "code": p.returncode,
                              "stdout": p.stdout[-20000:], "stderr": p.stderr[-20000:],
                              "seconds": round(time.time() - t0, 2)}
                except subprocess.TimeoutExpired:
                    result = {"name": name, "code": -1, "stdout": "",
                              "stderr": f"timeout after {TIMEOUT}s",
                              "seconds": TIMEOUT}
                with open(os.path.join(RESULTS, name + ".json"), "w") as f:
                    json.dump(result, f, indent=1)
                os.replace(path, os.path.join(DONE, f"{int(time.time())}-{name}"))
                log(f"DONE {name}: exit {result['code']} in {result['seconds']}s")
            time.sleep(2)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            os.remove(PIDFILE)
        except OSError:
            pass
        log("bridge runner stopped")


if __name__ == "__main__":
    main()
