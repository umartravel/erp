"""
Autentikasi: JWT + bcrypt + dependency RBAC untuk FastAPI.
Setara dengan blok authenticateToken + bcrypt + jwt pada server.js.

Catatan: hash password di DB dibuat oleh bcrypt Node ($2b$...) dan
sepenuhnya kompatibel dengan paket `bcrypt` Python.
"""
import datetime
import os

import bcrypt
import jwt
from fastapi import Header, HTTPException, Query

# Kunci JWT. Untuk produksi/VPS, set variabel lingkungan JWT_SECRET agar tidak
# memakai nilai default. Default disediakan agar tetap jalan langsung saat lokal.
JWT_SECRET = os.environ.get("JWT_SECRET", "umar_crm_super_secret_key_2026")
JWT_ALGO = "HS256"
TOKEN_TTL_HOURS = 8


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt(10)).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except (ValueError, TypeError):
        return False


def create_token(user_id: int, role: str, name: str) -> str:
    payload = {
        "id": user_id,
        "role": role,
        "name": name,
        "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=TOKEN_TTL_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)


def authenticate_token(authorization: str = Header(default=None)) -> dict:
    """
    Dependency FastAPI yang meniru middleware authenticateToken di Express.
    401 jika token tidak ada, 403 jika token tidak valid/kedaluwarsa.
    Mengembalikan payload user (id, role, name) -> req.user.
    """
    token = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ", 1)[1]
    elif authorization and " " in authorization:
        token = authorization.split(" ")[1]

    if not token:
        raise HTTPException(status_code=401, detail="Akses Ditolak")

    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
    except jwt.PyJWTError:
        raise HTTPException(status_code=403, detail="Token tidak valid")


def authenticate_file_token(
    authorization: str = Header(default=None), token: str = Query(default=None)
) -> dict:
    """
    Seperti authenticate_token, tetapi juga menerima token via query string (?token=).
    Diperlukan untuk akses file PII lewat <img>/<a> yang tidak bisa mengirim header.
    """
    raw = None
    if authorization and authorization.startswith("Bearer "):
        raw = authorization.split(" ", 1)[1]
    elif token:
        raw = token

    if not raw:
        raise HTTPException(status_code=401, detail="Akses Ditolak")
    try:
        return jwt.decode(raw, JWT_SECRET, algorithms=[JWT_ALGO])
    except jwt.PyJWTError:
        raise HTTPException(status_code=403, detail="Token tidak valid")
