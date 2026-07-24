"""
publish.py
Step 2 of the daily pipeline. Runs AFTER generate.py's output image has been
committed & pushed to GitHub (so it's reachable at a public raw URL).

Reads pending_post.json, posts to Instagram via the Graph API (two-step:
create media container, then publish it), and appends the story link to
posted.json so it's never reposted.
"""

import json
import os
import time

import requests

IG_USER_ID = os.environ["IG_USER_ID"]
IG_ACCESS_TOKEN = os.environ["IG_ACCESS_TOKEN"]
POSTED_FILE = "posted.json"
PENDING_FILE = "pending_post.json"


def load_posted():
    if os.path.exists(POSTED_FILE):
        with open(POSTED_FILE) as f:
            return json.load(f)
    return []


def save_posted(posted):
    with open(POSTED_FILE, "w") as f:
        json.dump(posted, f, indent=2)


def post_to_instagram(image_url, caption):
    base = f"https://graph.facebook.com/v20.0/{IG_USER_ID}"

    # 1. create media container
    r1 = requests.post(f"{base}/media", data={
        "image_url": image_url,
        "caption": caption,
        "access_token": IG_ACCESS_TOKEN,
    }, timeout=60)
    if not r1.ok:
        print("Instagram API error creating media container:")
        print(r1.text)
    r1.raise_for_status()
    creation_id = r1.json()["id"]

    # give Instagram a moment to fetch/process the image
    time.sleep(10)

    # 2. publish it
    r2 = requests.post(f"{base}/media_publish", data={
        "creation_id": creation_id,
        "access_token": IG_ACCESS_TOKEN,
    }, timeout=60)
    if not r2.ok:
        print("Instagram API error publishing media:")
        print(r2.text)
    r2.raise_for_status()
    return r2.json()


def main():
    if not os.path.exists(PENDING_FILE):
        print("No pending_post.json found. Nothing to publish.")
        return

    with open(PENDING_FILE) as f:
        pending = json.load(f)

    result = post_to_instagram(pending["image_url"], pending["caption"])
    print("Posted to Instagram:", result)

    posted = load_posted()
    posted.append(pending["link"])
    posted = posted[-500:]  # cap the log so it doesn't grow forever
    save_posted(posted)

    os.remove(PENDING_FILE)


if __name__ == "__main__":
    main()
