"""Fixed sample checker; independent of editable Java harness and LightRE core."""
import hashlib
from pathlib import Path

def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

DG_NATIVE_SIGNATURES = (
    'entryHelperNative(J)V',
    'initNative(Landroid/content/Context;Ljava/lang/String;[BLjava/lang/Object;Landroid/os/Bundle;II)J',
    'ssNative(J[Ljava/lang/String;Landroid/os/Bundle;)[B',
    'heNative(JLjava/util/Map;)V', 'closeNative(J[Ljava/lang/String;)V',
    'xssNative(J[Ljava/lang/String;Ljava/util/Map;)[B', 'lcsNative(J)I',
)

def normalize(request, base=None):
    request = dict(request)
    allowed = {'input_file', 'input_hex', 'stage', 'vm_entry_rva',
               'runtime_gr', 'runtime_i', 'runtime_j', 'runtime_h', 'runtime_m', 'runtime_k',
               'monotonic_nanos', 'wall_time_millis', 'random_seed', 'environment_profile'}
    if set(request) - allowed:
        raise ValueError(f'Unknown dg parameters: {set(request) - allowed}')
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
    if request.get('stage') != 'run':
        raise ValueError('DG verification requires stage=run, not load-only')
    if not isinstance(request.get('vm_entry_rva'), str) or not 0 < int(request['vm_entry_rva'], 0) < 2 ** 32:
        raise ValueError('DG requires the version-specific vm_entry_rva')
    if request.get('environment_profile') != 'headless-v1':
        raise ValueError('DG requires explicit environment_profile=headless-v1')
    for key in ('runtime_h', 'runtime_m', 'runtime_k'):
        if not isinstance(request.get(key), str) or '\x00' in request[key] or len(request[key].encode()) > 4096:
            raise ValueError(f'{key} must be a string without NUL (up to 4096 bytes)')
    for key in ('runtime_gr', 'runtime_i', 'runtime_j', 'monotonic_nanos', 'wall_time_millis', 'random_seed'):
        if type(request.get(key)) is not int or not -2 ** 63 <= request[key] < 2 ** 63:
            raise ValueError(f'{key} must be an explicit int64 environment response')
    if (not -(2**31) <= request['runtime_gr'] < 2**31
            or min(request['monotonic_nanos'], request['wall_time_millis']) < 0):
        raise ValueError('runtime_gr must fit int32 and fixture clocks must be nonnegative')
    return {**request, 'input_file': 'input.bin'}, data, origin

def expected(normalized, data, rows):
    """Only an exact saved input/parameter contract earns a passing assertion."""
    sha = hashlib.sha256(data).hexdigest()
    for row in rows:
        parameters = {k: v for k, v in row['request'].items() if k not in ('input_file', 'input_hex')}
        if parameters == {k: v for k, v in normalized.items() if k != 'input_file'} and row['input_sha256'] == sha:
            result = row['expected']
            if not isinstance(result, dict):
                raise ValueError('Target expected result must be an object')
            required = {'loaded': True, 'stage': 'run', 'handle_nonzero': True, 'closed': True,
                        'missing_jni': [], 'environment_profile': 'headless-v1'}
            if any(key not in result or type(result[key]) is not type(value) or result[key] != value
                   for key, value in required.items()):
                raise ValueError('DG requires successful init/ss/close with no missing JNI in headless-v1')
            return result
    raise ValueError('No expected result for this input/parameter combination; configure a target case first')

def validate(observation, root):
    if not isinstance(observation, dict):
        return []
    differences = []
    if type(observation.get('vm_entries')) is not int:
        differences.append('Missing actual VM entry observation')
    elif observation['vm_entries'] < 1:
        differences.append('DG core VM was never entered; loading/JNI output alone is insufficient')
    output = observation.get('output_hex')
    try:
        data = bytes.fromhex(output)
        output_file = Path(root) / 'output.bin'
        if not output_file.is_file() or output_file.read_bytes() != data:
            differences.append('Missing or inconsistent DG output.bin')
        if len(data) != observation.get('length') or not data:
            differences.append('Invalid DG byte-array output length')
    except (TypeError, ValueError):
        differences.append('Invalid DG output_hex')
    for phase in ('init_vm_entries', 'ss_vm_entries'):
        if type(observation.get(phase)) is not int or observation[phase] < 1:
            differences.append(f'DG core VM was not observed during {phase}')
    if set(observation.get('registrations', {})) != set(DG_NATIVE_SIGNATURES):
        differences.append('Expected seven dynamically observed DG JNI registrations')
    # unidbg can report an emulation error and still return from JNI.
    logs = ''.join((Path(root) / 'execute' / name).read_text(errors='replace')
                   for name in ('stdout.log', 'stderr.log'))
    if any(marker in logs for marker in ('UnsupportedOperationException', 'Invalid memory read',
                                         'Invalid memory write', ' exception sp=', 'handleInterrupt intno=')):
        differences.append('unidbg reported an emulation/JNI failure; see execute logs')
    return differences
