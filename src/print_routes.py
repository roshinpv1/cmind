from fastapi import FastAPI
from codemind.api.server import app

for route in app.routes:
    if hasattr(route, "methods"):
        print(f"{''.join(route.methods)} {route.path}")
