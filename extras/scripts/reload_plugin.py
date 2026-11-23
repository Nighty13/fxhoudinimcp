"""Hot-reload the plugin's handlers inside a running Houdini after editing them.

python hcall.py code.execute_python --py reload_plugin.py r
"""

import importlib

import fxhoudinimcp_server.dispatcher as d
import fxhoudinimcp_server.handlers as h

before = len(d._handler_registry)
importlib.reload(d)
importlib.reload(h)
res = h.reload_plugin()
r = {"before": before, "after": len(d._handler_registry), "failed": res.get("failed")}
