# FoxWorks Lead Pipeline

Scrape → Filter → Scan → Score → Export.

Reduces 300–500 raw company records to 100–200 high-probability ICP leads
**before** sending anything to Apollo — cutting enrichment cost 3–10×.

---

## How it works

```
Google Maps / DuckDuckGo
         │
         ▼
   raw_leads.csv          (all scraped, unfiltered)
         │
    Deduplication
         │
   Website Scanner         (async HTTP, cached)
         │
    ICP Scorer             (keyword matching, 0–1 score)
         │
   filtered_leads.csv      (score ≥ 0.6 only)
         │
   ┌─────┴─────┐
   ▼           ▼
apollo_ready   outreach_ready
  .csv           .csv
```

No paid APIs. No LLMs. Runs locally.

---

## Setup

**Requirements:** Python 3.11+

```bash
# 1. Install Python dependencies
pip install -r requirements.txt

# 2. Install the Chromium browser for Playwright
playwright install chromium
```

That's it.

---

## Usage

```bash
# Full run — Google Maps primary, DuckDuckGo fallback
python main.py \
  --keywords "plumbing,HVAC,roofing,med spa" \
  --location "Utah" \
  --limit 300

# DuckDuckGo only (no Playwright, faster, slightly less data)
python main.py \
  --keywords "plumbing,HVAC,roofing" \
  --location "Salt Lake City Utah" \
  --limit 200 \
  --no-maps

# Verbose debug output
python main.py --keywords "roofing" --location "Utah" --limit 50 --debug
```

### Options

| Flag         | Default | Description                                    |
|--------------|---------|------------------------------------------------|
| `--keywords` | —       | Comma-separated ICP keywords (required)        |
| `--location` | —       | Target market string (required)                |
| `--limit`    | 300     | Max raw leads to scrape before filtering       |
| `--no-maps`  | off     | Skip Google Maps; use DuckDuckGo only          |
| `--debug`    | off     | Enable verbose DEBUG logging                   |

---

## Output files

All outputs land in `./output/`.

### `raw_leads.csv`
Everything scraped, before any filtering.

```
company_name,website,location,category,source
Best Utah Plumbing,https://bestutahplumbing.com,Salt Lake City UT,Plumber,google_maps
Wasatch HVAC,https://wasatchhvac.com,Provo UT,HVAC contractor,google_maps
...
```

### `filtered_leads.csv`
ICP-scored leads at or above the threshold (default 0.6).

```
company_name,domain,location,category,icp_score,reason_tags,page_title,page_description
Best Utah Plumbing,bestutahplumbing.com,Salt Lake City UT,Plumber,0.9,has_website|industry:home_services|location_match|has_contact|booking_intent|trust_signals,"Best Plumbers in Utah | Free Estimates","Family owned plumbing serving Utah since 1998..."
Wasatch HVAC,wasatchhvac.com,Provo UT,HVAC contractor,0.85,has_website|industry:home_services|location_match|has_contact|booking_intent,...
```

### `apollo_ready.csv`
Minimal two-column format for Apollo bulk enrichment upload.

```
company_name,domain
Best Utah Plumbing,bestutahplumbing.com
Wasatch HVAC,wasatchhvac.com
...
```

### `outreach_ready.csv`
Richer format for Instantly / Lemlist / manual outreach. Sorted by score descending.

```
company_name,domain,icp_score,reason_tags
Best Utah Plumbing,bestutahplumbing.com,0.9,has_website|industry:home_services|location_match|has_contact|booking_intent|trust_signals
Wasatch HVAC,wasatchhvac.com,0.85,has_website|industry:home_services|location_match|has_contact|booking_intent
...
```

---

## ICP Scoring

Scores are computed from keyword matching only — no LLMs, no paid services.

| Signal                              | Weight  |
|-------------------------------------|---------|
| ICP industry keyword match          | +0.20   |
| Location mentioned                  | +0.20   |
| Has a website domain                | +0.15   |
| Phone number or contact form found  | +0.15   |
| Booking-intent phrases detected     | +0.15   |
| Trust signals (family owned, since) | +0.10   |
| **Directory / marketplace domain**  | **−0.50** |
| **Enterprise language**             | **−0.40** |
| **Careers-heavy content**           | **−0.30** |

Default threshold: **0.6**. Change `ICP_THRESHOLD` in `config.py`.

---

## Tuning

All knobs are in `config.py`:

- `ICP_THRESHOLD` — raise to get fewer, higher-confidence leads
- `BOOKING_INTENT_PHRASES` — add phrases relevant to your niche
- `DIRECTORY_DOMAINS` — extend the blocklist for your market
- `CONCURRENT_REQUESTS` — increase if your network can handle it
- `SCORE_WEIGHTS` — rebalance signal importance

---

## Project structure

```
lead_pipeline/
├── scraper/
│   ├── google_maps.py      # Playwright-based Maps scraper
│   └── search_scraper.py   # DuckDuckGo HTML fallback
├── filter/
│   ├── dedup.py            # Domain normalisation + deduplication
│   └── icp_scorer.py       # ICP scoring + threshold filter
├── scanner/
│   ├── fetcher.py          # Async HTTP with retry + disk cache
│   └── extractor.py        # HTML → title / description / text / signals
└── export/
    ├── apollo.py           # apollo_ready.csv
    └── outreach.py         # outreach_ready.csv

main.py                     # CLI entry point
config.py                   # All tunable constants
requirements.txt
```

---

## Caching

Website HTML is cached in `.cache/` (MD5-keyed files) so repeat runs
don't re-fetch pages you've already scanned. Delete `.cache/` to force
a fresh scan.

---

## Example run output

```
FoxWorks Lead Pipeline
  Keywords : ['plumbing', 'HVAC', 'roofing', 'med spa']
  Location : Utah
  Limit    : 300 raw leads
  Source   : Google Maps (DDG fallback)

──────────────── Stage 1 · Scraping ────────────────
  Source: Google Maps (Playwright)
  ✓ 312 raw leads → output/raw_leads.csv

──────────────── Stage 2 · Deduplication ────────────────
  ✓ 312 → 248 after dedup

──────────────── Stage 3 · Website Scanning ────────────────
  Scanning 248 sites (concurrency=10) …
  ✓ 201/248 pages scanned

──────────────── Stage 4 · ICP Scoring & Filtering ────────────────
  ✓ 147 ICP-qualified leads (≥0.6) → output/filtered_leads.csv

──────────────── Stage 5 · Export ────────────────
  ✓ Apollo:   147 rows → output/apollo_ready.csv
  ✓ Outreach: 147 rows → output/outreach_ready.csv

──────────────────── Pipeline Complete ────────────────────

  Company                              Domain                       Score  Tags
 ─────────────────────────────────────────────────────────────────────────────
  Best Utah Plumbing LLC               bestutahplumbing.com          0.90  has_website|industry:home_services|...
  Wasatch HVAC & Cooling               wasatchhvac.com               0.85  has_website|industry:home_services|...
  Premier Roofing Utah                 premierroofingutah.com        0.85  ...
  Alpine Med Spa                       alpinemedspa.com              0.80  ...
  ...

  Raw scraped:    312
  After dedup:    248
  Pages scanned:  201
  ICP qualified:  147
  Filter ratio:   2.1x (raw → qualified)
```
