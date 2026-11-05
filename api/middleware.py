"""Limit request bodies before multipart parsing can spool an unbounded upload."""

from fastapi import HTTPException
from starlette.responses import JSONResponse

from api.config import get_settings


class BodyLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in {"POST", "PUT", "PATCH"}:
            return await self.app(scope, receive, send)
        # Allow multipart boundaries and field headers in addition to file data.
        limit = get_settings().max_batch_bytes + 2 * 1024 * 1024
        headers = dict(scope.get("headers", []))
        try:
            declared = int(headers.get(b"content-length", b"0"))
        except ValueError:
            return await JSONResponse({"detail": "Некорректный Content-Length."}, status_code=400)(scope, receive, send)
        if declared < 0:
            return await JSONResponse({"detail": "Некорректный Content-Length."}, status_code=400)(scope, receive, send)
        if declared > limit:
            return await JSONResponse({"detail": "Превышен размер HTTP-запроса."}, status_code=413)(
                scope, receive, send
            )
        received = 0

        async def bounded_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise HTTPException(413, "Превышен размер HTTP-запроса.")
            return message

        await self.app(scope, bounded_receive, send)
