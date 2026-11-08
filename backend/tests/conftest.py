"""Shared test setup: never touch a real progress database."""

import os

# Set before the app module is imported anywhere, so ``app = create_app()`` and any
# default store use a throwaway in-memory database.
os.environ["POKERLAB_DB"] = ":memory:"
