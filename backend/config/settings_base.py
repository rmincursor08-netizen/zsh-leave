import json
import os
import shutil
from ipaddress import ip_network
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BASE_DIR.parent


def env(name: str, default: str | None = None, required: bool = False) -> str:
    value = os.environ.get(name, default)
    if required and (value is None or value == ""):
        raise RuntimeError(f"Required environment variable {name} is not set.")
    return value or ""


SECRET_KEY = env("ZSH_SECRET_KEY", "dev-only-secret-key-change-me")
DEBUG = env("ZSH_DEBUG", "0").lower() in {"1", "true", "yes", "on"}
ALLOWED_HOSTS = [host.strip() for host in env("ZSH_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if host.strip()]
ZSH_TRUSTED_PROXY_NETWORKS = tuple(
    ip_network(value.strip(), strict=False)
    for value in env("ZSH_TRUSTED_PROXY_IPS", "").split(",")
    if value.strip()
)

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "drf_spectacular",
    "leave",
]

MIDDLEWARE = [
    "leave.middleware.TrustedProxySSLHeaderMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "leave.middleware.AuditContextMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    }
]

WSGI_APPLICATION = "config.wsgi.application"

if env("ZSH_DB_ENGINE", "postgres").lower() == "sqlite":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": env("ZSH_DB_NAME", "zsh"),
            "USER": env("ZSH_DB_USER", "postgres"),
            "PASSWORD": env("ZSH_DB_PASSWORD", "postgres"),
            "HOST": env("ZSH_DB_HOST", "localhost"),
            "PORT": env("ZSH_DB_PORT", "5432"),
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

EMAIL_BACKEND = env(
    "ZSH_EMAIL_BACKEND",
    "django.core.mail.backends.smtp.EmailBackend",
)
EMAIL_HOST = env("ZSH_EMAIL_HOST", "")
EMAIL_PORT = int(env("ZSH_EMAIL_PORT", "587"))
EMAIL_HOST_USER = env("ZSH_EMAIL_USER", "")
EMAIL_HOST_PASSWORD = env("ZSH_EMAIL_PASSWORD", "")
EMAIL_USE_TLS = env("ZSH_EMAIL_USE_TLS", "1").lower() in {"1", "true", "yes", "on"}
EMAIL_USE_SSL = env("ZSH_EMAIL_USE_SSL", "0").lower() in {"1", "true", "yes", "on"}
if EMAIL_USE_TLS and EMAIL_USE_SSL:
    raise RuntimeError("Enable either ZSH_EMAIL_USE_TLS or ZSH_EMAIL_USE_SSL, not both.")
DEFAULT_FROM_EMAIL = env("ZSH_DEFAULT_FROM_EMAIL", "")
ZSH_EMAIL_ENABLED = env("ZSH_EMAIL_ENABLED", "0").lower() in {"1", "true", "yes", "on"}
ZSH_INVITATION_TOKEN_TTL_HOURS = int(env("ZSH_INVITATION_TOKEN_TTL_HOURS", "72"))
ZSH_INVITATION_EXPOSE_DEV_LINKS = (
    env("ZSH_INVITATION_EXPOSE_DEV_LINKS", "0").lower() in {"1", "true", "yes", "on"}
)
ZSH_PUBLIC_BASE_URL = env("ZSH_PUBLIC_BASE_URL", "")
ZSH_JOB_SCHEDULER_ENABLED = env("ZSH_JOB_SCHEDULER_ENABLED", "0").lower() in {
    "1", "true", "yes", "on",
}
ZSH_NOTIFICATION_BATCH_LIMIT = int(env("ZSH_NOTIFICATION_BATCH_LIMIT", "50"))
if not 1 <= ZSH_NOTIFICATION_BATCH_LIMIT <= 500:
    raise RuntimeError("ZSH_NOTIFICATION_BATCH_LIMIT must be between 1 and 500.")
ZSH_JOB_SCHEDULER_REQUIRED = False
if ZSH_INVITATION_TOKEN_TTL_HOURS < 1:
    raise RuntimeError("ZSH_INVITATION_TOKEN_TTL_HOURS must be positive.")

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
]

LANGUAGE_CODE = "fa-ir"
TIME_ZONE = "Asia/Tehran"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"
ZSH_ATTACHMENT_ROOT = Path(
    env("ZSH_ATTACHMENT_ROOT", str(Path.home() / ".local/share/zsh-leave/attachments")),
).expanduser().resolve()
ZSH_ATTACHMENT_SCANNER = env("ZSH_ATTACHMENT_SCANNER", "").strip()
try:
    ZSH_ATTACHMENT_SCANNER_COMMAND = json.loads(env("ZSH_ATTACHMENT_SCANNER_COMMAND", "[]"))
except json.JSONDecodeError as exc:
    raise RuntimeError("ZSH_ATTACHMENT_SCANNER_COMMAND must be a JSON argument array.") from exc
if (
    not isinstance(ZSH_ATTACHMENT_SCANNER_COMMAND, list)
    or any(not isinstance(argument, str) or not argument or "\0" in argument
           for argument in ZSH_ATTACHMENT_SCANNER_COMMAND)
):
    raise RuntimeError("ZSH_ATTACHMENT_SCANNER_COMMAND must be a non-empty-string argument array.")
ZSH_ATTACHMENT_SCANNER_TIMEOUT_SECONDS = int(
    env("ZSH_ATTACHMENT_SCANNER_TIMEOUT_SECONDS", "30"),
)
if not 1 <= ZSH_ATTACHMENT_SCANNER_TIMEOUT_SECONDS <= 120:
    raise RuntimeError("ZSH_ATTACHMENT_SCANNER_TIMEOUT_SECONDS must be between 1 and 120.")
ZSH_ATTACHMENT_PRLIMIT = shutil.which("prlimit") or ""
ZSH_ATTACHMENT_SCANNER_REQUIRED = False
ZSH_ATTACHMENT_RETENTION_MONTHS = 2

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_THROTTLE_CLASSES": [
        "leave.throttling.AnonymousClientThrottle",
        "leave.throttling.AuthenticatedUserThrottle",
        "leave.throttling.AuthenticatedClientThrottle",
        "leave.throttling.ScopedClientThrottle",
        "leave.throttling.ScopedUserThrottle",
        "leave.throttling.LoginAccountThrottle",
    ],
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "leave.authentication_classes.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "user": "120/min",
        "anon": "20/min",
        "authenticated_client": "120/min",
        "auth_login": "5/min",
        "auth_login_account": "5/min",
        "activation": "5/min",
        "employee_invitation": "10/hour",
        "password_operation": "5/hour",
        "password_reset": "5/hour",
        "employee_search": "60/min",
        "bulk_import": "5/hour",
        "expensive_operation": "10/min",
        "file_upload": "10/hour",
        "file_access": "30/min",
    },
    "EXCEPTION_HANDLER": "leave.throttling.rate_limit_exception_handler",
    "NUM_PROXIES": 0,
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "ZSH Leave API",
    "DESCRIPTION": "Backend foundation and session authentication API for the ZSH Leave Management system.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {
            "format": "%(asctime)s %(levelname)s %(module)s %(message)s",
        }
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json",
        }
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": "INFO",
        },
        "leave": {
            "handlers": ["console"],
            "level": "INFO",
        },
    },
}

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = 8 * 60 * 60
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
CSRF_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in env("ZSH_CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin.strip()
]
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

AUTH_USER_MODEL = "auth.User"
