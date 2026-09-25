from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Boolean, Enum, Float, Text
from sqlalchemy.orm import declarative_base, sessionmaker

from app.core.config import settings

engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True, pool_recycle=3600)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """Dependency — injeta sessão do banco nos endpoints."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ============================================================
# Modelos
# ============================================================


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(150), nullable=False)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password = Column(String(255), nullable=False)
    role = Column(Enum("admin", "operator", "viewer", name="user_role"), default="viewer", nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Host(Base):
    __tablename__ = "hosts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    hostname = Column(String(255), unique=True, nullable=False, index=True)
    domain = Column(String(255), nullable=True)
    status = Column(Enum("online", "offline", "unknown", name="host_status"), default="unknown")
    so_type = Column(Enum("windows", "linux", "unknown", name="so_type"), default="unknown")
    ping_status = Column(String(20), default="unknown")
    last_ping = Column(DateTime, nullable=True)
    is_legacy = Column(Boolean, default=False, nullable=False, index=True)
    legacy_since = Column(DateTime, nullable=True)
    last_seen = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class HostHardware(Base):
    __tablename__ = "host_hardware"

    id = Column(Integer, primary_key=True, autoincrement=True)
    host_id = Column(Integer, nullable=False, index=True)
    manufacturer = Column(String(255), nullable=True)
    model = Column(String(255), nullable=True)
    serial = Column(String(255), nullable=True)
    cpu = Column(String(255), nullable=True)
    ram_gb = Column(Float, nullable=True)
    bios_version = Column(String(255), nullable=True)
    last_boot = Column(DateTime, nullable=True)
    last_user = Column(String(255), nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class HostNetwork(Base):
    __tablename__ = "host_network"

    id = Column(Integer, primary_key=True, autoincrement=True)
    host_id = Column(Integer, nullable=False, index=True)
    ip = Column(String(45), nullable=True)
    mac = Column(String(17), nullable=True)
    gateway = Column(String(45), nullable=True)
    dns = Column(String(255), nullable=True)
    adapter_name = Column(String(255), nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class HostDisk(Base):
    __tablename__ = "host_disks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    host_id = Column(Integer, nullable=False, index=True)
    drive = Column(String(50), nullable=True)
    total_gb = Column(Float, nullable=True)
    free_gb = Column(Float, nullable=True)
    filesystem = Column(String(50), nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class HostLicense(Base):
    __tablename__ = "host_licenses"

    id = Column(Integer, primary_key=True, autoincrement=True)
    host_id = Column(Integer, nullable=False, index=True)
    product = Column(Enum("windows", "office", name="license_product"), nullable=False)
    edition = Column(String(255), nullable=True)
    version = Column(String(50), nullable=True)
    channel = Column(String(100), nullable=True)
    license_status = Column(String(100), nullable=True)
    partial_product_key = Column(String(10), nullable=True)
    oem_key_found = Column(Boolean, nullable=True)
    click_to_run = Column(Boolean, nullable=True)
    detection_method = Column(String(100), nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class HostRemoteAccess(Base):
    __tablename__ = "host_remote_access"

    id = Column(Integer, primary_key=True, autoincrement=True)
    host_id = Column(Integer, nullable=False, index=True)
    anydesk_id = Column(String(50), nullable=True)
    anydesk_alias = Column(String(255), nullable=True)
    anydesk_version = Column(String(50), nullable=True)
    ultravnc_installed = Column(Boolean, nullable=True)
    ultravnc_port = Column(Integer, nullable=True)
    ultravnc_version = Column(String(50), nullable=True)
    teamviewer_id = Column(String(50), nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class HostSoftware(Base):
    __tablename__ = "host_software"

    id = Column(Integer, primary_key=True, autoincrement=True)
    host_id = Column(Integer, nullable=False, index=True)
    name = Column(String(500), nullable=True)
    version = Column(String(100), nullable=True)
    publisher = Column(String(255), nullable=True)
    install_date = Column(String(20), nullable=True)
    install_location = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ScanHistory(Base):
    __tablename__ = "scan_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    host_id = Column(Integer, nullable=False, index=True)
    hostname = Column(String(255), nullable=True)
    status = Column(Enum("success", "offline", "error", name="scan_status"), nullable=False)
    message = Column(Text, nullable=True)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, default=datetime.utcnow)


class PurchasedLicense(Base):
    __tablename__ = "purchased_licenses"

    id = Column(Integer, primary_key=True, autoincrement=True)
    product = Column(String(255), nullable=False)
    edition = Column(String(255), nullable=True)
    key = Column(String(255), nullable=True)
    total_seats = Column(Integer, default=0)
    used_seats = Column(Integer, default=0)
    expires_at = Column(DateTime, nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ActivityLog(Base):
    __tablename__ = "activity_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    activity_type = Column(Enum(
        "collector_checkin",
        "manual_collect",
        "discovery_run",
        "discovery_import",
        "ping_sweep",
        "ping_single",
        "winrm_test",
        "winrm_enable",
        "host_created",
        name="activity_type",
    ), nullable=False, index=True)
    hostname = Column(String(255), nullable=True, index=True)
    host_id = Column(Integer, nullable=True)
    status = Column(String(50), nullable=True)
    message = Column(Text, nullable=True)
    details = Column(Text, nullable=True)  # JSON com dados extras
    source = Column(String(100), nullable=True)  # "collector", "manual", "system"
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class Setting(Base):
    __tablename__ = "settings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String(100), unique=True, nullable=False, index=True)
    value = Column(Text, nullable=True)
    category = Column(String(50), nullable=False, default="general")
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class HostPatch(Base):
    """KB instalado no host (Get-HotFix)."""
    __tablename__ = "host_patches"

    id = Column(Integer, primary_key=True, autoincrement=True)
    host_id = Column(Integer, nullable=False, index=True)
    kb = Column(String(20), nullable=True, index=True)
    description = Column(String(100), nullable=True)  # Security Update, Hotfix, etc
    installed_on = Column(String(20), nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class HostPendingUpdate(Base):
    """Update pendente detectado via Microsoft.Update.Session (COM)."""
    __tablename__ = "host_pending_updates"

    id = Column(Integer, primary_key=True, autoincrement=True)
    host_id = Column(Integer, nullable=False, index=True)
    kb = Column(String(20), nullable=True, index=True)
    title = Column(String(500), nullable=True)
    severity = Column(String(20), nullable=True)  # Critical, Important, Moderate, Low, None
    reboot_required = Column(Boolean, default=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class HostPatchStatus(Base):
    """Status de atualização do host (1 linha por host)."""
    __tablename__ = "host_patch_status"

    id = Column(Integer, primary_key=True, autoincrement=True)
    host_id = Column(Integer, nullable=False, index=True)
    os_edition = Column(String(255), nullable=True)
    display_version = Column(String(20), nullable=True)  # 22H2, 23H2...
    build = Column(String(30), nullable=True)  # 19045.4046
    wu_last_success = Column(String(50), nullable=True)  # último WU OK (registry)
    pending_count = Column(Integer, default=0)
    critical_pending = Column(Integer, default=0)
    last_error = Column(Text, nullable=True)
    last_scan = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class HostDistro(Base):
    __tablename__ = "host_distro"

    id = Column(Integer, primary_key=True, autoincrement=True)
    host_id = Column(Integer, nullable=False, index=True)
    name = Column(String(255), nullable=True)
    version = Column(String(100), nullable=True)
    distro_id = Column(String(100), nullable=True)
    id_like = Column(String(255), nullable=True)
    pretty_name = Column(String(255), nullable=True)
    kernel = Column(String(100), nullable=True)
    arch = Column(String(50), nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
