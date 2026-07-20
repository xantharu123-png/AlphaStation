# Alpha Station Commercial Launch Checklist

This checklist is for a paid beta or subscription launch. It does not modify or migrate the auth database.

## Must Be Green Before Selling

- `COMMERCE_ENFORCE_AUTH=1` and `COMMERCIAL_STRICT_MODE=1` are set.
- `/api/commercial-readiness` returns `commercial_ready: true`.
- `JWT_SECRET` is a long random production value; `ALLOW_LEGACY_ADMIN_MASTER_KEY=0`.
- Every credential that ever appeared in Git history was revoked and replaced. Only then set `HISTORICAL_SECRETS_ROTATED=1`.
- Repository visibility, collaborators, deploy keys and personal access tokens were reviewed. Only then set `SOURCE_REPOSITORY_ACCESS_REVIEWED=1`.
- `PUBLIC_APP_URL` and `CORS_ORIGINS` use the exact production HTTPS origin.
- Stripe uses live keys, a live webhook secret and verified live Price IDs.
- The persistent auth database is backed up and a restore was tested.
- Market-data, catalyst-data and news-data provider terms allow the planned commercial use and redistribution.
- Terms, privacy, risk disclaimer, cancellation/refund policy, tax/VAT and public company details were reviewed by qualified humans.
- Email alerts are only sent to eligible subscribers according to their saved preferences.
- Calendar coverage extends beyond the next relevant catalyst and earnings window.
- `bash deploy/verify_commercial_edge.sh` passes: nginx/TLS is healthy, port 80 redirects to HTTPS, FastAPI `8000` is loopback-only, and legacy ports `3000`/`8501` are closed.
- Full tests and the frontend bundle verifier pass from the exact target revision before code is activated.

## Required Production Environment

Template: `.env.production.example`. Keep `/home/tradingbot/app/.env` mode `600` and never commit real secrets.

| Variable | Required value / format |
|---|---|
| `JWT_SECRET` | 64 random hex characters, for example `openssl rand -hex 32` |
| `JWT_EXPIRY_HOURS` | `24` |
| `COMMERCE_ENFORCE_AUTH` | `1` |
| `COMMERCIAL_STRICT_MODE` | `1` |
| `ALLOW_LEGACY_ADMIN_MASTER_KEY` | `0` |
| `HISTORICAL_SECRETS_ROTATED` | `1` only after complete credential rotation |
| `SOURCE_REPOSITORY_ACCESS_REVIEWED` | `1` only after repository/access review |
| `CORS_ORIGINS` | exact `https://DOMAIN` origin, no wildcard or trailing slash |
| `PUBLIC_APP_URL` | exact `https://DOMAIN` origin |
| `ENABLE_COUPONS` | `0` until coupon redemption and access grants are transactional |
| `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_*` | verified Stripe live values |
| `LEGAL_REVIEW_APPROVED`, `DATA_LICENSE_APPROVED`, `TAX_SETUP_APPROVED` | `1` only after the named review is actually complete |
| `GMAIL_USER`, `GMAIL_APP_PASSWORD`, `ALERT_EMAIL`, `ALERT_SEND_TO_SUBSCRIBERS` | production alert configuration |

## Runtime Architecture

- `tradingbot-api.service`: FastAPI on `127.0.0.1:8000`.
- `tradingbot-bg.service`: background scans and subscriber alerts.
- nginx: static frontend and `/api/` reverse proxy on HTTPS.
- There must be no `tradingbot-frontend.service`, Streamlit `tradingbot.service`, or public listeners on `3000`, `8501` or `8000`.

Signal tracking runs from `bg_service.py`; `/api/signal-performance` is admin-only. Telegram mirroring is optional and must remain disabled unless both token and chat ID are configured securely.

## TLS Setup

`deploy/install.sh` obtains the certificate before enabling the TLS vHost. Manual equivalent:

```bash
sudo certbot certonly --nginx -d DOMAIN
sudo sed 's/${DOMAIN}/DOMAIN/g' deploy/nginx-tradingbot.conf | sudo tee /etc/nginx/sites-available/tradingbot
sudo ln -sf /etc/nginx/sites-available/tradingbot /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

## Final Server Commands

```bash
cd /home/tradingbot/app
bash deploy/verify_commercial_edge.sh
curl -s http://127.0.0.1:8000/api/commercial-readiness | python3 -m json.tool
COMMERCIAL_DEPLOY=1 bash deploy/safe_deploy.sh
```

## Product Positioning

Alpha Station is a research, scanner and alert platform. It must never promise guaranteed profits, guaranteed hit rates or personalized financial advice.
