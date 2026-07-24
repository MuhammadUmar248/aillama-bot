"""
generate.py
Step 1 of the daily pipeline:
  1. Pull latest AI news from RSS feeds
  2. Skip anything already posted (posted.json)
  3. Ask an LLM (Groq / Llama 3.3) to turn it into a 4-slide carousel script
     (title, "the story", "why it matters", follow CTA)
  4. Render all 4 slides on a FIXED brand template (same gradient, fonts,
     colors every time) with Pillow, so every post looks consistent
  5. Write pending_post.json describing what still needs to be published

The actual Instagram publish step happens in publish.py, AFTER these images
have been committed & pushed to the repo (so they have public raw URLs).
"""

import json
import os
import textwrap
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import feedparser
import requests
from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

RSS_FEEDS = [
    "https://techcrunch.com/category/artificial-intelligence/feed/",
    "https://venturebeat.com/category/ai/feed/",
    "https://news.google.com/rss/search?q=artificial%20intelligence%20when:1d&hl=en-US&gl=US&ceid=US:en",
    "http://export.arxiv.org/rss/cs.AI",
]

POSTED_FILE = "posted.json"
IMAGE_DIR = "images"

BOLD_FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
REGULAR_FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

GROQ_API_KEY = os.environ["GROQ_API_KEY"]
GITHUB_REPOSITORY = os.environ.get("GITHUB_REPOSITORY", "")  # e.g. "yourname/aillama-bot"
BRANCH = os.environ.get("BRANCH", "main")

LOOKBACK_HOURS = 30  # how far back to consider "fresh" news

# ---- Brand look — kept identical across every slide of every post ----
IMG_SIZE = 1080
BG_TOP = (12, 18, 36)        # deep navy
BG_BOTTOM = (35, 20, 66)     # deep purple
ACCENT = (255, 209, 102)     # gold
TEXT_WHITE = (240, 242, 248)
TEXT_MUTED = (170, 176, 196)
INSTAGRAM_HANDLE = "@aillama.daily"
BRAND_LINE = "\U0001F999 AI LLAMA DAILY"


# ---------------------------------------------------------------------------
# STEP 1: fetch + dedupe
# ---------------------------------------------------------------------------

def load_posted():
    if os.path.exists(POSTED_FILE):
        with open(POSTED_FILE) as f:
            return set(json.load(f))
    return set()


def fetch_candidates(posted):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    candidates = []

    for feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
        except Exception as e:
            print(f"Failed to parse {feed_url}: {e}")
            continue

        for entry in feed.entries:
            link = entry.get("link")
            if not link or link in posted:
                continue

            published = None
            if getattr(entry, "published_parsed", None):
                published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)

            if published and published < cutoff:
                continue

            candidates.append({
                "title": entry.get("title", "").strip(),
                "summary": entry.get("summary", entry.get("title", "")).strip(),
                "link": link,
                "published": published or datetime.now(timezone.utc),
            })

    candidates.sort(key=lambda c: c["published"], reverse=True)
    return candidates


# ---------------------------------------------------------------------------
# STEP 2: LLM content generation (Groq, free tier, Llama 3.3 70B)
# ---------------------------------------------------------------------------

def generate_content(story):
    prompt = f"""You are the content writer for an Instagram page called "AI Llama Daily"
that posts a 4-slide carousel brief for each AI news story.

News headline: {story['title']}
Summary: {story['summary']}

Write the carousel script:
1. hook_title: slide 1 headline, max 7 words, punchy, no hashtags, no period
2. story_text: slide 2 body, 2-3 short sentences plainly explaining what happened
3. impact_text: slide 3 body, 2-3 short sentences on why it matters / what's next
4. caption: an Instagram caption (3-5 sentences, engaging, plain language, 1-2 emojis,
   ends with a question), followed by 6-10 relevant hashtags IN THE SAME "caption" STRING

Respond with ONLY a single valid JSON object and nothing else — no markdown
fences, no commentary, no alternate versions, no explanation before or after it.
Inside the JSON string values, any line break MUST be written as the two
characters backslash-n (a JSON-escaped newline) — never a literal line break.
The JSON object must have EXACTLY these four keys, no others:
{{"hook_title": "...", "story_text": "...", "impact_text": "...", "caption": "..."}}
"""
    resp = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
        json={
            "model": "llama-3.3-70b-versatile",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.6,
            "response_format": {"type": "json_object"},
        },
        timeout=60,
    )
    resp.raise_for_status()
    text = resp.json()["choices"][0]["message"]["content"].strip()

    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()

    decoder = json.JSONDecoder(strict=False)
    try:
        obj, _end_index = decoder.raw_decode(text)
    except json.JSONDecodeError:
        print("Raw model output that failed to parse:\n", text)
        raise

    for key in ("hook_title", "story_text", "impact_text", "caption"):
        if key not in obj:
            raise ValueError(f"Model response missing required key '{key}': {obj}")

    return obj


# ---------------------------------------------------------------------------
# STEP 3: branded slide rendering (fixed template, Pillow only — no external
# image API — so every post looks identical in style)
# ---------------------------------------------------------------------------

def brand_canvas():
    """A fresh 1080x1080 canvas with the same gradient + subtle dot grid
    every single time, so all slides (and all posts) share one look."""
    img = Image.new("RGB", (IMG_SIZE, IMG_SIZE))
    draw = ImageDraw.Draw(img)
    for y in range(IMG_SIZE):
        t = y / IMG_SIZE
        r = int(BG_TOP[0] + (BG_BOTTOM[0] - BG_TOP[0]) * t)
        g = int(BG_TOP[1] + (BG_BOTTOM[1] - BG_TOP[1]) * t)
        b = int(BG_TOP[2] + (BG_BOTTOM[2] - BG_TOP[2]) * t)
        draw.line([(0, y), (IMG_SIZE, y)], fill=(r, g, b))

    draw = ImageDraw.Draw(img, "RGBA")
    spacing = 54
    for x in range(0, IMG_SIZE, spacing):
        for y in range(0, IMG_SIZE, spacing):
            draw.ellipse([x, y, x + 2, y + 2], fill=(255, 255, 255, 22))

    return img


def draw_footer(draw, page_label):
    footer_font = ImageFont.truetype(BOLD_FONT_PATH, 30)
    page_font = ImageFont.truetype(REGULAR_FONT_PATH, 26)
    draw.text((60, IMG_SIZE - 70), BRAND_LINE, font=footer_font, fill=ACCENT)
    bbox = draw.textbbox((0, 0), page_label, font=page_font)
    w = bbox[2] - bbox[0]
    draw.text((IMG_SIZE - 60 - w, IMG_SIZE - 66), page_label, font=page_font, fill=TEXT_MUTED)


def slide_title(hook_title):
    img = brand_canvas()
    draw = ImageDraw.Draw(img, "RGBA")

    kicker_font = ImageFont.truetype(BOLD_FONT_PATH, 34)
    title_font = ImageFont.truetype(BOLD_FONT_PATH, 84)

    draw.text((60, 300), "AI NEWS BRIEF", font=kicker_font, fill=ACCENT)

    wrapped = textwrap.fill(hook_title.upper(), width=14)
    draw.multiline_text((60, 370), wrapped, font=title_font, fill=TEXT_WHITE, spacing=16)

    draw_footer(draw, "1/4")
    return img


def slide_body(heading, body_text, page_label):
    img = brand_canvas()
    draw = ImageDraw.Draw(img, "RGBA")

    heading_font = ImageFont.truetype(BOLD_FONT_PATH, 46)
    body_font = ImageFont.truetype(REGULAR_FONT_PATH, 46)

    draw.text((60, 140), heading.upper(), font=heading_font, fill=ACCENT)
    draw.line([(60, 210), (280, 210)], fill=ACCENT, width=4)

    wrapped = textwrap.fill(body_text, width=26)
    draw.multiline_text((60, 280), wrapped, font=body_font, fill=TEXT_WHITE, spacing=18)

    draw_footer(draw, page_label)
    return img


def slide_follow():
    img = brand_canvas()
    draw = ImageDraw.Draw(img, "RGBA")

    llama_font = ImageFont.truetype(BOLD_FONT_PATH, 140)
    cta_font = ImageFont.truetype(BOLD_FONT_PATH, 58)
    handle_font = ImageFont.truetype(REGULAR_FONT_PATH, 40)

    draw.text((IMG_SIZE / 2 - 90, 320), "\U0001F999", font=llama_font, fill=ACCENT)

    cta_wrapped = textwrap.fill("FOLLOW FOR DAILY AI NEWS", width=14)
    bbox = draw.multiline_textbbox((0, 0), cta_wrapped, font=cta_font, align="center")
    w = bbox[2] - bbox[0]
    draw.multiline_text(((IMG_SIZE - w) / 2, 520), cta_wrapped, font=cta_font,
                         fill=TEXT_WHITE, spacing=14, align="center")

    bbox2 = draw.textbbox((0, 0), INSTAGRAM_HANDLE, font=handle_font)
    w2 = bbox2[2] - bbox2[0]
    draw.text(((IMG_SIZE - w2) / 2, 660), INSTAGRAM_HANDLE, font=handle_font, fill=ACCENT)

    draw_footer(draw, "4/4")
    return img


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    posted = load_posted()
    candidates = fetch_candidates(posted)

    if not candidates:
        print("No fresh, unposted AI stories found today. Skipping.")
        return

    story = candidates[0]
    print("Selected story:", story["title"])

    content = generate_content(story)
    print("Generated content:", content)

    slides = [
        slide_title(content["hook_title"]),
        slide_body("The Story", content["story_text"], "2/4"),
        slide_body("Why It Matters", content["impact_text"], "3/4"),
        slide_follow(),
    ]

    os.makedirs(IMAGE_DIR, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    image_urls = []

    for i, slide_img in enumerate(slides, start=1):
        filename = f"{IMAGE_DIR}/{stamp}_{i}.png"
        slide_img.save(filename, "PNG")
        image_urls.append(f"https://raw.githubusercontent.com/{GITHUB_REPOSITORY}/{BRANCH}/{filename}")

    with open("pending_post.json", "w") as f:
        json.dump({
            "link": story["link"],
            "caption": content["caption"],
            "image_urls": image_urls,
        }, f, indent=2)

    print("Wrote pending_post.json with", len(image_urls), "slides.")


if __name__ == "__main__":
    main()
