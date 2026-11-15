"""Distribution identity supplied by the build workflow."""
import os

__version__ = os.environ.get("SINGVERSE_VERSION") or "0.0.0"
