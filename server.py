"""Read-only Dota 2 analytics MCP, OpenDota account 129442282."""
import os
import asyncio
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from typing import Any

import httpx
from fastmcp import FastMCP

ACCOUNT_ID = 129442282
API = "https://api.opendota.com/api"
TURBO_MODE = 23
MAX_MATCHES = 500
mcp = FastMCP("Dota Analytics — 129442282")


async def fetch(path: str, params: dict | None = None) -> Any:
    query = dict(params or {})
    api_key = os.getenv("OPENDOTA_API_KEY")
    if api_key:
        query["api_key"] = api_key
    async with httpx.AsyncClient(timeout=45, headers={"User-Agent": "DotaAnalyticsMCP/3.0"}) as client:
        for attempt in range(3):
            try:
                response = await client.get(f"{API}{path}", params=query)
                if response.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                    await asyncio.sleep(2 ** attempt)
                    continue
                response.raise_for_status()
                return response.json()
            except (httpx.TimeoutException, httpx.TransportError):
                if attempt == 2:
                    raise
                await asyncio.sleep(2 ** attempt)


def is_win(match: dict) -> bool:
    return (int(match["player_slot"]) < 128) == bool(match["radiant_win"])


async def hero_names() -> dict[int, str]:
    data = await fetch("/constants/heroes")
    return {int(k): v.get("localized_name", f"Hero {k}") for k, v in data.items()}


def summarize(matches: list[dict], names: dict[int, str]) -> dict:
    n = len(matches)
    wins = sum(is_win(m) for m in matches)
    kills = sum(int(m.get("kills") or 0) for m in matches)
    deaths = sum(int(m.get("deaths") or 0) for m in matches)
    assists = sum(int(m.get("assists") or 0) for m in matches)
    heroes = defaultdict(lambda: {"matches": 0, "wins": 0, "kills": 0, "deaths": 0, "assists": 0})
    for match in matches:
        h = heroes[int(match["hero_id"])]
        h["matches"] += 1
        h["wins"] += int(is_win(match))
        for stat in ("kills", "deaths", "assists"):
            h[stat] += int(match.get(stat) or 0)
    hero_rows = []
    for hero_id, h in heroes.items():
        games = h["matches"]
        hero_rows.append({
            "hero_id": hero_id, "hero_name": names.get(hero_id, f"Hero {hero_id}"),
            "matches": games, "wins": h["wins"],
            "winrate_pct": round(100 * h["wins"] / games, 2),
            "avg_kills": round(h["kills"] / games, 2),
            "avg_deaths": round(h["deaths"] / games, 2),
            "avg_assists": round(h["assists"] / games, 2),
            "kda_ratio": round((h["kills"] + h["assists"]) / h["deaths"], 2) if h["deaths"] else None,
        })
    hero_rows.sort(key=lambda h: (-h["matches"], h["hero_name"]))
    return {
        "account_id": ACCOUNT_ID, "matches": n, "wins": wins, "losses": n - wins,
        "winrate_pct": round(100 * wins / n, 2) if n else None,
        "avg_kills": round(kills / n, 2) if n else None,
        "avg_deaths": round(deaths / n, 2) if n else None,
        "avg_assists": round(assists / n, 2) if n else None,
        "kda_ratio": round((kills + assists) / deaths, 2) if deaths else None,
        "heroes": hero_rows,
    }


async def collect_matches(count: int, ranked_only: bool) -> tuple[list[dict], dict]:
    """Page by offset, deduplicate, filter, and report completeness honestly."""
    result = []
    seen = set()
    page_size = 100
    pages = 0
    exhausted = False
    # Up to 50 pages (5000 raw matches), bounded for OpenDota rate limits.
    for page in range(50):
        batch = await fetch(f"/players/{ACCOUNT_ID}/matches", {"limit": page_size, "offset": page * page_size})
        pages += 1
        if not isinstance(batch, list):
            raise RuntimeError("OpenDota returned an unexpected match-history response")
        new_ids = 0
        for match in batch:
            mid = int(match["match_id"])
            if mid in seen:
                continue
            seen.add(mid)
            new_ids += 1
            if int(match.get("game_mode") or -1) == TURBO_MODE:
                continue
            if ranked_only and int(match.get("lobby_type") or -1) != 7:
                continue
            result.append(match)
            if len(result) == count:
                return result, {"requested": count, "returned": len(result), "complete": True, "pages_fetched": pages, "unique_source_matches": len(seen)}
        if len(batch) < page_size:
            exhausted = True
            break
        if new_ids == 0:
            # API ignored offset, or repeated a page; do not pretend the sample is complete.
            break
    return result, {"requested": count, "returned": len(result), "complete": False, "pages_fetched": pages, "unique_source_matches": len(seen), "history_exhausted": exhausted, "warning": "Insufficient unique qualifying matches; check OpenDota pagination, API visibility, or request limits"}


async def prepare(count: int, ranked_only: bool):
    count = max(1, min(int(count), MAX_MATCHES))
    matches, status = await collect_matches(count, ranked_only)
    names = await hero_names()
    return matches, status, names


@mcp.tool()
async def recent_matches(count: int = 100, ranked_only: bool = False) -> dict:
    """Return up to 500 newest unique non-Turbo matches, optionally Ranked only; include completeness status."""
    matches, status, names = await prepare(count, ranked_only)
    enriched = [{**m, "hero_name": names.get(int(m["hero_id"]), f"Hero {m['hero_id']}"), "win": is_win(m)} for m in matches]
    return {"filters": {"exclude_turbo": True, "ranked_only": ranked_only}, "collection": status, "summary": summarize(matches, names), "matches": enriched}


@mcp.tool()
async def hero_statistics(count: int = 300, ranked_only: bool = False) -> dict:
    """Summarize up to 500 newest unique non-Turbo matches by hero, including hero names and KDA."""
    matches, status, names = await prepare(count, ranked_only)
    return {"filters": {"exclude_turbo": True, "ranked_only": ranked_only}, "collection": status, "summary": summarize(matches, names)}


@mcp.tool()
async def match_details(match_id: int) -> dict:
    """Get a parsed match's detailed data, if available, for this account."""
    if not 1_000_000_000 <= int(match_id) <= 99_999_999_999:
        raise ValueError("Invalid match ID")
    data = await fetch(f"/matches/{int(match_id)}")
    if not any(int(p.get("account_id") or 0) == ACCOUNT_ID for p in data.get("players", [])):
        raise ValueError("Match does not contain configured account")
    return data


@mcp.tool()
async def compare_periods(count: int = 50, ranked_only: bool = False) -> dict:
    """Compare latest N to previous N unique non-Turbo matches; never imply full periods if incomplete."""
    count = max(1, min(int(count), MAX_MATCHES // 2))
    matches, status = await collect_matches(count * 2, ranked_only)
    names = await hero_names()
    return {"filters": {"exclude_turbo": True, "ranked_only": ranked_only}, "collection": status,
            "latest": summarize(matches[:count], names), "previous": summarize(matches[count:2 * count], names),
            "full_comparison_available": len(matches) >= 2 * count}


# --- v3: bounded period collection with explicit source-completeness semantics ---
MOSCOW = timezone(timedelta(hours=3))
PERIOD_MAX_PAGES = 100
PERIOD_PAGE_SIZE = 100
PERIOD_MAX_OUTPUT = 5000


def mode_category(match: dict) -> str:
    lobby = int(match.get("lobby_type") if match.get("lobby_type") is not None else -1)
    if lobby == 7:
        return "ranked"
    if lobby == 0:
        return "unranked"
    return "other"


def period_summary(matches: list[dict], names: dict[int, str]) -> dict:
    result = summarize(matches, names)
    seconds = sum(int(m.get("duration") or 0) for m in matches)
    deaths = sum(int(m.get("deaths") or 0) for m in matches)
    ten_plus = sum(int(m.get("deaths") or 0) >= 10 for m in matches)
    result.update({
        "duration_seconds_total": seconds,
        "deaths_per_10_min_total_time": round(600 * deaths / seconds, 3) if seconds else None,
        "matches_10_plus_deaths": ten_plus,
        "matches_10_plus_deaths_pct": round(100 * ten_plus / len(matches), 2) if matches else None,
        "mean_match_deaths_per_10_min": round(
            sum(600 * int(m.get("deaths") or 0) / int(m["duration"])
                for m in matches if int(m.get("duration") or 0) > 0) /
            sum(int(m.get("duration") or 0) > 0 for m in matches), 3
        ) if any(int(m.get("duration") or 0) > 0 for m in matches) else None,
    })
    return result


def compact_match(m: dict, names: dict[int, str]) -> dict:
    start = int(m["start_time"])
    duration = int(m.get("duration") or 0)
    hero_id = int(m["hero_id"])
    return {
        "match_id": int(m["match_id"]), "start_time": start,
        "start_time_msk": datetime.fromtimestamp(start, MOSCOW).isoformat(),
        "hero_id": hero_id, "hero_name": names.get(hero_id, f"Hero {hero_id}"),
        "game_mode": m.get("game_mode"), "lobby_type": m.get("lobby_type"),
        "mode_category": mode_category(m), "player_slot": m.get("player_slot"),
        "radiant_win": m.get("radiant_win"), "win": is_win(m),
        "kills": m.get("kills"), "deaths": m.get("deaths"), "assists": m.get("assists"),
        "duration": duration, "duration_mmss": f"{duration // 60}:{duration % 60:02d}",
    }


@mcp.tool()
async def matches_by_period(start_time: int, end_time: int = 0,
                            include_matches: bool = True, offset: int = 0) -> dict:
    """Get all OpenDota-visible non-Turbo matches with start_time > start_time and <= end_time.

    Use Unix UTC seconds. end_time=0 means now. Returns ranked/unranked/other separately.
    If capped, use collection.next_cursor as the offset in another call; do not
    treat source completeness as proof that OpenDota has every recently played match.
    """
    start_time = int(start_time)
    end_time = int(end_time) if end_time else int(datetime.now(timezone.utc).timestamp())
    offset = int(offset)
    if start_time < 0 or end_time <= start_time or end_time > int(datetime.now(timezone.utc).timestamp()) + 60:
        raise ValueError("Invalid start_time/end_time interval")
    if offset < 0 or offset % PERIOD_PAGE_SIZE:
        raise ValueError("offset must be a nonnegative multiple of 100")
    fetched_at = datetime.now(timezone.utc).isoformat()
    unique = {}
    duplicates = 0
    total_fetched = 0
    pages = 0
    errors = []
    boundary_reached = False
    source_exhausted = False
    stop_reason = None
    next_cursor = None
    for page in range(PERIOD_MAX_PAGES):
        current_offset = offset + page * PERIOD_PAGE_SIZE
        try:
            batch = await fetch(f"/players/{ACCOUNT_ID}/matches", {
                "limit": PERIOD_PAGE_SIZE, "offset": current_offset
            })
            if not isinstance(batch, list):
                raise RuntimeError("Unexpected OpenDota response (expected list)")
        except Exception as exc:
            errors.append(f"offset={current_offset}: {type(exc).__name__}: {exc}")
            stop_reason = "source_error"
            next_cursor = current_offset
            break
        pages += 1
        total_fetched += len(batch)
        if not batch:
            source_exhausted = True
            stop_reason = "source_exhausted"
            break
        new_ids = 0
        page_times = []
        for m in batch:
            try:
                mid = int(m["match_id"])
                timestamp = int(m["start_time"])
            except (KeyError, TypeError, ValueError) as exc:
                errors.append(f"Invalid match row at offset={current_offset}: {exc}")
                continue
            page_times.append(timestamp)
            if mid in unique:
                duplicates += 1
                continue
            unique[mid] = m
            new_ids += 1
        if not new_ids:
            stop_reason = "repeated_page"
            next_cursor = current_offset + PERIOD_PAGE_SIZE
            errors.append("Page has no new match IDs; pagination may be repeating")
            break
        # OpenDota player history is expected newest first; check before early stop.
        if any(page_times[i] < page_times[i + 1] for i in range(len(page_times) - 1)):
            errors.append(f"Page at offset={current_offset} is not ordered newest-first")
            stop_reason = "unexpected_order"
            next_cursor = current_offset + PERIOD_PAGE_SIZE
            break
        if page_times and min(page_times) <= start_time:
            boundary_reached = True
            stop_reason = "start_boundary_reached"
            break
        if len(batch) < PERIOD_PAGE_SIZE:
            source_exhausted = True
            stop_reason = "source_exhausted"
            break
        if len(unique) >= PERIOD_MAX_OUTPUT:
            stop_reason = "output_limit"
            next_cursor = current_offset + PERIOD_PAGE_SIZE
            break
    else:
        stop_reason = "page_limit"
        next_cursor = offset + PERIOD_MAX_PAGES * PERIOD_PAGE_SIZE
    selected = [m for m in unique.values()
                if start_time < int(m["start_time"]) <= end_time
                and int(m.get("game_mode") if m.get("game_mode") is not None else -1) != TURBO_MODE]
    selected.sort(key=lambda m: (int(m["start_time"]), int(m["match_id"])), reverse=True)
    groups = {category: [m for m in selected if mode_category(m) == category]
              for category in ("ranked", "unranked", "other")}
    names = {}
    try:
        names = await hero_names()
    except Exception as exc:
        errors.append(f"Hero names unavailable: {type(exc).__name__}: {exc}")
    stats = {key: period_summary(group, names) for key, group in groups.items()}
    # complete refers ONLY to successful traversal of the available API history.
    complete = (boundary_reached or source_exhausted) and not any(
        e for e in errors if not e.startswith("Hero names unavailable"))
    collection = {
        "fetched_at": fetched_at, "source_updated_at": None,
        "source_freshness": "unverified; OpenDota may omit recent matches",
        "start_time_exclusive": start_time, "end_time_inclusive": end_time,
        "oldest_match_time": min((int(m["start_time"]) for m in selected), default=None),
        "newest_match_time": max((int(m["start_time"]) for m in selected), default=None),
        "total_fetched": total_fetched, "unique_source_matches": len(unique),
        "unique_matches": len(selected), "duplicates_removed": duplicates,
        "pages_fetched": pages, "complete": complete, "stop_reason": stop_reason,
        "errors": errors, "next_cursor": next_cursor,
        "warning": "No matches found does not prove no games were played",
        "missing_fields": sorted({field for m in selected for field in
            ("match_id", "start_time", "hero_id", "game_mode", "lobby_type", "player_slot",
             "radiant_win", "kills", "deaths", "assists", "duration") if m.get(field) is None}),
    }
    return {
        "account_id": ACCOUNT_ID,
        "filters": {"exclude_turbo": True, "start_time_exclusive": start_time,
                    "end_time_inclusive": end_time, "lobby_categories": ["ranked", "unranked", "other"]},
        "collection": collection, "statistics": stats,
        "matches": {key: [compact_match(m, names) for m in group] for key, group in groups.items()}
                   if include_matches else None,
    }


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    mcp.run(transport="http", host="0.0.0.0", port=port, path="/mcp")
