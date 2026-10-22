import aiosqlite
from datetime import datetime, timedelta, timezone

DB_NAME = "vozduh.db"


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
                vpn_key TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS payment_issuance (
                payment_id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'processing',
                error_type TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        await db.commit()


async def get_subscription(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM subscriptions WHERE user_id = ?",
            (user_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def save_subscription(
    user_id: int,
    username: str | None,
    tariff: str,
    devices: int,
    days: int,
    vpn_client_id: str | None = None,
    vpn_key: str | None = None,
):
    expires_at = (
        datetime.now(timezone.utc) + timedelta(days=days)
    ).isoformat()

    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            INSERT INTO subscriptions (
                user_id, username, status, tariff, devices,
                expires_at, vpn_client_id, vpn_key
            )
            VALUES (?, ?, 'active', ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                status = 'active',
                tariff = excluded.tariff,
                devices = excluded.devices,
                expires_at = excluded.expires_at,
                vpn_client_id = COALESCE(excluded.vpn_client_id, subscriptions.vpn_client_id),
                vpn_key = COALESCE(excluded.vpn_key, subscriptions.vpn_key)
        """, (
            user_id,
            username,
            tariff,
            devices,
            expires_at,
            vpn_client_id,
            vpn_key,
        ))
        await db.commit()
    return expires_at


async def claim_payment_issuance(payment_id: str, user_id: int) -> bool:
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("""
            INSERT INTO payment_issuance (
                payment_id, user_id, status, created_at, updated_at
            ) VALUES (?, ?, 'processing', ?, ?)
            ON CONFLICT(payment_id) DO NOTHING
        """, (payment_id, user_id, now, now))
        await db.commit()
        return cursor.rowcount == 1


async def finish_payment_issuance(payment_id: str):
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            UPDATE payment_issuance
            SET status = 'completed', error_type = NULL, updated_at = ?
            WHERE payment_id = ?
        """, (now, payment_id))
        await db.commit()


async def fail_payment_issuance(payment_id: str, error_type: str):
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            UPDATE payment_issuance
            SET status = 'error', error_type = ?, updated_at = ?
            WHERE payment_id = ?
        """, (error_type[:80], now, payment_id))
        await db.commit()
