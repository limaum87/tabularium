from datetime import datetime
from pydantic import BaseModel, EmailStr
from typing import Optional


# ---- Auth ----

class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class MeResponse(BaseModel):
    id: int
    name: str
    email: str
    role: str
    is_active: bool

    class Config:
        from_attributes = True


class PasswordChange(BaseModel):
    current_password: str
    new_password: str


# ---- Users ----

class UserCreate(BaseModel):
    name: str
    email: str
    password: str
    role: str = "viewer"


class UserUpdate(BaseModel):
    name: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None


class UserResponse(BaseModel):
    id: int
    name: str
    email: str
    role: str
    is_active: bool
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# ---- Hosts ----

class HostResponse(BaseModel):
    id: int
    hostname: str
    domain: Optional[str] = None
    status: str
    so_type: Optional[str] = "unknown"
    ping_status: Optional[str] = "unknown"
    last_ping: Optional[datetime] = None
    is_legacy: bool = False
    legacy_since: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# ---- Checkin (payload do collector) ----

class HardwarePayload(BaseModel):
    manufacturer: Optional[str] = None
    model: Optional[str] = None
    serial: Optional[str] = None
    cpu: Optional[str] = None
    ram_gb: Optional[float] = None
    bios_version: Optional[str] = None
    last_boot: Optional[str] = None
    last_user: Optional[str] = None


class DiskPayload(BaseModel):
    drive: Optional[str] = None
    total_gb: Optional[float] = None
    free_gb: Optional[float] = None
    filesystem: Optional[str] = None


class NetworkPayload(BaseModel):
    ip: Optional[str] = None
    mac: Optional[str] = None
    gateway: Optional[str] = None
    dns: Optional[str] = None
    adapter_name: Optional[str] = None


class LicensePayload(BaseModel):
    product: str  # "windows" ou "office"
    edition: Optional[str] = None
    version: Optional[str] = None
    channel: Optional[str] = None
    license_status: Optional[str] = None
    partial_product_key: Optional[str] = None
    oem_key_found: Optional[bool] = None
    click_to_run: Optional[bool] = None
    detection_method: Optional[str] = None


class SoftwarePayload(BaseModel):
    name: Optional[str] = None
    version: Optional[str] = None
    publisher: Optional[str] = None
    install_date: Optional[str] = None
    install_location: Optional[str] = None


class CheckinPayload(BaseModel):
    hostname: str
    domain: Optional[str] = None
    status: Optional[str] = None
    so_type: Optional[str] = None
    message: Optional[str] = None
    hardware: Optional[HardwarePayload] = None
    disks: Optional[list[DiskPayload]] = None
    network: Optional[list[NetworkPayload]] = None
    licenses: Optional[list[LicensePayload]] = None
    software: Optional[list[SoftwarePayload]] = None
