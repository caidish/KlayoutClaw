"""Exercise the legacy and SOP-driven material commits with synthetic traces."""

import importlib
import json
import sys
from types import SimpleNamespace

import cv2
import klayout.db as pya
import numpy as np
import pytest

from conftest import GDSALIGN_DIR


@pytest.fixture
def material_commit(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(GDSALIGN_DIR)
    commit_gds = importlib.import_module("commit_gds")

    warp = tmp_path / "gds_warp.npy"
    np.save(warp, np.array([[1, 0, 0], [0, -1, 24]]))
    (tmp_path / "gds_alignment_report.json").write_text(json.dumps({
        "status": "complete",
        "quality": {"inliers": 4, "mean_residual_um": 0},
        "transform": {"scale": 1, "rotation_deg": 0,
                      "translation_um": [0, 24], "reflected": True},
    }))
    image = tmp_path / "stack.png"
    assert cv2.imwrite(str(image), np.zeros((24, 24, 3), dtype=np.uint8))
    layout = pya.Layout()
    cell = layout.create_cell("TEMPLATE")
    cell.shapes(layout.layer(5, 0)).insert(pya.Box(0, 0, 1000, 1000))
    template = tmp_path / "template.gds"
    layout.write(str(template))
    connected = []

    def execute_script(code):
        # Run the actual polygon insertion and verification code with pya.
        # Template, raster and viewport operations require the GUI.
        if "cell.shapes(" not in code:
            return {"status": "ok", "dbu": layout.dbu}
        namespace = {"pya": pya, "_get_or_create_view": lambda: (None, layout, cell)}
        exec(code, namespace)
        return namespace["result"]

    monkeypatch.setitem(sys.modules, "mcp_client", SimpleNamespace(
        init_session=lambda: connected.append(True),
        execute_script=execute_script,
        load_mcp_config=lambda path: None,
        tool_call=lambda *args, **kwargs: None,
    ))

    def run(materials, map_text=None):
        traces = tmp_path / "traces.json"
        traces.write_text(json.dumps({"materials": materials}))
        argv = ["commit_gds.py", "--warp", str(warp), "--traces", str(traces),
                "--image", str(image), "--pixel-size", "1", "--gds", str(template),
                "--output-dir", str(tmp_path), "--verify"]
        if map_text is not None:
            map_file = tmp_path / "material_layer_map.json"
            map_file.write_text(map_text)
            argv += ["--material-layer-map", str(map_file)]
        monkeypatch.setattr(sys, "argv", argv)
        with pytest.raises(SystemExit) as exc:
            commit_gds.main()
        return exc.value.code

    return run, layout, cell, connected


def contours(roles):
    return {role: [{"contour_um": [[x, 1], [x + 1, 1], [x + 1, 2], [x, 2]]}]
            for x, role in enumerate(roles, 1)}


@pytest.mark.parametrize("device_sop", [False, True], ids=["legacy", "device-sop"])
def test_commit_material_layers(device_sop, material_commit, tmp_path, capsys):
    run, layout, cell, connected = material_commit
    expected = {"top_hBN": "10/0", "graphene": "11/0", "bottom_hBN": "12/0",
                "graphite": "13/0"}
    if device_sop:
        expected = {"top_top_hBN": "110/0", "top_graphite": "111/0",
                    "top_hBN": "112/0", "graphene": "113/0",
                    "bottom_hBN": "114/0", "bottom_graphite": "111/7"}
    materials = contours(expected)
    if not device_sop:
        materials["unmapped"] = materials["graphite"]

    assert run(materials, json.dumps(expected) if device_sop else None) == 0
    assert connected == [True]
    actual = {str(info) for info in layout.layer_infos() if info.layer != 5}
    assert actual == set(expected.values())
    for x, spec in enumerate(expected.values(), 1):
        layer = layout.layer(*map(int, spec.split("/")))
        assert cell.shapes(layer).size() == 1
        assert cell.bbox_per_layer(layer).left == round(x / layout.dbu)
    output = capsys.readouterr().out
    assert str({role: 1 for role in expected}) in output
    warped = json.loads((tmp_path / "traces_gds.json").read_text())
    if device_sop:
        assert warped["layer_map"] == expected
    else:
        assert "No layer mapping for 'unmapped', skipping" in output
        assert "layer_map" not in warped


@pytest.mark.parametrize("map_text, roles, message", [
    ("{}", ["graphene"], "nonempty JSON object"),
    ('{"graphene": "20"}', ["graphene"], "'L/D'"),
    ('{"graphene": "20/0", "graphene": "21/0"}', ["graphene"], "duplicate material role"),
    ('{"top": "20/0", "bottom": "20/0"}', ["top", "bottom"], "multiple material roles"),
    ('{"top": "20/0"}', ["top", "bottom"], "unmapped trace roles: bottom"),
    ('{"top": "20/0", "bottom": "21/0"}', ["top"], "'bottom' has no contours"),
])
def test_invalid_map_stops_before_mcp(map_text, roles, message, material_commit, capsys):
    run, _, _, connected = material_commit
    assert run(contours(roles), map_text) == 2
    assert message in capsys.readouterr().err
    assert connected == []


def test_mapped_contour_collapses_on_template_grid(material_commit, capsys):
    run, layout, _, connected = material_commit
    layout.dbu = 1.0
    materials = {"graphene": [{"contour_um": [[1.1, 1.1], [1.4, 1.1], [1.4, 1.4]]}]}
    assert run(materials, '{"graphene": "111/0"}') == 2
    assert "zero-area contour on the KLayout grid" in capsys.readouterr().err
    assert connected == [True]
    assert {str(info) for info in layout.layer_infos()} == {"5/0"}
