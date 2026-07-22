#!/usr/bin/env python3
"""
A Brilliant Mind — University Prospect Crawler + Auto-Discovery
================================================================
Two modes:
  1. UPDATE existing rows (fill missing contacts from team pages)
  2. DISCOVER new universities and add them automatically

Usage:
  python crawler.py                  # update missing contacts
  python crawler.py --all            # re-crawl all rows
  python crawler.py --discover       # find new universities to add
  python crawler.py --country DE     # filter by country code
  python crawler.py --push           # push updated CSV to GitHub

Requirements: pip install requests beautifulsoup4 lxml
GitHub push:  set env var GITHUB_TOKEN=ghp_...
"""

import csv, re, sys, os, time, json, base64, argparse, logging
from datetime import datetime
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

# ─── CONFIG ──────────────────────────────────────────────────────────────────
GITHUB_USER   = "YOUR-GITHUB-USERNAME"
GITHUB_REPO   = "YOUR-REPO-NAME"
GITHUB_BRANCH = "main"
CSV_FILE      = "prospects.csv"
DELAY         = 2.0   # seconds between requests — be polite

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9,de;q=0.8",
}

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("crawler")

# ─── DISCOVERY SEED LIST ─────────────────────────────────────────────────────
# Universities to auto-discover from. Each entry: (country_code, country, url)
DISCOVERY_SOURCES = [
    # UniWinD — German graduate academy network (member list = your DE market)
    ("DE", "Germany",       "https://www.uniwind.org/mitglieder"),
    # Austrian university association
    ("AT", "Austria",       "https://www.uniko.ac.at/mitglieder"),
    # Swiss universities
    ("CH", "Switzerland",   "https://www.swissuniversities.ch/en/higher-education-area/recognised-swiss-higher-education-institutions"),
    # UK Research & Innovation - UKRI list
    ("UK", "United Kingdom","https://www.ukri.org/what-we-do/supporting-healthy-research-and-innovation-culture/developing-people-and-skills/"),
    # Netherlands - VSNU member list
    ("NL", "Netherlands",   "https://www.universiteitenvannederland.nl/en_GB/universities.html"),
    # Vitae UK researcher development network
    ("UK", "United Kingdom","https://www.vitae.ac.uk/higher-education/member-organisations"),
    # EUA Council for Doctoral Education member list
    ("EU", "",              "https://eua-cde.org/members.html"),
    # UK Medical Schools Council — complete list of UK medical schools
    ("UK", "United Kingdom","https://www.medschools.ac.uk/our-schools"),
    # German university hospitals
    ("DE", "Germany",       "https://www.uniklinika.de/die-deutschen-uniklinika/uniklinika-von-a-z/"),
    # MedUni network Austria
    ("AT", "Austria",       "https://www.meduni.ac.at"),

]

# Search queries for Google Scholar-style discovery (via DuckDuckGo HTML)
DISCOVERY_QUERIES = [
    "doctoral school researcher development transferable skills site:ac.uk",
    "graduiertenakademie nachwuchsförderung kontakt site:.de",
    "graduate school postdoc researcher development site:.nl",
    "doctoral training researcher development site:.be",
    "doctoral school researcher training site:.fi",
    "doctoral school researcher training site:.no",
    "doctoral school researcher training site:.se",
    "doctoral school researcher training site:.dk",
]

# Keywords that identify the right unit on a university page
UNIT_KEYWORDS = [
    "graduiertenakademie", "graduate school", "doctoral school", "doctoral college",
    "researcher development", "research academy", "nachwuchsförderung",
    "personalentwicklung", "postdoc", "early career", "phd support",
    "transferable skills", "transversal skills", "qualification programme",
    "academic staff development", "researcher training",
]

ROLE_KEYWORDS = [
    "managing director", "geschäftsführ", "head of", "leitung", "director",
    "coordinator", "koordinator", "researcher development", "doctoral",
    "postdoc", "nachwuchs", "early career", "transferable skills",
    "qualification", "academic staff development", "personalentwicklung",
]

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

FIELDNAMES = ["university","country","country_code","unit_type","unit_name",
              "contact_name","role","email","url","priority","status","notes"]

# ─── HTTP ─────────────────────────────────────────────────────────────────────
def fetch(url, timeout=12):
    try:
        r = requests.get(url.strip(), headers=HEADERS, timeout=timeout,
                         allow_redirects=True)
        r.raise_for_status()
        return BeautifulSoup(r.text, "lxml")
    except Exception as e:
        log.warning(f"  fetch failed {url}: {e}")
        return None

def clean(s): return re.sub(r"\s+", " ", s or "").strip()

# ─── CONTACT EXTRACTION ───────────────────────────────────────────────────────
def score_role(text):
    t = text.lower()
    return sum(len(ROLE_KEYWORDS)-i for i,kw in enumerate(ROLE_KEYWORDS) if kw in t)

def extract_contacts(soup, url):
    candidates = []

    # Strategy 1: person/staff blocks
    for sel in ["[class*='person']","[class*='staff']","[class*='team']",
                "[class*='contact']","[class*='mitarbeit']","[class*='ansprechpartner']",
                "[class*='member']","[class*='people']","[class*='profile']","[class*='card']"]:
        blocks = soup.select(sel)
        if not blocks: continue
        for block in blocks[:40]:
            text = block.get_text(" ", strip=True)
            mailto = next((a["href"][7:].split("?")[0] for a in block.find_all("a",href=True)
                           if a["href"].startswith("mailto:")), None)
            emails = EMAIL_RE.findall(text)
            name_el = (block.find(["h2","h3","h4","strong","b"]) or
                       block.find(class_=re.compile(r"name|title|heading",re.I)))
            name = name_el.get_text(strip=True) if name_el else ""
            role_text = text.replace(name,"").strip()
            role = ([l.strip() for l in role_text.splitlines() if l.strip()] or [""])[0]
            if not name and not emails and not mailto: continue
            candidates.append({"name":name,"role":role[:120],
                "email":mailto or (emails[0] if emails else ""),
                "score":score_role(name+" "+role)})
        if candidates: break

    # Strategy 2: tables
    if not candidates:
        for table in soup.find_all("table")[:5]:
            for row in table.find_all("tr"):
                cells = row.find_all(["td","th"])
                if len(cells)<2: continue
                text = " ".join(c.get_text(strip=True) for c in cells)
                mailto = next((a["href"][7:].split("?")[0] for a in row.find_all("a",href=True)
                               if a["href"].startswith("mailto:")), None)
                emails = EMAIL_RE.findall(text)
                if not emails and not mailto: continue
                name,role = cells[0].get_text(strip=True), cells[1].get_text(strip=True)
                candidates.append({"name":name,"role":role[:120],
                    "email":mailto or emails[0], "score":score_role(name+" "+role)})

    # Strategy 3: any mailto near a heading
    if not candidates:
        for a in soup.find_all("a", href=True):
            if not a["href"].startswith("mailto:"): continue
            email = a["href"][7:].split("?")[0].strip()
            name,parent = "",a.parent
            for _ in range(4):
                if parent is None: break
                h = parent.find(["h2","h3","h4","strong"])
                if h: name=h.get_text(strip=True); break
                parent = parent.parent
            candidates.append({"name":name,"role":"","email":email,"score":score_role(name)})

    candidates.sort(key=lambda x:(-x["score"],-bool(x["name"])))
    return candidates

def find_team_url(base_url, soup):
    keywords=["team","contact","kontakt","ansprechpartner","staff","people","mitarbeiter","personen"]
    for a in soup.find_all("a", href=True):
        href,text = a["href"].lower(), a.get_text(strip=True).lower()
        if any(kw in href or kw in text for kw in keywords):
            full = urljoin(base_url, a["href"])
            if urlparse(full).netloc == urlparse(base_url).netloc:
                return full
    return None

# ─── CRAWL ONE ROW ────────────────────────────────────────────────────────────
def crawl_row(row):
    url = row.get("url","").strip()
    uni = row.get("university","")
    log.info(f"▶ {uni}")
    if not url or url=="#": return row

    soup = fetch(url)
    if soup is None: return row

    candidates = extract_contacts(soup, url)
    if not candidates or not candidates[0].get("name"):
        team_url = find_team_url(url, soup)
        if team_url and team_url!=url:
            log.info(f"  → team page: {team_url}")
            time.sleep(DELAY)
            soup2 = fetch(team_url)
            if soup2:
                c2 = extract_contacts(soup2, team_url)
                if c2: candidates=c2; row["url"]=team_url

    if candidates:
        c = candidates[0]
        updated=False
        for field,key in [("contact_name","name"),("role","role"),("email","email")]:
            val = clean(c.get(key,""))
            if val and not row.get(field,"").strip():
                row[field]=val; updated=True
                log.info(f"  ✓ {field}: {val}")
        if updated:
            row["status"] = f"Crawler updated {datetime.now().strftime('%d %b %Y')}"
    else:
        log.info("  – no contacts found")
    return row

# ─── AUTO-DISCOVERY ───────────────────────────────────────────────────────────
def infer_unit_type(text):
    t = text.lower()
    if any(k in t for k in ["postdoc","early career","nachwuchs","career centre"]): return "Postdoc Support"
    if any(k in t for k in ["personalentwicklung","staff development","talent development"]): return "Staff Development"
    if any(k in t for k in ["continuing","weiterbildung","lifelong"]): return "Continuing Education"
    if any(k in t for k in ["writing centre","writing center","schreibzentrum"]): return "Writing Center"
    return "Doctoral School"

def discover_from_page(url, country_code, country, existing_unis):
    """Scrape a member/directory page and extract new university entries."""
    log.info(f"🔍 Discovering from: {url}")
    soup = fetch(url)
    if not soup: return []

    new_rows = []
    seen_names = {e.lower() for e in existing_unis}

    # look for links that point to university domains
    uni_pattern = re.compile(r"https?://(?:www\.)?([a-z0-9\-]+\.(ac\.uk|edu|uni\-\w+\.\w+|tu\-\w+\.\w+|fh\-\w+\.\w+))", re.I)

    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = a.get_text(strip=True)
        if not text or len(text)<4 or len(text)>80: continue
        # must link to a university-ish domain
        if not any(s in href.lower() for s in [".ac.uk",".edu","/uni-","univ","university",
                                                 "hochschule","tu-","rwth","lmu","fau","kit"]):
            continue
        if text.lower() in seen_names: continue
        if len(new_rows)>50: break  # cap per source

        new_rows.append({
            "university": text,
            "country": country,
            "country_code": country_code,
            "unit_type": "Doctoral School",
            "unit_name": "",
            "contact_name": "",
            "role": "",
            "email": "",
            "url": href if href.startswith("http") else urljoin(url, href),
            "priority": "C",
            "status": f"Discovered {datetime.now().strftime('%d %b %Y')}",
            "notes": f"Auto-discovered from {url}",
        })
        seen_names.add(text.lower())
        log.info(f"  + found: {text}")

    return new_rows

def run_discovery(rows):
    existing_unis = [r["university"] for r in rows]
    new_rows = []
    for country_code, country, url in DISCOVERY_SOURCES:
        found = discover_from_page(url, country_code, country, existing_unis)
        new_rows.extend(found)
        existing_unis.extend([r["university"] for r in found])
        time.sleep(DELAY*2)

    log.info(f"Discovery: found {len(new_rows)} new universities")

    # Now try to crawl each new row immediately to get contacts
    crawled = []
    for i, row in enumerate(new_rows):
        log.info(f"  crawling new entry {i+1}/{len(new_rows)}: {row['university']}")
        crawled.append(crawl_row(row))
        time.sleep(DELAY)

    return crawled

# ─── CSV I/O ─────────────────────────────────────────────────────────────────
def read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))

def write_csv(path, rows):
    with open(path,"w",newline="",encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        w.writeheader(); w.writerows(rows)
    log.info(f"✓ Saved {len(rows)} rows → {path}")

def write_status(rows):
    has_name  = sum(1 for r in rows if r.get("contact_name","").strip())
    has_email = sum(1 for r in rows if r.get("email","").strip())
    verified  = sum(1 for r in rows if "verified" in r.get("status","").lower())
    status = {
        "last_run": datetime.utcnow().isoformat()+"Z",
        "total_rows": len(rows),
        "rows_with_name": has_name,
        "rows_with_email": has_email,
        "rows_verified": verified,
        "coverage_pct": round(has_name/len(rows)*100,1) if rows else 0,
    }
    with open("crawl_status.json","w") as f: json.dump(status, f, indent=2)
    log.info(f"✓ Status: {has_name}/{len(rows)} named, {has_email} with email")

# ─── GITHUB PUSH ─────────────────────────────────────────────────────────────
def push_file(path, message):
    token = os.environ.get("GITHUB_TOKEN")
    if not token: log.error("GITHUB_TOKEN not set"); return
    filename = os.path.basename(path)
    api = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/contents/{filename}"
    hdrs = {"Authorization":f"token {token}","Accept":"application/vnd.github.v3+json"}
    r = requests.get(api, headers=hdrs, params={"ref":GITHUB_BRANCH})
    sha = r.json().get("sha") if r.ok else None
    with open(path,"rb") as f: content=base64.b64encode(f.read()).decode()
    payload = {"message":message,"content":content,"branch":GITHUB_BRANCH}
    if sha: payload["sha"]=sha
    r = requests.put(api, headers=hdrs, json=payload)
    if r.ok: log.info(f"✓ Pushed {filename} to GitHub")
    else: log.error(f"Push failed: {r.status_code} {r.text[:200]}")

# ─── MAIN ─────────────────────────────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser(description="ABM University Crawler + Discovery")
    p.add_argument("--all",      action="store_true", help="Re-crawl all rows")
    p.add_argument("--discover", action="store_true", help="Find and add new universities")
    p.add_argument("--row",      type=int, help="Crawl a single row number")
    p.add_argument("--country",  type=str, help="Filter by country code (e.g. DE)")
    p.add_argument("--push",     action="store_true", help="Push updated files to GitHub")
    p.add_argument("--csv",      type=str, default=CSV_FILE)
    args = p.parse_args()

    if not os.path.exists(args.csv):
        log.error(f"CSV not found: {args.csv}"); sys.exit(1)

    rows = read_csv(args.csv)
    log.info(f"Loaded {len(rows)} rows")

    # ── discovery mode ────────────────────────────────────────────────────────
    if args.discover:
        new_rows = run_discovery(rows)
        rows.extend(new_rows)
        log.info(f"Added {len(new_rows)} new universities")

    # ── update existing rows ──────────────────────────────────────────────────
    updated = 0
    for i, row in enumerate(rows):
        if args.row is not None and i!=args.row: continue
        if args.country and row.get("country_code","").upper()!=args.country.upper(): continue
        if not args.all and row.get("contact_name","").strip(): continue
        if not row.get("url","").strip(): continue
        rows[i] = crawl_row(row)
        updated += 1
        time.sleep(DELAY)

    log.info(f"Crawled {updated} rows")
    write_csv(args.csv, rows)
    write_status(rows)

    if args.push:
        msg = f"crawler: update {datetime.now().strftime('%Y-%m-%d %H:%M')} UTC"
        push_file(args.csv, msg)
        push_file("crawl_status.json", msg)

if __name__ == "__main__":
    main()

# Medical university discovery sources (append to DISCOVERY_SOURCES in the script)
MEDICAL_SOURCES = [
    ("DE", "Germany",       "https://www.uniklinika.de/"),           # German university hospitals
    ("AT", "Austria",       "https://www.meduniwien.ac.at"),         # MedUni Wien
    ("CH", "Switzerland",   "https://www.swiss-universities.ch"),    # Swiss medical
    ("EU", "",              "https://www.emuni.si/"),                 # European medical
    ("UK", "United Kingdom","https://www.medschools.ac.uk/"),         # UK medical schools council
]


def inject_data_into_html(csv_path, html_path):
    """
    Reads prospects.csv and injects the data directly into index.html
    as a JS variable — eliminates the CSV fetch entirely.
    Call this after write_csv().
    """
    import csv, json, os
    if not os.path.exists(html_path):
        log.warning(f"HTML not found: {html_path}")
        return

    with open(csv_path, encoding='utf-8') as f:
        rows = list(csv.DictReader(f))

    data_js = "window.PROSPECT_DATA = " + json.dumps(rows, ensure_ascii=False) + ";"

    with open(html_path, encoding='utf-8') as f:
        html = f.read()

    # Replace or insert the data block
    marker_start = "/* PROSPECT_DATA_START */"
    marker_end   = "/* PROSPECT_DATA_END */"

    if marker_start in html:
        import re
        html = re.sub(
            r'/\* PROSPECT_DATA_START \*/.*?/\* PROSPECT_DATA_END \*/',
            f"{marker_start}\n{data_js}\n{marker_end}",
            html, flags=re.DOTALL
        )
    else:
        # inject before closing </script> of the main script block
        html = html.replace(
            "loadData();",
            f"{marker_start}\n{data_js}\n{marker_end}\nloadData();"
        )

    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(html)

    log.info(f"✓ Injected {len(rows)} rows into {html_path}")
