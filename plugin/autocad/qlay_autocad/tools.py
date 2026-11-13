"""MCP tool registry for native drawing, display and interchange."""

import inspect

from .cad import AutoCAD

CAD = AutoCAD()
TOOLS = {}

S = {"type": "string"}
N = {"type": "number"}
A = {"type": "array"}
B = {"type": "boolean"}
DOCUMENT = {"document_id": S}


def tool(description, properties=None, required=(), readonly=False):
    def register(function):
        TOOLS[function.__name__] = {
            "name": function.__name__,
            "description": description,
            "inputSchema": {
                "type": "object",
                "properties": properties or {},
                "required": list(required),
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": readonly},
            "function": function,
        }
        return function
    return register


@tool("Check the native AutoCAD bridge and its version.", readonly=True)
def ping():
    return CAD.bridge.ping()


@tool("List open AutoCAD drawings and their session IDs.", readonly=True)
def documents():
    return {"documents": CAD.bridge.documents()}


@tool("Open a new task drawing in micrometres.", {"name": S, "dbu": N})
def create_layout(name="TOP", dbu=0.001):
    return CAD.create_layout(name, dbu)


@tool("Open a task copy of a micrometre DWG, or import GDS/OASIS into a new drawing.",
      {"path": S, "top": S}, ("path",))
def import_layout(path, top=None):
    return CAD.import_layout(path, top)


@tool("Activate a task drawing owned by this MCP session.", DOCUMENT, ("document_id",))
def activate_document(document_id):
    return CAD.activate_document(document_id)


@tool("Close an active, saved task drawing owned by this MCP session.", DOCUMENT, ("document_id",))
def close_layout_view(document_id):
    return CAD.close_layout_view(document_id)


@tool("Inspect the bound drawing's ID, units, layers and entity count.", readonly=True)
def get_layout_info():
    return CAD.get_layout_info()


@tool("Create a filled native HATCH polygon, with optional interior holes, in micrometres.",
      {"layer": S, "points": A, "holes": A}, ("layer", "points"))
def add_polygon(layer, points, holes=None):
    return {"handle": CAD.add_polygon(layer, points, holes)}


@tool("Create a filled native rectangle in micrometres.",
      {"layer": S, "x1": N, "y1": N, "x2": N, "y2": N}, ("layer", "x1", "y1", "x2", "y2"))
def add_rect(layer, x1, y1, x2, y2):
    return {"handle": CAD.add_rect(layer, x1, y1, x2, y2)}


@tool("Create a constant-width native polyline in micrometres.",
      {"layer": S, "points": A, "width": N, "closed": B}, ("layer", "points", "width"))
def add_path(layer, points, width, closed=False):
    return {"handle": CAD.add_path(layer, points, width, closed)}


@tool("Set a native layer's color and visibility. Specify the layer as layer/datatype.",
      {"layer": S, "color": {"type": "integer"}, "visible": B}, ("layer",))
def set_layer(layer, color=7, visible=True):
    return {"layer": CAD.set_layer(layer, color, visible)}


@tool("Attach a local raster at a lower-left world origin and calibrated micrometres per pixel.",
      {"path": S, "pixel_size": N, "origin": A, "rotation": N, "mirror": B, "layer": S},
      ("path", "pixel_size"))
def add_image(path, pixel_size, origin=(0, 0), rotation=0, mirror=False, layer="900/0"):
    return {"handle": CAD.add_image(path, pixel_size, origin, rotation, mirror, layer)}


@tool("List the bound drawing's raster paths and actual world vectors.", readonly=True)
def list_images():
    return {"images": CAD.list_images()}


@tool("Save DWG/DXF, or export selected layer/datatype pairs as GDS/OASIS from native readback.",
      {"path": S, "layers": A, "name": S}, ("path",))
def save_layout(path, layers=None, name=None):
    return CAD.save_layout(path, layers, name)


def definitions():
    return [{key: value for key, value in item.items() if key != "function"} for item in TOOLS.values()]


def invoke(name, arguments=None):
    if name not in TOOLS:
        raise ValueError(f"Unknown tool: {name}")
    if arguments is None:
        arguments = {}
    for required in TOOLS[name]["inputSchema"]["required"]:
        if required not in arguments:
            raise ValueError(f"Missing required argument: {required}")
    function = TOOLS[name]["function"]
    inspect.signature(function).bind(**arguments)
    return function(**arguments)
