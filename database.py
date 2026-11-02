import os
import re
import secrets
import asyncio
from pathlib import Path
from typing import Any, List, Optional, Sequence
import asyncpg
from config import DATABASE_URL, DB_POOL_MIN_SIZE, DB_POOL_MAX_SIZE, PG_HOST, PG_PORT, PG_USER, PG_PASSWORD, PG_DATABASE

_pool: Optional[asyncpg.Pool] = None
_pool_lock = asyncio.Lock()

def convert_placeholders(sql: str) -> str:
    """
    Converts SQLite ? placeholders to PostgreSQL $1, $2, ... placeholders,
    ignoring ? inside string literals. Also translates common SQLite syntax.
    """
    # Replace SQLite datetime idioms
    sql = re.sub(r"datetime\('now',\s*'localtime'\)", "NOW()", sql, flags=re.IGNORECASE)
    sql = re.sub(r"datetime\('now'\)", "NOW()", sql, flags=re.IGNORECASE)

    # Convert ? to $1, $2, ...
    parts = []
    param_idx = 1
    in_quote = False
    quote_char = ''
    i = 0
    n = len(sql)

    while i < n:
        ch = sql[i]
        if ch in ("'", '"'):
            if not in_quote:
                in_quote = True
                quote_char = ch
            elif quote_char == ch:
                in_quote = False
            parts.append(ch)
        elif ch == '?' and not in_quote:
            parts.append(f"${param_idx}")
            param_idx += 1
        else:
            parts.append(ch)
        i += 1

    return "".join(parts)


class PgCursor:
    def __init__(self, conn: "PgConnection"):
        self.conn = conn
        self._results: List[Any] = []
        self._idx = 0
        self.rowcount = 0

    async def execute(self, query: str, params: Sequence[Any] = ()):
        query = query.strip()
        if not query:
            return self

        # Handle SQLite-style transaction commands
        if query.upper() in ("BEGIN", "BEGIN IMMEDIATE", "BEGIN TRANSACTION"):
            await self.conn.ensure_transaction()
            return self
        if query.upper() in ("COMMIT", "COMMIT TRANSACTION"):
            await self.conn.commit()
            return self
        if query.upper() in ("ROLLBACK", "ROLLBACK TRANSACTION"):
            await self.conn.rollback()
            return self
        if query.upper().startswith("PRAGMA"):
            return self

        converted_sql = convert_placeholders(query)
        params_list = list(params) if params else []

        raw_conn = self.conn.raw_conn
        # Determine if statement returns records
        is_select = converted_sql.lstrip().upper().startswith("SELECT") or "RETURNING" in converted_sql.upper() or "WITH " in converted_sql.lstrip().upper()

        if is_select:
            rows = await raw_conn.fetch(converted_sql, *params_list)
            self._results = rows
            self._idx = 0
            self.rowcount = len(rows)
        else:
            status = await raw_conn.execute(converted_sql, *params_list)
            self._results = []
            self._idx = 0
            # Parse row count from asyncpg status, e.g. "UPDATE 5" -> 5
            try:
                parts = status.split()
                self.rowcount = int(parts[-1]) if parts and parts[-1].isdigit() else 0
            except Exception:
                self.rowcount = 0
        return self

    async def executemany(self, query: str, seq_of_params: Sequence[Sequence[Any]]):
        converted_sql = convert_placeholders(query)
        raw_conn = self.conn.raw_conn
        total = 0
        for params in seq_of_params:
            status = await raw_conn.execute(converted_sql, *list(params))
            try:
                parts = status.split()
                if parts and parts[-1].isdigit():
                    total += int(parts[-1])
            except Exception:
                pass
        self.rowcount = total
        self._results = []
        self._idx = 0
        return self

    async def fetchone(self) -> Optional[Any]:
        if self._idx < len(self._results):
            row = self._results[self._idx]
            self._idx += 1
            return row
        return None

    async def fetchall(self) -> List[Any]:
        if self._idx < len(self._results):
            res = self._results[self._idx:]
            self._idx = len(self._results)
            return res
        return []


class PgConnection:
    def __init__(self, raw_conn: asyncpg.Connection):
        self.raw_conn = raw_conn
        self._tx: Optional[asyncpg.connection.transaction.Transaction] = None

    async def cursor(self) -> PgCursor:
        return PgCursor(self)

    async def ensure_transaction(self):
        if self._tx is None:
            self._tx = self.raw_conn.transaction()
            await self._tx.start()

    async def commit(self):
        if self._tx is not None:
            await self._tx.commit()
            self._tx = None

    async def rollback(self):
        if self._tx is not None:
            await self._tx.rollback()
            self._tx = None

    async def execute(self, query: str, *params):
        c = await self.cursor()
        await c.execute(query, params)
        return c


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        async with _pool_lock:
            if _pool is None:
                try:
                    _pool = await asyncpg.create_pool(
                        dsn=DATABASE_URL,
                        min_size=DB_POOL_MIN_SIZE,
                        max_size=DB_POOL_MAX_SIZE
                    )
                except Exception as e:
                    # Attempt to create database if it doesn't exist
                    err_msg = str(e).lower()
                    if "database" in err_msg and ("does not exist" in err_msg or "не существует" in err_msg):
                        admin_dsn = f"postgresql://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/postgres"
                        admin_conn = await asyncpg.connect(admin_dsn)
                        try:
                            await admin_conn.execute(f'CREATE DATABASE "{PG_DATABASE}"')
                            print(f"[DB] Created database '{PG_DATABASE}' successfully.")
                        finally:
                            await admin_conn.close()

                        _pool = await asyncpg.create_pool(
                            dsn=DATABASE_URL,
                            min_size=DB_POOL_MIN_SIZE,
                            max_size=DB_POOL_MAX_SIZE
                        )
                    else:
                        raise e
    return _pool


async def get_db():
    pool = await get_pool()
    async with pool.acquire() as raw_conn:
        conn_wrapper = PgConnection(raw_conn)
        try:
            yield conn_wrapper
        finally:
            if conn_wrapper._tx is not None:
                try:
                    await conn_wrapper.rollback()
                except Exception:
                    pass


async def init_db():
    pool = await get_pool()
    async with pool.acquire() as raw_conn:
        conn = PgConnection(raw_conn)
        c = await conn.cursor()

        await c.execute("""
            CREATE TABLE IF NOT EXISTS DbVersion (
                Revision INTEGER DEFAULT 0,
                AdminRevision INTEGER DEFAULT 0
            )
        """)

        await c.execute("""
            CREATE TABLE IF NOT EXISTS Entities (
                Id TEXT PRIMARY KEY,
                FolderId TEXT DEFAULT '',
                EncryptedData TEXT,
                Deleted INTEGER DEFAULT 0,
                Revision INTEGER DEFAULT 0,
                DeletedAt TIMESTAMP WITH TIME ZONE
            )
        """)

        await c.execute("""
            CREATE TABLE IF NOT EXISTS Users (
                Id TEXT PRIMARY KEY,
                Username TEXT,
                Email TEXT,
                FirstConnect TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                LastConnect TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                IsApproved INTEGER DEFAULT 0,
                IsActive INTEGER DEFAULT 1
            )
        """)

        await c.execute("""
            CREATE TABLE IF NOT EXISTS LocalTokens (
                Id TEXT PRIMARY KEY,
                TokenHash TEXT,
                Description TEXT,
                ExpiresAt TIMESTAMP WITH TIME ZONE,
                CreatedAt TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                IsActive INTEGER DEFAULT 1
            )
        """)

        await c.execute("""
            CREATE TABLE IF NOT EXISTS Groups (
                Id TEXT PRIMARY KEY,
                Name TEXT,
                IsSuperAdmin INTEGER DEFAULT 0,
                CanManageUsers INTEGER DEFAULT 0,
                CanSaveLocal INTEGER DEFAULT 0,
                CanAdd INTEGER DEFAULT 0,
                CanEdit INTEGER DEFAULT 0,
                CanDelete INTEGER DEFAULT 0,
                CanBulkEdit INTEGER DEFAULT 0,
                CanReadLog INTEGER DEFAULT 0,
                CanManageRoles INTEGER DEFAULT 0,
                CanManageSettings INTEGER DEFAULT 0,
                IsHidden INTEGER DEFAULT 0,
                IsDeleted INTEGER DEFAULT 0
            )
        """)

        await c.execute("""
            CREATE TABLE IF NOT EXISTS UserGroups (
                UserId TEXT,
                GroupId TEXT,
                PRIMARY KEY(UserId, GroupId)
            )
        """)

        await c.execute("""
            CREATE TABLE IF NOT EXISTS EntityPermissions (
                EntityId TEXT,
                GroupId TEXT,
                AccessLevel TEXT,
                PRIMARY KEY(EntityId, GroupId)
            )
        """)

        await c.execute("""
            CREATE TABLE IF NOT EXISTS ServerSettings (
                Key TEXT PRIMARY KEY,
                Value TEXT
            )
        """)

        await c.execute("""
            CREATE TABLE IF NOT EXISTS AuditLog (
                Id SERIAL PRIMARY KEY,
                Timestamp TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                EventType TEXT,
                Username TEXT,
                Email TEXT,
                IpAddress TEXT,
                Details TEXT
            )
        """)

        await c.execute("""
            CREATE TABLE IF NOT EXISTS DeletedEntities (
                Id TEXT PRIMARY KEY,
                Revision INTEGER DEFAULT 0
            )
        """)

        # Migration columns if existing tables lack them
        try: await c.execute("ALTER TABLE Groups ADD COLUMN IF NOT EXISTS CanBulkEdit INTEGER DEFAULT 0")
        except: pass
        try: await c.execute("ALTER TABLE Users ADD COLUMN IF NOT EXISTS IsActive INTEGER DEFAULT 1")
        except: pass
        try: await c.execute("ALTER TABLE LocalTokens ADD COLUMN IF NOT EXISTS IsActive INTEGER DEFAULT 1")
        except: pass
        try: await c.execute("ALTER TABLE DbVersion ADD COLUMN IF NOT EXISTS AdminRevision INTEGER DEFAULT 0")
        except: pass
        try: await c.execute("ALTER TABLE Entities ADD COLUMN IF NOT EXISTS DeletedAt TIMESTAMP WITH TIME ZONE")
        except: pass

        # DbVersion default row
        await c.execute("SELECT Revision FROM DbVersion LIMIT 1")
        if not await c.fetchone():
            await c.execute("INSERT INTO DbVersion (Revision, AdminRevision) VALUES (0, 0)")

        # Groups default records (Admin has CanBulkEdit=1)
        await c.execute("SELECT 1 FROM Groups WHERE Id = 'admin_group'")
        if not await c.fetchone():
            await c.execute("""
                INSERT INTO Groups (Id, Name, IsSuperAdmin, CanBulkEdit) 
                VALUES ('admin_group', 'Администратор', 1, 1)
                ON CONFLICT (Id) DO NOTHING
            """)
            await c.execute("""
                INSERT INTO Groups (Id, Name, CanAdd, CanEdit, CanDelete, CanManageUsers, CanBulkEdit) 
                VALUES ('moder_group', 'Модератор', 1, 1, 1, 1, 0)
                ON CONFLICT (Id) DO NOTHING
            """)
            await c.execute("""
                INSERT INTO Groups (Id, Name, CanAdd, CanEdit, CanBulkEdit) 
                VALUES ('user_group', 'Пользователь', 1, 1, 0)
                ON CONFLICT (Id) DO NOTHING
            """)
            await c.execute("""
                INSERT INTO Groups (Id, Name, CanBulkEdit) 
                VALUES ('new_user_group', 'Новый пользователь', 0)
                ON CONFLICT (Id) DO NOTHING
            """)
            await c.execute("""
                INSERT INTO Groups (Id, Name, IsHidden, CanBulkEdit) 
                VALUES ('no_rights_group', 'Без прав', 1, 0)
                ON CONFLICT (Id) DO NOTHING
            """)

        # Default server settings
        await c.execute("INSERT INTO ServerSettings (Key, Value) VALUES ('AuditRetentionDays', '90') ON CONFLICT (Key) DO NOTHING")
        await c.execute("INSERT INTO ServerSettings (Key, Value) VALUES ('DeletedRetentionDays', '30') ON CONFLICT (Key) DO NOTHING")
        await c.execute("INSERT INTO ServerSettings (Key, Value) VALUES ('DefaultGroupId', 'new_user_group') ON CONFLICT (Key) DO NOTHING")

        # MasterKey
        await c.execute("SELECT Value FROM ServerSettings WHERE Key = 'MasterKey'")
        if not await c.fetchone():
            master_key = secrets.token_urlsafe(32)
            await c.execute("INSERT INTO ServerSettings (Key, Value) VALUES ('MasterKey', ?) ON CONFLICT (Key) DO NOTHING", (master_key,))

        # Normalize access levels
        try:
            await c.execute("UPDATE EntityPermissions SET AccessLevel = 'none' WHERE AccessLevel = 'Нет доступа'")
            await c.execute("UPDATE EntityPermissions SET AccessLevel = 'read' WHERE AccessLevel = 'Чтение'")
            await c.execute("UPDATE EntityPermissions SET AccessLevel = 'write' WHERE AccessLevel = 'Чтение / Запись'")
        except Exception as e:
            print(f"Permissions normalize error: {e}")

        # Check for legacy SQLite database to import if PostgreSQL is empty
        await c.execute("SELECT COUNT(*) FROM Entities")
        ent_count_row = await c.fetchone()
        ent_count = ent_count_row[0] if ent_count_row else 0
        
        old_sqlite_candidates = [Path("data/database.db"), Path("database.db")]
        for old_sqlite in old_sqlite_candidates:
            if old_sqlite.exists() and ent_count == 0:
                print(f"[DB MIGRATION] Found SQLite database {old_sqlite}. Migrating to PostgreSQL...")
                try:
                    import sqlite3
                    with sqlite3.connect(old_sqlite) as sq_conn:
                        sq_c = sq_conn.cursor()
                        # Entities
                        try:
                            sq_c.execute("SELECT Id, FolderId, EncryptedData, Deleted, Revision FROM Entities")
                            for row in sq_c.fetchall():
                                await c.execute("""
                                    INSERT INTO Entities (Id, FolderId, EncryptedData, Deleted, Revision) 
                                    VALUES (?, ?, ?, ?, ?) ON CONFLICT (Id) DO NOTHING
                                """, row)
                        except Exception as ex:
                            print(f"[DB MIGRATION] Entities copy note: {ex}")

                        # Users
                        try:
                            sq_c.execute("SELECT Id, Username, Email, IsApproved FROM Users")
                            for row in sq_c.fetchall():
                                await c.execute("""
                                    INSERT INTO Users (Id, Username, Email, IsApproved) 
                                    VALUES (?, ?, ?, ?) ON CONFLICT (Id) DO NOTHING
                                """, row)
                        except Exception as ex:
                            print(f"[DB MIGRATION] Users copy note: {ex}")

                        # Groups
                        try:
                            sq_c.execute("SELECT Id, Name, IsSuperAdmin, CanManageUsers, CanSaveLocal, CanAdd, CanEdit, CanDelete, IsHidden, IsDeleted, CanReadLog, CanManageRoles, CanManageSettings FROM Groups")
                            for row in sq_c.fetchall():
                                await c.execute("""
                                    INSERT INTO Groups (Id, Name, IsSuperAdmin, CanManageUsers, CanSaveLocal, CanAdd, CanEdit, CanDelete, IsHidden, IsDeleted, CanReadLog, CanManageRoles, CanManageSettings, CanBulkEdit) 
                                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0) 
                                    ON CONFLICT (Id) DO UPDATE SET Name = EXCLUDED.Name
                                """, row)
                        except Exception as ex:
                            print(f"[DB MIGRATION] Groups copy note: {ex}")

                        # DbVersion
                        try:
                            sq_c.execute("SELECT Revision FROM DbVersion LIMIT 1")
                            sq_rev = sq_c.fetchone()
                            if sq_rev:
                                await c.execute("UPDATE DbVersion SET Revision = ?", (sq_rev[0],))
                        except Exception:
                            pass

                    print("[DB MIGRATION] Successfully imported data from SQLite into PostgreSQL!")
                except Exception as ex:
                    print(f"[DB MIGRATION ERROR] Could not migrate SQLite data: {ex}")
                break

        await conn.commit()
