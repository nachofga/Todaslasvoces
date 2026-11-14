"""
Configuración de Todas las Voces.

Todo lo que cambia entre tu ordenador y el servidor se lee de variables de
entorno (ver .env.example). Sin ninguna variable, la aplicación arranca en modo
desarrollo: SQLite, correos por consola y motor de voz simulado.
"""
import os
from pathlib import Path

import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent


def env(name, default=""):
    return os.environ.get(name, default)


def env_bool(name, default=False):
    return env(name, "1" if default else "0").strip().lower() in ("1", "true", "yes", "on")


# Carga opcional de un fichero .env en desarrollo (sin dependencias externas).
_env_file = BASE_DIR / ".env"
if _env_file.exists():
    for _line in _env_file.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

DEBUG = env_bool("DEBUG", False)
SECRET_KEY = env("SECRET_KEY") or "dev-only-change-me-0123456789abcdefghijklmnopqrstuvwxyz"
if not DEBUG and SECRET_KEY.startswith("dev-only") and env("TLV_ALLOW_DEV_SECRET") != "1":
    # En producción la clave debe venir del entorno (Render la genera sola).
    if env("RENDER") or env("TLV_PRODUCTION") == "1":
        raise RuntimeError("Falta la variable SECRET_KEY en producción.")

ALLOWED_HOSTS = [h.strip() for h in env("ALLOWED_HOSTS", "localhost,127.0.0.1,testserver").split(",") if h.strip()]
if env("RENDER_EXTERNAL_HOSTNAME"):
    ALLOWED_HOSTS.append(env("RENDER_EXTERNAL_HOSTNAME"))
if env("RAILWAY_PUBLIC_DOMAIN"):
    ALLOWED_HOSTS.append(env("RAILWAY_PUBLIC_DOMAIN"))
CSRF_TRUSTED_ORIGINS = [f"https://{h}" for h in ALLOWED_HOSTS if h not in ("localhost", "127.0.0.1", "testserver")]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "whitenoise.runserver_nostatic",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "tlv",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
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
                "django.template.context_processors.i18n",
                "tlv.context.site",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# --- Base de datos -----------------------------------------------------------
# En producción: DATABASE_URL=postgres://usuario:clave@host:5432/nombre
(BASE_DIR / "var").mkdir(exist_ok=True)
DATABASES = {
    "default": dj_database_url.config(
        default=f"sqlite:///{BASE_DIR / 'var' / 'db.sqlite3'}",
        conn_max_age=600,
        conn_health_checks=True,
    )
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": "tlv_cache",
    }
}

# --- Usuarios ----------------------------------------------------------------
AUTH_USER_MODEL = "tlv.User"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "home"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --- Idiomas -----------------------------------------------------------------
LANGUAGE_CODE = "es"
LANGUAGES = [("es", "Español"), ("en", "English")]
LOCALE_PATHS = [BASE_DIR / "tlv" / "locale"]
USE_I18N = True
TIME_ZONE = "Europe/Madrid"
USE_TZ = True
# Formato numérico fijo: punto decimal y coma de miles (1,234.50 €).
USE_THOUSAND_SEPARATOR = False
FORMAT_MODULE_PATH = ["config.formats"]

# --- Ficheros ----------------------------------------------------------------
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
# Las grabaciones y audios NO se sirven por URL pública: siempre pasan por una
# vista que comprueba permisos (tlv/views/media.py).
MEDIA_ROOT = Path(env("TLV_MEDIA_ROOT") or (BASE_DIR / "var" / "media"))
MEDIA_URL = "/_media-privado/"

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
WHITENOISE_MANIFEST_STRICT = False
if env("TLV_S3_BUCKET"):
    # Almacenamiento compatible con S3 (Cloudflare R2, Supabase Storage, AWS...).
    # Requiere: pip install "django-storages[s3]"
    STORAGES["default"] = {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "bucket_name": env("TLV_S3_BUCKET"),
            "endpoint_url": env("TLV_S3_ENDPOINT") or None,
            "region_name": env("TLV_S3_REGION") or None,
            "access_key": env("TLV_S3_ACCESS_KEY"),
            "secret_key": env("TLV_S3_SECRET_KEY"),
            "default_acl": "private",
            "querystring_auth": True,
            "file_overwrite": False,
        },
    }

FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 80 * 1024 * 1024
TLV_MAX_UPLOAD_MB = int(env("TLV_MAX_UPLOAD_MB", "40"))

# --- Correo ------------------------------------------------------------------
# Cualquier proveedor SMTP sirve (Brevo, Resend, Mailgun, Gmail...).
if env("EMAIL_HOST"):
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_HOST = env("EMAIL_HOST")
    EMAIL_PORT = int(env("EMAIL_PORT", "587"))
    EMAIL_HOST_USER = env("EMAIL_HOST_USER")
    EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD")
    EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
    EMAIL_TIMEOUT = 15
else:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", "Todas las Voces <no-responder@todaslasvoces.local>")
SERVER_EMAIL = DEFAULT_FROM_EMAIL

# --- Todas las Voces ---------------------------------------------------------
# Dirección pública de la web (para enlaces en correos, certificados y QR).
_default_site = "http://localhost:8000"
if env("RENDER_EXTERNAL_URL"):
    _default_site = env("RENDER_EXTERNAL_URL")
TLV_SITE_URL = (env("TLV_SITE_URL") or _default_site).rstrip("/")

# Motor de voz: "simulated" (sin coste, para probar el flujo) o "elevenlabs".
TLV_VOICE_ENGINE = env("TLV_VOICE_ENGINE") or ("elevenlabs" if env("ELEVENLABS_API_KEY") else "simulated")
ELEVENLABS_API_KEY = env("ELEVENLABS_API_KEY")
# Residencia de datos en la UE (solo cuentas Enterprise): https://api.eu.residency.elevenlabs.io
ELEVENLABS_BASE_URL = (env("ELEVENLABS_BASE_URL") or "https://api.elevenlabs.io").rstrip("/")
ELEVENLABS_MODEL_ID = env("ELEVENLABS_MODEL_ID") or "eleven_multilingual_v2"
ELEVENLABS_OUTPUT_FORMAT = env("ELEVENLABS_OUTPUT_FORMAT") or "mp3_44100_128"
ELEVENLABS_STT_MODEL = env("ELEVENLABS_STT_MODEL") or "scribe_v2"
ELEVENLABS_TIMEOUT = int(env("ELEVENLABS_TIMEOUT", "110"))

# Clave de la marca de agua invisible. Quien la tenga puede leer (y, en teoría,
# intentar borrar) las marcas: trátala como una contraseña y no la cambies
# nunca una vez en producción, o dejarás de poder leer los audios antiguos.
TLV_WATERMARK_KEY = env("TLV_WATERMARK_KEY") or ("wm-" + SECRET_KEY)
TLV_WATERMARK_STRENGTH = float(env("TLV_WATERMARK_STRENGTH", "0.10"))
# Semilla de la clave de firma del registro de trazabilidad (Ed25519).
TLV_SIGNING_SEED = env("TLV_SIGNING_SEED") or ("sign-" + SECRET_KEY)

TLV_CONTRACT_VERSION = "2026.1"
FFMPEG_BIN = env("FFMPEG_BIN")

# --- Seguridad ---------------------------------------------------------------
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = env_bool("TLV_SECURE_COOKIES", True)
    CSRF_COOKIE_SECURE = SESSION_COOKIE_SECURE
    SECURE_SSL_REDIRECT = env_bool("TLV_SSL_REDIRECT", SESSION_COOKIE_SECURE)
    SECURE_HSTS_SECONDS = 3600 if SESSION_COOKIE_SECURE else 0
    SECURE_REFERRER_POLICY = "same-origin"
    SECURE_REDIRECT_EXEMPT = [r"^salud/$"]
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_AGE = 60 * 60 * 24 * 14
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {"tlv": {"handlers": ["console"], "level": "INFO", "propagate": False}},
}
