"""Convert the local .streamlit/secrets.toml + mcp_tokens.json OAuth cache
into a Streamlit-secrets TOML block, so a Community Cloud deploy (which has
no browser and no persistent filesystem) can be seeded with a working
session instead of running the interactive OAuth flow.

Usage:
    uv run python export_mcp_secrets.py

Then open the deployed app's Settings -> Secrets in Streamlit Community
Cloud and paste the contents of .streamlit/mcp_tokens_secrets.toml in.

Note: if the OAuth provider rotates refresh tokens on use, the container
will need this re-run and re-pasted after the seeded refresh token is
consumed and the app is redeployed/restarted (a running instance keeps
refreshing fine on its own local disk in the meantime).
"""

import json
import tomllib
from pathlib import Path

SECRETS_FILE = Path(__file__).parent / ".streamlit" / "secrets.toml"
TOKEN_FILE = Path(__file__).parent / ".streamlit" / "mcp_tokens.json"
OUTPUT_FILE = Path(__file__).parent / ".streamlit" / "mcp_tokens_secrets.toml"


def _toml_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    return json.dumps(str(value))


def _toml_table(name: str, fields: dict) -> str:
    lines = [f"[{name}]"]
    for key, value in fields.items():
        if value is None:
            continue  # TOML has no null; omit unset fields
        lines.append(f"{key} = {_toml_value(value)}")
    return "\n".join(lines)


def main() -> None:
    if not TOKEN_FILE.exists():
        raise SystemExit(
            f"{TOKEN_FILE} not found. Run the dashboard locally first so it can "
            "complete the OAuth login and write this file."
        )
    if not SECRETS_FILE.exists():
        raise SystemExit(f"{SECRETS_FILE} not found. Add mcp_server_url to it first.")

    local_secrets = tomllib.loads(SECRETS_FILE.read_text())
    server_url = local_secrets.get("mcp_server_url")
    if not server_url:
        raise SystemExit(f"'mcp_server_url' not set in {SECRETS_FILE}.")

    data = json.loads(TOKEN_FILE.read_text())
    blocks = [
        f"mcp_server_url = {_toml_value(server_url)}",
        _toml_table("mcp_tokens.client_info", data["client_info"]),
        _toml_table("mcp_tokens.tokens", data["tokens"]),
    ]
    OUTPUT_FILE.write_text("\n\n".join(blocks) + "\n")
    print(f"Wrote {OUTPUT_FILE}")
    print("Paste its contents into the app's Secrets settings on Streamlit Community Cloud.")


if __name__ == "__main__":
    main()
