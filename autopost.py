#!/usr/bin/env python3
"""
Ottomate Instagram autoposter.

Reads every batch in content/<batch>/ (schedule.csv + batch.json + media files)
and publishes anything that is due to Instagram using the official
Instagram API (Instagram Login). Keeps a log in state/posted.json so nothing
is ever posted twice.

Commands:
  python autopost.py run          post anything that is due (used by the timer)
  python autopost.py dry-run      show what WOULD be posted now, post nothing
  python autopost.py upcoming     list the next 15 scheduled items
  python autopost.py check        check every batch for mistakes
  python autopost.py whoami       test the token: shows which account it posts to
  python autopost.py refresh-token --out FILE   get a fresh 60-day token
"""
import argparse
import csv
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, date
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).resolve().parent
CONTENT = ROOT / "content"
LOG_PATH = ROOT / "state" / "posted.json"
CFG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))

TZ = ZoneInfo(CFG.get("timezone", "Europe/London"))
API = "https://graph.instagram.com/" + CFG.get("api_version", "v23.0")
MAX_PER_RUN = int(CFG.get("max_posts_per_run", 2))
LATE_LIMIT = timedelta(hours=float(CFG.get("late_limit_hours", 6)))
MAX_ATTEMPTS = int(CFG.get("max_attempts", 3))

TYPES = {"post", "story", "reel", "carousel"}
IMAGE_EXT = {".jpg", ".jpeg"}
VIDEO_EXT = {".mp4", ".mov"}


# ---------------------------------------------------------------- helpers
def gh_annotation(kind, msg):
    """Show a coloured message in the GitHub Actions summary."""
    if os.environ.get("GITHUB_ACTIONS"):
        print(f"::{kind}::{msg}")
    else:
        print(f"[{kind.upper()}] {msg}")


def media_base_url():
    if CFG.get("media_base_url"):
        return CFG["media_base_url"].rstrip("/") + "/"
    repo = os.environ.get("GITHUB_REPOSITORY")  # e.g. sose/ottomate-instagram
    if not repo:
        return None
    owner, name = repo.split("/", 1)
    return f"https://{owner.lower()}.github.io/{name}/content/"


def load_log():
    if LOG_PATH.exists():
        return json.loads(LOG_PATH.read_text(encoding="utf-8") or "{}")
    return {}


def save_log(log):
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_PATH.write_text(json.dumps(log, indent=2, sort_keys=True), encoding="utf-8")


# ---------------------------------------------------------------- schedule
@dataclass
class Item:
    batch: str
    id: str
    when: datetime
    type: str
    media: list
    caption: str

    @property
    def key(self):
        return f"{self.batch}/{self.id}"

    def label(self):
        return f"{self.when:%a %d %b %H:%M}  {self.type:<8} {self.key}"


def load_items(errors=None):
    errors = [] if errors is None else errors
    items, seen = [], set()
    if not CONTENT.exists():
        return items
    for bdir in sorted(p for p in CONTENT.iterdir() if p.is_dir()):
        sched, meta = bdir / "schedule.csv", bdir / "batch.json"
        if not sched.exists():
            continue
        if not meta.exists():
            errors.append(f"{bdir.name}: batch.json is missing")
            continue
        try:
            start = date.fromisoformat(json.loads(meta.read_text(encoding="utf-8"))["start_date"])
        except Exception:
            errors.append(f"{bdir.name}: batch.json needs \"start_date\": \"YYYY-MM-DD\"")
            continue
        with sched.open(newline="", encoding="utf-8-sig") as f:
            for n, row in enumerate(csv.DictReader(f), start=2):
                where = f"{bdir.name}/schedule.csv line {n}"
                try:
                    rid = (row.get("id") or "").strip()
                    typ = (row.get("type") or "").strip().lower()
                    day = int((row.get("day") or "").strip())
                    hh, mm = (row.get("time") or "").strip().split(":")
                    when = datetime.combine(start + timedelta(days=day),
                                            datetime.min.time(), TZ).replace(hour=int(hh), minute=int(mm))
                except Exception:
                    errors.append(f"{where}: check the id / day / time columns (time must be like 08:30)")
                    continue
                media = [m.strip() for m in (row.get("media") or "").split("|") if m.strip()]
                caption = (row.get("caption") or "").replace("\\n", "\n").strip()
                item = Item(bdir.name, rid, when, typ, media, caption)
                errors.extend(validate(item, where, bdir, seen))
                seen.add(item.key)
                items.append(item)
    items.sort(key=lambda i: i.when)
    return items


def validate(item, where, bdir, seen):
    errs = []
    if not item.id:
        errs.append(f"{where}: id is empty")
    if item.key in seen:
        errs.append(f"{where}: id '{item.id}' is used twice in this batch")
    if item.type not in TYPES:
        errs.append(f"{where}: type must be one of {sorted(TYPES)}")
        return errs
    if not item.media:
        errs.append(f"{where}: no media file listed")
    for m in item.media:
        if not (bdir / m).exists():
            errs.append(f"{where}: file '{m}' is not in the {bdir.name} folder")
    exts = [Path(m).suffix.lower() for m in item.media]
    if item.type == "post" and (len(exts) != 1 or exts[0] not in IMAGE_EXT):
        errs.append(f"{where}: a post needs exactly one .jpg image")
    if item.type == "story" and (len(exts) != 1 or exts[0] not in IMAGE_EXT | VIDEO_EXT):
        errs.append(f"{where}: a story needs exactly one .jpg or .mp4")
    if item.type == "reel" and (len(exts) != 1 or exts[0] not in VIDEO_EXT):
        errs.append(f"{where}: a reel needs exactly one .mp4 video")
    if item.type == "carousel" and not (2 <= len(exts) <= 10):
        errs.append(f"{where}: a carousel needs 2 to 10 files separated by |")
    if len(item.caption) > 2200:
        errs.append(f"{where}: caption is over Instagram's 2,200 character limit")
    if len(re.findall(r"#\w+", item.caption)) > 30:
        errs.append(f"{where}: caption has more than 30 hashtags")
    return errs


# ---------------------------------------------------------------- Instagram API
class ApiError(Exception):
    pass


class Instagram:
    def __init__(self, token):
        if not token:
            raise ApiError("No IG_ACCESS_TOKEN found. Add it in GitHub > Settings > Secrets and variables > Actions.")
        self.token = token
        self._uid = None

    def _call(self, method, path, **params):
        params["access_token"] = self.token
        for attempt in range(3):
            try:
                if method == "GET":
                    r = requests.get(f"{API}/{path}", params=params, timeout=60)
                else:
                    r = requests.post(f"{API}/{path}", data=params, timeout=120)
                data = r.json()
            except (requests.RequestException, ValueError) as e:
                if attempt == 2:
                    raise ApiError(f"network problem talking to Instagram: {e}")
                time.sleep(5 * (attempt + 1))
                continue
            if "error" in data:
                err = data["error"]
                transient = err.get("is_transient") or err.get("code") in (1, 2, 4, 17, 32, 341)
                if transient and attempt < 2:
                    time.sleep(10 * (attempt + 1))
                    continue
                raise ApiError(f"Instagram said: {err.get('message')} (code {err.get('code')})")
            return data
        raise ApiError("Instagram did not respond")

    def me(self):
        return self._call("GET", "me", fields="user_id,username,account_type")

    @property
    def uid(self):
        if not self._uid:
            d = self.me()
            self._uid = d.get("user_id") or d["id"]
        return self._uid

    def container(self, **params):
        return self._call("POST", f"{self.uid}/media", **params)["id"]

    def wait_ready(self, cid, video):
        deadline = time.time() + (900 if video else 120)
        while time.time() < deadline:
            status = self._call("GET", cid, fields="status_code,status").get("status_code")
            if status == "FINISHED":
                return
            if status in ("ERROR", "EXPIRED"):
                detail = self._call("GET", cid, fields="status").get("status", "")
                raise ApiError(f"Instagram could not process the media ({status}). {detail}")
            time.sleep(10 if video else 3)
        raise ApiError("Instagram took too long to process the media")

    def publish(self, cid):
        return self._call("POST", f"{self.uid}/media_publish", creation_id=cid)["id"]

    def refresh(self):
        r = requests.get("https://graph.instagram.com/refresh_access_token",
                         params={"grant_type": "ig_refresh_token", "access_token": self.token}, timeout=60)
        data = r.json()
        if "error" in data:
            raise ApiError(f"Token refresh failed: {data['error'].get('message')}")
        return data["access_token"], int(data.get("expires_in", 0))


def url_for(base, item, fname):
    return f"{base}{item.batch}/{fname}"


def url_is_live(url):
    try:
        r = requests.head(url, timeout=20, allow_redirects=True)
        return r.status_code == 200
    except requests.RequestException:
        return False


def is_video(fname):
    return Path(fname).suffix.lower() in VIDEO_EXT


def post_item(ig, base, item):
    urls = [url_for(base, item, m) for m in item.media]
    if item.type == "post":
        cid = ig.container(image_url=urls[0], caption=item.caption)
        ig.wait_ready(cid, False)
    elif item.type == "story":
        if is_video(item.media[0]):
            cid = ig.container(media_type="STORIES", video_url=urls[0])
            ig.wait_ready(cid, True)
        else:
            cid = ig.container(media_type="STORIES", image_url=urls[0])
            ig.wait_ready(cid, False)
    elif item.type == "reel":
        cid = ig.container(media_type="REELS", video_url=urls[0], caption=item.caption,
                           share_to_feed="true", thumb_offset=str(CFG.get("reel_cover_ms", 2500)))
        ig.wait_ready(cid, True)
    else:  # carousel
        kids = []
        for m, u in zip(item.media, urls):
            if is_video(m):
                k = ig.container(media_type="VIDEO", video_url=u, is_carousel_item="true")
            else:
                k = ig.container(image_url=u, is_carousel_item="true")
            ig.wait_ready(k, is_video(m))
            kids.append(k)
        cid = ig.container(media_type="CAROUSEL", children=",".join(kids), caption=item.caption)
        ig.wait_ready(cid, False)
    return ig.publish(cid)


# ---------------------------------------------------------------- commands
def cmd_check():
    errors = []
    items = load_items(errors)
    for e in errors:
        gh_annotation("error", e)
    print(f"Checked {len(items)} scheduled items: {len(errors)} problem(s).")
    return 1 if errors else 0


def cmd_upcoming():
    log, now = load_log(), datetime.now(TZ)
    items = [i for i in load_items() if log.get(i.key, {}).get("status") != "posted" and i.when >= now - LATE_LIMIT]
    print(f"Now: {now:%a %d %b %H:%M} ({TZ.key})")
    for i in items[:15]:
        print("  " + i.label())
    if not items:
        print("  Nothing scheduled. Time for a new batch!")
    return 0


def due_items(log, now):
    due, late = [], []
    for i in load_items():
        entry = log.get(i.key, {})
        if entry.get("status") == "posted":
            continue
        if entry.get("status") == "failed" and entry.get("attempts", 0) >= MAX_ATTEMPTS:
            continue
        if i.when > now:
            continue
        (late if now - i.when > LATE_LIMIT else due).append(i)
    return due, late


def cmd_run(dry):
    errors = []
    load_items(errors)
    if errors:
        for e in errors:
            gh_annotation("error", e)
        gh_annotation("error", "Fix the problems above. Nothing was posted from the broken rows' batches.")
    log, now = load_log(), datetime.now(TZ)
    due, late = due_items(log, now)
    for i in late:
        if now - i.when > timedelta(days=2):
            continue  # only remind about recently missed items
        gh_annotation("warning", f"Skipped (more than {LATE_LIMIT} late): {i.label()}. "
                                  f"Change start_date in content/{i.batch}/batch.json to reschedule.")
    if not due:
        print(f"{now:%H:%M} Nothing due right now.")
        cmd_upcoming()
        return 0
    if dry:
        print("DRY RUN: these would be posted now:")
        for i in due[:MAX_PER_RUN]:
            print("  " + i.label())
        return 0

    base = media_base_url()
    if not base:
        gh_annotation("error", "Can't work out where the media is hosted. Set media_base_url in config.json.")
        return 1
    ig = Instagram(os.environ.get("IG_ACCESS_TOKEN", "").strip())
    failed = False
    for n, i in enumerate(due[:MAX_PER_RUN]):
        if not all(url_is_live(url_for(base, i, m)) for m in i.media):
            gh_annotation("warning", f"Media for {i.key} isn't live on GitHub Pages yet; will try again next run.")
            continue
        entry = log.get(i.key, {})
        try:
            media_id = post_item(ig, base, i)
            log[i.key] = {"status": "posted", "media_id": media_id, "at": datetime.now(TZ).isoformat(timespec="minutes")}
            print(f"POSTED  {i.label()}  (id {media_id})")
        except ApiError as e:
            attempts = entry.get("attempts", 0) + 1
            log[i.key] = {"status": "failed", "attempts": attempts, "error": str(e)[:300],
                          "at": datetime.now(TZ).isoformat(timespec="minutes")}
            final = attempts >= MAX_ATTEMPTS
            gh_annotation("error", f"{'GAVE UP on' if final else 'Failed (will retry)'} {i.label()}: {e}")
            failed = True
        save_log(log)
        if n < len(due[:MAX_PER_RUN]) - 1:
            time.sleep(30)
    return 1 if failed else 0


def cmd_whoami():
    ig = Instagram(os.environ.get("IG_ACCESS_TOKEN", "").strip())
    d = ig.me()
    print(f"Connected to @{d.get('username')} (account type: {d.get('account_type')}, id {d.get('user_id') or d.get('id')})")
    if d.get("account_type") not in ("BUSINESS", None):
        gh_annotation("warning", "Stories can only be posted from a Business account. Switch it in Instagram settings.")
    base = media_base_url()
    print(f"Media will be loaded from: {base}")
    return 0


def cmd_refresh(out):
    ig = Instagram(os.environ.get("IG_ACCESS_TOKEN", "").strip())
    token, expires = ig.refresh()
    Path(out).write_text(token, encoding="utf-8")
    print(f"Got a fresh token, valid for about {expires // 86400} days.")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Ottomate Instagram autoposter")
    ap.add_argument("command", choices=["run", "dry-run", "upcoming", "check", "whoami", "refresh-token"])
    ap.add_argument("--out", default="new_token.txt")
    a = ap.parse_args()
    try:
        if a.command == "run":
            return cmd_run(False)
        if a.command == "dry-run":
            return cmd_run(True)
        if a.command == "upcoming":
            return cmd_upcoming()
        if a.command == "check":
            return cmd_check()
        if a.command == "whoami":
            return cmd_whoami()
        if a.command == "refresh-token":
            return cmd_refresh(a.out)
    except ApiError as e:
        gh_annotation("error", str(e))
        return 1


if __name__ == "__main__":
    sys.exit(main())
