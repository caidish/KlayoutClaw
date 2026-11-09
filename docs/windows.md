# Run KlayoutClaw on Windows

Use a Windows x64 machine with the desktop version of
[KLayout](https://www.klayout.de/build.html) and 64-bit Python 3.11. Run the
commands in PowerShell from the KlayoutClaw checkout. KLayout and the client
run on the same Windows computer.

## Install the plugin and open KLayout

Save your work and exit KLayout before installing or updating the plugin.

```powershell
py -3.11 --version
py -3.11 install.py
```

The installer prints the path where it copied the macros. On most Windows
machines it is `C:\Users\<you>\KLayout\pymacros`. If you use a custom
[KLayout home](https://www.klayout.de/command_args.html), set `KLAYOUT_HOME` to
that absolute directory in PowerShell before installation and launch KLayout
from the same PowerShell session.

Open KLayout in editor mode. From PowerShell, use the actual path to your
KLayout executable with the `-e` option. For example:

```powershell
$klayout = 'C:\path\to\klayout_app.exe'
Start-Process -FilePath $klayout -ArgumentList '-e'
```

The plugin starts inside KLayout and listens at
`http://127.0.0.1:8765/mcp`.

## Check the connection, drawing and export

With KLayout open, run:

```powershell
py -3.11 -X utf8 tests/check_klayout_gui.py
```

The check connects to MCP, creates a temporary tab, draws a rectangle on
`20/9`, saves a PNG and GDS in a temporary path with spaces and Chinese
characters, and reads the GDS back. It prints the actual KLayout version,
profile and output folder. Leave the test tab active until the script closes it.

## Check routing and design evaluation

Use a separate Python virtual environment for the workers:

```powershell
py -3.11 -m venv ".tmp\Worker Python"
$worker = (Resolve-Path ".tmp\Worker Python\Scripts\python.exe").Path
& $worker -m pip install "klayout==0.30.3" numpy scipy scikit-image gdstk shapely
py -3.11 -X utf8 tests/check_klayout_gui.py --worker-python "$worker"
```

The final run should also print `PASS auto_route` and `PASS evaluate_design`.
For normal work, give the two tools the full path as `python_path`, or launch
KLayout with `PYTHON_PATH` set to use that interpreter for all calls:

```powershell
$env:PYTHON_PATH = $worker
Start-Process -FilePath $klayout -ArgumentList '-e'
```

Exit the previous KLayout process before launching it with the new environment.
You can then check the running server's configured worker Python with
`py -3.11 -X utf8 tests/check_klayout_gui.py --workers-from-server-env`.

## Finish an isolated validation run

For automated checks, install into a disposable KLayout profile and start
that test instance with PowerShell's -WindowStyle Hidden and -PassThru.
Keep the returned process object and put cleanup in a finally block. Close
that test instance on both success and failure, wait for it to exit, and
end any terminal job that was waiting on it before reporting completion.
If the disposable instance cannot close normally, stop that exact process.
Keep any separate KLayout window opened for the user's design review.

Routing and design-evaluation workers run without opening their own Windows
console windows. Their output is still captured for diagnostics.

## Use Codex locally

Open the checkout as a local project in Codex on Windows. To let Codex call
the KLayout tools directly, add or update this entry in the project's
`.codex/config.toml`, then start a new chat in the trusted project. See
[OpenAI Docs for MCP configuration](https://developers.openai.com/codex/mcp).

```toml
[mcp_servers.klayoutclaw]
url = "http://127.0.0.1:8765/mcp"
tool_timeout_sec = 120
```

Ask Codex to call KlayoutClaw's `ping` tool. You can also ask it to run
`py -3.11 -X utf8 tests/check_klayout_gui.py` from the project root and report
the `PASS` lines or the first failure. The test uses synthetic geometry and
leaves your existing drawings alone.

If the server is unreachable, compare the install path with the KLayout
profile and check `klayoutclaw.log` in that profile. For a failed test, capture
the first `FAIL` line, the KLayout version and the last relevant log lines.
