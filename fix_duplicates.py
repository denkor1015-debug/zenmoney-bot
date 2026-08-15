"""
Прибирає транзакції-сироти від пробного прогону `--limit 5 --send`.

Проблема: другий запуск (--send без --rollback) перезаписав payload.json
новими id, тому штатний --rollback про пробну пʼятірку вже не знає.

Скрипт шукає копії пробних транзакцій, яких немає в payload.json, і видаляє
саме їх. Без --confirm нічого не чіпає, тільки показує знайдене.

    python3 fix_duplicates.py             # показати
    python3 fix_duplicates.py --confirm   # видалити
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

from zenmoney import ZenMoneyClient

BASE = Path(__file__).parent
OUT = BASE / "import_out"
TRIAL_COUNT = 5


async def fetch_all() -> list[dict]:
    client = ZenMoneyClient()
    data = await client._post("/v8/diff/", {
        "currentClientTimestamp": int(time.time()),
        "serverTimestamp": 0,
    })
    return [t for t in data.get("transaction", []) if not t.get("deleted")]


def find_orphans(txns: list[dict]) -> list[dict]:
    known = {p["id"] for p in json.load(open(OUT / "payload.json"))}
    trial = json.load(open(OUT / "items.json"))[:TRIAL_COUNT]
    orphans = []
    for t in trial:
        same = [
            x for x in txns
            if x["date"] == t["date"]
            and abs((x["outcome"] or x["income"]) - t["amount"]) < 0.01
            and (x.get("comment") or "")[:40] == t["desc"][:40]
        ]
        extra = [x for x in same if x["id"] not in known]
        print(f"  {t['date']}  {t['amount']:>9,.2f}  {t['desc'][:42]:44} "
              f"копій {len(same)}, зайвих {len(extra)}")
        if len(same) != 2 or len(extra) != 1:
            print("    ⚠️  очікувалось рівно 2 копії й 1 зайва — пропускаю, "
                  "розберись вручну")
            continue
        orphans += extra
    return orphans


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--confirm", action="store_true",
                    help="реально видалити знайдені дублі")
    args = ap.parse_args()

    print("Шукаю дублі пробного прогону …")
    txns = await fetch_all()
    print(f"транзакцій у ZenMoney: {len(txns)}\n")
    orphans = find_orphans(txns)

    print(f"\nДо видалення: {len(orphans)} транзакцій на "
          f"{sum(o['outcome'] or o['income'] for o in orphans):,.2f} ₴")
    for o in orphans:
        print(f"  {o['id']}  {o['date']}  "
              f"{o['outcome'] or o['income']:>9,.2f}  "
              f"{(o.get('comment') or '')[:44]}")

    if not orphans:
        print("\nНічого видаляти — дублів немає.")
        return

    if not args.confirm:
        print("\nНічого не видалено. Щоб видалити: python3 fix_duplicates.py --confirm")
        return

    now = int(time.time())
    for o in orphans:
        o["deleted"] = True
        o["changed"] = now
    await ZenMoneyClient().add_transactions(orphans)
    json.dump(orphans, open(OUT / "deleted_dupes.json", "w"),
              ensure_ascii=False, indent=1)
    print(f"\nВидалено {len(orphans)}. Список — у import_out/deleted_dupes.json")


if __name__ == "__main__":
    asyncio.run(main())
