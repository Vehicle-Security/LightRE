"""REST application for external systems, with the built-in UI mounted separately."""

from fastapi import FastAPI

from .routes import router_for


def create_app(reader, *, include_ui=True, start_worker=True, status=None):
    app = FastAPI(title='LightRE REST API')
    app.include_router(router_for(reader, status=status))
    if include_ui:
        from webui.app import create_app as create_ui_app
        ui_app = create_ui_app(reader, start_worker=start_worker, status=status)
        app.mount('/ui', ui_app)
        # Keep the root UI after the REST routes, so it cannot hide /api or /docs.
        app.mount('/', ui_app)
    return app
