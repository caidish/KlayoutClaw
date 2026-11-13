import pytest
from qlay_autocad.bridge import lisp,validate_lisp,wrap_lisp_lines
from qlay_autocad.server import dispatch


def test_native_expression_wrapping_preserves_strings_and_comments():
    expression='(list '+lisp('quoted (text) with " and spaces')+' '+('(+ 1 2) '*1000)+') ; keep this comment\n'
    wrapped=wrap_lisp_lines(expression)
    validate_lisp(wrapped)
    assert lisp('quoted (text) with " and spaces') in wrapped
    assert '; keep this comment\n' in wrapped
    assert max(map(len,wrapped.splitlines()))<1100
    with pytest.raises(ValueError):validate_lisp('(list 1 2))')


def test_mcp_notifications_and_tool_errors():
    assert dispatch({'jsonrpc':'2.0','method':'notifications/initialized'}) is None
    initialized=dispatch({'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-11-25'}})
    assert initialized['result']['capabilities']['tools']=={'listChanged':False}
    failed=dispatch({'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'add_rect','arguments':{}}})
    assert failed['result']['isError'] is True
    assert 'Missing required argument' in failed['result']['structuredContent']['error']
