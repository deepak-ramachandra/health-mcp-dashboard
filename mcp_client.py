"""Sync helper for calling a remote MCP server (nutrition, workouts,
transactions) from Streamlit. Handles OAuth: first run locally opens a browser
to authorize once, tokens are then cached to disk and refreshed automatically.

The server URL is deliberately kept out of source control - it's read from
`st.secrets["mcp_server_url"]` (see .streamlit/secrets.toml locally, or the
app's Secrets settings on Streamlit Community Cloud).

On Streamlit Community Cloud there's no browser and no local filesystem
persistence across deploys, so the initial token/client registration is
instead seeded from an `[mcp_tokens]` table in `st.secrets` (see
`export_mcp_secrets.py`). Refreshed tokens are still cached to the
container's local disk for that running instance's lifetime.
"""

import asyncio
import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import streamlit as st
from mcp import ClientSession
from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.client.streamable_http import streamablehttp_client
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken

MCP_SERVER_URL = st.secrets.get("mcp_server_url")
if not MCP_SERVER_URL:
    raise RuntimeError(
        "Missing 'mcp_server_url' secret. Add it to .streamlit/secrets.toml locally, "
        "or this app's Secrets settings on Streamlit Community Cloud."
    )
TOKEN_FILE = Path(__file__).parent / ".streamlit" / "mcp_tokens.json"
REDIRECT_PORT = 8765


class NoInteractiveAuthError(RuntimeError):
    """Raised when OAuth needs a browser-based login but none is available
    (e.g. on Streamlit Community Cloud)."""


class _FileTokenStorage(TokenStorage):
    """Persists OAuth tokens + client registration to a local JSON file,
    seeded from st.secrets['mcp_tokens'] when the file doesn't exist yet."""

    def _read(self) -> dict:
        if TOKEN_FILE.exists():
            return json.loads(TOKEN_FILE.read_text())
        seed = st.secrets.get("mcp_tokens")
        if seed:
            return {k: dict(v) for k, v in seed.items()}
        return {}

    def _write(self, data: dict) -> None:
        TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_FILE.write_text(json.dumps(data))

    async def get_tokens(self) -> OAuthToken | None:
        data = self._read()
        return OAuthToken.model_validate(data["tokens"]) if "tokens" in data else None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        data = self._read()
        data["tokens"] = tokens.model_dump(mode="json")
        self._write(data)

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        data = self._read()
        return OAuthClientInformationFull.model_validate(data["client_info"]) if "client_info" in data else None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        data = self._read()
        data["client_info"] = client_info.model_dump(mode="json")
        self._write(data)


class _OAuthCallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        qs = parse_qs(urlparse(self.path).query)
        self.server.result = (qs.get("code", [None])[0], qs.get("state", [None])[0])
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        self.wfile.write(b"<html><body>Authorized - you can close this tab.</body></html>")
        self.server.done.set()

    def log_message(self, *args):
        pass


_REAUTH_HELP = (
    "This app needs a fresh sign-in, but no browser is available here "
    "(this happens on Streamlit Community Cloud, where the refresh token has "
    "expired or was rotated). Run this app locally once to re-authorize, then "
    "regenerate the 'mcp_tokens' secret with `uv run python export_mcp_secrets.py` "
    "and paste it into this app's Secrets settings."
)


async def _redirect_handler(url: str) -> None:
    try:
        opened = webbrowser.open(url)
    except webbrowser.Error:
        opened = False
    if not opened:
        raise NoInteractiveAuthError(_REAUTH_HELP)


async def _callback_handler() -> tuple[str, str | None]:
    server = HTTPServer(("localhost", REDIRECT_PORT), _OAuthCallbackHandler)
    server.done = threading.Event()
    server.result = (None, None)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, server.done.wait, 300)
    finally:
        server.shutdown()
    if server.result == (None, None):
        raise NoInteractiveAuthError(_REAUTH_HELP)
    return server.result


def _make_auth() -> OAuthClientProvider:
    client_metadata = OAuthClientMetadata(
        client_name="Health dashboard",
        redirect_uris=[f"http://localhost:{REDIRECT_PORT}/callback"],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        token_endpoint_auth_method="none",
    )
    return OAuthClientProvider(
        server_url=MCP_SERVER_URL,
        client_metadata=client_metadata,
        storage=_FileTokenStorage(),
        redirect_handler=_redirect_handler,
        callback_handler=_callback_handler,
    )


async def _call_tool_async(name: str, arguments: dict) -> str:
    async with streamablehttp_client(MCP_SERVER_URL, auth=_make_auth()) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(name, arguments)
            if result.isError:
                raise RuntimeError(f"MCP tool '{name}' failed: {result.content[0].text}")
            return result.content[0].text


def call_tool(name: str, arguments: dict | None = None) -> dict | list:
    """Call an MCP tool on the configured server and return the parsed JSON result."""
    raw = asyncio.run(_call_tool_async(name, arguments or {}))
    return json.loads(raw)
