# Houdini + Unreal workflow (fork setup)

How this fork is set up and used day to day: an AI agent (Claude Code) drives Houdini through
fxhoudinimcp, and the results go to Unreal Engine 5.8 through Houdini Engine (HDAs, landscapes,
instancing) and SideFX Labs (vertex animation textures). Follow it top to bottom on a new machine.

Tested with Houdini 22.0.508, SideFX Labs 22.0, Unreal Engine 5.8, Windows 11, Python 3.12.

## 1. Houdini and licensing

1. Install the **SideFX Launcher** and sign in with your SideFX account.
2. In the Launcher, install **Houdini 22.0** (production build). Also tick **Houdini Engine for Unreal**
   and **SideFX Labs** (Labs lands in `C:/Program Files/Side Effects Software/sidefx_packages/SideFXLabs22.0`).
3. Licensing: open **License Administrator** → *File > Login* and use the SideFX-hosted (cloud) licenses
   tied to your account. No local license server is needed. Check that Houdini opens without the
   Apprentice watermark.

## 2. The MCP (this fork)

```shell
git clone https://github.com/Nighty13/fxhoudinimcp.git
cd fxhoudinimcp
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
```

**Point Houdini at the clone** (the plugin loads from `houdini/` in the clone, so `git pull` updates it).
Write `Documents/houdini22.0/packages/fxhoudinimcp.json`, saved as UTF-8 **without a BOM**:

```json
{
  "env": [
    {"FXHOUDINIMCP": "C:/path/to/fxhoudinimcp/houdini"},
    {"FXHOUDINIMCP_PORT": "8100"},
    {"FXHOUDINIMCP_BIND": "127.0.0.1"},
    {"FXHOUDINIMCP_AUTOSTART": "1"},
    {"FXHOUDINIMCP_AUTO_LAYOUT": "1"},
    {"FXHOUDINIMCP_TIMEOUT": "120"}
  ],
  "path": "$FXHOUDINIMCP"
}
```

`AUTOSTART=1` starts the plugin's server with Houdini (otherwise use the **MCP** menu → Start).

**Register the server with Claude Code** (use the venv's absolute Python path):

```shell
claude mcp add --scope user fxhoudini -- "C:\path\to\fxhoudinimcp\.venv\Scripts\python.exe" -m fxhoudinimcp
```

Restart Houdini and the client, then ask the agent to call `get_houdini_connection_status`.
See the README's *Installation → Troubleshooting* if the MCP menu is missing.

**Developing the fork:**

- Tests: `.venv\Scripts\python -m pytest tests -m "not integration"`; lint: `ruff check .` and `ruff format --check .`
- After adding a handler, regenerate `required_commands.json` with `python tools/gen_required_commands.py`.
- Hot-reload edited handlers in a running Houdini, without restarting it:
  `python extras/scripts/hcall.py code.execute_python --py extras/scripts/reload_plugin.py r`
- `extras/scripts/hcall.py` calls any plugin command directly (no MCP client), handy for scripts.
- The friction log the fork is shaped from is `notes/agent-ux-notes.md`. Add to it whenever a tool is
  missing, wrong, or wastes tokens.

## 3. Unreal Engine project setup

**One command (recommended):**

```powershell
python extras/unreal/setup_unreal_project.py D:/path/MyProject/MyProject.uproject
claude mcp add --transport http --scope user unreal http://127.0.0.1:8000/mcp
```

The script copies SideFX's prebuilt Houdini Engine plugin (after checking its BuildId matches the engine) and
this repo's `extras/unreal/HoudiniEngineToolset` into `<Project>/Plugins/`. It enables Houdini Engine, Python,
Epic's **Unreal MCP** (`ModelContextProtocol`, experimental in 5.8) and `EditorToolset`. It also turns on Python
remote execution and auto-starts the MCP server on port 8000. Backups are kept as `*.bak_setup`. Open the
project once and the agent gets Epic's toolsets (viewport capture, logs, actors, assets, PCG...) plus
`HoudiniEngineTools`: `import_hda`, `spawn_hda`, `list_hda_actors`, `get_hda_parameters`, `set_hda_parameters`,
`recook_hda`, `get_hda_status`, `get_hda_inputs`, `set_hda_curve_input`, `set_hda_object_input`, `refine_hda`, `restart_houdini_session`, `frame_hda`, `bake_hda`. Anything else is plain editor Python: `unreal_python` (fxhoudinimcp) or `ue_exec.py`, with reusable scripts in `extras/unreal/recipes/`.
Unreal MCP exposes three tools (`list_toolsets`, `describe_toolset`, `call_tool`); toolset names are the full
Python path, e.g. `houdini_engine_toolset.toolset.HoudiniEngineTools`.
`extras/unreal/ue_mcp_call.py` calls it from scripts; `ue_exec.py` (below) stays the escape hatch for anything
no tool covers, and also hot-reloads the toolset after an edit (re-import the module, unregister, register).

Gotchas found on the way:
- An HDA with an optional curve input receives a default 2-point spline from Houdini Engine; guard the
  input (`npoints(...) >= 3`) or the asset builds from that line.
- `CaptureViewport` needs `captureTransform` and `annotations` even though it calls them optional
  (`frame_hda` returns a ready `captureTransform`).
- Houdini Engine's default material ignores the HDA's `Cd`; colours need an `unreal_material` with a Vertex Color node.
- From Git Bash, `/Game/...` arguments get rewritten to `C:/Program Files/Git/Game/...`: set `MSYS_NO_PATHCONV=1`.

**By hand:**

1. **Plugins** (copy into `<Project>/Plugins/`, then enable them in the `.uproject`):
   - `HoudiniEngine`: from `C:/Program Files/Side Effects Software/Houdini Engine/Unreal/22.0.508/5.8/`.
   - `SideFX_Labs`: from `.../sidefx_packages/SideFXLabs22.0/unreal/5.8/SideFX_Labs`. Copy only the main plugin
     folder; it carries the VAT materials and the Python `labs_vat_importer`.
   - Also enable `PythonScriptPlugin`, `EditorScriptingUtilities`, and `PCG` if you scatter with PCG.
2. **Python remote execution**, so the agent can drive the editor from outside. In `Config/DefaultEngine.ini`:

   ```ini
   [/Script/PythonScriptPlugin.PythonScriptPluginSettings]
   bDeveloperMode=True
   bRemoteExecution=True
   ```

3. **Rendering** (Lumen, Virtual Shadow Maps, Nanite foliage). DX12 with SM6, otherwise the editor nags that
   VSM needs SM6:

   ```ini
   [/Script/WindowsTargetPlatform.WindowsTargetSettings]
   DefaultGraphicsRHI=DefaultGraphicsRHI_DX12
   -D3D12TargetedShaderFormats=PCD3D_SM5
   +D3D12TargetedShaderFormats=PCD3D_SM6
   ```

4. Open the project once in the editor, so the plugins compile and Remote Execution starts.

**Running Python in the editor from outside**: `python extras/unreal/ue_exec.py some_script.py` runs a
file inside the open editor and prints its output (set `UE_ROOT` if Unreal isn't in
`C:\Program Files\Epic Games\UE_5.8`). All of the `extras/unreal/*.py` scripts are meant to run this way.

Don't use `UnrealEditor.exe -ExecutePythonScript=...` for this; it quits the editor when the script ends.

## 4. Pipelines that worked

### World / procedural assets → Houdini Engine

- Build the generator as an HDA (`create_hda`), with the spline, seed and density controls promoted.
- Engine conventions that matter:
  - landscape paint layers come **only** from the heightfield layer names (material layer names);
  - `unreal_instance` on points instances an asset;
  - `unreal_material` sets the material;
  - `unreal_num_custom_floats` with `unreal_per_instance_custom_data0..N` feed per-instance shader data;
  - `unreal_landscape_layer_noweightblend` controls landscape weight blending.
- Drop the HDA in a level (or spawn it with Python through `ue_exec.py`) and let Engine cook it.
- Mask foliage out of water and steep slopes in Houdini, before export; don't rely on Unreal to do it.

### Destruction → VAT → Unreal

1. RBD in SOPs:
   - fracture with `rbdmaterialfracture`;
   - simulate with `rbdbulletsolver`;
   - embedded rebar needs `collisiongroup` + `collisionignore` point attributes, or Bullet explodes the wall;
   - use soft, plastic `rbdconstraintproperties` links so bars bend and hold.
2. Before the bake, packed pieces need `pivot`, `orient` and `Cd` on points (Cd feeds the colour texture).
3. Bake with **Labs Vertex Animation Textures 4.0** (Rigid mode) to `$HIP/export/vat`.
4. **Known Labs 4.0 bug on Houdini 22.0:**
   - The VAT ROP's own FBX comes out empty. Object-merge `/out/<vat>/export/MESH/EXPORT` into an object and
     write it with a normal FBX ROP to `export/vat/geo/<name>_mesh.fbx`.
   - That needs **Enable Cooking** (`enablecooking=1`) on the VAT ROP. Leave it **on while baking**: with it off,
     the bake writes all-zero textures and reports no error.
   - The MCP's `start_render` now flags this (`flat_images` in the result). Re-run the FBX export after
     every bake.
5. In Unreal, run `extras/unreal/vat_import.py` (edit its paths first). It uses the Labs importer
   (`labs_vat_importer.import_vat`) to make the mesh, textures (HDR, nearest filtering, no mips) and the
   material instance, plus a test level.
6. Judge the result: `extras/unreal/vat_freeze_frames.py` places copies frozen at chosen frames
   (Auto Playback off, Display Frame set), then `extras/unreal/screenshot.py` saves a viewport shot.

### Screenshots from Unreal

- `SceneCapture2D` exports often come back black (the editor throttles in the background). Use the viewport
  screenshot in `screenshot.py`.
- The editor renders **nothing while minimized**: run `extras/unreal/restore_editor.ps1` first. Files land in
  `<Project>/Saved/Screenshots/WindowsEditor/`.

## 5. Working habits for the agent

- Check results with numbers before pictures:
  - `get_attrib_stats` percentiles for sims (displacement, speed, broken %);
  - `verify_network` and `get_node_errors_detailed` for errors;
  - `get_network_overview` to read graphs as text.
- When a network is finished, call `tidy_network` with titled sections and a note each. It boxes and
  documents the stages; reviewers shouldn't have to read a wall of nodes.
- Read rendered outputs back. A clean `start_render` with `flat_images` means the data never reached the writer.
- Reset SOP-level solvers (`rbdbulletsolver`) by pressing their `resetsim` button. `reset_simulation` only
  knows DOP networks, and stale caches look like "my change did nothing".
