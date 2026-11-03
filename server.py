"""Read-only OpenDota MCP server for account 129442282."""
import os
from collections import Counter, defaultdict
from typing import Any

import httpx
from fastmcp import FastMCP

ACCOUNT_ID = 129442282
API = "https://api.opendota.com/api"
TURBO_MODE = 23
mcp = FastMCP("Dota Analytics — 129442282")


async def fetch(path: str, params: dict | None = None) -> Any:
    headers = {"User-Agent": "DotaAnalyticsMCP/1.0"}
    api_key = os.getenv("OPENDOTA_API_KEY")
    if api_key:
        params = {**(params or {}), "api_key": api_key}
    async with httpx.AsyncClient(timeout=30, headers=headers) as client:
        response = await client.get(f"{API}{path}", params=params)
        response.raise_for_status()
        return response.json()


def is_win(match: dict) -> bool:
    radiant = int(match["player_slot"]) < 128
    return bool(match["radiant_win"]) == radiant


def summarize(matches: list[dict]) -> dict:
    n = len(matches)
    wins = sum(is_win(m) for m in matches)
    hero_stats: dict[int, dict] = defaultdict(lambda: {"matches": 0, "wins": 0})
    for match in matches:
        h = hero_stats[int(match["hero_id"])]
        h["matches"] += 1
        h["wins"] += is_win(match)
    return {
        "account_id": ACCOUNT_ID,
        "matches": n,
        "wins": wins,
        "losses": n - wins,
        "winrate_pct": round(100 * wins / n, 2) if n else None,
        "avg_kills": round(sum(m.get("kills", 0) for m in matches) / n, 2) if n else None,
        "avg_deaths": round(sum(m.get("deaths", 0) for m in matches) / n, 2) if n else None,
        "avg_assists": round(sum(m.get("assists", 0) for m in matches) / n, 2) if n else None,
        "heroes": [{"hero_id": k, **v, "winrate_pct": round(100 * v["wins"] / v["matches"], 2)} for k, v in sorted(hero_stats.items(), key=lambda kv: -kv[1]["matches"])],
    }


async def collect_matches(count: int, ranked_only: bool) -> list[dict]:
    """Page backwards until enough qualifying matches; avoid truncation due to Turbo."""
    matches: list[dict] = []
    last_match_id = None
    # 20 pages * 100 = at most 2000 source games; bounded to avoid abusive calls.
    for _ in range(20):
        params: dict[str, int] = {"limit": 100}
        if last_match_id is not None:
            params["less_than_match_id"] = last_match_id
        batch = await fetch(f"/players/{ACCOUNT_ID}/matches", params)
        if not batch:
            break
        for match in batch:
            if match.get("game_mode") == TURBO_MODE:
                continue
            if ranked_only and match.get("lobby_type") != 7:
                continue
            matches.append(match)
            if len(matches) >= count:
                return matches
        last_match_id = min(int(m["match_id"]) for m in batch)
        if len(batch) < 100:
            break
    return matches


@mcp.tool()
async def recent_matches(count: int = 100, ranked_only: bool = False) -> dict:
    """Get latest non-Turbo matches, optionally ranked only. Returns matches and summary."""
    count = max(1, min(int(count), 200))
    matches = await collect_matches(count, ranked_only)
    return {"filters": {"exclude_turbo": True, "ranked_only": ranked_only}, "summary": summarize(matches), "matches": matches}


@mcp.tool()
async def hero_statistics(count: int = 100, ranked_only: bool = False) -> dict:
    """Calculate hero win rates and KDA over latest non-Turbo matches."""
    count = max(1, min(int(count), 200))
    matches = await collect_matches(count, ranked_only)
    return {"filters": {"exclude_turbo": True, "ranked_only": ranked_only}, "summary": summarize(matches)}


@mcp.tool()
async def match_details(match_id: int) -> dict:
    """Retrieve detailed match data from OpenDota, when available."""
    if not 1_000_000_000 <= int(match_id) <= 99_999_999_999:
        raise ValueError("Invalid match ID")
    data = await fetch(f"/matches/{int(match_id)}")
    players = data.get("players", [])
    if not any(p.get("account_id") == ACCOUNT_ID for p in players):
        raise ValueError("This match does not contain the configured account")
    return data


@mcp.tool()
async def compare_periods(count: int = 50, ranked_only: bool = False) -> dict:
    """Compare latest N non-Turbo matches to the preceding N."""
    count = max(1, min(int(count), 100))
    matches = await collect_matches(count * 2, ranked_only)
    return {"filters": {"exclude_turbo": True, "ranked_only": ranked_only}, "latest": summarize(matches[:count]), "previous": summarize(matches[count:count * 2]), "sample_size": len(matches)}


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    mcp.run(transport="http", host="0.0.0.0", port=port, path="/mcp")
