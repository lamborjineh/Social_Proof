"""
SocialProof — URL Fetcher  v4.1
Fetches and extracts clean text from a URL for analysis.
Called by the orchestrator when input_type == 'url'.

v4.1 Changes vs v4.0 — Cloudflare + gov.ph bypass:
  - cloudscraper added as Pass 1 for ALL fetches (replaces bare urllib for general sites)
    → spoofs TLS fingerprint + solves CF JS challenge without a browser
  - playwright-stealth added to Playwright pass — patches navigator.webdriver leak
    → handles CF-protected sites that need full JS rendering
  - ScraperAPI added as final fallback (uses rotating residential IPs)
    → already in your .env.example — just set SCRAPER_API_KEY
  - gov.ph SSL: verify=False applied in cloudscraper pass (same as live_search.py)
  - User-Agent pool rotated per request (same as live_search.py BUG 8 fix)

v4.0 Changes vs v3.1:
  - Playwright fallback for JS-heavy pages
  - Reddit JSON API, YouTube oEmbed, Twitter Nitter, TikTok oEmbed
  - archive.ph + Wayback Machine fallbacks for paywalled/gone pages

FULL FALLBACK CHAIN (matches live_search.py):
  1. cloudscraper      — handles Cloudflare JS challenge, gov.ph SSL, most blocks
  2. playwright-stealth — for sites needing full JS render + CF bypass
  3. ScraperAPI        — rotating residential proxies, last resort
  4. OG/metadata       — always available, least content
  5. archive.ph        — paywalled articles
  6. Wayback Machine   — gone/403 pages

SETUP (add to requirements.txt):
  trafilatura
  cloudscraper
  playwright          → then run: playwright install chromium
  playwright-stealth  → pip install playwright-stealth
  requests            → already in your requirements.txt

NEW env vars (add to .env):
  SCRAPER_API_KEY=your_key     # get at scraperapi.com — 1000 free req/month
  NITTER_INSTANCE=https://nitter.net
"""

import json
import re
import random
import socket
import ipaddress
import urllib.request
import urllib.error
from html import unescape as html_unescape
from urllib.parse import urlparse, urlencode, quote_plus
from typing import Optional

from config import logger

import os


# ── SSRF protection ───────────────────────────────────────────────────────────
def _is_ssrf_blocked(url: str) -> bool:
    """
    Security fix: block requests to loopback, link-local, and private IP ranges
    to prevent Server-Side Request Forgery (e.g. AWS metadata, internal services).
    Returns True if the URL should be blocked.
    """
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        if not hostname:
            return True
        # Resolve to IP; catch resolution failures
        try:
            ip = ipaddress.ip_address(socket.gethostbyname(hostname))
        except (socket.gaierror, ValueError):
            return True  # unresolvable — block
        return (
            ip.is_loopback
            or ip.is_link_local
            or ip.is_private
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        )
    except Exception:
        return True  # block on any unexpected error

# ── Optional dependencies ─────────────────────────────────────────────────────
try:
    import trafilatura
    _TRAFILATURA_AVAILABLE = True
except ImportError:
    _TRAFILATURA_AVAILABLE = False
    logger.warning("[URLFetcher] trafilatura not installed — pip install trafilatura")

try:
    import cloudscraper
    _CLOUDSCRAPER_AVAILABLE = True
except ImportError:
    _CLOUDSCRAPER_AVAILABLE = False
    logger.warning("[URLFetcher] cloudscraper not installed — pip install cloudscraper")

try:
    from playwright.sync_api import sync_playwright
    _PLAYWRIGHT_AVAILABLE = True
except ImportError:
    _PLAYWRIGHT_AVAILABLE = False
    logger.warning("[URLFetcher] playwright not installed — JS-heavy pages won't render")

try:
    try:
        # playwright-stealth v2.x
        from playwright_stealth import Stealth
        def stealth_sync(page):
            Stealth().apply_stealth_sync(page)
    except (ImportError, AttributeError):
        # playwright-stealth v1.x fallback
        from playwright_stealth import stealth_sync
    _STEALTH_AVAILABLE = True
except ImportError:
    _STEALTH_AVAILABLE = False
    logger.warning("[URLFetcher] playwright-stealth not installed — CF bypass weakened")

# ── Config ────────────────────────────────────────────────────────────────────
NITTER_INSTANCE  = os.getenv("NITTER_INSTANCE", "https://nitter.net")
SCRAPER_API_KEY  = os.getenv("SCRAPER_API_KEY", "")

# Rotating User-Agent pool (same as live_search.py BUG 8 fix)
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
]

# ── Domain sets ───────────────────────────────────────────────────────────────

# Social media — attempt OG + platform-specific fallback
_SOCIAL_DOMAINS = {
    "facebook.com", "fb.com",
    "instagram.com",
    "twitter.com", "x.com",
    "tiktok.com",
    "threads.net",
    "linkedin.com",
}

# Reddit — use JSON API directly
_REDDIT_DOMAINS = {"reddit.com", "old.reddit.com", "www.reddit.com", "redd.it"}

# YouTube — use oEmbed
_YOUTUBE_DOMAINS = {"youtube.com", "youtu.be", "www.youtube.com"}

# URL shorteners — resolve then fetch
_SHORTENER_DOMAINS = {
    "bit.ly", "t.co", "tinyurl.com", "goo.gl", "ow.ly",
    "buff.ly", "rb.gy", "short.link", "tiny.cc", "is.gd",
    "lnkd.in", "dlvr.it", "ift.tt",
}

# Known Cloudflare / JS-heavy domains (trigger Playwright immediately)
_JS_HEAVY_DOMAINS = {
    "medium.com", "substack.com", "bloomberg.com",
    "wsj.com", "ft.com", "nytimes.com",
    "businessinsider.com", "theguardian.com",
}

# HTTP codes that trigger archive fallbacks
_WAYBACK_TRIGGER_CODES  = {403, 404, 410, 451}
_ARCHIVE_PH_TRIGGER_CODES = {403, 410, 451}   # paywall signals


class URLFetcher:
    TIMEOUT    = 14
    MAX_BYTES  = 500_000    # 500 KB
    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )

    # ── HTML parsing patterns ─────────────────────────────────────────────────
    STRIP_TAGS   = re.compile(
        r"<(script|style|nav|footer|header|aside|form|iframe|noscript)[^>]*>.*?</\1>",
        re.IGNORECASE | re.DOTALL,
    )
    ARTICLE_TAG  = re.compile(r"<article[^>]*>(.*?)</article>", re.IGNORECASE | re.DOTALL)
    MAIN_TAG     = re.compile(r"<main[^>]*>(.*?)</main>",       re.IGNORECASE | re.DOTALL)
    CONTENT_TAGS = re.compile(
        r"<(p|h[1-6]|li|blockquote|td|th|figcaption)[^>]*>(.*?)</\1>",
        re.IGNORECASE | re.DOTALL,
    )
    ALL_TAGS     = re.compile(r"<[^>]+>")

    # ── Domain helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _netloc(url: str) -> str:
        try:
            return urlparse(url).netloc.lower().lstrip("www.")
        except Exception:
            return ""

    @classmethod
    def _domain_in(cls, url: str, domain_set: set) -> bool:
        netloc = cls._netloc(url)
        return any(netloc == d or netloc.endswith("." + d) for d in domain_set)

    # ── Quality checks ────────────────────────────────────────────────────────

    @staticmethod
    def _is_low_quality(text: str) -> bool:
        t = text.lower()
        return (
            len(text) < 100
            or len(text.split()) < 20
            or "enable javascript" in t
            or "please enable js" in t
            or "checking your browser" in t
            or "ddos protection" in t
            or "just a moment" in t          # Cloudflare challenge page
            or "verify you are human" in t
        )

    @staticmethod
    def _smart_truncate(text: str, max_chars: int = 8_000) -> str:
        if len(text) <= max_chars:
            return text
        truncated   = text[:max_chars]
        last_period = truncated.rfind(".")
        if last_period > max_chars * 0.7:
            return truncated[:last_period + 1]
        return truncated

    # ── Metadata helpers ──────────────────────────────────────────────────────

    @classmethod
    def _extract_og(cls, raw: str) -> str:
        og_title = re.search(
            r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\'](.*?)["\']',
            raw, re.IGNORECASE,
        )
        og_desc = re.search(
            r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\'](.*?)["\']',
            raw, re.IGNORECASE,
        )
        parts = [
            og_title.group(1).strip() if og_title else "",
            og_desc.group(1).strip()  if og_desc  else "",
        ]
        return " ".join(p for p in parts if p)

    @classmethod
    def _extract_meta_desc(cls, raw: str) -> str:
        m = re.search(
            r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']',
            raw, re.IGNORECASE,
        )
        return m.group(1).strip() if m else ""

    @classmethod
    def _extract_title(cls, raw: str) -> str:
        m = re.search(r"<title[^>]*>(.*?)</title>", raw, re.IGNORECASE | re.DOTALL)
        return cls.ALL_TAGS.sub("", m.group(1)).strip() if m else ""

    @staticmethod
    def _detect_charset(content_type: str) -> str:
        m = re.search(r"charset\s*=\s*([\w-]+)", content_type, re.IGNORECASE)
        return m.group(1).lower() if m else "utf-8"

    # ── Text extraction helpers ───────────────────────────────────────────────

    @classmethod
    def _extract_inline_content(cls, html_fragment: str) -> str:
        parts = cls.CONTENT_TAGS.findall(html_fragment)
        if parts:
            texts = [cls.ALL_TAGS.sub("", p[1]).strip() for p in parts]
            return " ".join(t for t in texts if len(t) > 20)
        return cls.ALL_TAGS.sub(" ", html_fragment)

    @classmethod
    def _trafilatura_extract(cls, raw: str, url: str) -> str:
        if not _TRAFILATURA_AVAILABLE:
            return ""
        try:
            result = trafilatura.extract(
                raw, url=url,
                include_comments=False, include_tables=True,
                no_fallback=False, favor_precision=True,
            )
            return (result or "").strip()
        except Exception as exc:
            logger.debug(f"[URLFetcher] trafilatura failed: {exc}")
            return ""

    @classmethod
    def _regex_extract(cls, raw: str) -> str:
        cleaned = cls.STRIP_TAGS.sub(" ", raw)
        for tag_match in [cls.ARTICLE_TAG.search(cleaned), cls.MAIN_TAG.search(cleaned)]:
            if tag_match:
                text = cls._extract_inline_content(tag_match.group(1))
                if not cls._is_low_quality(text):
                    return text
        parts = cls.CONTENT_TAGS.findall(cleaned)
        if parts:
            text = " ".join(
                cls.ALL_TAGS.sub("", p[1]).strip()
                for p in parts if len(p[1]) > 20
            )
            if not cls._is_low_quality(text):
                return text
        body_m = re.search(r"<body[^>]*>(.*?)</body>", cleaned, re.IGNORECASE | re.DOTALL)
        body = body_m.group(1) if body_m else cleaned
        return cls.ALL_TAGS.sub(" ", body)

    @classmethod
    def _normalise(cls, text: str) -> str:
        text = re.sub(r"\s+", " ", text).strip()
        text = html_unescape(text)
        text = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", text)
        text = (text
                .replace("\u201c", '"').replace("\u201d", '"')
                .replace("\u2018", "'").replace("\u2019", "'"))
        return text

    # ═══════════════════════════════════════════════════════════════════════════
    # PLATFORM-SPECIFIC FETCHERS
    # ═══════════════════════════════════════════════════════════════════════════

    # ── Core fetchers (fallback chain) ────────────────────────────────────────

    @classmethod
    def _fetch_raw_html(cls, url: str) -> tuple[str, str, str]:
        """
        Pass 1: cloudscraper — handles Cloudflare JS challenge + gov.ph SSL.
        Falls back to bare urllib if cloudscraper is not installed.

        cloudscraper works by:
          - Mimicking a real browser's TLS fingerprint (JA3 hash)
          - Automatically solving Cloudflare's JS challenge (cf_clearance cookie)
          - Rotating User-Agents per request
        """
        ua = random.choice(_USER_AGENTS)

        if _CLOUDSCRAPER_AVAILABLE:
            try:
                scraper = cloudscraper.create_scraper(
                    browser={"browser": "chrome", "platform": "windows", "mobile": False}
                )
                # gov.ph SSL cert issues — same fix as live_search.py
                is_gov = ".gov.ph" in url or ".gov.ph" in urlparse(url).netloc
                resp = scraper.get(
                    url,
                    headers={"User-Agent": ua, "Accept-Language": "en-US,en;q=0.9"},
                    timeout=cls.TIMEOUT,
                    allow_redirects=True,
                    verify=(not is_gov),   # skip SSL verify for gov.ph
                )
                if resp.status_code in (403, 429):
                    # cloudscraper failed CF — escalate to Playwright
                    raise ValueError(f"HTTP {resp.status_code} even after CF solve")
                resp.raise_for_status()
                content_type = resp.headers.get("Content-Type", "")
                charset      = cls._detect_charset(content_type)
                raw          = resp.content.decode(charset, errors="replace")
                return raw, resp.url, content_type
            except Exception as e:
                logger.debug(f"[URLFetcher] cloudscraper failed ({e}) — falling back to urllib")

        # urllib fallback (no CF bypass)
        req = urllib.request.Request(url, headers={
            "User-Agent":      ua,
            "Accept":          "text/html,application/xhtml+xml,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "identity",
        })
        with urllib.request.urlopen(req, timeout=cls.TIMEOUT) as resp:
            content_type = resp.headers.get("Content-Type", "")
            final_url    = resp.url
            charset      = cls._detect_charset(content_type)
            raw          = resp.read(cls.MAX_BYTES).decode(charset, errors="replace")
        return raw, final_url, content_type

    @classmethod
    def _fetch_with_playwright_stealth(cls, url: str) -> str:
        """
        Pass 2: Playwright + stealth patch.

        playwright-stealth patches these Cloudflare detection signals:
          - navigator.webdriver  → set to undefined (normal browsers don't have this)
          - chrome runtime       → injected to look like a real Chrome install
          - permissions API      → spoofed
          - language/timezone    → set to match locale

        Use this when cloudscraper passes the CF challenge but the page
        still needs full JS rendering (React/Vue SPAs, lazy-loaded content).
        """
        if not _PLAYWRIGHT_AVAILABLE:
            return ""
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(
                    user_agent=random.choice(_USER_AGENTS),
                    locale="en-PH",
                    viewport={"width": 1280, "height": 800},
                )
                page = context.new_page()

                # Apply stealth patches BEFORE navigating — order matters
                if _STEALTH_AVAILABLE:
                    stealth_sync(page)
                else:
                    # Manual minimal stealth if playwright-stealth not installed
                    page.add_init_script("""
                        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                        Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3]});
                        window.chrome = {runtime: {}};
                    """)

                # Block images/fonts/CSS to speed up load
                page.route("**/*.{png,jpg,jpeg,gif,webp,svg,woff,woff2,ttf,css}",
                           lambda route: route.abort())

                page.goto(url, timeout=25_000, wait_until="domcontentloaded")
                page.wait_for_timeout(2500)   # let JS + CF challenge settle

                # If CF challenge page — wait for it to auto-solve (up to 8s)
                if "just a moment" in page.title().lower():
                    logger.info(f"[URLFetcher] CF challenge detected — waiting for auto-solve")
                    page.wait_for_timeout(8000)

                raw = page.content()
                browser.close()
            return raw
        except Exception as e:
            logger.warning(f"[URLFetcher] Playwright-stealth failed for {url}: {e}")
            return ""

    @classmethod
    def _fetch_with_scraperapi(cls, url: str) -> tuple[str, str]:
        """
        Pass 3: ScraperAPI — rotating residential IPs, handles any block.
        Already in your .env.example as SCRAPER_API_KEY.
        1,000 free requests/month. $29/month for 250k requests.

        ScraperAPI routes your request through real residential IP addresses
        that are not flagged as bots, bypassing both Cloudflare and
        IP-based rate limiting. It also handles JS rendering server-side.
        """
        if not SCRAPER_API_KEY:
            return "", ""
        try:
            import requests as req_lib
            api_url = (
                f"http://api.scraperapi.com"
                f"?api_key={SCRAPER_API_KEY}"
                f"&url={quote_plus(url)}"
                f"&country_code=ph"      # route through PH IPs for gov.ph
                f"&render=true"          # JS rendering enabled
            )
            resp = req_lib.get(api_url, timeout=45)
            if resp.status_code != 200:
                return "", ""
            content_type = resp.headers.get("Content-Type", "text/html")
            raw = resp.content.decode("utf-8", errors="replace")
            logger.info(f"[URLFetcher] ScraperAPI succeeded for {url}")
            return raw, content_type
        except Exception as e:
            logger.warning(f"[URLFetcher] ScraperAPI failed: {e}")
            return "", ""

    # ── Reddit ────────────────────────────────────────────────────────────────

    @classmethod
    def _fetch_reddit(cls, url: str) -> dict:
        """
        Reddit exposes full post JSON at <post_url>.json
        No API key needed. Works for public posts and comments.
        """
        # Normalise: strip query params, add .json
        parsed   = urlparse(url)
        clean    = f"https://www.reddit.com{parsed.path.rstrip('/')}.json?limit=10"
        try:
            req = urllib.request.Request(clean, headers={
                "User-Agent": "SocialProofBot/1.0 (fact-checking tool; admin@socialproof.ph)",
                "Accept":     "application/json",
            })
            with urllib.request.urlopen(req, timeout=cls.TIMEOUT) as resp:
                data = json.loads(resp.read())

            # Reddit JSON: [post_listing, comments_listing]
            post  = data[0]["data"]["children"][0]["data"]
            title = post.get("title", "")
            body  = post.get("selftext", "") or ""
            sub   = post.get("subreddit_name_prefixed", "")

            # Also grab top 3 comments for context
            comments = []
            for child in data[1]["data"]["children"][:3]:
                cdata = child.get("data", {})
                cbody = cdata.get("body", "")
                if cbody and cbody not in ("[deleted]", "[removed]") and len(cbody) > 20:
                    comments.append(cbody)

            full_text = f"[{sub}] {title}"
            if body:
                full_text += f"\n\n{body}"
            if comments:
                full_text += "\n\nTop comments:\n" + "\n".join(f"- {c}" for c in comments)

            return {
                "text":               cls._smart_truncate(cls._normalise(full_text)),
                "title":              title,
                "url":                url,
                "error":              None,
                "source_type":        "reddit_json",
                "wayback_used":       False,
                "shortener_expanded": False,
            }
        except Exception as e:
            logger.warning(f"[URLFetcher] Reddit JSON fetch failed: {e}")
            # Fall through to OG fallback
            return cls._og_only_fallback(url, "reddit")

    # ── YouTube ───────────────────────────────────────────────────────────────

    @classmethod
    def _fetch_youtube(cls, url: str) -> dict:
        """
        Uses YouTube oEmbed API — completely free, no key needed.
        Returns video title + author. For description, falls back to page OG.
        """
        oembed_url = (
            "https://www.youtube.com/oembed?"
            + urlencode({"url": url, "format": "json"})
        )
        try:
            req = urllib.request.Request(oembed_url, headers={"User-Agent": cls.USER_AGENT})
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read())

            title      = data.get("title", "")
            author     = data.get("author_name", "")
            oembed_text = f"YouTube video: {title} by {author}."

            # Also try page OG for description
            try:
                raw, final_url, _ = cls._fetch_raw_html(url)
                og_text = cls._extract_og(raw)
                if og_text and len(og_text) > len(oembed_text):
                    oembed_text = og_text
            except Exception:
                pass

            return {
                "text":               cls._normalise(oembed_text),
                "title":              title,
                "url":                url,
                "error":              None,
                "source_type":        "youtube_oembed",
                "wayback_used":       False,
                "shortener_expanded": False,
            }
        except Exception as e:
            logger.warning(f"[URLFetcher] YouTube oEmbed failed: {e}")
            return cls._og_only_fallback(url, "youtube")

    # ── Twitter/X via Nitter ──────────────────────────────────────────────────

    @classmethod
    def _fetch_twitter(cls, url: str) -> dict:
        """
        Attempts to fetch the tweet via a nitter.net mirror (plain HTML, no JS).
        Falls back to OG metadata if nitter is unavailable.

        Nitter is a free open-source Twitter frontend.
        Set NITTER_INSTANCE in .env if nitter.net is down.
        """
        try:
            # Convert twitter.com/x.com URL to nitter URL
            nitter_url = re.sub(
                r"https?://(www\.)?(twitter\.com|x\.com)",
                NITTER_INSTANCE,
                url,
            )
            raw, _, content_type = cls._fetch_raw_html(nitter_url)

            if "text" not in content_type and "html" not in content_type:
                raise ValueError("Non-HTML content from nitter")

            # Nitter wraps tweet content in <div class="tweet-content">
            tweet_content = re.search(
                r'<div[^>]+class="[^"]*tweet-content[^"]*"[^>]*>(.*?)</div>',
                raw, re.IGNORECASE | re.DOTALL,
            )
            if tweet_content:
                text = cls._normalise(cls.ALL_TAGS.sub(" ", tweet_content.group(1)))
                if not cls._is_low_quality(text):
                    logger.info(f"[URLFetcher] Nitter extracted tweet content ({len(text)} chars)")
                    return {
                        "text":               cls._smart_truncate(text),
                        "title":              cls._extract_title(raw),
                        "url":                url,
                        "error":              None,
                        "source_type":        "twitter_nitter",
                        "wayback_used":       False,
                        "shortener_expanded": False,
                    }
        except Exception as e:
            logger.info(f"[URLFetcher] Nitter unavailable ({e}) — falling back to OG")

        # Fallback: OG metadata from twitter.com/x.com directly
        return cls._og_only_fallback(url, "twitter")

    # ── TikTok via oEmbed ────────────────────────────────────────────────────

    @classmethod
    def _fetch_tiktok(cls, url: str) -> dict:
        """
        TikTok has an official oEmbed endpoint that returns video title + author.
        No API key needed. Description not available via oEmbed.
        """
        oembed_url = (
            "https://www.tiktok.com/oembed?"
            + urlencode({"url": url})
        )
        try:
            req = urllib.request.Request(oembed_url, headers={"User-Agent": cls.USER_AGENT})
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read())
            title  = data.get("title", "")
            author = data.get("author_name", "")
            text   = f"TikTok video by @{author}: {title}"
            return {
                "text":               cls._normalise(text),
                "title":              title,
                "url":                url,
                "error":              None,
                "source_type":        "tiktok_oembed",
                "wayback_used":       False,
                "shortener_expanded": False,
            }
        except Exception as e:
            logger.warning(f"[URLFetcher] TikTok oEmbed failed: {e}")
            return cls._og_only_fallback(url, "tiktok")

    # ── Generic OG-only fallback ──────────────────────────────────────────────

    @classmethod
    def _og_only_fallback(cls, url: str, platform: str) -> dict:
        """Try to get at least OG metadata from any URL."""
        try:
            raw, final_url, _ = cls._fetch_raw_html(url)
            og   = cls._extract_og(raw)
            meta = cls._extract_meta_desc(raw)
            title = cls._extract_title(raw)
            text  = cls._normalise(og or meta or title)
            if text and not cls._is_low_quality(text):
                return {
                    "text":               cls._smart_truncate(text),
                    "title":              title,
                    "url":                final_url,
                    "error":              None,
                    "source_type":        f"{platform}_og_only",
                    "wayback_used":       False,
                    "shortener_expanded": False,
                }
        except Exception as e:
            logger.debug(f"[URLFetcher] OG fallback failed for {platform}: {e}")

        return {
            "text":         "",
            "title":        "",
            "url":          url,
            "error": (
                f"{platform.title()} URL detected. This platform restricts full content access. "
                "Please paste the post text directly for full analysis."
            ),
            "source_type":        f"{platform}_blocked",
            "wayback_used":       False,
            "shortener_expanded": False,
        }

    # ── Playwright (JS-heavy pages) ───────────────────────────────────────────

    @classmethod
    def _fetch_with_playwright(cls, url: str) -> str:
        """Alias — calls stealth version."""
        return cls._fetch_with_playwright_stealth(url)

    # ── archive.ph fallback (paywalls) ────────────────────────────────────────

    @classmethod
    def _archive_ph_fallback(cls, url: str) -> dict:
        """
        Try archive.ph (formerly archive.is) for paywalled or blocked articles.
        archive.ph caches content without paywalls for many major outlets.
        """
        try:
            # archive.ph/newest/<url> redirects to the latest saved snapshot
            archive_url = f"https://archive.ph/newest/{quote_plus(url)}"
            raw, final_url, content_type = cls._fetch_raw_html(archive_url)
            if "text" not in content_type:
                raise ValueError("non-HTML from archive.ph")

            text = cls._trafilatura_extract(raw, final_url)
            if cls._is_low_quality(text):
                text = cls._regex_extract(raw)
            text = cls._normalise(text)

            if not cls._is_low_quality(text):
                logger.info(f"[URLFetcher] archive.ph snapshot used for {url}")
                return {
                    "text":               cls._smart_truncate(text),
                    "title":              cls._extract_title(raw),
                    "url":                url,
                    "error":              None,
                    "source_type":        "archive_ph",
                    "wayback_used":       False,
                    "shortener_expanded": False,
                }
        except Exception as e:
            logger.debug(f"[URLFetcher] archive.ph failed: {e}")

        return {}   # empty dict signals: try Wayback next

    # ── Wayback Machine fallback ──────────────────────────────────────────────

    @classmethod
    def _wayback_fallback(cls, url: str) -> dict:
        try:
            api_url = f"https://archive.org/wayback/available?url={url}"
            with urllib.request.urlopen(api_url, timeout=8) as r:
                data = json.loads(r.read())
            snapshot = data.get("archived_snapshots", {}).get("closest", {})
            if snapshot.get("available") and snapshot.get("url"):
                archive_url = snapshot["url"]
                logger.info(f"[URLFetcher] Wayback Machine snapshot: {archive_url}")
                result = cls.fetch(archive_url)
                result["wayback_used"] = True
                result["url"]          = url
                return result
        except Exception as exc:
            logger.debug(f"[URLFetcher] Wayback lookup failed: {exc}")

        return {
            "text": "", "title": "", "url": url,
            "error":        "Page unavailable and no archive snapshot found.",
            "source_type":  "unavailable",
            "wayback_used": False, "shortener_expanded": False,
        }

    # ═══════════════════════════════════════════════════════════════════════════
    # MAIN FETCH — entry point
    # ═══════════════════════════════════════════════════════════════════════════

    @classmethod
    def fetch(cls, url: str) -> dict:
        """
        Returns:
            {
                "text":               str,   # extracted plain text
                "title":              str,   # page title
                "url":                str,   # FINAL URL after redirects
                "error":              str | None,
                "source_type":        str,   # full | social_og_only | reddit_json |
                                             # youtube_oembed | twitter_nitter |
                                             # tiktok_oembed | archive_ph | wayback | ...
                "wayback_used":       bool,
                "shortener_expanded": bool,
            }

        Always pass result["url"] to SourceCredibilityModule.evaluate()
        so that shortener-expanded domains are scored correctly.
        """
        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        # Security fix: block SSRF — private/loopback/link-local addresses
        if _is_ssrf_blocked(url):
            logger.warning(f"[URLFetcher] SSRF blocked: {url}")
            return {
                "text": "", "title": "", "url": url,
                "error": "Blocked: URL resolves to a private or reserved address.",
                "source_type": "error", "wayback_used": False,
                "shortener_expanded": False,
            }

        shortener_expanded = cls._domain_in(url, _SHORTENER_DOMAINS)
        if shortener_expanded:
            logger.info(f"[URLFetcher] Shortener detected: {url}")

        # ── Route to platform-specific fetchers ───────────────────────────────
        if cls._domain_in(url, _REDDIT_DOMAINS):
            return cls._fetch_reddit(url)

        if cls._domain_in(url, _YOUTUBE_DOMAINS):
            return cls._fetch_youtube(url)

        if cls._domain_in(url, {"twitter.com", "x.com"}):
            return cls._fetch_twitter(url)

        if cls._domain_in(url, {"tiktok.com"}):
            return cls._fetch_tiktok(url)

        if cls._domain_in(url, _SOCIAL_DOMAINS):
            # Facebook, Instagram, LinkedIn, Threads — OG only
            return cls._og_only_fallback(url, cls._netloc(url).split(".")[0])

        # ── General fetch ─────────────────────────────────────────────────────
        use_playwright_first = cls._domain_in(url, _JS_HEAVY_DOMAINS)

        try:
            raw, final_url, content_type = cls._fetch_raw_html(url)
        except urllib.error.HTTPError as e:
            # Paywalls / gone pages — try archive.ph then Wayback
            if e.code in _ARCHIVE_PH_TRIGGER_CODES:
                logger.info(f"[URLFetcher] HTTP {e.code} — trying archive.ph for {url}")
                result = cls._archive_ph_fallback(url)
                if result:
                    result["shortener_expanded"] = shortener_expanded
                    return result
            if e.code in _WAYBACK_TRIGGER_CODES:
                logger.info(f"[URLFetcher] Trying Wayback Machine for {url}")
                result = cls._wayback_fallback(url)
                result["shortener_expanded"] = shortener_expanded
                return result
            return {
                "text": "", "title": "", "url": url,
                "error": f"HTTP {e.code}: {e.reason}",
                "source_type": "error", "wayback_used": False,
                "shortener_expanded": shortener_expanded,
            }
        except Exception as e:
            return {
                "text": "", "title": "", "url": url,
                "error": f"Fetch error: {type(e).__name__}: {e}",
                "source_type": "error", "wayback_used": False,
                "shortener_expanded": shortener_expanded,
            }

        if "text" not in content_type and "html" not in content_type:
            return {
                "text": "", "title": "", "url": final_url,
                "error": f"Non-text content type: {content_type}",
                "source_type": "rejected", "wayback_used": False,
                "shortener_expanded": shortener_expanded,
            }

        # ── Metadata ──────────────────────────────────────────────────────────
        title     = cls._extract_title(raw)
        meta_desc = cls._extract_meta_desc(raw)
        og_text   = cls._extract_og(raw)

        # ── Extraction pass 1: trafilatura ────────────────────────────────────
        text = "" if use_playwright_first else cls._trafilatura_extract(raw, final_url)

        # ── Extraction pass 2: Playwright stealth (JS-heavy or CF blocked) ───
        if cls._is_low_quality(text) and _PLAYWRIGHT_AVAILABLE:
            if use_playwright_first or cls._is_low_quality(cls._regex_extract(raw)):
                logger.info(f"[URLFetcher] Using Playwright-stealth for {final_url}")
                pw_html = cls._fetch_with_playwright_stealth(final_url)
                if pw_html and not cls._is_low_quality(pw_html):
                    text = cls._trafilatura_extract(pw_html, final_url)
                    if cls._is_low_quality(text):
                        text = cls._regex_extract(pw_html)

        # ── Extraction pass 3: ScraperAPI (residential IPs, last resort) ─────
        if cls._is_low_quality(text) and SCRAPER_API_KEY:
            logger.info(f"[URLFetcher] Using ScraperAPI for {final_url}")
            sa_html, sa_ct = cls._fetch_with_scraperapi(final_url)
            if sa_html:
                text = cls._trafilatura_extract(sa_html, final_url)
                if cls._is_low_quality(text):
                    text = cls._regex_extract(sa_html)

        # ── Extraction pass 4: regex chain ────────────────────────────────────
        if cls._is_low_quality(text):
            text = cls._regex_extract(raw)

        # ── Normalise ─────────────────────────────────────────────────────────
        text = cls._normalise(text)

        # ── Extraction pass 5: metadata fallback ──────────────────────────────
        if cls._is_low_quality(text):
            fallback = cls._normalise(" ".join(filter(None, [og_text, meta_desc, title])))
            if fallback:
                logger.info(
                    f"[URLFetcher] Body low quality — using metadata fallback "
                    f"({len(fallback)} chars)"
                )
                text = fallback

        logger.info(
            f"[URLFetcher] {final_url} → {len(text)} chars"
            + (" [shortener expanded]" if shortener_expanded else "")
            + (" [playwright]"         if use_playwright_first else "")
        )

        return {
            "text":               cls._smart_truncate(text),
            "title":              title,
            "url":                final_url,
            "error":              None,
            "source_type":        "full",
            "wayback_used":       False,
            "shortener_expanded": shortener_expanded,
        }
