"""Download helper with on-disk caching."""

import time
from pathlib import Path

import requests


def download(url, dest, user_agent, max_age_days=None):
    """Download url to dest unless it is already cached (and, with max_age_days,
    no older than that); return the path."""
    dest = Path(dest)
    fresh = max_age_days is None or time.time() - dest.stat().st_mtime < max_age_days * 86400 if dest.exists() else False
    if dest.exists() and dest.stat().st_size > 0 and fresh:
        return dest
    print(f"  downloading {url}")
    resp = requests.get(url, headers={"User-Agent": user_agent}, timeout=300)
    if resp.status_code == 403:
        raise RuntimeError(
            f"{url} refused the download (HTTP 403). bls.gov requires a "
            "User-Agent with contact details: pass --contact-email. Or download "
            "the file in a browser and pass its local path instead."
        )
    resp.raise_for_status()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(resp.content)
    return dest
