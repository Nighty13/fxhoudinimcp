"""Agent-UX batch 2, geometry: numbers that replaced execute_python loops.

- sample_volume(node_paths=[A, B], compare_to="P.y"): per-point signed delta,
  percentiles, counts beyond a tolerance, histogram and the worst points (#23);
- get_attrib_stats(group_by=..., buckets=..., reduce=[...]) and
  list_if_count_below (#24): "water width per row" and "the 3 waterfall points";
- get_volume_info(compact=True): one row per layer (#22);
- sim_report: one row per frame of count, displacement, speed, broken % (#29).

hou is mocked here; the numeric logic lives in pure helpers tested directly.
"""

from __future__ import annotations

# Built-in
import os
import sys
from unittest.mock import AsyncMock, MagicMock

# Third-party
import numpy as np
import pytest

sys.modules.setdefault("hou", MagicMock())
sys.modules.setdefault("hdefereval", MagicMock())
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "houdini", "scripts", "python"))

# Internal
import fxhoudinimcp_server.handlers.geometry_handlers as geometry  # noqa: E402

hou = geometry.hou


class _Attrib:
    def __init__(self, name, size, data_type):
        self._name, self._size, self._type = name, size, data_type

    def name(self):
        return self._name

    def size(self):
        return self._size

    def dataType(self):
        return self._type

    def isArrayType(self):
        return False


class _Point:
    def __init__(self, geo, index):
        self._geo, self._index = geo, index

    def attribValue(self, name):
        size, values = self._geo.columns[name]
        row = values[self._index * size : (self._index + 1) * size]
        return row[0] if size == 1 else tuple(row)


class _Geo:
    """Point geometry from {name: (size, flat values)}; strings are str lists."""

    def __init__(self, columns, counts=None):
        self.columns = columns
        self._counts = counts or {}

    def _type(self, name):
        values = self.columns[name][1]
        if values and isinstance(values[0], str):
            return hou.attribData.String
        if values and all(isinstance(v, int) for v in values):
            return hou.attribData.Int
        return hou.attribData.Float

    def pointAttribs(self):
        return [_Attrib(n, self.columns[n][0], self._type(n)) for n in self.columns]

    def findPointAttrib(self, name):
        if name not in self.columns:
            return None
        return _Attrib(name, self.columns[name][0], self._type(name))

    def pointFloatAttribValues(self, name):
        return self.columns[name][1]

    pointIntAttribValues = pointFloatAttribValues
    pointStringAttribValues = pointFloatAttribValues

    def pointFloatAttribValuesAsString(self, *_):
        raise RuntimeError("no buffer in the fake")  # the list path is tested

    pointIntAttribValuesAsString = pointFloatAttribValuesAsString

    def intrinsicValue(self, name):
        return self._counts[name]

    def point(self, index):
        return _Point(self, index)

    # Listers the stats handler binds up front; this fake has points only.
    def points(self):
        return [_Point(self, i) for i in range(self._counts.get("pointcount", 0))]

    def prims(self):
        return []

    def primAttribs(self):
        return []

    def vertexAttribs(self):
        return []

    def globalAttribs(self):
        return []


@pytest.fixture
def real_errors(monkeypatch):
    # hou is a MagicMock here, and an except clause needs a real class.
    monkeypatch.setattr(hou, "OperationFailed", RuntimeError)


###### #24 group_by and list_if_count_below


class TestSplitComponent:
    @pytest.mark.parametrize(
        ("spec", "expected"),
        [("P.y", ("P", 1)), ("uv[0]", ("uv", 0)), ("fuel", ("fuel", None)), ("Cd.b", ("Cd", 2))],
    )
    def test_specs(self, spec, expected):
        assert geometry._split_component(spec) == expected

    def test_junk_is_refused(self):
        with pytest.raises(ValueError, match="not an attribute name"):
            geometry._split_component("P.q")


class TestGroupByTable:
    def test_width_per_row_is_the_spread_per_exact_value(self):
        # Two rows of a lattice: s is constant per row, uv.x spans the row.
        keys = np.array([0.0, 0.0, 0.0, 0.5, 0.5])
        uv = np.array([[0.1, 0], [0.5, 0], [0.9, 0], [0.3, 0], [0.4, 0]])
        table = geometry._group_by_table(keys, {"uv": uv}, numeric=True)
        assert table["group_count"] == 2
        first, second = table["groups"]
        assert (first["key"], first["count"]) == (0.0, 3)
        assert first["uv"]["spread"][0] == pytest.approx(0.8)
        assert second["uv"]["spread"][0] == pytest.approx(0.1)
        assert second["uv"]["min"] == pytest.approx([0.3, 0.0])

    def test_int_keys_stay_ints_and_scalars_stay_scalars(self):
        table = geometry._group_by_table(
            np.array([2.0, 1.0, 2.0]),
            {"h": np.array([[1.0], [5.0], [3.0]])},
            numeric=True,
            integer=True,
            reduce=["mean", "max"],
        )
        assert [g["key"] for g in table["groups"]] == [1, 2]
        assert isinstance(table["groups"][0]["key"], int)
        assert table["groups"][1]["h"] == {"mean": 2.0, "max": 3.0}

    def test_buckets_are_equal_width_over_the_range(self):
        keys = np.array([0.0, 1.0, 2.0, 9.0, 10.0])
        table = geometry._group_by_table(
            keys, {"k": keys.reshape(-1, 1)}, numeric=True, buckets=2, reduce=["count"]
        )
        assert [g["range"] for g in table["groups"]] == [[0.0, 5.0], [5.0, 10.0]]
        assert [g["count"] for g in table["groups"]] == [3, 2]  # the max lands in the last bin

    def test_bucket_size_aligns_to_zero_and_skips_empty_bins(self):
        keys = np.array([0.05, 0.15, 0.95])
        table = geometry._group_by_table(keys, {}, numeric=True, bucket_size=0.1)
        ranges = np.array([g["range"] for g in table["groups"]])
        assert ranges == pytest.approx(np.array([[0.0, 0.1], [0.1, 0.2], [0.9, 1.0]]))

    def test_many_distinct_floats_are_bucketed_with_a_note(self):
        keys = np.linspace(0, 1, 200)
        table = geometry._group_by_table(keys, {}, numeric=True, max_groups=50)
        assert len(table["groups"]) == 10
        assert "bucketed into 10" in table["note"]

    def test_string_keys(self):
        keys = ["rock", "tree", "rock"]
        table = geometry._group_by_table(
            keys, {"pscale": np.array([[1.0], [4.0], [3.0]])}, numeric=False
        )
        rock = table["groups"][0]
        assert (rock["key"], rock["count"], rock["pscale"]["max"]) == ("rock", 2, 3.0)

    def test_too_many_strings_keep_the_most_common(self):
        keys = ["a"] * 5 + ["b"] * 3 + ["c"]
        table = geometry._group_by_table(keys, {}, numeric=False, max_groups=2)
        assert [g["key"] for g in table["groups"]] == ["a", "b"]
        assert table["group_count"] == 3 and "most common" in table["note"]

    def test_bad_options_are_refused(self):
        with pytest.raises(ValueError, match="Unknown reduce"):
            geometry._group_by_table([1.0], {}, numeric=True, reduce=["p99"])
        with pytest.raises(ValueError, match="not both"):
            geometry._group_by_table([1.0], {}, numeric=True, buckets=2, bucket_size=1)
        with pytest.raises(ValueError, match="numeric"):
            geometry._group_by_table(["a"], {}, numeric=False, buckets=2)


class TestAttribStatsGroupBy:
    @pytest.fixture
    def lattice(self, monkeypatch, real_errors):
        geo = _Geo(
            {
                "row": (1, [0, 0, 1, 1, 1]),
                "uv": (2, [0.0, 0, 1.0, 0, 0.2, 0, 0.4, 0, 0.6, 0]),
                "kind": (1, ["bank", "bank", "bed", "bed", "bank"]),
            },
            counts={"pointcount": 5},
        )
        monkeypatch.setattr(geometry, "_get_sop_geo", lambda path: geo)
        return geo

    def test_group_by_answers_per_row(self, lattice):
        result = geometry._get_attrib_stats(
            node_path="/obj/river/lattice", attribs=["uv"], group_by="row"
        )
        groups = result["group_by"]["groups"]
        assert result["group_by"]["attribute"] == "row"
        assert [g["key"] for g in groups] == [0, 1]
        assert groups[0]["uv"]["spread"][0] == pytest.approx(1.0)
        assert groups[1]["uv"]["spread"][0] == pytest.approx(0.4)
        assert result["stats"]["uv"]["count"] == 5  # the overall stats are still there

    def test_group_by_a_component(self, lattice):
        result = geometry._get_attrib_stats(
            node_path="/x", attribs=["row"], group_by="uv.x", buckets=2, reduce=["max"]
        )
        assert [g["count"] for g in result["group_by"]["groups"]] == [3, 2]

    def test_a_vector_key_needs_a_component(self, lattice):
        with pytest.raises(ValueError, match="uv.x"):
            geometry._get_attrib_stats(node_path="/x", attribs=["row"], group_by="uv")

    def test_a_missing_key_names_what_there_is(self, lattice):
        with pytest.raises(ValueError, match="kind, row, uv"):
            geometry._get_attrib_stats(node_path="/x", attribs=["row"], group_by="nope")

    def test_buckets_without_group_by_are_refused(self, lattice):
        with pytest.raises(ValueError, match="group_by"):
            geometry._get_attrib_stats(node_path="/x", buckets=3)

    def test_a_string_key_and_the_string_attribute_is_not_aggregated(self, lattice):
        result = geometry._get_attrib_stats(
            node_path="/x", attribs=["row", "kind"], group_by="kind", reduce=["mean"]
        )
        groups = {g["key"]: g for g in result["group_by"]["groups"]}
        assert groups["bank"]["count"] == 3
        assert groups["bed"]["row"] == {"mean": 1.0}

    def test_a_group_narrows_before_grouping(self, lattice):
        lattice.globPoints = lambda pattern: [
            MagicMock(number=lambda: 0),
            MagicMock(number=lambda: 4),
        ]
        result = geometry._get_attrib_stats(
            node_path="/x", attribs=["uv"], group_by="row", group="sel"
        )
        assert [(g["key"], g["count"]) for g in result["group_by"]["groups"]] == [(0, 1), (1, 1)]

    def test_small_counts_list_the_elements(self, lattice):
        result = geometry._get_attrib_stats(
            node_path="/x", attribs=["uv", "kind"], list_if_count_below=5
        )
        assert result["elements"][2] == {"index": 2, "uv": [0.2, 0.0], "kind": "bed"}
        assert len(result["elements"]) == 5

    def test_listing_follows_the_group(self, lattice):
        lattice.globPoints = lambda pattern: [MagicMock(number=lambda: 3)]
        result = geometry._get_attrib_stats(
            node_path="/x", attribs=["row"], group="falls", list_if_count_below=3
        )
        assert result["elements"] == [{"index": 3, "row": 1}]

    def test_large_counts_say_why_nothing_is_listed(self, lattice):
        result = geometry._get_attrib_stats(node_path="/x", attribs=["row"], list_if_count_below=2)
        assert "elements" not in result
        assert "list_if_count_below=2" in result["elements_note"]

    def test_detail_refuses_group_by(self, lattice):
        with pytest.raises(ValueError, match="not detail"):
            geometry._get_attrib_stats(node_path="/x", attrib_class="detail", group_by="row")


###### #22 get_volume_info compact


def _layer(name, low, high, mean, stats=None):
    entry = {
        "name": name,
        "resolution": [1009, 1009, 1],
        "bbox_min": [-504.5, -0.5, -504.5],
        "bbox_max": [504.5, 0.5, 504.5],
        "voxel_size": [1.0, 1.0, 1.0],
        "min_value": low,
        "max_value": high,
        "mean_value": mean,
        "first_voxel_center": [-504.0, 0.0, -504.0],
        "last_voxel_center": [504.0, 0.0, 504.0],
    }
    if stats:
        entry["voxel_stats"] = stats
    return entry


class TestCompactVolumeTable:
    def test_one_row_per_layer(self):
        table = geometry._compact_volume_table(
            [_layer("height", -12.3456789, 88.0, 20.5), _layer("mask", 0.0, 1.0, 0.25)]
        )
        assert table["columns"] == [
            "name",
            "res",
            "bbox_min",
            "bbox_max",
            "voxel",
            "min",
            "max",
            "mean",
        ]
        assert table["rows"][0] == [
            "height",
            [1009, 1009, 1],
            [-504.5, -0.5, -504.5],
            [504.5, 0.5, 504.5],
            1.0,
            -12.3457,
            88.0,
            20.5,
        ]

    def test_percentiles_and_threshold_share_when_voxels_were_read(self):
        stats = {
            "percentiles": {"50": 0.5, "95": 0.9},
            "threshold": {"share_above": 0.125},
        }
        table = geometry._compact_volume_table([_layer("height", 0.0, 1.0, 0.5, stats)])
        assert table["columns"][-3:] == ["p50", "p95", "share_above"]
        assert table["rows"][0][-3:] == [0.5, 0.9, 0.125]

    def test_a_non_cubic_voxel_keeps_its_three_sizes(self):
        entry = _layer("density", 0.0, 1.0, 0.1)
        entry["voxel_size"] = [1.0, 2.0, 1.0]
        assert geometry._compact_volume_table([entry])["rows"][0][4] == [1.0, 2.0, 1.0]

    def test_the_handler_answers_the_table_only_when_asked(self, monkeypatch):
        monkeypatch.setattr(geometry, "_get_sop_geo", lambda path: MagicMock())
        monkeypatch.setattr(geometry, "_volume_prims", lambda geo: ["h", "m"])
        monkeypatch.setattr(geometry, "_volume_entry", lambda prim: _layer(prim, 0.0, 1.0, 0.5))
        full = geometry._get_volume_info(node_path="/obj/hf")
        assert "volumes" in full and "rows" not in full
        compact = geometry._get_volume_info(node_path="/obj/hf", compact=True)
        assert "volumes" not in compact
        assert [row[0] for row in compact["rows"]] == ["h", "m"]
        assert compact["volume_count"] == 2 and compact["truncated"] is False
        assert len(str(compact)) < len(str(full))


###### #23 sample_volume compare_to


class TestDeltaReport:
    def test_counts_percentiles_and_worst_on_each_side(self):
        delta = np.array([0.0, 0.5, -2.0, 3.0, 0.05, -0.2])
        summary, above, below = geometry._delta_report(delta, tolerance=0.1, worst=2)
        assert (summary["above"], summary["below"], summary["within"]) == (2, 2, 2)
        assert summary["min"] == -2.0 and summary["max"] == 3.0
        assert set(summary["percentiles"]) == {"1", "5", "50", "95", "99"}
        assert summary["percentiles"]["50"] == pytest.approx(np.percentile(delta, 50))
        assert sum(summary["histogram"]["counts"]) == 6
        assert len(summary["histogram"]["edges"]) == 9
        assert above == [3, 1]  # largest positive first
        assert below == [2, 5]  # most negative first

    def test_non_finite_is_counted_not_used(self):
        summary, _, _ = geometry._delta_report(np.array([1.0, np.nan, -1.0]))
        assert summary["count"] == 2 and summary["non_finite"] == 1

    def test_nothing_finite(self):
        summary, above, below = geometry._delta_report(np.array([np.nan]))
        assert summary == {"count": 0, "non_finite": 1}
        assert above == below == []


class TestSampleVolumeCompare:
    @pytest.fixture
    def scene(self, monkeypatch, real_errors):
        # Water points at y = 1, 2, 3; terrain A reads 1.5, 1.0, 3.0; terrain B 1, 1, 1.
        water = _Geo(
            {
                "P": (3, [0.0, 1.0, 0.0, 5.0, 2.0, 0.0, 9.0, 3.0, 0.0]),
                "s": (1, [0.0, 0.5, 1.0]),
            }
        )
        geos = {"/obj/water": water, "/obj/A": MagicMock(), "/obj/B": MagicMock()}
        reads = {
            "/obj/A": np.array([1.5, 1.0, 3.0]),
            "/obj/B": np.array([1.0, 1.0, 1.0]),
        }
        monkeypatch.setattr(geometry, "_get_sop_geo", lambda path: geos[path])
        monkeypatch.setattr(geometry, "_named_volume", lambda geo, name, path: None)
        by_geo = {id(geos[path]): reads[path] for path in reads}
        monkeypatch.setattr(
            geometry,
            "_read_fields_at",
            lambda geo, fields, points: {field: by_geo[id(geo)] for field in fields},
        )
        return water

    def test_height_minus_p_y_with_the_worst_points(self, scene):
        result = geometry._sample_volume(
            node_path="/obj/A", from_node="/obj/water", compare_to="P.y", attribs=["s"]
        )
        block = result["compare"]["height"]
        assert result["compare"]["reference"] == "P.y"
        assert (block["above"], block["below"], block["within"]) == (1, 1, 1)
        top = block["worst_above"][0]
        assert top["point"] == 0 and top["delta"] == pytest.approx(0.5)
        assert top["P"] == [0.0, 1.0, 0.0] and top["s"] == 0.0
        assert block["worst_below"][0]["point"] == 1  # floats 1 above the terrain
        assert "values" not in result  # compact unless asked

    def test_two_terrains_against_each_other(self, scene):
        result = geometry._sample_volume(
            node_paths=["/obj/A", "/obj/B"], from_node="/obj/water", compare_to="B"
        )
        assert set(result["summary"]) == {"/obj/A", "/obj/B"}
        block = result["compare"]["/obj/A"]
        assert "/obj/B" not in result["compare"]
        assert block["max"] == pytest.approx(2.0) and block["min"] == pytest.approx(0.0)
        assert result["node_paths"] == ["/obj/A", "/obj/B"]

    def test_values_only_when_asked_and_capped(self, scene):
        result = geometry._sample_volume(
            node_path="/obj/A",
            from_node="/obj/water",
            compare_to=1.0,
            include_values=True,
            limit=2,
        )
        assert result["values"]["height"] == [1.5, 1.0]
        assert result["values"]["delta"]["height"] == pytest.approx([0.5, 0.0])
        assert "first 2 of 3" in result["note"]

    def test_the_old_shape_without_compare_to(self, scene):
        result = geometry._sample_volume(
            node_path="/obj/A", fields=["height"], from_node="/obj/water"
        )
        assert result["node_path"] == "/obj/A"
        assert result["values"] == {"height": [1.5, 1.0, 3.0]}
        assert "compare" not in result

    def test_an_unknown_reference_names_the_point_attributes(self, scene):
        with pytest.raises(ValueError, match="P, s"):
            geometry._sample_volume(node_path="/obj/A", from_node="/obj/water", compare_to="nope")

    def test_a_vector_reference_needs_a_component(self, scene):
        with pytest.raises(ValueError, match="P.y"):
            geometry._sample_volume(node_path="/obj/A", from_node="/obj/water", compare_to="P")

    def test_node_path_and_node_paths_together_are_refused(self, scene):
        with pytest.raises(ValueError, match="not both"):
            geometry._sample_volume(node_path="/obj/A", node_paths=["/obj/B"], from_node="/w")


###### #29 sim_report


class TestSimFrameMetrics:
    def test_displacement_speed_and_broken(self):
        rest = (np.zeros((4, 3)), [10, 11, 12, 13])
        positions = [[0, 0, 0], [3, 4, 0], [0, 1, 0], [0, 0, 2]]
        row = geometry._sim_frame_metrics(
            positions,
            keys=[13, 11, 12, 99],  # reordered, one new point
            rest=rest,
            attribs={"v": np.array([[3.0, 4, 0], [0, 0, 0], [0, 0, 1], [0, 2, 0]])},
            flags={"broken": np.array([[1], [0], [0], [1]])},
            percentiles=(50,),
        )
        assert row["points"] == 4
        assert row["unmatched"] == 1
        assert row["disp_max"] == 5.0  # point 1 matched to rest id 11
        assert row["v_len_max"] == 5.0 and row["v_len_p50"] == 1.5
        assert (row["broken_count"], row["broken_pct"]) == (2, 50.0)

    def test_point_numbers_when_there_are_no_keys(self):
        row = geometry._sim_frame_metrics(
            [[1, 0, 0], [0, 2, 0]], rest=(np.zeros((1, 3)), None), percentiles=(50,)
        )
        assert row["disp_max"] == 1.0 and row["unmatched"] == 1

    def test_a_missing_attribute_is_none_not_an_error(self):
        row = geometry._sim_frame_metrics(
            [[0, 0, 0]], attribs={"v": None}, flags={"broken": None}, percentiles=(99,)
        )
        assert row["v_p99"] is None and row["broken_pct"] is None

    def test_string_flags(self):
        row = geometry._sim_frame_metrics([[0, 0, 0]] * 4, flags={"tag": ["a", "", "b", ""]})
        assert row["tag_pct"] == 50.0


class TestSimReport:
    @pytest.fixture
    def sim(self, monkeypatch, real_errors):
        state = {"frame": 7.0, "visited": []}
        monkeypatch.setattr(hou, "frame", lambda: state["frame"])

        def set_frame(frame):
            state["frame"] = frame
            state["visited"].append(frame)

        monkeypatch.setattr(hou, "setFrame", set_frame)
        monkeypatch.setattr(hou, "node", lambda path: object() if path == "/obj/sim" else None)

        def cooked(path):
            frame = state["frame"]
            if frame == 99:
                raise RuntimeError("cook failed")
            # Two pieces falling: y = -frame; piece 2 breaks from frame 10.
            return _Geo(
                {
                    "P": (3, [0.0, -frame, 0.0, 1.0, -frame, 0.0]),
                    "id": (1, [1, 2]),
                    "v": (3, [0.0, -frame, 0.0, 0.0, -2 * frame, 0.0]),
                    "broken": (1, [0, 1 if frame >= 10 else 0]),
                }
            )

        monkeypatch.setattr(geometry, "_get_sop_geo", cooked)
        return state

    def test_one_row_per_frame_rest_from_the_first(self, sim):
        result = geometry._sim_report(
            node_path="/obj/sim",
            frames=[20, 1, 10],
            attribs=["v"],
            flags=["broken"],
            percentiles=[50],
        )
        columns = result["columns"]
        rows = [dict(zip(columns, row, strict=True)) for row in result["rows"]]
        assert [r["frame"] for r in rows] == [1.0, 10.0, 20.0]
        assert rows[0]["disp_max"] == 0.0
        assert rows[2]["disp_max"] == pytest.approx(19.0)
        assert rows[2]["v_len_max"] == 40.0
        assert [r["broken_pct"] for r in rows] == [0.0, 50.0, 50.0]
        assert result["rest"] == "frame 1" and result["matched_by"] == "id"
        assert sim["frame"] == 7.0 and result["frame_restored"] == 7.0

    def test_a_failing_frame_is_an_error_entry(self, sim):
        result = geometry._sim_report(node_path="/obj/sim", frames=[1, 99])
        assert result["errors"] == {"99": "cook failed"}
        assert len(result["rows"]) == 2
        assert sim["frame"] == 7.0

    def test_missing_attributes_are_listed(self, sim):
        result = geometry._sim_report(node_path="/obj/sim", frames=[1], flags=["nope"])
        assert result["missing"] == ["nope"]

    def test_refusals(self, sim):
        with pytest.raises(ValueError, match="frames"):
            geometry._sim_report(node_path="/obj/sim", frames=[])
        with pytest.raises(ValueError, match="Node not found"):
            geometry._sim_report(node_path="/obj/none", frames=[1])
        result = geometry._sim_report(node_path="/obj/sim", frames=[1], match_by="bogus")
        assert "match_by" in result["errors"]["1"]


###### client tools forward the new arguments


@pytest.fixture
def bridge(monkeypatch):
    import fxhoudinimcp.tools.geometry as tools

    fake = MagicMock()
    fake.execute = AsyncMock(return_value={})
    monkeypatch.setattr(tools, "_get_bridge", lambda ctx: fake)
    return fake


class TestClientTools:
    @pytest.mark.asyncio
    async def test_sample_volume_forwards_compare(self, bridge):
        from fxhoudinimcp.tools.geometry import sample_volume

        await sample_volume(
            MagicMock(), node_paths=["/a", "/b"], from_node="/w", compare_to="P.y", worst=3
        )
        command, params = bridge.execute.call_args.args
        assert command == "geometry.sample_volume"
        assert params["node_paths"] == ["/a", "/b"]
        assert (params["compare_to"], params["worst"], params["tolerance"]) == ("P.y", 3, 0.0)
        assert "node_path" not in params

    @pytest.mark.asyncio
    async def test_sample_volume_old_call_unchanged(self, bridge):
        from fxhoudinimcp.tools.geometry import sample_volume

        await sample_volume(MagicMock(), node_path="/a", fields=["density"], positions=[[0, 0, 0]])
        _, params = bridge.execute.call_args.args
        assert params == {
            "node_path": "/a",
            "fields": ["density"],
            "positions": [[0, 0, 0]],
            "limit": 1000,
        }

    @pytest.mark.asyncio
    async def test_attrib_stats_forwards_group_by(self, bridge):
        from fxhoudinimcp.tools.geometry import get_attrib_stats

        await get_attrib_stats(
            MagicMock(), node_path="/x", group_by="s", reduce=["spread"], list_if_count_below=0
        )
        _, params = bridge.execute.call_args.args
        assert params["group_by"] == "s" and params["reduce"] == ["spread"]
        assert params["list_if_count_below"] == 0

    @pytest.mark.asyncio
    async def test_volume_info_compact_only_when_asked(self, bridge):
        from fxhoudinimcp.tools.geometry import get_volume_info

        await get_volume_info(MagicMock(), node_path="/hf", compact=True)
        assert bridge.execute.call_args.args[1]["compact"] is True

    @pytest.mark.asyncio
    async def test_sim_report_has_no_timeout(self, bridge):
        from fxhoudinimcp.bridge import NO_TIMEOUT
        from fxhoudinimcp.tools.geometry import sim_report

        await sim_report(MagicMock(), node_path="/obj/sim", frames=[1, 60], flags=["broken"])
        command, params = bridge.execute.call_args.args
        assert command == "geometry.sim_report"
        assert params["flags"] == ["broken"]
        assert bridge.execute.call_args.kwargs["timeout"] is NO_TIMEOUT
