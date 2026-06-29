"""
Encrypt your secrets into secrets.enc (Never #1 — no plaintext keys in files).

Usage:
  1. Generate a master key (store it in your host secret manager, NOT the repo):
       python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
  2. Put it in your shell:  export ENV_MASTER_KEY='...'
  3. Fill secrets.json (gitignored) with the real values, then:
       python scripts/encrypt_env.py secrets.json
  4. Delete secrets.json. At runtime config.py decrypts secrets.enc with ENV_MASTER_KEY.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    if len(sys.argv) != 2:
        print("usage: python scripts/encrypt_env.py <secrets.json>")
        sys.exit(1)
    key = os.getenv("ENV_MASTER_KEY")
    if not key:
        print("ERROR: set ENV_MASTER_KEY first (see this file's docstring).")
        sys.exit(1)
    data = json.loads(Path(sys.argv[1]).read_text())
    token = Fernet(key.encode()).encrypt(json.dumps(data).encode())
    out = ROOT / "secrets.enc"
    out.write_bytes(token)
    print(f"Wrote {out} ({len(token)} bytes). Now delete {sys.argv[1]}.")


if __name__ == "__main__":
    main()
