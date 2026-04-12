"""
Enterprise SSO and JWT Authentication Layer.

Handles:
- OIDC Callback (Simulated enterprise flow)
- JWT Token Generation & Verification
- FastAPI Security Dependencies
- User Session Management
"""

import json
import os
import time
from datetime import datetime, timedelta
from typing import Optional, Dict

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

# --- Configuration ---
# In production, these should be in .env
SECRET_KEY = os.getenv("JWT_SECRET_KEY", "74889c1694d9b2323c6f2a7a371c6ca674889c1694d")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 1 week

# Feature flag to entirely disable auth during local dev/testing
AUTH_DISABLED = os.getenv("CODEMIND_AUTH_DISABLED", "false").lower() == "true"

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/v1/auth/token", auto_error=False)

# --- JWT Helpers ---

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def decode_access_token(token: str) -> Optional[dict]:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        return None

# --- Dependencies ---

async def get_current_user(
    token: str = Depends(oauth2_scheme)
) -> Optional[dict]:
    """
    Dependency to get the current authenticated user from JWT.
    Returns the user dict if valid, otherwise None (allowing public access where needed).
    """
    # Dynamically check ENV every request so toggles don't require server restarts
    if os.getenv("CODEMIND_AUTH_DISABLED", "false").lower() == "true":
        return {
            "user_id": "auth_disabled_override",
            "email": "localdeveloper@codemind.local",
            "full_name": "Local Overrider",
            "role": "admin",
            "department": "Engineering"
        }
        
    if not token:
        return None
        
    payload = decode_access_token(token)
    if not payload or "sub" not in payload:
        return None
        
    # In a real app, we would fetch from DB here or just use payload
    return {
        "user_id": payload.get("sub"),
        "email": payload.get("email"),
        "full_name": payload.get("name"),
        "role": payload.get("role", "user"),
        "department": payload.get("dept")
    }

async def require_user(user: Optional[dict] = Depends(get_current_user)):
    """Strong dependency to force authentication."""
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user

async def require_admin(user: dict = Depends(require_user)):
    """Strong dependency to force admin role."""
    if user.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required"
        )
    return user

# --- OIDC / SSO Mock flow ---

def sync_user_to_db(id_token_payload: dict, db: Session):
    """
    Syncs user information from SSO provider into our local SQLite.
    Called during login/callback.
    """
    from ..storage.database import UserRecord
    
    sub = id_token_payload.get("sub")
    if not sub:
        return None
        
    user = db.query(UserRecord).filter_by(user_id=sub).first()
    now = int(time.time())
    
    if not user:
        user = UserRecord(
            user_id=sub,
            email=id_token_payload.get("email", ""),
            full_name=id_token_payload.get("name", ""),
            role=id_token_payload.get("role", "user"),
            department=id_token_payload.get("dept", "General"),
            created_at=now
        )
        db.add(user)
    
    user.last_login = now
    db.commit()
    db.refresh(user)
    return user
