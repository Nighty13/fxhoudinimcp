"""Call Epic's Unreal MCP server from a script (no MCP client needed).

    python ue_mcp_call.py tools                         # tools/list
    python ue_mcp_call.py call list_toolsets '{}'
    python ue_mcp_call.py call describe_toolset '{"toolset_name": "HoudiniEngineTools"}'
    python ue_mcp_call.py call call_tool '{"tool_name": "...", "arguments": {...}}'

UE_MCP_URL overrides the default http://127.0.0.1:8000/mcp.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request

URL = os.environ.get("UE_MCP_URL", "http://127.0.0.1:8000/mcp")


def rpc(method: str, params: dict | None = None, session: str | None = None, rid: int = 1):
    body = {"jsonrpc": "2.0", "method": method, "params": params or {}}
    if rid is not None:
        body["id"] = rid
    req = urllib.request.Request(URL, data=json.dumps(body).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json, text/event-stream")
    if session:
        req.add_header("Mcp-Session-Id", session)
    with urllib.request.urlopen(req, timeout=120) as resp:
        sid = resp.headers.get("Mcp-Session-Id") or session
        text = resp.read().decode("utf-8", "replace")
    if text.startswith("event:") or text.startswith("data:"):
        text = "\n".join(line[5:].strip() for line in text.splitlines() if line.startswith("data:"))
    return (json.loads(text) if text.strip() else None), sid


def session() -> str | None:
    _, sid = rpc(
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "ue_mcp_call", "version": "1"},
        },
    )
    rpc("notifications/initialized", session=sid, rid=None)
    return sid


def main() -> None:
    sid = session()
    if sys.argv[1] == "tools":
        out, _ = rpc("tools/list", session=sid, rid=2)
    else:
        name, args = sys.argv[2], json.loads(sys.argv[3] if len(sys.argv) > 3 else "{}")
        out, _ = rpc("tools/call", {"name": name, "arguments": args}, session=sid, rid=2)
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
