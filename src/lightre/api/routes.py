"""REST routes exposed to external LightRE clients."""

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Response


def router_for(reader, *, status=None):
    router = APIRouter(prefix='/api')

    @router.get('/status')
    def analysis_status(response: Response):
        response.headers['Cache-Control'] = 'no-store'
        return status() if status else {'ready': True, 'status': 'ready', 'phase': 'ready'}

    @router.get('/health')
    def health():
        return {'status': 'ok', 'workspace': str(reader.directory)}

    @router.get('/workflow')
    def workflow():
        path = Path(reader.directory) / 'main-workflow.json'
        if not path.is_file():
            return {'status': 'not_started'}
        return json.loads(path.read_text(encoding='utf-8'))

    @router.get('/workspace')
    def workspace():
        return reader.catalog()

    @router.get('/recovery')
    def recovery():
        from recovery.jobs import progress
        return progress(reader)

    @router.post('/recovery/resume')
    def resume_recovery():
        from recovery.jobs import start_task
        try:
            return start_task(reader)
        except (RuntimeError, ValueError, OSError) as error:
            raise HTTPException(409, str(error)) from error

    return router
