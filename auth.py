"""
Autentikasi: JWT + bcrypt + dependency RBAC untuk FastAPI.
Setara dengan blok authenticateToken + bcrypt + jwt pada server.js.

Catatan: hash password di DB dibuat oleh bcrypt Node ($2b$...) dan
sepenuhnya kompatibel dengan paket `bcrypt` Python.
"""
import datetime
import logging
import os
import secrets

import bcrypt
import jwt
from fastapi import Header, HTTPException, Query

# Kunci JWT. Prioritas resolve:
#   1. env var JWT_SECRET       -> menang, dipakai apa adanya (deploy prod).
#   2. file .jwt_secret di CWD  -> baca kalau ada (persistent per-install).
#   3. auto-generate 64 hex     -> tulis ke .jwt_secret, log info sekali.
#
# Ini menghilangkan shared-default hardcoded yang bisa dipakai attacker mana pun
# untuk forge token. Setiap install fresh dapat unique secret. .jwt_secret
# harus di-gitignore + protect (600 di POSIX). PyJWT >=2.13 wajib >=32 bytes
# utk HS256; 64 hex chars = 32 bytes, memenuhi.
_JWT_SECRET_FILE = ".jwt_secret"


def _load_or_create_secret() -> str:
    env = os.environ.get("JWT_SECRET")
    if env:
        return env
    try:
        with open(_JWT_SECRET_FILE, "r", encoding="utf-8") as f:
            v = f.read().strip()
            if v and len(v) >= 32:
                return v
    except FileNotFoundError:
        pass
    except OSError:
        pass
    # Generate + persist. 32 bytes = 64 hex chars.
    v = secrets.token_hex(32)
    try:
        with open(_JWT_SECRET_FILE, "w", encoding="utf-8") as f:
            f.write(v)
        try:
            os.chmod(_JWT_SECRET_FILE, 0o600)  # POSIX; no-op di Windows.
        except (OSError, NotImplementedError):
            pass
        logging.getLogger("auth").info(
            "JWT_SECRET baru di-generate + disimpan ke %s (unique per-install).",
            _JWT_SECRET_FILE)
    except OSError as e:
        logging.getLogger("auth").warning(
            "Gagal tulis %s (%s). Secret ephemeral -- semua token invalid saat restart!",
            _JWT_SECRET_FILE, e)
    return v


JWT_SECRET = _load_or_create_secret()
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
