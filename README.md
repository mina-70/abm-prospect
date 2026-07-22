# A Brilliant Mind — University Prospect Search

A shareable search engine for finding doctoral school and researcher development contacts across European universities. Built for [abrilliantmind.blog](https://abrilliantmind.blog).

## 🚀 Setup (5 minutes)

### Step 1 — Upload these files to GitHub

Create a new repository (e.g. `abm-prospects`) and upload:
- `index.html` — the website
- `prospects.csv` — the contact data
- `README.md` — this file

### Step 2 — Edit index.html (2 lines)

Open `index.html` and find these two lines near the bottom inside the `<script>` tag:

```js
const GITHUB_USER   = 'YOUR-GITHUB-USERNAME';
const GITHUB_REPO   = 'YOUR-REPO-NAME';
```

Replace with your actual values, e.g.:
```js
const GITHUB_USER   = 'abrilliantmind';
const GITHUB_REPO   = 'abm-prospects';
```

Save and commit.

### Step 3 — Enable GitHub Pages

1. Go to your repository → **Settings** → **Pages**
2. Under "Source" choose **Deploy from a branch**
3. Select branch: `main`, folder: `/ (root)`
4. Click **Save**

Your site will be live at:
`https://YOUR-GITHUB-USERNAME.github.io/YOUR-REPO-NAME/`

(Takes about 1–2 minutes to go live the first time)

---

## 📝 Updating the data

All contact data lives in **`prospects.csv`** — you never need to touch `index.html` again.

To add or update a contact:
1. Edit `prospects.csv` directly on GitHub (click the file → pencil icon)
2. Add a new row following the same column order
3. Commit — the website updates automatically within seconds

### CSV columns

| Column | Description |
|--------|-------------|
| `university` | University name (shown as main heading) |
| `country` | Full country name (e.g. Germany) |
| `country_code` | 2-letter code (e.g. DE) — used for flag and filtering |
| `unit_type` | Category: `Doctoral School`, `Postdoc Support`, `Staff Development`, `Continuing Education`, `Writing Center` |
| `unit_name` | Department/unit name |
| `contact_name` | Person's full name (leave blank if unknown) |
| `role` | Job title |
| `email` | Direct email address |
| `url` | Link to their team page — used for both the university link and the person's name link |
| `priority` | `A`, `B`, or `C` |
| `status` | e.g. `Verified Jul 2026`, `Unit verified`, `To research` |
| `notes` | Any extra context, additional contacts, tips |

---

## 🔗 Sharing

Once GitHub Pages is enabled, share the URL directly. No login needed, works on mobile.

You can also use a custom domain by adding a `CNAME` file to the repo with your domain name.

---

## 📤 Export

The **Export CSV** button in the top-right downloads the currently-filtered view as a CSV ready to import into HubSpot.

---

Data collected July 2026 · [abrilliantmind.blog](https://abrilliantmind.blog)
