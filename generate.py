"""
generate.py
Step 1 of the daily pipeline:
  1. Pull latest AI news from RSS feeds
  2. Skip anything already posted (posted.json)
  3. Ask an LLM (Groq / Llama 3.3) to turn it into a hook title + IG caption
  4. Generate a background image (Pollinations, free, no key) and overlay the
     hook title on it with Pillow to make a branded 1080x1080 post image
  5. Write pending_post.json describing what still needs to be published

The actual Instagram publish step happens in publish.py, AFTER this image
has been committed & pushed to the repo (so it has a public raw URL).
"""

import json
import os
import textwrap
import time
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
FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

GROQ_API_KEY = os.environ["GROQ_API_KEY"]
GITHUB_REPOSITORY = os.environ.get("GITHUB_REPOSITORY", "")  # e.g. "yourname/aillama-bot"
BRANCH = os.environ.get("BRANCH", "main")

LOOKBACK_HOURS = 30  # how far back to consider "fresh" news


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
that posts quick, eye-catching AI news briefs to a general audience.

News headline: {story['title']}
Summary: {story['summary']}

Write:
1. hook_title: a short punchy hook for the image overlay (max 8 words, no hashtags, high energy)
2. caption: an Instagram caption (3-5 sentences, engaging, plain language, 1-2 emojis,
   ends with a question or call-to-action), followed by 6-10 relevant hashtags
   IN THE SAME "caption" STRING (hashtags go inside caption, not a separate field)
3. image_prompt: max 15 words describing an ABSTRACT visual for this topic
   (no text, no logos, no real people, futuristic/tech aesthetic)

Respond with ONLY a single valid JSON object and nothing else — no markdown
fences, no commentary, no alternate versions, no explanation before or after it.
Inside the JSON string values, any line break MUST be written as the two
characters backslash-n (a JSON-escaped newline) — never a literal line break.
The JSON object must have EXACTLY these three keys, no others:
{{"hook_title": "...", "caption": "...", "image_prompt": "..."}}
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

    # strip markdown fences if the model added them anyway
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()

    # Sometimes the model still adds extra text/objects after the first JSON
    # object (e.g. "a better version would be {...}"). raw_decode() parses
    # just the first valid JSON value and tells us where it ends, so we can
    # ignore anything after it instead of erroring out on "Extra data".
    # strict=False also tolerates stray raw control characters (like literal
    # newlines) inside string values.
    decoder = json.JSONDecoder(strict=False)
    try:
        obj, _end_index = decoder.raw_decode(text)
    except json.JSONDecodeError:
        print("Raw model output that failed to parse:\n", text)
        raise

    # Guard against missing keys (e.g. if the model used "hashtags" as a
    # separate field instead of folding it into "caption")
    for key in ("hook_title", "caption", "image_prompt"):
        if key not in obj:
            raise ValueError(f"Model response missing required key '{key}': {obj}")

    return obj


# ---------------------------------------------------------------------------
# STEP 3: image generation (Pollinations background + Pillow text overlay)
# ---------------------------------------------------------------------------

def generate_background(prompt):
    full_prompt = f"{prompt}, abstract digital art, futuristic gradient, no text, no words, no logos"
    url = f"https://image.pollinations.ai/prompt/{quote(full_prompt)}?width=1080&height=1080&nologo=true"
    r = requests.get(url, timeout=90)
    r.raise_for_status()
    bg_path = "bg.png"
    with open(bg_path, "wb") as f:
        f.write(r.content)
    return bg_path


def create_post_image(bg_path, hook_title, out_path):
    img = Image.open(bg_path).convert("RGBA").resize((1080, 1080))

    # dark gradient band at the bottom so text stays readable
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    band_height = 360
    for y in range(band_height):
        alpha = int(210 * (y / band_height))
        yy = img.height - band_height + y
        draw.line([(0, yy), (img.width, yy)], fill=(0, 0, 0, alpha))
    img = Image.alpha_composite(img, overlay)
    draw = ImageDraw.Draw(img)

    title_font = ImageFont.truetype(FONT_PATH, 66)
    brand_font = ImageFont.truetype(FONT_PATH, 32)

    wrapped = textwrap.fill(hook_title.upper(), width=18)
    draw.multiline_text((60, img.height - 300), wrapped, font=title_font, fill="white", spacing=12)
    draw.text((60, img.height - 60), "\U0001F999 AI LLAMA DAILY", font=brand_font, fill=(255, 209, 102, 255))

    img.convert("RGB").save(out_path, "PNG")
    return out_path


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

    bg_path = generate_background(content["image_prompt"])

    os.makedirs(IMAGE_DIR, exist_ok=True)
    filename = f"{IMAGE_DIR}/{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.png"
    create_post_image(bg_path, content["hook_title"], filename)

    image_url = f"https://raw.githubusercontent.com/{GITHUB_REPOSITORY}/{BRANCH}/{filename}"

    with open("pending_post.json", "w") as f:
        json.dump({
            "link": story["link"],
            "caption": content["caption"],
            "image_url": image_url,
        }, f, indent=2)

    print("Wrote pending_post.json. Image will be published after commit at:", image_url)


if __name__ == "__main__":
    main()
