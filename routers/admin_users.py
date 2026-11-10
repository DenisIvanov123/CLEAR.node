from dependencies import increment_admin_revision, require_manage_users, require_manage_roles
from ws_router import manager
from fastapi import APIRouter, Depends, Request, HTTPException
from cache import auth_cache, rights_cache, accessible_ids_cache, users_cache, pending_cache, groups_cache
from database import get_db, PgConnection
from utils import log_event, get_user_id, hash_password, get_master_key, encrypt_plain_password, decrypt_plain_password
import uuid
from models import UserGroupUpdate, UserCreateReq, UserResetPasswordReq
from datetime import datetime

router = APIRouter(prefix="/admin", tags=["Admin Users"])

def format_dt(val):
    if val is None:
        return ""
    if isinstance(val, datetime):
        return val.strftime("%Y-%m-%d %H:%M:%S")
    return str(val)

@router.get("/users")
async def get_all_users(user = Depends(require_manage_users), db: PgConnection = Depends(get_db)):
    cached_users = users_cache.get("all")
    
    if not cached_users:
        c = await db.cursor()
        try:
            await c.execute("""
                SELECT u.Id, u.Username, u.Email, u.LastConnect, ug.GroupId, u.IsActive, COALESCE(u.PlainPassword, '') 
                FROM Users u 
                LEFT JOIN UserGroups ug ON u.Id = ug.UserId 
                WHERE u.IsApproved = 1 ORDER BY u.LastConnect DESC
            """)
            rows = await c.fetchall()
            master_key = await get_master_key(db)
            
            cached_users = [{
                "id": r[0], 
                "username": r[1], 
                "email": r[2], 
                "last_connect": format_dt(r[3]), 
                "group_id": r[4] if r[4] else "", 
                "is_active": bool(r[5] if r[5] is not None else 1),
                "plain_password": decrypt_plain_password(r[6], master_key) if len(r) > 6 and r[6] else ""
            } for r in rows]
            
            users_cache.set("all", cached_users)
            
        except Exception as e:
            print(f"Ошибка БД при получении пользователей: {e}")
            return []

    result = []
    now = datetime.now()
    for u in cached_users:
        user_data = dict(u)
        is_online = False
        if user_data["last_connect"]:
            try:
                last_time = datetime.strptime(user_data["last_connect"], "%Y-%m-%d %H:%M:%S")
                if (now - last_time).total_seconds() < 15: 
                    is_online = True
            except:
                pass
        user_data["is_online"] = is_online
        result.append(user_data)

    return result

@router.get("/pending_users")
async def get_pending_users(user = Depends(require_manage_users), db: PgConnection = Depends(get_db)):
    cached_pending = pending_cache.get("all")
    if cached_pending: return cached_pending

    c = await db.cursor()
    await c.execute("SELECT Id, Username, Email, FirstConnect FROM Users WHERE IsApproved = 0 ORDER BY FirstConnect DESC")
    rows = await c.fetchall()
    result = [{
        "id": r[0], 
        "username": r[1], 
        "email": r[2], 
        "first_connect": format_dt(r[3])
    } for r in rows]
    
    pending_cache.set("all", result)
    return result

@router.post("/pending_users/{target_user_id}/approve")
async def approve_user(target_user_id: str, request: Request, user = Depends(require_manage_users), db: PgConnection = Depends(get_db)):
    c = await db.cursor()
    await c.execute("UPDATE Users SET IsApproved = 1, IsActive = 1 WHERE Id = ?", (target_user_id,))
    
    await c.execute("SELECT Value FROM ServerSettings WHERE Key = 'DefaultGroupId'")
    def_grp = await c.fetchone()
    if def_grp and def_grp[0]:
        await c.execute("""
            INSERT INTO UserGroups (UserId, GroupId) VALUES (?, ?)
            ON CONFLICT (UserId, GroupId) DO NOTHING
        """, (target_user_id, def_grp[0]))
    await db.commit()
    
    new_admin_rev = await increment_admin_revision(db)
    await manager.broadcast({"event": "admin_revision", "revision": new_admin_rev})

    await log_event(db, "Изменение прав", user, request.client.host, f"Одобрена заявка пользователя {target_user_id}")
    
    auth_cache.clear()
    rights_cache.clear()
    accessible_ids_cache.clear()
    pending_cache.clear()
    users_cache.clear() 
    return {"status": "ok"}

@router.delete("/pending_users/{target_user_id}")
async def reject_user(target_user_id: str, request: Request, user = Depends(require_manage_users), db: PgConnection = Depends(get_db)):
    c = await db.cursor()
    await c.execute("UPDATE Users SET IsActive = 0 WHERE Id = ?", (target_user_id,))
    await db.commit()
    
    new_admin_rev = await increment_admin_revision(db)
    await manager.broadcast({"event": "admin_revision", "revision": new_admin_rev})

    auth_cache.clear()
    rights_cache.clear()
    accessible_ids_cache.clear()
    pending_cache.clear()
    users_cache.clear() 
    return {"status": "ok"}

@router.post("/pending_users/{target_user_id}/restore")
async def restore_pending_user(target_user_id: str, request: Request, user = Depends(require_manage_users), db: PgConnection = Depends(get_db)):
    c = await db.cursor()
    await c.execute("UPDATE Users SET IsActive = 1 WHERE Id = ?", (target_user_id,))
    await db.commit()
    
    new_admin_rev = await increment_admin_revision(db)
    await manager.broadcast({"event": "admin_revision", "revision": new_admin_rev})

    auth_cache.clear()
    rights_cache.clear()
    accessible_ids_cache.clear()
    pending_cache.clear()
    users_cache.clear() 
    return {"status": "ok"}

@router.put("/users/{target_user_id}/group")
async def set_user_group(target_user_id: str, req: UserGroupUpdate, request: Request, user = Depends(require_manage_roles), db: PgConnection = Depends(get_db)):
    c = await db.cursor()
    
    if not req.group_id:
        await c.execute("DELETE FROM UserGroups WHERE UserId = ?", (target_user_id,))
    else:
        await c.execute("DELETE FROM UserGroups WHERE UserId = ?", (target_user_id,))
        await c.execute("INSERT INTO UserGroups (UserId, GroupId) VALUES (?, ?) ON CONFLICT (UserId, GroupId) DO NOTHING", (target_user_id, req.group_id))
        
    await db.commit()
    
    new_admin_rev = await increment_admin_revision(db)
    await manager.broadcast({"event": "admin_revision", "revision": new_admin_rev})

    await log_event(db, "Изменение прав", user, request.client.host, f"Изменена группа доступа пользователя {target_user_id}")
    
    groups_cache.clear()
    rights_cache.clear()
    accessible_ids_cache.clear()
    users_cache.clear()
    auth_cache.clear()
    return {"status": "ok"}

@router.delete("/users/{target_user_id}")
async def ban_user(target_user_id: str, request: Request, user = Depends(require_manage_users), db: PgConnection = Depends(get_db)):
    c = await db.cursor()

    await c.execute("UPDATE Users SET IsActive = 0 WHERE Id = ?", (target_user_id,))
    
    if target_user_id.startswith("local_token_"):
        token_id = target_user_id.replace("local_token_", "")
        await c.execute("UPDATE LocalTokens SET IsActive = 0 WHERE Id = ?", (token_id,))
        users_cache.clear() 
    
    await db.commit()
    
    new_admin_rev = await increment_admin_revision(db)
    await manager.broadcast({"event": "admin_revision", "revision": new_admin_rev})

    await log_event(db, "Изменение прав", user, request.client.host, f"Учетная запись отключена: {target_user_id}")
    
    auth_cache.clear()
    rights_cache.clear()
    accessible_ids_cache.clear()
    users_cache.clear()
    
    return {"status": "ok"}

@router.post("/users/{target_user_id}/restore")
async def restore_user(target_user_id: str, request: Request, user = Depends(require_manage_users), db: PgConnection = Depends(get_db)):
    c = await db.cursor()
    await c.execute("UPDATE Users SET IsActive = 1 WHERE Id = ?", (target_user_id,))
    await db.commit()
    
    new_admin_rev = await increment_admin_revision(db)
    await manager.broadcast({"event": "admin_revision", "revision": new_admin_rev})

    await log_event(db, "Изменение прав", user, request.client.host, f"Учетная запись восстановлена: {target_user_id}")
    
    auth_cache.clear()
    rights_cache.clear()
    accessible_ids_cache.clear()
    users_cache.clear()
    return {"status": "ok"}


@router.post("/users/create")
async def create_user(req: UserCreateReq, request: Request, user = Depends(require_manage_users), db: PgConnection = Depends(get_db)):
    username = req.username.strip()
    if not username:
        raise HTTPException(400, "Имя пользователя не может быть пустым")
    if not req.password:
        raise HTTPException(400, "Пароль не может быть пустым")
        
    c = await db.cursor()
    await c.execute("SELECT Id, IsApproved FROM Users WHERE LOWER(Username) = LOWER(?)", (username,))
    existing = await c.fetchone()
    now = datetime.now()
    pw_hash = hash_password(req.password)

    if existing:
        if existing[1] == 1:
            raise HTTPException(400, "Пользователь с таким логином уже существует")
        else:
            user_id = existing[0]
            master_key = await get_master_key(db)
            enc_pw = encrypt_plain_password(req.password, master_key)
            await c.execute("""
                UPDATE Users SET PasswordHash = ?, PlainPassword = ?, Email = ?, IsApproved = 1, IsActive = 1, LastConnect = ?
                WHERE Id = ?
            """, (pw_hash, enc_pw, req.email or "", now, user_id))
            if req.group_id:
                await c.execute("DELETE FROM UserGroups WHERE UserId = ?", (user_id,))
                await c.execute("INSERT INTO UserGroups (UserId, GroupId) VALUES (?, ?)", (user_id, req.group_id))
            await db.commit()
            users_cache.clear()
            pending_cache.clear()
            new_admin_rev = await increment_admin_revision(db)
            await manager.broadcast({"event": "admin_revision", "revision": new_admin_rev})
            client_ip = request.client.host if request.client else "unknown"
            await log_event(db, "UserCreate", user, client_ip, f"Создан/активирован пользователь {username}")
            return {"status": "ok", "user_id": user_id, "username": username}

    user_id = str(uuid.uuid4())
    
    master_key = await get_master_key(db)
    enc_pw = encrypt_plain_password(req.password, master_key)
    await c.execute("""
        INSERT INTO Users (Id, Username, Email, PasswordHash, PlainPassword, FirstConnect, LastConnect, IsApproved, IsActive)
        VALUES (?, ?, ?, ?, ?, ?, ?, 1, 1)
    """, (user_id, username, req.email or "", pw_hash, enc_pw, now, now))
    
    if req.group_id:
        await c.execute("DELETE FROM UserGroups WHERE UserId = ?", (user_id,))
        await c.execute("INSERT INTO UserGroups (UserId, GroupId) VALUES (?, ?)", (user_id, req.group_id))
        
    await db.commit()
    users_cache.clear()
    
    new_admin_rev = await increment_admin_revision(db)
    await manager.broadcast({"event": "admin_revision", "revision": new_admin_rev})
    
    client_ip = request.client.host if request.client else "unknown"
    await log_event(db, "UserCreate", user, client_ip, f"Создан пользователь {username}")
    
    return {"status": "ok", "user_id": user_id, "username": username}

@router.post("/users/{target_user_id}/reset_password")
async def reset_password(target_user_id: str, req: UserResetPasswordReq, request: Request, user = Depends(require_manage_users), db: PgConnection = Depends(get_db)):
    if not req.new_password:
        raise HTTPException(400, "Новый пароль не может быть пустым")
        
    c = await db.cursor()
    await c.execute("SELECT Id, Username FROM Users WHERE Id = ?", (target_user_id,))
    row = await c.fetchone()
    if not row:
        raise HTTPException(404, "Пользователь не найден")
        
    target_username = row[1]
    pw_hash = hash_password(req.new_password)
    master_key = await get_master_key(db)
    enc_pw = encrypt_plain_password(req.new_password, master_key)
    
    await c.execute("UPDATE Users SET PasswordHash = ?, PlainPassword = ?, must_change_password = 0 WHERE Id = ? OR LOWER(Username) = LOWER(?)", (pw_hash, enc_pw, target_user_id, target_username))
    await db.commit()
    
    auth_cache.clear()
    client_ip = request.client.host if request.client else "unknown"
    await log_event(db, "PasswordReset", user, client_ip, f"Сброшен пароль пользователя {target_username}")
    
    return {"status": "ok", "message": f"Пароль для {target_username} успешно обновлен"}
