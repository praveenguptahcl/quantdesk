#!/usr/bin/env python3
"""Nightly/manual backup: dumps the sqlite store + configs to backups/ (M14)."""
import json, os, shutil, sys, time, urllib.request
stamp = time.strftime("%Y%m%d-%H%M%S")
dest = os.path.join(os.path.dirname(__file__), "..", "backups", stamp)
os.makedirs(dest, exist_ok=True)
try:
    data = json.load(urllib.request.urlopen("http://127.0.0.1:8700/api/backup"))
    json.dump(data, open(os.path.join(dest, "state.json"), "w"), indent=1)
except Exception:
    db = os.environ.get("QD_DB", os.path.join(os.path.dirname(__file__), "..", "quantdesk.db"))
    if os.path.exists(db): shutil.copy(db, dest)
for f in ("risk/limits.yaml", ".env.example"):
    src = os.path.join(os.path.dirname(__file__), "..", f)
    if os.path.exists(src): shutil.copy(src, dest)
print("backup written to", dest, "- copy it OFF this machine")
