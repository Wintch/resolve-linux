#!/usr/bin/env python3
"""`resolve_headless.py run -- <cmd>` with its status chatter kept off stdout.

Upstream's `cmd_run` print()s "starting: ...", "ready in 6.0s", "reusing the
running headless instance" to stdout. When <cmd> is an MCP stdio server, stdout
IS the JSON-RPC channel, so those lines arrive at the client ahead of the first
protocol message ("Invalid JSON: expected value at line 1 column 1") and strict
clients (the Python `mcp` SDK, Hermes) drop the session.

Only Python-level `sys.stdout` is redirected to stderr; fd 1 is untouched, so
the child server still writes protocol frames to the real stdout.

Usage: resolve_headless_stdio.py <repo> -- <server command...>
"""
import sys

repo, *argv = sys.argv[1:]
argv = [a for a in argv if a != "--"]
if not argv:
    sys.exit("usage: resolve_headless_stdio.py <repo> -- <command...>")
sys.path.insert(0, f"{repo}/scripts")
import resolve_headless as rh  # noqa: E402

sys.stdout = sys.stderr
raise SystemExit(rh.cmd_run(argv, 120.0))
