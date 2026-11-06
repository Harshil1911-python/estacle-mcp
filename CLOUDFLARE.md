# Deploy ESTACLE-ULTRA on Cloudflare

This project is set up for **Cloudflare Containers** (Flask + gunicorn in Docker),
fronted by a Worker. That is the supported way to run this full CRM on Cloudflare
without rewriting SQLite → D1 yet.

## Requirements

- Cloudflare account with **Workers Paid** (Containers)
- Node.js 18+ and npm
- Docker (for building the image on deploy)
- Wrangler CLI (`npm i` in this repo installs it)

## 1. Install

```bash
cd ESTACLE-ULTRA
npm install
pip install -r requirements.txt   # optional, for local python app.py
```

## 2. Login

```bash
npx wrangler login
```

## 3. Set secrets

```bash
npx wrangler secret put SECRET_KEY
# paste a long random string
```

## 4. Deploy

```bash
npm run deploy
# or: npx wrangler deploy
```

Wrangler will:

1. Publish the Worker (`cf-worker/src/index.js`)
2. Build `./Dockerfile` (Flask + gunicorn)
3. Run container instances (max 1 — safer for SQLite)

Your app will be at:

`https://estacle-ultra.<your-subdomain>.workers.dev`

## 5. Custom domain

Workers & Pages → your worker → **Settings → Domains** → Add custom domain  
(or Cloudflare DNS CNAME to workers.dev, proxied).

## Local development

**Python only (fast):**

```bash
export FLASK_DEBUG=1
python app.py
# http://127.0.0.1:5001
```

**Full Cloudflare stack locally:**

```bash
# Docker must be running
npm run dev
```

## Architecture

```
Internet → Cloudflare Worker → Container (gunicorn :8080) → Flask → SQLite
```

- `/static/*` still gets long cache headers (Worker/CF edge can cache)
- `/health` for checks
- `max_instances = 1` avoids multi-node SQLite corruption

## Limits / next steps

| Topic | Today | Later |
|--------|--------|--------|
| Database | SQLite inside container | D1 or Turso/Postgres + Hyperdrive |
| Uploads | Local disk in container | R2 bucket |
| Scale-out | Single instance | External DB first, then scale |

## Alternative: proxy-only Cloudflare

If you prefer a free/simple path without Containers:

1. Run gunicorn on Render/Railway/VPS
2. Point a Cloudflare domain (orange cloud) at that origin
3. Use cache rules from earlier docs for `/static/*`

## Troubleshooting

- **Deploy needs Docker** — start Docker Desktop / dockerd before `wrangler deploy`
- **502 from Worker** — container cold start; retry; check `wrangler tail`
- **Login sessions** — ensure `SECRET_KEY` secret is set and `SESSION_COOKIE_SECURE=1`
