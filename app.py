import os
import uuid
import json
import sqlite3
import datetime
import logging
import threading
import re
import base64
from pathlib import Path
from flask import Flask, request, jsonify, render_template
import feedparser
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv

load_dotenv()

# ── App setup ─────────────────────────────────────────────────────────────────
app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DB_PATH = os.environ.get('DB_PATH', 'mir_studio.db')
API_SECRET = os.environ.get('API_SECRET_KEY', 'mir-studio-secret-2026')


# ── Database ──────────────────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS content_items (
                id TEXT PRIMARY KEY,
                platforms TEXT,
                raw_thought TEXT,
                linkedin_content TEXT,
                newsletter_content TEXT,
                instagram_script TEXT,
                image_url TEXT,
                status TEXT DEFAULT 'draft',
                rating INTEGER DEFAULT 0,
                posted_at TEXT,
                created_at TEXT
            );

            CREATE TABLE IF NOT EXISTS intel_cache (
                id TEXT PRIMARY KEY,
                source TEXT,
                title TEXT,
                url TEXT,
                summary TEXT,
                category TEXT,
                cached_at TEXT
            );

            CREATE TABLE IF NOT EXISTS intel_analysis (
                id TEXT PRIMARY KEY,
                debates TEXT,
                gaps TEXT,
                trending TEXT,
                suggested_angles TEXT,
                instagram_insights TEXT,
                generated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS kb_voice_profile (
                id TEXT PRIMARY KEY,
                key TEXT UNIQUE,
                value TEXT,
                updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS kb_topics_covered (
                id TEXT PRIMARY KEY,
                topic TEXT,
                platform TEXT,
                times_posted INTEGER DEFAULT 1,
                last_posted_at TEXT
            );

            CREATE TABLE IF NOT EXISTS kb_story_bank (
                id TEXT PRIMARY KEY,
                title TEXT,
                story_snippet TEXT,
                platform TEXT,
                tags TEXT,
                times_used INTEGER DEFAULT 0,
                created_at TEXT
            );

            CREATE TABLE IF NOT EXISTS kb_raw_ideas (
                id TEXT PRIMARY KEY,
                thought TEXT,
                source TEXT,
                platforms_intended TEXT,
                converted_to TEXT,
                tags TEXT,
                created_at TEXT
            );

            CREATE TABLE IF NOT EXISTS app_stats (
                id INTEGER PRIMARY KEY,
                streak_count INTEGER DEFAULT 0,
                last_posted_date TEXT,
                total_linkedin INTEGER DEFAULT 0,
                total_newsletter INTEGER DEFAULT 0,
                total_instagram INTEGER DEFAULT 0,
                last_intel_refresh TEXT
            );

            CREATE TABLE IF NOT EXISTS kb_discard_log (
                id TEXT PRIMARY KEY,
                item_id TEXT,
                platform TEXT,
                raw_thought TEXT,
                discard_reason TEXT,
                pattern_notes TEXT,
                discarded_at TEXT
            );

            CREATE TABLE IF NOT EXISTS instagram_reel_analytics (
                id TEXT PRIMARY KEY,
                reel_url TEXT,
                caption_preview TEXT,
                views INTEGER DEFAULT 0,
                likes INTEGER DEFAULT 0,
                saves INTEGER DEFAULT 0,
                shares INTEGER DEFAULT 0,
                reach INTEGER DEFAULT 0,
                comments INTEGER DEFAULT 0,
                hook_type TEXT,
                performance_tier TEXT,
                collected_at TEXT
            );

            CREATE TABLE IF NOT EXISTS tracked_creators (
                id TEXT PRIMARY KEY,
                platform TEXT NOT NULL,
                handle TEXT NOT NULL,
                profile_url TEXT,
                niche TEXT,
                added_at TEXT,
                UNIQUE(platform, handle)
            );

            CREATE TABLE IF NOT EXISTS intel_run_log (
                id TEXT PRIMARY KEY,
                run_at TEXT,
                source_name TEXT,
                source_type TEXT,
                articles_found INTEGER DEFAULT 0,
                success INTEGER DEFAULT 1,
                error_msg TEXT
            );
        """)

        # Initialize app_stats row if missing
        stats = conn.execute("SELECT id FROM app_stats WHERE id = 1").fetchone()
        if not stats:
            conn.execute(
                "INSERT INTO app_stats (id, streak_count, total_linkedin, total_newsletter, total_instagram) "
                "VALUES (1, 0, 0, 0, 0)"
            )
        conn.commit()
    logger.info("Database initialized.")


# ── Knowledge Base helpers ────────────────────────────────────────────────────
def build_voice_context():
    """Assembles Claude system prompt dynamically from KB — richer every week."""
    try:
        with get_db() as conn:
            rows = conn.execute("SELECT key, value FROM kb_voice_profile").fetchall()
            profile = {r['key']: r['value'] for r in rows}

            fourteen_ago = (datetime.datetime.now() - datetime.timedelta(days=14)).isoformat()
            recent_topics = conn.execute(
                "SELECT topic, platform FROM kb_topics_covered WHERE last_posted_at > ? "
                "ORDER BY last_posted_at DESC LIMIT 10",
                (fourteen_ago,)
            ).fetchall()

            ig_insight_row = conn.execute(
                "SELECT instagram_insights FROM intel_analysis ORDER BY generated_at DESC LIMIT 1"
            ).fetchone()

        parts = []

        if profile:
            parts.append(f"""You are a ghostwriter for Mir Tahmid Ali. Write exactly in his voice.

IDENTITY: {profile.get('identity', 'Community-first systems architect designing autonomy across geography, income, and impact')}

SIGNATURE ANGLE: {profile.get('signature_angle', 'Empathy + systems thinking. Most people are one or the other.')}

WRITING STYLE:
{profile.get('writing_style', 'Short declarative sentences. Each on its own line. Real numbers. Real examples. No fluff.')}

TONE:
{profile.get('tone', 'Emotional but grounded. Authentic. No corporate speak. No performative vulnerability.')}

NEVER USE: {profile.get('never_say', 'leverage, synergy, circle back, game changer, unlock, I am excited to share, thrilled to announce')}

NEVER START A POST WITH: {profile.get('never_start_with', 'I  or  In todays world')}

LINKEDIN STRUCTURE: {profile.get('linkedin_structure', 'Hook → Emotional layer → Core insight → Question or soft CTA')}

INSTAGRAM STRUCTURE: {profile.get('instagram_structure', 'Visual hook → Scroll stop → Relatable angle → Value loop')}""")
        else:
            parts.append(
                "You are ghostwriting for Mir Tahmid Ali, a community builder and B2B creator economy expert. "
                "Write in short declarative sentences. Real numbers only. No corporate speak. Never start with 'I'."
            )

        if recent_topics:
            topics_str = ', '.join([f"{r['topic']} ({r['platform']})" for r in recent_topics])
            parts.append(f"\nRECENT TOPICS COVERED — avoid repeating these: {topics_str}")

        if ig_insight_row and ig_insight_row['instagram_insights']:
            parts.append(f"\nINSTAGRAM PERFORMANCE INSIGHTS (apply to reel scripts): {ig_insight_row['instagram_insights']}")

        # Pull recent discards — patterns Mir rejected
        try:
            with get_db() as conn:
                week_ago = (datetime.datetime.now() - datetime.timedelta(days=30)).isoformat()
                discards = conn.execute(
                    "SELECT platform, pattern_notes FROM kb_discard_log WHERE discarded_at > ? "
                    "AND pattern_notes IS NOT NULL AND pattern_notes != '' ORDER BY discarded_at DESC LIMIT 10",
                    (week_ago,)
                ).fetchall()
            if discards:
                discard_lines = '; '.join([f"{d['platform']}: {d['pattern_notes']}" for d in discards])
                parts.append(f"\nPATTERNS MIR REJECTED (avoid these): {discard_lines}")
        except Exception:
            pass

        return '\n'.join(parts)

    except Exception as e:
        logger.error(f"build_voice_context error: {e}")
        return (
            "You are ghostwriting for Mir Tahmid Ali. Short sentences. Real numbers. "
            "No corporate speak. Never start with 'I'."
        )


def get_story_context(story_id):
    try:
        with get_db() as conn:
            story = conn.execute("SELECT * FROM kb_story_bank WHERE id = ?", (story_id,)).fetchone()
            if story:
                conn.execute("UPDATE kb_story_bank SET times_used = times_used + 1 WHERE id = ?", (story_id,))
                conn.commit()
                return f"PERSONAL STORY TO WEAVE IN:\nTitle: {story['title']}\n{story['story_snippet']}"
    except Exception as e:
        logger.error(f"get_story_context error: {e}")
    return ""


# ── RSS Intelligence Pipeline ─────────────────────────────────────────────────
# ── Intel sources: 7-day filtered, LinkedIn + Instagram focused ───────────────
# Google News &tbs=qdr:w = past week. &hl=en-US for English results.
RSS_SOURCES = [
    # LinkedIn domain — broad queries that consistently return news articles
    ("Creator Economy News",       "https://news.google.com/rss/search?q=creator+economy+monetization&hl=en-US&gl=US&ceid=US:en", "linkedin_posts"),
    ("B2B Influencer Marketing",   "https://news.google.com/rss/search?q=B2B+influencer+marketing+strategy&hl=en-US&gl=US&ceid=US:en", "linkedin_posts"),
    ("Community Building",         "https://news.google.com/rss/search?q=community+building+business+growth&hl=en-US&gl=US&ceid=US:en", "linkedin_posts"),
    ("Content Marketing LinkedIn", "https://news.google.com/rss/search?q=content+marketing+personal+brand+LinkedIn&hl=en-US&gl=US&ceid=US:en", "linkedin_posts"),
    ("Creator Monetization",       "https://news.google.com/rss/search?q=creator+monetization+brand+deals+2026&hl=en-US&gl=US&ceid=US:en", "linkedin_posts"),
    # Instagram domain — broad travel/nomad queries
    ("Digital Nomad News",         "https://news.google.com/rss/search?q=digital+nomad+remote+work+2026&hl=en-US&gl=US&ceid=US:en", "instagram_posts"),
    ("Slow Travel Trends",         "https://news.google.com/rss/search?q=slow+travel+Southeast+Asia+backpacking&hl=en-US&gl=US&ceid=US:en", "instagram_posts"),
    ("Indian Travel Passport",     "https://news.google.com/rss/search?q=Indian+passport+travel+visa+abroad&hl=en-US&gl=US&ceid=US:en", "instagram_posts"),
    ("Remote Work Lifestyle",      "https://news.google.com/rss/search?q=remote+work+location+independent+nomad&hl=en-US&gl=US&ceid=US:en", "instagram_posts"),
    # Industry feeds (real blogs, fast-updating)
    ("Social Media Examiner",      "https://www.socialmediaexaminer.com/feed/",  "linkedin_posts"),
    ("Buffer Blog",                "https://buffer.com/resources/feed/",         "linkedin_posts"),
    ("Nomadic Matt Blog",          "https://www.nomadicmatt.com/travel-blog/feed/", "instagram_posts"),
]


def get_tracked_handles(platform):
    """Return list of handles from tracked_creators table for a given platform."""
    try:
        with get_db() as conn:
            rows = conn.execute(
                "SELECT handle FROM tracked_creators WHERE platform = ?", (platform,)
            ).fetchall()
        return [r['handle'] for r in rows]
    except Exception:
        return []


def _outlier_score(items, score_fn):
    """Detect outliers: items scoring above mean + 2*stdev. Returns (scored_items, threshold)."""
    import statistics
    scored = [(score_fn(i), i) for i in items]
    vals = [s for s, _ in scored]
    if not vals:
        return scored, 0
    mean_s = statistics.mean(vals)
    stdev_s = statistics.stdev(vals) if len(vals) > 1 else 0
    return scored, mean_s + (2.0 * stdev_s)


# ── Topic keyword lists (hard-separated by domain) ────────────────────────────
# LinkedIn: B2B / creator-economy / community topics
_LINKEDIN_KEYWORDS = [
    "creator economy",
    "B2B influencer marketing",
    "community management",
    "community building",
    "sales and marketing",
    "content marketing strategy",
]

# Instagram: travel / nomad / remote-work topics (hashtags for Apify, subreddits for Reddit)
_INSTAGRAM_HASHTAGS = [
    "digitalnomad",
    "remotework",
    "slowtravel",
    "travelcontentcreator",
    "indiantraveller",
    "nomadlife",
]

# Reddit fallback subreddits — strictly separated
_LINKEDIN_SUBREDDITS = ["marketing", "b2bmarketing", "sales", "entrepreneur", "socialmediamarketing", "content_marketing"]
_INSTAGRAM_SUBREDDITS = ["digitalnomad", "remotework", "solotravel", "travel", "IndiaTravel", "backpacking"]


def fetch_linkedin_apify():
    """LinkedIn keyword search via Apify — real posts ranked by engagement.
    Searches each of Mir's LinkedIn topic keywords, scores posts, flags outliers."""
    token = os.environ.get('APIFY_TOKEN')
    if not token:
        return [], False  # (articles, apify_used)
    try:
        from apify_client import ApifyClient
        apify = ApifyClient(token)
        all_items = []
        for keyword in _LINKEDIN_KEYWORDS:
            try:
                # apimaestro/linkedin-posts-search-scraper-no-cookies — no auth required, 4.5★
                run = apify.actor("apimaestro/linkedin-posts-search-scraper-no-cookies").call(run_input={
                    "keyword": keyword,
                    "date_filter": "past-week",
                    "sort_type": "date_posted",
                    "total_posts": 8,
                }, timeout_secs=90)
                items = list(apify.dataset(run["defaultDatasetId"]).iterate_items())
                for item in items:
                    item['_keyword'] = keyword  # tag so we know which topic surfaced it
                all_items.extend(items)
            except Exception as e:
                logger.warning(f"Apify LinkedIn keyword '{keyword}' error: {e}")
                continue
        if not all_items:
            return [], True  # Apify was used but returned nothing

        def li_score(i):
            return (i.get('totalReactionCount') or i.get('reactionCount') or i.get('likeCount') or 0) + \
                   3 * (i.get('commentsCount') or i.get('commentCount') or 0) + \
                   2 * (i.get('repostsCount') or i.get('reshareCount') or i.get('shareCount') or 0)

        scored, threshold = _outlier_score(all_items, li_score)
        articles = []
        for score, item in scored:
            reactions = item.get('totalReactionCount') or item.get('reactionCount') or item.get('likeCount') or 0
            comments = item.get('commentsCount') or item.get('commentCount') or 0
            reposts = item.get('repostsCount') or item.get('reshareCount') or item.get('shareCount') or 0
            # Handle nested author object or flat field
            author_obj = item.get('author') or {}
            author = (item.get('authorName') or
                      author_obj.get('name') or author_obj.get('fullName') or
                      item.get('actorName') or 'LinkedIn user')
            text = (item.get('text') or item.get('commentary') or item.get('content') or '')[:300]
            keyword = item.get('_keyword', 'linkedin')
            is_outlier = score > threshold
            articles.append({
                'id': str(uuid.uuid4()),
                'source': f"LinkedIn — {keyword}{' ⚡OUTLIER' if is_outlier else ''}",
                'title': text[:80] or f"Post by {author}",
                'url': item.get('url') or item.get('postUrl') or item.get('link', ''),
                'summary': (
                    f"{'⚡ OUTLIER — ' if is_outlier else ''}"
                    f"Keyword: '{keyword}' | Eng score: {int(score)} | "
                    f"{reactions} reactions, {comments} comments, {reposts} reposts | "
                    f"Author: {author} | Post: {text[:200]}"
                ),
                'category': 'linkedin_posts',
                'cached_at': datetime.datetime.now().isoformat(),
            })
        outlier_count = sum(1 for s, _ in scored if s > threshold)
        logger.info(f"Apify LinkedIn keyword search: {len(articles)} posts across {len(_LINKEDIN_KEYWORDS)} keywords, {outlier_count} outliers")
        return articles, True
    except ImportError:
        logger.warning("apify-client not installed")
        return [], False
    except Exception as e:
        logger.error(f"Apify LinkedIn error: {e}")
        return [], False


def fetch_instagram(hashtags=None):
    """Unified Instagram fetcher. Priority: Instaloader (free) → Apify.

    Instaloader scrapes public hashtag pages directly — no API key needed.
    Apify is the fallback using residential proxies for reliability.
    Returns (articles, method_used) where method_used is 'instaloader'|'apify'|'none'.
    """
    if hashtags is None:
        hashtags = _INSTAGRAM_HASHTAGS

    # 1. Instaloader — free, no API key, works for public hashtags
    posts = _fetch_instagram_instaloader(hashtags)
    if posts:
        return posts, 'instaloader'

    logger.warning("Instaloader Instagram returned 0 posts — trying Apify")

    # 2. Apify fallback
    posts, used = _fetch_instagram_apify_raw(hashtags)
    return posts, ('apify' if used else 'none')


def _fetch_instagram_instaloader(hashtags):
    """Scrape Instagram hashtags via Instaloader (no API key needed for public content)."""
    try:
        import instaloader
        from itertools import islice as _islice
    except ImportError:
        logger.warning("instaloader not installed")
        return []

    L = instaloader.Instaloader(
        download_pictures=False,
        download_videos=False,
        download_video_thumbnails=False,
        save_metadata=False,
        compress_json=False,
        quiet=True,
        request_timeout=30,
    )

    # Optional: login for better rate limits and more data
    ig_user = os.environ.get('INSTAGRAM_USERNAME', '').strip()
    ig_pass = os.environ.get('INSTAGRAM_PASSWORD', '').strip()
    if ig_user and ig_pass:
        try:
            L.login(ig_user, ig_pass)
            logger.info(f"Instaloader: logged in as @{ig_user}")
        except Exception as e:
            logger.warning(f"Instaloader login failed: {e} — continuing without login")

    articles = []
    for tag in hashtags:
        try:
            hashtag_obj = instaloader.Hashtag.from_name(L.context, tag)
            for post in _islice(hashtag_obj.get_posts(), 8):
                caption = (post.caption or '')[:300].strip()
                likes = post.likes or 0
                comments = post.comments or 0
                views = (post.video_view_count or 0) if post.is_video else 0
                owner = post.owner_username or 'unknown'
                eng_score = likes + 3 * comments + 0.1 * views
                summary = (
                    f"Eng score: {int(eng_score)} | {likes:,} likes, {comments:,} comments"
                    + (f", {views:,} views" if views else "")
                    + f" | @{owner} | {caption[:180]}"
                )
                articles.append({
                    'id': str(uuid.uuid4()),
                    'source': f"Instagram #{tag}",
                    'title': caption[:80] or f"Post by @{owner}",
                    'url': f"https://www.instagram.com/p/{post.shortcode}/",
                    'summary': summary,
                    'category': 'instagram_posts',
                    'cached_at': datetime.datetime.now().isoformat(),
                    'score': int(eng_score),
                })
        except Exception as e:
            logger.warning(f"Instaloader #{tag} error: {e}")

    logger.info(f"Instaloader Instagram: {len(articles)} posts from {len(hashtags)} hashtags")
    return articles


def _fetch_instagram_apify_raw(hashtags):
    """Internal: Apify Instagram hashtag scraper (used as fallback). Returns (articles, used)."""
    token = os.environ.get('APIFY_TOKEN')
    if not token:
        return [], False
    try:
        from apify_client import ApifyClient
        apify = ApifyClient(token)
        # Use dedicated hashtag scraper — more reliable than generic instagram-scraper
        run = apify.actor("apify/instagram-hashtag-scraper").call(run_input={
            "hashtags": hashtags,      # list of strings without #
            "resultsType": "posts",
            "resultsLimit": 10,
        }, timeout_secs=120)
        items = list(apify.dataset(run["defaultDatasetId"]).iterate_items())
        if not items:
            return [], True

        def ig_score(i):
            return (i.get('likesCount') or 0) + \
                   3 * (i.get('commentsCount') or 0) + \
                   0.1 * (i.get('videoPlayCount') or i.get('videoViewCount') or 0)

        scored, threshold = _outlier_score(items, ig_score)
        articles = []
        for score, item in scored:
            likes = item.get('likesCount') or 0
            comments = item.get('commentsCount') or 0
            views = item.get('videoPlayCount') or item.get('videoViewCount') or 0
            owner = item.get('ownerUsername') or item.get('username', 'unknown')
            hashtags_used = ' '.join((item.get('hashtags') or [])[:5])
            caption = (item.get('caption') or item.get('text') or '')[:300]
            is_outlier = score > threshold
            articles.append({
                'id': str(uuid.uuid4()),
                'source': f"Instagram hashtag{' ⚡OUTLIER' if is_outlier else ''}",
                'title': caption[:80] or f"Post by @{owner}",
                'url': item.get('url') or item.get('shortCode') and f"https://www.instagram.com/p/{item['shortCode']}/" or '',
                'summary': (
                    f"{'⚡ OUTLIER — ' if is_outlier else ''}"
                    f"Eng score: {int(score)} | {likes} likes, {comments} comments, {views} views | "
                    f"@{owner} | Tags: {hashtags_used} | Caption: {caption[:180]}"
                ),
                'category': 'instagram_posts',
                'cached_at': datetime.datetime.now().isoformat(),
            })
        return articles, True
    except ImportError:
        return [], False
    except Exception as e:
        logger.error(f"Apify Instagram error: {e}")
        return [], False


def fetch_instagram_apify():
    """Legacy wrapper — kept for backward compat. Use fetch_instagram() instead."""
    posts, used = _fetch_instagram_apify_raw(_INSTAGRAM_HASHTAGS)
    return posts, used


def fetch_rss_fallback(queries_with_labels, platform_label):
    """Google News RSS fallback — used when Apify is unavailable.
    Takes a list of (label, search_query) tuples and fetches Google News RSS for each."""
    import time as _time
    articles = []
    cutoff_ts = _time.time() - (30 * 24 * 3600)
    for label, query in queries_with_labels:
        try:
            encoded = query.replace(' ', '+')
            url = f"https://news.google.com/rss/search?q={encoded}&hl=en-US&gl=US&ceid=US:en"
            feed = feedparser.parse(url)
            added = 0
            for entry in feed.entries[:5]:
                pub = getattr(entry, 'published_parsed', None) or getattr(entry, 'updated_parsed', None)
                if pub:
                    import time as _t
                    if _t.mktime(pub) < cutoff_ts:
                        continue
                title = getattr(entry, 'title', '').strip()
                if not title:
                    continue
                summary = re.sub('<[^<]+?>', '', getattr(entry, 'summary', ''))[:250]
                articles.append({
                    'id': str(uuid.uuid4()),
                    'source': f"Google News — {label}",
                    'title': title,
                    'url': getattr(entry, 'link', ''),
                    'summary': summary,
                    'category': platform_label,
                    'cached_at': datetime.datetime.now().isoformat(),
                })
                added += 1
                if added >= 3:
                    break
        except Exception as e:
            logger.warning(f"RSS fallback '{label}' error: {e}")
    logger.info(f"RSS fallback {platform_label}: {len(articles)} articles from {len(queries_with_labels)} queries")
    return articles


def fetch_reddit_intel(subreddits, platform_label):
    """Fetch Reddit posts. Priority: Apify → PRAW OAuth → RSS fallback.

    Apify uses residential proxies so it's never blocked from Railway.
    PRAW uses the official OAuth API (oauth.reddit.com, also not blocked).
    RSS is the last resort and may be rate-limited from datacenter IPs.
    """
    # 1. Apify reddit scraper — best option, uses residential proxies
    if os.environ.get('APIFY_TOKEN'):
        posts = _fetch_reddit_apify(subreddits, platform_label)
        if posts:
            return posts
        logger.warning("Apify Reddit returned 0 posts — trying next method")

    # 2. PRAW OAuth API — no proxy needed, official endpoint
    client_id = os.environ.get('REDDIT_CLIENT_ID', '').strip()
    client_secret = os.environ.get('REDDIT_CLIENT_SECRET', '').strip()
    if client_id and client_secret:
        posts = _fetch_reddit_praw(subreddits, platform_label, client_id, client_secret)
        if posts:
            return posts
        logger.warning("PRAW returned 0 posts — falling back to RSS")

    # 3. RSS fallback
    return _fetch_reddit_rss(subreddits, platform_label)


def _fetch_reddit_apify(subreddits, platform_label):
    """Fetch Reddit top posts via Apify actor (residential proxies, no Reddit creds needed)."""
    try:
        from apify_client import ApifyClient
        apify = ApifyClient(os.environ['APIFY_TOKEN'])
        articles = []
        for sub in subreddits:
            try:
                run = apify.actor("trudax/reddit-scraper").call(run_input={
                    "searches": [{
                        "type": "community",
                        "community": sub,
                        "sort": "top",
                        "time": "week",
                        "maxItems": 8,
                    }],
                    "maxItems": 8,
                }, timeout_secs=90)
                items = list(apify.dataset(run["defaultDatasetId"]).iterate_items())
                for item in items:
                    title = (item.get('title') or '').strip()
                    if not title or title.lower().startswith(('[deleted]', '[removed]')):
                        continue
                    score = item.get('score') or item.get('upvotes') or 0
                    num_comments = item.get('numComments') or item.get('commentsCount') or 0
                    body = (item.get('text') or item.get('body') or '')[:200].strip()
                    summary = body or f"{score:,} upvotes · {num_comments:,} comments"
                    articles.append({
                        'id': str(uuid.uuid4()),
                        'source': f"Reddit r/{sub}",
                        'title': title,
                        'url': item.get('url') or item.get('link', ''),
                        'summary': summary,
                        'category': platform_label,
                        'cached_at': datetime.datetime.now().isoformat(),
                        'score': score,
                        'comments': num_comments,
                    })
            except Exception as e:
                logger.warning(f"Apify Reddit r/{sub} error: {e}")
        logger.info(f"Apify Reddit {platform_label}: {len(articles)} posts from {len(subreddits)} subreddits")
        return articles
    except ImportError:
        logger.warning("apify-client not installed")
        return []
    except Exception as e:
        logger.error(f"Apify Reddit init error: {e}")
        return []


def _fetch_reddit_praw(subreddits, platform_label, client_id, client_secret):
    """Fetch Reddit posts using PRAW (Official OAuth2 API — not blocked by Railway)."""
    import time as _t
    try:
        import praw
        import praw.exceptions
    except ImportError:
        logger.warning("praw not installed — falling back to RSS")
        return _fetch_reddit_rss(subreddits, platform_label)

    articles = []
    cutoff_ts = _t.time() - (7 * 24 * 3600)
    try:
        reddit = praw.Reddit(
            client_id=client_id,
            client_secret=client_secret,
            user_agent="MirStudio/2.0 intel-fetcher (by /u/mir_studio_app)",
            read_only=True,
        )
        for sub in subreddits:
            try:
                subreddit = reddit.subreddit(sub)
                for post in subreddit.top(time_filter='week', limit=8):
                    if post.created_utc < cutoff_ts:
                        continue
                    if post.stickied or not post.title:
                        continue
                    title = post.title.strip()
                    if title.lower().startswith('[deleted]') or title.lower().startswith('[removed]'):
                        continue
                    # Build a rich summary with engagement data
                    engagement = f"{post.score:,} upvotes · {post.num_comments:,} comments"
                    body_snippet = (post.selftext[:200].strip() if post.selftext else '') or engagement
                    articles.append({
                        'id': str(uuid.uuid4()),
                        'source': f"Reddit r/{sub}",
                        'title': title,
                        'url': f"https://reddit.com{post.permalink}",
                        'summary': body_snippet,
                        'category': platform_label,
                        'cached_at': datetime.datetime.now().isoformat(),
                        'score': post.score,
                        'comments': post.num_comments,
                    })
            except Exception as e:
                logger.warning(f"PRAW r/{sub} error: {e}")
    except Exception as e:
        logger.error(f"PRAW init error: {e} — falling back to RSS")
        return _fetch_reddit_rss(subreddits, platform_label)

    logger.info(f"PRAW Reddit {platform_label}: {len(articles)} posts from {len(subreddits)} subreddits")
    return articles


def _fetch_reddit_rss(subreddits, platform_label):
    """Fallback: Fetch Reddit posts via RSS (may be blocked from Railway datacenter)."""
    import time as _t
    cutoff_ts = _t.time() - (7 * 24 * 3600)
    articles = []
    for sub in subreddits:
        try:
            rss_url = f"https://www.reddit.com/r/{sub}/top.rss?t=week&limit=8"
            feed = feedparser.parse(rss_url, request_headers={
                "User-Agent": "MirStudio/2.0 (+https://mir.up.railway.app)"
            })
            if feed.bozo and not feed.entries:
                logger.warning(f"Reddit RSS r/{sub}: parse error or blocked")
                continue
            for entry in feed.entries[:8]:
                pub = getattr(entry, 'published_parsed', None) or getattr(entry, 'updated_parsed', None)
                if pub and _t.mktime(pub) < cutoff_ts:
                    continue
                title = getattr(entry, 'title', '').strip()
                if not title or title.lower().startswith('[deleted]'):
                    continue
                summary = re.sub('<[^<]+?>', '', getattr(entry, 'summary', ''))[:250].strip()
                articles.append({
                    'id': str(uuid.uuid4()),
                    'source': f"Reddit r/{sub}",
                    'title': title,
                    'url': getattr(entry, 'link', ''),
                    'summary': summary or 'Reddit community discussion',
                    'category': platform_label,
                    'cached_at': datetime.datetime.now().isoformat(),
                })
        except Exception as e:
            logger.warning(f"Reddit RSS r/{sub} error: {e}")
    logger.info(f"Reddit RSS {platform_label}: {len(articles)} posts from {len(subreddits)} subreddits")
    return articles


# Fallback query sets (used when Apify is unavailable)
_LINKEDIN_RSS_FALLBACK = [
    ("creator economy",       "creator economy monetization brand deals"),
    ("B2B influencer",        "B2B influencer marketing strategy 2026"),
    ("community building",    "community building business growth strategy"),
    ("content marketing",     "content marketing personal brand LinkedIn"),
    ("sales marketing",       "sales marketing creator outreach trends"),
]
_INSTAGRAM_RSS_FALLBACK = [
    ("digital nomad",         "digital nomad remote work lifestyle 2026"),
    ("slow travel",           "slow travel Southeast Asia backpacking budget"),
    ("Indian passport",       "Indian passport travel visa abroad tips"),
    ("remote work travel",    "remote work location independent travel"),
    ("travel content",        "travel content creator Instagram reels tips"),
]


def _log_intel_source(run_at, source_name, source_type, count, success=True, error=''):
    """Store per-source stats for the last intel run."""
    try:
        with get_db() as conn:
            conn.execute(
                "INSERT INTO intel_run_log (id, run_at, source_name, source_type, articles_found, success, error_msg) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), run_at, source_name, source_type, count, 1 if success else 0, error or '')
            )
            conn.commit()
    except Exception as e:
        logger.warning(f"intel log error: {e}")


def fetch_and_cache_intel():
    """Pull RSS feeds (+ optional Apify) → cache → Claude analysis → store in DB."""
    import time as _time
    logger.info("Intel pipeline running...")
    api_key = os.environ.get('ANTHROPIC_API_KEY')
    run_at = datetime.datetime.now().isoformat()

    # Clear old run log for this run
    try:
        with get_db() as conn:
            # Keep only last 2 runs worth of logs
            conn.execute(
                "DELETE FROM intel_run_log WHERE run_at < ?",
                ((datetime.datetime.now() - datetime.timedelta(days=2)).isoformat(),)
            )
            conn.commit()
    except Exception:
        pass

    # 7-day cutoff — only fresh trending data
    cutoff_ts = _time.time() - (7 * 24 * 3600)

    articles = []

    # ── 1. Google News RSS + Blog feeds ──────────────────────────────────────
    for name, url, category in RSS_SOURCES:
        try:
            feed = feedparser.parse(url)
            added = 0
            for entry in feed.entries[:8]:
                pub = getattr(entry, 'published_parsed', None) or getattr(entry, 'updated_parsed', None)
                if pub:
                    entry_ts = _time.mktime(pub)
                    if entry_ts < cutoff_ts:
                        continue
                title = getattr(entry, 'title', '').strip()
                if not title:
                    continue
                summary = re.sub('<[^<]+?>', '', getattr(entry, 'summary', ''))[:280]
                articles.append({
                    'id': str(uuid.uuid4()),
                    'source': name,
                    'title': title,
                    'url': getattr(entry, 'link', ''),
                    'summary': summary,
                    'category': category,
                    'cached_at': run_at,
                })
                added += 1
                if added >= 3:
                    break
            _log_intel_source(run_at, name, 'google_news_rss', added, success=True)
            if added == 0:
                logger.warning(f"RSS '{name}': no fresh articles in past 7 days")
        except Exception as e:
            logger.error(f"RSS error {name}: {e}")
            _log_intel_source(run_at, name, 'google_news_rss', 0, success=False, error=str(e))

    rss_count = len(articles)
    logger.info(f"Google News RSS: {rss_count} articles from {len(RSS_SOURCES)} sources")

    # ── 2. Apify — real LinkedIn post engagement + Instagram hashtag data ─────
    li_apify_error = ''
    ig_apify_error = ''
    try:
        li_posts, li_used = fetch_linkedin_apify()
        if li_used and not li_posts:
            li_apify_error = 'Apify ran but returned 0 LinkedIn posts'
        _log_intel_source(run_at, 'Apify LinkedIn keyword search', 'apify_linkedin',
                          len(li_posts), success=li_used, error=li_apify_error)
    except Exception as e:
        li_posts, li_used = [], False
        li_apify_error = str(e)
        _log_intel_source(run_at, 'Apify LinkedIn keyword search', 'apify_linkedin', 0, success=False, error=li_apify_error)

    # ── Instagram: Instaloader (free) → Apify fallback ───────────────────────
    try:
        ig_posts, ig_method = fetch_instagram()
        _ig_source_labels = {
            'instaloader': ('Instaloader Instagram hashtags', 'instagram_instaloader'),
            'apify':       ('Apify Instagram hashtag search', 'apify_instagram'),
            'none':        ('Instagram (no method available)', 'instagram_none'),
        }
        ig_label, ig_type = _ig_source_labels.get(ig_method, ('Instagram', 'instagram_unknown'))
        _log_intel_source(run_at, ig_label, ig_type,
                          len(ig_posts), success=len(ig_posts) > 0,
                          error='' if ig_posts else f'0 posts via {ig_method}')
    except Exception as e:
        ig_posts, ig_method = [], 'none'
        _log_intel_source(run_at, 'Instagram', 'instagram_none', 0, success=False, error=str(e))

    articles.extend(li_posts)
    articles.extend(ig_posts)
    logger.info(f"LinkedIn (Apify): {len(li_posts)} | Instagram ({ig_method}): {len(ig_posts)}")

    # ── 3. Reddit: Apify → PRAW → RSS ────────────────────────────────────────
    li_reddit = fetch_reddit_intel(_LINKEDIN_SUBREDDITS, 'linkedin_posts')
    ig_reddit = fetch_reddit_intel(_INSTAGRAM_SUBREDDITS, 'instagram_posts')
    reddit_count = len(li_reddit) + len(ig_reddit)
    # Detect which method was actually used for accurate log labels
    _has_apify   = bool(os.environ.get('APIFY_TOKEN'))
    _has_praw    = bool(os.environ.get('REDDIT_CLIENT_ID') and os.environ.get('REDDIT_CLIENT_SECRET'))
    _reddit_type = 'reddit_apify' if _has_apify else ('reddit_praw' if _has_praw else 'reddit_rss')
    _reddit_method_name = 'Apify' if _has_apify else ('PRAW OAuth' if _has_praw else 'RSS')
    _reddit_err  = '' if reddit_count > 0 else (
        '0 posts — Apify actor may need a moment, retry refresh' if _has_apify else
        'Add APIFY_TOKEN or REDDIT credentials in Settings'
    )
    _log_intel_source(run_at, f'Reddit B2B/marketing ({_reddit_method_name})', _reddit_type,
                      len(li_reddit), success=len(li_reddit) > 0,
                      error=_reddit_err if len(li_reddit) == 0 else '')
    _log_intel_source(run_at, f'Reddit travel/nomad ({_reddit_method_name})', _reddit_type,
                      len(ig_reddit), success=len(ig_reddit) > 0,
                      error=_reddit_err if len(ig_reddit) == 0 else '')
    articles.extend(li_reddit)
    articles.extend(ig_reddit)
    logger.info(f"Reddit ({_reddit_method_name}): {reddit_count} total posts")

    has_deep = bool(li_posts or ig_posts or li_reddit or ig_reddit)  # True when any real social data came in

    if articles:
        with get_db() as conn:
            conn.execute("DELETE FROM intel_cache")
            for a in articles:
                conn.execute(
                    "INSERT INTO intel_cache (id, source, title, url, summary, category, cached_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (a['id'], a['source'], a['title'], a['url'], a['summary'], a['category'], a['cached_at'])
                )
            conn.commit()

    if not api_key or not articles:
        logger.warning("Intel: skipping Claude analysis (no API key or no articles)")
        return

    try:
        from anthropic import Anthropic
        client = Anthropic(api_key=api_key)

        # Separate social posts (Apify/Reddit) from RSS news articles
        # has_deep already set above — True only when Apify returned real engagement data
        apify_items = [a for a in articles if a['category'] in ('linkedin_posts', 'instagram_posts')]
        rss_items = [a for a in articles if a['category'] not in ('linkedin_posts', 'instagram_posts')]

        def _fmt(a):
            return f"[{a['category'].upper()}] {a['title']} | Source: {a['source']} | {a['summary'][:200]}"

        # Outliers first, then RSS, cap at 35 total
        article_text = '\n'.join(
            [_fmt(a) for a in apify_items[:20]] +
            [_fmt(a) for a in rss_items[:15]]
        )
        data_quality_note = (
            "DATA: Real post data with engagement scores from Apify — LinkedIn keyword search + Instagram hashtag search. "
            "⚡OUTLIER = statistically above-average engagement. Topics are keyword/hashtag-driven, NOT profile-based."
            if has_deep else
            "DATA: Reddit community posts (top of week) + Google News RSS. Reddit shows what people are actively discussing."
        )

        with get_db() as conn:
            recent_rows = conn.execute(
                "SELECT topic FROM kb_topics_covered WHERE last_posted_at > ? "
                "ORDER BY last_posted_at DESC LIMIT 5",
                ((datetime.datetime.now() - datetime.timedelta(days=14)).isoformat(),)
            ).fetchall()
        recent_str = ', '.join([r['topic'] for r in recent_rows]) if recent_rows else 'none'

        # Pull user-tracked creators from DB
        tracked_li = get_tracked_handles('linkedin')
        tracked_ig = get_tracked_handles('instagram')
        li_str = ', '.join(tracked_li) if tracked_li else 'Justin Welsh, Matt Gray, Lara Acosta, Richard van der Blom, Sam Szuchan, Jasmin Alic, Steph Smith'
        ig_str = ', '.join([f'@{h}' for h in tracked_ig]) if tracked_ig else 'Indian passport travel creators, SEA nomad creators, @nomadnumbers, @thewanderingquinn'

        response = client.messages.create(
            model=os.environ.get('CLAUDE_MODEL', 'claude-sonnet-4-5-20250929'),
            max_tokens=1800,
            messages=[{
                "role": "user",
                "content": f"""You are the intel engine for Mir Tahmid Ali's content system.
All articles below are from the PAST 7 DAYS only. Ignore anything that feels older.

━━━ MIR'S DOMAINS (STRICT — DO NOT MIX) ━━━

LINKEDIN DOMAIN (only B2B/professional topics go here):
- B2B creator and influencer marketing
- Creator economy mechanics (deals, ROI, attribution)
- Community building as a business strategy
- Founder/operator insights
- Creator sourcing and outreach (Passionfroot's world)
LinkedIn audience: founders, marketers, operators, B2B decision-makers

INSTAGRAM DOMAIN (only travel/lifestyle/nomad topics go here):
- Slow travel on an Indian passport (visa hacks, affordable destinations)
- Digital nomad logistics (cost of living, remote work setup, SEA hubs)
- Cultural immersion — specific places, real moments
- Indian traveler abroad — the struggle and the freedom
- Remote work lifestyle — where to go, how to live cheaply
Instagram audience: Indian professionals considering travel, nomads, remote workers

NEWSLETTER DOMAIN (the bridge — mixes both worlds):
- Personal story from travels that connects to a professional insight
- Mir's full self: the builder AND the traveler
- One concrete lesson from both worlds

━━━ CREATORS MIR MONITORS ━━━

LinkedIn accounts (what's getting traction in their space this week):
{li_str}

Instagram accounts (what formats/topics are resonating this week):
{ig_str}

━━━ DATA SOURCE ━━━
{data_quality_note}

━━━ CONTENT + ENGAGEMENT DATA (PAST 7 DAYS) ━━━
{article_text}

RECENTLY COVERED by Mir (do NOT repeat these): {recent_str}

━━━ TASK ━━━
1. Identify what is genuinely trending RIGHT NOW (this week) in each domain
2. Flag OUTLIERS — topics getting 2x+ normal engagement vs. typical weeks
3. Suggest angles that are NOT obvious — gaps nobody is writing about
4. Keep LinkedIn and Instagram intel completely separate — no crossover

Return ONLY valid JSON:
{{
  "linkedin_intel": [
    {{
      "topic": "Specific B2B/creator/community topic that is hot THIS week on LinkedIn",
      "why_trending": "What specifically is driving this conversation right now — cite the signal",
      "hook": "First line of a LinkedIn post — pattern interrupt, never starts with I, no em dashes",
      "is_outlier": true,
      "source_name": "exact source name from articles above",
      "source_url": "exact URL",
      "found_via": "Google News — creator economy — past 7 days"
    }}
  ],
  "instagram_intel": [
    {{
      "topic": "Specific travel/nomad/Indian passport topic trending on Instagram this week",
      "why_trending": "What visual format or angle is resonating right now — be specific",
      "hook": "What the FIRST FRAME of the reel shows — visual and emotional",
      "is_outlier": false,
      "source_name": "exact source name",
      "source_url": "exact URL",
      "found_via": "Google News — slow travel — past 7 days"
    }}
  ],
  "content_gaps": [
    {{
      "angle": "Something in Mir's world that nobody is writing about this week",
      "why": "Why this is underserved right now",
      "platform": "linkedin or instagram",
      "source_name": "source that hints at this gap",
      "source_url": "URL"
    }}
  ],
  "suggested_angles": [
    {{
      "idea": "Concrete post idea in Mir's voice — specific, no platitudes, real numbers or situations",
      "platform": "linkedin or instagram",
      "source_url": "URL that inspired this"
    }}
  ]
}}

2-3 items per section. If you cannot find genuine past-7-day data for a section, return an empty array rather than making things up."""
            }]
        )

        raw = response.content[0].text.strip()
        if '```json' in raw:
            raw = raw.split('```json')[1].split('```')[0].strip()
        elif '```' in raw:
            raw = raw.split('```')[1].split('```')[0].strip()
        analysis = json.loads(raw)

        # Build Instagram performance insight from analytics table
        instagram_insights = ""
        with get_db() as conn:
            ig_rows = conn.execute(
                "SELECT hook_type, performance_tier, saves, reach FROM instagram_reel_analytics "
                "WHERE collected_at > ? ORDER BY collected_at DESC LIMIT 30",
                ((datetime.datetime.now() - datetime.timedelta(days=30)).isoformat(),)
            ).fetchall()

        if ig_rows:
            top = [r for r in ig_rows if r['performance_tier'] == 'top']
            if top:
                hook_counts = {}
                for r in top:
                    h = r['hook_type'] or 'unknown'
                    hook_counts[h] = hook_counts.get(h, 0) + 1
                best_hook = max(hook_counts, key=hook_counts.get)
                avg_saves = sum(r['saves'] for r in top) // len(top)
                instagram_insights = (
                    f"Top reels use {best_hook} hooks. Average saves on top performers: {avg_saves}. "
                    f"Recommend opening next scripts with a {best_hook} approach."
                )

        with get_db() as conn:
            conn.execute("DELETE FROM intel_analysis")
            conn.execute(
                "INSERT INTO intel_analysis (id, debates, gaps, trending, suggested_angles, instagram_insights, generated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    str(uuid.uuid4()),
                    json.dumps(analysis.get('linkedin_intel', analysis.get('debates', []))),
                    json.dumps(analysis.get('content_gaps', analysis.get('gaps', []))),
                    json.dumps(analysis.get('instagram_intel', analysis.get('trending', []))),
                    json.dumps({'angles': analysis.get('suggested_angles', []), 'deep_data': has_deep}),
                    instagram_insights,
                    datetime.datetime.now().isoformat(),
                )
            )
            conn.execute(
                "UPDATE app_stats SET last_intel_refresh = ? WHERE id = 1",
                (datetime.datetime.now().isoformat(),)
            )
            conn.commit()

        logger.info("Intel analysis complete.")

    except Exception as e:
        logger.error(f"Intel analysis error: {e}")


# ── Routes ────────────────────────────────────────────────────────────────────
@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/status')
def status():
    has_key = bool(os.environ.get('ANTHROPIC_API_KEY'))
    with get_db() as conn:
        stats = conn.execute("SELECT * FROM app_stats WHERE id = 1").fetchone()
        kb_count = conn.execute("SELECT COUNT(*) as c FROM kb_voice_profile").fetchone()['c']
        stories_count = conn.execute("SELECT COUNT(*) as c FROM kb_story_bank").fetchone()['c']
        raw_ideas_count = conn.execute("SELECT COUNT(*) as c FROM kb_raw_ideas").fetchone()['c']
    return jsonify({
        'has_api_key': has_key,
        'has_reddit': bool(os.environ.get('REDDIT_CLIENT_ID') and os.environ.get('REDDIT_CLIENT_SECRET')),
        'has_apify': bool(os.environ.get('APIFY_TOKEN')),
        'kb_ready': kb_count > 0,
        'stories_count': stories_count,
        'raw_ideas_count': raw_ideas_count,
        'streak': stats['streak_count'] if stats else 0,
        'total_linkedin': stats['total_linkedin'] if stats else 0,
        'total_newsletter': stats['total_newsletter'] if stats else 0,
        'total_instagram': stats['total_instagram'] if stats else 0,
        'last_intel_refresh': stats['last_intel_refresh'] if stats else None,
    })


@app.route('/api/settings/apikey', methods=['POST'])
def set_api_key():
    data = request.json or {}
    key = data.get('api_key', '').strip()
    if not key.startswith('sk-ant-'):
        return jsonify({'error': 'Invalid API key format. Should start with sk-ant-'}), 400

    env_path = Path('.env')
    env_content = env_path.read_text() if env_path.exists() else ''
    if 'ANTHROPIC_API_KEY=' in env_content:
        env_content = re.sub(r'ANTHROPIC_API_KEY=.*', f'ANTHROPIC_API_KEY={key}', env_content)
    else:
        env_content += f'\nANTHROPIC_API_KEY={key}\n'
    env_path.write_text(env_content)
    os.environ['ANTHROPIC_API_KEY'] = key
    return jsonify({'ok': True})


@app.route('/api/settings/reddit', methods=['POST'])
def set_reddit_creds():
    """Save Reddit API credentials (client_id + client_secret) to .env and runtime."""
    data = request.json or {}
    client_id = data.get('client_id', '').strip()
    client_secret = data.get('client_secret', '').strip()
    if not client_id or not client_secret:
        return jsonify({'error': 'Both client_id and client_secret are required'}), 400

    env_path = Path('.env')
    env_content = env_path.read_text() if env_path.exists() else ''

    def _upsert(content, key, value):
        if f'{key}=' in content:
            return re.sub(rf'{key}=.*', f'{key}={value}', content)
        return content + f'\n{key}={value}\n'

    env_content = _upsert(env_content, 'REDDIT_CLIENT_ID', client_id)
    env_content = _upsert(env_content, 'REDDIT_CLIENT_SECRET', client_secret)
    env_path.write_text(env_content)
    os.environ['REDDIT_CLIENT_ID'] = client_id
    os.environ['REDDIT_CLIENT_SECRET'] = client_secret
    logger.info("Reddit credentials saved")
    return jsonify({'ok': True})


@app.route('/api/generate', methods=['POST'])
def generate():
    import traceback
    try:
        return _generate_inner()
    except Exception as e:
        logger.error(f"generate() unhandled error: {traceback.format_exc()}")
        return jsonify({'error': f'Server error: {type(e).__name__}: {e}'}), 500


def _generate_inner():
    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        return jsonify({'error': 'No API key configured. Go to ⚙️ Settings to add your Anthropic API key.'}), 400

    data = request.json or {}
    thought = data.get('thought', '').strip()
    platforms = data.get('platforms', ['linkedin'])
    story_id = data.get('story_id')
    intel_context = data.get('intel_context', '')
    image_description = data.get('image_description', '')

    if not thought:
        return jsonify({'error': 'No thought provided'}), 400

    from anthropic import Anthropic
    client = Anthropic(api_key=api_key)

    voice_ctx = build_voice_context()
    story_ctx = get_story_context(story_id) if story_id else ''

    extra = []
    if story_ctx:
        extra.append(story_ctx)
    if intel_context:
        extra.append(f"RESEARCH CONTEXT TO DRAW FROM:\n{intel_context}")
    if image_description:
        extra.append(f"IMAGE CONTEXT:\n{image_description}")
    context_block = ('\n\n' + '\n\n'.join(extra)) if extra else ''

    results = {}

    # ── LinkedIn ──────────────────────────────────────────────────────────────
    if 'linkedin' in platforms:
        try:
            prompt = f"""Write a LinkedIn post for Mir Tahmid Ali.

LINKEDIN DOMAIN: B2B creator marketing, community building, influencer ROI, creator economy mechanics, founder ops.
This is NOT the place for travel or nomad content. This is Mir the builder, the operator, the systems thinker.

RAW THOUGHT:
{thought}
{context_block}

TONE: Mir talking to ONE smart friend who works in startups or marketing. Not a thought leader speaking to a crowd. Not a brand. A person with experience sharing a real observation.

STRUCTURE:
1. Hook: Pattern interrupt. Surprising stat, counterintuitive truth, or relatable frustration. Never starts with "I".
2. Human layer: 1-2 sentences on why this matters or what it felt like to learn this.
3. Core insight: One concrete thing. Real numbers if available. Systems thinking applied to a real situation.
4. Close: A genuine question OR a soft observation. Never a call-to-action. Never "DM me".

HARD RULES:
- 150-250 words
- Short paragraphs (1-2 sentences max), blank line between each
- NEVER start with "I" as the very first word
- NEVER use em dashes. Use commas or short sentences.
- No bullet point lists unless the thought is naturally list-shaped
- No "excited to share", "thrilled", "let's talk about"
- End with 3-5 hashtags on the last line only

Return ONLY the post text. Nothing else."""
            r = client.messages.create(
                model=os.environ.get('CLAUDE_MODEL', 'claude-sonnet-4-5-20250929'),
                max_tokens=600,
                system=voice_ctx,
                messages=[{"role": "user", "content": prompt}]
            )
            results['linkedin'] = r.content[0].text.strip()
        except Exception as e:
            results['linkedin_error'] = str(e)

    # ── Newsletter ────────────────────────────────────────────────────────────
    if 'newsletter' in platforms:
        try:
            prompt = f"""Write a Beehiiv newsletter section for Mir Tahmid Ali.

NEWSLETTER DOMAIN: The bridge between Mir's two worlds — the builder (B2B/creator/startup) and the traveler (slow travel, Indian passport, nomad). This is the only platform where both sides of Mir exist in the same piece. The professional insight lands harder because of the personal story behind it, or vice versa.

RAW THOUGHT:
{thought}
{context_block}

TONE: A personal note from a friend who happens to be both an operator and a traveler. Not a newsletter. A letter. Written to someone Mir respects, not to subscribers.

WHAT MAKES IT WORK:
- Opens with something specific: a situation, a number, a place, a feeling
- Connects the personal and the professional naturally (not forced)
- One concrete insight that the reader will remember tomorrow
- Ends with something quotable OR an honest question they'll want to reply to

HARD RULES:
- Body: 180-220 words
- NO section headers inside the body
- NO "Hi everyone", "In this issue", "Today I want to talk about"
- NEVER use em dashes. Use commas or short sentences.
- NEVER start the body with "I"
- At least one real number or real place name
- Closing line: memorable. Could be a single sentence they screenshot.

OUTPUT FORMAT (exact labels, nothing else):

Subject: [curiosity-gap or direct-benefit — max 50 chars]
Headline: [bold article-style headline for top of Beehiiv post]
---
[Body — 180-220 words, no headers]

Return ONLY the formatted content. Nothing else."""
            r = client.messages.create(
                model=os.environ.get('CLAUDE_MODEL', 'claude-sonnet-4-5-20250929'),
                max_tokens=500,
                system=voice_ctx,
                messages=[{"role": "user", "content": prompt}]
            )
            results['newsletter'] = r.content[0].text.strip()
        except Exception as e:
            results['newsletter_error'] = str(e)

    # ── Instagram ─────────────────────────────────────────────────────────────
    if 'instagram' in platforms:
        try:
            prompt = f"""Write a 45-second Instagram reel voice-over script for Mir Tahmid Ali (@mirtheexplorer).

INSTAGRAM DOMAIN: Slow travel, Indian passport travel, digital nomad logistics, remote work lifestyle, SEA destinations, cultural immersion. This is NOT the place for B2B or creator economy content. This is Mir the traveler, the explorer, the person figuring out how to live freely on an Indian passport.

RAW THOUGHT:
{thought}
{context_block}

TONE: Telling ONE friend about something that happened or something you realized while traveling. Conversational. Specific. Grounded. Not inspirational. Not advice-giving. Just honest.

WHAT MAKES IT WORK ON INSTAGRAM:
- First 3 seconds: visual + emotional hook that stops the scroll
- Specific location, specific moment — not "I was somewhere beautiful"
- The feeling of being an Indian traveler abroad — the unique friction and freedom
- One real insight. Not a lesson. A realization.
- Pacing: short sentences. Deliberate pauses. Mir speaks slowly.

HARD RULES:
- 75-90 words total for voice-over text (people speak ~130 wpm, 45s = ~100 words max)
- NEVER use em dashes. Use commas, line breaks, or short sentences.
- Specific real location (city, cafe, airport, hostel). Not "a beautiful place."
- No generic travel wisdom ("life is short", "step outside your comfort zone")
- No "Don't forget to follow" or "subscribe"

OUTPUT FORMAT (exact labels):

HOOK (0-5s): [1-2 short punchy lines. Visual scene + instant emotional grab.]
STORY (5-25s): [3-4 sentences. Specific location, real moment, what actually happened.]
INSIGHT (25-40s): [1-2 sentences. The realization. Should work as a standalone quote.]
LOOP/CTA (40-45s): [1 sentence. Loops back to opening image OR ends with a real question.]

B-ROLL:
1. [specific shot — what the camera shows]
2. [specific shot]
3. [specific shot]
4. [specific shot]

THUMBNAIL FRAME: [the single most scroll-stopping visual from this story]

Return ONLY the formatted script. Nothing else."""
            r = client.messages.create(
                model=os.environ.get('CLAUDE_MODEL', 'claude-sonnet-4-5-20250929'),
                max_tokens=500,
                system=voice_ctx,
                messages=[{"role": "user", "content": prompt}]
            )
            results['instagram'] = r.content[0].text.strip()
        except Exception as e:
            results['instagram_error'] = str(e)

    # Save to content_items
    item_id = str(uuid.uuid4())
    with get_db() as conn:
        conn.execute(
            "INSERT INTO content_items "
            "(id, platforms, raw_thought, linkedin_content, newsletter_content, instagram_script, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'draft', ?)",
            (
                item_id,
                json.dumps(platforms),
                thought,
                results.get('linkedin', ''),
                results.get('newsletter', ''),
                results.get('instagram', ''),
                datetime.datetime.now().isoformat(),
            )
        )
        conn.commit()

    results['item_id'] = item_id
    return jsonify(results)


@app.route('/api/raw-ideas', methods=['GET'])
def get_raw_ideas():
    with get_db() as conn:
        ideas = conn.execute(
            "SELECT * FROM kb_raw_ideas ORDER BY created_at DESC LIMIT 200"
        ).fetchall()
    return jsonify([dict(r) for r in ideas])


@app.route('/api/raw-ideas', methods=['POST'])
def save_raw_idea():
    data = request.json or {}
    thought = data.get('thought', '').strip()
    if not thought or len(thought) < 5:
        return jsonify({'ok': True})

    with get_db() as conn:
        # Deduplicate: same thought within last 30 seconds
        recent_cutoff = (datetime.datetime.now() - datetime.timedelta(seconds=30)).isoformat()
        existing = conn.execute(
            "SELECT id FROM kb_raw_ideas WHERE thought = ? AND created_at > ?",
            (thought, recent_cutoff)
        ).fetchone()
        if existing:
            return jsonify({'ok': True, 'id': existing['id']})

        idea_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO kb_raw_ideas (id, thought, source, platforms_intended, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                idea_id, thought,
                data.get('source', 'text'),
                json.dumps(data.get('platforms', [])),
                datetime.datetime.now().isoformat(),
            )
        )
        conn.commit()
    return jsonify({'ok': True, 'id': idea_id})


@app.route('/api/raw-ideas/<idea_id>', methods=['DELETE'])
def delete_raw_idea(idea_id):
    with get_db() as conn:
        conn.execute("DELETE FROM kb_raw_ideas WHERE id = ?", (idea_id,))
        conn.commit()
    return jsonify({'ok': True})


@app.route('/api/content', methods=['GET'])
def get_content():
    platform = request.args.get('platform')
    status_filter = request.args.get('status')

    query = "SELECT * FROM content_items WHERE 1=1"
    params = []
    if platform:
        query += " AND platforms LIKE ?"
        params.append(f'%"{platform}"%')
    if status_filter:
        query += " AND status = ?"
        params.append(status_filter)
    query += " ORDER BY created_at DESC LIMIT 100"

    with get_db() as conn:
        items = conn.execute(query, params).fetchall()
    return jsonify([dict(r) for r in items])


@app.route('/api/content/<item_id>', methods=['PUT'])
def update_content(item_id):
    data = request.json or {}
    allowed = ['status', 'rating', 'linkedin_content', 'newsletter_content', 'instagram_script', 'posted_at']
    updates = {k: v for k, v in data.items() if k in allowed}
    if not updates:
        return jsonify({'error': 'No valid fields'}), 400

    set_clause = ', '.join([f"{k} = ?" for k in updates])
    values = list(updates.values()) + [item_id]
    with get_db() as conn:
        conn.execute(f"UPDATE content_items SET {set_clause} WHERE id = ?", values)
        conn.commit()
    return jsonify({'ok': True})


@app.route('/api/content/<item_id>/post', methods=['POST'])
def mark_posted(item_id):
    now = datetime.datetime.now()
    now_iso = now.isoformat()
    today = now.date().isoformat()
    yesterday = (now.date() - datetime.timedelta(days=1)).isoformat()

    with get_db() as conn:
        item = conn.execute("SELECT * FROM content_items WHERE id = ?", (item_id,)).fetchone()
        if not item:
            return jsonify({'error': 'Not found'}), 404

        conn.execute(
            "UPDATE content_items SET status = 'posted', posted_at = ? WHERE id = ?",
            (now_iso, item_id)
        )

        stats = conn.execute("SELECT * FROM app_stats WHERE id = 1").fetchone()
        streak = stats['streak_count'] if stats else 0
        last_date = stats['last_posted_date'] if stats else None

        if last_date == today:
            pass  # same day, streak unchanged
        elif last_date == yesterday:
            streak += 1  # consecutive day
        else:
            streak = 1  # reset

        platforms = json.loads(item['platforms'] or '[]')
        li = 1 if 'linkedin' in platforms else 0
        nl = 1 if 'newsletter' in platforms else 0
        ig = 1 if 'instagram' in platforms else 0

        conn.execute(
            "UPDATE app_stats SET streak_count=?, last_posted_date=?, "
            "total_linkedin=total_linkedin+?, total_newsletter=total_newsletter+?, "
            "total_instagram=total_instagram+? WHERE id=1",
            (streak, today, li, nl, ig)
        )

        # Update KB topics
        raw = item['raw_thought'] or ''
        if raw and len(raw) > 5:
            topic = raw[:60].strip()
            for platform in platforms:
                existing = conn.execute(
                    "SELECT id FROM kb_topics_covered WHERE topic = ? AND platform = ?",
                    (topic, platform)
                ).fetchone()
                if existing:
                    conn.execute(
                        "UPDATE kb_topics_covered SET times_posted = times_posted + 1, last_posted_at = ? WHERE id = ?",
                        (now_iso, existing['id'])
                    )
                else:
                    conn.execute(
                        "INSERT INTO kb_topics_covered (id, topic, platform, times_posted, last_posted_at) "
                        "VALUES (?, ?, ?, 1, ?)",
                        (str(uuid.uuid4()), topic, platform, now_iso)
                    )

        conn.commit()

    return jsonify({'ok': True, 'streak': streak})


@app.route('/api/content/<item_id>', methods=['DELETE'])
def delete_content(item_id):
    with get_db() as conn:
        conn.execute("DELETE FROM content_items WHERE id = ?", (item_id,))
        conn.commit()
    return jsonify({'ok': True})


@app.route('/api/content/<item_id>/discard', methods=['POST'])
def discard_content(item_id):
    """Log what Mir rejected so the KB learns his preferences."""
    data = request.json or {}
    with get_db() as conn:
        item = conn.execute("SELECT * FROM content_items WHERE id = ?", (item_id,)).fetchone()
        if not item:
            return jsonify({'error': 'Not found'}), 404
        platforms = json.loads(item['platforms'] or '[]')
        for platform in platforms:
            content_key = {'linkedin': 'linkedin_content', 'newsletter': 'newsletter_content', 'instagram': 'instagram_script'}.get(platform)
            content = item[content_key] if content_key else ''
            conn.execute(
                "INSERT INTO kb_discard_log (id, item_id, platform, raw_thought, discard_reason, pattern_notes, discarded_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    str(uuid.uuid4()), item_id, platform,
                    item['raw_thought'] or '',
                    data.get('reason', ''),
                    data.get('pattern_notes', ''),
                    datetime.datetime.now().isoformat(),
                )
            )
        conn.execute("UPDATE content_items SET status = 'discarded' WHERE id = ?", (item_id,))
        conn.commit()
    return jsonify({'ok': True})


# ── Tracked Creators ──────────────────────────────────────────────────────────
def _parse_creator_url(url):
    """Extract platform + handle from a LinkedIn or Instagram profile URL."""
    url = url.strip().rstrip('/')
    # Instagram
    m = re.match(r'(?:https?://)?(?:www\.)?instagram\.com/([^/?#]+)', url)
    if m:
        return 'instagram', m.group(1)
    # LinkedIn
    m = re.match(r'(?:https?://)?(?:www\.)?linkedin\.com/in/([^/?#]+)', url)
    if m:
        return 'linkedin', m.group(1)
    # LinkedIn company
    m = re.match(r'(?:https?://)?(?:www\.)?linkedin\.com/company/([^/?#]+)', url)
    if m:
        return 'linkedin', m.group(1)
    # Raw @handle or bare username — require explicit platform
    return None, None


@app.route('/api/creators', methods=['GET'])
def get_creators():
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM tracked_creators ORDER BY platform, added_at DESC"
        ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route('/api/creators', methods=['POST'])
def add_creator():
    data = request.json or {}
    url = data.get('url', '').strip()
    niche = data.get('niche', '').strip()
    platform, handle = _parse_creator_url(url)
    if not platform:
        return jsonify({'error': 'Could not parse URL. Paste a linkedin.com/in/... or instagram.com/... URL.'}), 400
    try:
        with get_db() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO tracked_creators (id, platform, handle, profile_url, niche, added_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), platform, handle, url, niche, datetime.datetime.now().isoformat())
            )
            conn.commit()
        return jsonify({'ok': True, 'platform': platform, 'handle': handle})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/creators/<creator_id>', methods=['DELETE'])
def delete_creator(creator_id):
    with get_db() as conn:
        conn.execute("DELETE FROM tracked_creators WHERE id = ?", (creator_id,))
        conn.commit()
    return jsonify({'ok': True})


@app.route('/api/intel', methods=['GET'])
def get_intel():
    with get_db() as conn:
        analysis = conn.execute(
            "SELECT * FROM intel_analysis ORDER BY generated_at DESC LIMIT 1"
        ).fetchone()
        articles = conn.execute(
            "SELECT * FROM intel_cache ORDER BY cached_at DESC LIMIT 30"
        ).fetchall()

    if analysis:
        angles_raw = json.loads(analysis['suggested_angles'] or '[]')
        # Handle both old format (plain list) and new format (dict with angles + deep_data)
        if isinstance(angles_raw, dict):
            angles = angles_raw.get('angles', [])
            deep_data = angles_raw.get('deep_data', False)
        else:
            angles = angles_raw
            deep_data = False
        debates_raw = json.loads(analysis['debates'] or '[]')
        gaps_raw = json.loads(analysis['gaps'] or '[]')
        trending_raw = json.loads(analysis['trending'] or '[]')
        return jsonify({
            'linkedin_intel': debates_raw,   # stored in 'debates' column
            'instagram_intel': trending_raw, # stored in 'trending' column
            'content_gaps': gaps_raw,        # stored in 'gaps' column
            # Legacy keys for any old UI references
            'debates': debates_raw,
            'gaps': gaps_raw,
            'trending': trending_raw,
            'suggested_angles': angles,
            'deep_data': deep_data,
            'generated_at': analysis['generated_at'],
            'articles': [dict(a) for a in articles],
        })
    return jsonify({
        'linkedin_intel': [], 'instagram_intel': [], 'content_gaps': [],
        'debates': [], 'gaps': [], 'trending': [],
        'suggested_angles': [], 'deep_data': False, 'generated_at': None, 'articles': [],
    })


@app.route('/api/intel/refresh', methods=['POST'])
def refresh_intel():
    t = threading.Thread(target=fetch_and_cache_intel)
    t.daemon = True
    t.start()
    return jsonify({'ok': True, 'message': 'Refreshing intel... check back in 60 seconds.'})


@app.route('/api/intel/sources', methods=['GET'])
def get_intel_sources():
    """Return per-source stats from the last intel run."""
    with get_db() as conn:
        # Get the most recent run_at
        latest = conn.execute(
            "SELECT run_at FROM intel_run_log ORDER BY run_at DESC LIMIT 1"
        ).fetchone()
        if not latest:
            return jsonify({'run_at': None, 'sources': []})
        run_at = latest['run_at']
        rows = conn.execute(
            "SELECT source_name, source_type, articles_found, success, error_msg "
            "FROM intel_run_log WHERE run_at = ? ORDER BY rowid",
            (run_at,)
        ).fetchall()
    return jsonify({
        'run_at': run_at,
        'sources': [dict(r) for r in rows],
    })


@app.route('/api/kb/export', methods=['GET'])
def export_kb():
    """Download entire knowledge base as a structured JSON file."""
    fmt = request.args.get('format', 'json')
    with get_db() as conn:
        profile = {r['key']: r['value'] for r in conn.execute("SELECT key, value FROM kb_voice_profile").fetchall()}
        stories = [dict(r) for r in conn.execute("SELECT * FROM kb_story_bank ORDER BY created_at").fetchall()]
        topics = [dict(r) for r in conn.execute(
            "SELECT topic, platform, times_posted, last_posted_at FROM kb_topics_covered ORDER BY last_posted_at DESC LIMIT 100"
        ).fetchall()]
        discards = [dict(r) for r in conn.execute(
            "SELECT platform, raw_thought, discard_reason, pattern_notes, discarded_at "
            "FROM kb_discard_log ORDER BY discarded_at DESC LIMIT 100"
        ).fetchall()]
        raw_ideas = [dict(r) for r in conn.execute(
            "SELECT thought, source, created_at FROM kb_raw_ideas ORDER BY created_at DESC LIMIT 200"
        ).fetchall()]
        stats = dict(conn.execute("SELECT * FROM app_stats WHERE id = 1").fetchone() or {})

    kb = {
        'exported_at': datetime.datetime.now().isoformat(),
        'app': 'Mir Studio',
        'voice_profile': profile,
        'story_bank': stories,
        'topics_covered': topics,
        'discard_log': discards,
        'raw_ideas': raw_ideas,
        'stats': stats,
    }

    if fmt == 'md':
        lines = [
            f"# Mir Studio — Knowledge Base Export",
            f"*Exported: {kb['exported_at']}*\n",
            "## Voice Profile\n",
        ]
        for k, v in profile.items():
            lines.append(f"**{k}**\n{v}\n")
        lines.append("\n## Story Bank\n")
        for s in stories:
            lines.append(f"### {s.get('title','')}\n*Platform: {s.get('platform','')}*\n\n{s.get('story_snippet','')}\n")
        lines.append("\n## Topics Covered (last 100)\n")
        for t in topics:
            lines.append(f"- **{t['topic']}** ({t['platform']}) — posted {t.get('times_posted',1)}× — last: {t.get('last_posted_at','')[:10]}")
        lines.append("\n## Discard Log (patterns rejected)\n")
        for d in discards:
            if d.get('pattern_notes'):
                lines.append(f"- [{d['platform']}] {d.get('pattern_notes','')} _{d.get('discarded_at','')[:10]}_")
        lines.append("\n## Raw Ideas (last 200)\n")
        for r in raw_ideas:
            lines.append(f"- {r.get('thought','')[:120]} _{r.get('created_at','')[:10]}_")
        content = '\n'.join(lines)
        from flask import Response
        return Response(content, mimetype='text/markdown',
                       headers={'Content-Disposition': 'attachment; filename=mir_kb.md'})

    from flask import Response
    return Response(
        json.dumps(kb, indent=2, ensure_ascii=False),
        mimetype='application/json',
        headers={'Content-Disposition': 'attachment; filename=mir_kb.json'}
    )


@app.route('/api/stories', methods=['GET'])
def get_stories():
    with get_db() as conn:
        stories = conn.execute(
            "SELECT * FROM kb_story_bank ORDER BY times_used ASC, created_at DESC"
        ).fetchall()
    return jsonify([dict(s) for s in stories])


@app.route('/api/stories', methods=['POST'])
def add_story():
    data = request.json or {}
    with get_db() as conn:
        conn.execute(
            "INSERT INTO kb_story_bank (id, title, story_snippet, platform, tags, times_used, created_at) "
            "VALUES (?, ?, ?, ?, ?, 0, ?)",
            (
                str(uuid.uuid4()),
                data.get('title', ''),
                data.get('story_snippet', ''),
                data.get('platform', 'both'),
                json.dumps(data.get('tags', [])),
                datetime.datetime.now().isoformat(),
            )
        )
        conn.commit()
    return jsonify({'ok': True})


@app.route('/api/stats', methods=['GET'])
def get_stats():
    with get_db() as conn:
        stats = conn.execute("SELECT * FROM app_stats WHERE id = 1").fetchone()
        raw_count = conn.execute("SELECT COUNT(*) as c FROM kb_raw_ideas").fetchone()['c']
        week_ago = (datetime.datetime.now() - datetime.timedelta(days=7)).isoformat()
        weekly = conn.execute(
            "SELECT COUNT(*) as c FROM content_items WHERE status = 'posted' AND posted_at > ?",
            (week_ago,)
        ).fetchone()['c']
    if stats:
        return jsonify({**dict(stats), 'raw_ideas_count': raw_count, 'weekly_posted': weekly})
    return jsonify({'streak_count': 0, 'raw_ideas_count': raw_count, 'weekly_posted': 0})


@app.route('/api/kb', methods=['GET'])
def get_kb():
    with get_db() as conn:
        profile = conn.execute("SELECT key, value FROM kb_voice_profile").fetchall()
        topics = conn.execute(
            "SELECT topic, platform, times_posted FROM kb_topics_covered "
            "ORDER BY last_posted_at DESC LIMIT 20"
        ).fetchall()
    return jsonify({
        'profile': {r['key']: r['value'] for r in profile},
        'topics_covered': [dict(t) for t in topics],
    })


@app.route('/api/upload', methods=['POST'])
def upload_image():
    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        return jsonify({'error': 'No API key configured'}), 400
    if 'image' not in request.files:
        return jsonify({'error': 'No image file'}), 400

    file = request.files['image']
    img_b64 = base64.standard_b64encode(file.read()).decode('utf-8')
    ext = (file.filename or 'img.jpg').rsplit('.', 1)[-1].lower()
    mt_map = {'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png',
              'gif': 'image/gif', 'webp': 'image/webp'}
    media_type = mt_map.get(ext, 'image/jpeg')

    try:
        from anthropic import Anthropic
        client = Anthropic(api_key=api_key)
        r = client.messages.create(
            model=os.environ.get('CLAUDE_MODEL', 'claude-sonnet-4-5-20250929'),
            max_tokens=200,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": img_b64}},
                {"type": "text", "text": (
                    "Describe this image in 2–3 sentences for use as context in a social media post. "
                    "Focus on what's visually interesting, the mood, location if apparent, "
                    "and any details that could inspire a story."
                )},
            ]}]
        )
        return jsonify({'description': r.content[0].text.strip()})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/analytics/instagram', methods=['POST'])
def receive_instagram_analytics():
    """Cowork Chrome task sends reel stats here every 2 days."""
    if request.headers.get('X-API-Key', '') != os.environ.get('API_SECRET_KEY', API_SECRET):
        return jsonify({'error': 'Unauthorized'}), 401

    # Rate limit: 1 ingestion per 24 hours
    with get_db() as conn:
        cutoff = (datetime.datetime.now() - datetime.timedelta(hours=24)).isoformat()
        recent = conn.execute(
            "SELECT id FROM instagram_reel_analytics WHERE collected_at > ? LIMIT 1", (cutoff,)
        ).fetchone()
        if recent:
            return jsonify({'ok': True, 'message': 'Rate limited — last ingestion within 24h'})

    data = request.json or {}
    reels = data.get('reels', [])
    if not reels:
        return jsonify({'error': 'No reel data'}), 400

    with get_db() as conn:
        for reel in reels:
            saves = reel.get('saves', 0)
            reach = max(reel.get('reach', 1), 1)
            save_rate = saves / reach
            tier = 'top' if save_rate > 0.04 else ('mid' if save_rate > 0.02 else 'low')

            conn.execute(
                "INSERT OR REPLACE INTO instagram_reel_analytics "
                "(id, reel_url, caption_preview, views, likes, saves, shares, reach, comments, "
                "hook_type, performance_tier, collected_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(uuid.uuid4()),
                    reel.get('reel_url', ''),
                    (reel.get('caption_preview', '') or '')[:100],
                    reel.get('views', 0), reel.get('likes', 0), reel.get('saves', 0),
                    reel.get('shares', 0), reel.get('reach', 0), reel.get('comments', 0),
                    reel.get('hook_type', 'unknown'), tier,
                    datetime.datetime.now().isoformat(),
                )
            )
        conn.commit()

    # Trigger async intel refresh to update analytics insights
    t = threading.Thread(target=fetch_and_cache_intel)
    t.daemon = True
    t.start()

    return jsonify({'ok': True, 'reels_ingested': len(reels)})


# ── Built-in Knowledge Base seed data ─────────────────────────────────────────
_VOICE_PROFILE = {
    "identity": (
        "Mir Tahmid Ali. Community-first systems architect. From Lalgola, West Bengal. "
        "Grew up in hostels from age 10. Learned adaptability before identity. "
        "Father passed away while Mir was traveling abroad. Was not physically present. Saw it on a screen. "
        "That shaped his urgency around stability, structure, and not wasting time. "
        "5 years with early-stage founders. One unicorn. One YC company. "
        "Builder: Adda Cafe (broke even 3 months, hired mostly women including his mom), "
        "Prompt Engineers (2,000 members no ads, newsletter 800+), Bit Billionaire (10,000 Telegram in 2 months), "
        "Prebites (200 to 2,000, 600+ colleges), Xoogler (30,000+ ex-Googlers), Skillza (kids+parents+mentors), Reworld (UGC gaming India). "
        "Now at Passionfroot: B2B creator sourcing, outreach, onboarding across LinkedIn, Instagram, newsletters, YouTube, TikTok. "
        "Slow traveler. One month per destination. Beach-adjacent. Local immersion. Indian passport. 12+ countries."
    ),
    "signature_angle": (
        "Empathy and systems thinking together. Most people are one or the other. "
        "He brings both. Structured and emotionally intelligent. "
        "Builds places people want to belong to. Cafe. Discord. Telegram. Creator marketplace. "
        "Not chasing attention. Chasing belonging and turning it into momentum."
    ),
    "writing_style": (
        "Short declarative sentences. Each on its own line.\n"
        "Real numbers. Real examples. No filler.\n"
        "Emotional but grounded. No hedging. No overhype.\n"
        "Blank line between every paragraph.\n"
        "Rubel's readability. Taylor's emotional arc. Conversational humility.\n"
        "Talks to ONE friend, not AN audience.\n"
        "Never lecture mode. Never guru energy. Never preachy."
    ),
    "tone": (
        "Mir talking to a friend. Not a brand talking to followers. "
        "Direct. Warm. Specific. Honest without being harsh. "
        "Smart friend who has been there. Not a motivational speaker. "
        "No cheesy tone. No corporate warmth. No performative vulnerability. No humble-bragging."
    ),
    "never_say": (
        "leverage, synergy, circle back, game changer, unlock, I am excited to share, "
        "thrilled to announce, dive into, double down, move the needle, paradigm shift, "
        "disruptive, scalable, ecosystem, bandwidth, think outside the box, "
        "in today's fast-paced world, the journey, I am proud to, let that sink in, "
        "unpopular opinion (as cliche opener), this is your sign, normalize, at the end of the day"
    ),
    "never_use_punctuation": (
        "NEVER use em dashes (the long dash symbol). They are an AI tell. "
        "Replace with a comma, period, or line break. "
        "No dramatic ellipsis. No excessive exclamation marks."
    ),
    "never_start_with": (
        "The word 'I' as the first word. "
        "'In today's world' or 'In a world where'. "
        "'As a [title]'. "
        "'I'm thrilled/excited/proud'. "
        "'Let's talk about'."
    ),
    "linkedin_structure": (
        "Hook: pattern interrupt. Stops scroll. Surprising stat, counterintuitive claim, relatable frustration. Never starts with I.\n"
        "Emotional layer: human side. What did this feel like? Why does it matter?\n"
        "Core insight: one thing. Systems thinking applied to a real situation.\n"
        "CTA: genuine question or soft invite. Never salesy. Often: 'What is your take?'"
    ),
    "instagram_structure": (
        "Visual hook: first frame stops scroll. Short punchy lines. Scene plus emotion.\n"
        "Scroll stop: first 5 seconds give them reason to keep watching.\n"
        "Relatable angle: personal story. Specific location. Specific moment.\n"
        "Value loop: end with something memorable. Often loops to opening."
    ),
    "newsletter_structure": (
        "Subject: curiosity-gap or direct benefit.\n"
        "Headline: bold article-style title for Beehiiv top.\n"
        "Body: 200 words. Personal note style. No section headers.\n"
        "Opening: specific stat or real situation. Never 'Hi everyone'.\n"
        "Core: one insight, one real number or story.\n"
        "Closing: one quotable sentence."
    ),
    "content_pillars_linkedin": (
        "B2B creator and influencer marketing (trust vs reach). "
        "Community building as a growth strategy. "
        "Founder-adjacent operating. "
        "Creator economy mechanics. "
        "AI tools for operators and marketers."
    ),
    "content_pillars_instagram": (
        "Slow travel on an Indian passport. "
        "How to find and land remote jobs. "
        "Digital nomad logistics. "
        "Cultural immersion and local market stories. "
        "Building a free and structured life."
    ),
    "key_numbers": (
        "Prompt Engineers: 2,000 members no paid ads. Newsletter 800+. "
        "Bit Billionaire: 10,000 in 2 months. "
        "Prebites: 200 to 2,000 across 600+ colleges. "
        "Xoogler: 30,000+ ex-Googlers. "
        "Adda Cafe: break-even in 3 months. "
        "5 years early-stage startups. One unicorn. One YC. "
        "12+ countries as slow traveler."
    ),
    "what_mir_dislikes_in_content": (
        "Performative vulnerability. Humble-bragging as lessons. "
        "Generic advice without real numbers or situations. "
        "Corporate warmth, forced enthusiasm, excessive exclamation marks. "
        "Overly polished thought-leader energy. "
        "Lists that substitute for real thinking. "
        "Em dashes for dramatic pauses. "
        "Opening with I or In today's world. "
        "Motivational platitudes with no grounding in real experience."
    ),
    "monitored_linkedin_accounts": (
        "These are creators Mir watches on LinkedIn to understand what content patterns are working. "
        "Not to copy — to spot outliers and understand what's resonating in his domain.\n"
        "Justin Welsh — solopreneur/creator economy, clean personal brand, high-converting newsletters\n"
        "Matt Gray — building in public, content systems, audience compounding\n"
        "Lara Acosta — personal brand building, creator economy, from 0 to audience\n"
        "Richard van der Blom — LinkedIn algorithm research, what actually gets reach\n"
        "Sam Szuchan — B2B influencer ROI, creator deal structures\n"
        "Jasmin Alic — LinkedIn content patterns, copy that converts\n"
        "Sahil Bloom — systems + storytelling, personal finance narratives\n"
        "Katelyn Bourgoin — B2B buyer psychology, why people buy\n"
        "Steph Smith — remote work, digital goods, contrarian tech takes"
    ),
    "monitored_instagram_accounts": (
        "These are creators Mir watches on Instagram to understand what's resonating in slow travel and nomad content.\n"
        "Not to copy — to understand visual hooks, trending formats, what the audience responds to.\n"
        "Indian passport travel creators — visa hacks, affordable destinations, the struggle and joy\n"
        "@tanay.p — tech + travel content, Indian founder abroad\n"
        "@thewanderingquinn — slow travel philosophy, place immersion\n"
        "@nomadnumbers — cost breakdowns, where to live and work\n"
        "@becomingminimalist — living with less while seeing more\n"
        "Remote work + SEA (Southeast Asia) content — Bali, Chiang Mai, Tbilisi reels\n"
        "Indian passport + Schengen visa content — high search volume, high frustration, high community"
    ),
}

_STORIES = [
    {
        "title": "Adda Cafe — broke even in 3 months",
        "platform": "linkedin",
        "tags": ["community", "startup", "offline"],
        "story_snippet": (
            "Adda Cafe was a community cafe Mir helped build. "
            "The goal was to create a space where people could just exist together — "
            "no pressure to buy, no rush to leave. "
            "It broke even in 3 months. Not because of a brilliant business model. "
            "Because people felt like they belonged there. "
            "That's the thing about community — when it works, the economics follow. "
            "Nobody tells you that in a startup course."
        ),
    },
    {
        "title": "Prompt Engineers — 2,000 members without ads",
        "platform": "linkedin",
        "tags": ["community", "growth", "no-ads", "creator"],
        "story_snippet": (
            "Prompt Engineers hit 2,000 members without a single paid ad. "
            "No growth hacks. No referral loops. No viral tweet. "
            "Just a clear problem (AI felt inaccessible to non-technical people), "
            "a clear who (operators and marketers), "
            "and a space where they could learn without feeling dumb. "
            "The lesson: specificity is a growth strategy. "
            "The more precise your 'for who,' the faster it spreads."
        ),
    },
    {
        "title": "Bit Billionaire — 10,000 members in crypto education",
        "platform": "linkedin",
        "tags": ["community", "growth", "crypto", "education"],
        "story_snippet": (
            "Bit Billionaire grew to 10,000 members teaching crypto to beginners. "
            "The dominant communities at the time were for traders and degens. "
            "Mir saw a gap: nobody was teaching the fundamentals without the hype. "
            "So that's what Bit Billionaire did. "
            "Gap-first thinking. Not 'what's trending' but 'what's missing.' "
            "That's how you build something that lasts past the cycle."
        ),
    },
    {
        "title": "Hostel kid — grew up in hostels from age 10",
        "platform": "both",
        "tags": ["origin", "community", "belonging", "personal"],
        "story_snippet": (
            "Mir grew up in hostels from the age of 10. Father passed away. "
            "Living in shared spaces with strangers became normal — and then necessary. "
            "He learned to read rooms fast. "
            "To sense who was lonely, who was guarded, who needed an easy entry point. "
            "That's where the community instinct comes from. "
            "Not a framework. Not a course. A childhood spent figuring out how to belong."
        ),
    },
    {
        "title": "Indian passport nomad — traveling the world on a difficult passport",
        "platform": "instagram",
        "tags": ["travel", "nomad", "indian-passport", "freedom"],
        "story_snippet": (
            "Traveling the world on an Indian passport means planning 3x harder than most. "
            "Visa applications. Rejection. Waiting. Replanning. "
            "The countries that feel effortless for a European passport holder "
            "require weeks of prep for Mir. "
            "And yet — he's lived in 12+ countries as a slow traveler. "
            "One month per destination. Culturally immersive. Beach-adjacent. "
            "The constraint became the skill. "
            "When you're used to planning around walls, you find doors nobody else noticed."
        ),
    },
    {
        "title": "Passionfroot — community and creator partnerships",
        "platform": "linkedin",
        "tags": ["work", "creator-economy", "B2B", "partnerships"],
        "story_snippet": (
            "At Passionfroot, Mir works at the intersection of community and creator partnerships. "
            "The job: help B2B brands find and work with the right creators. "
            "What he keeps seeing: brands optimising for reach, not resonance. "
            "They pick creators with big numbers and wonder why the campaigns feel flat. "
            "The metric that actually matters is trust — "
            "how much does this creator's audience trust their recommendations? "
            "That's not a number you find in a media kit."
        ),
    },
    {
        "title": "Xoogler — community for ex-Google employees",
        "platform": "linkedin",
        "tags": ["community", "niche", "identity", "belonging"],
        "story_snippet": (
            "Xoogler is a community for people who used to work at Google. "
            "On paper, it sounds exclusive. In practice, it's about transition — "
            "leaving a brand identity that defined you for years and figuring out what's next. "
            "The power of identity-based communities: "
            "people don't join because of the features. They join because they see themselves. "
            "'That's for people like me' is the most powerful sentence in community building."
        ),
    },
]


def auto_seed():
    """Always update voice profile keys; only seed stories if empty."""
    try:
        with get_db() as conn:
            now = datetime.datetime.now().isoformat()

            # Always update voice profile (ensures new keys and updated content get written)
            for key, value in _VOICE_PROFILE.items():
                existing = conn.execute("SELECT id FROM kb_voice_profile WHERE key = ?", (key,)).fetchone()
                if existing:
                    conn.execute("UPDATE kb_voice_profile SET value = ?, updated_at = ? WHERE key = ?", (value, now, key))
                else:
                    conn.execute(
                        "INSERT INTO kb_voice_profile (id, key, value, updated_at) VALUES (?, ?, ?, ?)",
                        (str(uuid.uuid4()), key, value, now)
                    )
            logger.info(f"Voice profile updated: {len(_VOICE_PROFILE)} keys")

            # Story bank
            story_count = conn.execute("SELECT COUNT(*) as c FROM kb_story_bank").fetchone()['c']
            if story_count == 0:
                for story in _STORIES:
                    conn.execute(
                        "INSERT INTO kb_story_bank (id, title, story_snippet, platform, tags, times_used, created_at) "
                        "VALUES (?, ?, ?, ?, ?, 0, ?)",
                        (
                            str(uuid.uuid4()),
                            story['title'],
                            story['story_snippet'],
                            story['platform'],
                            json.dumps(story['tags']),
                            now,
                        )
                    )
                logger.info(f"Story bank seeded: {len(_STORIES)} stories")

            conn.commit()
    except Exception as e:
        logger.error(f"auto_seed error: {e}")


@app.route('/api/admin/seed', methods=['POST'])
def admin_seed():
    """Force re-seed the knowledge base (clears and re-inserts)."""
    try:
        with get_db() as conn:
            now = datetime.datetime.now().isoformat()
            # Always re-seed voice profile
            for key, value in _VOICE_PROFILE.items():
                conn.execute(
                    "INSERT OR REPLACE INTO kb_voice_profile (id, key, value, updated_at) VALUES (?, ?, ?, ?)",
                    (str(uuid.uuid4()), key, value, now)
                )
            # Re-seed stories (only if missing)
            for story in _STORIES:
                existing = conn.execute(
                    "SELECT id FROM kb_story_bank WHERE title = ?", (story['title'],)
                ).fetchone()
                if not existing:
                    conn.execute(
                        "INSERT INTO kb_story_bank (id, title, story_snippet, platform, tags, times_used, created_at) "
                        "VALUES (?, ?, ?, ?, ?, 0, ?)",
                        (
                            str(uuid.uuid4()), story['title'], story['story_snippet'],
                            story['platform'], json.dumps(story['tags']), now,
                        )
                    )
            conn.commit()
        kb_count = len(_VOICE_PROFILE)
        story_count = len(_STORIES)
        return jsonify({'ok': True, 'voice_keys': kb_count, 'stories': story_count})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── Scheduler ─────────────────────────────────────────────────────────────────
scheduler = BackgroundScheduler(daemon=True)
scheduler.add_job(fetch_and_cache_intel, 'cron', hour=6, minute=0)
scheduler.start()

# ── Start ─────────────────────────────────────────────────────────────────────
init_db()
auto_seed()
logger.info("Mir Studio is live.")

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
