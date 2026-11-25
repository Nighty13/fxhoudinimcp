"""Prepare an Unreal project for agent work with Houdini Engine, in one command.

    python setup_unreal_project.py D:/path/MyProject/MyProject.uproject [--houdini 22.0.508] [--port 8000]

What it does (idempotent; the .uproject and each .ini are backed up once, as *.bak_setup):
  1. Copies SideFX's prebuilt Houdini Engine plugin for the project's engine version into <Project>/Plugins/,
     after checking its BuildId matches the engine's (a mismatch means it would need compiling).
  2. Copies this repo's HoudiniEngineToolset plugin (Python only) into <Project>/Plugins/.
  3. Enables in the .uproject: HoudiniEngine, HoudiniEngineToolset, PythonScriptPlugin,
     EditorScriptingUtilities, ModelContextProtocol (Epic's Unreal MCP), EditorToolset.
  4. Config/DefaultEngine.ini: Python remote execution on, bound to 127.0.0.1 (for extras/unreal/ue_exec.py).
  5. Config/DefaultEditorPerProjectUserSettings.ini: the Unreal MCP server starts with the editor on --port.

Then open the project once; the MCP server answers at http://127.0.0.1:<port>/mcp. Register it with
Claude Code:  claude mcp add --transport http unreal http://127.0.0.1:<port>/mcp
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import winreg
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLSET = HERE / "HoudiniEngineToolset"
PLUGINS = [
    "HoudiniEngine",
    "HoudiniEngineToolset",
    "PythonScriptPlugin",
    "EditorScriptingUtilities",
    "ModelContextProtocol",
    "EditorToolset",
]
SIDEFX = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Side Effects Software"


def engine_dir(association: str) -> Path:
    """Engine root for an EngineAssociation: a launcher version ("5.8") or a source build GUID."""
    if association.startswith("{"):
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, r"Software\Epic Games\Unreal Engine\Builds"
        ) as key:
            return Path(winreg.QueryValueEx(key, association)[0])
    dat = (
        Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData"))
        / "Epic/UnrealEngineLauncher/LauncherInstalled.dat"
    )
    for item in json.loads(dat.read_text(encoding="utf-8"))["InstallationList"]:
        if item.get("AppName") == f"UE_{association}":
            return Path(item["InstallLocation"])
    raise SystemExit(
        f"Unreal {association} is not installed through the launcher; pass a source build."
    )


def build_id(modules_file: Path) -> str | None:
    try:
        return json.loads(modules_file.read_text(encoding="utf-8")).get("BuildId")
    except OSError:
        return None


def houdini_engine_plugin(houdini: str | None, ue_version: str) -> Path:
    root = SIDEFX / "Houdini Engine" / "Unreal"
    versions = [houdini] if houdini else sorted((p.name for p in root.iterdir()), reverse=True)
    for v in versions:
        plugin = root / v / ue_version / "HoudiniEngine"
        if (plugin / "HoudiniEngine.uplugin").is_file():
            return plugin
    raise SystemExit(
        f"No Houdini Engine build for UE {ue_version} under {root} (versions {versions})."
    )


def backup(path: Path) -> None:
    copy = path.with_name(path.name + ".bak_setup")
    if path.is_file() and not copy.exists():
        shutil.copy2(path, copy)


def set_ini(path: Path, section: str, values: dict[str, str]) -> None:
    """Set keys in one [section] of an Unreal .ini, keeping everything else."""
    backup(path)
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    header = f"[{section}]"
    if header not in text:
        text = text.rstrip("\n") + f"\n\n{header}\n"
    start = text.index(header) + len(header)
    nxt = re.search(r"^\[", text[start:], re.M)
    end = start + nxt.start() if nxt else len(text)
    body = text[start:end]
    for key, value in values.items():
        line = f"{key}={value}"
        body, n = re.subn(rf"^{re.escape(key)}=.*$", line, body, flags=re.M)
        if not n:
            body = body.rstrip("\n") + f"\n{line}\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        text[:start] + body.rstrip("\n") + "\n" + text[end:].lstrip("\n"), encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("uproject", type=Path)
    parser.add_argument(
        "--houdini", help="Houdini version whose Engine plugin to use (default: newest)"
    )
    parser.add_argument("--port", type=int, default=8000, help="Unreal MCP server port")
    args = parser.parse_args()

    uproject = args.uproject.resolve()
    project = uproject.parent
    data = json.loads(uproject.read_text(encoding="utf-8"))
    engine = engine_dir(data["EngineAssociation"])
    version = ".".join(
        str(json.loads((engine / "Engine/Build/Build.version").read_text())[k])
        for k in ("MajorVersion", "MinorVersion")
    )
    report = {"project": str(project), "engine": str(engine), "ue_version": version}

    source = houdini_engine_plugin(args.houdini, version)
    plugin_id = build_id(source / "Binaries/Win64/UnrealEditor.modules")
    engine_id = build_id(engine / "Engine/Binaries/Win64/UnrealEditor.modules")
    if plugin_id != engine_id:
        raise SystemExit(
            f"Houdini Engine at {source} has BuildId {plugin_id}, engine has {engine_id}: rebuild needed."
        )
    dest = project / "Plugins" / "HoudiniEngine"
    if not dest.exists():
        shutil.copytree(source, dest, ignore=shutil.ignore_patterns("Intermediate"))
    report["houdini_engine"] = str(source)

    shutil.copytree(
        TOOLSET,
        project / "Plugins" / "HoudiniEngineToolset",
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__"),
    )

    backup(uproject)
    enabled = {p["Name"] for p in data.setdefault("Plugins", []) if p.get("Enabled")}
    for p in data["Plugins"]:
        if p["Name"] in PLUGINS:
            p["Enabled"] = True
    data["Plugins"] += [
        {"Name": n, "Enabled": True}
        for n in PLUGINS
        if n not in {p["Name"] for p in data["Plugins"]}
    ]
    uproject.write_text(json.dumps(data, indent="\t") + "\n", encoding="utf-8")
    report["plugins_enabled_now"] = sorted(set(PLUGINS) - enabled)

    set_ini(
        project / "Config/DefaultEngine.ini",
        "/Script/PythonScriptPlugin.PythonScriptPluginSettings",
        {"bRemoteExecution": "True", "RemoteExecutionMulticastBindAddress": "127.0.0.1"},
    )
    set_ini(
        project / "Config/DefaultEditorPerProjectUserSettings.ini",
        "/Script/ModelContextProtocolEngine.ModelContextProtocolSettings",
        {"bAutoStartServer": "True", "ServerPortNumber": str(args.port)},
    )
    report["mcp_url"] = f"http://127.0.0.1:{args.port}/mcp"
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    sys.exit(main())
