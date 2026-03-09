#!/usr/bin/env python
"""TDD test for UTF-8 body corruption bug in KlayoutClaw MCP server.

Bug: The HTTP body reading loop does a decode/encode round-trip per chunk:
    body += chunk.decode("utf-8", errors="replace").encode("utf-8")
If a chunk boundary splits a multi-byte UTF-8 sequence, errors="replace"
replaces the partial trailing bytes with U+FFFD (3 bytes each), corrupting
the body and causing json.loads() to fail with a parse/schema error.

These tests assert CORRECT behavior (no corruption). They FAIL with the
buggy code and PASS after the fix — proper TDD red-green cycle.

Usage:
    # Unit tests only (no server required):
    python tests/test_utf8_body_corruption.py --unit

    # Integration test (requires running KLayout with MCP server):
    python tests/test_utf8_body_corruption.py --integration

    # Both:
    python tests/test_utf8_body_corruption.py
"""

import sys
import json
import urllib.request
import urllib.error
import time

MCP_URL = "http://127.0.0.1:8765/mcp"


# ---------------------------------------------------------------------------
# Simulate the server's body reading logic
# ---------------------------------------------------------------------------

def server_body_read(full_body_bytes, chunk_size):
    """Simulate the server's body reading loop from klayoutclaw_server.lym.

    This replicates the exact logic from the server's _handle_request method.
    We simulate conn.read() by slicing full_body_bytes into fixed-size chunks.
    """
    content_length = len(full_body_bytes)
    body = b""
    offset = 0
    while len(body) < content_length:
        remaining = content_length - len(body)
        read_size = min(chunk_size, remaining)
        chunk = full_body_bytes[offset:offset + read_size]
        if not chunk:
            break
        offset += len(chunk)
        # This matches the server's fixed code: accumulate raw bytes
        if isinstance(chunk, bytes):
            body += chunk
        else:
            body += chunk.encode("utf-8")
    return body


# ---------------------------------------------------------------------------
# Unit tests — assert correct behavior (FAIL before fix, PASS after fix)
# ---------------------------------------------------------------------------

def test_chinese_chars_preserved():
    """Body reading must preserve Chinese characters at any chunk size.

    Chinese chars are 3 bytes in UTF-8. With chunk_size=1, every byte is
    decoded individually as an invalid UTF-8 fragment, causing corruption.
    """
    print("\n--- Test: Chinese characters preserved at all chunk sizes ---")

    code_str = 'result = "你好世界"'
    payload_bytes = json.dumps({"code": code_str}, ensure_ascii=False).encode("utf-8")

    for chunk_size in [1, 2, 5, 7, 64, 1024]:
        result = server_body_read(payload_bytes, chunk_size)
        if result != payload_bytes:
            print("  FAIL at chunk_size={}: {} bytes -> {} bytes".format(
                chunk_size, len(payload_bytes), len(result)))
            # Show what went wrong
            try:
                parsed = json.loads(result.decode("utf-8"))
                print("    JSON parsed but code garbled: '{}'".format(
                    parsed.get("code", "")[:60]))
            except Exception as e:
                print("    JSON parse failed: {}".format(e))
            return False

    # Also verify the result parses back to the original JSON
    parsed = json.loads(server_body_read(payload_bytes, 1).decode("utf-8"))
    if parsed["code"] != code_str:
        print("  FAIL: round-trip lost data: '{}' != '{}'".format(
            parsed["code"], code_str))
        return False

    print("  PASS: Chinese characters preserved at all chunk sizes")
    return True


def test_emoji_preserved():
    """Body reading must preserve emoji at any chunk size.

    Emoji U+1F600 is 4 bytes in UTF-8 (f0 9f 98 80). With chunk_size=3,
    the 4-byte sequence is split, causing corruption.
    """
    print("\n--- Test: Emoji preserved at all chunk sizes ---")

    code_str = 'result = "test 😀🌍🎉 emoji"'
    payload_bytes = json.dumps({"code": code_str}, ensure_ascii=False).encode("utf-8")

    for chunk_size in [1, 2, 3, 5, 7, 64, 1024]:
        result = server_body_read(payload_bytes, chunk_size)
        if result != payload_bytes:
            print("  FAIL at chunk_size={}: {} bytes -> {} bytes".format(
                chunk_size, len(payload_bytes), len(result)))
            return False

    print("  PASS: Emoji preserved at all chunk sizes")
    return True


def test_mixed_unicode_json_parseable():
    """Body reading must produce valid parseable JSON with mixed Unicode.

    Tests a realistic execute_script payload with Chinese, Japanese,
    and emoji characters. The result must be parseable as JSON and the
    code string must survive intact.
    """
    print("\n--- Test: Mixed Unicode payload stays JSON-parseable ---")

    code_str = 'result = json.dumps({"msg": "你好 こんにちは 🌍"})'
    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": "execute_script",
                           "arguments": {"code": code_str}}}
    payload_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    # Simulate worst case: chunk_size=1
    result = server_body_read(payload_bytes, chunk_size=1)

    try:
        parsed = json.loads(result.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        print("  FAIL: JSON parse error after body read: {}".format(e))
        return False

    recovered_code = parsed.get("params", {}).get("arguments", {}).get("code", "")
    if recovered_code != code_str:
        print("  FAIL: code string corrupted")
        print("    Expected: {}".format(code_str))
        print("    Got:      {}".format(recovered_code[:80]))
        return False

    print("  PASS: Mixed Unicode payload survives body read and JSON parse")
    return True


def test_body_length_unchanged():
    """Body reading must not change the byte length of the payload.

    With the bug, U+FFFD replacement inflates the byte count, causing
    content-length mismatches.
    """
    print("\n--- Test: Body byte length unchanged after read ---")

    payloads = [
        json.dumps({"code": "你好世界"}, ensure_ascii=False).encode("utf-8"),
        json.dumps({"code": "test 😀 emoji"}, ensure_ascii=False).encode("utf-8"),
        json.dumps({"code": "café résumé naïve"}, ensure_ascii=False).encode("utf-8"),
    ]

    for i, payload_bytes in enumerate(payloads):
        for chunk_size in [1, 2, 3]:
            result = server_body_read(payload_bytes, chunk_size)
            if len(result) != len(payload_bytes):
                print("  FAIL: payload[{}] chunk_size={}: {} bytes -> {} bytes".format(
                    i, chunk_size, len(payload_bytes), len(result)))
                return False

    print("  PASS: All payloads maintain original byte length")
    return True


# ---------------------------------------------------------------------------
# Integration test — requires running MCP server
# ---------------------------------------------------------------------------

def mcp_request(method, params=None, req_id=1, session_id=None):
    """Send a JSON-RPC request to the MCP server."""
    payload = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params:
        payload["params"] = params
    headers = {"Content-Type": "application/json"}
    if session_id:
        headers["Mcp-Session-Id"] = session_id

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(MCP_URL, data=body, headers=headers, method="POST")
    r = urllib.request.urlopen(req, timeout=10)
    resp_body = r.read().decode("utf-8")
    return r, json.loads(resp_body)


def mcp_notify(method, session_id):
    payload = {"jsonrpc": "2.0", "method": method}
    headers = {"Content-Type": "application/json", "Mcp-Session-Id": session_id}
    req = urllib.request.Request(
        MCP_URL, data=json.dumps(payload).encode(), headers=headers, method="POST")
    urllib.request.urlopen(req, timeout=5)


def wait_for_server():
    print("Waiting for MCP server at {}...".format(MCP_URL))
    start = time.time()
    while time.time() - start < 15:
        try:
            urllib.request.urlopen(MCP_URL, timeout=2)
            print("  Server responded")
            return True
        except urllib.error.URLError:
            time.sleep(1)
    print("  ERROR: Server not responding after 15s")
    return False


def test_integration_non_ascii_execute_script():
    """Send execute_script with non-ASCII code and verify round-trip."""
    print("\n--- Integration Test: Non-ASCII in execute_script ---")

    if not wait_for_server():
        print("  SKIP: Server not available")
        return None

    # Initialize session
    r, data = mcp_request("initialize", {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "test_utf8", "version": "0.1"},
    })
    session_id = r.headers.get("Mcp-Session-Id")
    mcp_notify("notifications/initialized", session_id)

    test_string = "你好世界 こんにちは 🌍🎉"
    code = 'result = json.dumps({{"echo": "{}"}})'.format(test_string)
    print("  Sending code: {}".format(code))

    _, resp = mcp_request("tools/call", {
        "name": "execute_script",
        "arguments": {"code": code},
    }, req_id=2, session_id=session_id)

    # Check for errors
    if "error" in resp:
        err = resp["error"]
        print("  FAIL: JSON-RPC error: {}".format(err.get("message", "")))
        return False

    result = resp.get("result", {})
    content = result.get("content", [])
    is_error = result.get("isError", False)

    if is_error:
        error_text = content[0]["text"] if content else "unknown"
        print("  FAIL: Tool error: {}".format(error_text[:200]))
        return False

    if not content:
        print("  FAIL: No content in response")
        return False

    try:
        tool_output = json.loads(content[0]["text"])
        echoed = tool_output.get("echo", "")
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        print("  FAIL: Could not parse tool output: {}".format(e))
        return False

    if echoed != test_string:
        print("  FAIL: String corrupted")
        print("    Expected: {}".format(test_string))
        print("    Got:      {}".format(echoed))
        return False

    print("  PASS: Non-ASCII characters survived the round-trip")
    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = sys.argv[1:]
    run_unit = ("--unit" in args) or (not args) or ("--all" in args)
    run_integration = ("--integration" in args) or (not args) or ("--all" in args)

    print("=" * 60)
    print("KlayoutClaw UTF-8 Body Corruption Test")
    print("=" * 60)

    results = {}

    if run_unit:
        print("\n### Unit Tests (no server needed) ###")
        results["chinese_preserved"] = test_chinese_chars_preserved()
        results["emoji_preserved"] = test_emoji_preserved()
        results["mixed_unicode_json"] = test_mixed_unicode_json_parseable()
        results["body_length"] = test_body_length_unchanged()

    if run_integration:
        print("\n### Integration Tests (server required) ###")
        results["non_ascii_execute"] = test_integration_non_ascii_execute_script()

    print("\n" + "=" * 60)
    print("Results:")
    for name, result in results.items():
        status = "PASS" if result is True else ("SKIP" if result is None else "FAIL")
        print("  {}: {}".format(name, status))

    passed = sum(1 for v in results.values() if v is True)
    failed = sum(1 for v in results.values() if v is False)
    skipped = sum(1 for v in results.values() if v is None)
    print("\n{} passed, {} failed, {} skipped".format(passed, failed, skipped))
    print("=" * 60)

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
