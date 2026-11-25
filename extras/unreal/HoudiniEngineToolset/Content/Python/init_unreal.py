"""Registers the Houdini Engine toolset with Unreal's AI Toolset Registry at editor start."""

import unreal

try:
    from houdini_engine_toolset import toolset

    if not toolset.registration.register():
        unreal.log_warning(
            "[HoudiniEngineToolset] Toolset Registry not available; tools not registered."
        )
    else:
        unreal.log("[HoudiniEngineToolset] registered HoudiniEngineTools")
    try:
        toolset.ensure_vertex_color_material()
    except Exception as exc:
        unreal.log_warning(f"[HoudiniEngineToolset] vertex colour material not created: {exc}")
except Exception as exc:  # never break editor start-up
    unreal.log_error(f"[HoudiniEngineToolset] registration failed: {type(exc).__name__}: {exc}")
