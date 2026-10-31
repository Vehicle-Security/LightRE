"""HTTP endpoints for the function browser."""

from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse


def create_app(reader):
    app = FastAPI(title="LightRE")

    @app.middleware("http")
    async def fresh_responses(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/", include_in_schema=False)
    def page():
        return FileResponse(Path(__file__).with_name("index.html"))

    @app.get("/api/functions")
    def functions():
        return reader.catalog()

    @app.get("/api/functions/{rva}/assembly")
    def assembly(rva: str, offset: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=200)):
        try:
            return reader.assembly(int(rva, 0), offset, limit)
        except KeyError as error:
            raise HTTPException(404, str(error)) from error
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
        except Exception as error:
            raise HTTPException(502, f"汇编读取失败：{error}") from error

    return app
