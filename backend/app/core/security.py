from datetime import datetime, timedelta
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, APIKeyHeader
from sqlalchemy.orm import Session

from app.core.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")
oauth2_scheme_optional = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)
api_key_scheme = APIKeyHeader(name="X-API-Key", auto_error=False, description="API key de agente/integração (somente leitura)")


# ---- Senha ----

def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


# ---- JWT ----

def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(hours=settings.JWT_EXPIRATION_HOURS)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        return payload
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido ou expirado",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ---- Dependências de autenticação ----

def get_current_user(token: str = Depends(oauth2_scheme)) -> dict:
    """Retorna payload do JWT (user_id, email, role)."""
    payload = decode_access_token(token)
    if "sub" not in payload:
        raise HTTPException(status_code=401, detail="Token malformado")
    return payload


def require_role(*roles: str):
    """Factory — retorna dependência que verifica se o usuário tem uma das roles."""
    def checker(current_user: dict = Depends(get_current_user)):
        if current_user.get("role") not in roles:
            raise HTTPException(status_code=403, detail="Permissão insuficiente")
        return current_user
    return checker


# ---- API Keys (agentes de IA / integrações) ----

import hashlib
import secrets


from app.core.database import ApiKey, get_db  # noqa: E402

API_KEY_PREFIX = "tabk_"


def generate_api_key() -> tuple[str, str, str]:
    """Gera uma nova API key. Retorna (chave_completa, prefixo, hash)."""
    raw = secrets.token_hex(24)
    full_key = f"{API_KEY_PREFIX}{raw}"
    key_hash = hashlib.sha256(full_key.encode()).hexdigest()
    return full_key, full_key[:12], key_hash


def get_read_principal(
    x_api_key: str | None = Depends(api_key_scheme),
    token: str | None = Depends(oauth2_scheme_optional),
    db: Session = Depends(get_db),
) -> dict:
    """Autentica via X-API-Key OU JWT (oauth2_scheme é opcional aqui).

    Para endpoints somente leitura — API keys têm papel efetivo de viewer.
    """
    if x_api_key:
        key_hash = hashlib.sha256(x_api_key.encode()).hexdigest()
        api_key = db.query(ApiKey).filter(
            ApiKey.key_hash == key_hash, ApiKey.is_active == True  # noqa: E712
        ).first()
        if not api_key:
            raise HTTPException(status_code=401, detail="API key inválida ou revogada")
        api_key.last_used_at = datetime.utcnow()
        db.commit()
        return {"sub": f"apikey:{api_key.id}", "role": "viewer", "auth": "api_key"}

    # Fallback: JWT normal
    if not token:
        raise HTTPException(status_code=401, detail="Não autenticado", headers={"WWW-Authenticate": "Bearer"})
    payload = decode_access_token(token)
    if "sub" not in payload:
        raise HTTPException(status_code=401, detail="Token malformado")
    return payload
