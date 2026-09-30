import os
import sys

import uvicorn

from .settings import API_PORT, HOST

if __name__ == "__main__":
    # --reload: restart on code changes (development)
    dev = "--reload" in sys.argv
    if dev:
        # file-change events don't arrive reliably on every drive here; polling always works
        os.environ.setdefault("WATCHFILES_FORCE_POLLING", "true")
    uvicorn.run("api.main:app", host=HOST, port=API_PORT, reload=dev,
                reload_dirs=["api", "autobot"] if dev else None)
