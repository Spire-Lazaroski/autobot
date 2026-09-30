import uvicorn

from .settings import API_PORT, HOST

if __name__ == "__main__":
    # No --reload: uvicorn's reloader hangs on this machine; restart the API after backend edits.
    uvicorn.run("api.main:app", host=HOST, port=API_PORT, timeout_graceful_shutdown=2)
