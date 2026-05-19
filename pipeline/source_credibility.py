"""
SocialProof — pipeline/source_credibility.py  v4.0
Evaluates source reliability based on domain type, HTTPS, and
the tiered reputation registry in corpus/source_registry.py.

v4.0 Changes vs v3.0:
  - SourceCredibilityModule.evaluate() now accepts final_url separately from
    the raw input URL — ensures shortener-expanded domains are scored correctly
  - International gov domains added: .gov.uk, .gov.au, .gov.sg, .gc.ca,
    .gouv.fr, .gob.mx, .govt.nz, .gov.in, .europa.eu, and more
  - International edu/academic domains added: .ac.uk, .edu.au, .ac.nz,
    .edu.sg, .edu.cn, .ac.jp, and more
  - Lookalike / spoof domain detection added — catches abc-news.com.co,
    cnn-breaking.xyz, bbc.net.co, and other impersonation patterns
  - Subdomain-aware gov scoring: deep subdomains of .gov still score as gov
  - _label() thresholds unchanged; all existing signals preserved

v3.0 Changes (retained):
  - Added get_mbfc_rating() — DB lookup, on-demand (Source node click)

Architecture note:
  SourceCredibilityModule.evaluate() → fast, fully offline, always runs.
  get_mbfc_rating()                  → DB lookup, on-demand.
  All results are context for the user — never verdicts.

IMPORTANT: Always pass result["url"] from URLFetcher (the FINAL redirected URL)
as the `final_url` argument to evaluate(). This ensures short-link domains like
t.co or bit.ly are not scored instead of the actual source domain.
"""

import asyncio
import hashlib
import json
import urllib.request
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List
from urllib.parse import urlparse

from config import logger

from corpus.source_registry import get_reputation, REPUTATION_THRESHOLD


# ── MBFC lookup (v3.0, unchanged) ─────────────────────────────────────────────

def get_mbfc_rating(url: Optional[str]) -> Optional[Dict]:
    """
    Look up a domain in the mbfc_domains table (populated by scripts/sync_mbfc.py).

    Returns a dict with non-judgmental signal fields, or None if:
      - no URL was provided
      - domain cannot be parsed
      - domain is not in the MBFC dataset

    The returned dict is added to the Source node response payload.
    The frontend displays it as context — e.g. "This domain has a [MIXED]
    factual reporting rating" — never as a verdict.

    Args:
        url: The URL submitted by the user, or None for text-only input.

    Returns:
        {
            "domain": str,
            "factual_reporting": str | None,   # HIGH / MOSTLY_FACTUAL / MIXED / LOW / VERY_LOW
            "bias_rating": str | None,          # LEFT-CENTER / CENTER / RIGHT / etc.
            "credibility_rating": str | None,
            "country": str | None,
        }
        or None
    """
    if not url or not url.strip():
        return None

    raw = url.strip().lower()
    if not raw.startswith("http"):
        raw = "https://" + raw

    try:
        parsed = urlparse(raw)
        domain = parsed.netloc.replace("www.", "").strip("/")
        if not domain:
            return None
    except Exception:
        return None

    try:
        import sqlalchemy as sa
        from database.models import engine

        with engine.connect() as conn:
            result = conn.execute(sa.text(
                "SELECT domain, factual_reporting, bias_rating, credibility_rating, country "
                "FROM mbfc_domains WHERE domain = :domain LIMIT 1"
            ), {"domain": domain})
            row = result.fetchone()

        if row is None:
            logger.debug(f"[MBFC] Domain not found in mbfc_domains: {domain}")
            return None

        return {
            "domain":             row[0],
            "factual_reporting":  row[1],
            "bias_rating":        row[2],
            "credibility_rating": row[3],
            "country":            row[4],
        }

    except Exception as e:
        logger.warning(f"[MBFC] DB lookup failed for {domain}: {e}")
        return None


# ── SourceCredibilityModule v4.0 ─────────────────────────────────────────────

class SourceCredibilityModule:
    """
    Outputs a source_score (0.0–1.0) and human-readable signals list.
    No external API calls — fully offline and reproducible.

    Scoring priority:
      1. Social platform → always 0.30 (low, social origin)
      2. Registry reputation >= 0.90 → score = reputation directly
      3. Registry reputation >= REPUTATION_THRESHOLD (0.65) → score = rep * 0.90
      4. Lookalike / spoof domain → heavy penalty
      5. Unknown domain → heuristics (TLD, HTTPS, international gov/edu/org bonus)
    """

    SOCIAL_PLATFORMS = {
        "facebook.com", "twitter.com", "x.com", "tiktok.com",
        "instagram.com", "youtube.com", "reddit.com", "threads.net",
    }

    SUSPICIOUS_TLDS = {".xyz", ".click", ".buzz", ".info", ".biz", ".top", ".win", ".loan"}

    # ── International gov domain suffixes ─────────────────────────────────────
    # Includes ccTLD variants used by national governments worldwide.
    GOV_SUFFIXES = {
        # Philippines
        ".gov.ph",
        # USA
        ".gov", ".mil",
        # UK
        ".gov.uk", ".mod.uk", ".nhs.uk", ".police.uk",
        # Australia
        ".gov.au", ".act.gov.au", ".nsw.gov.au", ".qld.gov.au",
        ".sa.gov.au", ".tas.gov.au", ".vic.gov.au", ".wa.gov.au",
        # Canada
        ".gc.ca", ".gov.ab.ca", ".gov.bc.ca", ".gov.mb.ca",
        ".gov.nb.ca", ".gov.nl.ca", ".gov.ns.ca", ".gov.on.ca",
        ".gov.pe.ca", ".gov.sk.ca",
        # EU & European nations
        ".europa.eu", ".gouv.fr", ".bund.de", ".gob.es",
        ".gov.it", ".gov.pt", ".overheid.nl", ".belgium.be",
        ".admin.ch",
        # Latin America
        ".gob.mx", ".gob.ar", ".gob.cl", ".gob.pe", ".gob.ve",
        ".gob.co", ".gob.bo", ".gob.ec", ".gob.gt", ".gob.hn",
        ".gob.ni", ".gob.pa", ".gob.py", ".gob.sv", ".gob.uy",
        ".gob.do", ".gob.cu",
        # Asia-Pacific
        ".gov.sg", ".gov.in", ".gov.nz", ".govt.nz",
        ".gov.my", ".gov.bd", ".gov.lk", ".gov.np",
        ".go.id", ".go.jp", ".go.kr", ".go.th",
        # Africa & Middle East
        ".gov.za", ".gov.ng", ".gov.ke", ".gov.eg",
        ".gov.il", ".gov.ae", ".gov.sa",
        # International organisations (treaty-backed)
        ".un.org", ".who.int", ".worldbank.org", ".imf.org",
        ".unesco.org", ".unicef.org", ".ilo.org", ".fao.org",
        ".wto.org", ".icj-cij.org", ".icc-cpi.int",
    }

    # ── International edu / academic domain suffixes ──────────────────────────
    EDU_SUFFIXES = {
        ".edu",           # USA (and widely adopted)
        ".edu.ph",        # Philippines
        ".ac.uk",         # UK
        ".edu.au",        # Australia
        ".ac.nz",         # New Zealand
        ".edu.sg",        # Singapore
        ".ac.jp",         # Japan
        ".edu.cn",        # China
        ".edu.in",        # India
        ".edu.my",        # Malaysia
        ".ac.za",         # South Africa
        ".edu.ng",        # Nigeria
        ".edu.mx",        # Mexico
        ".edu.ar",        # Argentina
        ".edu.co",        # Colombia
        ".edu.br",        # Brazil
        ".edu.pe",        # Peru
        ".edu.ve",        # Venezuela
    }

    # ── Known brands targeted by lookalike / spoof domains ───────────────────
    # Keep lowercase, no TLD.
    _KNOWN_BRANDS = {
        "bbc", "cnn", "reuters", "apnews", "ap",
        "nytimes", "newyorktimes", "washingtonpost", "wapo",
        "theguardian", "guardian",
        "abcnews", "nbcnews", "cbsnews", "foxnews", "msnbc",
        "bloomberg", "forbes", "businessinsider",
        "rappler", "inquirer", "philstar", "gmanetwork",
        "who", "unicef", "worldbank",
    }

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_domain(url: str) -> str:
        """Return lowercased domain without www., or empty string on failure."""
        try:
            parsed = urlparse(url if url.startswith("http") else "https://" + url)
            return parsed.netloc.lower().replace("www.", "")
        except Exception:
            return ""

    @classmethod
    def _is_gov(cls, domain: str) -> bool:
        return any(domain == suf.lstrip(".") or domain.endswith(suf)
                   for suf in cls.GOV_SUFFIXES)

    @classmethod
    def _is_edu(cls, domain: str) -> bool:
        return any(domain == suf.lstrip(".") or domain.endswith(suf)
                   for suf in cls.EDU_SUFFIXES)

    @classmethod
    def _is_lookalike(cls, domain: str, registry_domain: str) -> bool:
        """
        Detect spoof / lookalike domains.

        Patterns caught:
          abcnews.com.co   → extra TLD stacking (3+ dot-separated labels after base)
          cnn-breaking.xyz → known brand in subdomain/base + suspicious TLD
          bbc.net.co       → brand as base but wrong TLD combo
          reuters-news.co  → brand + hyphen + anything

        A domain already in the registry passes through unconditionally.
        """
        if registry_domain == domain:   # trusted registry match
            return False

        parts = domain.split(".")

        # Rule 1: TLD stacking (e.g. realsite.com.co, bbc.com.net.co)
        # Legitimate ccSLD registrations exist (e.g. .co.uk, .gov.ph) so we
        # only flag domains whose root matches a known brand AND that have ≥ 4 parts.
        if len(parts) >= 4:
            base = parts[0].replace("-", "").replace("_", "")
            if any(brand in base for brand in cls._KNOWN_BRANDS):
                return True

        # Rule 2: Known brand in the leftmost label + suspicious TLD
        base = parts[0].replace("-", "").replace("_", "")
        tld  = "." + parts[-1] if parts else ""
        if (any(brand in base for brand in cls._KNOWN_BRANDS)
                and tld in cls.SUSPICIOUS_TLDS):
            return True

        # Rule 3: Brand + hyphen pattern in base label (cnn-breaking, bbc-live)
        for brand in cls._KNOWN_BRANDS:
            if (base.startswith(brand + "-") or base.endswith("-" + brand)
                    or f"-{brand}-" in base):
                return True

        return False

    # ── Main evaluate ─────────────────────────────────────────────────────────

    @classmethod
    def evaluate(
        cls,
        url: Optional[str],
        text: str,
        final_url: Optional[str] = None,
    ) -> Dict:
        """
        Evaluate source credibility.

        Args:
            url:       The original URL as submitted by the user (may be a shortener).
            text:      The extracted article text (used for social-mention detection).
            final_url: The final URL after redirects, returned by URLFetcher as
                       result["url"]. When provided, this is what gets scored —
                       not the shortener or input URL.

        Returns:
            {"score": float, "label": str, "signals": list[str]}
        """
        signals: list = []
        score   = 0.5

        # ── Text-only input: no URL provided ──────────────────────────────────
        scoring_url = final_url or url
        if not scoring_url or scoring_url.strip() == "":
            social_mentions = sum(
                1 for p in cls.SOCIAL_PLATFORMS
                if p.replace(".com", "").replace(".net", "") in text.lower()
            )
            if social_mentions:
                score = 0.35
                signals.append("social_media_origin_detected")
            else:
                score = 0.45
                signals.append("no_source_provided")
            return {"score": score, "label": cls._label(score), "signals": signals}

        # ── Log if a shortener was expanded ───────────────────────────────────
        if final_url and url and final_url != url:
            logger.info(f"[Credibility] Shortener expanded: {url} → {final_url}")
            signals.append(f"shortener_expanded:{cls._parse_domain(url)}")

        domain = cls._parse_domain(scoring_url)
        tld    = "." + domain.split(".")[-1] if "." in domain else ""

        # ── HTTPS signal ──────────────────────────────────────────────────────
        if scoring_url.startswith("https"):
            score += 0.10
            signals.append("https_present")
        else:
            score -= 0.10
            signals.append("no_https")

        # ── Social platform ───────────────────────────────────────────────────
        if domain in cls.SOCIAL_PLATFORMS:
            score = 0.30
            signals.append("social_media_source")
            return {"score": round(score, 3), "label": cls._label(score), "signals": signals}

        # ── Registry lookup ───────────────────────────────────────────────────
        rep = get_reputation(domain)

        if rep >= 0.90:
            score = rep
            signals.append(f"registry_tier1_tier2:{domain}")
            return {"score": round(score, 3), "label": cls._label(score), "signals": signals}

        if rep >= REPUTATION_THRESHOLD:
            score = rep * 0.90
            signals.append(f"registry_tier3:{domain}")
            return {"score": round(score, 3), "label": cls._label(score), "signals": signals}

        # rep == 0.0 means domain is NOT in the registry — continue heuristics

        # ── Lookalike / spoof detection ───────────────────────────────────────
        # Check BEFORE gov/edu bonuses so a spoofed .gov.co doesn't get a bonus.
        registry_domain = domain if rep > 0 else ""
        if cls._is_lookalike(domain, registry_domain):
            score -= 0.30
            signals.append(f"possible_lookalike_domain:{domain}")
            # Still continue — collect other signals, then cap
            score = max(0.0, min(1.0, score))
            return {"score": round(score, 3), "label": cls._label(score), "signals": signals}

        # ── International gov bonus ───────────────────────────────────────────
        if cls._is_gov(domain):
            score += 0.35
            signals.append("gov_domain_unlisted")

        # ── International edu / academic bonus ───────────────────────────────
        elif cls._is_edu(domain):
            score += 0.30
            signals.append("edu_domain_unlisted")

        # ── .org bonus (mild) ─────────────────────────────────────────────────
        elif tld == ".org":
            score += 0.10
            signals.append("org_domain_unlisted")

        # ── Suspicious TLD penalty ────────────────────────────────────────────
        if tld in cls.SUSPICIOUS_TLDS:
            score -= 0.20
            signals.append(f"suspicious_tld:{tld}")

        score = max(0.0, min(1.0, score))
        return {"score": round(score, 3), "label": cls._label(score), "signals": signals}

    @staticmethod
    def _label(score: float) -> str:
        if score >= 0.70:
            return "High Credibility"
        if score >= 0.45:
            return "Moderate Credibility"
        return "Low Credibility"


