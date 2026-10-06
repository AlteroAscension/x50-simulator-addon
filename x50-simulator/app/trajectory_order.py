"""HA snapshot ordering: completion wins, then observation time."""


def snapshot_is_newer(incoming, current):
    if current is None:
        return True
    complete, previous_complete = bool(incoming.get("complete")), bool(current.get("complete"))
    if complete != previous_complete:
        return complete
    return int(incoming.get("observed_at_ms") or 0) > int(current.get("observed_at_ms") or 0)
