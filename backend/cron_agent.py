#!/usr/bin/env python3
"""Cron entrypoint for the autonomous agent loop.

Install (market hours, every 15 min, Mon-Fri):
  */15 9-16 * * 1-5  cd /app/backend && python cron_agent.py >> /var/log/agent_cron.log 2>&1
"""
import asyncio
from database import db
from alpaca import make_alpaca
from agent import run_cycle


async def main():
    alpaca = make_alpaca(db)
    await alpaca.ensure_seed()
    result = await run_cycle(db, alpaca, force=False)
    print(f"[cron] cycle {result.get('cycle_id')} status={result.get('status')} "
          f"decisions={len(result.get('decisions', []))}")


if __name__ == "__main__":
    asyncio.run(main())
