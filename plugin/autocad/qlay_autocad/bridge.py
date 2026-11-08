"""Standard-library client for the QlayAutoCAD native Unix socket."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import socket
import stat
import time
import uuid

MAX_MESSAGE = 64 * 1024 * 1024


class BridgeError(RuntimeError):
    def __init__(self, data):
        self.data = data
        super().__init__(data.get('error', str(data)) if isinstance(data, dict) else str(data))


def lisp(value):
    """Encode data as AutoLISP literals. Does not treat strings as executable code."""
    if value is None or value is False:
        return 'nil'
    if value is True:
        return 'T'
    if isinstance(value, str) or isinstance(value, Path):
        return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t') + '"'
    if isinstance(value, (list, tuple)):
        return '(list ' + ' '.join(lisp(x) for x in value) + ')'
    if isinstance(value, (int, float)):
        import math
        if not math.isfinite(value):
            raise ValueError('Nonfinite coordinates are not supported')
        return repr(value)
    raise TypeError(f'Cannot encode {type(value).__name__} as AutoLISP')


def validate_lisp(expression):
    depth=0;quoted=False;escaped=False;comment=False
    for char in expression:
        if comment:
            if char=='\n':comment=False
        elif quoted:
            if escaped:escaped=False
            elif char=='\\':escaped=True
            elif char=='"':quoted=False
        elif char==';':comment=True
        elif char=='"':quoted=True
        elif char=='(':depth+=1
        elif char==')':
            depth-=1
            if depth<0:raise ValueError('Unbalanced AutoLISP: unexpected closing parenthesis')
    if quoted or depth:raise ValueError('Unbalanced AutoLISP string or parentheses; request was not queued')


def wrap_lisp_lines(expression,width=1000):
    """Respect AutoCAD's input-line limit without changing strings or comments."""
    output=[];length=0;quoted=False;escaped=False;comment=False
    for char in expression:
        if char=='\n':
            length=0;comment=False
        else:length+=1
        if comment:
            output.append(char);continue
        if quoted:
            if escaped:escaped=False
            elif char=='\\':escaped=True
            elif char=='"':quoted=False
        elif char==';':comment=True
        elif char=='"':quoted=True
        elif char.isspace() and length>=width:
            char='\n';length=0
        output.append(char)
    return ''.join(output)


class Bridge:
    def __init__(self, socket_path=None, timeout=180):
        self.socket_path = Path(socket_path) if socket_path else None
        self.timeout = timeout

    def _discover(self):
        if self.socket_path is None:
            override = os.environ.get("KLAYOUTCLAW_AUTOCAD_SOCKET")
            if override:
                self.socket_path = Path(override)
            else:
                base = Path("/tmp") / f"klayoutclaw-autocad-{os.getuid()}"
                candidates = []
                for path in sorted(base.glob("bridge-*.sock")):
                    try:
                        pid = int(path.stem.removeprefix("bridge-"))
                        if pid <= 0 or not stat.S_ISSOCK(path.lstat().st_mode):
                            continue
                        os.kill(pid, 0)
                    except (OSError, ValueError):
                        continue
                    candidates.append(path)
                if not candidates:
                    raise BridgeError({"error": "AutoCAD bridge unavailable. Open a drawing and load the built bundle with arxload."})
                if len(candidates) != 1:
                    raise BridgeError({
                        "error": "Multiple AutoCAD sessions found. Set KLAYOUTCLAW_AUTOCAD_SOCKET to the intended socket.",
                        "sockets": [str(path) for path in candidates],
                    })
                self.socket_path = candidates[0]

        path = self.socket_path
        try:
            directory = path.parent.lstat()
            endpoint = path.lstat()
        except OSError as exc:
            raise BridgeError({"error": "AutoCAD socket is unavailable. Load the bundle and reconnect the MCP client."}) from exc
        if (not stat.S_ISDIR(directory.st_mode) or not stat.S_ISSOCK(endpoint.st_mode)
                or directory.st_uid != os.getuid() or endpoint.st_uid != os.getuid()
                or directory.st_mode & 0o077 or endpoint.st_mode & 0o077):
            raise BridgeError({"error": "AutoCAD socket and its directory must be private to the current user"})
        return path

    @contextmanager
    def _locked(self):
        path = self._discover()
        fd = os.open(str(path) + '.client.lock', os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield path
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def call(self, method, params=None):
        request_id = uuid.uuid4().hex
        payload = (json.dumps({'id': request_id, 'method': method, 'params': params or {}}, ensure_ascii=False) + '\n').encode()
        if len(payload) > MAX_MESSAGE:
            raise ValueError('Native request exceeds 64 MiB; split the job into batches')
        with self._locked() as path:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
                conn.settimeout(self.timeout)
                conn.connect(str(path))
                conn.sendall(payload)
                data = bytearray()
                while not data.endswith(b'\n'):
                    block = conn.recv(65536)
                    if not block:
                        raise BridgeError({'error': 'Native connection closed before a complete response; mutation outcome may be indeterminate'})
                    data.extend(block)
                    if len(data) > MAX_MESSAGE:
                        raise BridgeError({'error': 'Native response exceeds the configured limit'})
        response = json.loads(data)
        if response.get('id') != request_id:
            raise BridgeError({'error': 'Native response/request identity mismatch'})
        if not response.get('success'):
            raise BridgeError(response.get('data', response))
        return response['data']

    def ping(self):
        return self.call('ping')

    def documents(self):
        return self.call('documents')

    def active_document(self):
        active = [d for d in self.documents() if d['active']]
        if len(active) != 1:
            raise BridgeError({'error': 'Exactly one active AutoCAD document is required'})
        return active[0]

    def eval_lisp(self, expression, document_id=None, *, json_result=False, timeout=120):
        validate_lisp(expression)
        expression=wrap_lisp_lines(expression)
        document_id = document_id or self.active_document()['id']
        result = self.call('eval_lisp', {'expression': expression, 'document_id': document_id, 'timeout': timeout})['value']
        return json.loads(result) if json_result else result

    def open_document(self, path):
        path = Path(path).resolve(strict=True)
        if any(Path(doc["path"]).resolve() == path for doc in self.documents()):
            raise BridgeError({"error": "This drawing is already open. Use a separate task copy."})
        self.call("open_document", {"path": str(path)})
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            for doc in self.documents():
                if doc["active"] and Path(doc["path"]).resolve() == path:
                    return doc
            time.sleep(0.05)
        raise BridgeError({
            "error": "Opened drawing did not become active within 30s. Inspect documents before retrying.",
            "indeterminate": True,
        })

    def save_document(self, path, document_id=None):
        path = Path(path).resolve()
        if path.suffix.lower() not in {'.dwg', '.dxf'}:
            raise ValueError('Native save requires .dwg or .dxf')
        path.parent.mkdir(parents=True, exist_ok=True)
        document_id = document_id or self.active_document()['id']
        result = self.call('save_document', {'path': str(path), 'format': path.suffix[1:].lower(),
            'document_id': document_id})
        # AcDbDatabase::saveAs writes the file and updates its path, but leaves
        # DBMOD set in the editor. Finish the editor save before reporting a
        # clean DWG that can be closed without discarding pending changes.
        if path.suffix.lower() == '.dwg':
            dbmod = self.eval_lisp('(progn (command-s "_.QSAVE") (getvar "DBMOD"))', document_id)
            if dbmod != '0':
                raise BridgeError({'error': 'DWG was written but AutoCAD did not mark the drawing as saved'})
        return result

    def activate_document(self, document_id):
        self.call('activate_document', {'document_id': document_id})
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            active=self.active_document()
            if active['id']==document_id:return active
            time.sleep(.05)
        raise BridgeError({'error':'Document activation did not complete; inspect documents before retrying'})

    def close_document(self, document_id):
        self.call('close_document', {'document_id':document_id})
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            if all(d['id']!=document_id for d in self.documents()):return {'closed':document_id}
            time.sleep(.05)
        raise BridgeError({'error':'Document close did not complete; inspect documents before retrying'})
