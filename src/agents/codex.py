"""The Codex exec transport. No recovery, validation loop or task policy."""

import json
import os
import signal
import subprocess
import time
from pathlib import Path


def process_identity(pid):
    """Linux start ticks distinguish a live owner from a reused PID."""
    try:
        fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        return fields[19] if fields[0] != 'Z' else None
    except (OSError, IndexError):
        return None


def stop_group(pid):
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 0.3
    while time.monotonic() < deadline:
        try:
            os.killpg(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.02)
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def read_events(path, cursor=0, limit=100):
    """Byte cursors advance only over complete JSONL lines."""
    if cursor < 0 or not 1 <= limit <= 1000:
        raise ValueError('cursor must be nonnegative; limit must be between 1 and 1000')
    events, errors = [], []
    try:
        with Path(path).open('rb') as stream:
            stream.seek(cursor)
            for _ in range(limit):
                line = stream.readline()
                if not line.endswith(b'\n'):
                    break
                cursor = stream.tell()
                try:
                    event = json.loads(line)
                    if not isinstance(event, dict):
                        raise ValueError('event is not an object')
                    events.append(event)
                except (ValueError, UnicodeError) as error:
                    errors.append({'end_offset': cursor, 'error': str(error)})
    except FileNotFoundError:
        pass
    return {'events': events, 'errors': errors, 'cursor': cursor}


class CodexExec:
    supports_live_messages = False

    def __init__(self, session, request, directory):
        self.session = session
        self.request = request
        self.directory = Path(directory)

    def command(self):
        options = self.session['options']
        command = [options['executable'], '-a', 'never', 'exec']
        # Exec options precede the resume subcommand. Resume does not accept
        # every exec flag (notably -C, --sandbox and -p) in installed CLI versions.
        command += ['--sandbox', options['sandbox'], '-C', self.session['cwd']]
        if options.get('profile'):
            command += ['-p', options['profile']]
        if options.get('model'):
            command += ['-m', options['model']]
        for feature in options['disabled_features']:
            command += ['--disable', feature]
        for key, value in options['overrides'].items():
            command += ['-c', f'{key}={json.dumps(value, ensure_ascii=False)}']
        if self.session['thread_id'] and options['persistent']:
            command += ['resume', self.session['thread_id']]
        elif not options['persistent']:
            command.append('--ephemeral')
        command += ['--skip-git-repo-check', '--json', '-o', str(self.directory / 'result.txt')]
        if self.request['output_schema'] is not None:
            command += ['--output-schema', str(self.directory / 'schema.json')]
        return command + ['-']

    def start(self, lease_fd):
        with (self.directory / 'prompt.md').open('rb') as prompt, \
                (self.directory / 'events.jsonl').open('wb') as events, \
                (self.directory / 'stderr.log').open('wb') as errors:
            return subprocess.Popen(self.command(), cwd=self.session['cwd'], stdin=prompt,
                                    stdout=events, stderr=errors, start_new_session=True,
                                    pass_fds=(lease_fd,))

    def check_result(self, exit_code, thread_id, turn_completed):
        try:
            text = (self.directory / 'result.txt').read_text(encoding='utf-8')
        except (OSError, UnicodeError) as error:
            return f'Final reply is missing or unreadable: {error}'
        if exit_code:
            return f'Codex exited with code {exit_code}; inspect stderr.log and events.jsonl'
        if not text.strip():
            return 'Codex exited without a nonempty final reply'
        if not turn_completed:
            return 'Codex exited without a turn.completed event'
        if self.request['output_schema'] is not None:
            try:
                json.loads(text)
            except ValueError as error:
                return f'Final reply is not valid JSON: {error}'
        if self.session['options']['persistent'] and not thread_id:
            return 'Persistent execution did not report a thread ID; cannot safely continue this session'
        return None
