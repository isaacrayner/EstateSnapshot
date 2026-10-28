"""Small helpers shared by the model modules."""


def lc(s):
    return str(s if s is not None else "").lower()


def name_of(rid):
    return rid.rstrip("/").split("/")[-1] if rid else ""


def g(d, *path, default=None):
    for p in path:
        if not isinstance(d, dict):
            return default
        d = d.get(p)
        if d is None:
            return default
    return d


def gb(n):
    try:
        return round(float(n) / 1024 ** 3)
    except (TypeError, ValueError):
        return None
