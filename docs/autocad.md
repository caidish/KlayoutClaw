# AutoCAD for Mac

KlayoutClaw's optional AutoCAD adapter gives a local stdio MCP client access
to an open AutoCAD drawing. The Python process connects over a private Unix
socket to a locally built ObjectARX bundle. The bundle runs AutoCAD operations
in the application's own context.

The initial target is **full AutoCAD 2027 for Mac on Apple Silicon**. Use the
[KLayout Quick Start](../README.md#quick-start) for the KLayout HTTP server and
Qlaybot. The AutoCAD tools have their own
[Skill](../skills/autocad/SKILL.md) and drawing API.

## Build and connect

1. Install Python 3.12 and Apple Command Line Tools. Get the matching Mac
   [ObjectARX SDK](https://forge.autodesk.com/developer/overview/objectarx-autocad-sdk)
   from Autodesk. You can use its downloaded `.dmg` directly.
2. From the KlayoutClaw checkout, set up an adapter-local virtual environment
   and build the bundle.

   ```sh
   cd plugin/autocad
   python3.12 -m venv .venv
   .venv/bin/python -m pip install -r requirements.txt
   .venv/bin/python build_bridge.py --sdk "/absolute/path/to/objectarx-2027-english-macos.dmg"
   ```

   `--sdk` also accepts the SDK's extracted `Payload` directory containing
   `inc/acdocman.h`. Pass `--app "/path/to/AutoCAD 2027.app"` for a nonstandard
   AutoCAD install.
3. Launch AutoCAD and open a drawing. Show the AutoCAD command line with
   **Command+3** if needed. Paste the complete `(arxload "...")` expression
   printed by the build and press Return. Load this bundle into each new
   AutoCAD process you intend to use. If AutoCAD prompts about the locally built
   bundle, review the path and approve loading that bundle.
4. From the adapter directory in Terminal, run:

   ```sh
   ./qlay-autocad doctor
   ```

   The response identifies `KlayoutClaw AutoCAD`, the bridge version, its
   socket and the active drawing. Finish any active AutoCAD command before
   submitting drawing edits.

In the local MCP client's project configuration, register a **stdio** server
named `autocad`. For clients that use an `mcpServers` JSON object, use this
entry with the checkout's absolute path:

```json
{
  "mcpServers": {
    "autocad": {
      "command": "/absolute/path/to/KlayoutClaw/plugin/autocad/qlay-autocad",
      "args": ["serve"]
    }
  }
}
```

Start a new agent session, select the AutoCAD Skill, and use the `autocad`
server for that task. Each MCP process tracks its own task drawings. If two
AutoCAD processes have the bridge loaded, set `KLAYOUTCLAW_AUTOCAD_SOCKET` in
the MCP server's environment to the intended path listed by `doctor`. After
restarting AutoCAD, start a new MCP session to pick up the new socket and
document IDs.

## Make a first drawing

For a small check, ask the agent to create a task drawing, put a 12 × 10 µm
rectangle on `20/0`, save it to a new DWG, and export `20/0` to GDS. Use a
fresh output folder and absolute paths. The tool sequence is
`create_layout`, `add_rect`, `save_layout` for DWG, then `save_layout` with
`layers: ["20/0"]` for GDS. Use `close_layout_view` with the returned
`document_id` after saving.

- `import_layout` copies the saved DWG to a new file alongside the source
  and opens that copy, so relative references still resolve. The source
  directory must be writable and the DWG must declare micrometres
  (`INSUNITS=13`). The tool returns the working path for future saves.
  GDS/OASIS imports also open in a separate native drawing.
- Use world coordinates in µm. The string `20/9` maps to native layer
  `L020_D009` and stays separate from `20/0`. Pass the desired
  layer/datatype for each shape, and list the exact pairs when exporting
  masks. To export existing area geometry from a named DWG layer, give that
  layer its numeric encoding in AutoCAD first.
- Native raster references use the supplied local image file. Keep the image
  accessible at that path when reopening the DWG. GDS/OASIS export reads back
  the native drawing and flattens supported area geometry. Review the tool's
  ignored entity summary and independently inspect the saved mask. Images and annotations belong in the DWG.
- If the active AutoCAD drawing changes, activate the task drawing by ID before
  editing. If a request times out, inspect the drawing before retrying.

`./qlay-autocad tools` prints the available tool schemas. AutoCAD-specific
routing, optical registration and Qlaybot transport integration are separate
from this adapter.

## Verify the installation

Run the live check from `plugin/autocad` with the bundle loaded and AutoCAD
idle. It creates disposable task drawings and checks MCP edits, the drawing
identity guard, DWG reopen and the exported GDS.

```sh
.venv/bin/python tests/check_native.py
```

The geometry and protocol tests run separately:

```sh
.venv/bin/python -m pip install pytest
.venv/bin/python -m pytest -q tests/check_geometry_formats.py tests/check_protocol.py
```
