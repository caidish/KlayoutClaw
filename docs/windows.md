# Install KlayoutClaw on Windows

Use this guide on a 64-bit Windows PC. You’ll install the plugin in KLayout
and check that Codex can connect to it on the same computer.

## 1. Install KLayout and Python Install Manager

1. On the [KLayout downloads page](https://www.klayout.de/build.html), download
   the **64bit installer** for Windows (the file ending in `win64-install.exe`).
   Open it and follow the setup prompts. Keep the default options.
2. On the [Python downloads page](https://www.python.org/downloads/windows/),
   download **Latest Python install manager**. Open the downloaded file and
   click **Install**. If you already have Python 3.11, you can skip this.

## 2. Download and unzip KlayoutClaw

1. On the [KlayoutClaw project page](../), click **Code → Download ZIP**.
2. In your **Downloads** folder, right-click the ZIP file, choose
   **Extract All…**, and click **Extract**.
3. Open the extracted folder that contains `install.py` (it may appear as
   `install`) and the `plugin` folder.

## 3. Install the plugin

1. If KLayout is open, save your work and close it.
2. In the extracted folder, press **Ctrl+L** in File Explorer. Type
   `powershell` and press **Enter** to open a PowerShell window in that folder.
3. Copy the first line below into PowerShell and press **Enter**. Wait for it
   to finish, then do the same with the second line. If you already have
   Python 3.11, run only the second line.

   ```powershell
   py install 3.11
   py -3.11 install.py
   ```

   When you see `Done!`, close PowerShell.

## 4. Open KLayout and check the plugin

Open **Start**, search for **KLayout**, and choose the shortcut marked
**Editor**. Look at the bottom of the KLayout window for **MCP: Running**
with a green dot. Leave KLayout open while you use the plugin.

## 5. Connect Codex and try it

1. Open Codex in the [ChatGPT desktop app](https://chatgpt.com/download/) on
   the same PC and sign in if prompted. Go to **Settings → MCP servers → Add server**.
2. Name the server `klayoutclaw`, choose **Streamable HTTP**, and paste
   `http://127.0.0.1:8765/mcp` into the URL field. Save, then click **Restart**.
3. Start a new local Codex chat. If asked to choose a folder, select the one
   you extracted. Send “Call KlayoutClaw’s ping tool and tell me
   its status.” If it returns `ok`, you’re connected.

See the [Codex setup instructions](https://learn.chatgpt.com/docs/extend/mcp#configure-in-the-chatgpt-desktop-app)
for help finding these settings.

## If you get stuck

- If PowerShell doesn’t recognize `py`, finish installing Python Install
  Manager, then open a new PowerShell window from the extracted folder.
- If `py install 3.11` says it can’t open a file named `install`, run
  `pymanager install 3.11` and then continue with the second line.
- If PowerShell can’t find `install.py`, repeat step 3 from the folder that
  contains it.
- If **MCP: Running** is missing, close all KLayout windows and reopen the
  Editor. If the Editor shortcut is missing, enable **Edit mode** under
  **File → Setup → Application**, then restart KLayout.
- If Codex can’t connect, check that KLayout is still open and that the URL
  in Codex matches the one in step 5.

For automatic routing, design evaluation, or testing drawing and file exports,
see [Advanced Windows setup and validation](windows-validation.md).
