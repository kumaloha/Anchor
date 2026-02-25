#!/usr/bin/env python3
"""
Convenience runner for the Anchor backend.

Usage:
    python run.py              # Start the FastAPI server
    python run.py worker       # Start a Celery worker
    python run.py beat         # Start the Celery beat scheduler
    python run.py migrate      # Run Alembic migrations
"""

import subprocess
import sys


def main() -> None:
    args = sys.argv[1:]

    if not args or args[0] == "server":
        print("Starting Anchor FastAPI server on http://0.0.0.0:8000")
        subprocess.run(
            ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"],
            check=True,
        )

    elif args[0] == "worker":
        print("Starting Celery worker...")
        subprocess.run(
            [
                "celery",
                "-A",
                "app.core.celery_app.celery_app",
                "worker",
                "--loglevel=info",
                "--concurrency=2",
            ],
            check=True,
        )

    elif args[0] == "beat":
        print("Starting Celery beat scheduler...")
        subprocess.run(
            [
                "celery",
                "-A",
                "app.core.celery_app.celery_app",
                "beat",
                "--loglevel=info",
            ],
            check=True,
        )

    elif args[0] == "migrate":
        print("Running Alembic migrations...")
        subprocess.run(["alembic", "upgrade", "head"], check=True)

    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
