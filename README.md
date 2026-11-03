# Dota Analytics MCP

Read-only MCP server for OpenDota account `129442282`.

## Tools
- `recent_matches(count=100, ranked_only=False)` — most recent non-Turbo games.
- `hero_statistics(count=100, ranked_only=False)` — win rate/KDA by hero.
- `match_details(match_id)` — detailed match JSON for this account.
- `compare_periods(count=50, ranked_only=False)` — compare latest N vs previous N.

## Local run

```bash
pip install -r requirements.txt
python server.py
```

Remote MCP endpoint: `https://YOUR-SERVICE.onrender.com/mcp`.

## Notes

- Public data only. No Steam login or credentials required.
- OpenDota may rate-limit requests or lack parsed details for some matches.
- Free hosting may sleep; cold starts can delay requests.
- This is a public, unauthenticated read-only endpoint. Do not add private data without authentication.
