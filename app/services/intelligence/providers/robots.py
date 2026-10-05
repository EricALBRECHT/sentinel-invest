"""A small robots.txt reader. The longest matching rule wins. Allow wins a tie."""

from urllib.parse import urlsplit


def robots_policy(body: str, user_agent: str, url: str) -> tuple[bool, float | None]:
    path = urlsplit(url).path or "/"
    groups = _groups(body)
    chosen = _choose(groups, user_agent)
    delay = chosen.get("delay")
    allowed = _allowed(chosen.get("rules", []), path)
    return allowed, delay


def _groups(body: str) -> list[dict]:
    groups: list[dict] = []
    current: dict | None = None
    for raw in body.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if key == "user-agent":
            if current is None or current.get("rules") or current.get("delay") is not None:
                current = {"agents": [], "rules": [], "delay": None}
                groups.append(current)
            current["agents"].append(value.lower())
        elif current is None:
            continue
        elif key in {"allow", "disallow"}:
            current["rules"].append((key, value))
        elif key == "crawl-delay":
            try:
                current["delay"] = float(value)
            except ValueError:
                continue
    return groups


def _choose(groups: list[dict], user_agent: str) -> dict:
    agent = user_agent.lower()
    specific = [group for group in groups if any(name != "*" and name in agent for name in group["agents"])]
    if specific:
        return _merge(specific)
    generic = [group for group in groups if "*" in group["agents"]]
    if generic:
        return _merge(generic)
    return {"rules": [], "delay": None}


def _merge(groups: list[dict]) -> dict:
    rules = []
    delay = None
    for group in groups:
        rules.extend(group["rules"])
        if group["delay"] is not None:
            delay = group["delay"] if delay is None else max(delay, group["delay"])
    return {"rules": rules, "delay": delay}


def _allowed(rules: list[tuple[str, str]], path: str) -> bool:
    best_length = -1
    best_allow = True
    for kind, prefix in rules:
        if prefix == "":
            continue
        if prefix.endswith("$"):
            matched = path == prefix[:-1]
            length = len(prefix) - 1
        else:
            matched = path.startswith(prefix)
            length = len(prefix)
        if not matched or length < best_length:
            continue
        if length == best_length and best_allow:
            continue
        best_length = length
        best_allow = kind == "allow"
    return best_allow
