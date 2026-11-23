"""Run a Python file inside a running Unreal Editor and print its output.

    python ue_exec.py my_script.py

Needs the project's Python Remote Execution on (see docs/how-to/houdini-unreal-workflow.md).
Set UE_ROOT if Unreal is not installed at the default path.
"""

import os
import sys
import time
from pathlib import Path

UE_ROOT = os.environ.get("UE_ROOT", r"C:\Program Files\Epic Games\UE_5.8")
sys.path.insert(
    0,
    os.path.join(
        UE_ROOT, "Engine", "Plugins", "Experimental", "PythonScriptPlugin", "Content", "Python"
    ),
)
import remote_execution as rex_mod  # noqa: E402

code = Path(sys.argv[1]).read_text(encoding="utf-8-sig")
rex = rex_mod.RemoteExecution()
rex.start()
deadline = time.time() + 15
while not rex.remote_nodes and time.time() < deadline:
    time.sleep(0.25)
if not rex.remote_nodes:
    print("no editor found (is Remote Execution on and the editor open?)")
    rex.stop()
    sys.exit(1)
rex.open_command_connection(rex.remote_nodes[0]["node_id"])
res = rex.run_command(code, unattended=True, exec_mode=rex_mod.MODE_EXEC_FILE)
for line in res.get("output", []):
    print(line.get("output", "").rstrip())
print("success:", res.get("success"), "|", str(res.get("result", ""))[:2000])
rex.stop()
