"""
Entry point:
    uvicorn main:app --reload
    python main.py
    this is just a test
"""
from app.main import app

__all__ = ["app"]

if __name__ == "__main__":
    import os
    import uvicorn

    port = int(os.getenv("PORT", "8000"))
    host = os.getenv("HOST", "0.0.0.0")
    is_dev = os.getenv("ENV", "development").lower() in ("dev", "development", "local")
    uvicorn.run(
        "main:app",
        host=host,
        port=port,
        reload=is_dev,
        log_level="info",
        limit_max_requests=None if is_dev else 1000,
    )
