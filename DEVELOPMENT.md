# Development

## Contributing

We welcome contributions from the device physics community. If you're interested in joining our vibrant community building KlayoutClaw for device physicists, reach out to **jiaqi.cai@mit.edu**.

## Setup

```bash
# Clone the repo
git clone https://github.com/caidish/KlayoutClaw.git
cd KlayoutClaw

# Install the plugin into KLayout
python install.py

# Launch KLayout
open /Applications/klayout.app

# Run tests (requires KLayout running)
python tests/test_connection.py
```

## Running Tests

```bash
# Protocol-level connection test
python tests/test_connection.py

# Hall bar creation + structural eval
python tests/create_hallbar.py /tmp/hallbar.gds
python tests/evaluate_gds.py /tmp/hallbar.gds

# Autoroute test (needs conda env instrMCPdev)
bash tests/test_autoroute.sh

# Full E2E (installs plugin, launches KLayout, tests connection)
bash tests/test_connection.sh
```

## Architecture

- **`pya.QTcpServer`** on Qt main thread — no Python threads, no GIL issues
- **No external dependencies** for the server — only Python stdlib + pya
- **`auto_route`** spawns a subprocess for heavy computation (numpy/scipy/scikit-image in conda env `instrMCPdev`)
- **JSON-RPC 2.0** over HTTP (plain JSON, no SSE)
- `.lym` XML: escape `<` `>` `&` as `&lt;` `&gt;` `&amp;` in Python code

See [docs/plans/](docs/plans/) for design documents.
