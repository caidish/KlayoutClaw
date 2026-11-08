import math
import ezdxf
import gdstk
from shapely.geometry import box, Polygon
from qlay_autocad.geometry import IntegerRegion
from qlay_autocad.formats import read_gds, read_dxf, gds_to_dxf, write_gds


def test_shared_edge_difference_and_holes():
    outer=IntegerRegion(box(0,0,10,10));hole=IntegerRegion(box(3,3,7,7))
    ring=outer-hole
    assert ring.area_um2()==84
    assert ring.count()==1 and ring.to_records()[0]['holes_um']
    assert (ring&hole).is_empty()
    assert ((ring+hole)^outer).is_empty()
    assert (outer-IntegerRegion(box(5,0,10,10))).area_um2()==50


def test_hierarchy_mirror_rotation_repetition_roundtrip(tmp_path):
    lib=gdstk.Library(unit=1e-6,precision=1e-9)
    leaf=lib.new_cell('LEAF');leaf.add(gdstk.rectangle((0,0),(3,1),layer=37,datatype=4))
    middle=lib.new_cell('MIDDLE');middle.add(gdstk.Reference(leaf,(2,4),rotation=math.pi/2,x_reflection=True))
    top=lib.new_cell('TOP');top.add(gdstk.Reference(middle,(20,30),columns=2,rows=3,spacing=(10,12)))
    lib.write_gds(tmp_path/'source.gds')
    original=read_gds(tmp_path/'source.gds')
    gds_to_dxf(tmp_path/'source.gds',tmp_path/'native.dxf')
    native=read_dxf(tmp_path/'native.dxf')
    assert set(native['layers'])=={'37/4'}
    assert (IntegerRegion(original['layers']['37/4'])^IntegerRegion(native['layers']['37/4'])).is_empty()


def test_gds_hole_survives_native_hatch_exchange(tmp_path):
    region=IntegerRegion(box(0,0,12,10))-IntegerRegion(box(3,3,9,7))
    write_gds({'20/9':region.to_records()},tmp_path/'hole.gds')
    gds_to_dxf(tmp_path/'hole.gds',tmp_path/'hole.dxf')
    native=read_dxf(tmp_path/'hole.dxf')
    assert (region^IntegerRegion(native['layers']['20/9'])).is_empty()


def test_unknown_dxf_units_are_rejected(tmp_path):
    import ezdxf,pytest
    doc=ezdxf.new();doc.units=0;doc.modelspace().add_lwpolyline([(0,0),(1,0),(1,1)],close=True);doc.saveas(tmp_path/'unitless.dxf')
    with pytest.raises(ValueError,match='Unitless'):
        read_dxf(tmp_path/'unitless.dxf')


def test_complex_hole_export_preserves_integer_edges(tmp_path):
    import numpy as np
    theta=np.linspace(0,2*np.pi,180,endpoint=False)
    hull=np.round(np.stack([20*np.cos(theta),17*np.sin(theta)],axis=1),3)
    hole=np.round(np.stack([3+8*np.cos(theta),1+7*np.sin(theta)],axis=1),3)
    expected=IntegerRegion(Polygon(hull,[hole]))
    write_gds({'37/4':expected.to_records()},tmp_path/'complex.gds')
    actual=read_gds(tmp_path/'complex.gds')
    assert (expected^IntegerRegion(actual['layers']['37/4'])).is_empty()


def test_nested_insert_array_uses_parent_transform_on_all_instances(tmp_path):
    from shapely.ops import unary_union

    doc = ezdxf.new("R2018", units=13)
    leaf = doc.blocks.new("LEAF")
    leaf.add_lwpolyline([(0, 0), (2, 0), (2, 1), (0, 1)], close=True,
                        dxfattribs={"layer": "L020_D009"})
    parent = doc.blocks.new("PARENT")
    parent.add_blockref("LEAF", (0, 0), dxfattribs={
        "row_count": 2, "column_count": 3, "row_spacing": 8, "column_spacing": 10,
    })
    doc.modelspace().add_blockref("PARENT", (50, 20), dxfattribs={
        "rotation": 90, "xscale": 2, "yscale": 2,
    })
    path = tmp_path / "array.dxf"
    doc.saveas(path)
    expected = IntegerRegion(unary_union([
        box(48 - 16 * row, 20 + 20 * col, 50 - 16 * row, 24 + 20 * col)
        for row in range(2) for col in range(3)
    ]))
    result = read_dxf(path)
    assert not result["ignored_entity_types"]
    assert (expected ^ IntegerRegion(result["layers"]["20/9"])).is_empty()


def test_circle_readback_uses_world_coordinates_and_requested_tolerance(tmp_path):
    from qlay_autocad.geometry import from_records

    doc = ezdxf.new("R2018", units=13)
    doc.modelspace().add_circle((10, 20), 1000, dxfattribs={
        "layer": "L020_D000", "extrusion": (0, 0, -1),
    })
    path = tmp_path / "circle.dxf"
    doc.saveas(path)
    circle = from_records(read_dxf(path, tolerance_um=0.001)["layers"]["20/0"])
    assert math.isclose(circle.centroid.x, -10, abs_tol=1e-9)
    assert math.isclose(circle.centroid.y, 20, abs_tol=1e-9)
    vertices = list(circle.exterior.coords)
    sagitta = max(
        1000 - math.hypot((a[0] + b[0]) / 2 + 10, (a[1] + b[1]) / 2 - 20)
        for a, b in zip(vertices, vertices[1:])
    )
    assert 0 < sagitta <= 0.001
