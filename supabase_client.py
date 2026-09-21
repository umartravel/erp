"""
Phase SS-1.2 (2026-09-21): Thin wrapper untuk Supabase REST API.

Ini adalah client HTTP async ringan untuk sync Supabase closings ->
UMAR jamaah. Tidak pakai `supabase-py` library (heavy + lock ke API
mereka); langsung call PostgREST endpoint via httpx.

Config resolution (pola sama dgn auth.py:_load_or_create_secret):
1. `SUPABASE_URL` + `SUPABASE_KEY` env var -> priority (prod deploy)
2. File `.supabase_config.json` di CWD:
   {"url": "https://xxx.supabase.co", "key": "eyJhbGc..."}
3. Kalau semua tidak ada -> raise RuntimeError (sync tidak bisa jalan)

Key priority: service_role key > anon key. Service_role bypass RLS,
lebih aman untuk future kalau RLS ditighten. Anon key currently cukup
karena Supabase closings pakai policy public/qual='true' (kritis
security -- akan di-fix di Phase SS-2.4).

.supabase_config.json WAJIB gitignored (sama seperti .jwt_secret).
"""
import json
import logging
import os

import httpx

_log = logging.getLogger("supabase_client")
_CONFIG_FILE = ".supabase_config.json"
_TIMEOUT_SEC = 30.0  # Sync bisa lama untuk big table; 30s adequate


def _load_config() -> dict:
    """Return {'url': ..., 'key': ...} atau raise RuntimeError."""
    env_url = os.environ.get("SUPABASE_URL")
    env_key = os.environ.get("SUPABASE_KEY")
    if env_url and env_key:
        _log.info("Supabase config: env var terpakai (prod deploy).")
        return {"url": env_url.rstrip("/"), "key": env_key}

    if os.path.exists(_CONFIG_FILE):
        try:
            with open(_CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            if not cfg.get("url") or not cfg.get("key"):
                raise RuntimeError(
                    f"{_CONFIG_FILE} malformed: butuh 'url' + 'key' field."
                )
            _log.info("Supabase config: file %s terpakai.", _CONFIG_FILE)
            return {"url": cfg["url"].rstrip("/"), "key": cfg["key"]}
        except (OSError, json.JSONDecodeError) as e:
            raise RuntimeError(f"Gagal baca {_CONFIG_FILE}: {e}") from e

    raise RuntimeError(
        f"Supabase config tidak ada. Set env SUPABASE_URL + SUPABASE_KEY, "
        f"atau tulis file {_CONFIG_FILE} dgn format "
        "{'url': '...', 'key': '...'}. Sync tidak bisa jalan."
    )


def _headers(key: str, extra: dict | None = None) -> dict:
    """Standard PostgREST headers."""
    h = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "count=exact",
    }
    if extra:
        h.update(extra)
    return h


async def query_table(table: str, filter_str: str = "", select: str = "*",
                       order_by: str = "", limit: int | None = None) -> list[dict]:
    """
    SELECT dari table Supabase via PostgREST.

    - `table`: nama tabel di public schema (mis. 'closings')
    - `filter_str`: PostgREST filter syntax raw (mis. 'updated_at=gt.2026-09-01')
    - `select`: kolom (default '*')
    - `order_by`: mis. 'created_at.desc'
    - `limit`: max rows (None = semua)

    Raises: httpx.HTTPStatusError kalau non-2xx.
    """
    cfg = _load_config()
    url = f"{cfg['url']}/rest/v1/{table}"
    params = {"select": select}
    if filter_str:
        for pair in filter_str.split("&"):
            if "=" in pair:
                k, v = pair.split("=", 1)
                params[k] = v
    if order_by:
        params["order"] = order_by
    if limit is not None:
        params["limit"] = str(limit)

    async with httpx.AsyncClient(timeout=_TIMEOUT_SEC) as client:
        r = await client.get(url, headers=_headers(cfg["key"]), params=params)
        r.raise_for_status()
        return r.json()


async def insert_row(table: str, row: dict) -> dict:
    """INSERT row baru ke table Supabase. Return inserted row."""
    cfg = _load_config()
    url = f"{cfg['url']}/rest/v1/{table}"
    async with httpx.AsyncClient(timeout=_TIMEOUT_SEC) as client:
        r = await client.post(
            url,
            headers=_headers(cfg["key"], {"Prefer": "return=representation"}),
            json=row,
        )
        r.raise_for_status()
        data = r.json()
        return data[0] if isinstance(data, list) and data else data


def check_config_available() -> bool:
    """Non-raising check untuk startup. True kalau config bisa di-load."""
    try:
        _load_config()
        return True
    except RuntimeError:
        return False
