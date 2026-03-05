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
    # LinkedIn trending — Mir's domain
    ("LinkedIn Pulse — Creator Economy",  "https://news.google.com/rss/search?q=creator+economy+linkedin+site:linkedin.com&tbs=qdr:w&hl=en-US&gl=US&ceid=US:en", "linkedin_trending"),
    ("LinkedIn Pulse — Community Building","https://news.google.com/rss/search?q=community+building+linkedin&tbs=qdr:w&hl=en-US&gl=US&ceid=US:en", "linkedin_trending"),
    ("LinkedIn Pulse — B2B Creators",     "https://news.google.com/rss/search?q=B2B+influencer+marketing+linkedin&tbs=qdr:w&hl=en-US&gl=US&ceid=US:en", "linkedin_trending"),
    ("LinkedIn Pulse — Influencer ROI",   "https://news.google.com/rss/search?q=influencer+marketing+ROI+B2B&tbs=qdr:w&hl=en-US&gl=US&ceid=US:en", "linkedin_trending"),
    # Instagram trending — Mir's domain
    ("Instagram Trend — Digital Nomad",   "https://news.google.com/rss/search?q=digital+nomad+instagram+trending&tbs=qdr:w&hl=en-US&gl=US&ceid=US:en", "instagram_trending"),
    ("Instagram Trend — Remote Work",     "https://news.google.com/rss/search?q=remote+work+lifestyle+instagram+reel&tbs=qdr:w&hl=en-US&gl=US&ceid=US:en", "instagram_trending"),
    ("Instagram Trend — Slow Travel",     "https://news.google.com/rss/search?q=slow+travel+instagram+viral&tbs=qdr:w&hl=en-US&gl=US&ceid=US:en", "instagram_trending"),
    # Industry blogs (fast-updating)
    ("Social Media Examiner",  "https://www.socialmediaexaminer.com/feed/",         "industry"),
    ("Buffer Blog",            "https://buffer.com/resources/feed/",                "industry"),
    ("Sprout Social Insights", "https://sproutsocial.com/insights/feed/",           "industry"),
    ("Creator Economy Newsletter", "https://news.google.com/rss/search?q=creator+economy+newsletter+2026&tbs=qdr:w&hl=en-US&gl=US&ceid=US:en", "industry"),
]


def fetch_and_cache_intel():
    """Pull RSS feeds → cache → Claude analysis → store in DB."""
    logger.info("Intel pipeline running...")
    api_key = os.environ.get('ANTHROPIC_API_KEY')

    articles = []
    for name, url, category in RSS_SOURCES:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:3]:
                summary = re.sub('<[^<]+?>', '', getattr(entry, 'summary', ''))[:280]
                articles.append({
                    'id': str(uuid.uuid4()),
                    'source': name,
                    'title': getattr(entry, 'title', ''),
                    'url': getattr(entry, 'link', ''),
                    'summary': summary,
                    'category': category,
                    'cached_at': datetime.datetime.now().isoformat(),
                })
        except Exception as e:
            logger.error(f"RSS error {name}: {e}")

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

        article_text = '\n'.join([
            f"- {a['title']} ({a['source']}): {a['summary']}"
            for a in articles[:30]
        ])

        with get_db() as conn:
            recent_rows = conn.execute(
                "SELECT topic FROM kb_topics_covered WHERE last_posted_at > ? "
                "ORDER BY last_posted_at DESC LIMIT 5",
                ((datetime.datetime.now() - datetime.timedelta(days=14)).isoformat(),)
            ).fetchall()
        recent_str = ', '.join([r['topic'] for r in recent_rows]) if recent_rows else 'none'

        response = client.messages.create(
            model=os.environ.get('CLAUDE_MODEL', 'claude-sonnet-4-5-20250929'),
            max_tokens=1800,
            messages=[{
                "role": "user",
                "content": f"""Analyze these articles from the PAST 7 DAYS for Mir Tahmid Ali.
Mir's domains: B2B creator marketing, community building, digital nomad / slow travel, remote work.

ARTICLES (with sources):
{article_text}

RECENTLY COVERED by Mir (avoid repeating): {recent_str}

For each intel item, include the source name and URL it came from.

Return ONLY valid JSON — no markdown, no explanation:
{{
  "linkedin_intel": [
    {{
      "topic": "...",
      "why_trending": "What is driving this conversation on LinkedIn right now",
      "hook": "A first-line hook for a LinkedIn post about this — never starts with I",
      "source_name": "...",
      "source_url": "...",
      "found_via": "Google News search for [term] past 7 days"
    }}
  ],
  "instagram_intel": [
    {{
      "topic": "...",
      "why_trending": "What is trending on Instagram in this space right now",
      "hook": "A visual hook for a reel about this",
      "source_name": "...",
      "source_url": "...",
      "found_via": "Google News search for [term] past 7 days"
    }}
  ],
  "content_gaps": [
    {{
      "angle": "...",
      "why": "Why nobody is writing this yet",
      "source_name": "...",
      "source_url": "..."
    }}
  ],
  "suggested_angles": [
    {{"idea": "Specific post idea in Mir's voice", "platform": "linkedin or instagram", "source_url": "..."}}
  ]
}}

2-3 items per section. Only include items from the past 7 days. Hyper-specific to Mir's niche."""
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
                    json.dumps(analysis.get('suggested_angles', [])),
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
            prompt = f"""Write a LinkedIn post for Mir. He is talking to a friend, not broadcasting to followers.

RAW THOUGHT:
{thought}
{context_block}

RULES:
- 150-250 words
- Short 1-2 sentence paragraphs, blank line between each (mobile-friendly)
- NEVER start with "I"
- NEVER use em dashes (the long dash). Use commas or periods instead.
- Structure: Hook (stops scroll, never starts with I) then emotional layer then one core insight then a real question or soft CTA
- End with 3-5 relevant hashtags on last line only
- Sounds like Mir talking to one specific friend. Not a thought leader. Not a brand.

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
            prompt = f"""Write a Beehiiv newsletter section for Mir. He is writing a personal note to a friend, not a newsletter blast.

RAW THOUGHT:
{thought}
{context_block}

RULES:
- NEVER use em dashes. Use commas or periods instead.
- NEVER start body with "In this issue", "Hi everyone", "Today we"
- Body: 200 words. Personal note style. No section headers inside body.
- First sentence: specific stat, surprising claim, or real situation
- At least one real number or concrete example
- Closing: one quotable sentence. Optionally: "Reply and tell me what you think."

OUTPUT FORMAT (use exactly these labels):

Subject: [curiosity-gap or direct-benefit subject line]
Headline: [bold article-style headline for Beehiiv top]
---
[Body here]

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
            prompt = f"""Write a 45-second Instagram reel voice-over script for @mirtheexplorer. Personal travel and remote work content.

RAW THOUGHT:
{thought}
{context_block}

RULES:
- Voice: talking to ONE friend. Not an audience.
- Mir speaks slowly with deliberate pauses. 75-90 words total for voice-over.
- NEVER use em dashes. Use commas or short sentences instead.
- Specific location. Specific moment. Real details. No generic travel wisdom.

OUTPUT FORMAT (exact labels):

HOOK (0-5s): [1-2 short sentences. Grabs in first breath. Visual + emotional.]
STORY (5-25s): [3-4 sentences. Specific location, time, situation. First-person, conversational.]
INSIGHT (25-40s): [1-2 sentences. The one takeaway. Works as a standalone quote.]
LOOP/CTA (40-45s): [1 sentence. Loops to hook image or next step. Never "subscribe".]

B-ROLL IDEAS:
1. [specific shot]
2. [specific shot]
3. [specific shot]
4. [specific shot]

THUMBNAIL: [one visual concept]

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
        return jsonify({
            'debates': json.loads(analysis['debates'] or '[]'),
            'gaps': json.loads(analysis['gaps'] or '[]'),
            'trending': json.loads(analysis['trending'] or '[]'),
            'suggested_angles': json.loads(analysis['suggested_angles'] or '[]'),
            'generated_at': analysis['generated_at'],
            'articles': [dict(a) for a in articles],
        })
    return jsonify({
        'debates': [], 'gaps': [], 'trending': [],
        'suggested_angles': [], 'generated_at': None, 'articles': [],
    })


@app.route('/api/intel/refresh', methods=['POST'])
def refresh_intel():
    t = threading.Thread(target=fetch_and_cache_intel)
    t.daemon = True
    t.start()
    return jsonify({'ok': True, 'message': 'Refreshing intel... check back in 30 seconds.'})


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
