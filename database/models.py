"""
Active tables (33):
  admin_audit_log, confidence_snapshots, email_verification_tokens,
  eval_question_branches, eval_questions, lesson_completions, lessons,
  lessons_triggered, mbfc_domains, mindmap_edges, mindmap_interactions,
  mindmap_lens_progress, mindmap_nodes, mindmap_progress, mindmap_suggestions,
  password_reset_tokens, pretest_claims, pretest_results, quiz_attempts,
  quiz_questions, research_consent_log, source_diversity_log, submissions,
  url_tracking, user_behavior_profile, user_created_content, user_reflections,
  user_skill_history, user_skill_progress, users

Previously raw-SQL-only tables now with ORM classes (added in this cleanup):
  eval_question_branches — branching follow-up prompts per eval question
  pretest_claims         — managed claim pool for pre/post-test questions
  user_created_content   — user-authored corrective summaries and cohort posts

Removed in a prior cleanup:
  claims          — leftover fact-checking schema; label enum encoded system verdicts.
                    Nothing inserted into this table in any router or pipeline.
  evidence        — FK child of claims; removed with it.
  factcheck_cache — cached Google Fact Check API responses; get_factcheck_results()
                    was never called by any router. Removed along with the function.
"""

from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy import (
    Column, Integer, String, Text, Float,
    DateTime, SmallInteger, ForeignKey,
    Enum as SAEnum,
    create_engine,
)
from sqlalchemy.orm import declarative_base, relationship

from config import DATABASE_URL, logger

Base   = declarative_base()
engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=3600)


# ── Core tables ───────────────────────────────────────────────────────────────

class UserORM(Base):
    __tablename__ = "users"
    id                     = Column(Integer, primary_key=True, autoincrement=True)
    username               = Column(String(50),  nullable=False, unique=True)
    email                  = Column(String(150), nullable=False, unique=True)
    password_hash          = Column(String(255), nullable=False)
    role                   = Column(SAEnum("user", "admin"), nullable=False, default="user")
    is_verified            = Column(SmallInteger, nullable=False, default=0)
    # ── Research consent (IRB-compliant) ──────────────────────────────────────
    # research_consent:       1 = user agreed to research data use at registration
    # research_consent_at:    UTC timestamp when consent was granted
    # consent_withdrawn_at:   UTC timestamp if the user later withdrew consent;
    #                         NULL means consent is still active.
    # Any research query MUST filter: WHERE research_consent = 1
    #                                  AND consent_withdrawn_at IS NULL
    research_consent       = Column(SmallInteger, nullable=False, default=0)
    research_consent_at    = Column(DateTime, nullable=True)
    consent_withdrawn_at   = Column(DateTime, nullable=True)
    created_at             = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at             = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class ResearchConsentLogORM(Base):
    """
    Immutable audit trail for every consent grant and withdrawal.
    One row per action — never updated, never deleted.
    Required for IRB compliance: proves consent was obtained and honours
    withdrawal requests programmatically.
    """
    __tablename__  = "research_consent_log"
    id             = Column(Integer, primary_key=True, autoincrement=True)
    user_id        = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    action         = Column(SAEnum("granted", "withdrawn"), nullable=False)
    ip_address     = Column(String(45),  nullable=True)   # IPv4 or IPv6
    user_agent     = Column(String(500), nullable=True)
    acted_at       = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))


class AdminAuditLogORM(Base):
    """
    Immutable audit trail for every admin-initiated mutation.

    One row per action — never updated, never deleted.
    Captures who did what to which resource, when, and from where.

    `action`        : create | update | delete | role_change | upload | reorder
    `resource_type` : user | lesson | quiz_question | eval_question | corpus
    `resource_id`   : string representation of the affected record's PK
                      (string so it works for both integer PKs and slug PKs)
    `detail`        : JSON blob — key context fields (title, username, role, …)
    """
    __tablename__   = "admin_audit_log"
    id              = Column(Integer, primary_key=True, autoincrement=True)
    admin_id        = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    admin_username  = Column(String(50),   nullable=True)   # denormalised for display after user deletion
    action          = Column(
        SAEnum("create", "update", "delete", "role_change", "upload", "reorder"),
        nullable=False,
    )
    resource_type   = Column(String(50),  nullable=False)
    resource_id     = Column(String(100), nullable=True)    # NULL for bulk actions (reorder)
    detail          = Column(Text,        nullable=True)    # JSON
    ip_address      = Column(String(45),  nullable=True)
    performed_at    = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))


class PasswordResetTokenORM(Base):
    __tablename__ = "password_reset_tokens"
    id         = Column(Integer, primary_key=True, autoincrement=True)
    user_id    = Column(Integer, nullable=False)
    token_hash = Column(String(64), nullable=False, unique=True)
    expires_at = Column(DateTime, nullable=False)
    used       = Column(SmallInteger, nullable=False, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class EmailVerificationTokenORM(Base):
    __tablename__ = "email_verification_tokens"
    id         = Column(Integer, primary_key=True, autoincrement=True)
    user_id    = Column(Integer, nullable=False)
    token_hash = Column(String(64), nullable=False, unique=True)
    expires_at = Column(DateTime, nullable=False)
    used       = Column(SmallInteger, nullable=False, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class MindmapLensProgressORM(Base):
    __tablename__ = "mindmap_lens_progress"
    id          = Column(Integer, primary_key=True, autoincrement=True)
    user_id     = Column(Integer, nullable=False)
    lens_id     = Column(String(100), nullable=False)
    explored_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class SubmissionORM(Base):
    __tablename__ = "submissions"
    id            = Column(Integer, primary_key=True, autoincrement=True)
    user_id       = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    session_token = Column(String(64), nullable=False)
    input_type    = Column(SAEnum("text", "url", "image", "pdf", "file"), default="text")
    raw_content   = Column(Text, nullable=False)
    parsed_text   = Column(Text, nullable=True)
    status        = Column(SAEnum("pending", "analyzed", "complete"), default="pending")
    created_at    = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    # ORM relationships
    lessons_triggered  = relationship("LessonsTriggeredORM", back_populates="submission", cascade="all, delete-orphan")
    reflections        = relationship("UserReflectionORM",  back_populates="submission", cascade="all, delete-orphan")
    confidence_snapshots = relationship("ConfidenceSnapshotORM", back_populates="submission", cascade="all, delete-orphan")


# ── NEW v5.0: Reasoning Journal ───────────────────────────────────────────────

class UserReflectionORM(Base):
    """
    Stores per-stage Reasoning Journal entries.

    `stage` identifies where in the pipeline the reflection was captured:
      'post_eval'       — after the user completes the 8 evaluation steps
      'post_evidence'   — after reviewing retrieved articles
      'post_verdict'    — after the user submits their final verdict

    `bloom_level` is an integer 1–5 assigned by the frontend based on prompt:
      1 = recall ("What did you notice?")
      2 = understanding ("Why does that matter?")
      3 = application ("What step would you check first?")
      4 = analysis ("What technique did you spot?")
      5 = evaluation ("Which source was more useful and why?")

    This is the primary qualitative data source for:
      - Bloom's Taxonomy L4–5 analysis
      - Kirkpatrick Level 3 (behavior change evidence)
      - Metacognitive calibration research
    """
    __tablename__     = "user_reflections"
    id                = Column(Integer, primary_key=True, autoincrement=True)
    submission_id     = Column(Integer, ForeignKey("submissions.id", ondelete="SET NULL"), nullable=True)    # FK → submissions.id (nullable for anon)
    user_id           = Column(Integer, nullable=True)    # NULL for anonymous sessions
    session_token     = Column(String(64), nullable=False)
    stage             = Column(
        SAEnum("post_eval", "post_evidence", "post_verdict"),
        nullable=False,
        default="post_verdict",
    )
    # The three Bloom's L4–5 reflection prompts
    what_noticed      = Column(Text, nullable=True)       # "What did I notice about these articles?"
    still_uncertain   = Column(Text, nullable=True)       # "What am I still uncertain about?"
    would_check_next  = Column(Text, nullable=True)       # "What would I check next if this mattered to me?"
    # Free-form reasoning (maps to existing r6-reasoning-input)
    free_reasoning    = Column(Text, nullable=True)
    # Position taken by user (supported / unsupported / uncertain)
    verdict_position  = Column(String(20), nullable=True)
    # Bloom's level of the deepest prompt answered (1–5)
    bloom_level       = Column(Integer, nullable=True)
    # Word count across all prompts — proxy for reflection depth
    total_word_count  = Column(Integer, nullable=True)
    submitted_at      = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    # ORM relationship
    submission = relationship("SubmissionORM", back_populates="reflections")


# ── NEW v5.0: Confidence Snapshots (Confidence Before vs After) ───────────────

class ConfidenceSnapshotORM(Base):
    """
    Captures the user's confidence rating before and after seeing evidence.
    Used to measure:
      - Belief-updating (openness to revising opinion)
      - Metacognitive calibration (Dunning-Kruger detection)
      - MIL attitude shift as a measurable outcome

    confidence_before: captured at Step 1 (before retrieval runs)
    confidence_after:  captured at Step 8 / post-evidence stage
    Scale: 1 (not at all confident) → 5 (extremely confident)
    """
    __tablename__       = "confidence_snapshots"
    id                  = Column(Integer, primary_key=True, autoincrement=True)
    submission_id       = Column(Integer, ForeignKey("submissions.id", ondelete="SET NULL"), nullable=True)
    user_id             = Column(Integer, nullable=True)
    session_token       = Column(String(64), nullable=False)
    confidence_before   = Column(Integer, nullable=True)   # 1–5
    confidence_after    = Column(Integer, nullable=True)   # 1–5
    # Derived: positive = updated belief, 0 = unchanged, negative = became more confident (possible Dunning-Kruger)
    confidence_delta    = Column(Integer, nullable=True)
    # Whether the system flagged a calibration gap at submit time
    calibration_flag    = Column(SmallInteger, nullable=False, default=0)
    # The user's confidence_level string from the eval ("high"/"medium"/"low") — kept for compat
    confidence_label    = Column(String(10), nullable=True)
    recorded_at         = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    # ORM relationship
    submission = relationship("SubmissionORM", back_populates="confidence_snapshots")


# ── NEW v5.0: Source Diversity Log ────────────────────────────────────────────

class SourceDiversityLogORM(Base):
    """
    Records the source-type composition of each retrieval result.
    Enables UNESCO MIL alignment analysis — did the user see a diverse
    information ecosystem, or a monoculture of one source type?

    Counts are extracted from retrieved articles by inspect_source_diversity()
    in pipeline/orchestrator.py before returning the response.
    """
    __tablename__      = "source_diversity_log"
    id                 = Column(Integer, primary_key=True, autoincrement=True)
    submission_id      = Column(Integer, nullable=True)
    session_token      = Column(String(64), nullable=False)
    total_articles     = Column(Integer, nullable=False, default=0)
    count_government   = Column(Integer, nullable=False, default=0)
    count_academic     = Column(Integer, nullable=False, default=0)
    count_news         = Column(Integer, nullable=False, default=0)
    count_factcheck    = Column(Integer, nullable=False, default=0)
    count_international= Column(Integer, nullable=False, default=0)
    count_other        = Column(Integer, nullable=False, default=0)
    # Diversity score 0.0–1.0: higher = more spread across categories
    diversity_score    = Column(Float, nullable=True)
    logged_at          = Column(DateTime, default=lambda: datetime.now(timezone.utc))


# ── v9.0: Dynamic lesson topics ──────────────────────────────────────────────

class LessonTopicORM(Base):
    """
    Admin-manageable topic registry. Replaces the hard-coded ENUM on lessons/quiz.
    Each row defines a topic key, display label, emoji icon, and a hue (0-359)
    used to auto-generate consistent HSL colours everywhere in the UI.
    """
    __tablename__ = "lesson_topics"
    id         = Column(Integer, primary_key=True, autoincrement=True)
    key        = Column(String(60),  nullable=False, unique=True)
    label      = Column(String(100), nullable=False)
    icon       = Column(String(10),  nullable=False, default="📄")
    color_hue  = Column(Integer,     nullable=False, default=220)
    sort_order = Column(Integer,     nullable=False, default=0)
    quiz_limit = Column(Integer,     nullable=True)   # max questions shown per session; NULL = no limit
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


# ── NEW v5.0: Per-Skill History (sparkline data) ──────────────────────────────

class UserSkillHistoryORM(Base):
    """
    Append-only log of skill level changes per user per topic.
    One row is inserted every time user_skill_progress.current_level
    advances (or regresses) for a topic.

    Used by the dashboard to render a per-skill progress sparkline
    instead of just the current level — enabling the thesis claim:
    "Users improved specifically in source verification but struggled
    with logical analysis."
    """
    __tablename__   = "user_skill_history"
    id              = Column(Integer, primary_key=True, autoincrement=True)
    user_id         = Column(Integer, nullable=True)
    session_token   = Column(String(64), nullable=True)
    topic           = Column(String(60), nullable=False)
    level_from      = Column(SAEnum("beginner", "intermediate", "advanced"), nullable=True)
    level_to        = Column(SAEnum("beginner", "intermediate", "advanced"), nullable=False)
    quiz_accuracy   = Column(Float, nullable=True)   # accuracy at the time of this change
    trigger_event   = Column(String(50), nullable=True)  # "quiz_pass" | "lesson_complete" | "manual"
    changed_at      = Column(DateTime, default=lambda: datetime.now(timezone.utc))


# ── Admin / eval tables ───────────────────────────────────────────────────────

class EvalQuestionORM(Base):
    """
    Onboarding evaluation questions shown to new users before lessons are
    assigned. Managed entirely via admin.py endpoints.

    skip_lesson_id: if set, users who answer correctly skip that lesson.
    mindmap_node_id: if set, completing this eval step unlocks the linked mindmap node.
    """
    __tablename__ = "eval_questions"
    id              = Column(Integer, primary_key=True, autoincrement=True)
    step_number     = Column(Integer, nullable=False, default=1)
    title           = Column(String(255), nullable=False)
    step_label      = Column(String(80), nullable=True)    # short label shown in progress bar
    prompt          = Column(Text, nullable=False)
    hint            = Column(Text, nullable=True)
    input_type      = Column(String(32), nullable=False, default="text")
    options         = Column(Text, nullable=True)          # JSON string of choices
    is_active       = Column(SmallInteger, nullable=False, default=1)
    skip_lesson_id  = Column(Integer, nullable=True)       # FK → lessons.id
    step_link_type  = Column(String(32), nullable=True)    # url|lesson|quiz|mindmap|dashboard
    step_link_value = Column(String(512), nullable=True)   # key/id/url depending on type
    mindmap_node_id = Column(String(64), nullable=True)    # FK → mindmap_nodes.id; unlocks node on completion
    created_at      = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at      = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                             onupdate=lambda: datetime.now(timezone.utc))


class LessonORM(Base):
    __tablename__          = "lessons"
    id                     = Column(Integer, primary_key=True, autoincrement=True)
    lesson_key             = Column(String(100), nullable=False, unique=True)
    title                  = Column(String(255), nullable=False)
    content                = Column(Text, nullable=False)
    topic                  = Column(
        SAEnum("claim_detection", "source_verification", "bias_detection",
               "evidence_evaluation", "general"),
        nullable=False,
    )
    difficulty             = Column(
        SAEnum("beginner", "intermediate", "advanced"),
        nullable=False, default="beginner",
    )
    image_url              = Column(String(512),  nullable=True)
    mil_skill              = Column(String(50),   nullable=True)
    sort_order             = Column(Integer,       nullable=True)
    prerequisite_lesson_id = Column(Integer,       nullable=True)
    is_published           = Column(SmallInteger, nullable=False, default=1)
    mindmap_node_id        = Column(String(64),   nullable=True)  # FK → mindmap_nodes.id; unlocks node on completion
    created_at             = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at             = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class LessonsTriggeredORM(Base):
    __tablename__  = "lessons_triggered"
    id             = Column(Integer, primary_key=True, autoincrement=True)
    submission_id  = Column(Integer, ForeignKey("submissions.id", ondelete="CASCADE"), nullable=False)
    lesson_id      = Column(Integer, ForeignKey("lessons.id",     ondelete="CASCADE"), nullable=False)
    trigger_reason = Column(String(255), nullable=True)
    was_read       = Column(SmallInteger, nullable=False, default=0)
    triggered_at   = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    # ORM relationships
    submission = relationship("SubmissionORM", back_populates="lessons_triggered")
    lesson     = relationship("LessonORM")


class TopicQuizLinkORM(Base):
    """
    Explicit mapping of which quiz questions belong to a topic.
    When rows exist for a topic key, they override the default
    auto-match (questions whose .topic field == topic key).
    Managed via the admin Topics modal.
    """
    __tablename__ = "topic_quiz_links"
    id          = Column(Integer, primary_key=True, autoincrement=True)
    topic_key   = Column(String(60), nullable=False, index=True)
    question_id = Column(Integer,    nullable=False)


class QuizQuestionORM(Base):
    __tablename__ = "quiz_questions"
    id            = Column(Integer, primary_key=True, autoincrement=True)
    lesson_id     = Column(Integer, nullable=True)
    question_text = Column(Text, nullable=False)
    options       = Column(sa.JSON, nullable=False)
    correct_index = Column(Integer, nullable=False)
    explanation   = Column(Text, nullable=True)
    topic         = Column(String(40), nullable=False)
    difficulty    = Column(
        SAEnum("beginner", "intermediate", "advanced"),
        nullable=True, default="beginner",
    )
    hint          = Column(Text, nullable=True)
    image_url     = Column(String(512), nullable=True)
    video_url     = Column(String(1024), nullable=True)
    media_type    = Column(SAEnum("text", "image", "video", "file"), nullable=True, default="text")
    mindmap_node_id = Column(String(64), nullable=True)  # FK → mindmap_nodes.id; unlocks node on correct answer


class QuizAttemptORM(Base):
    __tablename__  = "quiz_attempts"
    id             = Column(Integer, primary_key=True, autoincrement=True)
    user_id        = Column(Integer, nullable=True)
    question_id    = Column(Integer, nullable=False)
    selected_index = Column(Integer, nullable=False)
    is_correct     = Column(SmallInteger, nullable=False)
    attempted_at   = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class UserSkillProgressORM(Base):
    """Per-user MIL skill level per topic. Updated after quiz attempts and lesson reads."""
    __tablename__     = "user_skill_progress"
    id                = Column(Integer, primary_key=True, autoincrement=True)
    user_id           = Column(Integer, nullable=True)
    session_token     = Column(String(64), nullable=True)
    topic             = Column(String(60), nullable=False)
    current_level     = Column(
        SAEnum("beginner", "intermediate", "advanced"),
        nullable=False, default="beginner",
    )
    quiz_accuracy_pct = Column(Float, nullable=True)
    lessons_completed = Column(Integer, nullable=False, default=0)
    last_updated      = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class PretestResultORM(Base):
    """Pre/post test results."""
    __tablename__ = "pretest_results"
    id            = Column(Integer, primary_key=True, autoincrement=True)
    user_id       = Column(Integer, nullable=True)
    session_token = Column(String(64), nullable=True)
    phase         = Column(SAEnum("pretest", "posttest"), nullable=False)
    score_pct     = Column(Integer, nullable=False)
    correct       = Column(Integer, nullable=False)
    total         = Column(Integer, nullable=False)
    submitted_at  = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class LessonCompletionORM(Base):
    """Per-user lesson completion tracking."""
    __tablename__  = "lesson_completions"
    id             = Column(Integer, primary_key=True, autoincrement=True)
    user_id        = Column(Integer, nullable=True)
    session_token  = Column(String(64), nullable=True)
    lesson_id      = Column(Integer, nullable=False)
    completed_at   = Column(DateTime, default=lambda: datetime.now(timezone.utc))


# ── v3 tables (unchanged) ─────────────────────────────────────────────────────

class MBFCDomainORM(Base):
    __tablename__      = "mbfc_domains"
    id                 = Column(Integer, primary_key=True, autoincrement=True)
    domain             = Column(String(255), nullable=False, unique=True)
    factual_reporting  = Column(String(50), nullable=True)
    bias_rating        = Column(String(50), nullable=True)
    credibility_rating = Column(String(50), nullable=True)
    country            = Column(String(10), nullable=True)
    notes_url          = Column(String(500), nullable=True)
    last_synced        = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class URLTrackingORM(Base):
    __tablename__  = "url_tracking"
    id             = Column(Integer, primary_key=True, autoincrement=True)
    url            = Column(String(2083), nullable=False)
    url_hash       = Column(String(64),   nullable=False)
    domain         = Column(String(255),  nullable=True)
    submitted_by   = Column(Integer,      nullable=True)
    session_token  = Column(String(64),   nullable=True)
    submission_id  = Column(Integer,      nullable=True)
    submitted_at   = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class UserBehaviorProfileORM(Base):
    __tablename__            = "user_behavior_profile"
    id                       = Column(Integer, primary_key=True, autoincrement=True)
    user_id                  = Column(Integer, nullable=True)
    session_token            = Column(String(64), nullable=True)
    claim_detection_score    = Column(Float, default=50)
    source_eval_score        = Column(Float, default=50)
    bias_detection_score     = Column(Float, default=50)
    evidence_eval_score      = Column(Float, default=50)
    avg_time_per_step_seconds = Column(Float, nullable=True)
    steps_skipped_rate       = Column(Float, default=0)
    lesson_read_rate         = Column(Float, default=0)
    total_submissions        = Column(Integer, nullable=False, default=0)
    total_lessons_read       = Column(Integer, nullable=False, default=0)
    last_activity_at         = Column(DateTime, nullable=True)
    updated_at               = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class MindmapNodeORM(Base):
    __tablename__ = "mindmap_nodes"
    id            = Column(String(64),  primary_key=True)
    map_id        = Column(String(32),  primary_key=True, default="main")
    type          = Column(sa.Enum("root", "cat", "leaf"), nullable=False, default="leaf")
    icon          = Column(String(16),  nullable=False, default="📌")
    label         = Column(String(120), nullable=False)
    sub           = Column(String(120), nullable=True)
    color         = Column(String(10),  nullable=False, default="#4488ff")
    x             = Column(Integer,     nullable=False, default=1800)
    y             = Column(Integer,     nullable=False, default=1500)
    start_visible = Column(sa.Boolean,  nullable=False, default=False)
    sort_order    = Column(Integer,     nullable=False, default=0)
    active        = Column(sa.Boolean,  nullable=False, default=True)
    created_at    = Column(DateTime,    default=lambda: datetime.now(timezone.utc))
    updated_at    = Column(DateTime,    default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class MindmapEdgeORM(Base):
    __tablename__ = "mindmap_edges"
    id       = Column(Integer,    primary_key=True, autoincrement=True)
    map_id   = Column(String(32), nullable=False, default="main")
    from_id  = Column(String(64), nullable=False)
    to_id    = Column(String(64), nullable=False)


class MindmapInteractionORM(Base):
    __tablename__ = "mindmap_interactions"
    node_id     = Column(String(64),  primary_key=True)
    map_id      = Column(String(32),  primary_key=True, default="main")
    icon        = Column(String(16),  nullable=False, default="📌")
    title       = Column(String(120), nullable=False)
    context     = Column(sa.Text,     nullable=True)
    widget_type = Column(sa.Enum("choice", "slider", "tap", "bots", "none"), nullable=False, default="choice")
    widget_json = Column(sa.JSON,     nullable=True)
    aftermath   = Column(sa.Text,     nullable=True)
    media_type  = Column(sa.Enum("image", "youtube"), nullable=True)
    media_url   = Column(String(512), nullable=True)
    media_thumb = Column(String(512), nullable=True)
    updated_at  = Column(DateTime,    default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class MindmapProgressORM(Base):
    __tablename__ = "mindmap_progress"
    id         = Column(Integer,    primary_key=True, autoincrement=True)
    user_id    = Column(Integer,    nullable=False)
    map_id     = Column(String(32), nullable=False, default="main")
    node_id    = Column(String(64), nullable=False)
    viewed_at  = Column(DateTime,   default=lambda: datetime.now(timezone.utc))


class MindmapSuggestionORM(Base):
    __tablename__ = "mindmap_suggestions"
    id              = Column(Integer,    primary_key=True, autoincrement=True)
    user_id         = Column(Integer,    nullable=True)
    map_id          = Column(String(32), nullable=False, default="main")
    label           = Column(String(120), nullable=False)
    reason          = Column(sa.Text,    nullable=True)
    connect_from_id = Column(String(64), nullable=True)
    status          = Column(sa.Enum("pending", "approved", "rejected"), nullable=False, default="pending")
    admin_note      = Column(String(255), nullable=True)
    submitted_at    = Column(DateTime,   default=lambda: datetime.now(timezone.utc))
    reviewed_at     = Column(DateTime,   nullable=True)


class EvalQuestionBranchORM(Base):
    """
    Branching follow-up prompts shown to users based on their answer to an
    eval question. Managed via admin.py endpoints.

    trigger_condition: 'equals' | 'includes' | 'skipped'
    trigger_value:     the answer string that fires this branch (empty for 'skipped')
    followup_type:     'hint' = informational nudge; 'block' = stops progression
    lesson_id:         optional lesson to surface alongside the follow-up
    """
    __tablename__     = "eval_question_branches"
    id                = Column(Integer, primary_key=True, autoincrement=True)
    question_id       = Column(Integer, ForeignKey("eval_questions.id", ondelete="CASCADE"), nullable=False)
    parent_branch_id  = Column(Integer, ForeignKey("eval_question_branches.id", ondelete="CASCADE"), nullable=True)
    trigger_condition = Column(SAEnum("equals", "includes", "skipped", "any"), nullable=False, default="equals")
    trigger_value     = Column(String(200), nullable=False, default="")
    followup_prompt   = Column(Text, nullable=False)
    followup_type     = Column(SAEnum("hint", "block"), nullable=False, default="hint")
    input_type        = Column(String(32), nullable=True, default="none")
    options           = Column(Text, nullable=True)
    scale_min_label   = Column(String(100), nullable=True)
    scale_max_label   = Column(String(100), nullable=True)
    lesson_id         = Column(Integer, nullable=True)
    content_type      = Column(String(32), nullable=True)
    quiz_question_id  = Column(Integer, nullable=True)
    content_url       = Column(String(512), nullable=True)
    image_url         = Column(String(512), nullable=True)
    file_url          = Column(String(512), nullable=True)
    file_name         = Column(String(255), nullable=True)
    link_label        = Column(String(255), nullable=True)
    is_active         = Column(SmallInteger, nullable=False, default=1)
    sort_order        = Column(Integer, nullable=False, default=0)
    created_at        = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at        = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                               onupdate=lambda: datetime.now(timezone.utc))

    question        = relationship("EvalQuestionORM", backref="branches")
    nested_branches = relationship(
        "EvalQuestionBranchORM",
        foreign_keys="EvalQuestionBranchORM.parent_branch_id",
        order_by="EvalQuestionBranchORM.sort_order",
        lazy="selectin",
    )


class PretestClaimORM(Base):
    """
    Managed pool of questions used in the pre- and post-test.
    Supports multiple question types: true_false, multiple_choice, yes_no, scale, open.

    question_type:  'true_false' | 'multiple_choice' | 'yes_no' | 'scale' | 'open'
    correct_answer: human-readable correct answer text (null for scale/open)
    options:        JSON array of option strings for multiple_choice
    correct_index:  0-based index into options for multiple_choice; 1=True/0=False for true_false
    attempt_count:  running tally incremented by quiz.py on each attempt
    """
    __tablename__   = "pretest_claims"
    id              = Column(Integer, primary_key=True, autoincrement=True)
    text            = Column(Text, nullable=False)
    question_type   = Column(String(32), nullable=False, default="true_false")
    correct_answer  = Column(String(255), nullable=True, default="True")
    options         = Column(Text, nullable=True)
    correct_index   = Column(SmallInteger, nullable=False, default=0)
    sort_order      = Column(Integer, nullable=False, default=0)
    is_active       = Column(SmallInteger, nullable=False, default=1)
    attempt_count   = Column(Integer, nullable=False, default=0)
    created_at      = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at      = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                             onupdate=lambda: datetime.now(timezone.utc))


class UserCreatedContentORM(Base):
    """
    User-authored corrective summaries, reflection posts, and cohort-shared
    posts. Created at Bloom's Level 6 (Create) in the analysis flow.

    content_type: 'corrective_summary' | 'reflection_post' | 'cohort_share'
    is_shared:    1 = opted in to cohort sharing for research analysis
    bloom_level:  always 6 for this table (Create-level task)
    """
    __tablename__  = "user_created_content"
    id             = Column(Integer, primary_key=True, autoincrement=True)
    user_id        = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    session_token  = Column(String(64), nullable=False)
    submission_id  = Column(Integer, nullable=True)
    content_type   = Column(
        SAEnum("corrective_summary", "reflection_post", "cohort_share"),
        nullable=False, default="corrective_summary",
    )
    body           = Column(Text, nullable=False)
    is_shared      = Column(SmallInteger, nullable=False, default=0)
    bloom_level    = Column(SmallInteger, nullable=False, default=6)
    created_at     = Column(DateTime, default=lambda: datetime.now(timezone.utc))


def init_mysql_schema():
    """
    Run safe migrations on startup. Every statement is idempotent.
    v5.0: adds user_reflections, confidence_snapshots, source_diversity_log,
    user_skill_history tables and their column-level safe migrations.
    """

    new_tables_sql = [
        # ── original tables (unchanged) ───────────────────────────────────────
        """CREATE TABLE IF NOT EXISTS submissions (
            id            INT UNSIGNED NOT NULL AUTO_INCREMENT,
            user_id       INT UNSIGNED NULL,
            session_token VARCHAR(64)  NOT NULL,
            input_type    ENUM('text','url','image','pdf','file') NOT NULL DEFAULT 'text',
            raw_content   TEXT         NOT NULL,
            parsed_text   TEXT         NULL,
            status        ENUM('pending','analyzed','complete') NOT NULL DEFAULT 'pending',
            created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`),
            KEY `idx_submissions_user`   (`user_id`),
            KEY `idx_submissions_status` (`status`),
            CONSTRAINT `submissions_ibfk_1`
                FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE SET NULL
        )""",

        """CREATE TABLE IF NOT EXISTS lessons (
            id                     INT UNSIGNED NOT NULL AUTO_INCREMENT,
            lesson_key             VARCHAR(100) NOT NULL,
            title                  VARCHAR(255) NOT NULL,
            content                TEXT         NOT NULL,
            topic                  ENUM('claim_detection','source_verification',
                                        'bias_detection','evidence_evaluation','general')
                                       NOT NULL,
            difficulty             ENUM('beginner','intermediate','advanced')
                                       NOT NULL DEFAULT 'beginner',
            mil_skill              VARCHAR(50)  NULL,
            sort_order             INT          NULL,
            prerequisite_lesson_id INT          NULL,
            is_published           TINYINT(1)   NOT NULL DEFAULT 1,
            created_at             DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at             DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
                                       ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`),
            UNIQUE KEY `lesson_key` (`lesson_key`)
        )""",

        """CREATE TABLE IF NOT EXISTS lessons_triggered (
            id             INT UNSIGNED NOT NULL AUTO_INCREMENT,
            submission_id  INT UNSIGNED NOT NULL,
            lesson_id      INT UNSIGNED NOT NULL,
            trigger_reason VARCHAR(255) NULL,
            was_read       TINYINT(1)   NOT NULL DEFAULT 0,
            triggered_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`),
            KEY `submission_id` (`submission_id`),
            KEY `lesson_id`     (`lesson_id`),
            CONSTRAINT `lessons_triggered_ibfk_1`
                FOREIGN KEY (`submission_id`) REFERENCES `submissions` (`id`) ON DELETE CASCADE,
            CONSTRAINT `lessons_triggered_ibfk_2`
                FOREIGN KEY (`lesson_id`) REFERENCES `lessons` (`id`) ON DELETE CASCADE
        )""",

        """CREATE TABLE IF NOT EXISTS user_skill_progress (
            id                INT AUTO_INCREMENT PRIMARY KEY,
            user_id           INT NULL,
            session_token     VARCHAR(64) NULL,
            topic             ENUM('claim_detection','source_verification','bias_detection',
                                   'evidence_evaluation','general') NOT NULL,
            current_level     ENUM('beginner','intermediate','advanced')
                                  NOT NULL DEFAULT 'beginner',
            quiz_accuracy_pct FLOAT NULL,
            lessons_completed INT NOT NULL DEFAULT 0,
            last_updated      DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            INDEX idx_usp_user  (user_id),
            INDEX idx_usp_topic (topic)
        )""",

        """CREATE TABLE IF NOT EXISTS pretest_results (
            id            INT AUTO_INCREMENT PRIMARY KEY,
            user_id       INT NULL,
            session_token VARCHAR(64) NULL,
            phase         ENUM('pretest','posttest') NOT NULL,
            score_pct     INT NOT NULL,
            correct       INT NOT NULL,
            total         INT NOT NULL,
            submitted_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_pr_user    (user_id),
            INDEX idx_pr_session (session_token)
        )""",

        """CREATE TABLE IF NOT EXISTS lesson_completions (
            id            INT AUTO_INCREMENT PRIMARY KEY,
            user_id       INT NULL,
            session_token VARCHAR(64) NULL,
            lesson_id     INT NOT NULL,
            completed_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_lc_user   (user_id),
            INDEX idx_lc_lesson (lesson_id)
        )""",

        """CREATE TABLE IF NOT EXISTS mbfc_domains (
            id                 INT AUTO_INCREMENT PRIMARY KEY,
            domain             VARCHAR(255) NOT NULL UNIQUE,
            factual_reporting  VARCHAR(50)  NULL,
            bias_rating        VARCHAR(50)  NULL,
            credibility_rating VARCHAR(50)  NULL,
            country            VARCHAR(10)  NULL,
            notes_url          VARCHAR(500) NULL,
            last_synced        DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            INDEX idx_mbfc_domain (domain)
        )""",

        """CREATE TABLE IF NOT EXISTS url_tracking (
            id            INT AUTO_INCREMENT PRIMARY KEY,
            url           VARCHAR(2083) NOT NULL,
            url_hash      VARCHAR(64)   NOT NULL,
            domain        VARCHAR(255)  NULL,
            submitted_by  INT           NULL,
            session_token VARCHAR(64)   NULL,
            submission_id INT           NULL,
            submitted_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY `uq_url_hash_session` (`url_hash`, `session_token`),
            INDEX idx_ut_hash       (url_hash),
            INDEX idx_ut_domain     (domain),
            INDEX idx_ut_user       (submitted_by),
            INDEX idx_ut_submission (submission_id)
        )""",
        """CREATE TABLE IF NOT EXISTS user_behavior_profile (
            id                        INT AUTO_INCREMENT PRIMARY KEY,
            user_id                   INT          NULL,
            session_token             VARCHAR(64)  NULL,
            claim_detection_score     FLOAT        DEFAULT 50,
            source_eval_score         FLOAT        DEFAULT 50,
            bias_detection_score      FLOAT        DEFAULT 50,
            evidence_eval_score       FLOAT        DEFAULT 50,
            avg_time_per_step_seconds FLOAT        NULL,
            steps_skipped_rate        FLOAT        DEFAULT 0,
            lesson_read_rate          FLOAT        DEFAULT 0,
            total_submissions         INT NOT NULL DEFAULT 0,
            total_lessons_read        INT NOT NULL DEFAULT 0,
            last_activity_at          DATETIME     NULL,
            updated_at                DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uq_ubp_user    (user_id),
            UNIQUE KEY uq_ubp_session (session_token),
            INDEX idx_ubp_user        (user_id),
            INDEX idx_ubp_session     (session_token)
        )""",

        """CREATE TABLE IF NOT EXISTS password_reset_tokens (
            id         INT          NOT NULL AUTO_INCREMENT,
            user_id    INT UNSIGNED NOT NULL,
            token_hash VARCHAR(64)  NOT NULL UNIQUE,
            expires_at DATETIME     NOT NULL,
            used       TINYINT(1)   NOT NULL DEFAULT 0,
            created_at DATETIME     DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (id),
            INDEX idx_prt_user   (user_id),
            INDEX idx_prt_hash   (token_hash),
            INDEX idx_prt_expiry (expires_at)
        )""",

        """CREATE TABLE IF NOT EXISTS email_verification_tokens (
            id         INT          NOT NULL AUTO_INCREMENT,
            user_id    INT UNSIGNED NOT NULL,
            token_hash VARCHAR(64)  NOT NULL UNIQUE,
            expires_at DATETIME     NOT NULL,
            used       TINYINT(1)   NOT NULL DEFAULT 0,
            created_at DATETIME     DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (id),
            INDEX idx_evt_user   (user_id),
            INDEX idx_evt_hash   (token_hash),
            INDEX idx_evt_expiry (expires_at)
        )""",

        """CREATE TABLE IF NOT EXISTS mindmap_lens_progress (
            id          INT          NOT NULL AUTO_INCREMENT,
            user_id     INT UNSIGNED NOT NULL,
            lens_id     VARCHAR(100) NOT NULL,
            explored_at DATETIME     DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (id),
            UNIQUE KEY  uq_mlp_user_lens (user_id, lens_id),
            INDEX idx_mlp_user (user_id)
        )""",

        """CREATE TABLE IF NOT EXISTS mindmap_nodes (
            id            VARCHAR(64)  NOT NULL,
            map_id        VARCHAR(32)  NOT NULL DEFAULT 'main',
            type          ENUM('root','cat','leaf') NOT NULL DEFAULT 'leaf',
            icon          VARCHAR(16)  NOT NULL DEFAULT '📌',
            label         VARCHAR(120) NOT NULL,
            sub           VARCHAR(120) DEFAULT NULL,
            color         VARCHAR(10)  NOT NULL DEFAULT '#4488ff',
            x             INT          NOT NULL DEFAULT 1800,
            y             INT          NOT NULL DEFAULT 1500,
            start_visible TINYINT(1)   NOT NULL DEFAULT 0,
            sort_order    INT          NOT NULL DEFAULT 0,
            active        TINYINT(1)   NOT NULL DEFAULT 1,
            created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (id, map_id)
        )""",

        """CREATE TABLE IF NOT EXISTS mindmap_edges (
            id       INT UNSIGNED NOT NULL AUTO_INCREMENT,
            map_id   VARCHAR(32)  NOT NULL DEFAULT 'main',
            from_id  VARCHAR(64)  NOT NULL,
            to_id    VARCHAR(64)  NOT NULL,
            PRIMARY KEY (id),
            UNIQUE KEY uq_edge (map_id, from_id, to_id),
            KEY idx_edge_from (from_id),
            KEY idx_edge_to   (to_id)
        )""",

        """CREATE TABLE IF NOT EXISTS mindmap_interactions (
            node_id     VARCHAR(64)  NOT NULL,
            map_id      VARCHAR(32)  NOT NULL DEFAULT 'main',
            icon        VARCHAR(16)  NOT NULL DEFAULT '📌',
            title       VARCHAR(120) NOT NULL,
            context     TEXT         DEFAULT NULL,
            widget_type ENUM('choice','slider','tap','bots','none') NOT NULL DEFAULT 'choice',
            widget_json JSON         DEFAULT NULL,
            aftermath   TEXT         DEFAULT NULL,
            media_type  ENUM('image','youtube') DEFAULT NULL,
            media_url   VARCHAR(512) DEFAULT NULL,
            media_thumb VARCHAR(512) DEFAULT NULL,
            updated_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (node_id, map_id)
        )""",

        """CREATE TABLE IF NOT EXISTS mindmap_progress (
            id        INT UNSIGNED NOT NULL AUTO_INCREMENT,
            user_id   INT UNSIGNED NOT NULL,
            map_id    VARCHAR(32)  NOT NULL DEFAULT 'main',
            node_id   VARCHAR(64)  NOT NULL,
            viewed_at DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (id),
            UNIQUE KEY uq_progress (user_id, map_id, node_id),
            KEY idx_mp_user (user_id),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )""",

        """CREATE TABLE IF NOT EXISTS mindmap_suggestions (
            id              INT UNSIGNED NOT NULL AUTO_INCREMENT,
            user_id         INT UNSIGNED DEFAULT NULL,
            map_id          VARCHAR(32)  NOT NULL DEFAULT 'main',
            label           VARCHAR(120) NOT NULL,
            reason          TEXT         DEFAULT NULL,
            connect_from_id VARCHAR(64)  DEFAULT NULL,
            status          ENUM('pending','approved','rejected') NOT NULL DEFAULT 'pending',
            admin_note      VARCHAR(255) DEFAULT NULL,
            submitted_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
            reviewed_at     DATETIME     DEFAULT NULL,
            PRIMARY KEY (id),
            KEY idx_ms_status (status),
            KEY idx_ms_user   (user_id)
        )""",

        # ── NEW v5.0 tables ───────────────────────────────────────────────────

        """CREATE TABLE IF NOT EXISTS user_reflections (
            id               INT UNSIGNED NOT NULL AUTO_INCREMENT,
            submission_id    INT UNSIGNED NULL,
            user_id          INT UNSIGNED NULL,
            session_token    VARCHAR(64)  NOT NULL,
            stage            ENUM('post_eval','post_evidence','post_verdict')
                                 NOT NULL DEFAULT 'post_verdict',
            what_noticed     TEXT         NULL,
            still_uncertain  TEXT         NULL,
            would_check_next TEXT         NULL,
            free_reasoning   TEXT         NULL,
            verdict_position VARCHAR(20)  NULL,
            bloom_level      TINYINT      NULL,
            total_word_count INT          NULL,
            submitted_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (id),
            KEY idx_ur_submission (submission_id),
            KEY idx_ur_user       (user_id),
            KEY idx_ur_session    (session_token),
            KEY idx_ur_stage      (stage)
        )""",

        """CREATE TABLE IF NOT EXISTS confidence_snapshots (
            id                INT UNSIGNED NOT NULL AUTO_INCREMENT,
            submission_id     INT UNSIGNED NULL,
            user_id           INT UNSIGNED NULL,
            session_token     VARCHAR(64)  NOT NULL,
            confidence_before TINYINT      NULL,
            confidence_after  TINYINT      NULL,
            confidence_delta  TINYINT      NULL,
            calibration_flag  TINYINT(1)   NOT NULL DEFAULT 0,
            confidence_label  VARCHAR(10)  NULL,
            recorded_at       DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (id),
            KEY idx_cs_submission (submission_id),
            KEY idx_cs_user       (user_id),
            KEY idx_cs_session    (session_token)
        )""",

        """CREATE TABLE IF NOT EXISTS source_diversity_log (
            id                  INT UNSIGNED NOT NULL AUTO_INCREMENT,
            submission_id       INT UNSIGNED NULL,
            session_token       VARCHAR(64)  NOT NULL,
            total_articles      INT          NOT NULL DEFAULT 0,
            count_government    INT          NOT NULL DEFAULT 0,
            count_academic      INT          NOT NULL DEFAULT 0,
            count_news          INT          NOT NULL DEFAULT 0,
            count_factcheck     INT          NOT NULL DEFAULT 0,
            count_international INT          NOT NULL DEFAULT 0,
            count_other         INT          NOT NULL DEFAULT 0,
            diversity_score     FLOAT        NULL,
            logged_at           DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (id),
            KEY idx_sdl_submission (submission_id),
            KEY idx_sdl_session    (session_token)
        )""",

        """CREATE TABLE IF NOT EXISTS user_skill_history (
            id            INT UNSIGNED NOT NULL AUTO_INCREMENT,
            user_id       INT          NULL,
            session_token VARCHAR(64)  NULL,
            topic         ENUM('claim_detection','source_verification','bias_detection',
                               'evidence_evaluation','general') NOT NULL,
            level_from    ENUM('beginner','intermediate','advanced') NULL,
            level_to      ENUM('beginner','intermediate','advanced') NOT NULL,
            quiz_accuracy FLOAT        NULL,
            trigger_event VARCHAR(50)  NULL,
            changed_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (id),
            KEY idx_ush_user    (user_id),
            KEY idx_ush_session (session_token),
            KEY idx_ush_topic   (topic)
        )""",

        # ── NEW v6.0 tables ───────────────────────────────────────────────────

        """CREATE TABLE IF NOT EXISTS eval_questions (
            id             INT          NOT NULL AUTO_INCREMENT,
            step_number    INT          NOT NULL DEFAULT 1,
            title          VARCHAR(255) NOT NULL,
            prompt         TEXT         NOT NULL,
            hint           TEXT         NULL,
            input_type     VARCHAR(32)  NOT NULL DEFAULT 'text',
            options        TEXT         NULL,
            is_active      TINYINT(1)   NOT NULL DEFAULT 1,
            skip_lesson_id INT          NULL,
            created_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
                               ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (id),
            KEY idx_eq_step   (step_number),
            KEY idx_eq_active (is_active)
        )""",
        # ── NEW checklist additions ───────────────────────────────────────────

        # b3/m2: L6 Create task — user-authored corrective summaries and cohort-shared posts
        """CREATE TABLE IF NOT EXISTS user_created_content (
            id               INT UNSIGNED NOT NULL AUTO_INCREMENT,
            user_id          INT UNSIGNED NULL,
            session_token    VARCHAR(64)  NOT NULL,
            submission_id    INT UNSIGNED NULL,
            content_type     ENUM('corrective_summary','reflection_post','cohort_share')
                                 NOT NULL DEFAULT 'corrective_summary',
            body             TEXT         NOT NULL,
            is_shared        TINYINT(1)   NOT NULL DEFAULT 0,
            bloom_level      TINYINT      NOT NULL DEFAULT 6,
            created_at       DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (id),
            KEY idx_ucc_user       (user_id),
            KEY idx_ucc_session    (session_token),
            KEY idx_ucc_submission (submission_id),
            KEY idx_ucc_shared     (is_shared)
        )""",
    ]

    with engine.connect() as conn:
        for sql in new_tables_sql:
            try:
                conn.execute(sa.text(sql))
                conn.commit()
            except Exception as e:
                logger.warning(f"[Migration] Table creation skipped: {e}")

    # ── v7.0: Research consent audit log ─────────────────────────────────────
    # Immutable per-action audit trail. Never updated or deleted.
    # IRB requirement: proves consent was obtained and records withdrawals.
    try:
        with engine.begin() as conn:
            conn.execute(sa.text("""
                CREATE TABLE IF NOT EXISTS research_consent_log (
                    id          INT UNSIGNED NOT NULL AUTO_INCREMENT,
                    user_id     INT UNSIGNED NOT NULL,
                    action      ENUM('granted','withdrawn') NOT NULL,
                    ip_address  VARCHAR(45)  NULL,
                    user_agent  VARCHAR(500) NULL,
                    acted_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (id),
                    INDEX idx_rcl_user (user_id),
                    CONSTRAINT fk_rcl_user FOREIGN KEY (user_id)
                        REFERENCES users(id) ON DELETE CASCADE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
            """))
        logger.info("[Migration] research_consent_log table ensured.")
    except Exception as e:
        logger.warning(f"[Migration] research_consent_log creation skipped: {e}")

    # ── Safe column additions ─────────────────────────────────────────────────
    column_migrations = [
        ("lessons", "image_url",
         "ALTER TABLE lessons ADD COLUMN image_url VARCHAR(512) NULL AFTER content"),
        ("lessons", "is_published",
         "ALTER TABLE lessons ADD COLUMN is_published TINYINT(1) NOT NULL DEFAULT 1"),
        ("lessons", "updated_at",
         "ALTER TABLE lessons ADD COLUMN updated_at DATETIME NOT NULL "
         "DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
        ("lessons", "mil_skill",
         "ALTER TABLE lessons ADD COLUMN mil_skill VARCHAR(50) NULL"),
        ("lessons", "sort_order",
         "ALTER TABLE lessons ADD COLUMN sort_order INT NULL"),
        ("lessons", "prerequisite_lesson_id",
         "ALTER TABLE lessons ADD COLUMN prerequisite_lesson_id INT NULL"),
        ("quiz_questions", "difficulty",
         "ALTER TABLE quiz_questions ADD COLUMN difficulty "
         "ENUM('beginner','intermediate','advanced') NULL DEFAULT 'beginner'"),
        ("quiz_questions", "image_url",
         "ALTER TABLE quiz_questions ADD COLUMN image_url VARCHAR(512) NULL"),
        ("quiz_questions", "video_url",
         "ALTER TABLE quiz_questions ADD COLUMN video_url VARCHAR(1024) NULL"),
        ("quiz_questions", "media_type",
         "ALTER TABLE quiz_questions ADD COLUMN media_type ENUM('text','image','video','file') NULL DEFAULT 'text'"),
        ("quiz_questions", "hint",
         "ALTER TABLE quiz_questions ADD COLUMN hint TEXT NULL"),
        ("lesson_topics", "quiz_limit",
         "ALTER TABLE lesson_topics ADD COLUMN quiz_limit INT NULL COMMENT 'Max quiz questions per session; NULL = no limit'"),
        ("pretest_results", "days_between",
         "ALTER TABLE pretest_results ADD COLUMN days_between FLOAT NULL COMMENT 'Days between pretest and posttest for longitudinal pairing'"),
        ("pretest_results", "score_gain_pct",
         "ALTER TABLE pretest_results ADD COLUMN score_gain_pct FLOAT NULL COMMENT 'score_pct(posttest) - score_pct(pretest)'"),
        ("users", "is_verified",
         "ALTER TABLE users ADD COLUMN is_verified TINYINT(1) NOT NULL DEFAULT 0"),
        # ── v7.0: IRB research consent fields ─────────────────────────────────
        ("users", "research_consent",
         "ALTER TABLE users ADD COLUMN research_consent TINYINT(1) NOT NULL DEFAULT 0"),
        ("users", "research_consent_at",
         "ALTER TABLE users ADD COLUMN research_consent_at DATETIME NULL"),
        ("users", "consent_withdrawn_at",
         "ALTER TABLE users ADD COLUMN consent_withdrawn_at DATETIME NULL"),
        ("eval_questions", "step_label",
         "ALTER TABLE eval_questions ADD COLUMN step_label VARCHAR(80) NULL"),
        ("eval_questions", "step_link_type",
         "ALTER TABLE eval_questions ADD COLUMN step_link_type VARCHAR(32) NULL"),
        ("eval_questions", "step_link_value",
         "ALTER TABLE eval_questions ADD COLUMN step_link_value VARCHAR(512) NULL"),
        # ── v9.0: Follow-up branch full parity + nesting ──────────────────────
        ("eval_question_branches", "parent_branch_id",
         "ALTER TABLE eval_question_branches ADD COLUMN parent_branch_id INT NULL "
         "COMMENT 'FK → eval_question_branches.id; NULL = top-level branch'"),
        ("eval_question_branches", "input_type",
         "ALTER TABLE eval_question_branches ADD COLUMN input_type VARCHAR(32) NULL DEFAULT 'none' "
         "COMMENT 'none|text|textarea|multiple_choice|yes_no|scale|checkbox'"),
        ("eval_question_branches", "options",
         "ALTER TABLE eval_question_branches ADD COLUMN options TEXT NULL "
         "COMMENT 'JSON [{value,label},...] for multiple_choice / checkbox / yes_no'"),
        ("eval_question_branches", "scale_min_label",
         "ALTER TABLE eval_question_branches ADD COLUMN scale_min_label VARCHAR(100) NULL"),
        ("eval_question_branches", "scale_max_label",
         "ALTER TABLE eval_question_branches ADD COLUMN scale_max_label VARCHAR(100) NULL"),
        ("eval_question_branches", "image_url",
         "ALTER TABLE eval_question_branches ADD COLUMN image_url VARCHAR(512) NULL "
         "COMMENT 'Direct image URL for content_type=image'"),
        ("eval_question_branches", "file_url",
         "ALTER TABLE eval_question_branches ADD COLUMN file_url VARCHAR(512) NULL "
         "COMMENT 'File download URL for content_type=file'"),
        ("eval_question_branches", "file_name",
         "ALTER TABLE eval_question_branches ADD COLUMN file_name VARCHAR(255) NULL "
         "COMMENT 'Display filename for content_type=file'"),
        ("eval_question_branches", "link_label",
         "ALTER TABLE eval_question_branches ADD COLUMN link_label VARCHAR(255) NULL "
         "COMMENT 'Button label for content_type=url'"),
        # ── v10.0: mindmap_node_id unlock hooks ───────────────────────────────
        ("quiz_questions", "mindmap_node_id",
         "ALTER TABLE quiz_questions ADD COLUMN mindmap_node_id VARCHAR(64) NULL "
         "COMMENT 'FK → mindmap_nodes.id; correct answer unlocks this node'"),
        ("lessons", "mindmap_node_id",
         "ALTER TABLE lessons ADD COLUMN mindmap_node_id VARCHAR(64) NULL "
         "COMMENT 'FK → mindmap_nodes.id; reading this lesson unlocks this node'"),
        ("eval_questions", "mindmap_node_id",
         "ALTER TABLE eval_questions ADD COLUMN mindmap_node_id VARCHAR(64) NULL "
         "COMMENT 'FK → mindmap_nodes.id; completing this eval step unlocks this node'"),
    ]

    with engine.connect() as conn:
        for table, column, sql in column_migrations:
            try:
                result = conn.execute(sa.text(
                    "SELECT COUNT(*) FROM information_schema.columns "
                    "WHERE table_schema = DATABASE() "
                    f"AND table_name = '{table}' AND column_name = '{column}'"
                ))
                if result.scalar() == 0:
                    conn.execute(sa.text(sql))
                    conn.commit()
                    logger.info(f"[Migration] Added column '{column}' to '{table}'")
            except Exception as e:
                logger.warning(f"[Migration] {table}.{column}: {e}")

    enum_migrations = [
        """ALTER TABLE submissions
           MODIFY COLUMN input_type ENUM('text','url','image','pdf','file')
           NOT NULL DEFAULT 'text'""",
    ]

    with engine.connect() as conn:
        for sql in enum_migrations:
            try:
                conn.execute(sa.text(sql))
                conn.commit()
            except Exception as e:
                logger.warning(f"[Migration] Enum expand skipped: {e}")

    # ── v8.0: Admin audit log ─────────────────────────────────────────────────
    # Immutable per-action trail for every admin mutation. Never updated/deleted.
    try:
        with engine.begin() as conn:
            conn.execute(sa.text("""
                CREATE TABLE IF NOT EXISTS admin_audit_log (
                    id             INT UNSIGNED NOT NULL AUTO_INCREMENT,
                    admin_id       INT UNSIGNED NULL,
                    admin_username VARCHAR(50)  NULL,
                    action         ENUM('create','update','delete','role_change','upload','reorder') NOT NULL,
                    resource_type  VARCHAR(50)  NOT NULL,
                    resource_id    VARCHAR(100) NULL,
                    detail         TEXT         NULL,
                    ip_address     VARCHAR(45)  NULL,
                    performed_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (id),
                    INDEX idx_aal_admin       (admin_id),
                    INDEX idx_aal_resource    (resource_type, resource_id),
                    INDEX idx_aal_performed   (performed_at),
                    CONSTRAINT fk_aal_admin FOREIGN KEY (admin_id)
                        REFERENCES users(id) ON DELETE SET NULL
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
            """))
        logger.info("[Migration] admin_audit_log table ensured.")
    except Exception as e:
        logger.warning(f"[Migration] admin_audit_log creation skipped: {e}")

    # ── v9.0: Dynamic topic registry ─────────────────────────────────────────
    try:
        with engine.begin() as conn:
            conn.execute(sa.text("""
                CREATE TABLE IF NOT EXISTS lesson_topics (
                    id         INT UNSIGNED NOT NULL AUTO_INCREMENT,
                    `key`      VARCHAR(60)  NOT NULL,
                    label      VARCHAR(100) NOT NULL,
                    icon       VARCHAR(10)  NOT NULL DEFAULT '📄',
                    color_hue  SMALLINT     NOT NULL DEFAULT 220,
                    sort_order INT          NOT NULL DEFAULT 0,
                    created_at DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (id),
                    UNIQUE KEY uq_topic_key (`key`)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """))
        logger.info("[Migration] lesson_topics table ensured.")
    except Exception as e:
        logger.warning(f"[Migration] lesson_topics creation skipped: {e}")

    # Seed built-in topics if table is empty
    try:
        with engine.begin() as conn:
            count = conn.execute(sa.text("SELECT COUNT(*) FROM lesson_topics")).scalar()
            if count == 0:
                conn.execute(sa.text("""
                    INSERT INTO lesson_topics (`key`, label, icon, color_hue, sort_order) VALUES
                    ('claim_detection',    'Claim Detection',    '🎯', 220, 1),
                    ('source_verification','Source Verification','🔍', 158, 2),
                    ('bias_detection',     'Bias Detection',     '⚡',  38, 3),
                    ('evidence_evaluation','Evidence Evaluation','📊', 340, 4),
                    ('general',            'General MIL',        '📖', 260, 5)
                """))
        logger.info("[Migration] lesson_topics seeded with built-in topics.")
    except Exception as e:
        logger.warning(f"[Migration] lesson_topics seeding skipped: {e}")

    # ── topic_quiz_links: explicit per-topic quiz question list ───────────────
    try:
        with engine.begin() as conn:
            conn.execute(sa.text("""
                CREATE TABLE IF NOT EXISTS topic_quiz_links (
                    id          INT UNSIGNED NOT NULL AUTO_INCREMENT,
                    topic_key   VARCHAR(60)  NOT NULL,
                    question_id INT          NOT NULL,
                    PRIMARY KEY (id),
                    UNIQUE KEY uq_tql (topic_key, question_id),
                    INDEX idx_tql_topic (topic_key),
                    INDEX idx_tql_qid   (question_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """))
        logger.info("[Migration] topic_quiz_links table ensured.")
    except Exception as e:
        logger.warning(f"[Migration] topic_quiz_links creation skipped: {e}")

    # Migrate ENUM → VARCHAR(60) so any topic key is accepted
    enum_to_varchar = [
        ("lessons",            "topic", "VARCHAR(60) NOT NULL"),
        ("quiz_questions",     "topic", "VARCHAR(60) NOT NULL"),
        ("user_skill_progress","topic", "VARCHAR(60) NOT NULL"),
        ("user_skill_history", "topic", "VARCHAR(60) NOT NULL"),
    ]
    with engine.connect() as conn:
        for table, column, col_def in enum_to_varchar:
            try:
                col_type = conn.execute(sa.text(
                    "SELECT DATA_TYPE FROM information_schema.columns "
                    "WHERE table_schema = DATABASE() "
                    f"AND table_name = '{table}' AND column_name = '{column}'"
                )).scalar()
                if col_type and col_type.upper() == "ENUM":
                    conn.execute(sa.text(
                        f"ALTER TABLE `{table}` MODIFY COLUMN `{column}` {col_def}"
                    ))
                    conn.commit()
                    logger.info(f"[Migration] {table}.{column} ENUM → VARCHAR(60)")
            except Exception as e:
                logger.warning(f"[Migration] {table}.{column} varchar migration skipped: {e}")
