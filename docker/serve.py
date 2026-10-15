"""The lab inside the container: the same FastAPI app as `python -m api`, with the built web pages, on all of the
container's interfaces at $PORT (the container's port is published to the server only; see docker-compose.yml).
On a PC the lab keeps listening on 127.0.0.1 only (api/__main__.py)."""
import os
import sys
from pathlib import Path

import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if __name__ == "__main__":
    uvicorn.run("api.main:app", host="0.0.0.0", port=int(os.environ.get("PORT", "8080")), timeout_graceful_shutdown=2)
