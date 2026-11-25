"""MCP tools that choose, start and stop the Houdini session this server talks to.

The server connects at startup to the first Houdini it finds. With several open,
or none, the agent had no way to pick one or to get one: these list what is
serving, switch the bridge to another port, and start a Houdini of its own.
"""

from __future__ import annotations

# Built-in
import asyncio
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

# Third-party
from fxhoudinimcp._sdk import Context

# Internal
from fxhoudinimcp.bridge import find_servers
from fxhoudinimcp.houdini_discovery import find_hython
from fxhoudinimcp.houdini_package import plugin_path
from fxhoudinimcp.server import _get_bridge, mcp

# The plugin's own port search starts here, so the sessions live from here up.
BASE_PORT = 8100

# Sessions this server started: pid -> (process, port it serves, port to go back to).
_started: dict[int, tuple[subprocess.Popen, int, int]] = {}


async def list_sessions(bridge) -> list[dict[str, Any]]:
    """Every Houdini serving the plugin, the one this server talks to marked current."""
    sessions = await find_servers(bridge.host, BASE_PORT)
    for session in sessions:
        session["current"] = session["port"] == bridge.port
        session["started_here"] = session.get("pid") in _started
    return sessions


@mcp.tool()
async def connect_houdini(ctx: Context, port: int) -> dict:
    """Talk to the Houdini serving on ``port`` from now on.

    get_houdini_connection_status lists the sessions and their ports.

    Args:
        port: Port of a Houdini serving the plugin.
    """
    bridge = _get_bridge(ctx)
    found = await find_servers(bridge.host, port, max_tries=1)
    if not found:
        others = await list_sessions(bridge)
        raise ValueError(
            f"No Houdini answers on port {port}. Serving: "
            + (", ".join(f"{s['port']} (pid {s.get('pid')})" for s in others) or "none")
            + ". start_houdini starts one."
        )
    previous = bridge.port
    await bridge.retarget(port)
    return {"connected": True, "port": port, "previous_port": previous, "health": found[0]}


def _hython_script(hip_file: str | None) -> str:
    """What the headless hython runs: load the plugin, open the hip, serve until stopped."""
    scripts = (plugin_path() / "scripts" / "python").as_posix()
    load = f"hou.hipFile.load({hip_file!r}, suppress_save_prompt=True)\n" if hip_file else ""
    return (
        "import sys\n"
        f"sys.path.insert(0, {scripts!r})\n"
        "import hou\n"
        f"{load}"
        "from fxhoudinimcp_server import startup\n"
        "startup.start(background=False)\n"
    )


def houdini_env() -> tuple[dict[str, str], str | None]:
    """The environment a Houdini started from here gets, and a note when it was changed.

    Git Bash / MSYS shells (the Claude Code agent shell among them) export
    HOME=C:/Users/<you>. Houdini on Windows takes $HOME over Documents for its
    preference dir, so a Houdini started from such a shell reads
    C:/Users/<you>/houdini22.0, finds none of the packages installed in
    Documents/houdini22.0/packages (this plugin's included), and never serves.
    Dropping HOME puts it back on Documents, as when started from the Start menu.
    """
    env = os.environ.copy()
    if sys.platform == "win32" and "HOME" in env and "HOUDINI_USER_PREF_DIR" not in env:
        home = env.pop("HOME")
        return env, (
            f"HOME ({home}) was left out of Houdini's environment: with it set, Houdini "
            "reads its preferences and packages from $HOME instead of Documents. Set "
            "HOUDINI_USER_PREF_DIR to choose the preference dir explicitly."
        )
    return env, None


def projects_root(root: str | None = None) -> Path:
    """Where projects live: ``root``, else FXHOUDINIMCP_PROJECTS_DIR, else ~/HoudiniProjects."""
    value = (
        root or os.environ.get("FXHOUDINIMCP_PROJECTS_DIR") or str(Path.home() / "HoudiniProjects")
    )
    return Path(os.path.expandvars(os.path.expanduser(value)))


# Folders a new project gets; $JOB points at the project folder.
PROJECT_FOLDERS = ("hda", "scripts", "shots", "cache", "tex")


def _project_entry(folder: Path) -> dict[str, Any] | None:
    """Summary of one project folder, or None when it holds no hip file."""
    hips = sorted(
        (p for p in folder.glob("*.hip*") if p.suffix in (".hip", ".hiplc", ".hipnc")),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not hips:
        return None
    hdas = (
        sorted(p.name for p in (folder / "hda").glob("*.hda")) if (folder / "hda").is_dir() else []
    )
    hdas += sorted(p.name for p in folder.glob("*.hda"))
    newest = max(p.stat().st_mtime for p in hips)
    return {
        "name": folder.name,
        "path": folder.as_posix(),
        "hip_files": [p.name for p in hips],
        "hda_files": hdas,
        "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(newest)),
        "_mtime": newest,
    }


def find_projects(root: Path) -> list[dict[str, Any]]:
    """Every folder directly under ``root`` holding a hip file, newest first."""
    if not root.is_dir():
        return []
    entries = [e for f in root.iterdir() if f.is_dir() and (e := _project_entry(f))]
    entries.sort(key=lambda e: e.pop("_mtime"), reverse=True)
    return entries


@mcp.tool()
async def list_projects(ctx: Context, root: str | None = None) -> dict:
    """List the Houdini projects on disk: each folder under the projects root
    holding a hip file, with its hip and HDA files and last change, newest first.

    open_project opens one by name; new_project makes one.

    Args:
        root: Projects folder. Default: FXHOUDINIMCP_PROJECTS_DIR, else ~/HoudiniProjects.
    """
    base = projects_root(root)
    return {"root": base.as_posix(), "exists": base.is_dir(), "projects": find_projects(base)}


async def _set_job(bridge, folder: Path) -> None:
    await bridge.execute("code.execute_hscript", {"command": f'setenv JOB = "{folder.as_posix()}"'})


@mcp.tool()
async def new_project(
    ctx: Context, name: str, root: str | None = None, keep_scene: bool = True
) -> dict:
    """Make a project folder and save the current scene into it, before any work.

    Creates <root>/<name>/ with hda/, scripts/, shots/, cache/, tex/, sets $JOB
    to it, and saves the open scene as <name>.hip there, so $HIP-relative caches
    and renders land in the project instead of Houdini's install folder (where a
    fresh session's untitled.hip sits). Refuses a folder that already has a hip
    file: open_project opens that one.

    Args:
        name: Project folder and hip file name.
        root: Projects folder. Default: FXHOUDINIMCP_PROJECTS_DIR, else ~/HoudiniProjects.
        keep_scene: Save the current scene's contents; False starts an empty scene first.
    """
    bridge = _get_bridge(ctx)
    if not name or any(c in name for c in '\\/:*?"<>|'):
        raise ValueError(f"'{name}' is not a usable folder name.")
    folder = projects_root(root) / name
    if folder.is_dir() and _project_entry(folder):
        raise ValueError(
            f"{folder.as_posix()} already holds a project ({_project_entry(folder)['hip_files']}); "
            f"open_project('{name}') opens it."
        )
    for sub in PROJECT_FOLDERS:
        (folder / sub).mkdir(parents=True, exist_ok=True)
    if not keep_scene:
        await bridge.execute("scene.new_scene", {"save_current": False})
    await _set_job(bridge, folder)
    hip = (folder / f"{name}.hip").as_posix()
    saved = await bridge.execute("scene.save_scene", {"file_path": hip})
    return {
        "project": folder.as_posix(),
        "hip_file": saved.get("hip_file", hip) if isinstance(saved, dict) else hip,
        "job": folder.as_posix(),
        "folders": list(PROJECT_FOLDERS),
    }


@mcp.tool()
async def open_project(
    ctx: Context, name: str, root: str | None = None, hip_file: str | None = None
) -> dict:
    """Open a project by folder name: loads its newest hip file and sets $JOB.

    list_projects shows the names. A name that matches no folder exactly is
    matched case-insensitively, then as a unique substring.

    Args:
        name: Project folder name (or a unique part of it).
        root: Projects folder. Default: FXHOUDINIMCP_PROJECTS_DIR, else ~/HoudiniProjects.
        hip_file: Which hip in the folder to open. Default: the newest.
    """
    bridge = _get_bridge(ctx)
    base = projects_root(root)
    projects = find_projects(base)
    match = (
        [p for p in projects if p["name"] == name]
        or [p for p in projects if p["name"].lower() == name.lower()]
        or [p for p in projects if name.lower() in p["name"].lower()]
    )
    if len(match) != 1:
        names = [p["name"] for p in (match or projects)]
        raise ValueError(
            f"{'No' if not match else 'More than one'} project matches '{name}' under "
            f"{base.as_posix()}. {'Candidates' if match else 'Projects'}: {names or 'none'}."
        )
    project = match[0]
    hip = hip_file or project["hip_files"][0]
    if hip not in project["hip_files"]:
        raise ValueError(f"{hip} is not in {project['path']}: {project['hip_files']}.")
    loaded = await bridge.execute(
        "scene.load_scene", {"file_path": f"{project['path']}/{hip}", "merge": False}
    )
    await _set_job(bridge, Path(project["path"]))
    return {"project": project, "opened": hip, "job": project["path"], "load": loaded}


@mcp.tool()
async def wait_for_houdini(ctx: Context, timeout_seconds: float = 180) -> dict:
    """Block until a Houdini serves the plugin, then connect to it.

    For a Houdini someone else is opening (the user, or a desktop launch):
    replaces sleeping and re-polling get_houdini_connection_status. Returns as
    soon as one answers; if the bridge's port is silent, it moves to the first
    session found.

    Args:
        timeout_seconds: Give up after this long.
    """
    bridge = _get_bridge(ctx)
    started = time.monotonic()
    while True:
        sessions = await find_servers(bridge.host, BASE_PORT)
        if sessions:
            current = next((s for s in sessions if s["port"] == bridge.port), None)
            if current is None:
                current = sessions[0]
                await bridge.retarget(current["port"])
            return {
                "connected": True,
                "port": current["port"],
                "pid": current.get("pid"),
                "houdini_version": current.get("houdini_version"),
                "waited_seconds": round(time.monotonic() - started, 1),
                "sessions": len(sessions),
            }
        if time.monotonic() - started >= timeout_seconds:
            raise TimeoutError(
                f"No Houdini served on ports {BASE_PORT}+ within {timeout_seconds:g}s. A GUI "
                "Houdini serves only with the plugin package installed in the preference dir "
                "it reads; start_houdini starts one."
            )
        await asyncio.sleep(1.0)


def _log_tail(path: Path, lines: int = 20) -> str:
    try:
        return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
    except OSError:
        return ""


@mcp.tool()
async def start_houdini(
    ctx: Context,
    gui: bool = False,
    hip_file: str | None = None,
    wait_seconds: float = 180,
) -> dict:
    """Start a Houdini of this server's own and connect to it.

    Headless (hython) by default: no window, no viewport, so the capture tools
    refuse there. gui=True starts the full application, which serves only if
    the plugin's package is installed (fxhoudinimcp install). Either takes a
    license seat until stop_houdini. Set HYTHON to choose the build.

    Args:
        gui: Start the Houdini application instead of headless hython.
        hip_file: Scene to open.
        wait_seconds: How long to wait for it to serve.
    """
    bridge = _get_bridge(ctx)
    hython = find_hython()
    if hython is None:
        raise RuntimeError("No Houdini found. Install one, or set HYTHON to its hython executable.")
    if gui:
        name = "houdini.exe" if sys.platform == "win32" else "houdini"
        command = [str(hython.with_name(name))] + ([hip_file] if hip_file else [])
    else:
        command = [str(hython), "-c", _hython_script(hip_file)]

    before = {s.get("pid") for s in await find_servers(bridge.host, BASE_PORT)}
    log = (
        Path(tempfile.gettempdir()) / "fxhoudinimcp" / f"houdini_{time.time_ns() // 1_000_000}.log"
    )
    log.parent.mkdir(parents=True, exist_ok=True)
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    env, env_note = houdini_env()
    with open(log, "wb") as out:
        process = subprocess.Popen(
            command,
            stdout=out,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            env=env,
            creationflags=flags,
            start_new_session=sys.platform != "win32",
        )

    started = time.monotonic()
    while time.monotonic() - started < wait_seconds:
        if process.poll() is not None:
            tail = _log_tail(log)
            hint = ""
            if gui and not tail.strip():
                # Seen from an agent shell with no interactive desktop: the
                # GUI dies at once and writes nothing, while hython works.
                hint = (
                    "The GUI exited without writing anything, which is what it does when "
                    "this server's process has no interactive desktop to open a window on. "
                    "Open Houdini on the desktop yourself (it serves on its own when the "
                    "plugin package is installed) and call get_houdini_connection_status, "
                    "or call start_houdini without gui for a headless session. "
                )
            raise RuntimeError(
                f"Houdini exited with code {process.returncode} before serving. {hint}"
                f"Last lines of {log}:\n{tail or '(empty)'}"
            )
        # A GUI launcher can hand off to another pid, so any new session counts.
        new = [s for s in await find_servers(bridge.host, BASE_PORT) if s.get("pid") not in before]
        if new:
            session = next((s for s in new if s.get("pid") == process.pid), new[0])
            previous = bridge.port
            _started[session["pid"]] = (process, session["port"], previous)
            await bridge.retarget(session["port"])
            result = {
                "started": True,
                "mode": "gui" if gui else "headless",
                "pid": session["pid"],
                "port": session["port"],
                "previous_port": previous,
                "seconds": round(time.monotonic() - started, 1),
                "log": str(log),
            }
            if env_note:
                result["env_note"] = env_note
            return result
        await asyncio.sleep(1.0)

    raise TimeoutError(
        f"Houdini (pid {process.pid}) did not serve within {wait_seconds:g}s and is still "
        f"running; stop_houdini(pid={process.pid}) ends it. "
        + (
            "With gui=True the plugin serves only if its package is installed in the "
            "preference dir Houdini reads (a 'Start Here' dialog on screen means it found "
            "a fresh, empty one). "
            if gui
            else ""
        )
        + f"Last lines of {log}:\n{_log_tail(log)}"
    )


@mcp.tool()
async def stop_houdini(ctx: Context, pid: int | None = None) -> dict:
    """Stop a Houdini that start_houdini started, and go back to the previous session.

    Only sessions started by this server can be stopped: anything else may be
    someone's unsaved work.

    Args:
        pid: Session to stop. Default: the current one, if this server started it.
    """
    bridge = _get_bridge(ctx)
    if pid is None:
        pid = next((p for p, (_, port, _) in _started.items() if port == bridge.port), None)
    if pid not in _started:
        raise ValueError(
            "That session was not started by this server, so it is not stopped. "
            f"Started here: {sorted(_started) or 'none'}."
        )
    process, port, previous = _started.pop(pid)
    if process.pid == pid:
        process.terminate()
        try:
            await asyncio.to_thread(process.wait, 15)
        except subprocess.TimeoutExpired:
            process.kill()
    else:
        # A GUI launcher handed off to this pid; the launcher is not what serves.
        os.kill(pid, signal.SIGTERM)
    back = None
    if bridge.port == port and await find_servers(bridge.host, previous, max_tries=1):
        await bridge.retarget(previous)
        back = previous
    return {"stopped": pid, "port": port, "reconnected_to": back}
