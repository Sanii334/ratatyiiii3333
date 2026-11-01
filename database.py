
import os
import aiosqlite
from datetime import datetime, timedelta, timezone

DB_NAME = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "vozduh.db"
)

ISSUANCE_LEASE_MINUTES = 10


def utcnow():
    return datetime.now(timezone.utc)


async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA busy_timeout=5000")

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

        await db.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                transaction_id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                username TEXT,
                tariff TEXT NOT NULL,
                devices INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                days INTEGER NOT NULL DEFAULT 30,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'subscription',
                target_devices INTEGER
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS trial_claims (
                user_id INTEGER PRIMARY KEY,
                claimed_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'claimed'
            )
        """)

        cur = await db.execute("PRAGMA table_info(payments)")
        payment_columns = {
            row[1] for row in await cur.fetchall()
        }

        if "kind" not in payment_columns:
            await db.execute("""
                ALTER TABLE payments
                ADD COLUMN kind TEXT NOT NULL DEFAULT 'subscription'
            """)

        if "target_devices" not in payment_columns:
            await db.execute("""
                ALTER TABLE payments
                ADD COLUMN target_devices INTEGER
            """)

        cur = await db.execute("PRAGMA table_info(trial_claims)")
        trial_columns = {
            row[1] for row in await cur.fetchall()
        }

        if "status" not in trial_columns:
            await db.execute("""
                ALTER TABLE trial_claims
                ADD COLUMN status TEXT NOT NULL DEFAULT 'claimed'
            """)

        await db.commit()


async def get_subscription(user_id):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row

        cur = await db.execute(
            "SELECT * FROM subscriptions WHERE user_id = ?",
            (user_id,)
        )

        row = await cur.fetchone()

        return dict(row) if row else None


async def save_subscription(
    *,
    user_id,
    username,
    tariff,
    devices,
    days,
    vpn_client_id,
    vpn_key,
    expires_at=None
):
    expiry = expires_at or (
        utcnow() + timedelta(days=days)
    ).isoformat()

    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            INSERT INTO subscriptions (
                user_id,
                username,
                status,
                tariff,
                devices,
                expires_at,
                vpn_client_id,
                vpn_key
            )
            VALUES (?, ?, 'active', ?, ?, ?, ?, ?)

            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                status = 'active',
                tariff = excluded.tariff,
                devices = excluded.devices,
                expires_at = excluded.expires_at,
                vpn_client_id = excluded.vpn_client_id,
                vpn_key = excluded.vpn_key
        """, (
            user_id,
            username,
            tariff,
            devices,
            expiry,
            vpn_client_id,
            vpn_key
        ))

        await db.commit()

    return expiry


async def upgrade_subscription_devices(
    user_id,
    *,
    new_tariff,
    new_devices
):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            UPDATE subscriptions
            SET tariff = ?, devices = ?
            WHERE user_id = ?
        """, (
            new_tariff,
            new_devices,
            user_id
        ))

        await db.commit()


async def save_payment(
    *,
    transaction_id,
    user_id,
    username,
    tariff,
    devices,
    amount,
    days,
    kind="subscription",
    target_devices=None
):
    now = utcnow().isoformat()

    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            INSERT OR IGNORE INTO payments (
                transaction_id,
                user_id,
                username,
                tariff,
                devices,
                amount,
                days,
                status,
                created_at,
                updated_at,
                kind,
                target_devices
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)
        """, (
            transaction_id,
            user_id,
            username,
            tariff,
            devices,
            amount,
            days,
            now,
            now,
            kind,
            target_devices
        ))

        cur = await db.execute("""
            SELECT user_id, amount
            FROM payments
            WHERE transaction_id = ?
        """, (transaction_id,))

        row = await cur.fetchone()

        if (
            not row
            or int(row[0]) != int(user_id)
            or int(row[1]) != int(amount)
        ):
            raise RuntimeError(
                "Не удалось безопасно сохранить платёж"
            )

        await db.commit()


async def get_payment(transaction_id):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row

        cur = await db.execute(
            "SELECT * FROM payments WHERE transaction_id = ?",
            (transaction_id,)
        )

        row = await cur.fetchone()

        return dict(row) if row else None


async def get_pending_payments():
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row

        cur = await db.execute("""
            SELECT *
            FROM payments
            WHERE status IN (
                'pending',
                'confirmed',
                'processing'
            )
            ORDER BY created_at
        """)

        return [
            dict(row)
            for row in await cur.fetchall()
        ]


async def update_payment_status(transaction_id, status):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            UPDATE payments
            SET status = ?, updated_at = ?
            WHERE transaction_id = ?
        """, (
            status,
            utcnow().isoformat(),
            transaction_id
        ))

        await db.commit()


async def get_payment_issuance_status(payment_id):
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute("""
            SELECT status
            FROM payment_issuance
            WHERE payment_id = ?
        """, (payment_id,))

        row = await cur.fetchone()

        return row[0] if row else None


async def claim_payment_issuance(payment_id: int, user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")

        cursor = await db.execute(
            """
            SELECT status, updated_at
            FROM payment_issuance
            WHERE payment_id = ? AND user_id = ?
            """,
            (payment_id, user_id)
        )
        row = await cursor.fetchone()

        if row is None:
            await db.execute(
                """
                INSERT INTO payment_issuance
                (payment_id, user_id, status, created_at, updated_at)
                VALUES (?, ?, 'processing', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (payment_id, user_id)
            )
            await db.commit()
            return True

        status, updated_at = row

        if status == "failed" or (
            status == "processing"
            and updated_at < await _get_stale_timestamp(db)
        ):
            await db.execute(
                """
                UPDATE payment_issuance
                SET status = 'processing',
                    updated_at = CURRENT_TIMESTAMP,
                    error_type = NULL
                WHERE payment_id = ? AND user_id = ?
                """,
                (payment_id, user_id)
            )
            await db.commit()
            return True

        await db.commit()
        return False

async def fail_payment_issuance(payment_id, error_type):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            UPDATE payment_issuance
            SET
                status = 'failed',
                error_type = ?,
                updated_at = ?
            WHERE
                payment_id = ?
                AND status != 'completed'
        """, (
            str(error_type)[:100],
            utcnow().isoformat(),
            payment_id
        ))

        await db.commit()


async def claim_trial(user_id):
    now = utcnow()

    now_text = now.isoformat()

    stale_before = (
        now - timedelta(minutes=ISSUANCE_LEASE_MINUTES)
    ).isoformat()

    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("PRAGMA busy_timeout=5000")
        await db.execute("BEGIN IMMEDIATE")

        try:
            await db.execute("""
                INSERT OR IGNORE INTO trial_claims (
                    user_id,
                    claimed_at,
                    status
                )
                VALUES (?, ?, 'processing')
            """, (
                user_id,
                now_text
            ))

            cur = await db.execute("""
                SELECT claimed_at, status
                FROM trial_claims
                WHERE user_id = ?
            """, (user_id,))

            row = await cur.fetchone()

            if not row:
                await db.commit()
                return False

            if row[1] != "processing":
                await db.commit()
                return False

            if (row[0] or "") >= stale_before:
                await db.commit()
                return False

            await db.execute("""
                UPDATE trial_claims
                SET claimed_at = ?, status = 'processing'
                WHERE user_id = ? AND status = 'processing'
            """, (
                now_text,
                user_id
            ))

            await db.commit()

            return True

        except Exception:
            await db.rollback()
            raise


async def finish_trial_claim(user_id):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            UPDATE trial_claims
            SET
                status = 'claimed',
                claimed_at = ?
            WHERE user_id = ?
        """, (
            utcnow().isoformat(),
            user_id
        ))

        await db.commit()


async def cancel_trial_claim(user_id):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            DELETE FROM trial_claims
            WHERE user_id = ?
            AND status = 'processing'
        """, (user_id,))

        await db.commit()
