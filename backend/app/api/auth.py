from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db, User
from app.core.security import (
    verify_password, hash_password, create_access_token,
    get_current_user, require_role,
)
from app.schemas.schemas import (
    LoginRequest, TokenResponse, MeResponse, PasswordChange,
    UserCreate, UserUpdate, UserResponse,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == body.email).first()
    if not user or not verify_password(body.password, user.password):
        raise HTTPException(status_code=401, detail="E-mail ou senha inválidos")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Conta desativada")

    token = create_access_token({
        "sub": str(user.id),
        "email": user.email,
        "role": user.role,
    })
    return TokenResponse(access_token=token)


@router.get("/me", response_model=MeResponse)
def me(current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == int(current_user["sub"])).first()
    if not user:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    return user


@router.put("/password")
def change_password(body: PasswordChange, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == int(current_user["sub"])).first()
    if not verify_password(body.current_password, user.password):
        raise HTTPException(status_code=400, detail="Senha atual incorreta")
    user.password = hash_password(body.new_password)
    user.updated_at = datetime.utcnow()
    db.commit()
    return {"detail": "Senha alterada com sucesso"}


# ---- CRUD Usuários ----

@router.post("/register", response_model=UserResponse, status_code=201)
def register(body: UserCreate, db: Session = Depends(get_db), _=Depends(require_role("admin"))):
    if db.query(User).filter(User.email == body.email).first():
        raise HTTPException(status_code=400, detail="E-mail já cadastrado")
    user = User(
        name=body.name,
        email=body.email,
        password=hash_password(body.password),
        role=body.role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user
