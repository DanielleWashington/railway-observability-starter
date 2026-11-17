"""Temporary external webhook fixture with separate write/read credentials."""
from collections import deque
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import threading
import time

EVENTS = deque(maxlen=50)
LOCK = threading.Lock()
MAX_BODY = 65_536


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def reply(self, status, value):
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def authorized(self, variable):
        token = os.environ.get(variable, '')
        return bool(token) and hmac.compare_digest(
            self.headers.get('Authorization', ''), 'Bearer ' + token)

    def do_GET(self):
        if self.path == '/health':
            return self.reply(200, {'status': 'ok'})
        if not self.authorized('RECEIVER_READ_TOKEN'):
            return self.reply(403, {'error': 'forbidden'})
        if self.path != '/events':
            return self.reply(404, {'error': 'not found'})
        with LOCK:
            events = list(EVENTS)
        self.reply(200, events)

    def do_POST(self):
        if not self.authorized('ALERT_WEBHOOK_TOKEN'):
            return self.reply(403, {'error': 'forbidden'})
        if self.path != '/webhook':
            return self.reply(404, {'error': 'not found'})
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= MAX_BODY:
                return self.reply(413, {'error': 'invalid size'})
            payload = json.loads(self.rfile.read(size))
            if (not isinstance(payload, dict) or not isinstance(payload.get('alerts'), list)
                    or not 1 <= len(payload['alerts']) <= 20
                    or any(not isinstance(a, dict) or a.get('status') not in ('firing', 'resolved')
                           for a in payload['alerts'])):
                raise ValueError('invalid alert payload')
        except (ValueError, UnicodeError):
            return self.reply(400, {'error': 'invalid JSON payload'})
        event = {'received_at': time.time(), 'payload': payload,
                 'transport': {'host': self.headers.get('Host', '')[:255],
                               'forwarded_proto': self.headers.get('X-Forwarded-Proto', '')[:20],
                               'user_agent': self.headers.get('User-Agent', '')[:255]},
                 'bearer_authenticated': True}
        with LOCK:
            EVENTS.append(event)
        print(json.dumps({'event': 'webhook_received', 'status': payload.get('status'),
                          'alert_count': len(payload['alerts']), 'received_at': event['received_at']}), flush=True)
        self.reply(200, {'received': True})


if __name__ == '__main__':
    assert os.environ.get('ALERT_WEBHOOK_TOKEN'), 'Missing write token'
    assert os.environ.get('RECEIVER_READ_TOKEN'), 'Missing read token'
    assert os.environ['ALERT_WEBHOOK_TOKEN'] != os.environ['RECEIVER_READ_TOKEN'], 'Use distinct tokens'
    ThreadingHTTPServer(('0.0.0.0', int(os.environ.get('PORT', '8080'))), Handler).serve_forever()
