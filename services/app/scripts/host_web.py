"""host 화면 중계(EV-001 TB-14 · CH-36, 시험 전용 — verify-e2e · 평가 host 실행): infra/nginx/nginx.conf와 같은 일을 표준 라이브러리로.

  python3 scripts/host_web.py <tech 포트> <lead 포트> <앱 서버 http://127.0.0.1:port> <저장소 루트>
- 정비자 앱 = apps/tech/dist, 파트장 앱 = apps/lead/dist(빌드된 것) — 없는 경로는 index.html(try_files)
- /api/... 는 앱 서버로 넘기고 X-App(tech | lead)을 덮어씀 · Host · X-Request-Id(nginx와 같은 칸)
- 127.0.0.1에만 열림. 응답 · 쿠키는 그대로 돌려줌.
"""
import http.client
import mimetypes
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

HOP = {"connection", "keep-alive", "transfer-encoding", "upgrade", "proxy-connection", "te", "trailer"}


def handler(app: str, root: Path, upstream: str):
    up = urlsplit(upstream)

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _proxy(self):
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n) if n else None
            headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP | {"x-app", "host"}}
            headers |= {"X-App": app, "Host": self.headers.get("Host", ""), "X-Request-Id": uuid.uuid4().hex}
            conn = http.client.HTTPConnection(up.hostname, up.port, timeout=600)
            try:
                conn.request(self.command, self.path, body=body, headers=headers)
                r = conn.getresponse()
                data = r.read()
            except OSError:
                return self._send(502, b"bad gateway", "text/plain")
            self.send_response(r.status)
            for k, v in r.getheaders():
                if k.lower() not in HOP | {"content-length"}:
                    self.send_header(k, v)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _send(self, status, data, ctype):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _static(self):
            path = urlsplit(self.path).path
            f = (root / path.lstrip("/")).resolve()
            if not str(f).startswith(str(root.resolve())) or not f.is_file():
                f = root / "index.html"  # try_files $uri /index.html
            self._send(200, f.read_bytes(), mimetypes.guess_type(f.name)[0] or "application/octet-stream")

        def do_GET(self):
            self._proxy() if self.path.startswith("/api/") else self._static()

        def do_POST(self):
            self._proxy() if self.path.startswith("/api/") else self._send(405, b"", "text/plain")

        do_PUT = do_DELETE = do_PATCH = do_POST

    return H


if __name__ == "__main__":
    tech_port, lead_port, upstream, repo = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3], Path(sys.argv[4])
    servers = [
        ThreadingHTTPServer(("127.0.0.1", tech_port), handler("tech", repo / "apps/tech/dist", upstream)),
        ThreadingHTTPServer(("127.0.0.1", lead_port), handler("lead", repo / "apps/lead/dist", upstream)),
    ]
    for s in servers[1:]:
        threading.Thread(target=s.serve_forever, daemon=True).start()
    print(f"host_web tech 127.0.0.1:{tech_port} lead 127.0.0.1:{lead_port} → {upstream}", flush=True)
    servers[0].serve_forever()
