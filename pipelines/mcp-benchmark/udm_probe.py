"""Probe harness for unofficial-davinci-mcp over stdio.

No args: list tools. One JSON arg: [[tool, {args}], ...] called in order.
Run with ~/resolve-install/unofficial-davinci-mcp/venv/bin/python (needs `mcp`).
"""
import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from bench_lib import resolve_env

REPO = os.path.expanduser("~/resolve-install/unofficial-davinci-mcp")


def text(result):
    return "\n".join(c.text for c in result.content if getattr(c, "type", "") == "text")


async def main(calls):
    params = StdioServerParameters(
        command=f"{REPO}/venv/bin/unofficial-davinci-mcp", args=[], env=resolve_env()
    )
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            if not calls:
                tools = (await s.list_tools()).tools
                print(f"TOOLS ({len(tools)}):")
                for t in tools:
                    ann = t.annotations
                    flags = []
                    if ann and ann.readOnlyHint:
                        flags.append("ro")
                    if ann and ann.destructiveHint:
                        flags.append("DESTRUCTIVE")
                    print(f"  {t.name:38s} {','.join(flags):14s} {(t.description or '').splitlines()[0][:90]}")
                return
            for name, args in calls:
                res = await s.call_tool(name, args)
                print(f"=== {name} {json.dumps(args)} isError={res.isError}")
                print(text(res)[:2500])


if __name__ == "__main__":
    calls = json.loads(sys.argv[1]) if len(sys.argv) > 1 else []
    asyncio.run(main(calls))
