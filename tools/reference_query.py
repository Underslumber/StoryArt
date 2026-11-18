"""Query-time discovery rules. These never alter indexed reference evidence."""
from collections import deque
import re
import unicodedata


BODY_FACINGS = ("FRONT", "SIDE", "BACK", "THREE_QUARTER")
_FACING_QUERIES = {
    "спереди": "FRONT",
    "анфас": "FRONT",
    "сбоку": "SIDE",
    "в профиль": "SIDE",
    "со спины": "BACK",
    "сзади": "BACK",
    "в 3/4": "THREE_QUARTER",
    "3/4": "THREE_QUARTER",
    "в три четверти": "THREE_QUARTER",
}


def resolve_body_facing(query: str, explicit: str = "") -> tuple[str, str]:
    phrase = unicodedata.normalize("NFKC", query).casefold().replace("⁄", "/")
    facing = _FACING_QUERIES.get(" ".join(phrase.split()))
    if facing and explicit and facing != explicit:
        raise ValueError("Запрос и --body-facing задают разные виды корпуса")
    return ("", facing) if facing else (query, explicit)


def normalize_query(query: str) -> str:
    query = re.sub(r"\bдай\s+(?:пять|5)\b", "high five", query.casefold())
    return " ".join(re.sub(r"\bсидит\b", "сидя", query).split())


def rank_shortlist(records, query, text, source, group, limit):
    """Offer distinct groups across matching pools before additional views."""
    if limit <= 0:
        return []
    terms = normalize_query(query).split()

    def sort_key(record):
        haystack = text(record).casefold()
        title = record.get("_search_fields", {}).get("title", record.get("title", "")).casefold()
        relevance = sum(8 * (term in title) + 2 * haystack.count(term) for term in terms)
        return -relevance, str(source(record)), str(group(record))

    first_by_source = {}
    seen_groups = set()
    other_views = []
    for record in sorted(records, key=sort_key):
        pool = source(record)
        key = (pool, group(record))
        if key in seen_groups:
            other_views.append(record)
        else:
            seen_groups.add(key)
            first_by_source.setdefault(pool, deque()).append(record)

    pools = deque(first_by_source.values())
    result = []
    while pools and len(result) < limit:
        rows = pools.popleft()
        result.append(rows.popleft())
        if rows:
            pools.append(rows)
    return result + other_views[:limit - len(result)]
