"""nqq.cli — command line: run the arena, browse history, verify bundles, serve.

    python3 cli.py arena                 # run the arena, print the honest board
    python3 cli.py arena --html b.html   # write the board to an HTML file
    python3 cli.py history               # list frozen (signed) weeks
    python3 cli.py verify <week>         # verify a week's signed bundle
    python3 cli.py doctor                # deep self-check
    python3 cli.py serve                 # start the web UI + JSON API
Zero third-party deps.
"""
import sys

import arena
import kernel


def _board_text(board):
    out = [f"nqq arena — week {board['week']} (durable ranking = deflated Sharpe)", ""]
    out.append(f"{'#':>2}  {'agent':<22}{'operator':<10}{'rawSR':>7}{'dSR':>7}"
               f"{'CI':>18}{'cap':>12}  verdict")
    for r in board["rows"]:
        ci = f"{r['ci']['lo']}..{r['ci']['hi']}"
        cap = (f"${r['capacity_aum']/1e6:.1f}M" if r["capacity_aum"] >= 1e6
               else f"${r['capacity_aum']:,.0f}")
        out.append(f"{r['rank']:>2}  {r['display']:<22}{r['operator']:<10}"
                   f"{r['raw_sharpe']:>7.2f}{r['deflated_sharpe']:>7.2f}{ci:>18}"
                   f"{cap:>12}  {r['verdict']}")
    out += ["", "! " + board["banner"]]
    return "\n".join(out)


def _board_html(board):
    rows = "".join(
        f"<tr><td>{r['rank']}</td><td>{r['display']}</td><td>{r['operator']}</td>"
        f"<td>{r['raw_sharpe']:.2f}</td><td>{r['deflated_sharpe']:.2f}</td>"
        f"<td>{r['ci']['lo']}…{r['ci']['hi']}</td><td>{r['verdict']}</td></tr>"
        for r in board["rows"])
    return (f"<!doctype html><meta charset=utf-8><title>nqq arena</title>"
            f"<body style='background:#070D19;color:#E6EDF7;font-family:monospace'>"
            f"<h2>nqq arena — week {board['week']}</h2>"
            f"<p style='color:#F5A623'>{board['banner']}</p>"
            f"<table border=1 cellpadding=6 style='border-collapse:collapse'>"
            f"<tr><th>#<th>agent<th>operator<th>raw SR<th>deflated SR<th>CI<th>verdict</tr>"
            f"{rows}</table></body>")


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__); return 0
    cmd = argv[0]
    store = kernel.Store()
    if cmd == "arena":
        arena.seed(store)
        board = arena.standings(store, recompute=True)
        if "--html" in argv:
            path = argv[argv.index("--html") + 1]
            open(path, "w").write(_board_html(board))
            print(f"wrote {path}")
        else:
            print(_board_text(board))
    elif cmd == "history":
        weeks = store.get("static", "arena_weeks") or []
        if not weeks:
            print("no frozen weeks — run: python3 cli.py arena, then finalize in Admin")
        for w in weeks:
            print(f"  {w['week']}  {w['verdict']}  sig {w['bundle_sig']}")
    elif cmd == "verify":
        if len(argv) < 2:
            print("usage: verify <week>"); return 1
        b = store.get("bundles", f"week-{argv[1]}")
        if not b:
            print("no bundle for that week"); return 1
        ok, reason = kernel.verify_bundle(b)
        print(f"week {argv[1]}: {'VERIFIED ✔' if ok else 'FAILED ✗ — ' + reason}")
        return 0 if ok else 1
    elif cmd == "doctor":
        import doctor
        return doctor.run()
    elif cmd == "serve":
        import server
        server.main()
    else:
        print(f"unknown command: {cmd}"); print(__doc__); return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
