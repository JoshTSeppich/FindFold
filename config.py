"""
FoxWorks Lead Pipeline — central configuration.

Tune ICP scoring weights, thresholds, and keyword lists here.
No secrets or API keys required — this pipeline is 100% free to run.
"""
from pathlib import Path

# ---------------------------------------------------------------------------
# Output paths
# ---------------------------------------------------------------------------
OUTPUT_DIR = Path("output")
CACHE_DIR = Path(".cache")

RAW_OUTPUT      = OUTPUT_DIR / "raw_leads.csv"
FILTERED_OUTPUT = OUTPUT_DIR / "filtered_leads.csv"
APOLLO_OUTPUT   = OUTPUT_DIR / "apollo_ready.csv"
OUTREACH_OUTPUT = OUTPUT_DIR / "outreach_ready.csv"

# ---------------------------------------------------------------------------
# ICP threshold — leads below this score are dropped
# ---------------------------------------------------------------------------
ICP_THRESHOLD = 0.6

# ---------------------------------------------------------------------------
# Directory / marketplace domains — always excluded, score penalty applied
# ---------------------------------------------------------------------------
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

# ---------------------------------------------------------------------------
# ICP signal keyword lists
# ---------------------------------------------------------------------------
BOOKING_INTENT_PHRASES = [
    "free estimate", "free quote", "instant quote",
    "call now", "call today", "call us",
    "book appointment", "book online", "book now",
    "request quote", "request service", "get a quote",
    "get estimate", "get started",
    "free consultation", "schedule now", "schedule service",
    "schedule a call", "24/7", "same day", "emergency service",
]

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

ENTERPRISE_SIGNALS = [
    " platform", "enterprise", "global ",
    " api ", " saas ", " b2b ",
    " inc.", " corp.", " corporation",
    "holdings", "nationwide", "international",
    "headquarters", "fortune 500",
    "publicly traded", "nyse:", "nasdaq:",
    "thousands of locations", "hundreds of locations",
]

DIRECTORY_SIGNALS = [
    "find a ", "search for ", "compare ",
    "get quotes from", "thousands of pros",
    "millions of reviews", "browse pros",
    "list your business", "claim your business",
    "read reviews", "top rated pros near",
]

CAREER_SIGNALS = [
    "we're hiring", "join our team",
    "open positions", "job openings",
    "apply now", "careers page",
    "view all jobs",
]

# ---------------------------------------------------------------------------
# Industry keyword groups — used for ICP category detection
# ---------------------------------------------------------------------------
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

# ---------------------------------------------------------------------------
# Scoring weights
# Positives sum to 0.95 max; negatives are penalties.
# ---------------------------------------------------------------------------
SCORE_WEIGHTS = {
    # Positive signals
    "keyword_match":          0.20,
    "location_match":         0.20,
    "has_website":            0.15,
    "has_contact_indicators": 0.15,
    "booking_intent":         0.15,
    "trust_signals":          0.10,
    # Negative penalties
    "directory":             -0.50,
    "enterprise":            -0.40,
    "careers_heavy":         -0.30,
}

# ---------------------------------------------------------------------------
# HTTP / concurrency settings
# ---------------------------------------------------------------------------
REQUEST_TIMEOUT    = 15    # seconds per request
MAX_RETRIES        = 3     # attempts before giving up
RETRY_DELAY        = 1.5   # seconds between attempts (multiplied by attempt #)
CONCURRENT_REQUESTS = 10   # parallel website scans
