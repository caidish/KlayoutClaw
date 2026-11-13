"""GDS/OASIS and native AutoCAD DXF interchange, with explicit micrometre units."""
import math
import json
import re
from pathlib import Path
from tempfile import TemporaryDirectory
from collections import defaultdict, Counter
import ezdxf
import gdstk
import numpy as np
from shapely.geometry import Polygon, LineString
from shapely.ops import unary_union
from .geometry import IntegerRegion, records

LAYER_RE = re.compile(r'^L(\d+)_D(\d+)(?:__.*)?$')


def layer_pair(spec):
    if isinstance(spec, (list, tuple)):
        parts = spec
    else:
        match = LAYER_RE.fullmatch(str(spec))
        parts = match.groups() if match else str(spec).split('/')
        if len(parts) == 1 and parts[0].isdigit():
            parts = (parts[0], '0')
    if len(parts) != 2 or any(not re.fullmatch(r'[0-9]+', str(part)) for part in parts):
        raise ValueError(f'Layer/datatype mapping is required for {spec!r}')
    layer, datatype = map(int, parts)
    if not (0 <= layer <= 65535 and 0 <= datatype <= 65535):
        raise ValueError('GDS layer and datatype must be in [0, 65535]')
    return layer, datatype


def layer_name(spec):
    a, b = layer_pair(spec)
    return f'L{a:03d}_D{b:03d}'


def read_gds(path, top=None):
    path = Path(path)
    lib = gdstk.read_oas(path) if path.suffix.lower() in {'.oas', '.oasis'} else gdstk.read_gds(path)
    tops = lib.top_level()
    if top:
        tops = [c for c in lib.cells if c.name == top]
    if len(tops) != 1:
        raise ValueError('Select one top cell explicitly: ' + ', '.join(c.name for c in lib.top_level()))
    cell = tops[0]
    factor = lib.unit / 1e-6
    grouped = defaultdict(list)
    degenerate=[]
    for polygon in cell.get_polygons():
        points=polygon.points*factor
        if len(points)<3 or polygon.area()==0:
            degenerate.append({'layer':f'{polygon.layer}/{polygon.datatype}','points_um':points.tolist()})
            continue
        geometry=Polygon(points)
        if not geometry.is_valid:
            from shapely import make_valid
            geometry=make_valid(geometry)
        grouped[f'{polygon.layer}/{polygon.datatype}'].append(geometry)
    return {'name': cell.name, 'units': 'um', 'dbu_um': lib.precision / 1e-6,
            'layers': {key: IntegerRegion(unary_union(polys), lib.precision / 1e-6).to_records() for key, polys in grouped.items()},
            'library': lib, 'top': cell, 'scale_to_um': factor,'zero_area_boundaries':degenerate}


def add_hatch(space, hull, holes, layer):
    hatch = space.add_hatch(color=256, dxfattribs={'layer': layer})
    hatch.paths.add_polyline_path(hull, is_closed=True, flags=3)
    for hole in holes:
        hatch.paths.add_polyline_path(hole, is_closed=True, flags=2)
    return hatch


def gds_to_dxf(source, target, top=None, preserve_hierarchy=True):
    data = read_gds(source, top)
    doc = ezdxf.new('R2018', units=13)
    doc.header['$MEASUREMENT'] = 1
    doc.header['$LUNITS'] = 2
    factor = data['scale_to_um']
    def polygons_to_space(cell, space):
        for p in cell.get_polygons(depth=0):
            name = layer_name([p.layer, p.datatype])
            if name not in doc.layers:
                doc.layers.new(name)
            if len(p.points)<3 or p.area()==0:
                space.add_lwpolyline((p.points*factor).tolist(),dxfattribs={'layer':name})
            else:
                geometry=Polygon(p.points*factor)
                if not geometry.is_valid:
                    from shapely import make_valid
                    geometry=make_valid(geometry)
                for polygon in IntegerRegion(geometry,data['dbu_um']).to_records():
                    add_hatch(space,polygon['hull_um'],polygon['holes_um'],name)
        for label in cell.labels:
            name = layer_name([label.layer, label.texttype])
            if name not in doc.layers:
                doc.layers.new(name)
            space.add_text(label.text, dxfattribs={'insert': np.asarray(label.origin) * factor,
                'height': 1.0, 'rotation': math.degrees(label.rotation), 'layer': name})
    if preserve_hierarchy:
        for cell in data['library'].cells:
            doc.blocks.new(cell.name)
        for cell in data['library'].cells:
            space = doc.blocks[cell.name]
            polygons_to_space(cell, space)
            for ref in cell.references:
                for delta in ref.repetition.get_offsets():
                    space.add_blockref(ref.cell.name, (np.asarray(ref.origin) + delta) * factor, dxfattribs={
                        'xscale': ref.magnification, 'yscale': -ref.magnification if ref.x_reflection else ref.magnification,
                        'rotation': math.degrees(ref.rotation)})
                if ref.repetition.size == 0:
                    space.add_blockref(ref.cell.name, np.asarray(ref.origin) * factor, dxfattribs={
                        'xscale': ref.magnification, 'yscale': -ref.magnification if ref.x_reflection else ref.magnification,
                        'rotation': math.degrees(ref.rotation)})
        doc.modelspace().add_blockref(data['name'], (0, 0))
    else:
        for key, polys in data['layers'].items():
            name = layer_name(key)
            if name not in doc.layers:
                doc.layers.new(name)
            for p in polys:
                add_hatch(doc.modelspace(), p['hull_um'], p['holes_um'], name)
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(target)
    return {'path': str(target), 'name': data['name'], 'dbu_um': data['dbu_um'], 'preserve_hierarchy': preserve_hierarchy,
            'layers': list(data['layers'])}


def _hatch_polygons(entity, tolerance):
    from ezdxf.path import from_hatch_boundary_path
    rings = []
    for boundary in entity.paths:
        if hasattr(boundary, 'vertices') and not boundary.has_bulge():
            points = [(p[0], p[1]) for p in boundary.vertices]
        else:
            path = from_hatch_boundary_path(boundary)
            points = [(p.x, p.y) for p in path.flattening(tolerance)]
        # Mirrored BLOCK/INSERT transforms can flip the HATCH extrusion axis.
        # Boundary vertices are OCS coordinates, not necessarily world XY.
        ocs = entity.ocs()
        elevation = entity.dxf.elevation.z
        points = [(p.x,p.y) for p in (ocs.to_wcs((x,y,elevation)) for x,y in points)]
        if len(points) >= 3:
            ring = Polygon(points)
            if not ring.is_valid:
                # Native GDS keyhole paths intentionally join a hole to its hull.
                from shapely import make_valid
                ring = make_valid(ring)
            rings.append(ring)
    if entity.dxf.hatch_style == 2:
        return unary_union(rings)
    result = Polygon()
    for ring in rings:
        result = result.symmetric_difference(ring)
    return result


def read_dxf(path, *, unit_um=None, layer_map=None, closed_polylines_as_regions=True, tolerance_um=0.001):
    doc = ezdxf.readfile(path)
    units = {1: 25400, 2: 304800, 3: 1609344000, 4: 1000, 5: 10000, 6: 1e6, 7: 1e9,
             8: 0.0254, 9: 25.4, 10: 914400, 11: 1e-4, 12: 1e-3, 13: 1, 14: 100000, 15: 1e7}
    factor = unit_um if unit_um is not None else units.get(doc.units)
    if factor is None:
        raise ValueError('Unitless DXF: provide unit_um explicitly from physical evidence')
    # Resolve arrays in block-local coordinates before a parent INSERT transforms
    # their locations. Transforming the MINSERT itself leaves its spacing unscaled.
    for block in doc.blocks:
        for entity in block.query('INSERT'):
            if entity.mcount > 1:
                for instance in entity.multi_insert():
                    block.add_entity(instance)
                block.delete_entity(entity)

    grouped, features, ignored = defaultdict(list), [], Counter()

    def record_skipped(entity, _reason):
        ignored[entity.dxftype()] += 1

    def walk(entities, inherited=None, parent=''):
        for index,entity in enumerate(entities):
            current = entity.dxf.layer
            layer = inherited if current == '0' and inherited else current
            identity = parent + (entity.dxf.get('handle') or ('virtual-' + str(index)))
            if entity.dxftype() == 'INSERT':
                yield from walk(
                    entity.virtual_entities(skipped_entity_callback=record_skipped), layer, identity + '/',
                )
            else:
                yield entity, layer, identity
    for entity, native_layer, identity in walk(doc.modelspace()):
        kind, geometry = entity.dxftype(), None
        metadata = {}
        if entity.has_xdata('QLAY'):
            metadata = json.loads(''.join(tag.value for tag in entity.get_xdata('QLAY') if tag.code == 1000))
        if metadata.get('kind')=='display_outline':
            ignored['DISPLAY_OUTLINE']+=1
            continue
        try:
            key = (layer_map or {}).get(native_layer) or '/'.join(map(str, layer_pair(native_layer)))
            key = '/'.join(map(str, layer_pair(key)))
        except ValueError:
            key = native_layer
        if kind == 'HATCH':
            geometry = _hatch_polygons(entity, tolerance_um / factor)
        elif kind in {'SOLID', 'TRACE', '3DFACE'}:
            pts = entity.wcs_vertices(close=False)
            geometry = Polygon([(v.x, v.y) for v in pts])
        elif kind == 'LWPOLYLINE':
            if entity.has_arc:
                from ezdxf.path import make_path
                points = [(p.x, p.y) for p in make_path(entity).flattening(tolerance_um / factor)]
            else:
                points = [(p.x,p.y) for p in entity.vertices_in_wcs()]
            width = entity.dxf.const_width
            if not width and entity.has_width:
                raise ValueError('Variable-width polylines need explicit polygon conversion')
            if width:
                if entity.closed: points += [points[0]]
                geometry = LineString(points).buffer(width / 2, cap_style='flat', join_style='mitre')
            elif entity.closed and closed_polylines_as_regions:
                geometry = Polygon(points)
        elif kind == 'POLYLINE' and entity.is_2d_polyline:
            points = [(v.dxf.location.x, v.dxf.location.y) for v in entity.vertices]
            if entity.is_closed and closed_polylines_as_regions:
                geometry = Polygon(points)
        elif kind == 'CIRCLE':
            normal = entity.ocs().uz
            if not math.isclose(abs(normal.z), 1.0, rel_tol=0, abs_tol=1e-12):
                raise ValueError('Mask export requires circles in planes parallel to world XY')
            radius = abs(entity.dxf.radius)
            if radius > 0:
                sagitta = min(tolerance_um / factor, radius / 2)
                geometry = Polygon([(v.x, v.y) for v in entity.flattening(sagitta)])
        if geometry is None:
            ignored[kind] += 1
            continue
        if not geometry.is_valid:
            from shapely import make_valid
            geometry = make_valid(geometry)
        from shapely.affinity import scale
        geometry = scale(geometry, xfact=factor, yfact=factor, origin=(0, 0))
        if not geometry.is_empty:
            grouped[key].append(geometry)
            features.append({'handle': entity.dxf.get('handle'), 'type': kind, 'layer': key,
                'entity_id': identity, 'native_layer': native_layer, 'geometry': geometry, 'metadata': metadata})
    return {'name': Path(path).stem, 'units': 'um', 'unit_um': factor,
        'layers': {key: records(unary_union(polys)) for key, polys in grouped.items()},
        'features': features, 'ignored_entity_types': dict(ignored), 'document': doc}


def write_gds(layers, path, name='TOP', dbu=0.001):
    lib = gdstk.Library(unit=1e-6, precision=dbu * 1e-6)
    cell = lib.new_cell(name)
    for key, polys in layers.items():
        layer, datatype = layer_pair(key)
        for polygon in IntegerRegion(polys, dbu).to_records():
            hull = gdstk.Polygon(polygon['hull_um'])
            pieces = gdstk.boolean([hull], [gdstk.Polygon(h) for h in polygon['holes_um']], 'not', precision=dbu) if polygon['holes_um'] else [hull]
            for piece in pieces:
                piece.layer, piece.datatype = layer, datatype
                cell.add(piece)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix='.autocad-export-', dir=path.parent) as directory:
        staged = Path(directory) / path.name
        if path.suffix.lower() in {'.oas', '.oasis'}:
            lib.write_oas(staged)
        else:
            # The default 199-point fracture can round newly introduced cuts on
            # complex hole-bearing polygons. Keep up to 8190 vertices per record.
            lib.write_gds(staged, max_points=8190)
        staged.replace(path)
    return {'path': str(path), 'name': name, 'layers': list(layers), 'dbu_um': dbu}
