"""Independent oracle for the fixed development fixture."""
import hashlib
from pathlib import Path

def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def normalize(request, base=None):
    request = dict(request)
    allowed = {'a', 'b', 'key', 'input_hex', 'input_file', 'capacity'}
    if set(request) - allowed:
        raise ValueError(f'Unknown native-smoke parameters: {set(request) - allowed}')
    for key, low, high in [('a', -2**31, 2**31-1), ('b', -2**31, 2**31-1), ('key', 0, 255)]:
        value = request.get(key)
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f'{key} must be an integer in [{low}, {high}]')
    origin = None
    if ('input_hex' in request) == ('input_file' in request):
        raise ValueError('Provide exactly one of input_hex or input_file')
    if 'input_file' in request:
        path = (Path(base or Path.cwd()) / request['input_file']).resolve()
        data = path.read_bytes()
        origin = {'path': str(path), 'sha256': digest(path)}
    else:
        data = bytes.fromhex(request['input_hex'])
    capacity = request.get('capacity', len(data))
    if len(data) > 1024*1024 or type(capacity) is not int or not 0 <= capacity <= 1024*1024:
        raise ValueError('Input and capacity are limited to 1 MiB for native-smoke')
    normalized = {key: request[key] for key in ('a', 'b', 'key')}
    return {**normalized, 'input_file': 'input.bin', 'capacity': capacity}, data, origin


def expected(request, data, rows):
    length = len(data) if request['capacity'] >= len(data) else -1
    output = bytes(value ^ ((request['key'] + index) & 255) for index, value in enumerate(data))
    return {'integer': request['a']*3-request['b'], 'length': length,
            'output_hex': output.hex() if length >= 0 else ''}


def validate(observation, root):
    return []
