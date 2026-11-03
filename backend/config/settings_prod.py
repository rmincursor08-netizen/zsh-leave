import os
from urllib.parse import urlsplit
from pathlib import Path

from .settings_base import *  # noqa F401,F403

DEBUG = False
ZSH_JOB_SCHEDULER_REQUIRED = True
ZSH_ATTACHMENT_SCANNER_REQUIRED = True
SECRET_KEY = env("ZSH_SECRET_KEY", required=True)
if env("ZSH_INVITATION_EXPOSE_DEV_LINKS", "0").lower() in {"1", "true", "yes", "on"}:
    raise RuntimeError("Development invitation links cannot be exposed in production.")
if (
    env("ZSH_EMAIL_ENABLED", "0").lower() in {"1", "true", "yes", "on"}
    and not env("ZSH_PUBLIC_BASE_URL", "").startswith("https://")
):
    raise RuntimeError("ZSH_PUBLIC_BASE_URL must use HTTPS when invitation email is enabled.")
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_SSL_REDIRECT = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
ALLOWED_HOSTS = [host.strip() for host in env("ZSH_ALLOWED_HOSTS", "").split(",") if host.strip()]

if not ALLOWED_HOSTS:
    raise RuntimeError("ZSH_ALLOWED_HOSTS must be set in production.")

if "*" in ALLOWED_HOSTS:
    raise RuntimeError("Wildcard hosts are not allowed in production.")

for origin in CSRF_TRUSTED_ORIGINS:
    parsed_origin = urlsplit(origin)
    if (
        parsed_origin.scheme != "https"
        or not parsed_origin.hostname
        or "*" in parsed_origin.hostname
        or parsed_origin.username is not None
        or parsed_origin.password is not None
        or parsed_origin.path
        or parsed_origin.query
        or parsed_origin.fragment
    ):
        raise RuntimeError(
            "Production CSRF trusted origins must be exact HTTPS origins without paths."
        )
    try:
        parsed_origin.port
    except ValueError as exc:
        raise RuntimeError("Production CSRF trusted origin has an invalid port.") from exc

if len(SECRET_KEY) < 50:
    raise RuntimeError("ZSH_SECRET_KEY must contain at least 50 characters in production.")

if not ZSH_ATTACHMENT_SCANNER_COMMAND:
    raise RuntimeError("ZSH_ATTACHMENT_SCANNER_COMMAND must configure production malware scanning.")
if not Path(ZSH_ATTACHMENT_SCANNER_COMMAND[0]).is_absolute():
    raise RuntimeError("Production attachment scanner executable must use an absolute path.")
if (
    not Path(ZSH_ATTACHMENT_SCANNER_COMMAND[0]).is_file()
    or not os.access(ZSH_ATTACHMENT_SCANNER_COMMAND[0], os.X_OK)
):
    raise RuntimeError("Configured production attachment scanner executable is unavailable.")
if not ZSH_ATTACHMENT_PRLIMIT:
    raise RuntimeError("Production attachment scanning requires the prlimit utility.")

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("ZSH_DB_NAME", "zsh"),
        "USER": env("ZSH_DB_USER", required=True),
        "PASSWORD": env("ZSH_DB_PASSWORD", required=True),
        "HOST": env("ZSH_DB_HOST", "db"),
        "PORT": env("ZSH_DB_PORT", "5432"),
    }
}

attachment_root = Path(env("ZSH_ATTACHMENT_ROOT", required=True)).expanduser()
if not attachment_root.is_absolute():
    raise RuntimeError("ZSH_ATTACHMENT_ROOT must be an absolute private storage path.")
if attachment_root.is_symlink():
    raise RuntimeError("ZSH_ATTACHMENT_ROOT cannot be a symbolic link.")
ZSH_ATTACHMENT_ROOT = attachment_root.resolve()
try:
    ZSH_ATTACHMENT_ROOT.relative_to(PROJECT_ROOT)
except ValueError:
    pass
else:
    raise RuntimeError("ZSH_ATTACHMENT_ROOT must be outside the application repository.")

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": "zsh_cache",
    }
}
