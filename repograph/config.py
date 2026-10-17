"""Configuration loaded from the .env file."""

import os

from dotenv import load_dotenv

load_dotenv()

FALKOR_HOST = os.environ["FALKOR_HOST"]
FALKOR_PORT = int(os.environ["FALKOR_PORT"])
FALKOR_USERNAME = os.environ.get("FALKOR_USERNAME") or None
FALKOR_PASSWORD = os.environ.get("FALKOR_PASSWORD") or None
# Optional: only ingestion and keyword search need a local Oppia clone.
OPPIA_REPO = os.environ["OPPIA_REPO"]
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")

GRAPH_NAME = "repograph"
# Branch read from the Oppia clone: the official repository, not a fork,
# and never the checked-out working branch.
OPPIA_BRANCH = "upstream/develop"
# Only history from this date onward is loaded.
HISTORY_SINCE = "2022-01-01"
