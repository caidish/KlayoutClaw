# Seven-factor MCP configuration

## Goal

Give the packaged `klayoutclaw-mcp` process one deterministic, inspectable
configuration path while keeping the KLayout GUI server compatible with its
embedded Python runtime.

The design applies the [7-Factor Config](https://7factorconfig.org/)
principles: DRY, decoupled, abstracted, centralized, obvious, secure, and
versioned. Runtime precedence is evaluated per field:

```text
command-line flags > environment > config file > safe defaults
```

The KLayout GUI server cannot receive bridge CLI flags, so it uses the strict
subset `environment > config file > defaults`.

## Analysis

Before implementation, GitNexus reported low risk for all modified runtime
symbols:

- `resolve_mcp_url`: one direct caller (`klayoutclaw-mcp` startup)
- `mcp_bridge.main`: no application callers beyond its console entry point
- `lifecycle.ensure_user_config`: one direct caller (`install`) across the
  installer/CLI flow
- artifact validators: one direct caller each

The existing skill client remains independently importable because Claude
skills may execute without the Python distribution on their `sys.path`.

The final change detector classifies the completed patch as high blast radius
by process count: centralized `klayoutclaw-mcp.main` reaches eight internal
configuration/transport flows, and the artifact allowlist reaches the package
validation flow. Review confirmed that these flows remain bounded to bridge
startup, stdio forwarding, diagnostics, and distribution validation; no
KLayout geometry or tool-dispatch handler is downstream. This concentration is
covered by the full Python gate and a process-level stdio-to-HTTP test.

## Resolution contract

### Config path

1. `--config PATH`
2. `KLAYOUT_MCP_CONFIG`
3. `~/.klayout/klayoutclaw.json`

An explicitly selected missing file is an error. The implicit default file is
optional.

### Endpoint values

1. CLI `--url`, then explicit component flags (`--bind`, `--port`,
   `--endpoint`, `--tls`/`--no-tls`)
2. `KLAYOUT_MCP_URL`; when absent, component environment variables
3. Config `mcp.url`, then explicit `mcp` component values
4. `http://127.0.0.1:8765/mcp`

Within a layer, the complete URL establishes a base and component CLI/config
values refine it. `KLAYOUT_MCP_URL` remains authoritative within the
environment layer for backward compatibility.

## Seven-factor mapping

| Principle | Implementation |
|---|---|
| DRY | One `ResolvedMcpConfig` model declares endpoint values and provenance. |
| Decoupled | Deployment values remain outside source code in flags, env, or the user config. |
| Abstracted | Bridge transport consumes a validated URL, independent of how values were fetched. |
| Centralized | Parsing, validation, precedence, and diagnostics live in `klayoutclaw.config`. |
| Obvious | `klayoutclaw-mcp --print-config` reports the effective non-secret values and each source. |
| Secure | URL credentials, query strings, and fragments are rejected; diagnostics omit TLS key/certificate fields. |
| Versioned | New configs contain top-level `"schema": 1`; unversioned legacy configs remain accepted. |

## Validation and compatibility

- Ports are integers from 1 through 65535.
- Endpoints are absolute paths without whitespace, query strings, or fragments.
- Boolean values accept JSON booleans and common environment spellings.
- HTTP and HTTPS are supported; IPv6 hosts are serialized with brackets.
- Wildcard server bind addresses resolve to loopback for client connections.
- Existing `resolve_mcp_url(environ)` callers retain a URL-only compatibility
  wrapper.
- Existing user files are preserved by installation and are never rewritten.

## Acceptance criteria

- Every precedence boundary has a unit test.
- Explicit missing/invalid config fails before bridge startup.
- `--print-config` is deterministic and contains no certificate/key material.
- The complete Python gate, lint, security scan, and artifact validator pass.
- Wheel and sdist contain the centralized config module.
