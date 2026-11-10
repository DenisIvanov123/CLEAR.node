import httpx
import hashlib
from typing import Optional
from datetime import datetime, timezone
from fastapi import Header, HTTPException, Depends
from config import CENTRAL_AUTH_URL
from database import get_db, PgConnection
from utils import get_user_id, decode_jwt_token
from cache import auth_cache

async def verify_user(authorization: Optional[str] = Header(None), db: PgConnection = Depends(get_db)):
    if not authorization:
        raise HTTPException(401, "Authorization header required")
    if not authorization.startswith("Bearer "): 
        raise HTTPException(401, "Invalid token format")
    
    token = authorization.replace("Bearer ", "").strip()

    cached_user = auth_cache.get(token)
    if cached_user:
        return cached_user

    if token.startswith("cl_"):
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        c = await db.cursor()
        await c.execute("SELECT Description, ExpiresAt, Id FROM LocalTokens WHERE TokenHash = ?", (token_hash,))
        row = await c.fetchone()
        if not row: raise HTTPException(401, "Invalid or deleted local token")
        
        try:
            await c.execute("SELECT IsActive FROM LocalTokens WHERE TokenHash = ?", (token_hash,))
            act_row = await c.fetchone()
            if act_row and act_row[0] is not None and not bool(act_row[0]):
                raise HTTPException(403, "Token is disabled")
        except HTTPException:
            raise 
        except Exception:
            pass
            
        if row[1]:
            expires_at = row[1]
            if isinstance(expires_at, str):
                try:
                    expires_dt = datetime.strptime(expires_at, "%Y-%m-%d %H:%M:%S")
                except Exception:
                    expires_dt = datetime.fromisoformat(expires_at)
            else:
                expires_dt = expires_at

            # Convert timezone-aware to naive or compare safely
            if expires_dt.tzinfo is not None:
                now = datetime.now(expires_dt.tzinfo)
            else:
                now = datetime.now()

            if expires_dt < now:
                raise HTTPException(401, "Local token has expired")
            
        result = {"email": f"local_token_{row[2]}", "username": f"Token: {row[0]}", "is_local_token": True}
        auth_cache.set(token, result)
        return result
    else:
        # Check if this is a local node JWT token
        local_payload = decode_jwt_token(token)
        if not local_payload:
            print(f"\n[AUTH REJECTED TOKEN]: token={token[:30]}... len={len(token)}\n")
        if local_payload and local_payload.get("sub"):
            sub = str(local_payload.get("sub"))
            c = await db.cursor()
            await c.execute("SELECT Id, Username, Email, IsActive, IsApproved FROM Users WHERE Id = ? OR LOWER(Username) = LOWER(?)", (sub, sub))
            u_row = await c.fetchone()
            if not u_row:
                raise HTTPException(401, "User not found")
            if u_row[3] is not None and not bool(u_row[3]):
                raise HTTPException(403, "User account is disabled")
            if u_row[4] is not None and not bool(u_row[4]):
                raise HTTPException(403, "User account is not approved")
            user_data = {
                "id": str(u_row[0]),
                "username": u_row[1] or sub,
                "email": u_row[2] or local_payload.get("email", ""),
                "is_local_token": False
            }
            auth_cache.set(token, user_data)
            return user_data

        try:
            custom_headers = {
                "Authorization": authorization,
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "application/json"
            }
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{CENTRAL_AUTH_URL}/auth/verify", 
                    headers=custom_headers, 
                    follow_redirects=True, 
                    timeout=5.0
                )
                
                if resp.status_code != 200: 
                    print(f"\n[AUTH ERROR] Central server returned {resp.status_code}: {resp.text}\n")
                    raise HTTPException(401, "Invalid token from central auth")
                    
                user_data = resp.json()
                user_data["is_local_token"] = False 
                
        except httpx.RequestError as e:
            print(f"\n[NETWORK ERROR] Failed to connect to central auth server: {e}\n")
            raise HTTPException(401, "Central auth server unavailable")
            
        try:
            c = await db.cursor()
            await c.execute("SELECT IsActive FROM Users WHERE Id = ?", (get_user_id(user_data),))
            act_row = await c.fetchone()
            if act_row and act_row[0] is not None and not bool(act_row[0]):
                raise HTTPException(403, "User account is disabled")
        except HTTPException:
            raise
        except Exception:
            pass

        auth_cache.set(token, user_data)
        return user_data

async def require_manage_roles(user = Depends(verify_user), db: PgConnection = Depends(get_db)):
    if user.get("is_local_token"): raise HTTPException(403, "Access denied for local tokens")
    user_id = get_user_id(user)
    c = await db.cursor()
    await c.execute("""SELECT 1 FROM Groups g JOIN UserGroups ug ON g.Id = ug.GroupId 
                 WHERE ug.UserId = ? AND g.IsDeleted = 0 AND (g.IsSuperAdmin = 1 OR g.CanManageRoles = 1)""", (user_id,))
    if not await c.fetchone(): raise HTTPException(403, "Insufficient permissions to manage roles and access")
    return user

async def require_manage_users(user = Depends(verify_user), db: PgConnection = Depends(get_db)):
    if user.get("is_local_token"): raise HTTPException(403, "Access denied for local tokens")
    user_id = get_user_id(user)
    c = await db.cursor()
    await c.execute("""SELECT 1 FROM Groups g JOIN UserGroups ug ON g.Id = ug.GroupId 
                 WHERE ug.UserId = ? AND g.IsDeleted = 0 AND (g.IsSuperAdmin = 1 OR g.CanManageUsers = 1)""", (user_id,))
    if not await c.fetchone(): raise HTTPException(403, "Insufficient permissions to manage roles and access")
    return user

async def require_manage_settings(user = Depends(verify_user), db: PgConnection = Depends(get_db)):
    if user.get("is_local_token"): raise HTTPException(403, "Access denied for local tokens")
    user_id = get_user_id(user)
    c = await db.cursor()
    await c.execute("""SELECT 1 FROM Groups g JOIN UserGroups ug ON g.Id = ug.GroupId 
                 WHERE ug.UserId = ? AND g.IsDeleted = 0 AND (g.IsSuperAdmin = 1 OR g.CanManageSettings = 1)""", (user_id,))
    if not await c.fetchone(): raise HTTPException(403, "Insufficient permissions to manage settings")
    return user

async def require_read_log(user = Depends(verify_user), db: PgConnection = Depends(get_db)):
    if user.get("is_local_token"): raise HTTPException(403, "Access denied for local tokens")
    user_id = get_user_id(user)
    c = await db.cursor()
    await c.execute("""SELECT 1 FROM Groups g JOIN UserGroups ug ON g.Id = ug.GroupId 
                 WHERE ug.UserId = ? AND g.IsDeleted = 0 AND (g.IsSuperAdmin = 1 OR g.CanReadLog = 1)""", (user_id,))
    if not await c.fetchone(): raise HTTPException(403, "Insufficient permissions to read event logs")
    return user

async def require_superadmin(user = Depends(verify_user), db: PgConnection = Depends(get_db)):
    if user.get("is_local_token"): raise HTTPException(403, "Access denied for local tokens")
    user_id = get_user_id(user)
    c = await db.cursor()
    await c.execute("""SELECT 1 FROM Groups g JOIN UserGroups ug ON g.Id = ug.GroupId 
                 WHERE ug.UserId = ? AND g.IsDeleted = 0 AND g.IsSuperAdmin = 1""", (user_id,))
    if not await c.fetchone(): raise HTTPException(403, "Super-Admin privileges required")
    return user

async def increment_admin_revision(db: PgConnection) -> int:
    c = await db.cursor()
    await c.execute("INSERT INTO DbVersion (Revision, AdminRevision) SELECT 0, 0 WHERE NOT EXISTS (SELECT 1 FROM DbVersion)")
    
    await c.execute("UPDATE DbVersion SET AdminRevision = AdminRevision + 1")
    await c.execute("SELECT AdminRevision FROM DbVersion LIMIT 1")
    row = await c.fetchone()
    await db.commit()
    return row[0] if row else 1
