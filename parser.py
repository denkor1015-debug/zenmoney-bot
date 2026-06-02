"""
Natural language → transaction parser using Claude Haiku (Anthropic).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import date
from typing import Any

import anthropic
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a financial transaction parser for a Ukrainian user.
Parse the user message into a list of transactions.
Each transaction must have:
- amount: float (positive number)
- type: "expense" or "income" or "transfer"
- account: string (account name if mentioned, or null for default)
- category: string (one of the available categories below, or null if unclear)
- payee: string (merchant/shop name, or null)
- comment: string (short description in Ukrainian)
- date: "YYYY-MM-DD" (today if not specified)

Today's date: {today}
Available accounts: {account_names}
Available categories: {category_names}

Rules:
- CATEGORY MATCHING is CRITICAL. Match the user's intent to the closest available category above.
  Examples: "кава" → "кофе", "продукти/їжа/магазин" → "продукты", "таксі/убер" → "такси",
  "зарплата" → "зарплата", "АТБ/Сільпо" → match by store name if category exists.
  If user explicitly says "категорія X" or "категория X", use that exact match.
- If user mentions a shop/place name, put it in "payee"
- "comment" should be a short human-readable description in Ukrainian
- For salary/income messages set type="income"
- For transfers between accounts set type="transfer"
- Always return a JSON array, even for a single transaction
- Parse dates flexibly: "вчора"=yesterday, "1 червня"/"1 июня"=June 1, "позавчора"=day before yesterday
- If amount is ambiguous, use the most reasonable interpretation

Return ONLY valid JSON array. No markdown code fences. No explanation.
Example:
[
  {{"amount": 85, "type": "expense", "account": null, "category": "кофе", "payee": "кав'ярня", "comment": "кава", "date": "{today}"}},
  {{"amount": 340, "type": "expense", "account": "готівка", "category": "продукты", "payee": "АТБ", "comment": "продукти АТБ", "date": "{today}"}}
]
"""


class TransactionParser:
    def __init__(self) -> None:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set in environment")
        self._client = anthropic.Anthropic(api_key=api_key)

    async def parse(
        self,
        user_message: str,
        account_names: list[str],
        category_names: list[str] | None = None,
        today: date | None = None,
    ) -> list[dict[str, Any]]:
        """
        Parse a natural-language finance message into a list of transaction dicts.
        Raises ValueError if parsing fails or Claude returns invalid JSON.
        """
        if today is None:
            today = date.today()

        system = SYSTEM_PROMPT.format(
            today=today.isoformat(),
            account_names=", ".join(account_names) if account_names else "не задано",
            category_names=", ".join(category_names) if category_names else "не задано",
        )

        logger.info("Parsing message with Claude Haiku: %r", user_message)

        # anthropic SDK is sync — run in thread
        response = await asyncio.to_thread(
            self._client.messages.create,
            model="claude-haiku-4-5",
            max_tokens=1024,
            system=system,
            messages=[{"role": "user", "content": user_message}],
        )

        raw = response.content[0].text.strip()
        logger.debug("Claude raw response: %s", raw)

        # Strip markdown fences if model adds them despite instructions
        if raw.startswith("```"):
            lines = raw.splitlines()
            raw = "\n".join(
                line for line in lines if not line.startswith("```")
            ).strip()

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.error("Claude returned invalid JSON: %s\nRaw: %s", exc, raw)
            raise ValueError(f"Invalid JSON from Claude: {exc}") from exc

        if not isinstance(parsed, list):
            raise ValueError(f"Expected JSON array, got: {type(parsed).__name__}")

        # Validate / sanitize each transaction
        transactions = []
        for item in parsed:
            if not isinstance(item, dict):
                continue
            txn = {
                "amount": float(item.get("amount", 0)),
                "type": item.get("type", "expense"),
                "account": item.get("account"),
                "category": item.get("category"),
                "payee": item.get("payee"),
                "comment": item.get("comment", ""),
                "date": item.get("date", today.isoformat()),
            }
            if txn["amount"] <= 0:
                logger.warning("Skipping zero/negative amount: %s", item)
                continue
            transactions.append(txn)

        if not transactions:
            raise ValueError("No valid transactions parsed from message")

        logger.info("Parsed %d transaction(s)", len(transactions))
        return transactions
