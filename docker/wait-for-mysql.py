import os
import socket
import sys
import time


host = os.getenv("MYSQL_HOST", "127.0.0.1")
port = int(os.getenv("MYSQL_PORT", "3306"))

for attempt in range(30):
    try:
        with socket.create_connection((host, port), timeout=2):
            sys.exit(0)
    except Exception as exc:
        print(
            f"[startup] Waiting for MySQL at {host}:{port} "
            f"(attempt {attempt + 1}/30): {exc}",
            flush=True,
        )
        time.sleep(2)

print(f"[startup] MySQL did not become available at {host}:{port}.", flush=True)
sys.exit(1)
