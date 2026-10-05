def duration(
    start: str,
    end: str,
    val: int,
    *,
    fy: int,
    fp: str,
    accn: str = "0001045810-24-000001",
    form: str = "10-K",
    filed: str = "2024-02-21",
    frame: str | None = None,
) -> dict:
    item = {
        "start": start,
        "end": end,
        "val": val,
        "accn": accn,
        "fy": fy,
        "fp": fp,
        "form": form,
        "filed": filed,
    }
    if frame:
        item["frame"] = frame
    return item


def instant(
    end: str,
    val: int,
    *,
    fy: int,
    fp: str,
    accn: str = "0001045810-24-000001",
    form: str = "10-K",
    filed: str = "2024-02-21",
    frame: str | None = None,
) -> dict:
    item = {
        "end": end,
        "val": val,
        "accn": accn,
        "fy": fy,
        "fp": fp,
        "form": form,
        "filed": filed,
    }
    if frame:
        item["frame"] = frame
    return item


def facts_payload(*series: tuple[str, str, str, list[dict]]) -> dict:
    payload: dict = {"cik": 1045810, "entityName": "NVIDIA CORP", "facts": {}}
    for namespace, concept, unit, items in series:
        payload["facts"].setdefault(namespace, {})[concept] = {"units": {unit: items}}
    return payload
