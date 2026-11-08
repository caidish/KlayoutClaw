"""Integer-grid mask arithmetic, independent of any CAD application."""
import math
import pyclipper
from shapely.geometry import Polygon
from shapely.ops import unary_union


def polygon_parts(geometry):
    if geometry.is_empty:
        return []
    if geometry.geom_type == 'Polygon':
        return [geometry]
    return [part for child in getattr(geometry,'geoms',[]) for part in polygon_parts(child)]


def records(geometry):
    return [{'hull_um': [list(p) for p in g.exterior.coords[:-1]],
             'holes_um': [[list(p) for p in h.coords[:-1]] for h in g.interiors]} for g in polygon_parts(geometry)]


def from_records(items):
    return unary_union([Polygon(p['hull_um'], p.get('holes_um', [])) for p in items])


class IntegerRegion:
    """Clipper NONZERO regions with explicit hole orientation, on a declared grid."""
    def __init__(self, geometry=None, dbu=0.001, *, paths=None):
        if dbu <= 0 or not math.isfinite(dbu):
            raise ValueError('dbu must be a finite positive number in micrometres')
        self.dbu = dbu
        self.paths = list(paths or [])
        if geometry is not None:
            if isinstance(geometry, list):
                geometry = from_records(geometry)
            for polygon in polygon_parts(geometry):
                for ring, positive in [(polygon.exterior, True)] + [(r, False) for r in polygon.interiors]:
                    points = [(round(x / dbu), round(y / dbu)) for x, y in ring.coords[:-1]]
                    points = [p for i, p in enumerate(points) if i == 0 or p != points[i - 1]]
                    if len(set(points)) < 3 or pyclipper.Area(points) == 0:
                        continue
                    if max(abs(v) for p in points for v in p) >= 2**61:
                        raise ValueError('Geometry exceeds the safe integer coordinate range')
                    self.paths.append(points if pyclipper.Orientation(points) == positive else points[::-1])

    def _tree(self, operation=pyclipper.CT_UNION, other=None):
        pc = pyclipper.Pyclipper()
        if self.paths:
            pc.AddPaths(self.paths, pyclipper.PT_SUBJECT, True)
        if other is not None:
            if self.dbu != other.dbu:
                raise ValueError('Cannot combine regions on different integer grids')
            if other.paths:
                pc.AddPaths(other.paths, pyclipper.PT_CLIP, True)
        if not self.paths and (other is None or not other.paths):
            return None
        return pc.Execute2(operation, pyclipper.PFT_NONZERO, pyclipper.PFT_NONZERO)

    def _op(self, other, operation):
        tree = self._tree(operation, other)
        return IntegerRegion(dbu=self.dbu, paths=pyclipper.PolyTreeToPaths(tree) if tree else [])

    def __add__(self, other): return self._op(other, pyclipper.CT_UNION)
    def __sub__(self, other): return self._op(other, pyclipper.CT_DIFFERENCE)
    def __and__(self, other): return self._op(other, pyclipper.CT_INTERSECTION)
    def __xor__(self, other): return self._op(other, pyclipper.CT_XOR)
    def merged(self):
        tree = self._tree()
        return IntegerRegion(dbu=self.dbu, paths=pyclipper.PolyTreeToPaths(tree) if tree else [])
    def to_records(self):
        tree = self._tree()
        result = []
        def visit(node):
            if node.Contour and not node.IsHole:
                result.append({'hull_um': [[x * self.dbu, y * self.dbu] for x, y in node.Contour],
                    'holes_um': [[[x * self.dbu, y * self.dbu] for x, y in hole.Contour] for hole in node.Childs if hole.IsHole]})
            for child in node.Childs:
                visit(child)
        if tree:
            visit(tree)
        return result
    def area(self): return abs(sum(pyclipper.Area(p) for p in self.merged().paths))
    def area_um2(self): return self.area() * self.dbu**2
    def is_empty(self): return not self.merged().paths
    def count(self): return len(self.to_records())
