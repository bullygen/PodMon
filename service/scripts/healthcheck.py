"""Container healthcheck; runs only inside the monitoring container."""
from urllib.request import urlopen

with urlopen('http://127.0.0.1:8080/api/health', timeout=2) as response:
    if response.status != 200 or b'"ok"' not in response.read(256):
        raise SystemExit(1)
