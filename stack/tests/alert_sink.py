"""Local acceptance receiver; bounded in-memory history, no external delivery."""
from collections import deque
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import threading
import time

events = deque(maxlen=200)
lock = threading.Lock()

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def reply(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def authorized(self):
        token = os.environ.get('ALERT_WEBHOOK_TOKEN', '')
        return bool(token) and hmac.compare_digest(
            self.headers.get('Authorization', ''), 'Bearer ' + token)

    def do_GET(self):
        if self.path == '/health':
            return self.reply(200, {'status': 'ok'})
        if not self.authorized():
            return self.reply(403, {'error': 'forbidden'})
        if self.path != '/events':
            return self.reply(404, {'error': 'not found'})
        with lock:
            snapshot = list(events)
        self.reply(200, snapshot)

    def do_POST(self):
        if not self.authorized():
            return self.reply(403, {'error': 'forbidden'})
        if self.path != '/webhook':
            return self.reply(404, {'error': 'not found'})
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 1_048_576:
                return self.reply(413, {'error': 'invalid size'})
            payload = json.loads(self.rfile.read(size))
            if not isinstance(payload, dict) or not isinstance(payload.get('alerts'), list):
                raise ValueError('invalid alert payload')
        except (ValueError, UnicodeError):
            return self.reply(400, {'error': 'invalid JSON payload'})
        with lock:
            events.append({'received_at': time.time(), 'payload': payload})
        self.reply(200, {'received': True})

if __name__ == '__main__':
    ThreadingHTTPServer(('0.0.0.0', 8080), Handler).serve_forever()
