"""MCP tools that start, watch and talk to an Unreal Editor.

The Unreal side is Epic's Unreal MCP (the experimental ModelContextProtocol
plugin in UE 5.8) plus extras/unreal/HoudiniEngineToolset; the editor itself is
that MCP server, so starting and waiting for it has to happen out here.
extras/unreal/setup_unreal_project.py prepares a project for all of this.

unreal_call reaches the editor's MCP through this server. An MCP client
connects to HTTP servers once, at its own start, so an editor opened later is
unreachable as a native server until the client reconnects; through this
server it is reachable as soon as it serves.
"""

from __future__ import annotations

# Built-in
import asyncio
import contextlib
import itertools
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

# Third-party
import httpx

from fxhoudinimcp._sdk import Context

# Internal
from fxhoudinimcp.server import mcp
from fxhoudinimcp.tools.session import houdini_env

DEFAULT_PORT = int(os.environ.get("FXHOUDINIMCP_UNREAL_PORT", "8000"))
_started: dict[int, subprocess.Popen] = {}
_ids = itertools.count(1)


def engine_root(uproject: Path) -> Path:
    """Engine folder for a .uproject's EngineAssociation (launcher version or source-build GUID)."""
    association = json.loads(uproject.read_text(encoding="utf-8")).get("EngineAssociation", "")
    if override := os.environ.get("UE_ROOT"):
        return Path(override)
    if sys.platform == "win32":
        import winreg

        if association.startswith("{"):
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, r"Software\Epic Games\Unreal Engine\Builds"
            ) as key:
                return Path(winreg.QueryValueEx(key, association)[0])
        dat = (
            Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData"))
            / "Epic/UnrealEngineLauncher/LauncherInstalled.dat"
        )
        if dat.is_file():
            for item in json.loads(dat.read_text(encoding="utf-8")).get("InstallationList", []):
                if item.get("AppName") == f"UE_{association}":
                    return Path(item["InstallLocation"])
    raise FileNotFoundError(
        f"No Unreal {association or '(no EngineAssociation)'} install found for {uproject}; "
        "set UE_ROOT to the engine folder."
    )


def editor_exe(root: Path) -> Path:
    name = "UnrealEditor.exe" if sys.platform == "win32" else "UnrealEditor"
    platform = (
        "Win64" if sys.platform == "win32" else ("Mac" if sys.platform == "darwin" else "Linux")
    )
    return root / "Engine" / "Binaries" / platform / name


async def _rpc(port: int, method: str, params: dict | None, session: str | None, timeout: float):
    body: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "params": params or {}}
    if not method.startswith("notifications/"):
        body["id"] = next(_ids)
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if session:
        headers["Mcp-Session-Id"] = session
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(f"http://127.0.0.1:{port}/mcp", json=body, headers=headers)
    sid = response.headers.get("Mcp-Session-Id", session)
    text = response.text
    if text.lstrip().startswith(("event:", "data:")):
        text = "\n".join(line[5:].strip() for line in text.splitlines() if line.startswith("data:"))
    return (json.loads(text) if text.strip() else None), sid


async def _session(port: int, timeout: float = 5.0) -> str | None:
    params = {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "fxhoudinimcp", "version": "1"},
    }
    _, sid = await _rpc(port, "initialize", params, None, timeout)
    await _rpc(port, "notifications/initialized", None, sid, timeout)
    return sid


async def mcp_alive(port: int) -> bool:
    try:
        await _session(port, timeout=3.0)
        return True
    except (httpx.HTTPError, ValueError):
        return False


def _unwrap(result: Any) -> Any:
    """A toolset tool answers {"returnValue": <json string or value>}; give back the value itself."""
    if not isinstance(result, dict):
        return result
    content = result.get("content") or []
    texts = [c.get("text", "") for c in content if c.get("type") == "text"]
    if len(texts) == 1:
        try:
            value = json.loads(texts[0])
        except ValueError:
            return {"text": texts[0], "is_error": result.get("isError", False)}
        if isinstance(value, dict) and set(value) == {"returnValue"}:
            value = value["returnValue"]
            if isinstance(value, str):
                with contextlib.suppress(ValueError):
                    value = json.loads(value)
        return value
    return result


@mcp.tool()
async def unreal_status(ctx: Context, port: int = DEFAULT_PORT) -> dict:
    """Is an Unreal Editor serving Epic's Unreal MCP, and which editors are running.

    Args:
        port: Unreal MCP port (ServerPortNumber in the project's MCP settings).
    """
    alive = await mcp_alive(port)
    editors = []
    if sys.platform == "win32":
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq UnrealEditor.exe", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
        ).stdout
        editors = [
            int(line.split('","')[1]) for line in out.splitlines() if line.startswith('"Unreal')
        ]
    return {
        "mcp_serving": alive,
        "url": f"http://127.0.0.1:{port}/mcp",
        "editor_pids": editors,
        "started_here": sorted(_started),
    }


@mcp.tool()
async def start_unreal(
    ctx: Context, project: str, port: int = DEFAULT_PORT, wait_seconds: float = 600
) -> dict:
    """Open a .uproject in the Unreal Editor and wait until its Unreal MCP serves.

    The engine comes from the project's EngineAssociation (UE_ROOT overrides).
    The project must have been prepared with extras/unreal/setup_unreal_project.py
    (MCP plugin on, server auto-started). A first open compiles shaders and can
    take minutes. If an editor already serves on the port, nothing is started.

    Args:
        project: Path of the .uproject file (or of its folder).
        port: Unreal MCP port the project serves on.
        wait_seconds: Give up waiting after this long (the editor keeps running).
    """
    path = Path(os.path.expandvars(project))
    if path.is_dir():
        found = sorted(path.glob("*.uproject"))
        if not found:
            raise FileNotFoundError(f"No .uproject in {path}.")
        path = found[0]
    if await mcp_alive(port):
        return {"started": False, "already_serving": True, "url": f"http://127.0.0.1:{port}/mcp"}
    exe = editor_exe(engine_root(path))
    if not exe.is_file():
        raise FileNotFoundError(f"{exe} does not exist.")
    env, env_note = houdini_env()  # Houdini Engine inside the editor reads the same prefs
    flags = (
        (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
        if sys.platform == "win32"
        else 0
    )
    process = subprocess.Popen(
        [str(exe), str(path)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
        creationflags=flags,
        start_new_session=sys.platform != "win32",
    )
    _started[process.pid] = process
    started = time.monotonic()
    log = path.parent / "Saved" / "Logs" / f"{path.stem}.log"
    while time.monotonic() - started < wait_seconds:
        if process.poll() is not None:
            raise RuntimeError(
                f"Unreal Editor exited with code {process.returncode} before serving. See {log}."
            )
        if await mcp_alive(port):
            result = {
                "started": True,
                "pid": process.pid,
                "url": f"http://127.0.0.1:{port}/mcp",
                "seconds": round(time.monotonic() - started, 1),
                "log": str(log),
            }
            if env_note:
                result["env_note"] = env_note
            return result
        await asyncio.sleep(2.0)
    raise TimeoutError(
        f"Unreal Editor (pid {process.pid}) did not serve MCP on port {port} within "
        f"{wait_seconds:g}s; it is still running (stop_unreal ends it). Is the project set up "
        f"(setup_unreal_project.py)? Log: {log}"
    )


@mcp.tool()
async def stop_unreal(ctx: Context, pid: int | None = None) -> dict:
    """Close an Unreal Editor that start_unreal started (unsaved work is lost).

    Args:
        pid: Editor to stop. Default: the only one started here.
    """
    if pid is None and len(_started) == 1:
        pid = next(iter(_started))
    if pid not in _started:
        raise ValueError(
            f"Only editors started here are stopped; started here: {sorted(_started) or 'none'}."
        )
    process = _started.pop(pid)
    process.terminate()
    try:
        await asyncio.to_thread(process.wait, 30)
    except subprocess.TimeoutExpired:
        process.kill()
    return {"stopped": pid}


def _remote_execution_module(root: Path):
    """Epic's remote_execution.py, shipped with every engine's PythonScriptPlugin."""
    import importlib.util

    path = (
        root / "Engine/Plugins/Experimental/PythonScriptPlugin/Content/Python/remote_execution.py"
    )
    if not path.is_file():
        raise FileNotFoundError(f"{path} not found (set UE_ROOT to the engine folder).")
    spec = importlib.util.spec_from_file_location("ue_remote_execution", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_python(code: str, root: Path, timeout: float) -> dict:
    rex_mod = _remote_execution_module(root)
    rex = rex_mod.RemoteExecution()
    rex.start()
    try:
        deadline = time.monotonic() + min(timeout, 20)
        while not rex.remote_nodes and time.monotonic() < deadline:
            time.sleep(0.25)
        if not rex.remote_nodes:
            raise ConnectionError(
                "No Unreal Editor answered Python remote execution: is one open, and does its "
                "project have bRemoteExecution=True (setup_unreal_project.py sets it)?"
            )
        rex.open_command_connection(rex.remote_nodes[0]["node_id"])
        result = rex.run_command(code, unattended=True, exec_mode=rex_mod.MODE_EXEC_FILE)
    finally:
        rex.stop()
    output = [o.get("output", "").rstrip() for o in result.get("output", [])]
    return {
        "success": bool(result.get("success")),
        "output": "\n".join(output),
        "result": result.get("result"),
    }


@mcp.tool()
async def unreal_python(ctx: Context, code: str, timeout_seconds: float = 300) -> dict:
    """Run Python inside the open Unreal Editor (the full `unreal` API) and return its output.

    The general way to do anything in Unreal: levels, assets, materials,
    actors, imports, settings. Uses Epic's Python remote execution, so it works
    while the editor's MCP is busy elsewhere and needs no tool per operation.
    print() what you need back. Reusable recipes (tested scripts for common
    jobs) live in extras/unreal/recipes/: read one and pass its text.

    Args:
        code: Python source, run as a file (top-level statements, imports, defs).
        timeout_seconds: Upper bound for the run.
    """
    root = Path(os.environ["UE_ROOT"]) if os.environ.get("UE_ROOT") else None
    if root is None:
        for candidate in (
            Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Epic Games" / "UE_5.8",
        ):
            if candidate.is_dir():
                root = candidate
    if root is None:
        raise FileNotFoundError("Set UE_ROOT to the Unreal engine folder.")
    return await asyncio.wait_for(
        asyncio.to_thread(_run_python, code, root, timeout_seconds), timeout_seconds
    )


HE_TOOLSET = "houdini_engine_toolset.toolset.HoudiniEngineTools"
APP_TOOLSET = "EditorToolset.EditorAppToolset"
_NO_ANNOTATIONS = {
    "gridSpacing": 0,
    "gridExtent": 0,
    "gridHeight": 0,
    "maxLabelDistance": 0,
    "classFilter": {"refPath": "/Script/Engine.Actor"},
    "maxLabels": 0,
}


@mcp.tool()
async def unreal_capture(
    ctx: Context,
    actor_label: str | None = None,
    pitch: float = -15.0,
    yaw: float = 35.0,
    distance_scale: float = 2.5,
    output_path: str | None = None,
    port: int = DEFAULT_PORT,
) -> dict:
    """Capture the Unreal level viewport to a PNG, optionally framed on one actor first.

    Wraps Epic's CaptureViewport, which needs a camera and an annotation config
    even though it calls them optional. With actor_label, the camera is pointed
    at that Houdini asset actor (HoudiniEngineTools.frame_hda) from pitch/yaw.

    Args:
        actor_label: Houdini asset actor to frame; omitted keeps the current camera.
        pitch: Camera pitch in degrees when framing (negative looks down).
        yaw: Camera yaw in degrees when framing.
        distance_scale: Camera distance as a multiple of the actor's bounding radius.
        output_path: Where to write the PNG. Default: a temp file.
        port: Unreal MCP port.
    """
    if actor_label:
        framing = {
            "actor_label": actor_label,
            "pitch": pitch,
            "yaw": yaw,
            "distance_scale": distance_scale,
        }
        camera = await call_unreal("frame_hda", framing, HE_TOOLSET, port)
    else:
        camera = await call_unreal("GetCameraTransform", {}, APP_TOOLSET, port)
    shot = await call_unreal(
        "CaptureViewport",
        {"captureTransform": camera, "annotations": _NO_ANNOTATIONS, "bShowUI": False},
        APP_TOOLSET,
        port,
    )
    image = shot.get("image", {}) if isinstance(shot, dict) else {}
    path = image.get("path")
    if not path:
        raise RuntimeError(f"CaptureViewport returned no image: {str(shot)[:500]}")
    if output_path:
        target = Path(os.path.expandvars(output_path))
        target.parent.mkdir(parents=True, exist_ok=True)
        Path(path).replace(target)
        path = str(target)
    return {"path": path, "camera": camera}


@mcp.tool()
async def unreal_call(
    ctx: Context,
    tool_name: str,
    arguments: dict | None = None,
    toolset_name: str | None = None,
    port: int = DEFAULT_PORT,
    timeout_seconds: float = 120,
) -> Any:
    """Call a tool of the running Unreal Editor's MCP (Epic's Unreal MCP).

    Discovery: unreal_call("list_toolsets"), then
    unreal_call("describe_toolset", {"toolset_name": ...}). Toolset tools take
    their toolset: toolset_name="houdini_engine_toolset.toolset.HoudiniEngineTools",
    tool_name="set_hda_parameters". A toolset's {"returnValue": ...} wrapper is
    removed and JSON strings are decoded, so the reply is the tool's own value.
    An image (CaptureViewport) is saved to a PNG and its path returned instead
    of the base64.

    Args:
        tool_name: list_toolsets, describe_toolset, call_tool, or a toolset tool's name.
        arguments: The tool's arguments.
        toolset_name: For a toolset tool, its full name from list_toolsets.
        port: Unreal MCP port.
        timeout_seconds: How long to wait for the editor.
    """
    return await call_unreal(tool_name, arguments, toolset_name, port, timeout_seconds)


async def call_unreal(
    tool_name: str,
    arguments: dict | None = None,
    toolset_name: str | None = None,
    port: int = DEFAULT_PORT,
    timeout_seconds: float = 120,
) -> Any:
    """unreal_call without the MCP context, for other tools to build on."""
    try:
        return await _call_unreal(tool_name, arguments, toolset_name, port, timeout_seconds)
    except httpx.ConnectError as exc:
        raise ConnectionError(
            f"No Unreal Editor serves MCP on port {port}: start_unreal opens one."
        ) from exc
    except httpx.TimeoutException as exc:
        raise TimeoutError(
            f"The Unreal Editor on port {port} did not answer within {timeout_seconds:g}s. Its game "
            "thread is busy (shader compilation, a Houdini cook, a modal dialog): retry, or raise "
            "timeout_seconds."
        ) from exc


async def _call_unreal(tool_name, arguments, toolset_name, port, timeout_seconds) -> Any:
    sid = await _session(port, timeout=timeout_seconds)
    if toolset_name or tool_name not in ("list_toolsets", "describe_toolset", "call_tool"):
        params = {
            "name": "call_tool",
            "arguments": {"tool_name": tool_name, "arguments": arguments or {}},
        }
        if toolset_name:
            params["arguments"]["toolset_name"] = toolset_name
    else:
        params = {"name": tool_name, "arguments": arguments or {}}
    reply, _ = await _rpc(port, "tools/call", params, sid, timeout_seconds)
    if reply and "error" in reply:
        raise RuntimeError(f"Unreal MCP error: {reply['error']}")
    value = _unwrap((reply or {}).get("result"))
    image = value.get("image") if isinstance(value, dict) else None
    if isinstance(image, dict) and image.get("data"):
        import base64
        import tempfile

        out = (
            Path(tempfile.gettempdir())
            / "fxhoudinimcp"
            / f"unreal_{time.time_ns() // 1_000_000}.png"
        )
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(base64.b64decode(image["data"]))
        value = {**value, "image": {"path": str(out), "mimeType": image.get("mimeType")}}
    return value
