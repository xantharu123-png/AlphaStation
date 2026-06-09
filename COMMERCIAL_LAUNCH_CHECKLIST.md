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

## Server Commands

```bash
cd /home/tradingbot/app
curl -s http://127.0.0.1:8000/api/commercial-readiness | python3 -m json.tool
COMMERCIAL_DEPLOY=1 bash deploy/safe_deploy.sh
```

## Important Positioning

Alpha Station should be sold as a research, scanner and alert platform. It must not promise guaranteed profits, guaranteed win rate or personal financial advice.
