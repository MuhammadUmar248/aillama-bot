# AI Llama Daily — automated Instagram poster

Fully free daily pipeline: RSS news → Groq LLM (Llama 3.3) writes the hook +
caption → an image is generated and branded → posted to Instagram automatically
via GitHub Actions, once a day.

```
RSS feeds  →  generate.py (LLM + image)  →  commit image to repo
           →  publish.py (Instagram Graph API)  →  posted.json log
```

## 1. Create the repo

Push this folder to a **public** GitHub repo (public is required so
`raw.githubusercontent.com` can serve the generated image to Instagram for
free, with no extra image hosting).

## 2. Get a free Groq API key

1. Go to https://console.groq.com and sign up (free).
2. Create an API key.
3. You'll add this as a GitHub secret in step 4.

## 3. Set up the Instagram Graph API (official, free)

This is the part that takes the most setup, but it's a one-time thing.

1. **Convert your Instagram account to Professional.** In the Instagram app:
   Settings → Account type and tools → Switch to Professional Account →
   choose "Creator" or "Business."
2. **Link it to a Facebook Page.** Every IG Business/Creator account needs a
   connected Facebook Page (you can create a bare-minimum Page just for this).
3. **Create a Meta developer app.**
   - Go to https://developers.facebook.com/apps → Create App → choose
     "Business" type.
   - Add the **Instagram Graph API** product to the app.
4. **Get your IG User ID and an access token.**
   - Use the Graph API Explorer (https://developers.facebook.com/tools/explorer/):
     - Select your app, generate a User Access Token with these permissions:
       `instagram_basic`, `instagram_content_publish`, `pages_show_list`,
       `pages_read_engagement`.
     - Call `GET /me/accounts` to get your Facebook Page ID.
     - Call `GET /{page-id}?fields=instagram_business_account` to get your
       **IG_USER_ID**.
   - Exchange the short-lived token for a **long-lived token** (~60 days):
     `GET /oauth/access_token?grant_type=fb_exchange_token&client_id={app-id}&client_secret={app-secret}&fb_exchange_token={short-lived-token}`
   - **Note:** long-lived tokens expire after ~60 days. You'll need to refresh
     it periodically (Meta's docs cover refreshing before expiry) and update
     the GitHub secret. There's no way around this with the free official API.

## 4. Add GitHub secrets

In your repo: **Settings → Secrets and variables → Actions → New repository secret**

| Secret name | Value |
|---|---|
| `GROQ_API_KEY` | your Groq key from step 2 |
| `IG_USER_ID` | your Instagram Business Account ID from step 3 |
| `IG_ACCESS_TOKEN` | your long-lived access token from step 3 |

## 5. Test it

Go to the **Actions** tab → "Daily AI Llama Post" → **Run workflow** to
trigger it manually and confirm it posts correctly before waiting for the
schedule.

## 6. Adjust the schedule

Edit the `cron` line in `.github/workflows/daily-post.yml`. It's currently
set to `0 9 * * *` (9:00 AM UTC daily) — GitHub Actions cron is always UTC,
so convert to your local time.

## Customizing

- **News sources:** edit `RSS_FEEDS` in `generate.py`.
- **Caption/title style:** edit the prompt inside `generate_content()` in
  `generate.py`.
- **Image look:** `create_post_image()` in `generate.py` controls the overlay
  band, font, and colors — tweak to match your brand.
- **Posting frequency:** GitHub Actions cron only supports fixed schedules;
  for multiple posts/day just add more `cron` lines under `schedule:`.

## Notes on reliability

- If no fresh, un-posted AI story is found in the lookback window, the run
  skips quietly rather than posting nothing useful.
- `posted.json` is the dedupe log — don't delete it, or old stories may repost.
- Pollinations.ai (free image generation) occasionally has slow response
  times; the script already uses a generous timeout, but if it fails
  intermittently that's normal for a free, keyless service.
