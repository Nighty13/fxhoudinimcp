"""Call one fxhoudinimcp plugin command directly, without an MCP client. Handy for scripts and debugging.

    python hcall.py <namespace.command> '{"param": 1}'
    python hcall.py <namespace.command> params.json
    python hcall.py code.execute_python --py script.py [return_expression]

Houdini must be running with the plugin started (MCP menu > Start, or FXHOUDINIMCP_AUTOSTART=1).
Command names are the handler names, e.g. rendering.start_render, graph.verify_network.
"""

import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

PORT = os.environ.get("FXHOUDINIMCP_PORT", "8100")

cmd = sys.argv[1]
params = {}
if len(sys.argv) > 2:
    if sys.argv[2] == "--py":
        params = {"code": Path(sys.argv[3]).read_text(encoding="utf-8-sig")}
        if len(sys.argv) > 4:
            params["return_expression"] = sys.argv[4]
    elif sys.argv[2].endswith(".json"):
        params = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8-sig"))
    else:
        params = json.loads(sys.argv[2])
body = urllib.parse.urlencode(
    {
        "json": json.dumps(
            ["mcp.execute", [], {"command": cmd, "params": params, "request_id": "cli"}]
        )
    }
).encode()
req = urllib.request.Request(
    f"http://127.0.0.1:{PORT}/api",
    data=body,
    headers={"Content-Type": "application/x-www-form-urlencoded", "Host": f"127.0.0.1:{PORT}"},
)
print(json.dumps(json.loads(urllib.request.urlopen(req, timeout=600).read()), indent=1))
