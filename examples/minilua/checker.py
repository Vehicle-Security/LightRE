"""Fixed sample checker; independent of editable Java harness and LightRE core."""
import hashlib
from pathlib import Path

def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def normalize(request, base=None):
    request = dict(request)
    allowed = {'input_file', 'input_hex', 'chunk_name', 'argument'}
    if set(request) - allowed:
        raise ValueError(f'Unknown minilua parameters: {set(request) - allowed}')
    if ('input_file' in request) == ('input_hex' in request):
        raise ValueError('Provide exactly one of input_file or input_hex')
    origin = None
    if 'input_file' in request:
        path = (Path(base or Path.cwd()) / request.pop('input_file')).resolve()
        data = path.read_bytes()
        origin = {'path': str(path), 'sha256': digest(path)}
    else:
        data = bytes.fromhex(request.pop('input_hex'))
    if len(data) > 1024 * 1024:
        raise ValueError('Target input is limited to 1 MiB')
    for key in ('chunk_name', 'argument'):
        value = request.get(key)
        if key == 'argument' and value is None:
            continue
        if not isinstance(value, str) or '\x00' in value or len(value.encode()) > 4096:
            raise ValueError(f'{key} must be a string without NUL (up to 4096 bytes)')
    return ({**request, 'input_file': 'input.bin'}, data, origin)

def expected(normalized, data, rows):
    """Only an exact saved input/parameter contract earns a passing assertion."""
    sha = hashlib.sha256(data).hexdigest()
    for row in rows:
        parameters = {k: v for k, v in row['request'].items() if k not in ('input_file', 'input_hex')}
        if parameters == {k: v for k, v in normalized.items() if k != 'input_file'} and row['input_sha256'] == sha:
            result = row['expected']
            if not isinstance(result, dict):
                raise ValueError('Target expected result must be an object')
            if type(result.get('lua_status')) is not int or result['lua_status'] not in (0, 2, 3) or type(result.get('exit_code')) is not int or (result['exit_code'] != int(result['lua_status'] != 0)) or any((not isinstance(result.get(key), str) for key in ('stdout', 'stderr'))):
                raise ValueError('minilua requires explicit Lua status, exit code, stdout and stderr expectations')
            return result
    raise ValueError('No expected result for this input/parameter combination; configure a target case first')

def validate(observation, root):
    if not isinstance(observation, dict):
        return []
    differences = []
    if type(observation.get('vm_entries')) is not int:
        differences.append('Missing actual VM entry observation')
    else:
        syntax_error = observation.get('lua_status') == 3
        if not syntax_error and observation['vm_entries'] < 1:
            differences.append('Lua input never entered luaV_execute')
        proto = Path(root) / 'proto.txt'
        if not proto.is_file() or proto.stat().st_size != observation.get('proto_bytes'):
            differences.append('Missing or inconsistent Proto dump')
        elif not syntax_error and (not proto.read_text().startswith('P 0 parent=-1 ')):
            differences.append('Missing parsed root Proto')
    logs = ''.join(((Path(root) / 'execute' / name).read_text(errors='replace') for name in ('stdout.log', 'stderr.log')))
    if any((marker in logs for marker in ('UnsupportedOperationException', 'Invalid memory read', 'Invalid memory write', ' exception sp=', 'handleInterrupt intno='))):
        differences.append('unidbg reported an emulation/JNI failure; see execute logs')
    return differences
