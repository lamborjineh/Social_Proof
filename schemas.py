"""
SocialProof — Pydantic Schemas  v6.0

Changes in v6.0 vs v5.0:
  - ArticleResult gains:
      mbfc_url, mbfc_factual, mbfc_bias  — MBFC badge fields (link + context, not verdict)
      source_category                    — "government" | "academic" | "news" | "factcheck" | "international" | "other"
      retrieval_reason                   — short human-readable explanation of why this article was retrieved
  - ArticleRetrievalResponse gains:
      source_diversity                   — SourceDiversityInfo breakdown shown to user
  - ConfidenceSnapshot schema added      — for before/after confidence capture
  - ReasoningJournalEntry schema added   — for Reasoning Journal POST
  - SourceDiversityInfo schema added     — shown under evidence cards
  - All auth, quiz, pretest, lesson schemas unchanged.
"""

from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, EmailStr, field_validator


# ── Analysis — Request ────────────────────────────────────────────────────────

class AnalyzeRequest(BaseModel):
    text:               Optional[str] = Field(None, description="Raw text content")
    url:                Optional[str] = Field(None, description="URL to analyze")
    input_type:         str           = Field("text", description="text | url | image | file")
    session_token:      str           = Field(..., description="Anonymous session token")
    user_id:            Optional[int] = Field(None, description="Authenticated user ID")
    image_data:         Optional[str] = Field(None, description="Base64-encoded image (input_type=image)")
    file_data:          Optional[str] = Field(None, description="Base64-encoded file bytes (input_type=file)")
    file_name:          Optional[str] = Field(None, description="Original filename including extension")
    confidence_before:  Optional[int] = Field(None, description="User confidence before retrieval (1-5)")


# ── Source diversity breakdown ────────────────────────────────────────────────

class SourceDiversityInfo(BaseModel):
    """
    Source-type composition of the retrieved articles.
    Surfaced to the user to teach information ecosystem awareness
    (UNESCO MIL competency: 'Access and Evaluate').
    """
    total_articles:      int  = 0
    count_government:    int  = 0
    count_academic:      int  = 0
    count_news:          int  = 0
    count_factcheck:     int  = 0
    count_international: int  = 0
    count_other:         int  = 0
    diversity_score:     float = 0.0   # 0.0–1.0; higher = more varied


# ── Article result (one item returned from retrieval) ─────────────────────────

class ArticleResult(BaseModel):
    """
    A single retrieved article surfaced for the user to evaluate.
    No NLI label, no type (support/contradict/neutral), no verdict.

    v6.0 additions:
      mbfc_url, mbfc_factual, mbfc_bias — MBFC badge (link + label as context)
      source_category                   — used for source diversity breakdown
      retrieval_reason                  — "Why was this retrieved?" explainability chip
    """
    article_title:   str
    publisher:       str
    date_published:  Optional[str] = None
    source_url:      Optional[str] = None
    source_type:     Optional[str] = None    # "faiss" | "live" | "hardcoded"

    # MBFC fields — shown as a badge with link; never as a verdict
    mbfc_url:        Optional[str] = None    # direct link to MBFC page for this domain
    mbfc_factual:    Optional[str] = None    # e.g. "HIGH" | "MOSTLY FACTUAL" | "MIXED" | "LOW"
    mbfc_bias:       Optional[str] = None    # e.g. "LEFT-CENTER" | "CENTER" | "RIGHT"

    # Source type for diversity panel
    source_category: Optional[str] = None   # "government" | "academic" | "news" | "factcheck" | "international" | "other"

    # Retrieval explainability — one short phrase shown under the card
    retrieval_reason: Optional[str] = None  # e.g. "Matched: election fraud claims"


# ── Analysis — Response ───────────────────────────────────────────────────────

class ArticleRetrievalResponse(BaseModel):
    """
    Returned from POST /analyze.
    Contains retrieved articles for the user to evaluate — no verdict from the system.
    """
    submission_id:      int
    evaluation_id:      Optional[int]         = None
    articles:           List[ArticleResult]
    keywords:           List[str]             = Field(default_factory=list)
    processing_ms:      int
    live_search_used:   bool                  = False
    url_fetch_failed:   bool                  = False
    url_fetch_error:    str                   = ""
    source_diversity:   Optional[SourceDiversityInfo] = None


# ── MBFC domain signal — link only, not verdict ───────────────────────────────

class MBFCRating(BaseModel):
    domain:   str
    mbfc_url: Optional[str] = None


# ── Source step — on-demand domain lookup (no verdict) ───────────────────────

class SourceStepResponse(BaseModel):
    domain:       str
    source_type:  str
    trust_signals: List[str]
    mbfc:         Optional[MBFCRating] = None


# ── NEW v6.0: Reasoning Journal (Bloom's L4–5 reflection) ────────────────────

class ReasoningJournalEntry(BaseModel):
    """
    Posted by the frontend at three stages of the evaluation flow.

    stage values:
      'post_eval'      — after completing the 8-step evaluation
      'post_evidence'  — after the user has read the retrieved articles
      'post_verdict'   — after the user submits their final verdict

    bloom_level is computed by the frontend based on which prompts the user
    answered and how thoroughly (word count heuristic). Range 1–5.
    """
    submission_id:    Optional[int] = None
    user_id:          Optional[int] = None
    session_token:    str
    stage:            str           = "post_verdict"   # post_eval | post_evidence | post_verdict
    what_noticed:     Optional[str] = None
    still_uncertain:  Optional[str] = None
    would_check_next: Optional[str] = None
    free_reasoning:   Optional[str] = None
    verdict_position: Optional[str] = None
    bloom_level:      Optional[int] = None


class ReasoningJournalResponse(BaseModel):
    id:          int
    stage:       str
    bloom_level: Optional[int]
    saved:       bool = True


# ── NEW v7.0: Metacognitive Calibration Challenge Gate ────────────────────────

class ChallengeGateRequest(BaseModel):
    """
    Posted by the frontend BEFORE the verdict submit button fires.
    The backend evaluates the user's reasoning quality signals and decides
    whether to let them through (gate=pass) or issue a Socratic challenge.

    challenge_round: 0 on first call; 1 after the user responds to the first
    challenge; 2 after the second. On round >= 2 the gate always passes to
    prevent frustration loops.

    challenge_response: the user's typed reply to the previous challenge prompt.
    Empty on round 0.
    """
    submission_id:       Optional[int] = None
    user_id:             Optional[int] = None
    session_token:       str

    # Reasoning quality signals (mirrors user-evaluation inputs)
    bloom_level:         Optional[int]   = None    # 1–5 from Reasoning Journal
    calibration_gap:     Optional[float] = None    # from /user-evaluation response
    skipped_steps:       Optional[List[str]] = Field(default_factory=list)
    confidence_level:    Optional[str]   = None    # "high" | "medium" | "low"
    word_count:          Optional[int]   = None    # total words in reflection
    verdict_position:    Optional[str]   = None    # "supported" | "unsupported" | "uncertain"

    # Challenge loop state
    challenge_round:     int             = 0
    challenge_response:  Optional[str]   = None    # user's reply to prior challenge


class ChallengeGateResponse(BaseModel):
    """
    gate="pass"      → frontend enables the Submit Verdict button
    gate="challenge" → frontend shows challenge_prompt and blocks submission

    challenge_type values:
      "inoculation"   — Inoculation Theory: pre-bunk a detected reasoning pattern
      "slow_down"     — Dual Process: force System 2 engagement with a specific question
      "bloom_upgrade" — Bloom's: reject L1/L2 reasoning, ask for L3+ response

    min_words: minimum word count the user's challenge_response must reach
               before the gate will pass on the next round.
    """
    gate:             str                    # "pass" | "challenge"
    challenge_type:   Optional[str]  = None  # "inoculation" | "slow_down" | "bloom_upgrade"
    challenge_prompt: Optional[str]  = None  # The Socratic question shown to the user
    context_note:     Optional[str]  = None  # Why this challenge is being issued (shown subtly)
    min_words:        int            = 15
    round:            int            = 0
    framework_label:  Optional[str]  = None  # "Inoculation Theory" | "Dual Process" | "Bloom's Taxonomy"


# ── NEW v6.0: Confidence Snapshot ────────────────────────────────────────────

class ConfidenceSnapshotRequest(BaseModel):
    """
    Captures confidence_before (at the start of a session) or
    confidence_after (after reviewing evidence), or both.
    Posted from the frontend at Step 1 (before retrieval) and again after
    the user reads the evidence cards.
    """
    submission_id:      Optional[int] = None
    user_id:            Optional[int] = None
    session_token:      str
    confidence_before:  Optional[int] = None    # 1–5
    confidence_after:   Optional[int] = None    # 1–5
    confidence_label:   Optional[str] = None    # "high" | "medium" | "low" (from step eval)


class ConfidenceSnapshotResponse(BaseModel):
    id:                int
    confidence_before: Optional[int]
    confidence_after:  Optional[int]
    confidence_delta:  Optional[int]
    calibration_flag:  bool = False


# ── User Evaluation (self-assessment) ─────────────────────────────────────────

class UserEvaluationRequest(BaseModel):
    submission_id:     int
    user_id:           Optional[int]       = None
    session_token:     str
    identified_claims: Optional[List[str]] = None
    source_credible:   Optional[str]       = None
    bias_detected:     Optional[bool]      = None
    evidence_assessed: Optional[bool]      = None
    confidence_level:  Optional[str]       = None
    skipped_steps:     Optional[List[str]] = Field(default_factory=list)
    time_spent_seconds: Optional[int]      = None
    # v6.0: confidence before/after forwarded through user-evaluation
    confidence_before: Optional[int]       = None
    confidence_after:  Optional[int]       = None


class FeedbackItem(BaseModel):
    type: str   # "good" | "warn" | "bad" | "calibration" | "missed" | "diversity"
    text: str
    # Optional structured context for richer feedback display
    step_name:   Optional[str] = None    # which step this feedback belongs to
    learn_more:  Optional[str] = None    # lesson_key to link to


class TriggeredLesson(BaseModel):
    key:            str
    title:          str
    topic:          str
    trigger_reason: str


class ComparisonResult(BaseModel):
    confidence_level:   Optional[str]
    triggered_lessons:  List[TriggeredLesson]
    feedback_items:     List[FeedbackItem]
    feedback_summary:   str
    lesson_context_map: Dict[str, str]         = Field(default_factory=dict)
    calibration_gap:    Optional[float]        = None   # positive = overconfident
    skill_deltas:       Optional[Dict[str, str]] = None  # topic → "improved" | "needs_work"


class UserEvaluationResponse(BaseModel):
    submission_id: int
    comparison:    ComparisonResult


# ── Auth ──────────────────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    username:          str      = Field(..., min_length=3, max_length=50)
    email:             EmailStr = Field(..., description="Valid email address")
    password:          str      = Field(..., min_length=8)
    # ── Research consent (required before account creation) ───────────────────
    # Must be True — the registration endpoint rejects False.
    # Stored with a UTC timestamp for IRB audit compliance.
    research_consent:  bool     = Field(
        ...,
        description=(
            "Participant agrees to anonymised research use of their activity data. "
            "Must be True to register."
        ),
    )

    @field_validator("password")
    @classmethod
    def password_complexity(cls, v: str) -> str:
        if len(v.encode("utf-8")) > 72:
            raise ValueError("Password must be 72 characters or fewer (bcrypt limit).")
        if not any(c.isupper() for c in v):
            raise ValueError("Password must contain at least one uppercase letter.")
        if not any(c.isdigit() for c in v):
            raise ValueError("Password must contain at least one digit.")
        if not any(c in "!@#$%^&*()_+-=[]{}|;:,.<>?" for c in v):
            raise ValueError("Password must contain at least one special character (!@#$%^&*...).")
        return v

    @field_validator("research_consent")
    @classmethod
    def consent_must_be_given(cls, v: bool) -> bool:
        if not v:
            raise ValueError(
                "Research participation consent is required to create an account. "
                "You may withdraw consent at any time after registration."
            )
        return v


class ConsentWithdrawRequest(BaseModel):
    """Posted to POST /auth/withdraw-consent by an authenticated user."""
    user_id: int


class ConsentStatusResponse(BaseModel):
    user_id:              int
    research_consent:     bool
    research_consent_at:  Optional[str] = None   # ISO-8601 UTC
    consent_withdrawn_at: Optional[str] = None   # ISO-8601 UTC; None = still active


class LoginRequest(BaseModel):
    identifier: str
    password:   str


class AuthResponse(BaseModel):
    token:    str
    user_id:  int
    username: str
    role:     str


# ── Quiz ──────────────────────────────────────────────────────────────────────

class QuizAttemptRequest(BaseModel):
    user_id:        Optional[int] = None
    question_id:    int
    selected_index: int = Field(..., ge=0, le=9, description="Zero-based index of the chosen option (max 9)")


class QuizAttemptResponse(BaseModel):
    is_correct:        bool
    correct_index:     int
    explanation:       Optional[str] = None
    hint:              Optional[str] = None
    topic:             str
    difficulty:        Optional[str] = None
    skill_used:        Optional[str] = None
    skill_label:       Optional[str] = None
    skill_description: Optional[str] = None


# ── Pretest / Posttest ────────────────────────────────────────────────────────

class PretestAnswerItem(BaseModel):
    claim_id:       int
    selected_label: str


class PretestSubmitRequest(BaseModel):
    session_token: str
    user_id:       Optional[int] = None
    answers:       List[PretestAnswerItem]


class PretestResultResponse(BaseModel):
    phase:       str
    score_pct:   int
    correct:     int
    total:       int
    delta:       Optional[int]           = None
    skill_gains: Optional[Dict[str, Any]] = None


# ── Lessons ───────────────────────────────────────────────────────────────────

class LessonCompletionRequest(BaseModel):
    lesson_id:     int
    user_id:       Optional[int] = None
    session_token: str


class LessonCompletionResponse(BaseModel):
    lesson_id:    int
    completed_at: str
    message:      str = "Lesson marked complete."


# ── Auth — Password reset / Email verification ────────────────────────────────

class ForgotPasswordRequest(BaseModel):
    email: EmailStr

class ResetPasswordRequest(BaseModel):
    token:        str = Field(..., min_length=10)
    new_password: str = Field(..., min_length=8)

class VerifyEmailRequest(BaseModel):
    token: str = Field(..., min_length=10)
