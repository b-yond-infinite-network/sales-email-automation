"""
Authentication API endpoints for the Success Stories Knowledge Base
"""

from typing import Optional
from fastapi import APIRouter, HTTPException, status, Depends, Request
from fastapi.security import HTTPAuthorizationCredentials

from src.agent.auth import (
    get_current_user, User, UserCreate, UserLogin, TokenResponse, PasswordChange, UserUpdate, LockoutStatus,
    get_db_manager, login_user, refresh_access_token, logout_user,
    get_all_users, security, get_admin_user, get_lockout_status, get_client_ip, MAX_LOGIN_ATTEMPTS
)
from src.agent.logger import get_logger

logger = get_logger(__name__)

# Create authentication router
auth_router = APIRouter(prefix="/auth", tags=["authentication"])

@auth_router.options("/login")
async def login_options():
    """Handle OPTIONS preflight request for login"""
    return {"message": "OK"}

@auth_router.post("/login", response_model=TokenResponse)
async def login(login_data: UserLogin, request: Request):
    """
    Login with username and password to get access and refresh tokens
    Enhanced with rate limiting and brute force protection
    """
    try:
        return login_user(login_data.username, login_data.password, request)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Login error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Login failed"
        )

@auth_router.get("/lockout-status", response_model=LockoutStatus)
async def check_lockout_status(request: Request):
    """
    Check account lockout status with real-time countdown
    Public endpoint - no authentication required
    """
    try:
        client_ip = get_client_ip(request)
        return get_lockout_status(client_ip)
    except Exception as e:
        logger.error(f"Lockout status check error: {e}")
        return LockoutStatus(
            is_locked=False,
            remaining_minutes=0,
            remaining_seconds=0,
            failed_attempts=0,
            max_attempts=MAX_LOGIN_ATTEMPTS,
            message="Unable to check lockout status"
        )

@auth_router.post("/refresh", response_model=TokenResponse)
async def refresh_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """
    Refresh access token using refresh token
    """
    try:
        refresh_token = credentials.credentials
        return refresh_access_token(refresh_token)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Token refresh error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Token refresh failed"
        )

@auth_router.post("/logout")
async def logout(
    request: Request,
    current_user: User = Depends(get_current_user),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)
):
    """
    Secure logout that blacklists current tokens
    """
    try:
        access_token = None
        if credentials:
            access_token = credentials.credentials
        else:
            # Try to get from query param as fallback
            access_token = request.query_params.get("token")
        
        # Get refresh token from request body if provided
        refresh_token = None
        if request.method == "POST":
            try:
                body = await request.json()
                refresh_token = body.get("refresh_token")
            except:
                pass  # No refresh token provided
        
        result = logout_user(access_token, refresh_token)
        return result
        
    except Exception as e:
        logger.error(f"Logout error: {e}")
        return {"message": "Logout completed with errors"}

@auth_router.get("/me", response_model=User)
async def get_current_user_info(current_user: User = Depends(get_current_user)):
    """
    Get current user information
    """
    return current_user

@auth_router.post("/users", response_model=User)
async def create_user(
    user_data: UserCreate,
    current_user: User = Depends(get_admin_user)
):
    """
    Create a new user (admin only)
    """
    try:
        return get_db_manager().create_user(user_data)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"User creation error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="User creation failed"
        )

@auth_router.get("/users", response_model=list[User])
async def get_all_users(current_user: User = Depends(get_admin_user)):
    """
    Get all users (admin only)
    """
    try:
        return get_db_manager().get_all_users()
    except Exception as e:
        logger.error(f"Get users error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve users"
        )

@auth_router.put("/change-password")
async def change_password(
    password_data: PasswordChange,
    current_user: User = Depends(get_current_user)
):
    """
    Change user password
    """
    try:
        # First authenticate with current password
        authenticated_user = get_db_manager().authenticate_user(
            current_user.username, 
            password_data.current_password
        )
        
        if not authenticated_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Current password is incorrect"
            )
        
        # Update password
        success = get_db_manager().update_password(
            current_user.id, 
            password_data.new_password
        )
        
        if not success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update password"
            )
        
        return {"message": "Password updated successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Password change error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Password change failed"
        )

@auth_router.put("/users/{user_id}", response_model=User)
async def update_user(
    user_id: int,
    user_data: UserUpdate,
    current_user: User = Depends(get_admin_user)
):
    """
    Update user information (admin only)
    """
    try:
        # Prevent admin from deactivating themselves
        if user_id == current_user.id and user_data.is_active is False:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot deactivate your own account"
            )
        
        # Prevent admin from removing their own admin privileges if they are the only admin
        if (user_id == current_user.id and user_data.is_admin is False):
            # Check if there are other active admins
            all_users = get_db_manager().get_all_users()
            active_admins = [u for u in all_users if u.is_admin and u.is_active and u.id != user_id]
            
            if len(active_admins) == 0:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Cannot remove admin privileges - you are the only active admin"
                )
        
        updated_user = get_db_manager().update_user(
            user_id=user_id,
            username=user_data.username,
            email=user_data.email,
            is_admin=user_data.is_admin,
            is_active=user_data.is_active
        )
        
        if not updated_user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )
        
        return updated_user
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"User update error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="User update failed"
        )

@auth_router.delete("/users/{user_id}")
async def delete_user(
    user_id: int,
    hard_delete: bool = False,
    current_user: User = Depends(get_admin_user)
):
    """
    Delete user (admin only)
    By default performs soft delete (deactivates user)
    Use hard_delete=true for permanent deletion
    """
    try:
        # Prevent admin from deleting themselves
        if user_id == current_user.id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot delete your own account"
            )
        
        # Check if user exists and is active
        user_to_delete = get_db_manager().get_user_by_id(user_id)
        if not user_to_delete:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )
        
        # If deleting an admin, ensure there's at least one other active admin
        if user_to_delete.is_admin:
            all_users = get_db_manager().get_all_users()
            active_admins = [u for u in all_users if u.is_admin and u.is_active and u.id != user_id]
            
            if len(active_admins) == 0:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Cannot delete the last active admin"
                )
        
        success = get_db_manager().delete_user(user_id, soft_delete=not hard_delete)
        
        if not success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to delete user"
            )
        
        delete_type = "permanently deleted" if hard_delete else "deactivated"
        return {"message": f"User {delete_type} successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"User deletion error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="User deletion failed"
        )

@auth_router.post("/logout")
async def logout(current_user: User = Depends(get_current_user)):
    """
    Logout (invalidates current session)
    Note: In a production system, you might want to blacklist the token
    """
    return {"message": "Successfully logged out"}

# Health check for auth system
@auth_router.get("/health")
async def auth_health():
    """
    Check authentication system health
    """
    try:
        # Try to connect to database
        get_db_manager().init_database()
        return {"status": "healthy", "message": "Authentication system is operational"}
    except Exception as e:
        logger.error(f"Auth health check failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication system unavailable"
        )