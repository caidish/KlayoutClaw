---
name: autocad
description: Edit and export layout geometry in the optional AutoCAD for Mac backend when the user selects AutoCAD or requests native DWG editing through its MCP server.
---

# AutoCAD layout editing

Use the local `autocad` MCP tools. If the server is unavailable, follow the
[AutoCAD setup guide](../../docs/autocad.md). The KLayout Skills use a different
server and tool API.

1. Check `ping` and `documents`. Create a task drawing with `create_layout`
   or open a task copy using `import_layout`. Keep the returned document ID.
2. Work in world micrometres. Use the device's layer/datatype pairs when
   supplied, such as `20/0` and `20/9`. Use `add_polygon` with interior
   `holes` for filled masks, `add_path` for constant-width editable paths,
   and `add_image` with calibrated µm per pixel and a lower-left world origin
   for reference images.
3. If the user switches drawings, use `activate_document` with the task ID
   before resuming. After a timeout, inspect the native state before retrying.
4. Save the native DWG with `save_layout`. Use the same tool with a GDS/OASIS
   path and an explicit list of layer/datatype pairs for a mask export. Verify
   the saved file's units, layers and area geometry by reading it back. Close
   a saved task drawing by ID only when it is no longer needed.
