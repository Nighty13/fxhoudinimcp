You are exporting a Houdini particle or point simulation as a point cache for
Unreal Engine 5.8 Niagara.

Goal: {description}

The SideFX Labs Niagara ROP writes a point cache that the **HoudiniNiagara**
plugin reads in Unreal through a Houdini Niagara data interface. The plugin
ships with Houdini Engine for Unreal 22.0.508 (UE 5.7 and 5.8). Use Niagara for
particles that should stay particles in UE (sparks, debris, dust, leaves); use
`get_workflow_guide("vat")` for meshes that deform.

Node names present in this repo's sampled builds (20.5-22.0):
`labs::niagara_rop` (Driver, in /out) and `labs::niagara::2.0` (SOP-level
export); helpers `labs::niagara_rbd_split`, `labs::niagara_impacts`,
`labs::niagara_interpolate`. Read the card (`get_node_card`) for parm names
before setting anything; this guide does not name them.

## Attributes to keep

Export only what Niagara will read. Every extra attribute costs file size and
load time, and a heightfield-scatter or POP network leaves dozens behind.

| Attribute | Why |
|---|---|
| `P` | position (required) |
| `id` | stable particle identity across frames (required for spawn/death and interpolation) |
| `v` | velocity, for motion blur, orientation and interpolation between samples |
| `age`, `life` | normalised age drives colour/size over life in Niagara |
| `Cd` | colour |
| `pscale` | size |
| `orient` / `N` / `up` | only for meshes or oriented sprites |

attribdelete with a keep pattern (`* ^P ^id ^v ^age ^life ^Cd ^pscale`) right
before the ROP.

## Time, spawn and death

- **Frame rate**: the cache stores samples per frame at the hip's `$FPS`. Set
  `$FPS` to what the effect is authored for before simulating, not after.
- **`id` must be unique and stable**: a particle keeps its id for its whole
  life; a new particle never reuses a dead one's id in the same cache. POP
  networks do this by default; anything rebuilt per frame (scatter + copy)
  needs ids assigned once and carried.
- **Spawn**: a particle's first sample is its spawn time; Niagara sees it appear
  there. **Death**: it is absent from later samples. Do not leave dead particles
  at the origin or at a far-away "kill" position; delete them in Houdini.
- Keep the point count per frame inside what the effect can afford; check the
  peak count, not the last frame's.

## UE side (from the plugin's documented design; not verified in this repo's sessions)

These are not checked by any session recorded in this repo's notes; confirm in
the plugin's own documentation and in UE before relying on details:

- The cache imports as a Houdini point-cache asset that a Niagara emitter
  references through the Houdini Niagara data interface, which samples points by
  time (and by id) for position, colour and any exported attribute.
- Playback length in UE follows the cache's sample times, so a frame-rate
  mismatch shows up as a too-fast or too-slow effect.

## Verify (before importing into UE)

1. `verify_network(<sim net>, force_cook=True)`: healthy.
2. `get_geometry_info(<node feeding the ROP>)`: only the attributes in the table
   above, each in the expected class (point).
3. `get_attrib_stats(<that node>, attribs=["id"], unique=True, frames=[first, mid, last])`:
   `unique_count` equals the point count on every frame (no duplicate ids).
4. Same call with `attribs=["P", "v", "age/life", "pscale"]`: `age/life` in 0..1,
   `pscale` > 0, no `P` component far outside the effect's bounds (particles
   parked at a kill position), `element_count` per frame gives spawn and death
   over time and the peak.
5. `start_render(<niagara rop>, frame_range=[a, b])`: the cache file is on disk
   and not tiny.
6. `evaluate_expression("$FPS")` agrees with the frame rate the effect is authored for.

{network_housekeeping}
