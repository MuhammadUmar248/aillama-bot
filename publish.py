"""
publish.py
Step 2 of the daily pipeline. Runs AFTER generate.py's slide images have
been committed & pushed to GitHub (so they're reachable at public raw URLs).

Reads pending_post.json (a list of image_urls + one caption), posts them to
Instagram as a CAROUSEL (multi-slide post):
  1. Create one "carousel item" container per slide image
  2. Create a parent container of type CAROUSEL referencing all the items
  3. Publish the parent container
Then appends the story link to posted.json so it's never reposted.
"""

import json
import os
import time

import requests

IG_USER_ID = os.environ["IG_USER_ID"]
IG_ACCESS_TOKEN = os.environ["IG_ACCESS_TOKEN"]
POSTED_FILE = "posted.json"
PENDING_FILE = "pending_post.json"

# Using the Instagram API with Instagram Login (graph.instagram.com).
BASE_URL = f"https://graph.instagram.com/v20.0/{IG_USER_ID}"


def load_posted():
    if os.path.exists(POSTED_FILE):
        with open(POSTED_FILE) as f:
            return json.load(f)
    return []


def save_posted(posted):
    with open(POSTED_FILE, "w") as f:
        json.dump(posted, f, indent=2)


def _post(endpoint, data, label):
    r = requests.post(f"{BASE_URL}/{endpoint}", data=data, timeout=60)
    print(f"{label} response:", r.status_code, flush=True)
    print(r.text, flush=True)
    r.raise_for_status()
    return r.json()


def create_carousel_item(image_url):
    result = _post("media", {
        "image_url": image_url,
        "is_carousel_item": "true",
        "access_token": IG_ACCESS_TOKEN,
    }, "Create carousel item")
    return result["id"]


def create_carousel_container(children_ids, caption):
    result = _post("media", {
        "media_type": "CAROUSEL",
        "children": ",".join(children_ids),
        "caption": caption,
        "access_token": IG_ACCESS_TOKEN,
    }, "Create carousel parent container")
    return result["id"]


def publish_container(creation_id):
    return _post("media_publish", {
        "creation_id": creation_id,
        "access_token": IG_ACCESS_TOKEN,
    }, "Publish")


def post_carousel_to_instagram(image_urls, caption):
    children_ids = []
    for url in image_urls:
        children_ids.append(create_carousel_item(url))
        time.sleep(3)  # small pause between item creations

    # give Instagram a moment to finish fetching/processing all slides
    time.sleep(10)

    parent_id = create_carousel_container(children_ids, caption)

    time.sleep(5)

    return publish_container(parent_id)


def main():
    if not os.path.exists(PENDING_FILE):
        print("No pending_post.json found. Nothing to publish.")
        return

    with open(PENDING_FILE) as f:
        pending = json.load(f)

    result = post_carousel_to_instagram(pending["image_urls"], pending["caption"])
    print("Posted carousel to Instagram:", result)

    posted = load_posted()
    posted.append(pending["link"])
    posted = posted[-500:]  # cap the log so it doesn't grow forever
    save_posted(posted)

    os.remove(PENDING_FILE)


if __name__ == "__main__":
    main()
