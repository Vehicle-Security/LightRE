"""REST routes exposed to external LightRE clients."""

from fastapi import APIRouter, HTTPException, Response


def router_for(reader, *, status=None):
    router = APIRouter(prefix='/api')

    @router.get('/status')
    def analysis_status(response: Response):
        response.headers['Cache-Control'] = 'no-store'
        from rehost.progress import workflow_status
        return status() if status else {'ready': True, 'status': 'ready', 'phase': 'ready',
                                       'workflow': workflow_status(reader.directory)}

    @router.get('/health')
    def health():
        return {'status': 'ok', 'workspace': str(reader.directory)}

    @router.get('/workflow')
    def workflow():
        from rehost.progress import workflow_status
        return workflow_status(reader.directory)

    @router.get('/rehost')
    def rehost_progress(response: Response):
        from rehost.progress import progress
        response.headers['Cache-Control'] = 'no-store'
        return progress(reader.directory)

    @router.post('/rehost/retry', status_code=202)
    def retry_rehost():
        from lightre.main_workflow import retry_rehost
        try:
            return retry_rehost(reader)
        except (RuntimeError, ValueError, OSError) as error:
            raise HTTPException(409, str(error)) from error

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
