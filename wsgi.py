import os

from app import create_app

app = create_app()

if __name__ == "__main__":
    is_dev = app.config.get("APP_ENV") == "development"
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "5000"))
    app.run(host=host, port=port, debug=is_dev)
