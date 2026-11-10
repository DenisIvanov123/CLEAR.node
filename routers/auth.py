from fastapi import APIRouter, Depends, HTTPException, Request
from database import get_db, PgConnection
from models import LoginReq, ChangePasswordReq
from utils import verify_password, hash_password, create_jwt_token, log_event, get_user_id, get_master_key, encrypt_plain_password
from dependencies import verify_user
from datetime import datetime

router = APIRouter(prefix="/auth", tags=["Auth"])

@router.post("/login")
async def login(req: LoginReq, request: Request, db: PgConnection = Depends(get_db)):
    c = await db.cursor()
    username = req.username.strip()
    if not username:
        raise HTTPException(400, "Имя пользователя не указано")
    if not req.password:
        raise HTTPException(400, "Пароль не указан")

    await c.execute("""
        SELECT Id, Username, Email, PasswordHash, IsActive, IsApproved 
        FROM Users 
        WHERE LOWER(Username) = LOWER(?) OR LOWER(Email) = LOWER(?)
        ORDER BY (CASE WHEN PasswordHash IS NOT NULL AND PasswordHash != '' THEN 0 ELSE 1 END), LastConnect DESC NULLS LAST
    """, (username, username))
    row = await c.fetchone()
    if not row:
        raise HTTPException(401, "Неверный логин или пароль")

    user_id, db_username, email, pw_hash, is_active, is_approved = row[0], row[1], row[2], row[3], row[4], row[5]

    if is_active is not None and not bool(is_active):
        raise HTTPException(403, "Учетная запись отключена")
    if is_approved is not None and not bool(is_approved):
        raise HTTPException(403, "Учетная запись ожидает подтверждения администратора")

    if not pw_hash:
        raise HTTPException(401, "Пароль для данной учетной записи еще не задан. Обратитесь к администратору для сброса пароля.")

    if not verify_password(req.password, pw_hash):
        raise HTTPException(401, "Неверный логин или пароль")

    token = create_jwt_token({
        "sub": str(user_id),
        "username": db_username,
        "email": email or ""
    })

    client_ip = request.client.host if request.client else "unknown"
    await c.execute("UPDATE Users SET LastConnect = ? WHERE Id = ?", (datetime.now(), user_id))
    await log_event(db, "UserLogin", {"username": db_username, "email": email}, client_ip, "Успешный вход по паролю")

    return {
        "access_token": token,
        "token_type": "bearer",
        "username": db_username,
        "user_id": str(user_id)
    }

@router.get("/verify")
async def verify_auth(user = Depends(verify_user)):
    return user

@router.post("/change_password")
async def change_password(req: ChangePasswordReq, request: Request, user = Depends(verify_user), db: PgConnection = Depends(get_db)):
    if user.get("is_local_token"):
        raise HTTPException(403, "Смена пароля недоступна для локальных токенов")
    user_id = get_user_id(user)
    c = await db.cursor()
    await c.execute("SELECT Id, PasswordHash, Username, Email FROM Users WHERE Id = ? OR LOWER(Username) = LOWER(?)", (user_id, user_id))
    row = await c.fetchone()
    if not row:
        raise HTTPException(404, "Пользователь не найден")
    
    db_id, pw_hash, uname, email = row[0], row[1], row[2], row[3]
    if pw_hash and not verify_password(req.old_password, pw_hash):
        raise HTTPException(400, "Текущий пароль указан неверно")
        
    new_hash = hash_password(req.new_password)
    master_key = await get_master_key(db)
    enc_pw = encrypt_plain_password(req.new_password, master_key)
    await c.execute("UPDATE Users SET PasswordHash = ?, PlainPassword = ?, MustChangePassword = 0 WHERE Id = ?", (new_hash, enc_pw, db_id))
    
    client_ip = request.client.host if request.client else "unknown"
    await log_event(db, "PasswordChange", {"username": uname, "email": email}, client_ip, "Пользователь сменил пароль")
    return {"status": "ok", "message": "Пароль успешно изменен"}
