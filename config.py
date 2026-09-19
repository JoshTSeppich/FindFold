"""
All the knobs in one place: paths, thresholds, keyword lists, and scoring weights.

The keyword lists are what I tune when the pipeline runs on a new industry.
Set ANTHROPIC_API_KEY in .env to turn on Claude rescoring.
"""
from pathlib import Path

# Paths
OUTPUT_DIR = Path("output")
CACHE_DIR = Path(".cache")

RAW_OUTPUT = OUTPUT_DIR / "raw_leads.csv"
FILTERED_OUTPUT = OUTPUT_DIR / "filtered_leads.csv"
APOLLO_OUTPUT = OUTPUT_DIR / "apollo_ready.csv"
OUTREACH_OUTPUT = OUTPUT_DIR / "outreach_ready.csv"
SEEN_DOMAINS_FILE = OUTPUT_DIR / "seen_domains.json"

# Keyword scoring. A lead below this score is dropped, unless Claude is on and
# the score is still inside the ambiguous band below.
ICP_THRESHOLD = 0.55

# Claude rescoring. Only leads whose keyword score lands in this band go to
# Claude. Its score replaces the keyword score, and the lead must then clear
# CLAUDE_FINAL_THRESHOLD.
CLAUDE_MODEL = "claude-haiku-4-5-20251001"
CLAUDE_AMBIGUOUS_MIN = 0.40
CLAUDE_AMBIGUOUS_MAX = 0.72
CLAUDE_CONCURRENCY = 5
CLAUDE_FINAL_THRESHOLD = 0.60

# Directories and marketplaces. Never a customer.
DIRECTORY_DOMAINS = {
    "yelp.com", "angi.com", "angieslist.com", "thumbtack.com",
    "houzz.com", "homeadvisor.com", "yellowpages.com", "bbb.org",
    "manta.com", "expertise.com", "bark.com", "porch.com",
    "nextdoor.com", "google.com", "google.co", "facebook.com",
    "instagram.com", "bing.com", "mapquest.com", "whitepages.com",
    "superpages.com", "citysearch.com", "findlocalpros.com",
    "taskrabbit.com", "networx.com", "servicemagic.com",
    "checkbook.org", "topratedlocal.com", "buildzoom.com",
    "craftjack.com", "improvenet.com", "fixr.com", "groupon.com",
    "reddit.com", "twitter.com", "x.com", "linkedin.com",
    "youtube.com", "tiktok.com", "foursquare.com",
    "tripadvisor.com", "trustpilot.com", "chamber.com",
}

# National chains and franchises. Not small businesses, so not customers.
FRANCHISE_DOMAINS = {
    "rotorooter.com", "mrrooterutah.com", "mrrooter.com",
    "servpro.com", "servicemaster.com", "belfor.com",
    "mollymaid.com", "maidpro.com", "merry-maids.com", "themaids.com",
    "janiking.com", "servicesuper.com", "coverall.com",
    "1800gotjunk.com", "collegehunks.com", "twomenheavy.com",
    "anytimeplumbing.com", "rooterman.com", "bengal.com",
    "jennycraigslt.com", "orangetheory.com", "planetfitness.com",
    "anytimefitness.com", "goldsgym.com", "24hourfitness.com",
    "aspendentalcare.com", "aspendental.com", "sonitusdentalcare.com",
    "heartlanddental.com", "dentalcare.com",
}

# Brand names that mark a franchise even when the domain is not listed above.
FRANCHISE_NAME_SIGNALS = [
    "roto-rooter", "mr. rooter", "mr rooter",
    "servpro", "service master", "servicemaster",
    "molly maid", "maid pro", "merry maids",
    "jani-king", "jan-pro",
    "1-800-got-junk", "college hunks",
    "snap-on", "re/max", "keller williams", "century 21",
    "h&r block", "liberty tax",
    "supercuts", "great clips", "sport clips",
    "aspen dental", "heartland dental",
    "orangetheory", "planet fitness", "anytime fitness",
]

# Phrases that show a business takes inbound work.
BOOKING_INTENT_PHRASES = [
    "free estimate", "free quote", "instant quote",
    "call now", "call today", "call us",
    "book appointment", "book online", "book now",
    "request quote", "request service", "get a quote",
    "get estimate", "get started",
    "free consultation", "schedule now", "schedule service",
    "schedule a call", "24/7", "same day", "emergency service",
]

# Phrases small local businesses use about themselves.
TRUST_PHRASES = [
    "family owned", "family-owned", "family run",
    "locally owned", "locally-owned", "local",
    "serving", "we serve",
    "licensed", "insured", "bonded", "certified",
    "years of experience", "years experience",
    "since 19", "since 20",
    "satisfaction guaranteed", "satisfaction guarantee",
    "no job too small",
]

# Phrases that mark a company as too big.
ENTERPRISE_SIGNALS = [
    " platform", "enterprise", "global ",
    " api ", " saas ", " b2b ",
    " inc.", " corp.", " corporation",
    "holdings", "nationwide", "international",
    "headquarters", "fortune 500",
    "publicly traded", "nyse:", "nasdaq:",
    "thousands of locations", "hundreds of locations",
    "50+ locations", "100+ locations",
]

# Phrases that mark a directory rather than a business.
DIRECTORY_SIGNALS = [
    "find a ", "search for ", "compare ",
    "get quotes from", "thousands of pros",
    "millions of reviews", "browse pros",
    "list your business", "claim your business",
    "read reviews", "top rated pros near",
]

# Two or more of these and the page is mostly a careers page.
CAREER_SIGNALS = [
    "we're hiring", "join our team",
    "open positions", "job openings",
    "apply now", "careers page",
    "view all jobs",
]

# Industry groups. A lead gets the keyword_match weight if its text or the
# search keywords match any phrase in any group.
INDUSTRY_KEYWORDS = {
    "home_services": [
        "plumbing", "plumber", "hvac", "heating", "cooling", "air conditioning",
        "roofing", "roofer", "electrical", "electrician", "wiring",
        "cleaning", "maid", "janitorial", "painting", "painter",
        "landscaping", "lawn care", "lawn service", "pest control",
        "gutters", "gutter", "siding", "windows", "remodeling",
        "renovation", "contractor", "handyman", "pressure washing",
        "power washing", "pool service", "irrigation", "sprinkler",
        "garage door", "locksmith", "chimney", "fireplace",
        "flooring", "carpet cleaning", "junk removal",
    ],
    "appointment_based": [
        "med spa", "medspa", "medical spa", "dental", "dentist",
        "wellness", "fitness", "gym", "yoga", "pilates",
        "massage", "massage therapy", "salon", "hair salon",
        "nail salon", "spa", "day spa", "chiropractic",
        "physical therapy", "optometry", "eye care",
        "orthodontist", "dermatology", "skin care",
        "laser", "botox", "filler", "aesthetics",
        "weight loss", "nutrition", "personal trainer",
    ],
    "service_businesses": [
        "property management", "real estate", "insurance",
        "accounting", "bookkeeping", "tax preparation",
        "legal", "law firm", "attorney", "lawyer",
        "marketing agency", "digital marketing",
        "photography", "wedding photographer",
        "event planning", "catering", "moving company",
        "self storage", "auto repair", "mechanic",
        "car wash", "auto detailing", "towing",
    ],
}

# Scoring weights. Positives add up to 0.95, and a good review score adds
# 0.05 on top. The franchise penalty alone is enough to drop a lead.
SCORE_WEIGHTS = {
    "keyword_match": 0.20,
    "location_match": 0.20,
    "has_website": 0.15,
    "has_contact_indicators": 0.15,
    "booking_intent": 0.15,
    "trust_signals": 0.10,
    "franchise": -0.90,
    "directory": -0.50,
    "enterprise": -0.40,
    "careers_heavy": -0.30,
}

# Fetching and caching.
REQUEST_TIMEOUT = 15        # seconds per request
MAX_RETRIES = 3
RETRY_DELAY = 1.5           # seconds, doubled on each retry after the first
CONCURRENT_REQUESTS = 15    # homepages fetched at once
CACHE_TTL_DAYS = 30         # cached pages older than this are fetched again
