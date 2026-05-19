"""
SocialProof — Centralised Configuration
Loads from .env file when present; falls back to safe defaults.
All values are overridable via environment variables — never commit secrets.
"""
import logging
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ── Database ──────────────────────────────────────────────────────────────────
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "mysql+pymysql://user:password@localhost/socialproof_db",
)

# ── Auth / JWT ────────────────────────────────────────────────────────────────
_SECRET_KEY_DEFAULT = "change-this-in-production-please"
SECRET_KEY          = os.getenv("SECRET_KEY", _SECRET_KEY_DEFAULT)
if SECRET_KEY == _SECRET_KEY_DEFAULT:
    import logging as _logging
    _logging.getLogger("socialproof").critical(
        "SECRET_KEY is using the insecure default value! "
        "Set SECRET_KEY in your .env file before deploying to production."
    )
JWT_ALGORITHM   = "HS256"
JWT_EXPIRE_DAYS = int(os.getenv("JWT_EXPIRE_DAYS", "7"))

# ── ML Models ─────────────────────────────────────────────────────────────────
NLI_MODEL   = os.getenv("NLI_MODEL",   "MoritzLaurer/mDeBERTa-v3-base-mnli-xnli")
EMBED_MODEL = os.getenv("EMBED_MODEL", "all-MiniLM-L6-v2")

# ── Pipeline limits ───────────────────────────────────────────────────────────
MAX_EVIDENCE               = int(os.getenv("MAX_EVIDENCE",                "5"))
QUIZ_QUESTIONS_PER_SESSION = int(os.getenv("QUIZ_QUESTIONS_PER_SESSION", "10"))

# ── Per-step timeouts (seconds) ───────────────────────────────────────────────
TIMEOUT_LIVE_SEARCH = float(os.getenv("TIMEOUT_LIVE_SEARCH", "20.0"))  # fallback: partial results

# ── CORS origins ──────────────────────────────────────────────────────────────
_cors_raw    = os.getenv(
    "CORS_ORIGINS",
    "http://localhost,http://127.0.0.1,http://localhost:8000,http://127.0.0.1:8000,http://localhost:5500,http://127.0.0.1:5500,http://localhost:8080",
)
CORS_ORIGINS = [o.strip() for o in _cors_raw.split(",") if o.strip()]

# ── AI Suggest (Mindmap) ──────────────────────────────────────────────────────
# Primary:  Google Gemini 2.5 Flash — free tier: 500 req/day, 10 RPM
#   Get key: https://aistudio.google.com/app/apikey
# Fallback: Groq llama-3.3-70b-versatile — free tier: 1,000 req/day, 30 RPM
#   Get key: https://console.groq.com/keys
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GROQ_API_KEY   = os.getenv("GROQ_API_KEY",   "")

# ── Email / SMTP ──────────────────────────────────────────────────────────────
# Required for forgot-password and email-verification flows.
# Example (Gmail): SMTP_HOST=smtp.gmail.com  SMTP_PORT=587
#                  SMTP_USER=you@gmail.com  SMTP_PASS=your_app_password
SMTP_HOST    = os.getenv("SMTP_HOST", "")
SMTP_PORT    = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER    = os.getenv("SMTP_USER", "")
SMTP_PASS    = os.getenv("SMTP_PASS", "")
SMTP_FROM    = os.getenv("SMTP_FROM", SMTP_USER)
APP_BASE_URL = os.getenv("APP_BASE_URL", "http://localhost:8000")

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("socialproof")
