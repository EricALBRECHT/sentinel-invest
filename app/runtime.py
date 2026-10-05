"""Clock for this API process. Importing this module records the start time."""

from datetime import datetime, timezone

STARTED_AT = datetime.now(timezone.utc)
