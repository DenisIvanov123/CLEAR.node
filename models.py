from pydantic import BaseModel
from typing import List, Optional

class EncryptedEntity(BaseModel): 
    id: str
    folder_id: str = ""
    encrypted_data: str 
    deleted: bool = False 
    base_revision: int = 0 

class SyncRequest(BaseModel): 
    entities: List[EncryptedEntity]

class UserGroupUpdate(BaseModel): 
    group_id: str

class GroupCreate(BaseModel):
    id: str
    name: str
    is_superadmin: bool = False
    can_manage_users: bool = False
    can_save_local: bool = False
    can_add: bool = False
    can_edit: bool = False
    can_delete: bool = False
    can_bulk_edit: bool = False
    is_hidden: bool = False
    is_deleted: bool = False
    can_read_log: bool = False
    can_manage_roles: bool = False
    can_manage_settings: bool = False

class PermissionSetReq(BaseModel):
    entity_id: str
    group_id: str
    access_level: str 

class InviteReq(BaseModel): 
    user_id: str
    group_id: str

class GroupUsersReq(BaseModel): 
    user_ids: List[str]

class LocalTokenReq(BaseModel):
    description: str
    days_valid: Optional[int] = None

class ServerSettingsReq(BaseModel):
    audit_retention_days: int
    deleted_retention_days: int
    default_group_id: str

class UserCreateReq(BaseModel):
    username: str
    email: Optional[str] = ""
    password: str
    group_id: Optional[str] = ""

class UserResetPasswordReq(BaseModel):
    new_password: str

class LoginReq(BaseModel):
    username: str
    password: str

class ChangePasswordReq(BaseModel):
    old_password: str
    new_password: str



class ClientLogEntry(BaseModel):
    level: str = "ERROR"
    message: str
    stack_trace: Optional[str] = ""
    app_version: Optional[str] = ""
    timestamp: Optional[str] = None

class ClientLogsBatchReq(BaseModel):
    logs: List[ClientLogEntry]
