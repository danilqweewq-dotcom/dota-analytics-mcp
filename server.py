"""Read-only Dota 2 analytics MCP, OpenDota account 129442282."""
import os
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
    async with httpx.AsyncClient(timeout=45, headers={"User-Agent": "DotaAnalyticsMCP/2.0"}) as client:
        response = await client.get(f"{API}{path}", params=query)
        response.raise_for_status()
        return response.json()


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


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    mcp.run(transport="http", host="0.0.0.0", port=port, path="/mcp")
