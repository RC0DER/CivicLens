"""Gunicorn running uvicorn workers.

The access log is disabled here and served by the application instead, because
gunicorn's access log records the client address on every line - including on
the intake route, where that is exactly the record this platform must not
create.
"""

import multiprocessing
import os

# PaaS platforms (Railway, Render, Fly) assign the port at runtime.
bind = "0.0.0.0:" + os.getenv("PORT", "8000")
worker_class = "uvicorn.workers.UvicornWorker"
workers = int(os.getenv("WEB_CONCURRENCY", min(multiprocessing.cpu_count() * 2 + 1, 8)))

timeout = 30
graceful_timeout = 30
keepalive = 5
max_requests = 2000            # recycle workers to bound memory growth
max_requests_jitter = 200

accesslog = None               # see module docstring
errorlog = "-"
loglevel = os.getenv("LOG_LEVEL", "info")

forwarded_allow_ips = os.getenv("FORWARDED_ALLOW_IPS", "*")
proxy_protocol = False
