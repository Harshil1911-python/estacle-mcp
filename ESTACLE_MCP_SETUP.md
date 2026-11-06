# Estacle → Claude MCP Setup

## 1. Architecture

```
Claude Desktop / claude.ai
    │  Streamable HTTP MCP + OAuth 2.0 (PKCE)
    ▼
https://YOUR-DOMAIN/mcp          ← MCP tools (authenticated)
https://YOUR-DOMAIN/oauth/*      ← Authorize + token
https://YOUR-DOMAIN/             ← Existing Estacle Flask UI
    │
    ▼
SQLite instance/estacle.db       ← Same DB as the web app
```

- **Web UI** remains Flask (`routes/`, templates).
- **MCP** uses the official Python MCP SDK v2 (`MCPServer`) with Streamable HTTP.
- **Tools** call `core/crm_service.py`, which reads/writes the **same** SQLite tables as the website.
- **Auth**: each employee signs in with their Estacle **admin/agent** account; Claude stores a Bearer token.

## 2. Files created / modified

| Path | Purpose |
|------|---------|
| `core/crm_service.py` | Shared CRM operations for MCP |
| `core/oauth_store.py` | OAuth clients, codes, tokens |
| `routes/oauth.py` | AS metadata, authorize, token, dynamic registration |
| `templates/oauth/authorize.html` | Employee login consent page |
| `mcp_estacle/server.py` | FastMCP tools |
| `mcp_estacle/context.py` | Per-request user context |
| `asgi.py` | Starlette: `/mcp` + Flask `/` |
| `render.yaml` | Render start via uvicorn |
| `requirements.txt` | mcp, uvicorn, starlette |
| `app.py` | Registers OAuth blueprint |

## 3. Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `SECRET_KEY` | Yes (prod) | Flask session / crypto |
| `PUBLIC_BASE_URL` | Yes (prod) | e.g. `https://estacle-ultra.onrender.com` |
| `SESSION_COOKIE_SECURE` | Recommended | `1` on HTTPS |
| `DATABASE_PATH` | Optional | Override SQLite path |
| `PORT` | Set by Render | Listen port |

## 4. Render deployment

1. Push this repo to GitHub.
2. New **Web Service** on Render → connect repo.
3. Runtime: Python.
4. Build: `pip install -r requirements.txt`
5. Start: `uvicorn asgi:app --host 0.0.0.0 --port $PORT`
6. Set env:
   - `SECRET_KEY` = long random string  
   - `PUBLIC_BASE_URL` = `https://<your-service>.onrender.com`  
   - `SESSION_COOKIE_SECURE` = `1`
7. Deploy. Open the site and confirm login still works.

**MCP URL:**

```text
https://<your-service>.onrender.com/mcp
```

## 5. OAuth configuration

Built-in endpoints:

| Endpoint | Role |
|----------|------|
| `/.well-known/oauth-authorization-server` | AS metadata |
| `/.well-known/oauth-protected-resource` | Resource metadata for `/mcp` |
| `/oauth/authorize` | Browser login (admin/agent) |
| `/oauth/token` | Auth code → access token (PKCE) |
| `/oauth/register` | Dynamic client registration |

Pre-seeded public client id: `estacle-mcp-public` (PKCE, no secret).  
Claude may also register its own client via `/oauth/register`.

Only users with role **admin** or **agent** can authorize.

## 6. MCP server URL format

Use either form (both should work after the `/mcp` redirect fix):

```text
https://YOUR-DOMAIN/mcp
https://YOUR-DOMAIN/mcp/
```

If Claude reports **404**, confirm deploy is live and `curl -I https://YOUR-DOMAIN/mcp` is **401** or **307**, not Flask HTML 404.

## 6b. MCP server URL format (detail)


```text
https://YOUR-DOMAIN/mcp
```

Examples:

- `https://estacle-ultra.onrender.com/mcp`
- `https://crm.yourdomain.com/mcp`

## 7. Add Estacle to Claude

### Claude.ai (custom connector)

1. Open **Settings → Connectors** (or Custom MCP / integrations, depending on plan).
2. Add connector → **Remote MCP server**.
3. URL: `https://YOUR-DOMAIN/mcp`
4. Complete OAuth when prompted (Estacle login page).
5. Confirm tools appear (add_property, search_clients, …).

### Claude Desktop (if remote MCP supported in your build)

Add to the MCP config (names vary by version):

```json
{
  "mcpServers": {
    "estacle": {
      "url": "https://YOUR-DOMAIN/mcp"
    }
  }
}
```

Then complete OAuth in the browser when asked.

## 8. Testing

1. **Web**: `https://YOUR-DOMAIN/` login as `admin@estacle.com`.
2. **OAuth metadata**:  
   `curl -s https://YOUR-DOMAIN/.well-known/oauth-authorization-server | jq`
3. **MCP without token** should return **401**.
4. In Claude, after connecting:  
   *“Add a property in Vesu, Surat: 3BHK apartment priced at 8500000.”*
5. Refresh **Properties** in Estacle web — the new row must appear.
6. *“Show available properties”* → search_properties tool result.

Local combined server:

```bash
pip install -r requirements.txt
export PUBLIC_BASE_URL=http://127.0.0.1:5001
uvicorn asgi:app --host 0.0.0.0 --port 5001
```

## 9. Security considerations

- Per-user OAuth tokens; no shared admin API key for MCP.
- Agents are scoped to their own clients/properties where applicable.
- Deletes require `confirm=true` and admin role.
- Parameterized SQL only (no arbitrary SQL tools).
- Tokens expire (default 24h access).
- Set a strong `SECRET_KEY` and HTTPS (`PUBLIC_BASE_URL` https).

## 10. Troubleshooting

| Symptom | Check |
|---------|--------|
| Claude cannot connect | `PUBLIC_BASE_URL` matches the public HTTPS URL; service not sleeping mid-OAuth (Render free cold start) |
| 401 on tools | Re-run OAuth; token expired |
| Authorize fails | Use admin/agent credentials, not customer |
| redirect_uri error | Dynamic registration or claude.ai callback allow-list in `oauth_store` |
| Tools missing | Confirm start command is `uvicorn asgi:app` not only `gunicorn app:app` |
| Data not in UI | Same `instance/estacle.db` / `DATABASE_PATH` for the process |
| MCP import error | `pip install 'mcp>=1.2.0' uvicorn starlette` |

### Free Render note

Free instances spin down. First request after idle may be slow; OAuth + MCP may need a retry after wake-up.
