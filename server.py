import base64
import binascii
from contextlib import closing
import json
import mimetypes
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parent
DATABASE_DIR = ROOT / "data"
DATABASE_PATH = DATABASE_DIR / "applications.sqlite3"
MAX_REQUEST_SIZE = 12 * 1024 * 1024
MAX_PHOTO_SIZE = 5 * 1024 * 1024
PHOTO_PATTERN = re.compile(
    r"data:(image/(?:jpeg|png|webp|gif));base64,([A-Za-z0-9+/]*={0,2})"
)


def initialize_database():
    DATABASE_DIR.mkdir(exist_ok=True)
    with closing(sqlite3.connect(DATABASE_PATH)) as connection:
        with connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS applications (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    details_json TEXT NOT NULL
                )
                """
            )


class ApplicationRequestHandler(BaseHTTPRequestHandler):
    def is_local_origin(self, origin):
        try:
            parsed_origin = urlsplit(origin)
            port = parsed_origin.port
        except ValueError:
            return False
        return (
            parsed_origin.scheme == "http"
            and parsed_origin.hostname in ("localhost", "127.0.0.1")
            and port is not None
            and not parsed_origin.username
            and not parsed_origin.password
            and not parsed_origin.path
            and not parsed_origin.query
            and not parsed_origin.fragment
        )

    def end_headers(self):
        origin = self.headers.get("Origin")
        if origin and self.is_local_origin(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        super().end_headers()

    def send_json(self, status_code, response):
        body = json.dumps(response).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def send_error_response(self, status_code, message):
        self.send_json(status_code, {"error": message})

    def do_OPTIONS(self):
        origin = self.headers.get("Origin", "")
        if (
            urlsplit(self.path).path != "/api/applications"
            or not self.is_local_origin(origin)
        ):
            self.send_error_response(403, "Cross-origin request is not allowed.")
            return

        self.send_response(204)
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "600")
        self.end_headers()

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/api/health":
            self.send_json(200, {"status": "ok"})
            return
        if path == "/api/applications":
            body = json.dumps(
                {"error": "Use POST to save an application."}
            ).encode("utf-8")
            self.send_response(405)
            self.send_header("Allow", "POST")
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path in ("/", "/index.html"):
            file_path = ROOT / "index.html"
        elif path.startswith("/images/"):
            relative_path = Path(unquote(path).lstrip("/"))
            if ".." in relative_path.parts:
                self.send_error_response(404, "Not found.")
                return
            file_path = (ROOT / relative_path).resolve()
            if ROOT / "images" not in file_path.parents:
                self.send_error_response(404, "Not found.")
                return
        else:
            self.send_error_response(404, "Not found.")
            return

        if not file_path.is_file():
            self.send_error_response(404, "Not found.")
            return

        body = file_path.read_bytes()
        content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if urlsplit(self.path).path != "/api/applications":
            self.send_error_response(404, "Not found.")
            return

        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip()
        if content_type != "application/json":
            self.send_error_response(415, "Content-Type must be application/json.")
            return

        try:
            content_length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self.send_error_response(400, "A valid Content-Length header is required.")
            return

        if content_length < 0:
            self.send_error_response(400, "Content-Length cannot be negative.")
            return
        if content_length > MAX_REQUEST_SIZE:
            self.send_error_response(413, "The application is too large to save.")
            return

        try:
            details = json.loads(self.rfile.read(content_length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.send_error_response(400, "The request body must be valid JSON.")
            return

        if not isinstance(details, dict) or not all(
            isinstance(key, str) for key in details
        ):
            self.send_error_response(400, "The application must be a JSON object.")
            return

        if not all(
            isinstance(value, (str, list, dict)) for value in details.values()
        ):
            self.send_error_response(400, "Application fields have an invalid format.")
            return

        photo = details.get("passport-photo")
        if photo is not None:
            if not isinstance(photo, dict) or not isinstance(
                photo.get("dataUrl"), str
            ):
                self.send_error_response(400, "The passport photo has an invalid format.")
                return
            match = PHOTO_PATTERN.fullmatch(photo["dataUrl"])
            if not match or match.group(1) != photo.get("type"):
                self.send_error_response(
                    400, "Use a JPEG, PNG, WebP, or GIF passport photo."
                )
                return
            try:
                photo_bytes = base64.b64decode(match.group(2), validate=True)
            except (binascii.Error, ValueError):
                self.send_error_response(400, "The passport photo data is invalid.")
                return
            if len(photo_bytes) > MAX_PHOTO_SIZE or photo.get("size") != len(
                photo_bytes
            ):
                self.send_error_response(
                    400, "The passport photo must be 5 MB or smaller."
                )
                return

        application_id = str(uuid.uuid4())
        created_at = datetime.now(timezone.utc).isoformat()
        try:
            with closing(sqlite3.connect(DATABASE_PATH, timeout=10)) as connection:
                with connection:
                    connection.execute(
                        """
                        INSERT INTO applications (id, created_at, details_json)
                        VALUES (?, ?, ?)
                        """,
                        (
                            application_id,
                            created_at,
                            json.dumps(
                                details, separators=(",", ":"), ensure_ascii=False
                            ),
                        ),
                    )
        except sqlite3.Error:
            self.log_error("Could not save an application to the SQLite database.")
            self.send_error_response(500, "The application could not be saved.")
            return

        self.send_json(
            201,
            {"applicationId": application_id, "createdAt": created_at},
        )


if __name__ == "__main__":
    initialize_database()
    server = ThreadingHTTPServer(("127.0.0.1", 8000), ApplicationRequestHandler)
    print("Application form server running at http://127.0.0.1:8000")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping application form server.")
    finally:
        server.server_close()
