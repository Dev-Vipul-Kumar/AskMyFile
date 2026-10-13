from pathlib import Path
import os
import dj_database_url
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

# ── Security ──────────────────────────────────────────────────────────────────
SECRET_KEY = os.environ.get(
    "SECRET_KEY",
    "django-insecure-_4o-0k*$dx57d9625yo5yr-8jh&)f%r*q$3v3#cquvugzu4mp#",
)
DEBUG = os.environ.get("DEBUG", "True") == "True"

# ── Allowed hosts ─────────────────────────────────────────────────────────────
# Localhost for dev; RENDER_EXTERNAL_HOSTNAME is auto-injected by Render.
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "askmyfile-o8zt.onrender.com"]
_render_host = os.environ.get("RENDER_EXTERNAL_HOSTNAME")   # set by Render automatically
if _render_host and _render_host not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append(_render_host)
_extra_hosts = os.environ.get("ALLOWED_HOSTS_EXTRA", "")    # your custom domain if any
if _extra_hosts:
    ALLOWED_HOSTS += _extra_hosts.split()

# ── Apps ──────────────────────────────────────────────────────────────────────
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "accounts",
    "shares",
    "billing",
    "core",
    "admin_panel",
]

# ── Middleware ────────────────────────────────────────────────────────────────
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",   # serve static files in production
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "AskMyFile.middleware.DevCsrfMiddleware",        # handles Origin: null in dev
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "AskMyFile.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "AskMyFile.wsgi.application"

# ── Database ──────────────────────────────────────────────────────────────────
# Render provides DATABASE_URL env var for PostgreSQL automatically.
# Falls back to SQLite for local development.
_db_url = os.environ.get("DATABASE_URL", "")
if _db_url:
    DATABASES = {
        "default": dj_database_url.config(
            default=_db_url,
            conn_max_age=600,
            conn_health_checks=True,
        )
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

# ── Auth ──────────────────────────────────────────────────────────────────────
AUTH_USER_MODEL = "accounts.User"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ── Internationalisation ──────────────────────────────────────────────────────
LANGUAGE_CODE = "en-us"
TIME_ZONE     = os.environ.get("APP_TIME_ZONE", "Asia/Kolkata")
USE_I18N = True
USE_TZ   = True

# ── Static files ──────────────────────────────────────────────────────────────
STATIC_URL    = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT   = BASE_DIR / "staticfiles"
# WhiteNoise: serve compressed static files directly from gunicorn in production
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

# ── Media / private file uploads ──────────────────────────────────────────────
# NOTE: Render's free tier has an ephemeral filesystem — files are wiped on
# each deploy. For persistent storage, add a Render Disk ($0.25/GB/month)
# and set MEDIA_ROOT to the disk mount path (e.g. /var/data/uploads).
MEDIA_URL  = "/media/"
MEDIA_ROOT = os.environ.get("MEDIA_ROOT", str(BASE_DIR / "private_uploads"))

# ── Email ─────────────────────────────────────────────────────────────────────
EMAIL_BACKEND   = os.environ.get("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
EMAIL_HOST      = os.environ.get("EMAIL_HOST",    "smtp.gmail.com")
EMAIL_PORT      = int(os.environ.get("EMAIL_PORT", "465"))
EMAIL_USE_TLS   = os.environ.get("EMAIL_USE_TLS",  "False") == "True"
EMAIL_USE_SSL   = os.environ.get("EMAIL_USE_SSL",  "True")  == "True"
EMAIL_HOST_USER     = os.environ.get("EMAIL_HOST_USER",     "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
DEFAULT_FROM_EMAIL  = os.environ.get("DEFAULT_FROM_EMAIL",  "AskMyFile <noreply@askmyfile.app>")
EMAIL_TIMEOUT   = int(os.environ.get("EMAIL_TIMEOUT", "10"))  # 10s max — prevents worker kill

# ── Cookie / CSRF security ────────────────────────────────────────────────────
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE   = not DEBUG

CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SECURE   = not DEBUG

# ── CSRF trusted origins ─────────────────────────────────────────────────────
# Must include your live Render URL and localhost for dev.
CSRF_TRUSTED_ORIGINS = [
    "http://127.0.0.1:8000",
    "http://localhost:8000",
    "https://askmyfile-o8zt.onrender.com",   # live Render URL
]
if _render_host and f"https://{_render_host}" not in CSRF_TRUSTED_ORIGINS:
    CSRF_TRUSTED_ORIGINS.append(f"https://{_render_host}")
_csrf_extra = os.environ.get("CSRF_TRUSTED_ORIGINS_EXTRA", "")
if _csrf_extra:
    CSRF_TRUSTED_ORIGINS += _csrf_extra.split()

if DEBUG:
    CSRF_COOKIE_SAMESITE    = "Lax"
    SESSION_COOKIE_SAMESITE = "Lax"
    CSRF_USE_SESSIONS       = False

# ── File upload limits ────────────────────────────────────────────────────────
MAX_FILE_SIZE_BYTES      = int(os.environ.get("MAX_FILE_SIZE_BYTES",      str(100 * 1024 * 1024)))
FREE_MAX_FILE_SIZE_BYTES = int(os.environ.get("FREE_MAX_FILE_SIZE_BYTES", str(500 * 1024 * 1024)))
DATA_UPLOAD_MAX_MEMORY_SIZE = FREE_MAX_FILE_SIZE_BYTES + (2 * 1024 * 1024)
FILE_UPLOAD_MAX_MEMORY_SIZE = FREE_MAX_FILE_SIZE_BYTES + (2 * 1024 * 1024)

# ── Movie size limits per plan ────────────────────────────────────────────────
# 0 = not allowed on that plan,  -1 = unlimited
MOVIE_SIZE_FREE    = 0                                                               # not available on Free
MOVIE_SIZE_STARTER = int(os.environ.get("MOVIE_SIZE_STARTER", str(1 * 1024 ** 3))) # 1 GB
MOVIE_SIZE_PLUS    = int(os.environ.get("MOVIE_SIZE_PLUS",    str(2 * 1024 ** 3))) # 2 GB
MOVIE_SIZE_PRO     = -1                                                              # unlimited

# ── Quota / plan defaults ─────────────────────────────────────────────────────
ADMIN_PANEL_URL        = os.environ.get("ADMIN_PANEL_URL",        "admin-panel")
GUEST_SHARES_PER_DAY   = int(os.environ.get("GUEST_SHARES_PER_DAY",   "1"))
FREE_SHARES_PER_DAY    = int(os.environ.get("FREE_SHARES_PER_DAY",    "10"))
STARTER_SHARES_PER_DAY = int(os.environ.get("STARTER_SHARES_PER_DAY", "50"))
PLUS_SHARES_PER_DAY    = int(os.environ.get("PLUS_SHARES_PER_DAY",    "200"))
PRO_SHARES_PER_DAY     = int(os.environ.get("PRO_SHARES_PER_DAY",     "500"))

GUEST_LINK_EXPIRY_HOURS   = 0
GUEST_LINK_EXPIRY_MINUTES = int(os.environ.get("GUEST_LINK_EXPIRY_MINUTES", "1"))
ACCOUNT_LINK_EXPIRY_HOURS = int(os.environ.get("ACCOUNT_LINK_EXPIRY_HOURS", str(7 * 24)))
DEFAULT_MAX_DOWNLOADS     = int(os.environ.get("DEFAULT_MAX_DOWNLOADS",     "5"))

EMAIL_TOKEN_TTL_HOURS  = int(os.environ.get("EMAIL_TOKEN_TTL_HOURS",  "24"))
SMS_OTP_TTL_MINUTES    = int(os.environ.get("SMS_OTP_TTL_MINUTES",    "5"))
SMS_OTP_MAX_ATTEMPTS   = int(os.environ.get("SMS_OTP_MAX_ATTEMPTS",   "5"))

# ── Crypto ────────────────────────────────────────────────────────────────────
AES_ALGORITHM     = "AES-GCM"
AES_GCM_IV_BYTES  = 12
PBKDF2_SALT_BYTES = 16
PBKDF2_ITERATIONS = int(os.environ.get("PBKDF2_ITERATIONS", "600000"))

# ── Razorpay ──────────────────────────────────────────────────────────────────
RAZORPAY_KEY_ID         = os.environ.get("RAZORPAY_KEY_ID",         "")
RAZORPAY_KEY_SECRET     = os.environ.get("RAZORPAY_KEY_SECRET",     "")
RAZORPAY_WEBHOOK_SECRET = os.environ.get("RAZORPAY_WEBHOOK_SECRET", "")

# ── Legacy payment fields (kept for compatibility) ────────────────────────────
PAYMENT_WEBHOOK_SECRET = os.environ.get("PAYMENT_WEBHOOK_SECRET", "")
PAYMENT_PUBLIC_KEY     = os.environ.get("PAYMENT_PUBLIC_KEY",     "")
PAYMENT_SECRET_KEY     = os.environ.get("PAYMENT_SECRET_KEY",     "")

# ── SMS ───────────────────────────────────────────────────────────────────────
SMS_PROVIDER_KEY = os.environ.get("SMS_PROVIDER_KEY", "")
SMS_SENDER_ID    = os.environ.get("SMS_SENDER_ID",    "ASKMYF")

# ── URL redirects ─────────────────────────────────────────────────────────────
LOGIN_URL                 = "/accounts/login/"
LOGIN_REDIRECT_URL        = "/app/"
ADMIN_PANEL_LOGIN_REDIRECT= "/admin-panel/"
LOGOUT_REDIRECT_URL       = "/"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Store sessions in the database so they survive Render restarts/redeploys
SESSION_ENGINE = "django.contrib.sessions.backends.db"

# ── Site URL ──────────────────────────────────────────────────────────────────
SITE_URL = os.environ.get(
    "SITE_URL",
    "http://127.0.0.1:8000" if DEBUG else "https://askmyfile-o8zt.onrender.com"
)

# ── Render reverse-proxy headers (needed for correct URL generation) ──────────
# These must be set regardless of DEBUG so build_absolute_uri() works behind
# Render's load balancer in ALL environments including staging.
USE_X_FORWARDED_HOST  = True   # read Host from X-Forwarded-Host header
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# ── Production hardening (DEBUG=False) ───────────────────────────────────────
if not DEBUG:
    SECURE_SSL_REDIRECT            = True
    SECURE_HSTS_SECONDS            = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD            = True
    SECURE_CONTENT_TYPE_NOSNIFF    = True
    X_FRAME_OPTIONS                = "DENY"
