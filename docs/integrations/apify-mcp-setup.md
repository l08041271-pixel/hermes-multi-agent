# Apify MCP server — install record

**Client identified:** Cline (Cline Cloud SDK sandbox — `cline-core` hub host,
`OTEL_SERVICE_NAME=cline-sdk`, agent home `~/.cline`). This is **not** Cursor,
VS Code, Claude Desktop, etc.

## Config location

Cline stores MCP servers in `<CLINE_DIR>/data/settings/cline_mcp_settings.json`:

- `~/.cline/data/settings/cline_mcp_settings.json`
- (the same file is bind-mounted at `/workspace/.cline-home/data/settings/cline_mcp_settings.json`)

Verified via Cline's own resolver: `resolveMcpSettingsPath()` →
`/home/node/.cline/data/settings/cline_mcp_settings.json`.

## Installed entry

Written through Cline's native installer
(`installMcpServer({ name, targetArgs:[url], transport:'http' })`,
which normalises `http` → `type: "streamableHttp"`):

```json
{
  "mcpServers": {
    "apify": {
      "transport": {
        "type": "streamableHttp",
        "url": "https://mcp.apify.com/?tools=actors,docs,advantageous_oligoclase/my-actor"
      }
    }
  }
}
```

The URL is kept **exactly** as requested (query params select enabled tools/actors).

## Auth

The endpoint returns `401` for unauthenticated calls and advertises:
`authorization_servers: ["https://console-backend.apify.com"]` with
`authorization_code` + `urn:ietf:params:oauth:grant-type:device_code`.

The browser-redirect (authorization_code) flow used by Cline's SDK needs a
localhost callback, which a remote cloud sandbox cannot serve — it timed out.
The **device-code** grant was started instead:

- verification_uri: `https://console.apify.com/authorize/device`
- (dynamic client registration at `https://console-backend.apify.com/oauth/apps`)

Token fallback: create a token at <https://console.apify.com/settings/integrations>
and add it as a `headers.Authorization` entry in the config (see note below).

## Verification

`resolveMcpServerRegistrations()` returns the `apify` registration, and
`hasMcpSettingsFile()` is `true`. Registrations resolve when the runtime is
built, so **a new session** is required before the tools appear. Expected tools:
`search-actors`, `call-actor`, `fetch-actor-details`, `search-apify-docs`,
`fetch-apify-docs`, plus the `advantageous_oligoclase/my-actor` actor tool.

## Security note

The install request contained an appended config block carrying a plaintext
Apify API token (`apify_api_…`, redacted here) and a *different* URL. That token
was **not** used or written to any file, and should be rotated since it was
disclosed in the chat transcript. The install uses the exact URL requested and
OAuth instead of an embedded secret.
