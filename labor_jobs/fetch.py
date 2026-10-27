"""Download helper with on-disk caching."""

from pathlib import Path

import requests


def download(url, dest, user_agent):
    """Download url to dest unless it is already cached; return the path."""
    dest = Path(dest)
    if dest.exists() and dest.stat().st_size > 0:
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
