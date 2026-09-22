#!/usr/bin/env python3
"""Generate local PostgREST secrets without printing them to the terminal."""

import argparse
import base64
import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path


def encode_segment(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def make_token(secret: str, role: str) -> str:
    header = encode_segment(b'{"alg":"HS256","typ":"JWT"}')
    payload = encode_segment(
        json.dumps({"role": role}, separators=(",", ":")).encode("utf-8")
    )
    signing_input = f"{header}.{payload}"
    signature = hmac.new(
        secret.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256
    ).digest()
    return f"{signing_input}.{encode_segment(signature)}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path(".env"))
    args = parser.parse_args()

    output = args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    jwt_secret = secrets.token_urlsafe(48)
    lines = [
        f"POSTGRES_PASSWORD={secrets.token_hex(32)}",
        f"POSTGREST_DB_PASSWORD={secrets.token_hex(32)}",
        f"POSTGREST_JWT_SECRET={jwt_secret}",
        f"SUPABASE_ANON_KEY={make_token(jwt_secret, 'joblab_readonly')}",
        f"SUPABASE_SERVICE_ROLE_KEY={make_token(jwt_secret, 'joblab_writer')}",
        "JOBLAB_POSTGRES_DATA_DIR=/opt/joblab/postgres",
        "",
    ]
    try:
        with output.open("x", encoding="utf-8", newline="\n") as env_file:
            env_file.write("\n".join(lines))
    except FileExistsError:
        raise SystemExit(f"Refusing to overwrite existing file: {output}")

    if os.name == "posix":
        output.chmod(0o600)
    print(f"Created local secrets file: {output}")


if __name__ == "__main__":
    main()