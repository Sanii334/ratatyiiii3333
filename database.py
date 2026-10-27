import os
import aiosqlite
from datetime import datetime, timedelta, timezone

DB_NAME = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vozduh.db")

def utcnow():
    return datetime.now(timezone.utc)

async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""CREATE TABLE IF NOT EXISTS subscriptions (
            user_id INTEGER PRIMARY KEY, username TEXT, status TEXT NOT NULL DEFAULT 'inactive',
            tariff TEXT, devices INTEGER, expires_at TEXT, vpn_client_id TEXT, vpn_key TEXT
        )""")
        await db.execute("""CREATE TABLE IF NOT EXISTS payment_issuance (
            payment_id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'processing',
            error_type TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )""")
        await db.execute("""CREATE TABLE IF NOT EXISTS payments (
            transaction_id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, username TEXT, tariff TEXT NOT NULL,
            devices INTEGER NOT NULL, amount INTEGER NOT NULL, days INTEGER NOT NULL DEFAULT 30,
            status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT 'subscription', target_devices INTEGER
        )""")
        await db.execute("""CREATE TABLE IF NOT EXISTS trial_claims (
            user_id INTEGER PRIMARY KEY, claimed_at TEXT NOT NULL
        )""")
        cur = await db.execute("PRAGMA table_info(payments)")
        cols = {row[1] for row in await cur.fetchall()}
        if "kind" not in cols:
            await db.execute("ALTER TABLE payments ADD COLUMN kind TEXT NOT NULL DEFAULT 'subscription'")
        if "target_devices" not in cols:
            await db.execute("ALTER TABLE payments ADD COLUMN target_devices INTEGER")
        await db.commit()

async def get_subscription(user_id):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM subscriptions WHERE user_id=?", (user_id,))
        row = await cur.fetchone()
        return dict(row) if row else None

async def save_subscription(*, user_id, username, tariff, devices, days, vpn_client_id, vpn_key, expires_at=None):
    expiry = expires_at or (utcnow() + timedelta(days=days)).isoformat()
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""INSERT INTO subscriptions
            (user_id, username, status, tariff, devices, expires_at, vpn_client_id, vpn_key)
            VALUES (?, ?, 'active', ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, status='active',
            tariff=excluded.tariff, devices=excluded.devices, expires_at=excluded.expires_at,
            vpn_client_id=excluded.vpn_client_id, vpn_key=excluded.vpn_key""",
            (user_id, username, tariff, devices, expiry, vpn_client_id, vpn_key))
        await db.commit()
    return expiry

async def save_payment(*, transaction_id, user_id, username, tariff, devices, amount, days,
                       kind="subscription", target_devices=None):
    now = utcnow().isoformat()
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""INSERT OR IGNORE INTO payments
            (transaction_id,user_id,username,tariff,devices,amount,days,status,created_at,updated_at,kind,target_devices)
            VALUES (?,?,?,?,?,?,?,'pending',?,?,?,?)""",
            (transaction_id,user_id,username,tariff,devices,amount,days,now,now,kind,target_devices))
        await db.commit()

async def get_payment(transaction_id):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM payments WHERE transaction_id=?", (transaction_id,))
        row = await cur.fetchone()
        return dict(row) if row else None

async def get_pending_payments():
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM payments WHERE status IN ('pending','confirmed','processing') ORDER BY created_at")
        return [dict(row) for row in await cur.fetchall()]

async def update_payment_status(transaction_id, status):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE payments SET status=?, updated_at=? WHERE transaction_id=?",
                         (status, utcnow().isoformat(), transaction_id))
        await db.commit()

async def claim_payment_issuance(payment_id, user_id):
    now = utcnow().isoformat()
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT OR IGNORE INTO payment_issuance(payment_id,user_id,status,created_at,updated_at) VALUES(?,?,'processing',?,?)",
                         (payment_id,user_id,now,now))
        cur = await db.execute("SELECT user_id,status FROM payment_issuance WHERE payment_id=?", (payment_id,))
        row = await cur.fetchone()
        if not row or int(row[0]) != int(user_id) or row[1] == "completed":
            await db.commit()
            return False
        # Retry failed/incomplete work; H1 client lookup guards ordinary duplicate creation.
        await db.execute("UPDATE payment_issuance SET status='processing',updated_at=? WHERE payment_id=?", (now,payment_id))
        await db.commit()
        return True

async def finish_payment_issuance(payment_id):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE payment_issuance SET status='completed',error_type=NULL,updated_at=? WHERE payment_id=?",
                         (utcnow().isoformat(),payment_id))
        await db.commit()

async def fail_payment_issuance(payment_id, error_type):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE payment_issuance SET status='failed',error_type=?,updated_at=? WHERE payment_id=?",
                         (str(error_type)[:100],utcnow().isoformat(),payment_id))
        await db.commit()

async def claim_trial(user_id):
    async with aiosqlite.connect(DB_NAME) as db:
        try:
            await db.execute("INSERT INTO trial_claims(user_id,claimed_at) VALUES(?,?)",
                             (user_id,utcnow().isoformat()))
            await db.commit()
            return True
        except aiosqlite.IntegrityError:
            return False
