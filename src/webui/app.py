"""HTTP endpoints for the function browser."""

from pathlib import Path

from pydantic import BaseModel, Field

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from recovery import read_candidate, read_knowledge
from recovery.jobs import progress, start_task, stop_task, task_state
from recovery.queue import ACTIVE, cancel, clear_finished, ensure_worker, resume as resume_queue, snapshot
from recovery.feedback import history as feedback_history
from recovery.navigation import SourceChanged, source_navigation, assembly_navigation, source_names


class Feedback(BaseModel):
    expected_sha256: str | None = None
    comment: str = Field(min_length=1, max_length=20000)


def create_app(reader, *, start_worker=True, status=None):
    app = FastAPI(title="LightRE")
    app.mount('/static', StaticFiles(directory=Path(__file__).with_name('static')), name='static')
    if start_worker:
        ensure_worker(reader)

    @app.middleware("http")
    async def fresh_responses(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/", include_in_schema=False)
    def page():
        return FileResponse(Path(__file__).with_name("index.html"))

    @app.get('/api/status')
    def analysis_status():
        return status() if status else {'ready': True, 'status': 'ready', 'phase': 'ready'}

    @app.get("/api/functions")
    def functions():
        result = reader.catalog()
        try:
            current_names = source_names(reader)
        except (OSError, ValueError) as error:
            raise HTTPException(409, str(error)) from error
        for function in result['functions']:
            function['source_name'] = current_names.get(function['rva'])
        return result

    @app.get("/api/functions/{rva}/candidate")
    def candidate(rva: str):
        try:
            address = int(rva, 0)
        except ValueError as error:
            raise HTTPException(400, "RVA 应为整数或 0x 开头的十六进制地址") from error
        try:
            for attempt in range(2):
                result = read_candidate(reader, address)
                try:
                    result.update(source_navigation(reader, result))
                    break
                except SourceChanged:
                    if attempt:
                        raise
            result["notes"] = read_knowledge(reader, address)
            return result
        except KeyError as error:
            raise HTTPException(404, str(error)) from error
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        except OSError as error:
            raise HTTPException(502, f"候选读取失败：{error}") from error

    @app.get("/api/recovery")
    def recovery_progress():
        return progress(reader)

    @app.post("/api/recovery")
    def resume():
        try:
            resume_queue(reader)
            if any(job['status'] in ACTIVE for job in snapshot(reader)):
                return task_state(reader)
            return start_task(reader)
        except (RuntimeError, ValueError, OSError) as error:
            raise HTTPException(409, str(error)) from error

    @app.post("/api/recovery/stop")
    def stop():
        try:
            return stop_task(reader)
        except (RuntimeError, OSError) as error:
            raise HTTPException(409, str(error)) from error

    @app.post('/api/functions/{rva}/recover')
    def reanalyze(rva: str):
        try:
            address = int(rva, 0)
        except ValueError as error:
            raise HTTPException(400, '无效 RVA') from error
        try:
            read_candidate(reader, address)
            # Explicit selection uses targeted mode, including existing LLM
            # candidates; ordinary resume still skips completed functions.
            return start_task(reader, [address])
        except KeyError as error:
            raise HTTPException(404, str(error)) from error
        except (RuntimeError, ValueError, OSError) as error:
            raise HTTPException(409, str(error)) from error

    @app.post('/api/tasks/clear')
    def clear_tasks():
        try:
            return clear_finished(reader)
        except (OSError, ValueError) as error:
            raise HTTPException(409, str(error)) from error

    @app.post('/api/functions/{rva}/feedback/clear')
    def clear_feedback(rva: str):
        try:
            address = int(rva, 0)
        except ValueError as error:
            raise HTTPException(400, '无效 RVA') from error
        try:
            return clear_finished(reader, comment_rva=address)
        except KeyError as error:
            raise HTTPException(404, str(error)) from error
        except (OSError, ValueError) as error:
            raise HTTPException(409, str(error)) from error

    @app.post("/api/tasks/{identifier}/cancel")
    def cancel_task(identifier: str):
        try:
            return cancel(reader, identifier)
        except KeyError as error:
            raise HTTPException(404, str(error)) from error
        except OSError as error:
            raise HTTPException(409, str(error)) from error

    @app.get("/api/functions/{rva}/feedback")
    def feedback_status(rva: str, limit: int = Query(5, ge=1, le=20)):
        try:
            address = int(rva, 0)
        except ValueError as error:
            raise HTTPException(400, '无效 RVA') from error
        try:
            return feedback_history(reader, address, limit)
        except KeyError as error:
            raise HTTPException(404, str(error)) from error
        except (OSError, ValueError) as error:
            raise HTTPException(409, str(error)) from error

    @app.post("/api/functions/{rva}/feedback")
    def feedback(rva: str, body: Feedback):
        try:
            address = int(rva, 0)
        except ValueError as error:
            raise HTTPException(400, "无效 RVA") from error
        try:
            if not body.comment.strip():
                raise HTTPException(400, "请填写修改建议")
            return start_task(reader, [address], body.comment.strip(), body.expected_sha256)
        except KeyError as error:
            raise HTTPException(404, str(error)) from error
        except (RuntimeError, ValueError, OSError) as error:
            raise HTTPException(409, str(error)) from error

    @app.get("/api/functions/{rva}/assembly")
    def assembly(rva: str, offset: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=200)):
        try:
            return assembly_navigation(reader, reader.assembly(int(rva, 0), offset, limit))
        except KeyError as error:
            raise HTTPException(404, str(error)) from error
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
        except Exception as error:
            raise HTTPException(502, f"汇编读取失败：{error}") from error

    return app
