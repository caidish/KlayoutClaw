# KlayoutClaw

KlayoutClaw connects the KLayout GUI to AI agents through the Model Context
Protocol (MCP). It installs KLayout autorun macros, routing and evaluation
workers, and version-control support into KLayout's user macro directory.

The core MCP server uses only the Python standard library and KLayout's `pya`
module. Scientific worker dependencies are available through the optional
`workers` extra.

```bash
uv tool install klayoutclaw
klayoutclaw install
klayoutclaw status
```

For routing and design-evaluation workers on Apple Silicon, use Python 3.11
with the current dependency contract:

```bash
uv tool install --python 3.11 'klayoutclaw[workers]'
klayoutclaw install
klayoutclaw doctor
```

KLayout itself remains a prerequisite. Restart KLayout after installing or
updating the macros. The MCP endpoint defaults to
`http://127.0.0.1:8765/mcp`.

Project documentation, source code, and issue tracking are available in the
[KlayoutClaw repository](https://github.com/caidish/KlayoutClaw).
