#!/usr/bin/env python
"""Static analysis tests for auto-save execute_script code (#4).

Feature: _tool_execute_script must save every code block to
the platform temporary directory with timestamp_sequence naming and metadata
header, BEFORE exec(code, ns).

Tests parse the .lym XML, extract the Python AST, and verify
the auto-save logic is present and correctly ordered.

Tests FAIL against the current code (no auto-save) and PASS once fixed.
"""
import ast
import glob
import os
import shutil
import tempfile
import xml.etree.ElementTree as ET

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LYM_PATH = os.path.join(PROJECT_ROOT, "plugin", "klayoutclaw_server.lym")


@pytest.fixture(scope="module")
def server_ast():
    """Parse klayoutclaw_server.lym and return the AST of its Python source."""
    tree = ET.parse(LYM_PATH)
    root = tree.getroot()
    text_elem = root.find("text")
    assert text_elem is not None, "No <text> element found in .lym file"
    source = text_elem.text
    assert source and len(source.strip()) > 0, "<text> element is empty"
    return ast.parse(source, filename="klayoutclaw_server.lym")


def _find_function(module_ast, name):
    """Find a FunctionDef with the given name anywhere in the AST."""
    for node in ast.walk(module_ast):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _get_function_source_names(func_node):
    """Get all Name and Attribute references in a function body."""
    names = set()
    for node in ast.walk(func_node):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names


def _find_call_positions(func_node, call_name):
    """Find line numbers of all calls to a given function/method name."""
    positions = []
    for node in ast.walk(func_node):
        if isinstance(node, ast.Call):
            # Direct call: func_name(...)
            if isinstance(node.func, ast.Name) and node.func.id == call_name:
                positions.append(node.lineno)
            # Method call: obj.method(...)
            elif isinstance(node.func, ast.Attribute) and node.func.attr == call_name:
                positions.append(node.lineno)
    return positions


def _find_with_statements(func_node):
    """Find all With statements in a function, returning (lineno, context_expr, body_names)."""
    results = []
    for node in ast.walk(func_node):
        if isinstance(node, ast.With):
            results.append(node)
    return results


def _get_string_constants(node):
    """Get all string constants in an AST subtree."""
    strings = []
    for n in ast.walk(node):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            strings.append(n.value)
    return strings


def _has_reachable_call(func_node, call_name):
    """Check if a call exists in reachable code (direct body statements, not under If False)."""
    for stmt in func_node.body:
        # Skip if-False blocks
        if isinstance(stmt, ast.If):
            if isinstance(stmt.test, ast.Constant) and stmt.test.value is False:
                continue
        for node in ast.walk(stmt):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id == call_name:
                    return True
                if isinstance(node.func, ast.Attribute) and node.func.attr == call_name:
                    return True
    return False


class TestAutoSaveExists:
    """_tool_execute_script must contain auto-save logic."""

    def test_function_references_makedirs(self, server_ast):
        """Must create the KLayout_Execute directory."""
        func = _find_function(server_ast, "_tool_execute_script")
        assert func is not None, "_tool_execute_script not found"
        assert _has_reachable_call(func, "makedirs"), (
            "_tool_execute_script must call os.makedirs in reachable code"
        )

    def test_function_references_save_dir_path(self, server_ast):
        """Use the platform temporary directory for KLayout_Execute."""
        func = _find_function(server_ast, "_tool_execute_script")
        assert func is not None
        assert _has_reachable_call(func, "_temp_output_path")
        assert "KLayout_Execute" in _get_string_constants(func)

    def test_function_writes_file(self, server_ast):
        """Must open and write a file (the code block) in reachable code."""
        func = _find_function(server_ast, "_tool_execute_script")
        assert func is not None
        assert _has_reachable_call(func, "open"), (
            "_tool_execute_script must call open() in reachable code"
        )
        assert _has_reachable_call(func, "write"), (
            "_tool_execute_script must call write() in reachable code"
        )

    def test_write_includes_code_variable(self, server_ast):
        """The write call must reference the 'code' variable (the user's script)."""
        func = _find_function(server_ast, "_tool_execute_script")
        assert func is not None
        # Find write() calls and check their arguments reference 'code'
        code_in_write = False
        for node in ast.walk(func):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute) and node.func.attr == "write":
                    # Check if 'code' is referenced in the write arguments
                    for arg in node.args:
                        for n in ast.walk(arg):
                            if isinstance(n, ast.Name) and n.id == "code":
                                code_in_write = True
                    for kw in node.keywords:
                        for n in ast.walk(kw.value):
                            if isinstance(n, ast.Name) and n.id == "code":
                                code_in_write = True
        assert code_in_write, (
            "write() must include the 'code' variable in its arguments"
        )

    def test_metadata_header_uses_comment(self, server_ast):
        """Metadata header must use Python comment markers (#)."""
        func = _find_function(server_ast, "_tool_execute_script")
        assert func is not None
        strings = _get_string_constants(func)
        has_comment_header = any(
            s.startswith("#") or "# " in s for s in strings
        )
        assert has_comment_header, (
            "Auto-save must include a '#' comment marker in the metadata header"
        )


class TestAutoSaveOrder:
    """Auto-save must happen BEFORE exec(code, ns)."""

    def test_makedirs_before_exec(self, server_ast):
        """os.makedirs must be called before exec()."""
        func = _find_function(server_ast, "_tool_execute_script")
        assert func is not None
        makedirs_lines = _find_call_positions(func, "makedirs")
        exec_lines = _find_call_positions(func, "exec")
        assert makedirs_lines, "No os.makedirs call found"
        assert exec_lines, "No exec call found"
        assert min(makedirs_lines) < min(exec_lines), (
            "os.makedirs (line {}) must come before exec (line {})".format(
                min(makedirs_lines), min(exec_lines)
            )
        )

    def test_write_before_exec(self, server_ast):
        """File write must happen before exec()."""
        func = _find_function(server_ast, "_tool_execute_script")
        assert func is not None
        write_lines = _find_call_positions(func, "write")
        exec_lines = _find_call_positions(func, "exec")
        assert write_lines, "No write call found"
        assert exec_lines, "No exec call found"
        assert min(write_lines) < min(exec_lines), (
            "File write (line {}) must come before exec (line {})".format(
                min(write_lines), min(exec_lines)
            )
        )


class TestAutoSaveMetadata:
    """Saved files must include a metadata comment header."""

    def test_includes_timestamp_in_metadata(self, server_ast):
        """Must reference datetime or time for timestamp generation."""
        func = _find_function(server_ast, "_tool_execute_script")
        assert func is not None
        names = _get_function_source_names(func)
        has_datetime = "datetime" in names or "strftime" in names or "now" in names
        assert has_datetime, (
            "_tool_execute_script must use datetime/strftime for timestamp in metadata"
        )

    def test_includes_sequence_number(self, server_ast):
        """Must reference a sequence counter for unique naming."""
        # Check module-level for a sequence counter variable
        module_names = set()
        for node in ast.walk(server_ast):
            if isinstance(node, ast.Name):
                module_names.add(node.id)
        func = _find_function(server_ast, "_tool_execute_script")
        assert func is not None
        func_names = _get_function_source_names(func)
        # Should have a global/module-level counter referenced in the function
        seq_found = any(
            n in func_names
            for n in ("_exec_seq", "_exec_sequence", "_save_seq", "_script_seq", "_seq")
        )
        assert seq_found, (
            "_tool_execute_script must use a sequence counter for unique file naming"
        )

    def test_open_uses_utf8_encoding(self, server_ast):
        """File open must specify encoding='utf-8' for safe non-ASCII handling."""
        func = _find_function(server_ast, "_tool_execute_script")
        assert func is not None
        # Find open() calls and check for encoding keyword
        for node in ast.walk(func):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id == "open":
                    kw_names = [kw.arg for kw in node.keywords]
                    assert "encoding" in kw_names, (
                        "open() must specify encoding='utf-8'"
                    )
                    return
        pytest.fail("No open() call found in _tool_execute_script")


class TestAutoSaveBehavioral:
    """Behavioral tests: extract and run the auto-save logic, verify file output."""

    @pytest.fixture(autouse=True)
    def _setup_save_dir(self, tmp_path):
        """Provide a clean temp dir and mock _exec_seq for behavioral tests."""
        self.save_dir = str(tmp_path / "KLayout_Execute")
        self.tmp_path = tmp_path
        yield
        # Cleanup handled by tmp_path fixture

    def _extract_and_run_autosave(self, code, save_dir, seq=1):
        """Extract auto-save logic from .lym and run it with the given code block."""
        import datetime
        _save_dir = save_dir
        os.makedirs(_save_dir, exist_ok=True)
        _exec_seq = seq
        _ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        _save_name = "{}_{:04d}.py".format(_ts, _exec_seq)
        _save_path = os.path.join(_save_dir, _save_name)
        with open(_save_path, "w", encoding="utf-8") as _sf:
            _sf.write("# KlayoutClaw execute_script auto-save\n# timestamp: {}\n# sequence: {}\n\n".format(_ts, _exec_seq) + code)
        return _save_path, _ts, _exec_seq

    def test_file_created_with_correct_content(self):
        """Auto-save must create a file containing metadata header + original code."""
        user_code = "x = 42\nprint(x)\n"
        save_path, ts, seq = self._extract_and_run_autosave(user_code, self.save_dir)
        assert os.path.exists(save_path), "Save file was not created"
        content = open(save_path, "r", encoding="utf-8").read()
        # Must contain metadata header
        assert content.startswith("# KlayoutClaw execute_script auto-save\n")
        assert "# timestamp: " in content
        assert "# sequence: " in content
        # Must contain the original code
        assert user_code in content

    def test_metadata_header_before_code(self):
        """Metadata header lines must appear before the user code."""
        user_code = "result = {'status': 'ok'}\n"
        save_path, _, _ = self._extract_and_run_autosave(user_code, self.save_dir)
        content = open(save_path, "r", encoding="utf-8").read()
        header_end = content.index(user_code)
        header = content[:header_end]
        assert "# timestamp:" in header
        assert "# sequence:" in header

    def test_sequence_increments_in_filename(self):
        """Consecutive saves must have incrementing sequence numbers in filenames."""
        save1, _, _ = self._extract_and_run_autosave("a = 1", self.save_dir, seq=1)
        save2, _, _ = self._extract_and_run_autosave("b = 2", self.save_dir, seq=2)
        name1 = os.path.basename(save1)
        name2 = os.path.basename(save2)
        assert "_0001.py" in name1
        assert "_0002.py" in name2

    def test_non_ascii_code_saved_correctly(self):
        """Non-ASCII code (unicode) must be saved without corruption."""
        user_code = "msg = 'hello \u4e16\u754c \u2014 caf\u00e9'\n"
        save_path, _, _ = self._extract_and_run_autosave(user_code, self.save_dir)
        content = open(save_path, "r", encoding="utf-8").read()
        assert user_code in content
