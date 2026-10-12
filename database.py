import aiosqlite
import os
from datetime import datetime, timedelta, timezone

DB_NAME = os.getenv("DB_NAME", "vozduh.db")


async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                status TEXT NOT NULL DEFAULT 'inactive',
                tariff TEXT,
                devices INTEGER,
                expires_at TEXT,
                vpn_client_id TEXT,
                vpn_key TEXT,
                trial_used INTEGER NOT NULL DEFAULT 0
            )
        """)
        await db.commit()


async def get_subscription(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM subscriptions WHERE user_id = ?",
            (user_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def save_subscription(
    user_id: int,
    username: str | None,
    tariff: str,
    devices: int,
    days: int
):
    expires_at = (
        datetime.now(timezone.utc) + timedelta(days=days)
    ).isoformat()

    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            INSERT INTO subscriptions (
                user_id, username, status, tariff,
                devices, expires_at
            )
            VALUES (?, ?, 'active', ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                status = 'active',
                tariff = excluded.tariff,
                devices = excluded.devices,
                expires_at = excluded.expires_at
        """, (
            user_id, username, tariff, devices, expires_at
        ))
        await db.commit()


async def mark_trial_used(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            INSERT INTO subscriptions (user_id, trial_used)
            VALUES (?, 1)
            ON CONFLICT(user_id) DO UPDATE SET
                trial_used = 1
        """, (user_id,))
        await db.commit()


async def is_trial_available(user_id: int) -> bool:
    sub = await get_subscription(user_id)
    if sub is None:
        return True
    return sub.get("trial_used", 0) == 0
