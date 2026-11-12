#!/usr/bin/env python3
"""Offline install and API-surface baseline checks (no running Resolve needed).

1. The installed Resolve packages agree with each other and with the version baked into the binary.
2. The Resolve binary resolves all of its shared libraries.
3. The community server checkout: git describe, npm bootstrapper and server versions, local changes, and
   the two local patches every upgrade must carry (restart_resolve_app relaunches with -nogui on Linux;
   npm allowScripts approves the installed better-sqlite3).
4. API surface baselines in baselines/: the scripting stub's methods, their parameters and the option
   TypedDict keys; the vendor ResolveMCP tools and their input properties; the community compound tools,
   their properties, actions and the capabilities the live checks rely on (rlib.COMMUNITY_CAPS); the
   versions. Something REMOVED since the baseline is a FAIL (an upgrade dropped or renamed it: review the
   callers, then accept with `run.sh --offline --update-baselines`); something ADDED is a WARN; identical
   is OK. A whole source that a baseline records (ResolveMCP, the stub, the community venv) going missing
   is a FAIL too, never a SKIP. Every documented community action must also appear as a string literal
   in the server source (the dispatch), or it is a FAIL.

Usage: t00_install.py [--update-baselines]
"""
import os
import argparse
import ast
import json
import mmap
import re
import subprocess
import sys

import rlib
from rlib import BASELINES, COMMUNITY_REPO, Skip, Warn, expect

RESOLVE_BIN = rlib.Path("/opt/resolve/bin/resolve")
VENDOR_MCP = rlib.Path("/opt/resolve/bin/ResolveMCP")
STUB = rlib.Path("/opt/resolve/Developer/Scripting/DaVinciResolveScript.pyi")
COMMUNITY_PY = COMMUNITY_REPO / "venv" / "bin" / "python"
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+\.\d+$")
# An action line in a community tool description: indented "  action_name(args) -> {...}" or a
# bullet "- action_name(args) - ...", with alternatives on the same line as " / other_action(...)".
# Usage examples such as "  timeline(action=\"action_help\", ...)" call the tool itself, not an action.
ACTION_LINE = re.compile(r"^(?: +(?:- )?|- )([a-z_][a-z0-9_]*)\((?!\s*action\s*=)")
ACTION_ALT = re.compile(r"\s/\s*([a-z_][a-z0-9_]*)\(")

_cache = {}


def cached(fn):
    def wrapper():
        if fn.__name__ not in _cache:
            try:
                _cache[fn.__name__] = (True, fn())
            except Exception as exc:
                _cache[fn.__name__] = (False, exc)
        ok, value = _cache[fn.__name__]
        if not ok:
            raise value
        return value
    wrapper.__name__ = fn.__name__
    return wrapper


def sh(argv, cwd=None, timeout=60, env=None):
    r = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)
    if r.returncode != 0:
        raise RuntimeError(f"{' '.join(map(str, argv))} -> rc {r.returncode}: {(r.stderr or r.stdout).strip()[:300]}")
    return r.stdout


# ── facts ────────────────────────────────────────────────────────────────────────────────────

@cached
def resolve_packages():
    """Installed davinci-resolve* packages -> {name: version}."""
    try:
        out = sh(["dpkg-query", "-W", "-f", "${Package}\t${Version}\t${db:Status-Abbrev}\n", "davinci-resolve*"])
    except FileNotFoundError:
        raise Skip("no dpkg on this system")
    except RuntimeError:
        out = ""   # dpkg-query exits non-zero when nothing matches
    pkgs = {}
    for line in out.splitlines():
        parts = line.split("\t")
        # db:Status-Abbrev: the second letter is the current state ("ii" installed, "hi" held and installed)
        if len(parts) == 3 and len(parts[2]) > 1 and parts[2][1] == "i" and parts[1]:
            pkgs[parts[0]] = parts[1]
    return pkgs


@cached
def binary_version():
    """First string in the resolve binary shaped like 21.2.0.0006 (what `strings | grep -m1` gives)."""
    if not RESOLVE_BIN.exists():
        raise Skip(f"{RESOLVE_BIN} not found")
    try:
        p = subprocess.Popen(["strings", str(RESOLVE_BIN)], stdout=subprocess.PIPE, text=True, errors="replace")
        try:
            for line in p.stdout:
                if VERSION_RE.match(line.strip()):
                    return line.strip()
        finally:
            p.kill()
            p.wait()
    except FileNotFoundError:   # no binutils: scan the file directly (same rule, a bit slower)
        with open(RESOLVE_BIN, "rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as m:
            hit = re.search(rb"(?<![\x20-\x7e])(\d+\.\d+\.\d+\.\d+)(?![\x20-\x7e])", m)
            if hit:
                return hit.group(1).decode()
    raise RuntimeError("no N.N.N.N version string found in the resolve binary")


def _nums(text):
    return [int(x) for x in re.findall(r"\d+", text)]


def _same_release(deb_version, binary):
    """'21.2-mrd1.10.1' vs '21.2.0.0006', '21.1.1-mrd1.10.1' vs '21.1.1.0010': the package's upstream
    part, padded with zeros to major.minor.patch, must equal the binary's first three numbers."""
    upstream = _nums(deb_version.split(":")[-1].split("-")[0])[:3]
    upstream += [0] * (3 - len(upstream))
    return upstream == _nums(binary)[:3]


@cached
def community_facts():
    if not (COMMUNITY_REPO / ".git").exists():
        raise Skip(f"no community checkout at {COMMUNITY_REPO.name} (set RESOLVE_MCP_COMMUNITY_REPO)")
    git = ["git", "-C", str(COMMUNITY_REPO)]
    facts = {
        "describe": sh(git + ["describe", "--tags", "--always"]).strip(),
        "branch": sh(git + ["rev-parse", "--abbrev-ref", "HEAD"]).strip(),
        "dirty": [l for l in sh(git + ["status", "--porcelain"]).splitlines() if l.strip()],
    }
    pkg = json.loads((COMMUNITY_REPO / "package.json").read_text())
    facts["package"] = pkg.get("version")
    m = re.search(r'^VERSION\s*=\s*["\']([^"\']+)["\']', (COMMUNITY_REPO / "src" / "server.py").read_text(), re.M)
    facts["server"] = m.group(1) if m else None
    return facts


@cached
def stub_methods():
    if not STUB.exists():
        raise Skip(f"no scripting stub at {STUB} (older Resolve)")
    tree = ast.parse(STUB.read_text())
    methods, params, options = set(), set(), set()
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        if any((isinstance(b, ast.Name) and b.id == "TypedDict") or (isinstance(b, ast.Attribute) and b.attr == "TypedDict")
               for b in node.bases):
            options.update(f"{node.name}.{m.target.id}" for m in node.body
                           if isinstance(m, ast.AnnAssign) and isinstance(m.target, ast.Name))
            continue
        for m in node.body:
            if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)) and not m.name.startswith("_"):
                methods.add(f"{node.name}.{m.name}")
                a = m.args
                params.update(f"{node.name}.{m.name}:{x.arg}" for x in a.posonlyargs + a.args + a.kwonlyargs
                              if x.arg not in ("self", "cls"))
    expect(methods, "the stub parsed to zero public methods")
    return {"source": STUB.name, "count": len(methods), "methods": sorted(methods),
            "parameters": sorted(params), "options": sorted(options)}


@cached
def vendor_tools():
    if not VENDOR_MCP.exists():
        raise Skip(f"no {VENDOR_MCP.name} in this Resolve (added in 21.x)")
    data = json.loads(sh([str(VENDOR_MCP), "--dump-tools"], timeout=60))
    tools = sorted(({"name": t["name"], "properties": sorted((t.get("inputSchema") or {}).get("properties") or {})}
                    for t in data["tools"]), key=lambda t: t["name"])
    expect(tools, "--dump-tools listed zero tools")
    return {"count": len(tools), "tools": tools}


COMMUNITY_DUMP = r"""
import asyncio, json, sys
sys.path.insert(0, sys.argv[1])
import server
tools = asyncio.run(server.mcp.list_tools())
print("@@TOOLS@@" + json.dumps([{"name": t.name, "description": t.description or "",
    "properties": sorted((t.inputSchema or {}).get("properties") or {})} for t in tools]))
"""


def parse_actions(description):
    actions = set()
    for line in description.splitlines():
        m = ACTION_LINE.match(line)
        if m:
            actions.add(m.group(1))
            actions.update(ACTION_ALT.findall(line))
    return sorted(actions)


@cached
def community_tools():
    if not COMMUNITY_PY.exists():
        raise Skip(f"no community venv interpreter in {COMMUNITY_REPO.name}")
    # A separate interpreter: importing the server only registers tools, it does not reach Resolve. An empty
    # RESOLVE_MCP_LOG_FILE keeps the import from appending to the operator's logs/server.log.
    out = sh([str(COMMUNITY_PY), "-c", COMMUNITY_DUMP, str(COMMUNITY_REPO / "src")], cwd=str(scratch_dir()), timeout=180,
             env={**os.environ, "RESOLVE_MCP_LOG_FILE": ""})
    line = next((l for l in out.splitlines() if l.startswith("@@TOOLS@@")), None)
    expect(line, "the in-process community import printed no tool list")
    raw = json.loads(line[len("@@TOOLS@@"):])
    tools = sorted(({"name": t["name"], "actions": parse_actions(t["description"]), "properties": t["properties"]}
                    for t in raw), key=lambda t: t["name"])
    no_actions = [t["name"] for t in tools if not t["actions"]]
    expect(not no_actions, f"no action lines parsed from the description of: {', '.join(no_actions)} "
                           "(description format changed? fix ACTION_LINE)")
    desc = {t["name"]: t["description"] for t in raw}
    caps = sorted(cap for cap, (tool, marker) in rlib.COMMUNITY_CAPS.items() if marker in desc.get(tool, ""))
    return {"count": len(tools), "actions": sum(len(t["actions"]) for t in tools), "tools": tools, "capabilities": caps}


def undispatched_actions():
    """Documented actions with no string literal anywhere in the server source (so no dispatch entry)."""
    src = "\n".join(f.read_text(errors="replace") for f in (COMMUNITY_REPO / "src").rglob("*.py"))
    literals = set(re.findall(r"""["']([a-z_][a-z0-9_]*)["']""", src))
    return sorted(f"{t['name']}.{a}" for t in community_tools()["tools"] for a in t["actions"] if a not in literals)


def scratch_dir():
    rlib.SCRATCH.mkdir(parents=True, exist_ok=True)
    return rlib.SCRATCH


def _try(fn, pick=lambda v: v):
    try:
        return pick(fn())
    except Exception:
        return None


def versions():
    pkgs = _try(resolve_packages) or {}
    main = next((f"{n} {v}" for n, v in sorted(pkgs.items()) if not n.endswith("-data")), None)
    vend = _try(vendor_tools, lambda v: v["count"])
    return {
        "resolve_binary": _try(binary_version),
        "resolve_package": main,
        "community_describe": _try(community_facts, lambda f: f["describe"]),
        "community_package": _try(community_facts, lambda f: f["package"]),
        "community_server": _try(community_facts, lambda f: f["server"]),
        "vendor_tool_count": vend,
    }


# ── baselines ────────────────────────────────────────────────────────────────────────────────

def flat_vendor(d):
    return {t["name"] for t in d["tools"]} | {f"{t['name']}:{p}" for t in d["tools"] for p in t["properties"]}


def flat_stub_full(d):
    return set(d["methods"]) | set(d.get("parameters", [])) | {f"TypedDict:{o}" for o in d.get("options", [])}


def flat_community(d):
    return ({t["name"] for t in d["tools"]} | {f"{t['name']}.{a}" for t in d["tools"] for a in t["actions"]}
            | {f"{t['name']}:{p}" for t in d["tools"] for p in t.get("properties", [])}
            | {f"capability:{c}" for c in d.get("capabilities", [])})


def _listing(items, limit=60):
    items = sorted(items)
    more = f" ... (+{len(items) - limit} more)" if len(items) > limit else ""
    return ", ".join(items[:limit]) + more


def write_baseline(name, data):
    BASELINES.mkdir(parents=True, exist_ok=True)
    (BASELINES / name).write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")


def baseline_check(name, fact, flatten, summary, update):
    """fact: a callable returning the current surface. When it Skips (the source is not there) and a baseline
    exists, the whole recorded surface is gone: FAIL, not SKIP."""
    path = BASELINES / name
    try:
        current = fact()
    except Skip as exc:
        if path.exists():
            base = json.loads(path.read_text())
            raise AssertionError(f"{exc}, but {name} records {summary(base)}: REMOVED everything since the baseline")
        raise
    if update:
        diff = ""
        if path.exists():
            b, c = flatten(json.loads(path.read_text())), flatten(current)
            gone, new = b - c, c - b
            diff = (f": REMOVED {len(gone)} ({_listing(gone, 20)})" if gone else ": nothing removed") + \
                   (f", ADDED {len(new)} ({_listing(new, 20)})" if new else ", nothing added")
        write_baseline(name, current)
        return f"updated ({summary(current)}){diff}"
    if not path.exists():
        write_baseline(name, current)
        raise Warn(f"baseline created ({summary(current)}): review and commit {path.name}")
    base = json.loads(path.read_text())
    b, c = flatten(base), flatten(current)
    removed, added = b - c, c - b
    if removed:
        raise AssertionError(f"REMOVED {len(removed)} since the baseline: {_listing(removed)}"
                             + (f" | ADDED {len(added)}: {_listing(added, 30)}" if added else "")
                             + " -- review the callers, then accept with ./run.sh --offline --update-baselines")
    if added:
        raise Warn(f"ADDED {len(added)} since the baseline ({summary(current)}): {_listing(added)}"
                   " -- accept with ./run.sh --offline --update-baselines once reviewed")
    return f"identical ({summary(current)})"


def versions_check(update):
    current = versions()
    missing = [k for k, v in current.items() if v is None]
    path = BASELINES / "versions.json"
    shown = ", ".join(f"{k}={v}" for k, v in current.items())
    if update:
        lost = []
        if path.exists():
            old = json.loads(path.read_text())
            lost = [k for k in missing if old.get(k) is not None]
        expect(not lost, f"not updated: {', '.join(lost)} could not be read now but the baseline has a value "
                         f"({shown}); fix what broke first")
        write_baseline("versions.json", current)
        return f"updated: {shown}" + (f" (unknown: {', '.join(missing)})" if missing else "")
    if not path.exists():
        write_baseline("versions.json", current)
        raise Warn(f"baseline created: {shown}")
    base = json.loads(path.read_text())
    changed = [f"{k}: {base.get(k)} -> {current.get(k)}" for k in sorted(set(base) | set(current))
               if base.get(k) != current.get(k)]
    if changed:
        raise Warn("versions changed since the baseline: " + "; ".join(changed)
                   + " -- expected after an upgrade; accept with ./run.sh --offline --update-baselines once the suite passes")
    return f"unchanged: {shown}"


# ── checks ───────────────────────────────────────────────────────────────────────────────────

def check_packages():
    pkgs = resolve_packages()
    binary = binary_version()
    mains = sorted(n for n in pkgs if not n.endswith("-data"))
    if not mains:
        base = BASELINES / "versions.json"
        recorded = json.loads(base.read_text()).get("resolve_package") if base.exists() else None
        if recorded:
            raise AssertionError(f"no installed Resolve package (removed, or not in state 'installed'), but the baseline "
                                 f"records {recorded}; binary {binary}")
        raise Skip(f"Resolve is not installed from a .deb (binary {binary})")
    expect(len(mains) == 1, f"more than one Resolve package installed: {mains}")
    main = mains[0]
    data = f"{main}-data"
    expect(data in pkgs, f"{main} {pkgs[main]} installed without {data}")
    expect(pkgs[main] == pkgs[data], f"{main} {pkgs[main]} != {data} {pkgs[data]}")
    expect(_same_release(pkgs[main], binary), f"package {main} {pkgs[main]} does not match binary {binary}")
    return f"{main} {pkgs[main]} (+ -data), binary {binary}"


def check_ldd():
    if not RESOLVE_BIN.exists():
        raise Skip(f"{RESOLVE_BIN} not found")
    report = []
    for b in (RESOLVE_BIN, VENDOR_MCP):
        if not b.exists():
            expect(not (b == VENDOR_MCP and (BASELINES / "vendor_tools.json").exists()),
                   f"{b} is gone, but baselines/vendor_tools.json records it")
            continue
        out = sh(["ldd", str(b)])
        missing = sorted({l.split("=>")[0].strip() for l in out.splitlines() if "not found" in l})
        expect(not missing, f"{b.name}: unresolved libraries: {', '.join(missing)}")
        report.append(f"{b.name}: {sum('=>' in l for l in out.splitlines())} libs resolved")
    return "; ".join(report)


def local_patches():
    """The two local commits every upgrade cherry-picks. -> list of what is missing."""
    missing = []
    app = (COMMUNITY_REPO / "src" / "utils" / "app_control.py").read_text()
    m = re.search(r"==\s*['\"]linux['\"]\s*:\s*\n\s*subprocess\.Popen\(\[([^\]]*)\]", app)
    if not m or "-nogui" not in m.group(1):
        missing.append("restart_resolve_app does not relaunch with -nogui on Linux (it would open a GUI Resolve "
                       "on the person's seat): cherry-pick the app-control patch")
    pkg = json.loads((COMMUNITY_REPO / "package.json").read_text())
    lock = COMMUNITY_REPO / "package-lock.json"
    want = None
    if lock.exists():
        want = (json.loads(lock.read_text()).get("packages", {}).get("node_modules/better-sqlite3") or {}).get("version")
    if want and (pkg.get("allowScripts") or {}).get(f"better-sqlite3@{want}") is not True:
        missing.append(f"package.json allowScripts does not approve better-sqlite3@{want} (installed per package-lock): "
                       "cherry-pick the npm patch")
    return missing


def check_community():
    f = community_facts()
    # The bootstrapper prints package.json's version, so smoke == package.json only proves node runs it;
    # server.py's VERSION is the independent comparison.
    smoke = sh(["node", str(COMMUNITY_REPO / "bin" / "davinci-resolve-mcp.mjs"), "--version"], cwd=str(COMMUNITY_REPO)).strip()
    detail = f"{f['describe']} on {f['branch']}, npm --version {smoke}, package.json {f['package']}, server.py {f['server']}"
    expect(smoke, "npm bootstrapper printed no version")
    missing = local_patches()
    expect(not missing, "; ".join(missing) + f" ({detail})")
    notes = []
    if len({smoke, f["package"], f["server"]}) != 1:
        notes.append("version strings disagree")
    if f["dirty"]:
        notes.append(f"{len(f['dirty'])} uncommitted change(s): {_listing([l.strip() for l in f['dirty']], 20)}")
    if notes:
        raise Warn(f"{detail}; " + "; ".join(notes))
    return detail + "; both local patches present"


def check_dispatch():
    u = undispatched_actions()
    expect(not u, f"{len(u)} documented action(s) with no string literal in src/ (dropped from the dispatch?): {_listing(u, 30)}")
    return f"{community_tools()['actions']} documented actions, each found as a literal in src/"


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--update-baselines", action="store_true", help="rewrite the baseline files from this install")
    args = ap.parse_args()
    up = args.update_baselines

    suite = rlib.Suite("t00_install")
    suite.check("resolve packages match the binary", check_packages)
    suite.check("resolve shared libraries resolve", check_ldd)
    suite.check("community checkout", check_community)
    suite.check("baseline: scripting stub methods", lambda: baseline_check(
        "stub_methods.json", stub_methods, flat_stub_full,
        lambda d: f"{d['count']} methods, {len(d.get('parameters', []))} parameters, {len(d.get('options', []))} option keys", up))
    suite.check("baseline: vendor ResolveMCP tools", lambda: baseline_check(
        "vendor_tools.json", vendor_tools, flat_vendor,
        lambda d: f"{d['count']} tools, {sum(len(t['properties']) for t in d['tools'])} properties", up))
    suite.check("baseline: community compound tools", lambda: baseline_check(
        "community_tools.json", community_tools, flat_community,
        lambda d: f"{d['count']} tools, {d['actions']} actions, {len(d.get('capabilities', []))} capabilities", up))
    suite.check("community: every documented action is dispatched", check_dispatch)
    suite.check("baseline: versions", lambda: versions_check(up))
    raise SystemExit(suite.finish())


if __name__ == "__main__":
    main()
