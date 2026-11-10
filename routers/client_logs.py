from fastapi import APIRouter, Depends, Request, Header
from typing import Optional
from database import get_db, PgConnection
from models import ClientLogsBatchReq
from dependencies import require_manage_users, verify_user
from datetime import datetime

router = APIRouter(tags=["Client Logs"])

def format_dt(val):
    if val is None:
        return ""
    if isinstance(val, datetime):
        return val.strftime("%Y-%m-%d %H:%M:%S")
    return str(val)

@router.post("/client/logs")
async def receive_client_logs(
    req: ClientLogsBatchReq, 
    request: Request, 
    authorization: Optional[str] = Header(None),
    db: PgConnection = Depends(get_db)
):
    client_ip = request.client.host if request.client else "unknown"
    uname = ""
    if authorization and authorization.startswith("Bearer "):
        try:
            user = await verify_user(authorization, db)
            uname = user.get("username", "")
        except Exception:
            pass

    c = await db.cursor()
    for item in req.logs[:100]:  # Cap at 100 entries per batch
        level = (item.level or "INFO").upper()[:20]
        msg = (item.message or "")[:2000]
        st = (item.stack_trace or "")[:4000]
        ver = (item.app_version or "")[:50]
        
        await c.execute("""
            INSERT INTO ClientLogs (Username, ClientIp, LogLevel, Message, StackTrace, AppVersion)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (uname, client_ip, level, msg, st, ver))
        
    await db.commit()
    return {"status": "ok", "received": len(req.logs)}

@router.get("/admin/client_logs")
async def get_client_logs(
    limit: int = 200,
    level: Optional[str] = None,
    user = Depends(require_manage_users),
    db: PgConnection = Depends(get_db)
):
    c = await db.cursor()
    if level:
        await c.execute("""
            SELECT Id, Timestamp, Username, ClientIp, LogLevel, Message, StackTrace, AppVersion
            FROM ClientLogs
            WHERE LogLevel = ?
            ORDER BY Timestamp DESC LIMIT ?
        """, (level.upper(), limit))
    else:
        await c.execute("""
            SELECT Id, Timestamp, Username, ClientIp, LogLevel, Message, StackTrace, AppVersion
            FROM ClientLogs
            ORDER BY Timestamp DESC LIMIT ?
        """, (limit,))
        
    rows = await c.fetchall()
    return [{
        "id": r[0],
        "timestamp": format_dt(r[1]),
        "username": r[2],
        "client_ip": r[3],
        "level": r[4],
        "message": r[5],
        "stack_trace": r[6],
        "app_version": r[7]
    } for r in rows]
