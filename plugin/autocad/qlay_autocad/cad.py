"""Native AutoCAD drawing operations used by the optional MCP server."""

from dataclasses import dataclass
import math
from pathlib import Path
import shutil
import tempfile
import uuid

import ezdxf
from shapely.geometry import Polygon

from .bridge import Bridge, BridgeError, lisp
from .formats import gds_to_dxf, layer_name, layer_pair, read_dxf, write_gds

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".runtime"
MICROMETRES = 13


@dataclass(frozen=True)
class Drawing:
    name: str
    dbu: float
    exchange: Path | None = None
    source: Path | None = None


class AutoCAD:
    def __init__(self, bridge=None):
        self.bridge = bridge or Bridge()
        self.document_id = None
        self._drawings = {}
        self._loaded = set()

    def _id(self):
        if self.document_id is None:
            raise BridgeError({"error": "Create or open a task drawing in this MCP session first."})
        return self.document_id

    def _drawing(self):
        return self._drawings[self._id()]

    def _exchange_path(self):
        RUNTIME.mkdir(mode=0o700, exist_ok=True)
        return RUNTIME / f"layout-{uuid.uuid4().hex}.dxf"

    def _open(self, path, name, dbu, exchange=None, source=None):
        doc = self.bridge.open_document(path)
        did = doc["id"]
        if doc["units"] != MICROMETRES:
            self.bridge.close_document(did)
            if source is not None:
                path.unlink()
            raise ValueError("DWG must use micrometre drawing units (INSUNITS=13).")
        self._drawings[did] = Drawing(name, dbu, exchange, source)
        self.document_id = did
        return {"document_id": did, "name": name, "dbu_um": dbu, "units": "um"}

    def create_layout(self, name="TOP", dbu=0.001):
        if not math.isfinite(dbu) or dbu <= 0:
            raise ValueError("dbu must be a positive finite distance in micrometres")
        path = self._exchange_path()
        empty = ezdxf.new("R2018", units=MICROMETRES)
        empty.saveas(path)
        return self._open(path, name, dbu, exchange=path)

    def import_layout(self, path, top=None):
        path = Path(path).expanduser().resolve(strict=True)
        suffix = path.suffix.lower()
        if suffix == ".dwg":
            # A sibling copy keeps the original directory for relative raster and xref paths.
            with tempfile.NamedTemporaryFile(
                prefix=f"{path.stem[:60]}-qlayout-", suffix=".dwg", dir=path.parent, delete=False
            ) as target:
                working = Path(target.name)
                try:
                    with path.open("rb") as original:
                        shutil.copyfileobj(original, target)
                except OSError:
                    working.unlink(missing_ok=True)
                    raise
            opened = self._open(working, path.stem, 0.001, source=path)
            return {**opened, "working_path": str(working), "source_path": str(path)}
        if suffix not in {".gds", ".oas", ".oasis"}:
            raise ValueError("Import a micrometre DWG, GDS or OASIS file")
        exchange = self._exchange_path()
        data = gds_to_dxf(path, exchange, top=top)
        return self._open(exchange, data["name"], data["dbu_um"], exchange=exchange)

    def activate_document(self, document_id):
        if document_id not in self._drawings:
            raise ValueError("Select a drawing created or opened by this MCP session")
        result = self.bridge.activate_document(document_id)
        self.document_id = document_id
        return result

    def close_layout_view(self, document_id):
        if document_id not in self._drawings:
            raise ValueError("Only a drawing owned by this MCP session can be closed")
        result = self.bridge.close_document(document_id)
        drawing = self._drawings.pop(document_id)
        self._loaded.discard(document_id)
        if self.document_id == document_id:
            self.document_id = None
        if drawing.exchange:
            drawing.exchange.unlink(missing_ok=True)
        return result

    def _eval(self, code, *, json_result=False):
        did = self._id()
        if did not in self._loaded:
            runtime = (ROOT / "qlay_autocad" / "runtime.lsp").read_text()
            if self.bridge.eval_lisp(runtime, did) != "qlay-runtime-ready":
                raise BridgeError({"error": "Native geometry runtime failed to initialize"})
            self._loaded.add(did)
        return self.bridge.eval_lisp(code, did, json_result=json_result)

    def _points(self, points):
        dbu = self._drawing().dbu
        return [[round(float(x) / dbu) * dbu, round(float(y) / dbu) * dbu] for x, y in points]

    def _data_expression(self, value, variables):
        encoded = lisp(value)
        if len(encoded) <= 5000:
            return encoded
        if not isinstance(value, (list, tuple)):
            raise ValueError("One AutoLISP data token exceeds the supported input size")
        variable = "qlay:stage_" + uuid.uuid4().hex
        variables.append(variable)
        self._eval(f"(setq {variable} nil)")
        chunk = []
        length = 0
        for item in value:
            expression = self._data_expression(item, variables)
            if length + len(expression) > 5000 and chunk:
                self._eval(f"(setq {variable} (append {variable} (list {' '.join(chunk)})))")
                chunk = []
                length = 0
            chunk.append(expression)
            length += len(expression) + 1
        if chunk:
            self._eval(f"(setq {variable} (append {variable} (list {' '.join(chunk)})))")
        return variable

    def _clear_data(self, variables):
        if variables:
            self._eval("(setq " + " ".join(name + " nil" for name in variables) + ")")

    def add_polygon(self, layer, points, holes=None):
        polygon = Polygon(self._points(points), [self._points(hole) for hole in holes or []])
        if polygon.is_empty or not polygon.is_valid or polygon.area <= 0:
            raise ValueError("Polygon must have positive area and valid rings")
        variables = []
        try:
            hull = self._data_expression(list(polygon.exterior.coords)[:-1], variables)
            inner = self._data_expression([list(ring.coords)[:-1] for ring in polygon.interiors], variables)
            return self._eval(f"(qlay:polygon {lisp(layer_name(layer))} {hull} {inner})")
        finally:
            self._clear_data(variables)

    def add_rect(self, layer, x1, y1, x2, y2):
        return self.add_polygon(layer, [(x1, y1), (x2, y1), (x2, y2), (x1, y2)])

    def add_path(self, layer, points, width, closed=False):
        points = self._points(points)
        if not math.isfinite(width) or width <= 0 or len(set(map(tuple, points))) < 2:
            raise ValueError("Path needs positive finite width and at least two distinct points")
        variables = []
        try:
            data = self._data_expression(points, variables)
            return self._eval(f"(qlay:path {lisp(layer_name(layer))} {data} {lisp(width)} {lisp(closed)})")
        finally:
            self._clear_data(variables)

    def set_layer(self, layer, color=7, visible=True):
        if not 1 <= color <= 255:
            raise ValueError("AutoCAD color index must be in [1, 255]")
        name = lisp(layer_name(layer))
        return self._eval(
            f"(progn (qlay:layer {name} {lisp(color)}) (qlay:color {name} {lisp(color)}) "
            f"(qlay:visible {name} {lisp(visible)}))"
        )

    def get_layout_info(self):
        info = self._eval("(qlay:info)", json_result=True)
        return {**info, "document_id": self._id(), "dbu_um": self._drawing().dbu}

    def add_image(self, path, pixel_size, origin=(0, 0), rotation=0, mirror=False, layer="900/0"):
        path = Path(path).expanduser().resolve(strict=True)
        if not path.is_file() or not math.isfinite(pixel_size) or pixel_size <= 0:
            raise ValueError("Use an image file and a positive finite micrometre pixel size")
        if len(origin) != 2:
            raise ValueError("Image origin must be an (x, y) pair in world micrometres")
        angle = math.radians(rotation)
        u = [pixel_size * math.cos(angle), pixel_size * math.sin(angle)]
        v = [-u[1], u[0]]
        if mirror:
            u = [-value for value in u]
        return self._eval(
            f"(qlay:attach-image {lisp(path)} {lisp(layer_name(layer))} {lisp(origin)} {lisp(u)} {lisp(v)})"
        )

    def list_images(self):
        return self._eval("(qlay:images)", json_result=True)

    def save_layout(self, path, layers=None, name=None):
        did = self._id()
        drawing = self._drawing()
        path = Path(path).expanduser().resolve()
        suffix = path.suffix.lower()
        if suffix in {".dwg", ".dxf"}:
            if layers is not None or name is not None:
                raise ValueError("Layer selection and top cell name apply to GDS/OASIS exports")
            source = drawing.source
            if source is not None and (path == source or (
                path.exists() and source.exists() and path.samefile(source)
            )):
                raise ValueError("Save the task copy or choose a new file; the source DWG is protected")
            return self.bridge.save_document(path, did)
        if suffix not in {".gds", ".oas", ".oasis"}:
            raise ValueError("Save as DWG, DXF, GDS or OASIS")
        keys = ["/".join(map(str, layer_pair(layer_name(layer)))) for layer in layers] if layers is not None else None
        with tempfile.TemporaryDirectory(prefix="klayoutclaw-autocad-") as directory:
            exchange = Path(directory) / "readback.dxf"
            self.bridge.save_document(exchange, did)
            snapshot = read_dxf(exchange, tolerance_um=drawing.dbu)
        selected = snapshot["layers"]
        if keys is not None:
            missing = set(keys) - selected.keys()
            if missing:
                raise ValueError(f"Layers without exportable geometry: {', '.join(sorted(missing))}")
            selected = {key: selected[key] for key in keys}
        if not selected:
            raise ValueError("No mapped area geometry to export")
        exported = write_gds(selected, path, name or drawing.name, drawing.dbu)
        return {**exported, "ignored_entity_types": snapshot["ignored_entity_types"]}
