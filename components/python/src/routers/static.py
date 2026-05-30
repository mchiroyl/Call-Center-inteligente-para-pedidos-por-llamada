"""Static SPA routes."""

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, RedirectResponse
from starlette.staticfiles import StaticFiles

from security import get_staff_user_from_request
from settings import STATIC_DIR

router = APIRouter()


@router.get("/kitchen", response_class=FileResponse)
@router.get("/operations", response_class=FileResponse)
async def spa_routes(request: Request):
    user = get_staff_user_from_request(request)
    if not user:
        return RedirectResponse(url="/login?next=/operations", status_code=303)
    return FileResponse(STATIC_DIR / "index.html")


class NoCacheStaticFiles(StaticFiles):
    def is_not_modified(self, response_headers, request_headers):
        return False

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = (
            "no-store, no-cache, must-revalidate, max-age=0"
        )
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response

