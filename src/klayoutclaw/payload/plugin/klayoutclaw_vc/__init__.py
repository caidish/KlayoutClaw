"""qlaybot v0.4.4 Phase 4 — KlayoutClaw Version Control submodule.

Public API is intentionally sparse and consumed by the Phase 5 server bridge
(``plugin/klayoutclaw_server.lym``) and the Phase 6 UI panel
(``plugin/klayoutclaw_ui.lym``).  The submodule is deliberately importable as
plain Python (no pya side-effects at import time) so it can be unit-tested
without KLayout's GUI.
"""
