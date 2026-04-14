import uvicorn

from server.app import create_app, ASGILogger

# Wrap with ASGI logger to catch ALL incoming connections (including WebSocket)
app = ASGILogger(create_app())

if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
