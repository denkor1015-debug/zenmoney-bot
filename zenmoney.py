"""
ZenMoney API client — async, using httpx.
Endpoints used:
  POST /v8/diff/     — read/write all data
  POST /v8/suggest/  — category suggestion by payee name
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://api.zenmoney.ru"
STATE_FILE = Path("state.json")
_TMP_STATE = Path("state.json.tmp")

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------

def _load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text("utf-8"))
    return {
        "serverTimestamp": 0,
        "userId": None,
        "accounts": {},
        "defaultAccount": None,
        "tags": {},
    }


def _save_state(state: dict) -> None:
    """Atomic write: write to .tmp, then rename — safe against crash mid-write."""
    data = json.dumps(state, ensure_ascii=False, indent=2)
    _TMP_STATE.write_text(data, encoding="utf-8")
    _TMP_STATE.replace(STATE_FILE)


def load_state() -> dict:
    return _load_state()


def save_state(state: dict) -> None:
    _save_state(state)


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class ZenMoneyClient:
    def __init__(self) -> None:
        token = os.getenv("ZENMONEY_TOKEN")
        if not token:
            raise RuntimeError("ZENMONEY_TOKEN is not set in environment")
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _post(self, path: str, body: dict) -> dict:
        url = f"{BASE_URL}{path}"
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, json=body, headers=self._headers)
        if resp.status_code != 200:
            logger.error(
                "ZenMoney API error %d for %s: %s",
                resp.status_code, path, resp.text[:500],
            )
            resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def fetch_initial_data(self) -> dict:
        """
        Full sync from ZenMoney (serverTimestamp=0 → returns everything).
        Parses accounts, tags, userId and saves to state.json.
        Returns the updated state dict.
        """
        state = _load_state()
        body = {
            "currentClientTimestamp": int(time.time()),
            "serverTimestamp": 0,
        }
        logger.info("Fetching initial data from ZenMoney …")
        data = await self._post("/v8/diff/", body)

        state["serverTimestamp"] = data.get("serverTimestamp", 0)

        # userId — first user object
        users = data.get("user", [])
        if users:
            state["userId"] = users[0]["id"]

        # Accounts — index by lowercase title
        accounts: dict[str, Any] = {}
        default_account: str | None = None
        for acc in data.get("account", []):
            if acc.get("archive") or acc.get("deleted"):
                continue
            key = acc["title"].lower().strip()
            accounts[key] = {
                "id": acc["id"],
                "instrument": acc["instrument"],
                "title": acc["title"],
                "balance": acc.get("balance", 0),
            }
            if default_account is None:
                default_account = key

        state["accounts"] = accounts
        if state.get("defaultAccount") not in accounts:
            state["defaultAccount"] = default_account

        # Tags — store with hierarchy info
        # First pass: build id→title map
        id_to_title: dict[str, str] = {}
        for tag in data.get("tag", []):
            if tag.get("deleted"):
                continue
            id_to_title[tag["id"]] = tag["title"].strip()

        # Second pass: build tags dict with parent info
        tags: dict[str, dict] = {}
        for tag in data.get("tag", []):
            if tag.get("deleted"):
                continue
            key = tag["title"].lower().strip()
            parent_id = tag.get("parent")
            parent_title = id_to_title.get(parent_id, "").lower().strip() if parent_id else None
            tags[key] = {
                "id": tag["id"],
                "parent": parent_id,
                "parent_title": parent_title,
            }
        state["tags"] = tags

        _save_state(state)
        logger.info(
            "Fetched: %d accounts, %d tags, userId=%s",
            len(accounts),
            len(tags),
            state["userId"],
        )
        return state

    async def suggest(self, payee: str) -> dict:
        """
        Call ZenMoney /v8/suggest/ to get tag/merchant suggestions for a payee.
        Returns raw dict from API; keys: 'tag', 'merchant', etc.
        Returns {} on any error.
        """
        body = {
            "currentClientTimestamp": int(time.time()),
            "payee": payee,
        }
        try:
            data = await self._post("/v8/suggest/", body)
            return data
        except Exception as exc:
            logger.warning("suggest() failed for payee=%r: %s", payee, exc)
            return {}

    async def add_transactions(self, txns: list[dict]) -> bool:
        """
        Write a list of transaction dicts to ZenMoney via /v8/diff/.
        Each txn must be a fully-formed ZenMoney transaction object.
        Returns True on success.
        """
        state = _load_state()
        body = {
            "currentClientTimestamp": int(time.time()),
            "serverTimestamp": state.get("serverTimestamp", 0),
            "transaction": txns,
        }
        logger.info("Saving %d transaction(s) to ZenMoney …", len(txns))
        logger.debug("Transaction body: %s", json.dumps(txns, ensure_ascii=False, indent=2))
        data = await self._post("/v8/diff/", body)
        # Update serverTimestamp
        if "serverTimestamp" in data:
            state["serverTimestamp"] = data["serverTimestamp"]
            _save_state(state)
        return True

    async def get_balances(self) -> list[dict]:
        """
        Returns a list of non-archived accounts with current balances.
        Uses a fresh diff call to get up-to-date data.
        """
        state = _load_state()
        body = {
            "currentClientTimestamp": int(time.time()),
            "serverTimestamp": state.get("serverTimestamp", 0),
        }
        data = await self._post("/v8/diff/", body)

        if "serverTimestamp" in data:
            state["serverTimestamp"] = data["serverTimestamp"]
            _save_state(state)

        accounts = data.get("account", [])
        result = []
        for acc in accounts:
            if acc.get("archive") or acc.get("deleted"):
                continue
            result.append({
                "title": acc["title"],
                "balance": acc.get("balance", 0),
                "instrument": acc.get("instrument"),
            })
        return result
