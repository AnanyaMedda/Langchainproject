mport urllib.parse
import asyncio
import os
import sys
import re
from crawl4ai import AsyncWebCrawler, BrowserConfig
from bs4 import BeautifulSoup
from langchain.tools import tool

# ---------------------------------------------------------------------------
# DOMAIN LISTS
# ---------------------------------------------------------------------------

TRUSTED_RANKING_SITES = [
    "techradar", "tomsguide", "pcmag", "trustedreviews", "cnet",
    "wired", "digitaltrends", "tomshardware", "theverge", "engadget",
    "laptopmag", "91mobiles", "expertreviews", "t3.com",
    "ign", "gamespot",
    "timeout", "eater", "cntraveler", "theinfatuation", "culturetrip",
    "wanderlog", "tripsavvy", "bonappetit", "foodandwine",
    "empireonline", "rogerebert",
    "forbes", "businessinsider", "nytimes", "theguardian", "independent",
    "dpreview",
]

IGNORE_RANKING_DOMAINS = [
    "tripadvisor", "zomato", "yelp", "swiggy", "justdial", "booking",
    "agoda", "makemytrip", "klook", "magicpin", "opentable",
    "restaurantguru", "google", "dineout", "eazydiner", "wikipedia",
    "amazon", "flipkart", "snapdeal", "reddit", "quora",
]

AGGREGATOR_DOMAINS = [
    "tripadvisor", "zomato", "swiggy", "yelp", "opentable", "justdial",
    "dineout", "magicpin", "restaurantguru", "eazydiner",
    "makemytrip", "agoda", "booking", "klook", "google", "instagram",
    "facebook", "foursquare", "lbb.in", "wikipedia",
    "amazon", "flipkart", "snapdeal", "myntra", "gsmarena",
    "notebookcheck", "rtings", "techradar", "theverge", "tomshardware",
    "pcmag", "cnet", "digitaltrends", "91mobiles", "smartprix",
    "nanoreview", "phonearena", "kimovil", "imdb", "rottentomatoes",
    "metacritic", "letterboxd", "goodreads", "spotify", "pitchfork",
    "dpreview", "bhphotovideo", "adorama",
    "reddit", "quora", "youtube", "twitter", "pinterest",
    "linkedin", "tiktok", "fandom", "wikia",
    # AI / dataset hubs — never an official product page
    "huggingface", "kaggle", "paperswithcode", "arxiv",
    "dataset", "github", "gitlab", "stackoverflow",
    # Blog / self-publish platforms
    "medium.com", "wordpress.com", "substack.com", "blogspot.com",
]

BAD_URL_KEYWORDS = [
    "blog", "review", "news", "article", "guide", "reddit", "youtube",
    "facebook", "instagram", "pinterest", "/list", "top-", ".pdf",
    "wiki", "fandom", "forum", "compare",
    "dataset", "datasets", "huggingface", "kaggle", "arxiv",
    "github", "stackoverflow",
    "medium.com", "wordpress.com", "substack.com", "blogspot.com",
]

SPAM_DOMAINS = [
    "mahjong", "casino", "bet", "gambling", "slot", "jackpot",
    "poker", "lottery", "bingo",
]

# ---------------------------------------------------------------------------
# PAGE-LEVEL GUARDS
# ---------------------------------------------------------------------------

_STRONG_BLOCK_SIGNALS = [
    "enable javascript", "javascript is required",
    "please enable javascript", "you need to enable javascript",
    "javascript must be enabled", "cookie settings",
    "tracking technologies",
]


def is_usable_page(html: str) -> bool:
    if not html or len(html.strip()) < 400:
        return False
    lower = html.lower()
    if any(sig in lower for sig in _STRONG_BLOCK_SIGNALS):
        return False
    soup = BeautifulSoup(html, 'html.parser')
    visible = soup.get_text(separator=' ', strip=True)
    return len(visible.split()) >= 80


# ---------------------------------------------------------------------------
# QUERY PARSING
# ---------------------------------------------------------------------------

def parse_query(query: str):
    count_match = re.search(r'\b(\d+)\b', query)
    target_n = int(count_match.group(1)) if count_match else 5

    location_match = re.search(r'\bin\s+([A-Za-z\s]+?)(?:\s+\d{4})?$',
                               query, re.IGNORECASE)
    location = location_match.group(1).strip() if location_match else ""

    category = query.lower()
    category = re.sub(r'\b(find|top|best|list of|in)\b', '', category)
    if location:
        category = category.replace(location.lower(), '')
    category = re.sub(r'\d+', '', category)
    category = re.sub(r'\s+', ' ', category).strip()

    return target_n, category, location


# ---------------------------------------------------------------------------
# ENTITY TYPE DETECTION + VALIDATION
# ---------------------------------------------------------------------------

# Maps query keywords to an entity type label
_ENTITY_TYPE_MAP = [
    # tech hardware — expect "BrandName ModelName" patterns
    (["laptop", "laptops", "notebook"],           "laptop"),
    (["phone", "phones", "smartphone", "smartphones", "mobile"], "phone"),
    (["camera", "cameras", "mirrorless", "dslr"],  "camera"),
    (["headphone", "headphones", "earphone", "earbuds", "earphones"], "headphone"),
    (["tv", "television", "monitor", "display"],   "display"),
    # food / hospitality
    (["restaurant", "restaurants", "cafe", "cafes", "eatery", "dining", "food"], "restaurant"),
    (["hotel", "hotels", "resort", "resorts"],     "hotel"),
    # entertainment
    (["movie", "movies", "film", "films"],         "movie"),
    (["game", "games", "videogame"],               "game"),
    (["book", "books", "novel", "novels"],         "book"),
]

# Per entity type: words/patterns that should NOT appear in real item names
_ENTITY_REJECT: dict[str, re.Pattern] = {
    "laptop": re.compile(
        r'\b(gaming|performance|value|budget|premium|display|battery|'  
        r'keyboard|port|weight|thin|light|powerful|affordable|expensive)\b',
        re.IGNORECASE,
    ),
    "phone": re.compile(
        r'\b(camera|selfie|battery|charging|display|screen|chip|'  
        r'processor|storage|design|ultra|pro\s+max|fold|flip)\b',
        re.IGNORECASE,
    ),
    "camera": re.compile(
        r'\b(sensor|autofocus|video|stabilization|viewfinder|lens|'  
        r'aperture|shutter|iso|burst|raw|weather|sealed)\b',
        re.IGNORECASE,
    ),
    "restaurant": re.compile(
        r'\b(cuisine|menu|ambiance|rooftop|buffet|veg|non.?veg|'  
        r'fine\s+dining|casual|outdoor|indoor|takeaway|delivery)\b',
        re.IGNORECASE,
    ),
}


def detect_entity_type(query: str) -> str:
    """Return the entity type label for the query, or 'generic'."""
    q = query.lower()
    for keywords, label in _ENTITY_TYPE_MAP:
        if any(kw in q for kw in keywords):
            return label
    return "generic"


def _passes_entity_check(name: str, entity_type: str) -> bool:
    """
    Category-aware rejection.
    Returns False if the name looks like a feature/spec for that entity type
    rather than an actual item name.
    For 'generic', always passes.
    """
    if entity_type == "generic":
        return True
    pattern = _ENTITY_REJECT.get(entity_type)
    if pattern is None:
        return True
    # Only reject if the WHOLE name is built from these words (not just contains)
    words = name.split()
    reject_count = sum(1 for w in words if pattern.search(w))
    # If more than half the words are category-feature words, it's a heading
    return reject_count / max(len(words), 1) < 0.6


# ---------------------------------------------------------------------------
# NAME EXTRACTION — strict, numbered-first logic
# ---------------------------------------------------------------------------

# ── Reject patterns ────────────────────────────────────────────────────────

# Date patterns: "Jan 28, 2025", "Nov 2024", standalone month names
_DATE_RE = re.compile(
    r'\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?'
    r'|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?'
    r'|Nov(?:ember)?|Dec(?:ember)?)\b'
    r'|\b\d{1,2}[,\s]\s*\d{4}\b'
    r'|\b\d{4}\b',   # bare year e.g. "2025"
    re.IGNORECASE,
)

# Measurement/spec units → these are spec lines, not item names
_SPEC_RE = re.compile(
    r'\b\d+\.?\d*\s*(?:mp|megapixel|hz|khz|ghz|mhz|gb|tb|mb|fps|'
    r'inch(?:es)?|cm|mm|ms|mah|watts?|w\b|nm|nits?|ppi|dpi|rpm|'
    r'db|lux|x\d+|p\b)\b',
    re.IGNORECASE,
)

# Generic section / navigation headings AND marketing comparison phrases
_GENERIC_HEADING_RE = re.compile(
    r'\b(top|best|guide|review|buying|quick\s+list|jump\s+to|comparison|'
    r'faq|editor(?:s?)\'?\s*(?:choice|pick|note)?|tested\s+pick|'
    r'our\s+pick|our\s+top|recommended|most\s+popular|runner.?up|'
    r'what\s+(is|are|to)|how\s+to|why\s+we|pros?\s+and\s+cons?|'
    r'bottom\s+line|verdict|summary|conclusion|introduction|overview|'
    r'table\s+of\s+contents?|affiliate|advertisement|'
    # marketing / comparison framing that are NOT item names
    r'better\s+performer|affordable\s+kit|budget\s+pick|value\s+pick|'
    r'premium\s+pick|top\s+pick|best\s+overall|best\s+value|'
    r'best\s+budget|best\s+premium|best\s+for|great\s+for|'
    r'most\s+versatile|most\s+affordable|upgrade\s+pick|'
    r'also\s+great|step.?up|mid.?range\s+pick)\b',
    re.IGNORECASE,
)

# Comparative article framing — "The better performer", "A solid kit"
# These start with an article (The/A/An) + adjective, not a brand name.
_ARTICLE_FRAMING_RE = re.compile(
    r'^(?:the|a|an)\s+(?:better|worse|best|worst|good|great|solid|strong|'
    r'affordable|budget|premium|cheap|expensive|versatile|capable|'
    r'reliable|practical|compact|portable|lightweight)\b',
    re.IGNORECASE,
)

# Phone/camera feature names that are NOT product names
_FEATURE_WORDS_RE = re.compile(
    r'\b(main\s+camera|ultra\s+wide|telephoto|front\s+camera|'
    r'smart\s+hdr|portrait\s+mode|night\s+mode|cinematic\s+mode|'
    r'action\s+mode|display|battery\s+life|charging\s+speed|'
    r'performance|storage|processor|connectivity|design|price|'
    r'benchmarks?|speed\s+test|build\s+quality|audio|fingerprint|'
    r'face\s+id|biometric)\b',
    re.IGNORECASE,
)

# UI navigation noise + metadata labels
_UI_NOISE_RE = re.compile(
    r'\b(cookie|privacy|newsletter|sign[\s\-]?(?:up|in)|subscribe|log\s*in|'
    r'advertisement|notification|tracking|javascript|suggestions?\s+available|'
    r'read\s+more|see\s+more|load\s+more|show\s+more|back\s+to\s+top|'
    r'skip\s+to|close\s+menu|open\s+menu|search\s+for|share\s+this|'
    # navigation / page-structure words
    r'about\s+us|contact\s+us|partner\s+offer|your\s+itinerary|'
    r'mentioned\s+on|listed\s+on|view\s+on\s+map|open\s+in\s+maps|'
    r'get\s+directions|add\s+to\s+trip|save\s+place|claim\s+this)\b',
    re.IGNORECASE,
)

# Ratings / social proof metadata: "(206)", "• Mentioned on 5 lists", "4.5 stars"
_RATING_RE = re.compile(
    r'\(\d+\)'                          # (206)
    r'|•?\s*\d+\.?\d*\s*(?:star|review|rating|list|vote|like)s?'  # 4.5 stars
    r'|•?\s*mentioned\s+on\s+\d+\s+lists?'  # mentioned on 5 lists
    r'|•?\s*listed\s+on\s+\d+\s+lists?'     # listed on 3 lists
    r'|\$+\d*[-–]?\$*\d*'               # price ranges $$ $$$
    r'|\d+\s*(?:min(?:ute)?s?|km|mi(?:le)?s?)\s+away',  # distance
    re.IGNORECASE,
)


def _clean_rank_prefix(text: str) -> str:
    """Strip '1.', '2)', '#3', 'No. 4' from the start."""
    return re.sub(r'^(?:No\.?\s*)?\#?\d+[\.\-\):\s]+', '', text).strip()


def _scrub_metadata(text: str) -> str:
    """Remove inline ratings, social proof, and price markers before validation."""
    text = _RATING_RE.sub('', text)
    # Also strip trailing parenthetical notes: "Karavalli (Seafood)"
    text = re.sub(r'\s*\([^)]{0,30}\)\s*$', '', text)
    return re.sub(r'\s+', ' ', text).strip()


def _clean_name(text: str) -> str:
    """Normalise: strip rank prefix, scrub metadata, cut at separator."""
    text = _clean_rank_prefix(text)
    text = _scrub_metadata(text)
    text = re.split(r'\s*[:|–—]\s*', text)[0]
    return re.sub(r'\s+', ' ', text).strip()


def _is_valid_name(name: str) -> bool:
    """
    Returns True only if `name` looks like a real item (product/restaurant/film).
    Rejects: dates, specs, generic headings, feature words, UI/nav noise,
             ratings, too-short/long phrases, pure-number strings.
    """
    if not name:
        return False

    # Character length: 3–60
    if not (3 <= len(name) <= 60):
        return False

    words = name.split()

    # Word count: 1–8  (single-word restaurant names like "Flurys" are valid)
    if not (1 <= len(words) <= 8):
        return False

    # Must contain at least one purely alphabetic word (not all symbols/digits)
    if not any(re.search(r'[a-zA-Z]', w) for w in words):
        return False

    # No dates
    if _DATE_RE.search(name):
        return False

    # No spec measurements
    if _SPEC_RE.search(name):
        return False

    # No generic section headings
    if _GENERIC_HEADING_RE.search(name):
        return False

    # No camera/phone feature words
    if _FEATURE_WORDS_RE.search(name):
        return False

    # No UI / navigation / metadata noise
    if _UI_NOISE_RE.search(name):
        return False

    # Not purely numeric / punctuation
    if all(re.fullmatch(r'[\d\s\W]+', w) for w in words):
        return False

    # Reject comparison / marketing framing: "The better performer", "A solid kit"
    if _ARTICLE_FRAMING_RE.match(name):
        return False

    return True


def _deduplicate(names: list) -> list:
    seen = []
    for n in names:
        lower = n.lower()
        if not any(lower in s.lower() or s.lower() in lower for s in seen):
            seen.append(n)
    return seen


# --- Numbered-line regex ---
_NUMBERED_LINE_RE = re.compile(r'^\d+[.)]\s*(.+)$')


def _strategy_numbered_lines(soup: BeautifulSoup, target_n: int) -> list:
    """
    HIGHEST PRIORITY.
    Scans every tag for patterns like '1. Alienware m18' or '2) Indian Accent'.
    Searches: <li>, <h2>, <h3>, <h4>, <p>, <td>.
    """
    results = []
    for tag in soup.find_all(['li', 'h2', 'h3', 'h4', 'p', 'td', 'div']):
        raw = tag.get_text(separator=' ', strip=True)
        # Only look at the first 120 chars; don't parse long paragraphs
        raw = raw[:120]
        m = _NUMBERED_LINE_RE.match(raw.strip())
        if not m:
            continue
        name = _clean_name(m.group(1))
        if _is_valid_name(name) and name not in results:
            results.append(name)
        if len(results) >= target_n * 2:
            break
    return results


def _strategy_ol_lists(soup: BeautifulSoup, target_n: int) -> list:
    """
    SECOND PRIORITY.
    Reads items from <ol> ordered lists — very reliable for ranking pages.
    """
    results = []
    for ol in soup.find_all('ol'):
        for li in ol.find_all('li', recursive=False):
            strong = li.find(['strong', 'b', 'a'])
            raw = (strong.get_text(separator=' ', strip=True)
                   if strong
                   else li.get_text(separator=' ', strip=True))
            name = _clean_name(raw[:120])
            if _is_valid_name(name) and name not in results:
                results.append(name)
        if len(results) >= target_n * 2:
            break
    return results


def _strategy_strong_in_ul(soup: BeautifulSoup, target_n: int) -> list:
    """
    THIRD PRIORITY.
    Bold text inside unordered list items — common in food/travel listicles.
    """
    results = []
    for li in soup.find_all('li'):
        strong = li.find(['strong', 'b'])
        if not strong:
            continue
        name = _clean_name(strong.get_text(separator=' ', strip=True)[:100])
        if _is_valid_name(name) and name not in results:
            results.append(name)
        if len(results) >= target_n * 2:
            break
    return results


def _strategy_clean_headings(soup: BeautifulSoup, target_n: int) -> list:
    """
    LAST RESORT.
    Unnumbered <h2>/<h3> headings that pass all filters.
    """
    results = []
    for tag in soup.find_all(['h2', 'h3']):
        raw = tag.get_text(separator=' ', strip=True)
        if _NUMBERED_LINE_RE.match(raw.strip()):
            continue   # handled by strategy 1
        name = _clean_name(raw[:100])
        if _is_valid_name(name) and name not in results:
            results.append(name)
        if len(results) >= target_n * 2:
            break
    return results


def extract_item_names(html: str, target_n: int,
                       entity_type: str = "generic") -> list:
    """
    Run 4 strategies in descending confidence order.
    Returns up to 2×target_n unique, valid names.
    Stop early once enough names are found.
    Each candidate also passes entity-type validation.
    """
    soup = BeautifulSoup(html, 'html.parser')
    names: list[str] = []

    for strategy_fn in [
        _strategy_numbered_lines,
        _strategy_ol_lists,
        _strategy_strong_in_ul,
        _strategy_clean_headings,
    ]:
        batch = strategy_fn(soup, target_n)
        for name in batch:
            lower = name.lower()
            if not any(lower in n.lower() or n.lower() in lower for n in names):
                if _passes_entity_check(name, entity_type):
                    names.append(name)
        if len(names) >= target_n:
            break   # enough — don't add noise from lower-confidence strategies

    return _deduplicate(names)[:max(target_n * 2, 10)]


# ---------------------------------------------------------------------------
# URL HELPERS
# ---------------------------------------------------------------------------

def is_official_url(url: str) -> bool:
    lower = url.lower()
    if not lower.startswith("http"):
        return False
    if any(s in lower for s in SPAM_DOMAINS):
        return False
    if any(a in lower for a in AGGREGATOR_DOMAINS):
        return False
    if any(k in lower for k in BAD_URL_KEYWORDS):
        return False
    return True


def extract_ddg_links(html: str) -> list:
    soup = BeautifulSoup(html, 'html.parser')
    links = []
    for a in soup.find_all('a', class_='result__snippet'):
        href = a.get("href", "")
        if "uddg=" in href:
            url = urllib.parse.unquote(href.split("uddg=")[1].split("&")[0])
        elif href.startswith("http") and "duckduckgo.com" not in href:
            url = href
        else:
            continue
        if "duckduckgo.com/y.js" in url:
            continue
        links.append(url)
    return links


# ---------------------------------------------------------------------------
# ASYNC PIPELINE STEPS
# ---------------------------------------------------------------------------

async def get_ranking_article_url(crawler, query: str,
                                  max_candidates: int = 10) -> str | None:
    """
    STEP 1 – Find and validate a ranking article.
    Prefers trusted editorial sites, validates each before returning.
    """
    search_url = (f"https://html.duckduckgo.com/html/"
                  f"?q={urllib.parse.quote(query)}")
    result = await crawler.arun(url=search_url)
    if not result.html:
        return None

    links = extract_ddg_links(result.html)
    trusted, fallback = [], []

    for url in links[:max_candidates]:
        lower = url.lower()
        if any(site in lower for site in TRUSTED_RANKING_SITES):
            trusted.append(url)
        elif not any(dom in lower for dom in IGNORE_RANKING_DOMAINS):
            fallback.append(url)

    for url in trusted + fallback:
        page = await crawler.arun(url=url)
        if page.success and is_usable_page(page.html):
            return url

    return links[0] if links else None


async def get_official_url_for_item(crawler, name: str, category: str,
                                    location: str) -> str | None:
    """STEP 3 – Find the official/brand page for one item."""
    clean_name = _clean_rank_prefix(name)
    location_part = f" {location}" if location else ""
    query = f"{clean_name} {category}{location_part} official website"
    search_url = (f"https://html.duckduckgo.com/html/"
                  f"?q={urllib.parse.quote(query)}")

    result = await crawler.arun(url=search_url)
    if not result.html:
        return None

    for url in extract_ddg_links(result.html):
        if is_official_url(url):
            return url
    return None


async def scrape_page_content(crawler, url: str, word_limit: int = 400) -> str:
    """STEP 4 – Scrape first `word_limit` words; skip blocked pages."""
    try:
        result = await asyncio.wait_for(crawler.arun(url=url), timeout=15)
        if not result.success or not result.html:
            return ""
        if not is_usable_page(result.html):
            return ""
        soup = BeautifulSoup(result.html, 'html.parser')
        text = soup.get_text(separator=' ', strip=True)
        return ' '.join(text.split()[:word_limit])
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# MAIN ENTRY POINT
# ---------------------------------------------------------------------------

async def execute_duckduckgo_search(query: str) -> str:
    """
    Generic ranked-search pipeline:

      1. DDG search → pick a trusted, validated ranking article.
      2. Multi-strategy extraction in confidence order:
           numbered lines → ordered lists → bold-in-li → clean headings.
         Each candidate is filtered for dates, specs, generic headings,
         feature words, and UI noise before being accepted.
      3. For each item → find its official website (aggregators blocked).
      4. Scrape first 400 words from that website.
      5. Return formatted results.

    Works for: restaurants, laptops, phones, cameras, movies, headphones, etc.
    """
    target_n, category, location = parse_query(query)
    entity_type = detect_entity_type(query)

    browser_config = BrowserConfig(headless=True, browser_type="chromium",
                                   verbose=False)

    old_stdout, old_stderr = sys.stdout, sys.stderr
    fnull = open(os.devnull, 'w', encoding='utf-8')
    sys.stdout = sys.stderr = fnull

    results_output = []

    try:
        async with AsyncWebCrawler(config=browser_config) as crawler:

            # STEP 1 ─ Ranking article
            ranking_url = await get_ranking_article_url(crawler, query)
            if not ranking_url:
                sys.stdout = old_stdout; sys.stderr = old_stderr
                return (" Could not find a ranking article. "
                        "DuckDuckGo may be rate-limiting or returned 0 results.")

            # STEP 2 ─ Extract names
            article_result = await crawler.arun(url=ranking_url)
            if not article_result.success or not is_usable_page(article_result.html):
                sys.stdout = old_stdout; sys.stderr = old_stderr
                return (f" Ranking article blocked or failed to load.\n"
                        f"   Source: {ranking_url}")

            names = extract_item_names(article_result.html, target_n,
                                       entity_type=entity_type)

            if not names:
                sys.stdout = old_stdout; sys.stderr = old_stderr
                return (f" Could not extract item names from: {ranking_url}")

            # STEPS 3 & 4 ─ Find website + scrape
            fetched = 0
            for name in names:
                if fetched >= target_n:
                    break

                official_url = await get_official_url_for_item(
                    crawler, name, category, location
                )
                if not official_url:
                    continue

                content = await scrape_page_content(crawler, official_url)
                if not content:
                    continue

                clean = _clean_rank_prefix(name)
                results_output.append(
                    f"Result {fetched + 1}\n"
                    f"Name: {clean}\n"
                    f"URL: {official_url}\n"
                    f"First 400 Words: {content}\n"
                )
                fetched += 1

    except Exception as e:
        import traceback
        sys.stdout = old_stdout; sys.stderr = old_stderr
        traceback.print_exc()
        return f"Agent execution failed: {str(e)}"
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr
        fnull.close()

    if not results_output:
        return ("Search completed but no official websites could be "
                "found for the extracted item names.")

    return f"=== Results for: {query} ===\n\n" + "\n\n".join(results_output)


# ---------------------------------------------------------------------------
# LANGCHAIN TOOL WRAPPER
# ---------------------------------------------------------------------------

@tool
async def search_duckduckgo(query: str) -> str:
    """
    Search the internet for any ranked/top-N query and return scraped content
    from official brand sources.

    Examples:
      - "find top 5 restaurants in Delhi"
      - "top 3 gaming laptops 2026"
      - "best 5 DSLR cameras under 50000"
      - "top 10 sci-fi movies of all time"
      - "best 4 wireless headphones"
      - "top 5 smartphones 2025"
    """
    return await execute_duckduckgo_search(query)
