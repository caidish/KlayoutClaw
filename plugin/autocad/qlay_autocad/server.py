"""MCP stdio transport (newline-delimited JSON-RPC 2.0)."""
import json
import sys
from . import __version__
from .tools import definitions, invoke


def json_default(value):
    if hasattr(value,'item'):return value.item()
    if hasattr(value,'tolist'):return value.tolist()
    return str(value)


def dispatch(request):
    if not isinstance(request,dict) or request.get('jsonrpc')!='2.0' or not isinstance(request.get('method'),str):
        return {'jsonrpc':'2.0','id':request.get('id') if isinstance(request,dict) else None,'error':{'code':-32600,'message':'Invalid request'}}
    if 'id' not in request:return None
    identity=request['id'];method=request['method'];params=request.get('params') or {}
    try:
        if method=='initialize':
            supported={'2024-11-05','2025-03-26','2025-06-18','2025-11-25'}
            version=params.get('protocolVersion','2025-11-25')
            result={'protocolVersion':version if version in supported else '2025-11-25','capabilities':{'tools':{'listChanged':False}},
                'serverInfo':{'name':'KlayoutClaw AutoCAD','version':__version__},
                'instructions':'Create or open a task drawing in this session before editing. Use world micrometres and explicit layer/datatype pairs. Save the drawing before closing it.'}
        elif method=='ping':result={}
        elif method=='tools/list':result={'tools':definitions()}
        elif method=='tools/call':
            try:
                data=invoke(params['name'],params.get('arguments',{}))
                text=json.dumps(data,ensure_ascii=False,default=json_default)
                result={'content':[{'type':'text','text':text}],'isError':False}
                if isinstance(data,dict):result['structuredContent']=data
            except Exception as exc:
                data={'error':str(exc),'type':type(exc).__name__}
                if hasattr(exc,'data'):data['detail']=exc.data
                result={'content':[{'type':'text','text':json.dumps(data)}],'structuredContent':data,'isError':True}
        elif method=='resources/list':result={'resources':[]}
        elif method=='prompts/list':result={'prompts':[]}
        else:return {'jsonrpc':'2.0','id':identity,'error':{'code':-32601,'message':'Method not found'}}
        return {'jsonrpc':'2.0','id':identity,'result':result}
    except (KeyError,TypeError,ValueError) as exc:
        return {'jsonrpc':'2.0','id':identity,'error':{'code':-32602,'message':str(exc)}}


def serve():
    for line in sys.stdin:
        try:response=dispatch(json.loads(line))
        except json.JSONDecodeError:response={'jsonrpc':'2.0','id':None,'error':{'code':-32700,'message':'Parse error'}}
        if response is not None:
            sys.stdout.write(json.dumps(response,ensure_ascii=False,default=json_default)+'\n');sys.stdout.flush()
