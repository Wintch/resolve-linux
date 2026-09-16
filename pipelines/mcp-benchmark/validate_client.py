#!/usr/bin/env python3
"""
End-to-end smoke test for resolve_client.ResolveClient -- proves the unified
client works without the caller ever knowing (or caring) which MCP server
answered a given call.

Run: venv/bin/python validate_client.py
"""
import asyncio

from resolve_client import ResolveClient

ORIGINAL_NAMES = ["Solid Color"] * 8
TEST_NAMES = [f"Clip {i:02d}" for i in range(1, 9)]


async def main():
    checks = []

    async def check(label, coro):
        try:
            result = await coro
            checks.append((label, True, str(result)[:120]))
        except Exception as exc:
            checks.append((label, False, repr(exc)[:200]))

    async with ResolveClient() as client:
        await check("ensure_running", client.ensure_running())
        await check("get_version", client.get_version())
        await check("get_project_name", client.get_project_name())
        await check("get_current_timeline_name", client.get_current_timeline_name())
        await check("rename_clips -> test names", client.rename_clips(TEST_NAMES))
        await check("rename_clips -> restore", client.rename_clips(ORIGINAL_NAMES))
        await check(
            "generate_lut",
            client.generate_lut(
                path="validate_client_test.cube", size=9, transform="return (r, g, b)"
            ),
        )
        await check("list_luts", client.list_luts())

    print(f"\n{'check':<32} {'ok':<5} preview")
    all_ok = True
    for label, ok, preview in checks:
        all_ok &= ok
        print(f"{label:<32} {'OK' if ok else 'FAIL':<5} {preview}")

    print("\nALL CHECKS PASSED" if all_ok else "\nSOME CHECKS FAILED")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
