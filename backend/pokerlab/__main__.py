"""Run the API server: ``python -m pokerlab``."""

import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "pokerlab.api.app:app",
        host=os.getenv("POKERLAB_HOST", "127.0.0.1"),
        port=int(os.getenv("POKERLAB_PORT", "8000")),
    )


if __name__ == "__main__":
    main()
