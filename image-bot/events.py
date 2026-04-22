"""JSON event system for structured logging and external tool integration."""

import json
import time


def emit(event: str, **kwargs) -> None:
    """Print a structured JSON event to stdout.

    Example output:
        {"event": "job_started", "ts": 1719000000.123, "row": 5}
    """
    payload = {"event": event, "ts": time.time(), **kwargs}
    print(json.dumps(payload, ensure_ascii=False), flush=True)
