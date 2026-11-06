"""HTTP API + static files; no heavyweight web framework required."""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import BoundedSemaphore
from urllib.parse import urlparse

from .engine import ParkingEngine, ParkingError
from .ocr import recognize

BASE = Path(__file__).resolve().parent.parent
WEB = BASE / 'web'
MAX_REQUEST = 10_500_000
OCR_LOCK = BoundedSemaphore(1)  # Protect the memory-limited Render Free instance


def make_handler(engine):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'ParkTrace/1.0'

        def _send(self, status, content, content_type='application/json; charset=utf-8'):
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(content)))
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(content)

        def _json(self, status, data):
            self._send(status, json.dumps(data, ensure_ascii=False).encode('utf-8'))

        def do_GET(self):
            route = urlparse(self.path).path
            if route == '/api/state':
                return self._json(200, engine.state())
            if route == '/healthz':
                return self._json(200, {'ok': True})
            if route in ('/', '/index.html'):
                target, content_type = WEB / 'index.html', 'text/html; charset=utf-8'
            elif route == '/styles.css':
                target, content_type = WEB / 'styles.css', 'text/css; charset=utf-8'
            elif route == '/app.js':
                target, content_type = WEB / 'app.js', 'text/javascript; charset=utf-8'
            else:
                return self._json(404, {'error': '找不到頁面'})
            try:
                self._send(200, target.read_bytes(), content_type)
            except OSError:
                self._json(500, {'error': '靜態檔案遺失'})

        def do_POST(self):
            route = urlparse(self.path).path
            operations = {
                '/api/entry', '/api/exit', '/api/cancel-wait',
                '/api/observe', '/api/advance', '/api/reset', '/api/ocr',
            }
            if route not in operations:
                return self._json(404, {'error': '未知操作'})
            try:
                size = int(self.headers.get('Content-Length', 0))
                if size <= 0 or size > MAX_REQUEST:
                    return self._json(413, {'error': '要求內容過大或為空'})
                payload = json.loads(self.rfile.read(size))
                if not isinstance(payload, dict):
                    raise ValueError('必須提供 JSON 物件')
                if route == '/api/entry':
                    result = engine.entry(payload.get('plate'), payload.get('vehicle_type', 'normal'))
                elif route == '/api/exit':
                    result = engine.exit(payload.get('plate'))
                elif route == '/api/cancel-wait':
                    result = engine.cancel_wait(payload.get('plate'))
                elif route == '/api/observe':
                    result = engine.observe(payload.get('spot'), payload.get('plate', ''))
                elif route == '/api/advance':
                    result = engine.advance(payload.get('minutes'))
                elif route == '/api/reset':
                    result = engine.reset()
                else:
                    if not OCR_LOCK.acquire(blocking=False):
                        return self._json(503, {'error': '其他照片正在辨識，請稍後再試'})
                    try:
                        result = recognize(payload.get('image'), images=payload.get('images'))
                    finally:
                        OCR_LOCK.release()
                self._json(200, result)
            except (ParkingError, ValueError, TypeError, json.JSONDecodeError) as exc:
                self._json(400, {'error': str(exc)})
            except RuntimeError as exc:
                self._json(503, {'error': str(exc)})
            except Exception as exc:
                # Keep tracebacks in terminal, never leak SQL/server details to clients.
                import traceback
                traceback.print_exc()
                self._json(500, {'error': '伺服器發生錯誤，請查看終端機日誌'})

    return Handler


def run():
    port = int(os.getenv('PORT', '8000'))
    engine = ParkingEngine()
    server = ThreadingHTTPServer(('0.0.0.0', port), make_handler(engine))
    print(f'ParkTrace Twin — http://localhost:{port}', flush=True)
    server.serve_forever()


if __name__ == '__main__':
    run()
