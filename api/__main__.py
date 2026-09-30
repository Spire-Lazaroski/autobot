import uvicorn

from .settings import API_PORT, HOST

if __name__ == "__main__":
    uvicorn.run("api.main:app", host=HOST, port=API_PORT, reload=False)
