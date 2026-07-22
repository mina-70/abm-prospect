#!/usr/bin/env python3
"""
A Brilliant Mind — University Prospect Crawler
================================================
Crawls each university's team/contact page from prospects.csv,
extracts names, roles, emails and updates the CSV automatically.

Usage:
  python crawler.py                   # crawl all rows missing a contact name
  python crawler.py --all             # re-crawl every row
  python crawler.py --row 5           # re-crawl a specific row number
  python crawler.py --country DE      # crawl all German rows
  python crawler.py --push            # also push updated CSV to GitHub via API

Requirements:
  pip install requests beautifulsoup4 lxml

GitHub push (optional):
  Set env var GITHUB_TOKEN=your_personal_access_token
  Set GITHUB_USER and GITHUB_REPO below.
"""

import csv
import re
import sys
import os
import time
import json
import base64
import argparse
import logging
from datetime import datetime
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

# ─── CONFIG ──────────────────────────────────────────────────────────────────

GITHUB_USER   = "YOUR-GITHUB-USERNAME"   # ← change this
GITHUB_REPO   = "YOUR-REPO-NAME"          # ← change this
GITHUB_BRANCH = "main"
CSV_FILE      = "prospects.csv"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9,de;q=0.8",
}
REQUEST_TIMEOUT = 12
DELAY_BETWEEN_REQUESTS = 2.0   # seconds — be polite

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("crawler")

# ─── EMAIL / NAME PATTERNS ───────────────────────────────────────────────────

EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"
)

# Titles that signal an academic/admin person
TITLE_RE = re.compile(
    r"\b(Dr\.|Prof\.|Mag\.|MSc|MA|MBA|PhD|Dipl\.|Ing\.)\b", re.IGNORECASE
)

# Roles we care about — ordered by priority
ROLE_KEYWORDS = [
    "managing director", "geschäftsführ",
    "head of", "leitung", "director",
    "coordinator", "koordinator",
    "researcher development", "doctoral", "doktorat",
    "graduate school", "graduiertenakademie",
    "postdoc", "nachwuchs", "early career",
    "transferable skills", "weiterbildung",
    "qualification", "qualifizierung",
    "academic staff development", "personalentwicklung",
]

# ─── FETCH ───────────────────────────────────────────────────────────────────

def fetch_page(url: str) -> BeautifulSoup | None:
    url = url.strip()
    if not url or url == "#":
        return None
    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT,
                            allow_redirects=True)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "lxml")
    except Exception as e:
        log.warning(f"  fetch failed: {e}")
        return None

# ─── EXTRACTION ──────────────────────────────────────────────────────────────

def extract_emails(soup: BeautifulSoup, url: str) -> list[str]:
    """Pull all email addresses from the page (mailto links + raw text)."""
    emails = set()

    # mailto: links
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith("mailto:"):
            addr = href[7:].split("?")[0].strip()
            if addr:
                emails.add(addr.lower())

    # raw text scan
    for m in EMAIL_RE.finditer(soup.get_text(" ")):
        addr = m.group(0).lower()
        # filter out obvious non-person addresses
        if not any(x in addr for x in ["noreply", "example", "webmaster",
                                         "info@uni", "support@"]):
            emails.add(addr)

    return sorted(emails)


def score_role(text: str) -> int:
    """Higher score = more relevant role for our buyer persona."""
    t = text.lower()
    score = 0
    for i, kw in enumerate(ROLE_KEYWORDS):
        if kw in t:
            score += len(ROLE_KEYWORDS) - i   # earlier = higher priority
    return score


def extract_contacts(soup: BeautifulSoup, url: str) -> list[dict]:
    """
    Try multiple strategies to find named contacts with roles on a team page.
    Returns a list of dicts sorted by relevance score.
    """
    candidates = []

    # ── Strategy 1: look for vCard / staff-card / person blocks ──────────────
    person_selectors = [
        "[class*='person']", "[class*='staff']", "[class*='team']",
        "[class*='contact']", "[class*='mitarbeit']", "[class*='ansprechpartner']",
        "[class*='member']", "[class*='people']", "[class*='employee']",
        "[class*='profile']", "[class*='card']",
    ]
    blocks = []
    for sel in person_selectors:
        found = soup.select(sel)
        if found:
            blocks.extend(found)
            break   # use the first selector that yields results

    for block in blocks[:40]:
        text = block.get_text(" ", strip=True)
        emails_in_block = EMAIL_RE.findall(text)
        mailto = None
        for a in block.find_all("a", href=True):
            if a["href"].startswith("mailto:"):
                mailto = a["href"][7:].split("?")[0].strip()
                break

        # look for a heading-like element for the name
        name_el = (block.find(["h2","h3","h4","strong","b"]) or
                   block.find(class_=re.compile(r"name|title|heading", re.I)))
        name = name_el.get_text(strip=True) if name_el else ""

        # try to find the role in remaining text
        role_text = text.replace(name, "").strip()
        role_lines = [l.strip() for l in role_text.splitlines() if l.strip()]
        role = role_lines[0] if role_lines else ""

        if not name and not emails_in_block and not mailto:
            continue

        candidates.append({
            "name": name,
            "role": role[:120],
            "email": mailto or (emails_in_block[0] if emails_in_block else ""),
            "score": score_role(name + " " + role),
        })

    # ── Strategy 2: scan <table> rows (many German university pages) ──────────
    if not candidates:
        for table in soup.find_all("table")[:5]:
            for row in table.find_all("tr"):
                cells = row.find_all(["td", "th"])
                if len(cells) < 2:
                    continue
                text = " ".join(c.get_text(strip=True) for c in cells)
                emails_in_row = EMAIL_RE.findall(text)
                mailto = None
                for a in row.find_all("a", href=True):
                    if a["href"].startswith("mailto:"):
                        mailto = a["href"][7:].split("?")[0].strip()
                        break
                if not emails_in_row and not mailto:
                    continue
                name = cells[0].get_text(strip=True)
                role = cells[1].get_text(strip=True) if len(cells) > 1 else ""
                candidates.append({
                    "name": name,
                    "role": role[:120],
                    "email": mailto or emails_in_row[0],
                    "score": score_role(name + " " + role),
                })

    # ── Strategy 3: fallback — any <a mailto:> near a heading ─────────────────
    if not candidates:
        for a in soup.find_all("a", href=True):
            if not a["href"].startswith("mailto:"):
                continue
            email = a["href"][7:].split("?")[0].strip()
            # look for a nearby heading
            name, role = "", ""
            parent = a.parent
            for _ in range(4):
                if parent is None:
                    break
                heading = parent.find(["h2","h3","h4","strong"])
                if heading:
                    name = heading.get_text(strip=True)
                    break
                parent = parent.parent
            candidates.append({
                "name": name,
                "role": role,
                "email": email,
                "score": score_role(name),
            })

    # sort by score desc, then by presence of name
    candidates.sort(key=lambda x: (-x["score"], -bool(x["name"])))
    return candidates


def best_contact(candidates: list[dict]) -> dict:
    """Return the highest-scored candidate, or empty dict."""
    if not candidates:
        return {}
    c = candidates[0]
    return {
        "contact_name": clean(c.get("name", "")),
        "role": clean(c.get("role", "")),
        "email": clean(c.get("email", "")),
    }


def clean(s: str) -> str:
    """Strip excess whitespace and control chars."""
    return re.sub(r"\s+", " ", s).strip()

# ─── SECONDARY SEARCH: try common sub-pages if main page yields nothing ──────

TEAM_PAGE_PATHS = [
    "/team", "/team/", "/ueber-uns/team", "/about-us/team", "/about/team",
    "/contact", "/kontakt", "/ansprechpartner", "/people", "/staff",
    "/our-team", "/meet-the-team", "/who-we-are",
    "/die-einrichtung/team", "/ueber-uns/personen",
]

def find_team_page(base_url: str, soup: BeautifulSoup) -> str | None:
    """Look for a team/contact sub-page linked from the given page."""
    keywords = ["team", "contact", "kontakt", "ansprechpartner", "staff",
                "people", "who we are", "mitarbeiter", "personen"]
    for a in soup.find_all("a", href=True):
        href = a["href"].lower()
        text = a.get_text(strip=True).lower()
        if any(kw in href or kw in text for kw in keywords):
            full = urljoin(base_url, a["href"])
            # stay on same domain
            if urlparse(full).netloc == urlparse(base_url).netloc:
                return full
    return None

# ─── CRAWL ONE ROW ───────────────────────────────────────────────────────────

def crawl_row(row: dict) -> dict:
    url = row.get("url", "").strip()
    uni = row.get("university", "")
    log.info(f"▶ {uni} — {url}")

    if not url or url == "#":
        log.info("  no URL, skipping")
        return row

    soup = fetch_page(url)
    if soup is None:
        return row

    candidates = extract_contacts(soup, url)

    # if nothing found on main page, try to find a team sub-page
    if not candidates or not candidates[0].get("name"):
        team_url = find_team_page(url, soup)
        if team_url and team_url != url:
            log.info(f"  → trying team page: {team_url}")
            time.sleep(DELAY_BETWEEN_REQUESTS)
            soup2 = fetch_page(team_url)
            if soup2:
                candidates2 = extract_contacts(soup2, team_url)
                if candidates2:
                    candidates = candidates2
                    row["url"] = team_url   # update URL to point at team page

    contact = best_contact(candidates)

    updated = False
    for field in ["contact_name", "role", "email"]:
        if contact.get(field) and not row.get(field):
            row[field] = contact[field]
            updated = True
            log.info(f"  ✓ {field}: {contact[field]}")

    if not updated:
        log.info("  – no new data found")
    else:
        row["status"] = f"Crawler updated {datetime.now().strftime('%d %b %Y')}"

    return row

# ─── CSV I/O ─────────────────────────────────────────────────────────────────

FIELDNAMES = [
    "university","country","country_code","unit_type","unit_name",
    "contact_name","role","email","url","priority","status","notes"
]

def read_csv(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))

def write_csv(path: str, rows: list[dict]):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    log.info(f"✓ Saved {len(rows)} rows → {path}")

# ─── GITHUB PUSH ─────────────────────────────────────────────────────────────

def push_to_github(csv_path: str):
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        log.error("GITHUB_TOKEN env var not set — skipping push")
        return

    api = f"https://api.github.com/repos/{mina-70}/{abm-prospect}/contents/{CSV_FILE}"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }

    # get current SHA (needed for update)
    r = requests.get(api, headers=headers, params={"ref": GITHUB_BRANCH})
    sha = r.json().get("sha") if r.ok else None

    with open(csv_path, "rb") as f:
        content = base64.b64encode(f.read()).decode()

    payload = {
        "message": f"crawler: update prospects {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "content": content,
        "branch": GITHUB_BRANCH,
    }
    if sha:
        payload["sha"] = sha

    r = requests.put(api, headers=headers, json=payload)
    if r.ok:
        log.info(f"✓ Pushed {CSV_FILE} to GitHub ({GITHUB_USER}/{GITHUB_REPO})")
    else:
        log.error(f"Push failed: {r.status_code} {r.text[:200]}")

# ─── MAIN ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="ABM University Prospect Crawler")
    parser.add_argument("--all", action="store_true",
                        help="Re-crawl all rows, not just empty ones")
    parser.add_argument("--row", type=int, default=None,
                        help="Crawl a specific row number (0-indexed, ignoring header)")
    parser.add_argument("--country", type=str, default=None,
                        help="Only crawl rows with this country code (e.g. DE)")
    parser.add_argument("--priority", type=str, default=None,
                        help="Only crawl rows with this priority (A, B or C)")
    parser.add_argument("--push", action="store_true",
                        help="Push updated CSV to GitHub after crawling")
    parser.add_argument("--csv", type=str, default=CSV_FILE,
                        help=f"Path to CSV file (default: {CSV_FILE})")
    args = parser.parse_args()

    csv_path = args.csv
    if not os.path.exists(csv_path):
        log.error(f"CSV not found: {csv_path}")
        sys.exit(1)

    rows = read_csv(csv_path)
    log.info(f"Loaded {len(rows)} rows from {csv_path}")

    updated_count = 0

    for i, row in enumerate(rows):
        # ── filters ──
        if args.row is not None and i != args.row:
            continue
        if args.country and row.get("country_code","").upper() != args.country.upper():
            continue
        if args.priority and row.get("priority","").upper() != args.priority.upper():
            continue
        # skip rows that already have a contact name unless --all
        if not args.all and row.get("contact_name","").strip():
            continue
        # skip rows with no URL
        if not row.get("url","").strip():
            continue

        rows[i] = crawl_row(row)
        updated_count += 1
        time.sleep(DELAY_BETWEEN_REQUESTS)

    write_csv(csv_path, rows)
    log.info(f"Crawled {updated_count} rows")

    if args.push:
        push_to_github(csv_path)

    # print summary
    has_name  = sum(1 for r in rows if r.get("contact_name","").strip())
    has_email = sum(1 for r in rows if r.get("email","").strip())
    log.info(f"Summary: {len(rows)} total | {has_name} with name | {has_email} with email")

if __name__ == "__main__":
    main()
