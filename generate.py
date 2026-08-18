"""
generate.py
Step 1 of the daily pipeline:
  1. Pull latest AI news from RSS feeds
  2. Skip anything already posted (posted.json)
  3. Ask an LLM (Groq / Llama 3.3) to turn it into a 4-slide carousel script
     (title, "what's new" bullets, "why it matters" bullets, follow CTA)
  4. Render all 4 slides on a FIXED brand template (Poppins font, cream
     background, teal/gold accents, numbered badges, vector icons — no
     emoji/glyph dependency) so every post looks consistent
  5. Write pending_post.json describing what still needs to be published

The actual Instagram publish step happens in publish.py, AFTER these images
have been committed & pushed to the repo (so they have public raw URLs).
"""

import json
import os
import textwrap
from datetime import datetime, timedelta, timezone

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
FONT_DIR = "fonts"
BOLD_FONT = f"{FONT_DIR}/Poppins-Bold.ttf"
SEMIBOLD_FONT = f"{FONT_DIR}/Poppins-SemiBold.ttf"
REGULAR_FONT = f"{FONT_DIR}/Poppins-Regular.ttf"

GROQ_API_KEY = os.environ["GROQ_API_KEY"]
UNSPLASH_ACCESS_KEY = os.environ.get("UNSPLASH_ACCESS_KEY", "")
GITHUB_REPOSITORY = os.environ.get("GITHUB_REPOSITORY", "")  # e.g. "yourname/aillama-bot"
BRANCH = os.environ.get("BRANCH", "main")

LOOKBACK_HOURS = 30  # how far back to consider "fresh" news

# ---- Brand look — kept identical across every slide of every post ----
IMG_SIZE = 1080
BG_CREAM = (246, 242, 233)     # #F6F2E9
TEAL_DARK = (15, 118, 110)     # #0F766E
TEAL_LIGHT = (20, 184, 166)    # #14B8A6
GOLD = (245, 158, 11)          # #F59E0B
SLATE = (100, 116, 139)        # #64748B
TEXT_DARK = (31, 41, 55)
BRAND_HANDLE = "aillama.daily"


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
1. hook_title: slide 1 headline, max 9 words, no hashtags, no period.
   Write it like a scroll-stopping news hook — specific, concrete, creates a
   curiosity gap (readers want to know more). Use a real detail from the
   story (a name, number, or specific claim) instead of a generic phrase.
   Example style: "ChatGPT Just Broke a Coding Record" or
   "This AI Model Fooled Every Test It Took" — NOT generic phrases like
   "AI News Update" or "New AI Development".
2. image_query: 3-5 words describing a GENERIC stock-photo scene that fits
   this story's theme for a photo search — e.g. "robotics lab technology",
   "data center servers", "person coding laptop", "self driving car street".
   Never include a real person's name, company logo, or brand name here —
   describe a generic scene/subject only, since this drives a stock photo
   search.
3. story_points: a list of exactly 3 bullet phrases (9-13 words each, one
   full clear sentence), plainly explaining what happened, with enough
   detail to be genuinely informative on their own — not just a fragment
4. impact_points: a list of exactly 3 bullet phrases (9-13 words each, one
   full clear sentence) on why it matters / what happens next, similarly detailed
5. caption_paragraphs: a list of 3-4 SHORT strings, each one paragraph of an
   Instagram caption (1-2 sentences each), engaging, plain language, together
   telling the story and ending with a question to invite comments. Include
   1-2 emojis total across the paragraphs, not every paragraph.
6. hashtags: a list of exactly 8 hashtag words (no # symbol, no spaces, use
   CamelCase for multi-word tags). Pull most of them from SPECIFIC entities,
   products, companies, or technical terms actually named in this story
   (e.g. the model name, company name, technology). Include at most 2 broad
   generic AI tags (like ArtificialIntelligence or AINews) and make the rest
   specific and directly relevant to this story — not generic filler
   unrelated to the actual content.

Respond with ONLY a single valid JSON object and nothing else — no markdown
fences, no commentary, no alternate versions, no explanation before or after it.
The JSON object must have EXACTLY these six keys, no others:
{{"hook_title": "...", "image_query": "...", "story_points": ["...", "...", "..."], "impact_points": ["...", "...", "..."], "caption_paragraphs": ["...", "...", "..."], "hashtags": ["...", "...", "..."]}}
"""
    resp = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
        json={
            "model": "openai/gpt-oss-120b",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.7,
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

    for key in ("hook_title", "image_query", "story_points", "impact_points", "caption_paragraphs", "hashtags"):
        if key not in obj:
            raise ValueError(f"Model response missing required key '{key}': {obj}")

    return obj


def assemble_caption(content):
    """Join caption_paragraphs with real blank-line breaks, then the
    hashtags on their own line at the end. Building this in Python (rather
    than asking the LLM to hand-escape newlines inside one JSON string)
    avoids the literal backslash-n text bug entirely."""
    paragraphs = [p.strip() for p in content["caption_paragraphs"] if p.strip()]
    body = "\n\n".join(paragraphs)

    tags = [t.strip().lstrip("#") for t in content["hashtags"] if t.strip()]
    hashtag_line = " ".join(f"#{t}" for t in tags)

    return f"{body}\n\n{hashtag_line}"


# ---------------------------------------------------------------------------
# STEP 3: branded slide rendering (fixed template, Pillow only, Poppins font,
# every icon drawn as a vector shape — no emoji/glyph dependency)
# ---------------------------------------------------------------------------

def brand_canvas():
    """A fresh 1080x1080 canvas with the same cream background + decorative
    teal blob + dot grid every time, so all slides share one consistent look.
    All decoration lives in the top-right corner so it never collides with
    variable-length body text, which always renders bottom-left."""
    img = Image.new("RGB", (IMG_SIZE, IMG_SIZE), BG_CREAM)

    blob = Image.new("RGBA", (IMG_SIZE, IMG_SIZE), (0, 0, 0, 0))
    bdraw = ImageDraw.Draw(blob)
    bdraw.ellipse([760, -160, 1280, 360], fill=(*TEAL_LIGHT, 35))
    img.paste(blob, (0, 0), blob)

    draw = ImageDraw.Draw(img, "RGBA")
    for gx in range(5):
        for gy in range(5):
            x = 840 + gx * 26
            y = 40 + gy * 26
            draw.ellipse([x, y, x + 5, y + 5], fill=(*TEAL_DARK, 55))

    return img


def draw_number_badge(draw, number):
    draw.ellipse([60, 60, 140, 140], fill=TEAL_DARK)
    font = ImageFont.truetype(BOLD_FONT, 38)
    text = str(number)
    bbox = draw.textbbox((0, 0), text, font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text((100 - w / 2, 100 - h / 2 - bbox[1]), text, font=font, fill=(255, 255, 255))


def draw_kicker(draw, text):
    font = ImageFont.truetype(SEMIBOLD_FONT, 32)
    draw.text((162, 82), text.upper(), font=font, fill=TEAL_DARK)


def draw_brand_mark(draw, x, y):
    draw.ellipse([x, y, x + 34, y + 34], fill=TEAL_DARK)
    font = ImageFont.truetype(BOLD_FONT, 18)
    bbox = draw.textbbox((0, 0), "A", font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text((x + 17 - w / 2, y + 17 - h / 2 - bbox[1]), "A", font=font, fill=(255, 255, 255))


def draw_footer(draw, show_swipe):
    draw_brand_mark(draw, 60, 988)
    font = ImageFont.truetype(SEMIBOLD_FONT, 28)
    draw.text((104, 992), BRAND_HANDLE, font=font, fill=SLATE)

    if show_swipe:
        hint_font = ImageFont.truetype(SEMIBOLD_FONT, 28)
        text = "SWIPE"
        bbox = draw.textbbox((0, 0), text, font=hint_font)
        w = bbox[2] - bbox[0]
        tx = IMG_SIZE - 60 - w - 26
        draw.text((tx, 992), text, font=hint_font, fill=GOLD)
        ax, ay = IMG_SIZE - 60 - 16, 1006
        draw.polygon([(ax - 12, ay - 9), (ax - 12, ay + 9), (ax + 6, ay)], fill=GOLD)


def fetch_cover_photo(query):
    """Fetch a relevant, freely-licensed stock photo from Unsplash for the
    story's topic. Returns a local file path, or None if unavailable (no
    API key set, request failed, etc.) so the caller can fall back cleanly."""
    if not UNSPLASH_ACCESS_KEY:
        print("No UNSPLASH_ACCESS_KEY set — using fallback cover design.")
        return None
    try:
        resp = requests.get(
            "https://api.unsplash.com/photos/random",
            params={
                "query": query,
                "orientation": "squarish",
                "content_filter": "high",
            },
            headers={"Authorization": f"Client-ID {UNSPLASH_ACCESS_KEY}"},
            timeout=20,
        )
        resp.raise_for_status()
        photo_url = resp.json()["urls"]["regular"]

        img_resp = requests.get(photo_url, timeout=30)
        img_resp.raise_for_status()

        path = "cover_photo.jpg"
        with open(path, "wb") as f:
            f.write(img_resp.content)
        return path
    except Exception as e:
        print("Unsplash fetch failed, using fallback cover design:", e)
        return None


def draw_footer_on_photo(draw, show_swipe):
    """Same footer as draw_footer, but light-colored for a dark photo bg."""
    draw.ellipse([60, 988, 94, 1022], fill=(255, 255, 255))
    font_a = ImageFont.truetype(BOLD_FONT, 18)
    bbox_a = draw.textbbox((0, 0), "A", font=font_a)
    w, h = bbox_a[2] - bbox_a[0], bbox_a[3] - bbox_a[1]
    draw.text((77 - w / 2, 1005 - h / 2 - bbox_a[1]), "A", font=font_a, fill=TEAL_DARK)

    font = ImageFont.truetype(SEMIBOLD_FONT, 28)
    draw.text((104, 992), BRAND_HANDLE, font=font, fill=(255, 255, 255))

    if show_swipe:
        hint_font = ImageFont.truetype(SEMIBOLD_FONT, 28)
        text = "SWIPE"
        bbox = draw.textbbox((0, 0), text, font=hint_font)
        w2 = bbox[2] - bbox[0]
        tx = IMG_SIZE - 60 - w2 - 26
        draw.text((tx, 992), text, font=hint_font, fill=GOLD)
        ax, ay = IMG_SIZE - 60 - 16, 1006
        draw.polygon([(ax - 12, ay - 9), (ax - 12, ay + 9), (ax + 6, ay)], fill=GOLD)


def draw_bullets(draw, items, start_y, max_width_chars=34):
    body_font = ImageFont.truetype(REGULAR_FONT, 40)
    max_y = 960  # never draw past this — keeps clear of the footer at y=988
    y = start_y
    for item in items:
        if y > max_y:
            break
        draw.ellipse([60, y + 6, 92, y + 38], fill=GOLD)
        cx, cy = 76, y + 22
        draw.line([(cx - 8, cy), (cx - 2, cy + 7), (cx + 10, cy - 9)],
                  fill=(255, 255, 255), width=4, joint="curve")

        wrapped = textwrap.fill(item, width=max_width_chars)
        draw.multiline_text((116, y), wrapped, font=body_font, fill=TEXT_DARK, spacing=10)

        line_count = wrapped.count("\n") + 1
        y += line_count * 50 + 30
    return y


def slide_title(hook_title, image_query):
    photo_path = fetch_cover_photo(image_query)

    if photo_path:
        img = Image.open(photo_path).convert("RGB")
        # crop to square, centered
        w, h = img.size
        side = min(w, h)
        img = img.crop(((w - side) // 2, (h - side) // 2, (w + side) // 2, (h + side) // 2))
        img = img.resize((IMG_SIZE, IMG_SIZE)).convert("RGBA")

        # dark gradient overlay, stronger toward the bottom, for text legibility
        overlay = Image.new("RGBA", (IMG_SIZE, IMG_SIZE), (0, 0, 0, 0))
        odraw = ImageDraw.Draw(overlay)
        for y in range(IMG_SIZE):
            t = y / IMG_SIZE
            alpha = int(60 + 150 * t)
            odraw.line([(0, y), (IMG_SIZE, y)], fill=(10, 12, 20, alpha))
        img = Image.alpha_composite(img, overlay).convert("RGB")
        draw = ImageDraw.Draw(img, "RGBA")

        # semi-transparent badge/kicker chip so they read on any photo
        draw.ellipse([60, 60, 140, 140], fill=(*TEAL_DARK, 235))
        font = ImageFont.truetype(BOLD_FONT, 38)
        bbox = draw.textbbox((0, 0), "1", font=font)
        w2, h2 = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text((100 - w2 / 2, 100 - h2 / 2 - bbox[1]), "1", font=font, fill=(255, 255, 255))

        kicker_font = ImageFont.truetype(SEMIBOLD_FONT, 32)
        draw.text((162, 82), "AI NEWS BRIEF", font=kicker_font, fill=GOLD)

        title_font = ImageFont.truetype(BOLD_FONT, 74)
        wrapped = textwrap.fill(hook_title, width=16)
        draw.multiline_text((60, IMG_SIZE - 420), wrapped, font=title_font,
                             fill=(255, 255, 255), spacing=14)

        draw_footer_on_photo(draw, show_swipe=True)
        return img

    # fallback: same cream template used elsewhere, if no photo available
    img = brand_canvas()
    draw = ImageDraw.Draw(img, "RGBA")
    draw_number_badge(draw, 1)
    draw_kicker(draw, "AI News Brief")
    title_font = ImageFont.truetype(BOLD_FONT, 78)
    wrapped = textwrap.fill(hook_title, width=15)
    draw.multiline_text((60, 340), wrapped, font=title_font, fill=TEXT_DARK, spacing=14)
    draw_footer(draw, show_swipe=True)
    return img


def slide_bullets(number, kicker, heading, items):
    img = brand_canvas()
    draw = ImageDraw.Draw(img, "RGBA")

    draw_number_badge(draw, number)
    draw_kicker(draw, kicker)

    heading_font = ImageFont.truetype(BOLD_FONT, 56)
    draw.text((60, 200), heading, font=heading_font, fill=TEXT_DARK)
    draw.line([(60, 280), (240, 280)], fill=GOLD, width=5)

    draw_bullets(draw, items, start_y=340)

    draw_footer(draw, show_swipe=(number < 4))
    return img


def slide_follow():
    img = brand_canvas()
    draw = ImageDraw.Draw(img, "RGBA")

    draw_number_badge(draw, 4)
    draw_kicker(draw, "Stay Updated")

    cx, cy, r = IMG_SIZE / 2, 470, 110
    draw.ellipse([cx - r - 14, cy - r - 14, cx + r + 14, cy + r + 14], outline=GOLD, width=6)
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=TEAL_DARK)
    emblem_font = ImageFont.truetype(BOLD_FONT, 76)
    bbox0 = draw.textbbox((0, 0), "AI", font=emblem_font)
    w0, h0 = bbox0[2] - bbox0[0], bbox0[3] - bbox0[1]
    draw.text((cx - w0 / 2, cy - h0 / 2 - bbox0[1]), "AI", font=emblem_font, fill=(255, 255, 255))

    heading_font = ImageFont.truetype(BOLD_FONT, 58)
    text = "FOLLOW FOR DAILY\nAI NEWS"
    bbox2 = draw.multiline_textbbox((0, 0), text, font=heading_font, align="center")
    w2 = bbox2[2] - bbox2[0]
    draw.multiline_text(((IMG_SIZE - w2) / 2, 640), text, font=heading_font,
                         fill=TEXT_DARK, align="center", spacing=12)

    handle_font = ImageFont.truetype(SEMIBOLD_FONT, 40)
    pill_text = f"Follow @{BRAND_HANDLE}"
    bbox3 = draw.textbbox((0, 0), pill_text, font=handle_font)
    pw = bbox3[2] - bbox3[0]
    pad_x, pad_y = 44, 22
    pill_w, pill_h = pw + pad_x * 2, 40 + pad_y * 2
    px0 = (IMG_SIZE - pill_w) / 2
    py0 = 830
    draw.rounded_rectangle([px0, py0, px0 + pill_w, py0 + pill_h], radius=pill_h / 2, fill=TEAL_DARK)
    draw.text((px0 + pad_x, py0 + pad_y - 4), pill_text, font=handle_font, fill=(255, 255, 255))

    draw_footer(draw, show_swipe=False)
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
        slide_title(content["hook_title"], content["image_query"]),
        slide_bullets(2, "What's New", "The Story", content["story_points"]),
        slide_bullets(3, "Why It Matters", "Why It Matters", content["impact_points"]),
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
            "caption": assemble_caption(content),
            "image_urls": image_urls,
        }, f, indent=2)

    print("Wrote pending_post.json with", len(image_urls), "slides.")


if __name__ == "__main__":
    main()
