import secrets
import hashlib
from datetime import datetime, timezone
from dependencies import increment_admin_revision
from ws_router import manager
from fastapi import APIRouter, Depends, Request
from cache import auth_cache, tokens_cache, settings_cache, accessible_ids_cache, rights_cache, users_cache
from database import get_db, PgConnection
from dependencies import require_manage_settings, require_read_log, require_superadmin
from utils import log_event
from models import LocalTokenReq, ServerSettingsReq

router = APIRouter(prefix="/admin", tags=["Admin System"])

def format_dt(val):
    if val is None:
        return ""
    if isinstance(val, datetime):
        return val.strftime("%Y-%m-%d %H:%M:%S")
    return str(val)

@router.get("/tokens")
async def get_tokens(user = Depends(require_manage_settings), db: PgConnection = Depends(get_db)):
    cached_tokens = tokens_cache.get("all")
    if cached_tokens: return cached_tokens

    c = await db.cursor()
    await c.execute("SELECT Id, Description, ExpiresAt, CreatedAt, IsActive FROM LocalTokens")
    rows = await c.fetchall()
    
    result = [{
        "id": r[0], 
        "description": r[1], 
        "expires_at": format_dt(r[2]), 
        "created_at": format_dt(r[3]), 
        "is_active": bool(r[4])
    } for r in rows]
    
    tokens_cache.set("all", result)
    return result

@router.post("/tokens")
async def create_token(req: LocalTokenReq, request: Request, user = Depends(require_manage_settings), db: PgConnection = Depends(get_db)):
    raw_token = "cl_" + secrets.token_urlsafe(40)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    token_id = secrets.token_hex(8)
    
    expires_clause = f"NOW() + INTERVAL '{int(req.days_valid)} days'" if req.days_valid else "NULL"
    
    c = await db.cursor()
    await c.execute(f"INSERT INTO LocalTokens (Id, TokenHash, Description, ExpiresAt) VALUES (?, ?, ?, {expires_clause})", (token_id, token_hash, req.description))
    
    token_user_id = f"local_token_{token_id}"
    await c.execute("""
        INSERT INTO Users (Id, Username, Email, IsApproved) 
        VALUES (?, ?, '', 1) 
        ON CONFLICT (Id) DO NOTHING
    """, (token_user_id, f"Token: {req.description}"))
    
    await c.execute("SELECT Value FROM ServerSettings WHERE Key = 'DefaultGroupId'")
    def_grp = await c.fetchone()
    if def_grp and def_grp[0]:
        await c.execute("""
            INSERT INTO UserGroups (UserId, GroupId) VALUES (?, ?)
            ON CONFLICT (UserId, GroupId) DO NOTHING
        """, (token_user_id, def_grp[0]))

    await db.commit()
    new_admin_rev = await increment_admin_revision(db)
    await manager.broadcast({"event": "admin_revision", "revision": new_admin_rev})
    tokens_cache.clear()
    users_cache.clear()
    await log_event(db, "Настройки", user, request.client.host, f"Создан новый локальный токен: {req.description}")
    return {"status": "ok", "token": raw_token}

@router.delete("/tokens/{token_id}")
async def revoke_token(request: Request, token_id: str, user = Depends(require_manage_settings), db: PgConnection = Depends(get_db)):
    c = await db.cursor()
    await c.execute("UPDATE LocalTokens SET IsActive = 0 WHERE Id = ?", (token_id,))
    await c.execute("UPDATE Users SET IsActive = 0 WHERE Id = ?", (f"local_token_{token_id}",))

    await db.commit()
    new_admin_rev = await increment_admin_revision(db)
    await manager.broadcast({"event": "admin_revision", "revision": new_admin_rev})
    await log_event(db, "Настройки", user, request.client.host, f"Отключен локальный токен: {token_id}")
    
    auth_cache.clear()
    tokens_cache.clear()
    users_cache.clear()
    return {"status": "ok"}

@router.post("/tokens/{token_id}/restore")
async def restore_token(request: Request, token_id: str, user = Depends(require_manage_settings), db: PgConnection = Depends(get_db)):
    c = await db.cursor()
    await c.execute("UPDATE LocalTokens SET IsActive = 1 WHERE Id = ?", (token_id,))
    await c.execute("UPDATE Users SET IsActive = 1 WHERE Id = ?", (f"local_token_{token_id}",))

    await db.commit()
    new_admin_rev = await increment_admin_revision(db)
    await manager.broadcast({"event": "admin_revision", "revision": new_admin_rev})

    auth_cache.clear()
    tokens_cache.clear()
    return {"status": "ok"}

@router.get("/settings")
async def get_settings(user = Depends(require_manage_settings), db: PgConnection = Depends(get_db)):
    cached_settings = settings_cache.get("all")
    if cached_settings: return cached_settings

    c = await db.cursor()
    await c.execute("SELECT Key, Value FROM ServerSettings")
    rows = await c.fetchall()
    s = {r[0]: r[1] for r in rows}
    result = {"audit_retention_days": int(s.get("AuditRetentionDays", 90)), "deleted_retention_days": int(s.get("DeletedRetentionDays", 30)), "default_group_id": s.get("DefaultGroupId", "")}
    
    settings_cache.set("all", result)
    return result

@router.post("/settings")
async def save_settings(settings: ServerSettingsReq, request: Request, user = Depends(require_manage_settings), db: PgConnection = Depends(get_db)):
    c = await db.cursor()
    await c.execute("""
        INSERT INTO ServerSettings (Key, Value) VALUES ('AuditRetentionDays', ?)
        ON CONFLICT (Key) DO UPDATE SET Value = EXCLUDED.Value
    """, (str(settings.audit_retention_days),))
    await c.execute("""
        INSERT INTO ServerSettings (Key, Value) VALUES ('DeletedRetentionDays', ?)
        ON CONFLICT (Key) DO UPDATE SET Value = EXCLUDED.Value
    """, (str(settings.deleted_retention_days),))
    await c.execute("""
        INSERT INTO ServerSettings (Key, Value) VALUES ('DefaultGroupId', ?)
        ON CONFLICT (Key) DO UPDATE SET Value = EXCLUDED.Value
    """, (settings.default_group_id,))

    await db.commit()
    new_admin_rev = await increment_admin_revision(db)
    await manager.broadcast({"event": "admin_revision", "revision": new_admin_rev})

    await log_event(db, "Настройки", user, request.client.host, "Изменены системные настройки.")
    settings_cache.clear()
    return {"status": "ok"}

@router.delete("/wipe")
async def wipe_database(request: Request, user = Depends(require_superadmin), db: PgConnection = Depends(get_db)):
    c = await db.cursor()
    await c.execute("UPDATE DbVersion SET Revision = Revision + 1")
    await c.execute("UPDATE Entities SET Deleted = 1, EncryptedData = '', Revision = (SELECT Revision FROM DbVersion LIMIT 1)")

    await db.commit()
    new_admin_rev = await increment_admin_revision(db)
    await manager.broadcast({"event": "admin_revision", "revision": new_admin_rev})

    await log_event(db, "Удаление", user, request.client.host, "ВНИМАНИЕ: ПРОИЗВЕДЕНА ПОЛНАЯ ОЧИСТКА ВСЕХ ДАННЫХ СЕРВЕРА!")
    accessible_ids_cache.clear()
    rights_cache.clear()
    return {"status": "ok"}

@router.get("/auditlog")
async def get_audit_log(user = Depends(require_read_log), db: PgConnection = Depends(get_db)):
    c = await db.cursor()
    await c.execute("SELECT Timestamp, EventType, Username, Email, IpAddress, Details FROM AuditLog ORDER BY Id DESC LIMIT 3000")
    rows = await c.fetchall()
    return [{"timestamp": format_dt(r[0]), "event_type": r[1], "username": r[2], "email": r[3], "ip_address": r[4], "details": r[5]} for r in rows]

@router.get("/backup")
async def get_database_backup(request: Request, user = Depends(require_manage_settings), db: PgConnection = Depends(get_db)):
    c = await db.cursor()
    
    # 1. DbVersion
    await c.execute("SELECT Revision, AdminRevision FROM DbVersion LIMIT 1")
    rev_row = await c.fetchone()
    db_version = {
        "revision": rev_row[0] if rev_row else 0,
        "admin_revision": rev_row[1] if rev_row and len(rev_row) > 1 else 0
    }
    
    # 2. ServerSettings
    await c.execute("SELECT Key, Value FROM ServerSettings")
    rows = await c.fetchall()
    server_settings = [{"key": r[0], "value": r[1]} for r in rows]
    
    # 3. Groups
    await c.execute("""
        SELECT Id, Name, IsSuperAdmin, CanAdd, CanEdit, CanDelete, CanBulkEdit,
               CanManageUsers, CanManageGroups, CanManageSettings, CanReadLog, IsDeleted, CreatedAt
        FROM Groups
    """)
    rows = await c.fetchall()
    groups = [{
        "id": r[0], "name": r[1], "is_superadmin": bool(r[2]), "can_add": bool(r[3]),
        "can_edit": bool(r[4]), "can_delete": bool(r[5]), "can_bulk_edit": bool(r[6]),
        "can_manage_users": bool(r[7]), "can_manage_groups": bool(r[8]),
        "can_manage_settings": bool(r[9]), "can_read_log": bool(r[10]),
        "is_deleted": bool(r[11]), "created_at": format_dt(r[12])
    } for r in rows]
    
    # 4. Users
    await c.execute("SELECT Id, Username, Email, IsApproved, IsActive, CreatedAt, LastConnect FROM Users")
    rows = await c.fetchall()
    users = [{
        "id": r[0], "username": r[1], "email": r[2], "is_approved": bool(r[3]),
        "is_active": bool(r[4]) if r[4] is not None else True,
        "created_at": format_dt(r[5]), "last_connect": format_dt(r[6])
    } for r in rows]
    
    # 5. UserGroups
    await c.execute("SELECT UserId, GroupId FROM UserGroups")
    rows = await c.fetchall()
    user_groups = [{"user_id": r[0], "group_id": r[1]} for r in rows]
    
    # 6. Entities
    await c.execute("SELECT Id, FolderId, EncryptedData, Deleted, Revision, DeletedAt FROM Entities")
    rows = await c.fetchall()
    entities = [{
        "id": r[0], "folder_id": r[1], "encrypted_data": r[2], "deleted": bool(r[3]),
        "revision": r[4], "deleted_at": format_dt(r[5])
    } for r in rows]
    
    # 7. EntityPermissions
    await c.execute("SELECT EntityId, GroupId, AccessLevel FROM EntityPermissions")
    rows = await c.fetchall()
    entity_permissions = [{"entity_id": r[0], "group_id": r[1], "access_level": r[2]} for r in rows]
    
    # 8. LocalTokens
    await c.execute("SELECT Id, Description, ExpiresAt, CreatedAt, IsActive FROM LocalTokens")
    rows = await c.fetchall()
    tokens = [{
        "id": r[0], "description": r[1], "expires_at": format_dt(r[2]),
        "created_at": format_dt(r[3]), "is_active": bool(r[4])
    } for r in rows]
    
    backup_data = {
        "export_date": datetime.now(timezone.utc).isoformat(),
        "db_version": db_version,
        "settings": server_settings,
        "groups": groups,
        "users": users,
        "user_groups": user_groups,
        "entities": entities,
        "entity_permissions": entity_permissions,
        "local_tokens": tokens
    }
    
    await log_event(db, "Резервное копирование", user, request.client.host, f"Выгружен бэкап базы данных ({len(entities)} объектов, {len(users)} пользователей).")
    return backup_data
