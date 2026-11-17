"""Generate local-only credentials without printing them or replacing existing ones."""
import base64
import os
from pathlib import Path
import secrets

password = secrets.token_hex(24)
values = {
    "OTLP_HTPASSWD": "demo:" + password,
    "OTLP_AUTH": "Basic " + base64.b64encode(("demo:" + password).encode()).decode(),
    "GF_SECURITY_ADMIN_PASSWORD": secrets.token_hex(24),
    "DEMO_TEST_TOKEN": secrets.token_hex(24),
}
try:
    fd = os.open('.env', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
except FileExistsError:
    print('.env already exists; preserved existing credentials.')
else:
    with os.fdopen(fd, 'w') as f:
        f.write(''.join(f'{key}={value}\n' for key,value in values.items()))
    print('Created .env with private local credentials.')
