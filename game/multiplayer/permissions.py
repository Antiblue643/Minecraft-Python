
from __future__ import annotations


def debug_tools_allowed(session) -> bool:
    if session is not None and getattr(session, "mode", None) == "client":
        client = getattr(session, "client", None)
        return bool(client is not None and getattr(client, "is_operator", False))
    return True
