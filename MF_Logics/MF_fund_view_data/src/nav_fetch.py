"""Fetch NAV history from mfapi.in with on-disk caching."""
from __future__ import annotations

import json
import time
import warnings
from pathlib import Path

import requests

warnings.filterwarnings("ignore")

CACHE_DIR = Path(__file__).resolve().parents[1] / "build" / "cache" / "nav"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

API = "https://api.mfapi.in/mf/{code}"


def fetch_nav(scheme_code: int, force: bool = False) -> dict | None:
    cache_file = CACHE_DIR / f"{scheme_code}.json"
    if cache_file.exists() and not force:
        with open(cache_file) as f:
            return json.load(f)
    for attempt in range(3):
        try:
            r = requests.get(API.format(code=scheme_code), timeout=20)
            if r.status_code == 200:
                j = r.json()
                if j.get("status") == "SUCCESS" and j.get("data"):
                    with open(cache_file, "w") as f:
                        json.dump(j, f)
                    return j
        except Exception as e:
            print(f"  fetch err {scheme_code} attempt {attempt}: {e}")
            time.sleep(1.5 * (attempt + 1))
    return None


if __name__ == "__main__":
    j = fetch_nav(119018)
    print("OK" if j else "FAIL", "data points:", len(j["data"]) if j else 0)
