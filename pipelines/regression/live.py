#!/usr/bin/env python3
"""Module runner for the regression suite (driven by run.sh, see README.md).

  live.py t10 t20 ...            run live modules in order against the running Resolve, then always sweep
                                 the fixture (rlib.drop_scratch through a vendor client, leftover zz_reg_
                                 projects and Media Pool clips, fixture integrity) into the `live_cleanup` report
  live.py --outside ID [ARGS]    run one module that needs no shared session (offline, or starts its own)
  live.py --list                 the module ids found, one per line: "<id> <path> <first docstring line>"
  live.py --record SUITE         write a report from stdin lines "STATUS<TAB>LABEL<TAB>DETAIL"
  live.py --summary [NAME ...]   one line per report in rlib.REPORTS, then every FAIL and WARN; exit 1 on FAIL.
                                 Each NAME (a module id tNN or a report name) must have written a report:
                                 a missing one is a FAIL (a run that was cut short is never a pass)

Module ids are tNN. t10 is ../mcp-benchmark/validate_client.py (its table output is turned into a
report here); every other id is the file tNN_*.py next to this one. A module that exits without writing
a report, or with a non-zero code its report does not explain, gets a FAIL added on its behalf.
"""
import ast
import collections
import json
import os
import re
import signal
import subprocess
import sys
import threading
from pathlib import Path

import rlib

VALIDATE_CLIENT = rlib.MCP_BENCH / "validate_client.py"
MODULE_TIMEOUT = float(os.environ.get("REGRESSION_MODULE_TIMEOUT", "1800"))
STARTED_MARKER = rlib.REPORTS / ".live_started"
CHILD_MARKER = rlib.REPORTS / ".live_child"     # process group of the module running now (run.sh kills it on Ctrl-C)
_child = {"p": None}


class Interrupted(Exception):
    pass
ID_RE = re.compile(r"^t\d\d$")


# ---- modules -------------------------------------------------------------------------------------

def modules():
    """{id: path} of every module present."""
    found = {}
    if VALIDATE_CLIENT.exists():
        found["t10"] = VALIDATE_CLIENT
    for p in sorted(rlib.HERE.glob("t[0-9][0-9]_*.py")):
        mid = p.name[:3]
        if mid != "t10":
            found.setdefault(mid, p)
    return dict(sorted(found.items()))


def first_doc_line(path):
    try:
        doc = ast.get_docstring(ast.parse(path.read_text()))
    except (OSError, SyntaxError):
        doc = None
    return (doc or "").strip().splitlines()[0] if doc else ""


def report_state():
    rlib.REPORTS.mkdir(parents=True, exist_ok=True)
    return {p: p.stat().st_mtime_ns for p in rlib.REPORTS.glob("*.json")}


def write_report(name, results):
    """results: [(label, status, detail)] -> rlib-format report (reuses Suite.finish)."""
    s = rlib.Suite(name)
    s.results = [(l, st, d, 0.0) for l, st, d in results]
    return s.finish()


def append_fail(report, label, detail):
    data = json.loads(report.read_text())
    data["results"].append({"check": label, "status": "FAIL", "detail": detail, "seconds": 0.0})
    data["counts"] = {s: sum(r["status"] == s for r in data["results"]) for s in ("OK", "WARN", "SKIP", "FAIL")}
    report.write_text(json.dumps(data, indent=1))
    print(f"  FAIL {label}: {detail}", flush=True)


def run_child(argv, cwd):
    """Run argv, streaming its output; -> (exit code or None if killed, every output line). After
    MODULE_TIMEOUT seconds the whole process group (the MCP servers it spawned too) is killed."""
    p = subprocess.Popen(argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                         bufsize=1, start_new_session=True)
    _child["p"] = p
    try:
        CHILD_MARKER.write_text(str(p.pid))
    except OSError:
        pass
    lines = []

    def pump():
        for line in p.stdout:
            print(line, end="", flush=True)
            lines.append(line.rstrip("\n"))

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()
    try:
        code = p.wait(timeout=MODULE_TIMEOUT)
    except subprocess.TimeoutExpired:
        code = None
        kill_child(p)
    finally:
        _child["p"] = None
        CHILD_MARKER.unlink(missing_ok=True)
    reader.join(timeout=10)
    return code, lines


def kill_child(p):
    """TERM, then KILL, the module's whole process group (the MCP servers it spawned too)."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(p.pid, sig)
            p.wait(timeout=20)
            return
        except (OSError, subprocess.TimeoutExpired):
            continue


def _on_signal(signum, frame):
    p = _child["p"]
    if p is not None and p.poll() is None:
        kill_child(p)
    raise Interrupted(f"signal {signum}")


def parse_validate_client(lines):
    """The result table validate_client.py prints at the end: '<label> <OK|SKIP|FAIL> <detail>'."""
    rows, in_table = [], False
    for line in lines:
        if re.match(r"^check\s+ok\s+detail\s*$", line):
            in_table, rows = True, []
            continue
        if in_table:
            if not line.strip():
                break
            m = re.match(r"^(.+?)\s+(OK|SKIP|FAIL)(?:\s+(.*))?$", line)
            if m:
                rows.append((m.group(1).strip(), m.group(2), m.group(3) or ""))
    return rows


def run_module(mid, path, args=()):
    """Run one module as a subprocess; make sure it leaves a report that explains its exit code."""
    print(f"\n### {mid}: {path.name}", flush=True)
    before = report_state()
    code, lines = run_child([sys.executable, str(path), *args], cwd=str(path.parent))
    tail = " | ".join(l for l in lines[-6:] if l.strip())[-400:]
    if mid == "t10":
        rows = parse_validate_client(lines)
        if not rows:
            rows = [("validate_client.py", "FAIL", f"no result table (exit {code}): {tail}")]
        elif code not in (0, None) and not any(r[1] == "FAIL" for r in rows):
            rows.append(("validate_client.py exit code", "FAIL", f"exit {code} although no check failed: {tail}"))
        if code is None:
            rows.append(("validate_client.py", "FAIL", f"killed after {MODULE_TIMEOUT:.0f}s"))
        write_report("t10_validate_client", rows)
        return 1 if any(r[1] == "FAIL" for r in rows) else 0
    after = report_state()
    written = [p for p, m in after.items() if before.get(p) != m]
    if not written:
        why = f"killed after {MODULE_TIMEOUT:.0f}s" if code is None else f"exited with code {code}"
        write_report(path.stem, [(path.name, "FAIL", f"{why} without writing a report: {tail}")])
        return 1
    report = max(written, key=lambda p: after[p])
    if code is None:
        append_fail(report, path.name, f"killed after {MODULE_TIMEOUT:.0f}s")
        return 1
    if code != 0 and not json.loads(report.read_text())["counts"]["FAIL"]:
        append_fail(report, path.name, f"exited with code {code} after writing its report: {tail}")
    return 1 if code else 0


def resolve_ids(ids):
    known = modules()
    out = []
    for mid in ids:
        if not ID_RE.match(mid):
            raise SystemExit(f"not a module id: {mid!r} (expected tNN)")
        out.append((mid, known.get(mid)))
    return out


# ---- the live block ------------------------------------------------------------------------------

CLEANUP_SCRIPT = rlib.FIXTURE_PRELUDE + f"""
prefix = {rlib.SCRATCH_PREFIX!r}
leftover = [n for n in (pm.GetProjectListInCurrentFolder() or []) if n.startswith(prefix)]
deleted, kept = [], []
for n in leftover:
    (deleted if pm.DeleteProject(n) else kept).append(n)
base = timeline({rlib.FIXTURE_TIMELINE!r})
items = (base.GetItemListInTrack("video", 1) or []) if base is not None else []
result = {{"projects_deleted": deleted, "projects_kept": kept, "fixture_timeline": base is not None,
          "fixture_items": len(items), "fixture_names": sorted(set(i.GetName() for i in items)),
          "current_project": proj.GetName()}}
"""


def live_cleanup():
    """Always runs after the live modules, even when they failed, crashed or were interrupted."""
    rows = []
    try:
        with rlib.vendor() as v:
            try:
                r = rlib.drop_scratch(v)
                if not r.get("ok"):
                    rows.append(("drop zz_reg_ timelines", "FAIL", f"DeleteTimelines refused: {r}"))
                elif r.get("deleted"):
                    rows.append(("drop zz_reg_ timelines", "WARN",
                                 f"{r['deleted']} scratch timeline(s) were left behind by a module and deleted now"))
                else:
                    rows.append(("drop zz_reg_ timelines", "OK", "no scratch timeline left"))
            except Exception as exc:
                rows.append(("drop zz_reg_ timelines", "FAIL", f"{type(exc).__name__}: {exc}"))
            try:
                r = rlib.drop_scratch_clips(v)
                if not r.get("ok"):
                    rows.append(("drop zz_reg_ media pool clips", "FAIL", f"DeleteClips refused: {r}"))
                elif r.get("deleted"):
                    rows.append(("drop zz_reg_ media pool clips", "WARN",
                                 f"left in the fixture's Media Pool by a module and deleted now: {r['deleted']}"))
                else:
                    rows.append(("drop zz_reg_ media pool clips", "OK", "no scratch clip left"))
            except Exception as exc:
                rows.append(("drop zz_reg_ media pool clips", "FAIL", f"{type(exc).__name__}: {exc}"))
            try:
                r = rlib.run_script(v, CLEANUP_SCRIPT)
                if r["projects_kept"]:
                    rows.append(("zz_reg_ projects", "FAIL", f"could not delete {r['projects_kept']}"))
                elif r["projects_deleted"]:
                    rows.append(("zz_reg_ projects", "WARN", f"left behind by a module, deleted now: {r['projects_deleted']}"))
                else:
                    rows.append(("zz_reg_ projects", "OK", "none left"))
                if not r["fixture_timeline"]:
                    rows.append(("fixture intact", "FAIL", f"{rlib.FIXTURE_TIMELINE} is gone from {rlib.FIXTURE_PROJECT}"))
                elif r["fixture_items"] != 8:
                    rows.append(("fixture intact", "FAIL",
                                 f"{rlib.FIXTURE_TIMELINE} has {r['fixture_items']} video items, expected 8"))
                elif r["fixture_names"] != ["Solid Color"]:
                    rows.append(("fixture intact", "WARN",
                                 f"{rlib.FIXTURE_TIMELINE} item names changed: {r['fixture_names']}"))
                else:
                    rows.append(("fixture intact", "OK", f"{rlib.FIXTURE_TIMELINE}: 8 x Solid Color"))
            except Exception as exc:
                rows.append(("fixture sweep", "FAIL", f"{type(exc).__name__}: {exc}"))
    except Exception as exc:
        rows.append(("vendor client for the clean-up", "FAIL", f"{type(exc).__name__}: {exc}"))
    print("\n### live clean-up", flush=True)
    return write_report("live_cleanup", rows)


def live(ids):
    problem = rlib.session_problem()   # main_guard without the marker: this is what sets it
    if problem:
        print("refusing to run: " + problem, file=sys.stderr)
        return 2
    rlib.REPORTS.mkdir(parents=True, exist_ok=True)
    STARTED_MARKER.write_text(str(os.getpid()))
    os.environ[rlib.LIVE_ENV] = str(os.getpid())   # the modules' main_guard checks it
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, _on_signal)
    worst = 0
    try:
        for mid, path in resolve_ids(ids):
            if path is None:
                write_report(mid, [(mid, "FAIL", "module not found")])
                worst = 1
                continue
            try:
                worst |= run_module(mid, path)
            except Interrupted as exc:
                write_report(path.stem if mid != "t10" else "t10_validate_client",
                             [(path.name, "FAIL", f"interrupted ({exc}): the module was killed")])
                worst = 1
                break
            except Exception as exc:
                write_report(path.stem if mid != "t10" else "t10_validate_client",
                             [(path.name, "FAIL", f"runner error: {type(exc).__name__}: {exc}")])
                worst = 1
    finally:
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            signal.signal(sig, signal.SIG_IGN)   # the clean-up itself must not be cut short by a second signal
        worst |= live_cleanup()
    return worst


# ---- helpers for run.sh --------------------------------------------------------------------------

def outside(mid, args):
    [(mid, path)] = resolve_ids([mid])
    if path is None:
        return write_report(mid, [(mid, "FAIL", "module not found")])
    return run_module(mid, path, args)


def record(name):
    rows = []
    for line in sys.stdin:
        parts = line.rstrip("\n").split("\t", 2)
        if len(parts) == 3 and parts[0] in ("OK", "WARN", "SKIP", "FAIL"):
            rows.append((parts[1], parts[0], parts[2]))
    return write_report(name, rows)


def summary(expected=()):
    present = {p.stem for p in rlib.REPORTS.glob("*.json")}
    missing = []
    for name in expected:
        if ID_RE.match(name):   # a module id: its report is tNN_<stem>, tNN_not_run or tNN (module not found)
            found = any(r == name or r.startswith(name + "_") for r in present)
        else:
            found = name in present
        if not found:
            missing.append(name)
    if missing:
        write_report("missing_reports", [(m, "FAIL", "no report: the module or step never finished (killed or interrupted?)")
                                         for m in missing])
    reports = sorted(rlib.REPORTS.glob("*.json"), key=lambda p: p.stat().st_mtime_ns)
    if not reports:
        print("no reports")
        return 1
    print(f"\n{'module':<28} {'OK':>4} {'WARN':>5} {'SKIP':>5} {'FAIL':>5}")
    total = collections.Counter()
    problems = []
    for p in reports:
        data = json.loads(p.read_text())
        c = data["counts"]
        total.update(c)
        print(f"{data['suite']:<28} {c['OK']:>4} {c['WARN']:>5} {c['SKIP']:>5} {c['FAIL']:>5}")
        problems += [(r["status"], data["suite"], r["check"], r["detail"]) for r in data["results"]
                     if r["status"] in ("FAIL", "WARN")]
    print(f"{'total':<28} {total['OK']:>4} {total['WARN']:>5} {total['SKIP']:>5} {total['FAIL']:>5}")
    for status in ("FAIL", "WARN"):
        rows = [r for r in problems if r[0] == status]
        if rows:
            print(f"\n{status}:")
            for _, suite, check, detail in rows:
                print(f"  {suite} :: {check}: {detail}")
    print(f"\nreports: {rlib.REPORTS}")
    return 1 if total["FAIL"] else 0


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0 if argv else 2
    if argv[0] == "--list":
        for mid, path in modules().items():
            print(mid, path, first_doc_line(path))
        return 0
    if argv[0] == "--record":
        return record(argv[1])
    if argv[0] == "--summary":
        return summary([a for a in argv[1:] if a != "--"])
    if argv[0] == "--outside":
        return outside(argv[1], [a for a in argv[2:] if a != "--"])
    return live([a for a in argv if a != "--"])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
