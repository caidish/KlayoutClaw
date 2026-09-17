# Python distribution and uv installation

This document describes the new Python distribution boundary. The package
name `klayoutclaw` and initial version `0.6.0` are provisional until the
upstream maintainer confirms and reserves the PyPI namespace and adopts a
release-version contract.

The wheel owns only the KLayout integration: the two autorun macros, routing
and evaluation workers, and VC support modules. Claude skills remain in the
Claude plugin marketplace, and Qlaybot remains a Node application. KLayout
itself is a prerequisite and is not installed by this package.

## Build and test from a checkout

The root `plugin/` directory and selected files in `tools/` are canonical.
After editing an installed runtime file, refresh the checked-in wheel payload:

```bash
python scripts/sync_payload.py
uv run --isolated --no-project --with pytest pytest packaging_tests
uv build --no-sources
python scripts/validate_artifacts.py dist
```

The drift test recursively covers every file in `plugin/klayoutclaw_vc/` and
every `plugin/klayoutclaw_*.lym` macro. The artifact validator rejects mixed
repository content such as tests, fixtures, documentation, agent sources, and
local configuration.

## Install a built wheel

```bash
uv tool install --no-index --find-links dist klayoutclaw
klayoutclaw install
klayoutclaw status
```

`klayoutclaw install` records file ownership and hashes in KLayout's macro
directory. It refuses to overwrite unowned or locally modified files unless
the exact operation is repeated with `--force`. It also records the uv tool
environment's Python interpreter for the optional workers; use
`klayoutclaw doctor` to execute dependency imports in that interpreter and
inspect structured diagnostics for import, launch, timeout, or output errors.

Each owned file is staged beside its destination and replaced atomically. The
operation is resumable rather than globally transactional: if a later write
fails, already-replaced files remain valid and a repeated `install` adopts
matching files before completing the manifest. A failure-injection test locks
in this guarantee.

For compatibility with the original installer, missing `plugin/__init__.py`
and `tools/__init__.py` namespace markers are created as empty files. These are
generic shared paths: existing files are never overwritten, the markers are
not claimed in KlayoutClaw's ownership manifest, and uninstall never removes
them.

The `[workers]` extra is intentionally optional:

```bash
uv tool install 'klayoutclaw[workers]'
klayoutclaw install
```

The currently inherited `klayout==0.30.3` pin has an Apple Silicon wheel for
Python 3.11. Use `--python 3.11` when installing the extra on Apple Silicon
until upstream decides whether to retain that exact legacy pin or adopt a
compatible KLayout release range.

The KLayout macro does not consume the recorded worker interpreter yet. That
runtime integration is deliberately deferred until the configurable-port
change lands upstream and this branch is rebased, avoiding overlapping edits
to the server macro.

To remove the copied payload safely, uninstall it before removing the uv tool:

```bash
klayoutclaw uninstall
uv tool uninstall klayoutclaw
```
