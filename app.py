"""Stable ASGI entry point; assembly lives in collector.bootstrap."""
from collector.bootstrap import create_app

app = create_app(remote=False)
