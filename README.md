# FindFold

FindFold is a command-line pipeline I built to find local service businesses that fit the FoxWorks.dev customer profile. It scrapes Google Maps or Bing for a set of keywords and cities, fetches each company's homepage, scores it against the profile with keyword rules, sends only the ambiguous cases to Claude for a second opinion, and writes CSVs ready for Apollo enrichment and outreach. It remembers domains it has already processed so a business is never enriched twice.

## Status

Working. There are no automated tests. The scrapers drive the installed Google Chrome through Playwright, so Chrome must be present, and the sites being scraped can change their markup at any time.

## Run it

```
pip install -r requirements.txt
cp .env.example .env
python main.py --keywords "plumbing,HVAC,roofing" --location "Salt Lake City Utah" --limit 300
```

Output lands in `output/` as raw_leads.csv, filtered_leads.csv, apollo_ready.csv, and outreach_ready.csv. The Anthropic key in `.env` is optional. Without it the Claude step is skipped and the keyword scores are final.

## The main decision

I put the LLM at the narrowest point in the funnel instead of scoring every lead with it. Keyword rules score each homepage from 0 to 1. Leads that land clearly above or below the threshold keep that score. Only leads in the 0.40 to 0.72 band go to Claude Haiku, ten per request, and Claude's score replaces the keyword score for those. Paid Apollo enrichment comes after all of this, and only for domains not seen in a previous run. A run of a few hundred leads costs cents in API spend, and the scoring rules stay readable in config.py. The trade-off is that the keyword weights need hand tuning for each new industry.
