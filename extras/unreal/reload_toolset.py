"""Push this repo's HoudiniEngineToolset into a project and hot-reload it in the open editor.

    python reload_toolset.py D:/path/MyProject/MyProject.uproject

Copies extras/unreal/HoudiniEngineToolset over <Project>/Plugins/HoudiniEngineToolset, then (through
Python remote execution, ue_exec.py) re-imports the module and re-registers the toolset, so the Unreal
MCP serves the new tools without an editor restart.
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
RELOAD = """
import importlib
import houdini_engine_toolset.toolset as t
t.registration.unregister()
t = importlib.reload(t)
print("registered:", t.registration.register(), "| material:", t.ensure_vertex_color_material())
"""

project = Path(sys.argv[1]).resolve()
project = project.parent if project.suffix == ".uproject" else project
dest = project / "Plugins" / "HoudiniEngineToolset"
shutil.copytree(
    HERE / "HoudiniEngineToolset",
    dest,
    dirs_exist_ok=True,
    ignore=shutil.ignore_patterns("__pycache__"),
)
script = Path(tempfile.gettempdir()) / "reload_houdini_engine_toolset.py"
script.write_text(RELOAD, encoding="utf-8")
sys.exit(subprocess.call([sys.executable, str(HERE / "ue_exec.py"), str(script)]))
