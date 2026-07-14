# Development notes

This project captures GMGN upstream WebSocket/HTTP polling traffic with Playwright and delivers only to Telegram groups.

## Commands

```bash
uv sync --python 3.12 --frozen
uv run playwright install chromium
uv run python -m gmgn_twitter_monitor
GMGN_AUTH_URL='https://gmgn.ai/tglogin?...' uv run python -m gmgn_twitter_monitor --login
uv run python -m unittest discover -s tests -v
```

Production deployment is managed by `install.sh`. Configuration lives outside the release tree at `/etc/gmgn-twitter-monitor/gmgn.env`; mutable state lives at `/var/lib/gmgn-twitter-monitor`.

The `cp=0` payload is sent immediately as `TG_FAST`; `cp=1` updates the same Telegram message through `TG_UPDATE`. Do not reintroduce a delayed default distributor path.
