"""
SocialProof — Router: Authentication
Endpoints:
  POST /auth/register      — create account
  POST /auth/login         — [DEPRECATED] returns JWT in body; use /cookie-login
  POST /auth/cookie-login  — returns JWT via HttpOnly cookie (preferred)
  POST /auth/cookie-logout — clears the HttpOnly cookie
  GET  /auth/me            — decode token → user info
  POST /auth/logout        — invalidates token server-side (blocklist)
  GET  /auth/session       — generate anonymous session token

JWT is accepted via:
  1. Authorization: Bearer <token>  (API / existing integrations)
  2. HttpOnly cookie 'sp_jwt'       (cookie-based login, preferred)
"""
import secrets
import bcrypt
import hashlib
import hmac
import base64
import json
import time
import os
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from email.mime.text import MIMEText
import smtplib

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Header, Request, Response, Depends, Query

from sqlalchemy.orm import Session

from config import SECRET_KEY, JWT_ALGORITHM, JWT_EXPIRE_DAYS, logger, \
    SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, SMTP_FROM, APP_BASE_URL
from database.models import engine, UserORM, PasswordResetTokenORM, EmailVerificationTokenORM, ResearchConsentLogORM
from schemas import RegisterRequest, LoginRequest, AuthResponse, ForgotPasswordRequest, ResetPasswordRequest, VerifyEmailRequest, ConsentWithdrawRequest, ConsentStatusResponse

router = APIRouter(prefix="/auth")

# ── C-7: Enforce non-default SECRET_KEY at startup ───────────────────────────
_SECRET_KEY_DEFAULT = "change-this-in-production-please"
if SECRET_KEY == _SECRET_KEY_DEFAULT:
    raise RuntimeError(
        "FATAL: SECRET_KEY is using the insecure default value. "
        "Set a strong SECRET_KEY in your .env file before running the server."
    )

# ── C-6 / C-9: Redis client (optional) ───────────────────────────────────────
# Set REDIS_URL in your .env (e.g. redis://localhost:6379/0) to enable
# persistent rate limiting and token blocklist across all workers/restarts.
# Without Redis, both features fall back to in-process memory — safe for a
# single-worker dev server, but NOT suitable for multi-worker production.
_redis = None
_REDIS_URL = os.getenv("REDIS_URL", "")
if _REDIS_URL:
    try:
        import redis as _redis_lib
        _redis = _redis_lib.from_url(_REDIS_URL, decode_responses=True, socket_connect_timeout=2)
        _redis.ping()
        logger.info("Auth: Redis connected — rate limiter and token blocklist are persistent.")
    except Exception as _re:
        logger.warning(
            f"Auth: Redis connection failed ({_re}). "
            "Falling back to in-process rate limiter and blocklist. "
            "This is NOT safe for multi-worker deployments."
        )
        _redis = None
else:
    logger.warning(
        "Auth: REDIS_URL is not set. Rate limiter and token blocklist use in-process "
        "memory — they reset on every restart and are bypassed by multiple workers. "
        "Set REDIS_URL in .env for production deployments."
    )

# ── C-10: Multi-worker guard ──────────────────────────────────────────────────
# WEB_CONCURRENCY is respected by uvicorn, gunicorn, and most PaaS platforms.
# If more than one worker is configured but Redis is unavailable, abort startup
# immediately rather than silently running with broken per-worker auth state.
# To run single-worker dev: set WEB_CONCURRENCY=1 (or leave unset, default=1).
# To run multi-worker prod: set WEB_CONCURRENCY=4 (or N) AND set REDIS_URL.
_worker_count = int(os.getenv("WEB_CONCURRENCY", os.getenv("UVICORN_WORKERS", "1")))
if _worker_count > 1 and not _redis:
    raise RuntimeError(
        f"FATAL: {_worker_count} workers detected (WEB_CONCURRENCY={_worker_count}) "
        "but Redis is not available. "
        "In-process rate limiting and token blocklist are per-worker and will NOT "
        "share state across processes — users can bypass rate limits and revoked "
        "tokens remain valid on other workers. "
        "Fix: set REDIS_URL in your .env and ensure Redis is running, "
        "or set WEB_CONCURRENCY=1 for single-worker deployments."
    )

# ── Email config (SMTP) ───────────────────────────────────────────────────────
# Values imported from config — set SMTP_HOST/SMTP_USER/SMTP_PASS/APP_BASE_URL in .env

_RESET_TOKEN_EXPIRE_MINUTES  = 60    # 1 hour
_VERIFY_TOKEN_EXPIRE_MINUTES = 1440  # 24 hours


def _send_email(to: str, subject: str, body: str) -> bool:
    """Send a plain-text email. Returns True on success, False on failure."""
    if not SMTP_HOST or not SMTP_USER:
        logger.warning(f"[Email] SMTP not configured — skipping send to {to}. Set SMTP_HOST/SMTP_USER/SMTP_PASS in .env")
        return False
    try:
        msg = MIMEText(body, "plain")
        msg["Subject"] = subject
        msg["From"]    = SMTP_FROM
        msg["To"]      = to
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(SMTP_USER, SMTP_PASS)
            smtp.sendmail(SMTP_FROM, [to], msg.as_string())
        return True
    except Exception as e:
        logger.error(f"[Email] Failed to send to {to}: {e}")
        return False


# ── C-6: Rate limiter ─────────────────────────────────────────────────────────
_RATE_WINDOW_SECONDS = 60
_RATE_MAX_FAILURES   = 10
_fail_counts: dict   = defaultdict(list)   # fallback: ip → [timestamp, ...]

def _check_rate_limit(ip: str):
    if _redis:
        key   = f"sp:ratelimit:{ip}"
        count = _redis.get(key)
        if count and int(count) >= _RATE_MAX_FAILURES:
            raise HTTPException(
                status_code=429,
                detail=f"Too many failed login attempts. Please wait {_RATE_WINDOW_SECONDS} seconds.",
            )
    else:
        now    = time.time()
        cutoff = now - _RATE_WINDOW_SECONDS
        _fail_counts[ip] = [t for t in _fail_counts[ip] if t > cutoff]
        if len(_fail_counts[ip]) >= _RATE_MAX_FAILURES:
            raise HTTPException(
                status_code=429,
                detail=f"Too many failed login attempts. Please wait {_RATE_WINDOW_SECONDS} seconds.",
            )

def _record_failure(ip: str):
    if _redis:
        key = f"sp:ratelimit:{ip}"
        pipe = _redis.pipeline()
        pipe.incr(key)
        pipe.expire(key, _RATE_WINDOW_SECONDS)
        pipe.execute()
    else:
        _fail_counts[ip].append(time.time())

def _clear_failures(ip: str):
    if _redis:
        _redis.delete(f"sp:ratelimit:{ip}")
    else:
        _fail_counts.pop(ip, None)


# ── C-7: Hardened JWT helpers ─────────────────────────────────────────────────
def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

def _b64url_decode(s: str) -> bytes:
    """Correct padding regardless of how many chars are missing."""
    padding = (4 - len(s) % 4) % 4
    return base64.urlsafe_b64decode(s + "=" * padding)

def _sign(payload: dict) -> str:
    header  = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body    = _b64url(json.dumps(payload).encode())
    sig     = _b64url(
        hmac.new(SECRET_KEY.encode(), f"{header}.{body}".encode(), hashlib.sha256).digest()
    )
    return f"{header}.{body}.{sig}"

def _verify(token: str) -> dict:
    try:
        parts = token.split(".")
        if len(parts) != 3:
            raise ValueError("malformed token")
        header_b64, body_b64, sig = parts

        # C-7: verify alg claim before trusting the signature
        header = json.loads(_b64url_decode(header_b64))
        if header.get("alg") != "HS256":
            raise ValueError("unsupported algorithm")

        expected = _b64url(
            hmac.new(SECRET_KEY.encode(), f"{header_b64}.{body_b64}".encode(), hashlib.sha256).digest()
        )
        if not hmac.compare_digest(sig, expected):
            raise ValueError("bad signature")

        payload = json.loads(_b64url_decode(body_b64))

        now = time.time()
        if payload.get("exp", 0) < now:
            raise ValueError("token expired")
        # C-7: nbf (not-before) check
        if "nbf" in payload and payload["nbf"] > now:
            raise ValueError("token not yet valid")

        # C-9: blocklist check — reject revoked tokens
        jti = payload.get("jti")
        if jti and _is_token_revoked(jti):
            raise ValueError("token has been revoked")

        return payload
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}")


# ── C-9: Token blocklist ──────────────────────────────────────────────────────
_revoked_jtis: set = set()   # fallback only

def _revoke_token(payload: dict) -> None:
    """Add the token's JTI to the blocklist until it expires."""
    jti = payload.get("jti")
    if not jti:
        return
    remaining = max(0, int(payload.get("exp", 0) - time.time()))
    if _redis and remaining > 0:
        _redis.setex(f"sp:blocklist:{jti}", remaining, "1")
    else:
        _revoked_jtis.add(jti)

def _is_token_revoked(jti: str) -> bool:
    if _redis:
        return _redis.exists(f"sp:blocklist:{jti}") == 1
    return jti in _revoked_jtis


def _hash_pw(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

def _verify_pw(plaintext: str, stored_hash: str) -> bool:
    return bcrypt.checkpw(plaintext.encode("utf-8"), stored_hash.encode("utf-8"))


# ── C-8: get_current_user accepts Bearer header OR HttpOnly cookie ────────────
def get_current_user(
    request: Request,
    authorization: str = Header(None),
) -> dict:
    """Dependency — call in protected routes. Accepts Bearer token or sp_jwt cookie."""
    if authorization and authorization.startswith("Bearer "):
        return _verify(authorization.split(" ", 1)[1])
    cookie_token = request.cookies.get("sp_jwt")
    if cookie_token:
        return _verify(cookie_token)
    raise HTTPException(status_code=401, detail="Authorization header or session cookie missing.")


def _make_payload(user_id: int, username: str, role: str) -> dict:
    now = int(time.time())
    return {
        "sub":  user_id,
        "user": username,
        "role": role,
        "iat":  now,
        "nbf":  now,
        "exp":  int((datetime.now(timezone.utc) + timedelta(days=JWT_EXPIRE_DAYS)).timestamp()),
        # C-9: unique token ID enables per-token revocation
        "jti":  secrets.token_hex(16),
    }


# ── POST /auth/forgot-password ────────────────────────────────────────────────
@router.post("/forgot-password", status_code=202)
async def forgot_password(req: ForgotPasswordRequest):
    """
    Issues a time-limited password reset token and sends it via email.
    Always returns 202 regardless of whether the email exists (prevents enumeration).
    """
    db = Session(engine)
    try:
        row = db.execute(
            sa.text("SELECT id, email FROM users WHERE email = :e"),
            {"e": req.email},
        ).fetchone()
        if not row:
            return {"ok": True}  # silent — no enumeration

        raw_token  = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=_RESET_TOKEN_EXPIRE_MINUTES)

        # Invalidate any existing unused tokens for this user first
        db.execute(
            sa.text("UPDATE password_reset_tokens SET used = 1 WHERE user_id = :uid AND used = 0"),
            {"uid": row.id},
        )
        db.add(PasswordResetTokenORM(
            user_id    = row.id,
            token_hash = token_hash,
            expires_at = expires_at,
            used       = 0,
        ))
        db.commit()

        reset_link = f"{APP_BASE_URL}/reset-password.html?token={raw_token}"
        body = (
            f"You requested a password reset for your SocialProof account.\n\n"
            f"Click the link below to set a new password. This link expires in "
            f"{_RESET_TOKEN_EXPIRE_MINUTES} minutes.\n\n"
            f"{reset_link}\n\n"
            f"If you did not request this, you can safely ignore this email."
        )
        _send_email(row.email, "Reset your SocialProof password", body)
        logger.info(f"Password reset token issued for user_id={row.id}")
    finally:
        db.close()

    return {"ok": True}


# ── POST /auth/reset-password ────────────────────────────────────────────────
@router.post("/reset-password")
async def reset_password(req: ResetPasswordRequest):
    """Validates the reset token and updates the user's password."""
    token_hash = hashlib.sha256(req.token.encode()).hexdigest()
    db = Session(engine)
    try:
        row = db.execute(
            sa.text("""
                SELECT * FROM password_reset_tokens
                WHERE token_hash = :h AND used = 0 AND expires_at > UTC_TIMESTAMP()
            """),
            {"h": token_hash},
        ).fetchone()
        if not row:
            raise HTTPException(status_code=400, detail="Invalid or expired reset token.")

        new_hash = _hash_pw(req.new_password)
        db.execute(
            sa.text("UPDATE users SET password_hash = :h WHERE id = :uid"),
            {"h": new_hash, "uid": row.user_id},
        )
        db.execute(
            sa.text("UPDATE password_reset_tokens SET used = 1 WHERE id = :id"),
            {"id": row.id},
        )
        db.commit()
        logger.info(f"Password reset completed for user_id={row.user_id}")
    finally:
        db.close()

    return {"ok": True}


# ── POST /auth/send-verification ──────────────────────────────────────────────
@router.post("/send-verification")
async def send_verification(request: Request, authorization: str = Header(None)):
    """(Re)sends an email verification link for the authenticated user."""
    payload = get_current_user(request, authorization)
    user_id = payload["sub"]

    db = Session(engine)
    try:
        row = db.execute(
            sa.text("SELECT id, email, is_verified FROM users WHERE id = :uid"),
            {"uid": user_id},
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="User not found.")
        if row.is_verified:
            return {"ok": True, "message": "Already verified."}

        raw_token  = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=_VERIFY_TOKEN_EXPIRE_MINUTES)

        # Invalidate previous unused tokens
        db.execute(
            sa.text("UPDATE email_verification_tokens SET used = 1 WHERE user_id = :uid AND used = 0"),
            {"uid": user_id},
        )
        db.add(EmailVerificationTokenORM(
            user_id    = user_id,
            token_hash = token_hash,
            expires_at = expires_at,
            used       = 0,
        ))
        db.commit()

        verify_link = f"{APP_BASE_URL}/verify-email.html?token={raw_token}"
        body = (
            f"Welcome to SocialProof! Please verify your email address.\n\n"
            f"{verify_link}\n\n"
            f"This link expires in 24 hours."
        )
        _send_email(row.email, "Verify your SocialProof email", body)
        logger.info(f"Verification email sent to user_id={user_id}")
    finally:
        db.close()

    return {"ok": True}


# ── POST /auth/verify-email ───────────────────────────────────────────────────
@router.post("/verify-email")
async def verify_email(req: VerifyEmailRequest):
    """Marks the user's email as verified."""
    token_hash = hashlib.sha256(req.token.encode()).hexdigest()
    db = Session(engine)
    try:
        row = db.execute(
            sa.text("""
                SELECT * FROM email_verification_tokens
                WHERE token_hash = :h AND used = 0 AND expires_at > UTC_TIMESTAMP()
            """),
            {"h": token_hash},
        ).fetchone()
        if not row:
            raise HTTPException(status_code=400, detail="Invalid or expired verification token.")

        db.execute(
            sa.text("UPDATE users SET is_verified = 1 WHERE id = :uid"),
            {"uid": row.user_id},
        )
        db.execute(
            sa.text("UPDATE email_verification_tokens SET used = 1 WHERE id = :id"),
            {"id": row.id},
        )
        db.commit()
        logger.info(f"Email verified for user_id={row.user_id}")
    finally:
        db.close()

    return {"ok": True}


# ── Check username availability ───────────────────────────────────────────────
@router.get("/check-username")
async def check_username(u: str = Query(..., min_length=3, max_length=50)):
    """Returns {"available": bool} — used by the registration form for real-time feedback."""
    with Session(engine) as db:
        exists = db.query(UserORM).filter(UserORM.username == u).first() is not None
    return {"available": not exists}


# ── Register ──────────────────────────────────────────────────────────────────
@router.post("/register", response_model=AuthResponse, status_code=201)
async def register(req: RegisterRequest, request: Request):
    # Schema validator already rejects research_consent=False, but we
    # double-check here so the DB invariant is always enforced.
    if not req.research_consent:
        raise HTTPException(
            status_code=422,
            detail="Research participation consent is required to create an account.",
        )

    db = Session(engine)
    try:
        exists = db.execute(
            sa.text("SELECT id FROM users WHERE email=:e OR username=:u"),
            {"e": req.email, "u": req.username},
        ).fetchone()
        if exists:
            raise HTTPException(status_code=409, detail="Email or username already taken.")

        now = datetime.now(timezone.utc)
        user = UserORM(
            username             = req.username,
            email                = req.email,
            password_hash        = _hash_pw(req.password),
            role                 = "user",
            research_consent     = 1,
            research_consent_at  = now,
            consent_withdrawn_at = None,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        # ── Audit log: record consent grant ──────────────────────────────────
        ip         = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent", "")[:500]
        db.add(ResearchConsentLogORM(
            user_id    = user.id,
            action     = "granted",
            ip_address = ip,
            user_agent = user_agent,
            acted_at   = now,
        ))
        db.commit()

        token = _sign(_make_payload(user.id, user.username, user.role))
        logger.info(f"New user registered: {user.username} (id={user.id}), research_consent=True")

        # Send email verification token
        raw_token  = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=_VERIFY_TOKEN_EXPIRE_MINUTES)
        db.add(EmailVerificationTokenORM(
            user_id    = user.id,
            token_hash = token_hash,
            expires_at = expires_at,
            used       = 0,
        ))
        db.commit()
        verify_link = f"{APP_BASE_URL}/verify-email.html?token={raw_token}"
        body = (
            f"Welcome to SocialProof, {user.username}!\n\n"
            f"Please verify your email address:\n{verify_link}\n\n"
            f"This link expires in 24 hours."
        )
        _send_email(user.email, "Verify your SocialProof email", body)

        return AuthResponse(token=token, user_id=user.id, username=user.username, role=user.role)
    finally:
        db.close()


# ── GET /auth/consent-status ──────────────────────────────────────────────────
@router.get("/consent-status", response_model=ConsentStatusResponse)
async def consent_status(authorization: str = Header(None)):
    """
    Returns the current research consent status for the authenticated user.
    consent_withdrawn_at=null means consent is still active.
    """
    payload = _verify(authorization)
    user_id = payload.get("sub") or payload.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token.")

    db = Session(engine)
    try:
        row = db.execute(
            sa.text("SELECT research_consent, research_consent_at, consent_withdrawn_at FROM users WHERE id = :uid"),
            {"uid": user_id},
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="User not found.")
        return ConsentStatusResponse(
            user_id              = user_id,
            research_consent     = bool(row.research_consent),
            research_consent_at  = row.research_consent_at.isoformat() if row.research_consent_at else None,
            consent_withdrawn_at = row.consent_withdrawn_at.isoformat() if row.consent_withdrawn_at else None,
        )
    finally:
        db.close()


# ── POST /auth/withdraw-consent ───────────────────────────────────────────────
@router.post("/withdraw-consent")
async def withdraw_consent(req: ConsentWithdrawRequest, request: Request, authorization: str = Header(None)):
    """
    Allows an authenticated user to withdraw their research participation consent
    at any time, as required by informed consent principles.

    Effect:
      - Sets users.consent_withdrawn_at to the current UTC timestamp.
      - Appends an audit row to research_consent_log with action='withdrawn'.
      - Does NOT delete the user's account or any existing data.
      - Research queries MUST filter WHERE consent_withdrawn_at IS NULL — this
        ensures the user's future data is excluded from research analysis.

    The user can re-grant consent by contacting the research team (or via a
    future re-consent flow). This endpoint is one-way by design.
    """
    payload = _verify(authorization)
    token_user_id = payload.get("sub") or payload.get("user_id")
    if not token_user_id or int(token_user_id) != req.user_id:
        raise HTTPException(status_code=403, detail="You can only withdraw your own consent.")

    db = Session(engine)
    try:
        row = db.execute(
            sa.text("SELECT id, consent_withdrawn_at FROM users WHERE id = :uid"),
            {"uid": req.user_id},
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="User not found.")
        if row.consent_withdrawn_at is not None:
            raise HTTPException(status_code=409, detail="Consent has already been withdrawn.")

        now        = datetime.now(timezone.utc)
        ip         = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent", "")[:500]

        db.execute(
            sa.text("UPDATE users SET consent_withdrawn_at = :now WHERE id = :uid"),
            {"now": now, "uid": req.user_id},
        )
        db.add(ResearchConsentLogORM(
            user_id    = req.user_id,
            action     = "withdrawn",
            ip_address = ip,
            user_agent = user_agent,
            acted_at   = now,
        ))
        db.commit()
        logger.info(f"[Consent] User {req.user_id} withdrew research consent at {now}.")
        return {
            "ok":      True,
            "message": (
                "Your research participation consent has been withdrawn. "
                "Your data will no longer be included in research analysis going forward. "
                "Your account and prior submissions remain unchanged."
            ),
        }
    finally:
        db.close()


# ── Login (Bearer token — DEPRECATED, kept for API backward-compatibility) ────
@router.post("/login", response_model=AuthResponse)
async def login(req: LoginRequest, request: Request, response: Response):
    """
    DEPRECATED — returns the JWT in the response body, which requires the client
    to store it somewhere accessible to JavaScript (XSS risk).
    Use POST /auth/cookie-login instead.
    """
    response.headers["Deprecation"] = "true"
    response.headers["Sunset"] = "2026-12-31"
    response.headers["Link"] = '</auth/cookie-login>; rel="successor-version"'

    ip = request.client.host if request.client else "unknown"
    _check_rate_limit(ip)

    db = Session(engine)
    try:
        row = db.execute(
            sa.text("SELECT * FROM users WHERE email=:id OR username=:id"),
            {"id": req.identifier},
        ).fetchone()
        if not row or not _verify_pw(req.password, row.password_hash):
            _record_failure(ip)
            raise HTTPException(status_code=401, detail="Invalid credentials.")

        _clear_failures(ip)
        token = _sign(_make_payload(row.id, row.username, row.role))
        logger.warning(
            f"Deprecated /auth/login used by {ip} for user '{row.username}'. "
            "Migrate to /auth/cookie-login."
        )
        return AuthResponse(token=token, user_id=row.id, username=row.username, role=row.role)
    finally:
        db.close()


# ── Cookie Login (preferred) ──────────────────────────────────────────────────
@router.post("/cookie-login")
async def cookie_login(req: LoginRequest, request: Request, response: Response):
    """
    Sets the JWT as an HttpOnly; Secure; SameSite=Strict cookie.
    The token itself is NOT in the response body — only display data is returned.
    """
    ip = request.client.host if request.client else "unknown"
    _check_rate_limit(ip)

    db = Session(engine)
    try:
        row = db.execute(
            sa.text("SELECT * FROM users WHERE email=:id OR username=:id"),
            {"id": req.identifier},
        ).fetchone()
        if not row or not _verify_pw(req.password, row.password_hash):
            _record_failure(ip)
            raise HTTPException(status_code=401, detail="Invalid credentials.")

        _clear_failures(ip)
        token = _sign(_make_payload(row.id, row.username, row.role))

        response.set_cookie(
            key="sp_jwt",
            value=token,
            httponly=True,
            secure=True,
            samesite="strict",
            max_age=JWT_EXPIRE_DAYS * 86400,
            path="/",
        )
        return {"user_id": row.id, "username": row.username, "role": row.role}
    finally:
        db.close()


# ── Cookie Logout ─────────────────────────────────────────────────────────────
@router.post("/cookie-logout")
async def cookie_logout(request: Request, response: Response):
    """Revokes the current token server-side and clears the sp_jwt cookie."""
    cookie_token = request.cookies.get("sp_jwt")
    if cookie_token:
        try:
            parts = cookie_token.split(".")
            if len(parts) == 3:
                payload = json.loads(_b64url_decode(parts[1]))
                _revoke_token(payload)
        except Exception:
            pass
    response.delete_cookie(key="sp_jwt", path="/", httponly=True, secure=True, samesite="strict")
    return {"ok": True}


# ── Me ────────────────────────────────────────────────────────────────────────
@router.get("/me")
async def me(request: Request, authorization: str = Header(None)):
    payload = get_current_user(request, authorization)
    return {"user_id": payload["sub"], "username": payload["user"], "role": payload["role"]}


# ── Logout (Bearer token flow — revokes token server-side) ────────────────────
@router.post("/logout")
async def logout(request: Request, authorization: str = Header(None)):
    """
    Revokes the presented Bearer token or cookie token server-side.
    Without Redis, revocation only applies within this process instance.
    """
    token = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ", 1)[1]
    else:
        token = request.cookies.get("sp_jwt")

    if token:
        try:
            parts = token.split(".")
            if len(parts) == 3:
                payload = json.loads(_b64url_decode(parts[1]))
                _revoke_token(payload)
        except Exception:
            pass

    return {"ok": True, "message": "Token revoked."}


# ── GET /auth/session ─────────────────────────────────────────────────────────
@router.get("/session")
async def get_session_token():
    """Generate a cryptographically secure anonymous session token. Stateless."""
    return {"session_token": secrets.token_hex(32)}

# ── POST /auth/merge-session ──────────────────────────────────────────────────
# Checklist ms3: Fix anonymous → authenticated session merge.
# When users create an account after anonymous sessions, we migrate all
# rows with user_id = NULL and session_token = <old_token> to user_id = <uid>.
# Tables covered: submissions, user_reflections, confidence_snapshots,
#   source_diversity_log, user_skill_progress, user_skill_history,
#   lesson_completions, pretest_results, user_behavior_profile, quiz_attempts.
@router.post("/merge-session")
async def merge_session(body: dict, current_user=Depends(get_current_user)):
    """
    Merge anonymous session data into the authenticated user account.
    Must be called immediately after login/register for users who had
    a prior anonymous session. Idempotent — safe to call multiple times.
    """
    session_token = (body.get("session_token") or "").strip()
    if not session_token:
        raise HTTPException(status_code=422, detail="session_token is required.")

    user_id = current_user["id"]

    _MERGE_TABLES = [
        # (table, user_id_col, session_col)
        ("submissions",            "user_id",  "session_token"),
        ("user_reflections",       "user_id",  "session_token"),
        ("confidence_snapshots",   "user_id",  "session_token"),
        ("source_diversity_log",   None,       "session_token"),   # no user_id col — join via submission
        ("user_skill_progress",    "user_id",  "session_token"),
        ("user_skill_history",     "user_id",  "session_token"),
        ("lesson_completions",     "user_id",  "session_token"),
        ("pretest_results",        "user_id",  "session_token"),
        ("user_behavior_profile",  "user_id",  "session_token"),
    ]

    merged = {}
    try:
        with engine.begin() as conn:
            for table, uid_col, sess_col in _MERGE_TABLES:
                if uid_col is None:
                    continue  # handled separately
                try:
                    result = conn.execute(sa.text(f"""
                        UPDATE {table}
                        SET {uid_col} = :uid
                        WHERE {uid_col} IS NULL
                          AND {sess_col} = :tok
                    """), {"uid": user_id, "tok": session_token})
                    merged[table] = result.rowcount
                except Exception as te:
                    logger.warning(f"[merge-session] {table} update failed: {te}")
                    merged[table] = 0

            # source_diversity_log: update via submission join
            try:
                result = conn.execute(sa.text("""
                    UPDATE source_diversity_log sdl
                    INNER JOIN submissions s ON s.id = sdl.submission_id
                    SET s.user_id = :uid
                    WHERE sdl.session_token = :tok
                      AND s.user_id IS NULL
                """), {"uid": user_id, "tok": session_token})
                merged["source_diversity_log"] = result.rowcount
            except Exception as te:
                logger.warning(f"[merge-session] source_diversity_log update failed: {te}")
                merged["source_diversity_log"] = 0

        logger.info(f"[merge-session] user={user_id} session={session_token[:8]}… merged={merged}")
        return {"status": "ok", "user_id": user_id, "merged_rows": merged}

    except Exception as e:
        logger.error(f"[merge-session] error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Session merge failed.")
