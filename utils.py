import asyncio
from database import PgConnection

audit_lock = asyncio.Lock()

def get_user_id(user: dict):
    if user.get("is_local_token"):
        token_id = user.get("id") or user.get("sub") or user.get("token_id")
        if token_id and not str(token_id).startswith("local_token_"):
            return f"local_token_{token_id}"
        return token_id or "UnknownToken"

    uname = user.get("username", "").strip()
    if uname: return uname
    email = user.get("email", "").strip()
    if email: return email
    return "UnknownUser"

async def log_event(db: PgConnection, event_type: str, user: dict, ip: str, details: str):
    async with audit_lock:
        c = await db.cursor()
        username = user.get("username", "Unknown")
        email = user.get("email", "")
        await c.execute("INSERT INTO AuditLog (EventType, Username, Email, IpAddress, Details) VALUES (?, ?, ?, ?, ?)", (event_type, username, email, ip, details))
        await db.commit()
