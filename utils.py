import asyncio
import hashlib
import os
import hmac
import base64
import json
import time
from database import PgConnection

NODE_SECRET_KEY = os.getenv("NODE_SECRET_KEY", "CLEAR_NODE_SECRET_2026_KEY_AUTH")

def hash_password(password: str) -> str:
    salt = os.urandom(16)
    pw_hash = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 100_000)
    return salt.hex() + ":" + pw_hash.hex()

def verify_password(password: str, stored_hash: str) -> bool:
    if not stored_hash or ":" not in stored_hash:
        return False
    try:
        salt_hex, hash_hex = stored_hash.split(":", 1)
        salt = bytes.fromhex(salt_hex)
        pw_hash = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 100_000)
        return hmac.compare_digest(pw_hash.hex(), hash_hex)
    except Exception:
        return False

def create_jwt_token(payload: dict, expires_in_seconds: int = 86400 * 30) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    payload_copy = dict(payload)
    payload_copy["exp"] = int(time.time()) + expires_in_seconds
    payload_copy["iat"] = int(time.time())
    
    b64_header = base64.urlsafe_b64encode(json.dumps(header, separators=(',', ':')).encode('utf-8')).decode('utf-8').rstrip('=')
    b64_payload = base64.urlsafe_b64encode(json.dumps(payload_copy, separators=(',', ':')).encode('utf-8')).decode('utf-8').rstrip('=')
    
    signature = hmac.new(NODE_SECRET_KEY.encode('utf-8'), f"{b64_header}.{b64_payload}".encode('utf-8'), hashlib.sha256).digest()
    b64_signature = base64.urlsafe_b64encode(signature).decode('utf-8').rstrip('=')
    
    return f"{b64_header}.{b64_payload}.{b64_signature}"

def decode_jwt_token(token: str) -> dict:
    try:
        parts = token.split('.')
        if len(parts) != 3:
            return None
        b64_header, b64_payload, b64_sig = parts
        
        expected_sig = hmac.new(NODE_SECRET_KEY.encode('utf-8'), f"{b64_header}.{b64_payload}".encode('utf-8'), hashlib.sha256).digest()
        b64_expected_sig = base64.urlsafe_b64encode(expected_sig).decode('utf-8').rstrip('=')
        
        if not hmac.compare_digest(b64_sig, b64_expected_sig):
            return None
            
        rem = len(b64_payload) % 4
        if rem:
            b64_payload += '=' * (4 - rem)
            
        payload = json.loads(base64.urlsafe_b64decode(b64_payload).decode('utf-8'))
        if payload.get("exp") and time.time() > payload["exp"]:
            return None
        return payload
    except Exception:
        return None

audit_lock = asyncio.Lock()

def get_user_id(user: dict):
    if user.get("is_local_token"):
        token_id = user.get("id") or user.get("sub") or user.get("token_id")
        if token_id and not str(token_id).startswith("local_token_"):
            return f"local_token_{token_id}"
        return token_id or "UnknownToken"

    uid = user.get("id")
    if uid:
        return str(uid).strip()

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

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_cached_master_key = None

async def get_master_key(db: PgConnection) -> str:
    global _cached_master_key
    if _cached_master_key:
        return _cached_master_key
    c = await db.cursor()
    await c.execute("SELECT Value FROM ServerSettings WHERE Key = 'MasterKey'")
    row = await c.fetchone()
    if row and row[0]:
        _cached_master_key = row[0]
        return _cached_master_key
    return NODE_SECRET_KEY

def encrypt_plain_password(plain_pw: str, master_key: str) -> str:
    if not plain_pw:
        return ""
    if plain_pw.startswith("enc:"):
        return plain_pw
    key = hashlib.sha256(master_key.encode('utf-8')).digest()
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    ciphertext = aesgcm.encrypt(nonce, plain_pw.encode('utf-8'), None)
    return "enc:" + base64.b64encode(nonce + ciphertext).decode('utf-8')

def decrypt_plain_password(enc_pw: str, master_key: str) -> str:
    if not enc_pw:
        return ""
    if not enc_pw.startswith("enc:"):
        return enc_pw
    try:
        raw = base64.b64decode(enc_pw[4:])
        nonce = raw[:12]
        ciphertext = raw[12:]
        key = hashlib.sha256(master_key.encode('utf-8')).digest()
        aesgcm = AESGCM(key)
        plain = aesgcm.decrypt(nonce, ciphertext, None)
        return plain.decode('utf-8')
    except Exception as ex:
        print(f"Error decrypting password: {ex}")
        return ""
