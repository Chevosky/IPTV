"""Choose freshness only within an already matched XMLTV channel identity."""
from datetime import datetime, timezone


def _time(value):
    try:
        value = value.strip()
        fmt = "%Y%m%d%H%M%S %z" if value[14:].strip() else "%Y%m%d%H%M%S"
        parsed = datetime.strptime(value, fmt)
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except (AttributeError, ValueError):
        return None


def schedule_freshness(programmes, now):
    current = False
    latest = datetime.min.replace(tzinfo=timezone.utc)
    for programme in programmes:
        start, stop = _time(programme.get("start")), _time(programme.get("stop"))
        if start is None or stop is None or stop <= start:
            continue
        current |= start <= now < stop
        latest = max(latest, stop)
    return current, latest > now, latest


def prefer_incoming(existing, incoming, now):
    """Keep exact-source priority when both are fresh, never replace fresh by stale."""
    old, new = schedule_freshness(existing, now), schedule_freshness(incoming, now)
    if old[:2] != new[:2]:
        return new[:2] > old[:2]
    if not old[1] and not new[1]:
        return new[2] >= old[2]
    return True
