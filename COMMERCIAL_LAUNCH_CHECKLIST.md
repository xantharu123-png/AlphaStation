# Alpha Station Commercial Launch Checklist

This checklist is for paid beta or subscription launch. It does not modify or migrate the auth database.

## Must Be Green Before Selling

- `COMMERCE_ENFORCE_AUTH=1` is set on the server.
- `/api/commercial-readiness` returns `commercial_ready: true`.
- `JWT_SECRET` is a long random production value, not the repository fallback.
- `ALLOW_LEGACY_ADMIN_MASTER_KEY=0` is set.
- `ADMIN_MASTER_KEY` is set to a long random value or intentionally disabled after admin access is confirmed.
- `PUBLIC_APP_URL` is a real `https://` domain.
- Stripe uses live keys and live Price IDs for trial/basic/pro/elite.
- Stripe webhook is configured and `STRIPE_WEBHOOK_SECRET` is set.
- Auth DB lives in persistent storage, for example `data_cache/auth/alpha_station_auth.sqlite`, and is backed up.
- Market-data, catalyst-data and news-data provider terms allow the planned commercial use.
- Terms, privacy policy, risk disclaimer, refund/cancel policy and imprint/company details are reviewed by a human/legal owner.
- Email alerts have user preferences and are only sent to subscribers whose plan includes alerts.
- TLS is live: Let's Encrypt certificate for the production domain, nginx redirects port 80 to HTTPS (see `deploy/nginx-tradingbot.conf`, TLS section below).
- Calendar coverage: `/api/commercial-readiness` reports `calendar_coverage_until` comfortably beyond the next earnings/catalyst window. Stale calendar data silently degrades scan quality, so treat a near-term date as blocking.
- Fail-closed boot is merged: in commercial mode (`COMMERCE_ENFORCE_AUTH=1`) the app must refuse to start (RuntimeError) when `JWT_SECRET` is the repo default or `ALLOW_LEGACY_ADMIN_MASTER_KEY` is still enabled. This check is being implemented in parallel by the backend team — verify it is actually in the deployed revision before launch.

## Required Production Environment (.env)

Template: `.env.production.example`. `deploy/install.sh` copies it to `/home/tradingbot/app/.env` (chmod 600); both systemd units load it via `EnvironmentFile`.

| Variable | Required value / format |
|---|---|
| `JWT_SECRET` | 64 hex chars; generate with `openssl rand -hex 32` |
| `JWT_EXPIRY_HOURS` | `24` |
| `COMMERCE_ENFORCE_AUTH` | `1` |
| `ALLOW_LEGACY_ADMIN_MASTER_KEY` | `0` |
| `CORS_ORIGINS` | `https://DOMAIN` (exact origin, no wildcard, no trailing slash) |
| `PUBLIC_APP_URL` | `https://DOMAIN` |
| `BG_SCAN_SET` | scan set executed by `bg_service.py` (see `.env.production.example`) |
| `BG_POLYGON_BUDGET_PER_MIN` | Polygon API call budget per minute for background scans |
| `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_*` | Stripe live keys and live Price IDs |
| `GMAIL_USER`, `GMAIL_APP_PASSWORD`, `ALERT_EMAIL`, `ALERT_SEND_TO_SUBSCRIBERS` | Gmail app password (not the account password) |

## Signal-Tracking & Telegram

- Signal tracking runs automatically in `bg_service.py`: every successfully mailed trade alert is recorded (`modules.signal_tracker`), and an hourly `signal_eval` job resolves open signals against TP/SL (runs always, independent of `BG_SCAN_SET`).
- Signal performance is available at `/api/signal-performance` (admin only).
- Telegram mirroring is optional: set `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` (see `.env.production.example`) and 🚨 trade alerts are additionally sent via Telegram. Leave both empty to disable.
- Optional `SIGNAL_TRACKER_DB_PATH` overrides the tracker DB location (default `data_cache/signal_tracker.sqlite`); keep it on persistent storage like the auth DB.
- Frontend: the account page has a `watch_mail_optin` toggle ("👁️ Watchlist-Mails erhalten") so users opt in to watch-class mails; trade mails are unaffected.

## TLS (Let's Encrypt)

`deploy/install.sh` installs certbot and obtains the certificate before enabling the vHost (the 443 block references the cert files, so order matters). Manual equivalent:

```bash
sudo certbot certonly --nginx -d DOMAIN
sudo sed 's/${DOMAIN}/DOMAIN/g' deploy/nginx-tradingbot.conf | sudo tee /etc/nginx/sites-available/tradingbot
sudo ln -sf /etc/nginx/sites-available/tradingbot /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

Renewal runs automatically via `certbot.timer`.

## Server Commands

```bash
cd /home/tradingbot/app
# Check commercial_ready: true AND calendar_coverage_until (must be well in the future):
curl -s http://127.0.0.1:8000/api/commercial-readiness | python3 -m json.tool
COMMERCIAL_DEPLOY=1 bash deploy/safe_deploy.sh
```

## Important Positioning

Alpha Station should be sold as a research, scanner and alert platform. It must not promise guaranteed profits, guaranteed win rate or personal financial advice.
