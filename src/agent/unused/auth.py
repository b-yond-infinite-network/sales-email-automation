"""
Authentication and authorization system for the Success Stories Knowledge Base
This module provides JWT-based authentication, user management, and security utilities.
"""

import os
import psycopg2
import psycopg2.extras
import hashlib
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
from pathlib import Path
from collections import defaultdict

import jwt
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from src.agent.config import Config
from src.agent.logger import get_logger

logger = get_logger(__name__)
config = Config()

# Security configuration
try:
    # Try bcrypt first
    pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
    logger.info("Using bcrypt for password hashing")
except Exception as e:
    logger.warning(f"bcrypt initialization failed: {e}")
    # Fallback to pbkdf2_sha256 if bcrypt fails
    try:
        pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")
        logger.info("Using pbkdf2_sha256 for password hashing (bcrypt fallback)")
    except Exception as e2:
        logger.error(f"All password hashing schemes failed: {e2}")
        # Last resort - use basic sha256 (not recommended for production)
        pwd_context = CryptContext(schemes=["sha256_crypt"], deprecated="auto")
        logger.warning("Using sha256_crypt for password hashing (emergency fallback)")
security = HTTPBearer(auto_error=False)

# Pydantic Models
class LockoutStatus(BaseModel):
    is_locked: bool
    remaining_minutes: int
    remaining_seconds: int
    failed_attempts: int
    max_attempts: int
    message: str

# Security Helper Functions
def get_client_ip(request: Request) -> str:
    """Get client IP address, considering proxy headers"""
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip
    
    return request.client.host if request.client else "unknown"

def is_rate_limited(client_ip: str) -> bool:
    """Check if client IP is rate limited"""
    now = time.time()
    window_start = now - (RATE_LIMIT_WINDOW_MINUTES * 60)
    
    # Clean old entries
    request_counts[client_ip] = [
        timestamp for timestamp in request_counts[client_ip] 
        if timestamp > window_start
    ]
    
    # Check if limit exceeded
    if len(request_counts[client_ip]) >= MAX_REQUESTS_PER_WINDOW:
        return True
    
    # Add current request
    request_counts[client_ip].append(now)
    return False

def is_account_locked(client_ip: str) -> bool:
    """Check if account is locked due to failed login attempts"""
    now = time.time()
    lockout_end = now - (LOCKOUT_DURATION_MINUTES * 60)
    
    # Clean old attempts
    login_attempts[client_ip] = [
        timestamp for timestamp in login_attempts[client_ip]
        if timestamp > lockout_end
    ]
    
    return len(login_attempts[client_ip]) >= MAX_LOGIN_ATTEMPTS

def get_lockout_remaining_time(client_ip: str) -> tuple[int, int]:
    """Get remaining lockout time in minutes and seconds"""
    if client_ip not in login_attempts or len(login_attempts[client_ip]) == 0:
        return (0, 0)
    
    # Get the time when the lockout period ends (latest attempt + lockout duration)
    latest_attempt = max(login_attempts[client_ip])
    lockout_expires_at = latest_attempt + (LOCKOUT_DURATION_MINUTES * 60)
    
    now = time.time()
    remaining_seconds = max(0, lockout_expires_at - now)
    remaining_minutes = max(0, int(remaining_seconds / 60))
    remaining_secs = max(0, int(remaining_seconds % 60))
    
    return (remaining_minutes, remaining_secs)

def get_lockout_status(client_ip: str) -> LockoutStatus:
    """Get comprehensive lockout status for an IP"""
    is_locked = is_account_locked(client_ip)
    remaining_minutes, remaining_seconds = get_lockout_remaining_time(client_ip)
    
    # Get current failed attempts count
    failed_attempts = len(login_attempts.get(client_ip, []))
    
    if not is_locked:
        if failed_attempts > 0:
            message = f"Warning: {failed_attempts} of {MAX_LOGIN_ATTEMPTS} failed attempts. Account will be locked after {MAX_LOGIN_ATTEMPTS} failed attempts."
        else:
            message = "Account is not locked"
            
        return LockoutStatus(
            is_locked=False,
            remaining_minutes=0,
            remaining_seconds=0,
            failed_attempts=failed_attempts,
            max_attempts=MAX_LOGIN_ATTEMPTS,
            message=message
        )
    
    # Format the lockout message with attempt info
    if remaining_minutes > 0:
        if remaining_minutes == 1:
            message = f"Account locked after {failed_attempts} failed attempts. Try again in 1 minute and {remaining_seconds} seconds."
        else:
            message = f"Account locked after {failed_attempts} failed attempts. Try again in {remaining_minutes} minutes and {remaining_seconds} seconds."
    else:
        if remaining_seconds == 1:
            message = f"Account locked after {failed_attempts} failed attempts. Try again in 1 second."
        else:
            message = f"Account locked after {failed_attempts} failed attempts. Try again in {remaining_seconds} seconds."
    
    return LockoutStatus(
        is_locked=True,
        remaining_minutes=remaining_minutes,
        remaining_seconds=remaining_seconds,
        failed_attempts=failed_attempts,
        max_attempts=MAX_LOGIN_ATTEMPTS,
        message=message
    )

def record_failed_login(client_ip: str):
    """Record a failed login attempt"""
    login_attempts[client_ip].append(time.time())

def clear_login_attempts(client_ip: str):
    """Clear login attempts after successful login"""
    if client_ip in login_attempts:
        del login_attempts[client_ip]

def generate_jti() -> str:
    """Generate unique JWT ID"""
    return str(uuid.uuid4())

def is_token_blacklisted(jti: str) -> bool:
    """Check if token is blacklisted"""
    return jti in BLACKLISTED_TOKENS

def blacklist_token(jti: str):
    """Add token to blacklist"""
    BLACKLISTED_TOKENS.add(jti)

# JWT Configuration
JWT_SECRET_KEY = config.JWT_SECRET_KEY
JWT_ALGORITHM = config.JWT_ALGORITHM
JWT_ACCESS_TOKEN_EXPIRE_MINUTES = 30
JWT_REFRESH_TOKEN_EXPIRE_DAYS = 7

# Security Configuration
MAX_LOGIN_ATTEMPTS = 5
LOCKOUT_DURATION_MINUTES = 30
RATE_LIMIT_WINDOW_MINUTES = 15
MAX_REQUESTS_PER_WINDOW = 100

# Add JTI (JWT ID) tracking for token blacklisting
BLACKLISTED_TOKENS = set()  # In production, use Redis or database

# Rate limiting storage (in production, use Redis)
login_attempts = defaultdict(list)
request_counts = defaultdict(list)

class UserCreate(BaseModel):
    username: str
    email: EmailStr
    password: str
    is_admin: bool = False

class UserLogin(BaseModel):
    username: str
    password: str

class PasswordChange(BaseModel):
    current_password: str
    new_password: str

class UserUpdate(BaseModel):
    username: Optional[str] = None
    email: Optional[EmailStr] = None
    is_admin: Optional[bool] = None
    is_active: Optional[bool] = None

class User(BaseModel):
    id: int
    username: str
    email: str
    is_admin: bool
    is_active: bool
    created_at: datetime

class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    expires_in: int

class DatabaseManager:
    """Manages PostgreSQL database for user authentication"""
    
    def __init__(self, connection_params: dict = None):
        if connection_params is None:
            connection_params = {
                'host': config.POSTGRES_HOST,
                'port': config.POSTGRES_PORT,
                'database': config.POSTGRES_DB,
                'user': config.POSTGRES_USER,
                'password': config.POSTGRES_PASSWORD
            }
        self.connection_params = connection_params
        self.init_database()
    
    def get_connection(self):
        """Get a database connection"""
        return psycopg2.connect(**self.connection_params)
    
    def init_database(self):
        """Initialize the users database"""
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    # Create users table
                    cursor.execute("""
                        CREATE TABLE IF NOT EXISTS users (
                            id SERIAL PRIMARY KEY,
                            username VARCHAR(255) UNIQUE NOT NULL,
                            email VARCHAR(255) UNIQUE NOT NULL,
                            password_hash TEXT NOT NULL,
                            is_admin BOOLEAN DEFAULT FALSE,
                            is_active BOOLEAN DEFAULT TRUE,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            last_login TIMESTAMP
                        )
                    """)
                    
                    # Create refresh tokens table
                    cursor.execute("""
                        CREATE TABLE IF NOT EXISTS refresh_tokens (
                            id SERIAL PRIMARY KEY,
                            user_id INTEGER NOT NULL,
                            token_hash TEXT NOT NULL,
                            expires_at TIMESTAMP NOT NULL,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
                        )
                    """)
                    
                    # Create default admin user if no users exist
                    cursor.execute("SELECT COUNT(*) FROM users")
                    user_count = cursor.fetchone()[0]
                    
                    if user_count == 0:
                        admin_password = config.ADMIN_PASSWORD
                        if len(admin_password.encode('utf-8')) > 72:
                            logger.error("Admin password exceeds bcrypt's 72-byte limit. Please use a shorter password.")
                            raise ValueError("Admin password exceeds bcrypt's 72-byte limit. Please use a shorter password.")
                        
                        try:
                            password_hash = pwd_context.hash(admin_password)
                        except Exception as hash_error:
                            logger.error(f"Password hashing failed: {hash_error}")
                            # Use a simple fallback for initialization
                            import hashlib
                            password_hash = hashlib.sha256(admin_password.encode()).hexdigest()
                            logger.warning("Using SHA256 fallback for admin password")
                        
                        cursor.execute("""
                            INSERT INTO users (username, email, password_hash, is_admin)
                            VALUES (%s, %s, %s, %s)
                        """, ("admin", "admin@example.com", password_hash, True))
                        
                    
                    conn.commit()
                    logger.info("PostgreSQL database initialized successfully")
                
        except Exception as e:
            logger.error(f"Database initialization failed: {e}")
            raise
    
    def create_user(self, user_data: UserCreate) -> User:
        """Create a new user"""
        # Ensure password is under bcrypt 72-byte limit
        password = user_data.password[:72] if len(user_data.password.encode('utf-8')) > 72 else user_data.password
        
        try:
            password_hash = pwd_context.hash(password)
        except Exception as hash_error:
            logger.error(f"Password hashing failed: {hash_error}")
            # Use a simple fallback for user creation
            import hashlib
            password_hash = hashlib.sha256(password.encode()).hexdigest()
            logger.warning("Using SHA256 fallback for user password")
        
        try:
            with self.get_connection() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                    cursor.execute("""
                        INSERT INTO users (username, email, password_hash, is_admin)
                        VALUES (%s, %s, %s, %s) RETURNING *
                    """, (user_data.username, user_data.email, password_hash, user_data.is_admin))
                    
                    user_row = cursor.fetchone()
                    conn.commit()
                    
                    return User(
                        id=user_row["id"],
                        username=user_row["username"],
                        email=user_row["email"],
                        is_admin=bool(user_row["is_admin"]),
                        is_active=bool(user_row["is_active"]),
                        created_at=user_row["created_at"]
                    )
                
        except psycopg2.IntegrityError as e:
            if "username" in str(e):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Username already exists"
                )
            elif "email" in str(e):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Email already exists"
                )
            else:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="User creation failed"
                )
        except Exception as e:
            logger.error(f"Failed to create user: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Internal server error"
            )
    
    def authenticate_user(self, username: str, password: str) -> Optional[User]:
        """Authenticate a user and return user data if valid"""
        try:
            with self.get_connection() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                    cursor.execute(
                        "SELECT * FROM users WHERE username = %s AND is_active = TRUE",
                        (username,)
                    )
                    user_row = cursor.fetchone()
                    
                    if not user_row:
                        return None
                    
                    # Try bcrypt verification first
                    password_valid = False
                    try:
                        password_valid = pwd_context.verify(password, user_row["password_hash"])
                    except Exception as verify_error:
                        logger.warning(f"bcrypt verification failed: {verify_error}")
                        # Fallback to SHA256 verification
                        import hashlib
                        sha256_hash = hashlib.sha256(password.encode()).hexdigest()
                        password_valid = (sha256_hash == user_row["password_hash"])
                        if password_valid:
                            logger.warning("Authenticated using SHA256 fallback")
                    
                    if not password_valid:
                        return None
                    
                    # Update last login
                    cursor.execute(
                        "UPDATE users SET last_login = CURRENT_TIMESTAMP WHERE id = %s",
                        (user_row["id"],)
                    )
                    conn.commit()
                    
                    return User(
                        id=user_row["id"],
                        username=user_row["username"],
                        email=user_row["email"],
                        is_admin=bool(user_row["is_admin"]),
                        is_active=bool(user_row["is_active"]),
                        created_at=user_row["created_at"]
                    )
                
        except Exception as e:
            logger.error(f"Failed to authenticate user: {e}")
            return None
    
    def get_user_by_id(self, user_id: int) -> Optional[User]:
        """Get user by ID"""
        try:
            with self.get_connection() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                    cursor.execute(
                        "SELECT * FROM users WHERE id = %s AND is_active = TRUE",
                        (user_id,)
                    )
                    user_row = cursor.fetchone()
                    
                    if not user_row:
                        return None
                    
                    return User(
                        id=user_row["id"],
                        username=user_row["username"],
                        email=user_row["email"],
                        is_admin=bool(user_row["is_admin"]),
                        is_active=bool(user_row["is_active"]),
                        created_at=user_row["created_at"]
                    )
                
        except Exception as e:
            logger.error(f"Failed to get user by ID: {e}")
            return None
    
    def get_all_users(self) -> list[User]:
        """Get all active users"""
        try:
            with self.get_connection() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                    cursor.execute(
                        "SELECT * FROM users WHERE is_active = TRUE ORDER BY created_at DESC"
                    )
                    user_rows = cursor.fetchall()
                    
                    users = []
                    for user_row in user_rows:
                        users.append(User(
                            id=user_row["id"],
                            username=user_row["username"],
                            email=user_row["email"],
                            is_admin=bool(user_row["is_admin"]),
                            is_active=bool(user_row["is_active"]),
                            created_at=user_row["created_at"]
                        ))
                    
                    return users
                
        except Exception as e:
            logger.error(f"Failed to get all users: {e}")
            return []
    
    def update_password(self, user_id: int, new_password: str) -> bool:
        """Update user password"""
        # Ensure password is under bcrypt 72-byte limit
        password = new_password[:72] if len(new_password.encode('utf-8')) > 72 else new_password
        
        try:
            password_hash = pwd_context.hash(password)
        except Exception as hash_error:
            logger.error(f"Password hashing failed: {hash_error}")
            # Use a simple fallback
            import hashlib
            password_hash = hashlib.sha256(password.encode()).hexdigest()
            logger.warning("Using SHA256 fallback for password update")
        
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("""
                        UPDATE users SET password_hash = %s WHERE id = %s AND is_active = TRUE
                    """, (password_hash, user_id))
                    
                    if cursor.rowcount == 0:
                        return False
                    
                    conn.commit()
                    return True
                
        except Exception as e:
            logger.error(f"Failed to update password: {e}")
            return False
    
    def update_user(self, user_id: int, username: str = None, email: str = None, 
                    is_admin: bool = None, is_active: bool = None) -> Optional[User]:
        """Update user information"""
        try:
            with self.get_connection() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                    
                    # Build dynamic update query
                    update_fields = []
                    update_values = []
                    
                    if username is not None:
                        update_fields.append("username = %s")
                        update_values.append(username)
                    
                    if email is not None:
                        update_fields.append("email = %s")
                        update_values.append(email)
                    
                    if is_admin is not None:
                        update_fields.append("is_admin = %s")
                        update_values.append(is_admin)
                    
                    if is_active is not None:
                        update_fields.append("is_active = %s")
                        update_values.append(is_active)
                    
                    if not update_fields:
                        # No fields to update, return current user
                        return self.get_user_by_id(user_id)
                    
                    # Add user_id to values for WHERE clause
                    update_values.append(user_id)
                    
                    update_query = f"""
                        UPDATE users SET {', '.join(update_fields)} 
                        WHERE id = %s
                    """
                    
                    cursor.execute(update_query, update_values)
                    
                    if cursor.rowcount == 0:
                        return None
                    
                    conn.commit()
                    
                    # Return updated user
                    cursor.execute("SELECT * FROM users WHERE id = %s", (user_id,))
                    user_row = cursor.fetchone()
                    if not user_row:
                        return None
                    
                    return User(
                        id=user_row["id"],
                        username=user_row["username"],
                        email=user_row["email"],
                        is_admin=bool(user_row["is_admin"]),
                        is_active=bool(user_row["is_active"]),
                        created_at=user_row["created_at"]
                    )
                
        except psycopg2.IntegrityError as e:
            logger.error(f"Integrity error updating user: {e}")
            if "username" in str(e):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Username already exists"
                )
            elif "email" in str(e):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Email already exists"
                )
            else:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="User update failed"
                )
        except Exception as e:
            logger.error(f"Failed to update user: {e}")
            return None
    
    def delete_user(self, user_id: int, soft_delete: bool = True) -> bool:
        """Delete user (soft delete by default)"""
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    if soft_delete:
                        # Soft delete - set is_active to False
                        cursor.execute("""
                            UPDATE users SET is_active = FALSE WHERE id = %s
                        """, (user_id,))
                    else:
                        # Hard delete - actually remove from database
                        # First delete related refresh tokens
                        cursor.execute("DELETE FROM refresh_tokens WHERE user_id = %s", (user_id,))
                        
                        # Then delete user
                        cursor.execute("DELETE FROM users WHERE id = %s", (user_id,))
                    
                    if cursor.rowcount == 0:
                        return False
                    
                    conn.commit()
                    return True
                
        except Exception as e:
            logger.error(f"Failed to delete user: {e}")
            return False
    
    def store_refresh_token(self, user_id: int, refresh_token: str) -> None:
        """Store refresh token hash in database"""
        token_hash = hashlib.sha256(refresh_token.encode()).hexdigest()
        expires_at = datetime.now(timezone.utc) + timedelta(days=JWT_REFRESH_TOKEN_EXPIRE_DAYS)
        
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    # Clean up expired tokens
                    cursor.execute(
                        "DELETE FROM refresh_tokens WHERE expires_at < CURRENT_TIMESTAMP"
                    )
                    
                    # Store new token
                    cursor.execute("""
                        INSERT INTO refresh_tokens (user_id, token_hash, expires_at)
                        VALUES (%s, %s, %s)
                    """, (user_id, token_hash, expires_at))
                    
                    conn.commit()
                
        except Exception as e:
            logger.error(f"Failed to store refresh token: {e}")
    
    def validate_refresh_token(self, refresh_token: str) -> Optional[int]:
        """Validate refresh token and return user_id if valid"""
        token_hash = hashlib.sha256(refresh_token.encode()).hexdigest()
        
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("""
                        SELECT user_id FROM refresh_tokens 
                        WHERE token_hash = %s AND expires_at > CURRENT_TIMESTAMP
                    """, (token_hash,))
                    
                    result = cursor.fetchone()
                    return result[0] if result else None
                
        except Exception as e:
            logger.error(f"Failed to validate refresh token: {e}")
            return None
    
    def revoke_refresh_token(self, refresh_token: str):
        """Revoke a specific refresh token"""
        token_hash = hashlib.sha256(refresh_token.encode()).hexdigest()
        
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("""
                        DELETE FROM refresh_tokens WHERE token_hash = %s
                    """, (token_hash,))
                    conn.commit()
                    
        except Exception as e:
            logger.error(f"Failed to revoke refresh token: {e}")
    
    def revoke_all_user_tokens(self, user_id: int):
        """Revoke all refresh tokens for a user"""
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("""
                        DELETE FROM refresh_tokens WHERE user_id = %s
                    """, (user_id,))
                    conn.commit()
                    
        except Exception as e:
            logger.error(f"Failed to revoke user tokens: {e}")

# Database manager instance - initialized lazily
_db_manager = None

def get_db_manager():
    """Get or create the database manager instance"""
    global _db_manager
    if _db_manager is None:
        _db_manager = DatabaseManager()
    return _db_manager

class AuthManager:
    """Manages JWT tokens and authentication"""
    
    @staticmethod
    def create_access_token(data: Dict[str, Any]) -> str:
        """Create JWT access token with enhanced security"""
        to_encode = data.copy()
        jti = generate_jti()
        expire = datetime.now(timezone.utc) + timedelta(minutes=JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
        
        to_encode.update({
            "exp": expire,
            "iat": datetime.now(timezone.utc),  # Issued at
            "jti": jti,  # JWT ID for blacklisting
            "type": "access"
        })
        
        return jwt.encode(to_encode, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
    
    @staticmethod
    def create_refresh_token(data: Dict[str, Any]) -> str:
        """Create JWT refresh token with enhanced security"""
        to_encode = data.copy()
        jti = generate_jti()
        expire = datetime.now(timezone.utc) + timedelta(days=JWT_REFRESH_TOKEN_EXPIRE_DAYS)
        
        to_encode.update({
            "exp": expire,
            "iat": datetime.now(timezone.utc),  # Issued at
            "jti": jti,  # JWT ID for blacklisting
            "type": "refresh"
        })
        
        return jwt.encode(to_encode, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
    
    @staticmethod
    def verify_token(token: str, token_type: str = "access") -> Optional[Dict[str, Any]]:
        """Verify JWT token with enhanced security checks"""
        try:
            payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
            
            # Check token type
            if payload.get("type") != token_type:
                logger.warning(f"Invalid token type: expected {token_type}, got {payload.get('type')}")
                return None
            
            # Check if token is blacklisted
            jti = payload.get("jti")
            if jti and is_token_blacklisted(jti):
                logger.warning(f"Token is blacklisted: {jti}")
                return None
            
            # Additional security: check issued at time (prevent replay attacks)
            iat = payload.get("iat")
            if iat:
                issued_at = datetime.fromtimestamp(iat, timezone.utc)
                # Reject tokens issued more than their lifetime ago (prevents replay)
                max_age = timedelta(days=JWT_REFRESH_TOKEN_EXPIRE_DAYS) if token_type == "refresh" else timedelta(hours=1)
                if datetime.now(timezone.utc) - issued_at > max_age:
                    logger.warning("Token too old, possible replay attack")
                    return None
                
            return payload
            
        except jwt.ExpiredSignatureError:
            logger.warning("Token has expired")
            return None
        except jwt.InvalidTokenError as e:
            logger.warning(f"Invalid token: {e}")
            return None
    
    @staticmethod
    def create_tokens_for_user(user: User) -> TokenResponse:
        """Create access and refresh tokens for user"""
        token_data = {
            "sub": str(user.id),
            "username": user.username,
            "is_admin": user.is_admin
        }
        
        access_token = AuthManager.create_access_token(token_data)
        refresh_token = AuthManager.create_refresh_token(token_data)
        
        # Store refresh token hash in database
        # Store refresh token for future use
        get_db_manager().store_refresh_token(user.id, refresh_token)
        
        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60
        )

# Dependency functions for FastAPI
async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)
) -> User:
    """Get current authenticated user with enhanced security checks"""
    client_ip = get_client_ip(request)
    
    # Rate limiting for API requests
    if is_rate_limited(client_ip):
        logger.warning(f"API rate limit exceeded for IP: {client_ip}")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please try again later."
        )
    
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    token = None
    
    # First try to get token from Authorization header
    if credentials:
        token = credentials.credentials
    else:
        # If no header token, try query parameter (less secure, log warning)
        token = request.query_params.get("token")
        if token:
            logger.warning(f"Token provided via query parameter from IP: {client_ip}")
    
    if not token:
        raise credentials_exception
    
    try:
        payload = AuthManager.verify_token(token, "access")
        
        if payload is None:
            raise credentials_exception
        
        user_id = int(payload.get("sub"))
        if user_id is None:
            raise credentials_exception
        
        user = get_db_manager().get_user_by_id(user_id)
        if user is None:
            raise credentials_exception
        
        return user
        
    except (ValueError, TypeError):
        raise credentials_exception

async def get_admin_user(current_user: User = Depends(get_current_user)) -> User:
    """Ensure current user is an admin"""
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not enough permissions"
        )
    return current_user

# Authentication functions
def login_user(username: str, password: str, request: Request) -> TokenResponse:
    """Login user with enhanced security checks"""
    client_ip = get_client_ip(request)
    
    # Rate limiting check
    if is_rate_limited(client_ip):
        logger.warning(f"Rate limit exceeded for IP: {client_ip}")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please try again later."
        )
    
    # Account lockout check
    if is_account_locked(client_ip):
        remaining_minutes, remaining_seconds = get_lockout_remaining_time(client_ip)
        failed_attempts = len(login_attempts.get(client_ip, []))
        logger.warning(f"Account locked for IP: {client_ip}, {remaining_minutes}m {remaining_seconds}s remaining, {failed_attempts} attempts")
        
        if remaining_minutes <= 0 and remaining_seconds <= 0:
            # Lockout period has expired, clear attempts
            clear_login_attempts(client_ip)
        else:
            # Format the remaining time message with attempt count
            if remaining_minutes > 0:
                if remaining_minutes == 1:
                    time_message = f"1 minute and {remaining_seconds} seconds"
                else:
                    time_message = f"{remaining_minutes} minutes and {remaining_seconds} seconds"
            else:
                if remaining_seconds == 1:
                    time_message = "1 second"
                else:
                    time_message = f"{remaining_seconds} seconds"
                
            raise HTTPException(
                status_code=status.HTTP_423_LOCKED,
                detail=f"Account temporarily locked after {failed_attempts} failed attempts. Try again in {time_message}."
            )
    
    # Authenticate user
    user = get_db_manager().authenticate_user(username, password)
    if not user:
        # Record failed attempt
        record_failed_login(client_ip)
        logger.warning(f"Failed login attempt for username: {username}, IP: {client_ip}")
        
        # Generic error message to prevent username enumeration
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials"
        )
    
    # Clear failed attempts on successful login
    clear_login_attempts(client_ip)
    
    logger.info(f"Successful login for user: {username}, IP: {client_ip}")
    return AuthManager.create_tokens_for_user(user)

def refresh_access_token(refresh_token: str) -> TokenResponse:
    """Refresh access token with enhanced security"""
    payload = AuthManager.verify_token(refresh_token, "refresh")
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token"
        )
    
    user_id = int(payload.get("sub"))
    user = get_db_manager().get_user_by_id(user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found"
        )
    
    # Validate refresh token in database
    if not get_db_manager().validate_refresh_token(refresh_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token"
        )
    
    # Blacklist the old refresh token (token rotation)
    old_jti = payload.get("jti")
    if old_jti:
        blacklist_token(old_jti)
        logger.info(f"Blacklisted old refresh token: {old_jti}")
    
    # Remove old refresh token from database and issue new tokens
    get_db_manager().revoke_refresh_token(refresh_token)
    
    logger.info(f"Token refreshed for user: {user.username}")
    return AuthManager.create_tokens_for_user(user)

def logout_user(access_token: str, refresh_token: Optional[str] = None) -> Dict[str, str]:
    """Secure logout that blacklists tokens"""
    try:
        # Blacklist access token
        access_payload = AuthManager.verify_token(access_token, "access")
        if access_payload and access_payload.get("jti"):
            blacklist_token(access_payload["jti"])
            logger.info(f"Blacklisted access token: {access_payload['jti']}")
        
        # Blacklist and revoke refresh token if provided
        if refresh_token:
            refresh_payload = AuthManager.verify_token(refresh_token, "refresh")
            if refresh_payload and refresh_payload.get("jti"):
                blacklist_token(refresh_payload["jti"])
                logger.info(f"Blacklisted refresh token: {refresh_payload['jti']}")
            
            # Remove from database
            get_db_manager().revoke_refresh_token(refresh_token)
        
        return {"message": "Successfully logged out"}
        
    except Exception as e:
        logger.error(f"Logout error: {e}")
        return {"message": "Logged out with errors"}

def get_all_users() -> list[User]:
    """Get all users from the database"""
    return get_db_manager().get_all_users()