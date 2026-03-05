"""
seed_kb.py — Pre-loads Mir's voice profile and story bank into the database.
Run once after first deployment: python seed_kb.py
Safe to re-run — uses INSERT OR REPLACE.
"""

import sqlite3
import uuid
import json
import datetime
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
DB_PATH = os.environ.get('DB_PATH', 'mir_studio.db')


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ── Voice Profile ─────────────────────────────────────────────────────────────
# Extracted from Mir's 27-page profile document
VOICE_PROFILE = {
    "identity": (
        "Community-first systems architect designing autonomy across geography, income, and impact. "
        "5 years with early-stage startups. Builder of Adda Cafe, Prompt Engineers (2K members), "
        "Bit Billionaire (10K members), Prebites, Xoogler, Passionfroot. "
        "Slow traveler. Indian passport. Grew up in hostels from age 10."
    ),
    "signature_angle": (
        "Empathy + systems thinking — together. Most people are one or the other. "
        "Mir brings both. He reads rooms fast (hostel kid) and builds systems that last."
    ),
    "writing_style": (
        "Short declarative sentences.\n"
        "Each on its own line.\n"
        "Real numbers. Real examples.\n"
        "No fluff. No overhype. No hedging.\n"
        "Emotional but grounded.\n"
        "Systems thinking woven into every post.\n"
        "Each paragraph is 1–2 sentences max.\n"
        "Blank line between every paragraph."
    ),
    "tone": (
        "Authentic. No corporate speak. No performative vulnerability. "
        "Tells hard truths gently. Writes like he talks — direct, warm, specific. "
        "Never preachy. Never humble-brags. "
        "Sounds like a smart friend who's been there, not a guru lecturing from above."
    ),
    "never_say": (
        "leverage, synergy, circle back, game changer, unlock, I'm excited to share, "
        "thrilled to announce, dive into, double down, move the needle, at the end of the day, "
        "it is what it is, paradigm shift, disruptive, scalable, ecosystem, bandwidth, "
        "drill down, take it to the next level, think outside the box"
    ),
    "never_start_with": (
        "I  (never start a post with the word I) — "
        "In today's world  (too generic) — "
        "As a [title]  (too self-referential) — "
        "I'm thrilled / excited / proud  (performative)"
    ),
    "linkedin_structure": (
        "1. HOOK — Pattern interrupt. First line must stop the scroll. "
        "A surprising stat, a counterintuitive claim, or a relatable frustration. Never start with 'I'.\n"
        "2. EMOTIONAL LAYER — The human side. What did this feel like? Why should they care?\n"
        "3. CORE INSIGHT — The one thing. Systems thinking applied to a real situation.\n"
        "4. CTA — A genuine question or a soft invitation. Never salesy. Often: 'What's your take?'"
    ),
    "instagram_structure": (
        "1. VISUAL HOOK — The first frame must make them stop scrolling. "
        "One or two short punchy sentences. Set the scene visually AND emotionally.\n"
        "2. SCROLL STOP — First 5 seconds = they decide to keep watching or not. "
        "Must give them a reason to stay.\n"
        "3. RELATABLE ANGLE — Personal story. Specific location. Specific moment. "
        "They see themselves in it.\n"
        "4. VALUE LOOP — End with something they'll remember or want to act on. "
        "Often loops back to the opening image."
    ),
    "newsletter_structure": (
        "Subject line: curiosity-gap or direct benefit — optimised for open rate.\n"
        "Headline: bold article-style title shown at top of email in Beehiiv.\n"
        "Body: 200 words. Personal note style. No section headers inside body.\n"
        "Opening: specific stat, surprising claim, or relatable situation — never 'Hi everyone'.\n"
        "Core: one insight, one real number or story, no filler.\n"
        "Closing: one quotable sentence. Optional: 'Reply and tell me what you think.'"
    ),
    "content_pillars_linkedin": (
        "B2B influencer & creator marketing · "
        "Community building & management · "
        "Remote work culture & strategy · "
        "AI tools for operators & marketers · "
        "Startup & founder insights"
    ),
    "content_pillars_instagram": (
        "Slow travel across countries (voice-over reels) · "
        "How to find & land remote jobs · "
        "Digital nomad logistics (Indian passport) · "
        "Cultural immersion & local market stories · "
        "Building a free and structured life"
    ),
    "influences": (
        "Building in public, outcome-first storytelling, systems thinking applied to human problems. "
        "Paul Graham (sharp essays), James Clear (clear frameworks), "
        "Morgan Housel (storytelling through observation)."
    ),
    "key_numbers": (
        "Prompt Engineers: 2,000 members built without paid ads. "
        "Bit Billionaire: 10,000 members. "
        "Adda Cafe: broke even in 3 months. "
        "5 years with early-stage startups. "
        "12+ countries lived in as a slow traveler."
    ),
}

# ── Story Bank ────────────────────────────────────────────────────────────────
STORIES = [
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


def seed():
    with get_db() as conn:
        now = datetime.datetime.now().isoformat()

        # Seed voice profile
        for key, value in VOICE_PROFILE.items():
            conn.execute(
                "INSERT OR REPLACE INTO kb_voice_profile (id, key, value, updated_at) VALUES (?, ?, ?, ?)",
                (str(uuid.uuid4()), key, value, now)
            )

        # Seed story bank
        for story in STORIES:
            # Check if story with same title already exists
            existing = conn.execute(
                "SELECT id FROM kb_story_bank WHERE title = ?", (story['title'],)
            ).fetchone()
            if not existing:
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

        # Ensure app_stats row exists
        stats = conn.execute("SELECT id FROM app_stats WHERE id = 1").fetchone()
        if not stats:
            conn.execute(
                "INSERT INTO app_stats (id, streak_count, total_linkedin, total_newsletter, total_instagram) "
                "VALUES (1, 0, 0, 0, 0)"
            )

        conn.commit()

    print(f"✅  Voice profile seeded: {len(VOICE_PROFILE)} keys")
    print(f"✅  Story bank seeded: {len(STORIES)} stories")
    print("✅  Mir Studio KB is ready.")


if __name__ == '__main__':
    from app import init_db
    init_db()
    seed()
