# run.py — local dev launcher. Run from the backend/ directory: `uv run python run.py`.
# Serves the app on 127.0.0.1:8000 with autoreload; reach it through the dev auth
# proxy on :8001 so requests carry auth headers (see README, "Running locally").
import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
        reload_dirs=["app"],
    )
