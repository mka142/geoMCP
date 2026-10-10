"""Download links for full result tables.

HTTP mode: `/files/<name>?exp=<unix>&sig=<hmac>` links, signed with a per-installation secret and valid for
24 hours, so tables can be fetched without the API key (for example by a browser or a spreadsheet).
stdio mode: local file paths, since client and server share the machine.
"""

import hashlib
import hmac
import re
import secrets
import time
from pathlib import Path

from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response

from geomcp_core.files import files_dir

TTL_S = 24 * 3600
_NAME = re.compile(r"^[a-z0-9_-]+-[0-9a-f]{32}\.(csv|parquet)$")
MEDIA = {"csv": "text/csv; charset=utf-8", "parquet": "application/vnd.apache.parquet"}


def _secret(data_dir: Path) -> bytes:
    path = data_dir / "download-secret.key"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(secrets.token_hex(32))
        path.chmod(0o600)
    return bytes.fromhex(path.read_text().strip())


def _sig(secret: bytes, name: str, exp: int) -> str:
    return hmac.new(secret, f"{name}:{exp}".encode(), hashlib.sha256).hexdigest()[:32]


class Downloads:
    def __init__(self, data_dir: Path, public_url: str | None) -> None:
        self.public_url = public_url.rstrip("/") if public_url else None
        self.secret = _secret(data_dir)

    def link(self, path: Path) -> str:
        if self.public_url is None:
            return str(path)
        exp = int(time.time()) + TTL_S
        return f"{self.public_url}/files/{path.name}?exp={exp}&sig={_sig(self.secret, path.name, exp)}"

    async def serve(self, request: Request) -> Response:
        name = request.path_params["name"]
        try:
            exp = int(request.query_params.get("exp", "0"))
        except ValueError:
            exp = 0
        sig = request.query_params.get("sig", "")
        if not _NAME.match(name) or not hmac.compare_digest(sig, _sig(self.secret, name, exp)):
            return JSONResponse({"error": "invalid link"}, status_code=403)
        if exp < time.time():
            return JSONResponse({"error": "link expired; call the tool again for a fresh link"}, status_code=410)
        path = files_dir() / name
        if not path.exists():
            return JSONResponse({"error": "file no longer exists"}, status_code=404)
        return FileResponse(path, media_type=MEDIA[name.rsplit(".", 1)[1]], filename=name)
