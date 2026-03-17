# wolfsclaw-telegram

Sky ecosystem monitoring bot that polls multiple sources every 4 hours and pushes updates to a Telegram channel. Smart deduplication ensures only genuinely new information is posted.

## Sources Monitored

- **Sky Forum** (Discourse) — new topics across Sky Core, Spark Prime, Incubating Primes, Atlas Edit proposals, MSC settlement posts
- **Sky Live Data + DefiLlama** — USDS supply, SKY price, TVL across Sky lending/Spark protocols
- **Sky Atlas** (GitHub) — commits and PRs to the next-gen-atlas repo
- **X/Twitter** — 19 monitored accounts + 5 keyword searches
- **Sky Insights** — new reports from insights.skyeco.com

## Quick Start (Fresh DigitalOcean Droplet)

```bash
# 1. SSH into your droplet
ssh root@your-droplet-ip

# 2. Clone the repo
git clone https://github.com/your-username/wolfsclaw-telegram.git /opt/wolfsclaw-telegram
cd /opt/wolfsclaw-telegram

# 3. Run setup script
chmod +x scripts/setup.sh
./scripts/setup.sh

# 4. Configure environment
cp .env.example .env
nano .env  # Add your API keys

# 5. Start the bot
docker compose up -d --build

# 6. Check logs
docker logs -f wolfsclaw-telegram
```

## Environment Variables

| Variable | Required | Description | Where to Get It |
|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Yes | Telegram bot token | [BotFather](https://t.me/BotFather) — /newbot |
| `TELEGRAM_CHANNEL_ID` | Yes | Channel ID (e.g. `@mychannel` or `-1001234567890`) | Forward a channel message to [@userinfobot](https://t.me/userinfobot) |
| `SKY_FORUM_API_KEY` | Yes | Discourse API key (read-only) | Forum admin panel → API → Generate key |
| `X_BEARER_TOKEN` | No | Twitter API v2 Bearer token | [Twitter Developer Portal](https://developer.twitter.com/) |
| `GITHUB_TOKEN` | No | GitHub PAT (increases rate limit to 5000/hr) | [GitHub Settings → Tokens](https://github.com/settings/tokens) |
| `LOG_LEVEL` | No | Logging level (default: `INFO`) | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `DB_PATH` | No | SQLite database path (default: `data/state.db`) | — |
| `SKIP_STARTUP_POLL` | No | Skip initial poll on startup (default: `false`) | `true` or `false` |
| `DRY_RUN` | No | Log messages instead of sending to Telegram (default: `false`) | `true` or `false` |

## Monitoring

```bash
# View live logs
docker logs -f wolfsclaw-telegram

# Run healthcheck
./scripts/healthcheck.sh

# Check container status
docker ps

# View database
sqlite3 data/state.db ".tables"
sqlite3 data/state.db "SELECT count(*) FROM seen_forum_topics;"
```

## Updating

```bash
cd /opt/wolfsclaw-telegram
git pull
docker compose up -d --build
```

## Schedule

| Job | Frequency |
|---|---|
| All source pollers | Every 4 hours (with 0-300s random jitter) |
| Daily market summary | 09:00 UTC daily |
| Weekly TVL summary | 09:00 UTC every Monday |
| Startup poll | On boot (unless `SKIP_STARTUP_POLL=true`) |

## Systemd (Alternative to Docker)

```bash
sudo cp deploy/wolfsclaw-telegram.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable wolfsclaw-telegram
sudo systemctl start wolfsclaw-telegram
```
