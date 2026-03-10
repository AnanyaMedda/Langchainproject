import urllib.parse
import asyncio
import os
import sys
import re
from crawl4ai import AsyncWebCrawler, BrowserConfig
from bs4 import BeautifulSoup
from langchain.tools import tool

def extract_names_from_html(html, target_n, location_base=""):
    """Fallback heuristic to parse restaurant names from listicles without an LLM."""
    soup = BeautifulSoup(html, 'html.parser')
    names = []
    
    # Usually listicles (Eater, Timeout, Condenast) use <h2> or <h3> for items
    for tag in soup.find_all(['h2', 'h3']):
        text = tag.get_text(separator=" ", strip=True)
        # Match standard listicles "1. Name" OR just "Name" if it's inside an h2/h3
        clean_text = re.sub(r'^\d+[\.\-\)]?\s+', '', text).strip()
        
        if 2 < len(clean_text) < 60:
            # Remove descriptions: "Peter Cat: The Indian Continental Fusion" -> "Peter Cat"
            clean_text = re.split(r'[:|–-]', clean_text)[0].strip()
            lower_text = clean_text.lower()
            
            # 1. Ignore article titles & generic headings
            if ('restaurants' in lower_text or 'places' in lower_text) and ('essential' in lower_text or 'top' in lower_text or 'best' in lower_text):
                continue
            if lower_text in ['categories', 'all categories', 'top restaurants', 'best restaurants', 'home', 'about', 'services', 'listings', 'blog', 'contact', 'search']:
                continue

            # Skip common geographic headers that appear in listicles grouping by region
            geo_headers = {
                'india', 'north india', 'south india', 'east india', 'west india', 'central india',
                'maharashtra', 'tamil nadu', 'karnataka', 'kerala', 'gujarat', 'rajasthan', 
                'punjab', 'haryana', 'uttar pradesh', 'madhya pradesh', 'west bengal', 'bihar', 
                'odisha', 'assam', 'jharkhand', 'chhattisgarh', 'goa', 'himachal pradesh', 
                'uttarakhand', 'telangana', 'andhra pradesh', 'delhi', 'new delhi', 
                'mumbai', 'bengaluru', 'bangalore', 'chennai', 'kolkata', 'hyderabad', 'pune',
                'usa', 'uk', 'europe', 'asia', 'america', 'australia', 'africa',
                'alabama', 'alaska', 'arizona', 'arkansas', 'california', 'colorado', 'connecticut',
                'delaware', 'florida', 'georgia', 'hawaii', 'idaho', 'illinois', 'indiana', 'iowa',
                'kansas', 'kentucky', 'louisiana', 'maine', 'maryland', 'massachusetts', 'michigan',
                'minnesota', 'mississippi', 'missouri', 'montana', 'nebraska', 'nevada', 'new hampshire',
                'new jersey', 'new mexico', 'new york', 'north carolina', 'north dakota', 'ohio',
                'oklahoma', 'oregon', 'pennsylvania', 'rhode island', 'south carolina', 'south dakota',
                'tennessee', 'texas', 'utah', 'vermont', 'virginia', 'washington', 'west virginia',
                'wisconsin', 'wyoming', 'england', 'scotland', 'wales', 'northern ireland'
            }
            if location_base:
                geo_headers.add(location_base.lower())
                
            if lower_text in geo_headers:
                continue
                
            # 5. Skip non-restaurant entries
            bad_words = [
                'sign up', 'newsletter', 'read more', 'advertisement', 'cookie', 
                'privacy', 'related', 'where to', 'our favorite', 
                'guide', 'subscribe', 'review', 'menu', 'share', 'facebook', 
                'twitter', 'email', 'pin', 'reddit', 'pocket', 'more maps', 'maps in',
                'sweet', 'sweets', 'kachori', 'tour', 'vendor', 'street food', 'walk', 
                'bhandar', 'dairy', 'bakery', 'market', 'puchka', 
                'phuchka', 'chaat', 'golgappa', 'jhalmuri', 'food', 'dining', 'restaurant'
            ]
            if not any(bw in lower_text for bw in bad_words):
                if not (len(lower_text.split()) > 7 and ('restaurant' in lower_text or 'place' in lower_text or 'spot' in lower_text)):
                    if not any(n.lower() in lower_text or lower_text in n.lower() for n in names):
                        names.append(clean_text)
                        
    # TripAdvisor sometimes uses specific divs or links
    if len(names) < target_n:
        for tag in soup.find_all(attrs={"data-test-target": "restaurant-title"}):
            text = tag.get_text(strip=True)
            clean_text = re.sub(r'^\d+\.\s+', '', text).strip()
            if 2 < len(clean_text) < 45 and clean_text not in names:
                names.append(clean_text)
                
        for link in soup.find_all('a'):
            href = link.get('href', '')
            if '/Restaurant_Review' in href:
                text = link.get_text(strip=True)
                if text and 2 < len(text) < 45:
                    clean_text = re.sub(r'^\d+\.\s+', '', text).strip()
                    bad_words = ['read more', 'review', 'menu', 'website', 'reserve', 'write']
                    if clean_text and not any(bw in clean_text.lower() for bw in bad_words):
                        if not any(n.lower() in clean_text.lower() or clean_text.lower() in n.lower() for n in names):
                            names.append(clean_text)
                    
    return names[:max(target_n * 2, 15)]

async def execute_duckduckgo_search(query: str) -> str:
    target_matches = re.findall(r'\b(\d+)\b', query)
    target_num = int(target_matches[0]) if target_matches else 5
    
    location_base = re.sub(r'\b(top|best|fine dining|restaurants?|in)\b', '', query.lower()).strip()
    location_base = re.sub(r'\d+', '', location_base).strip()
    location_base = re.sub(r'\s+', ' ', location_base).strip()
    if not location_base:
        location_base = "your city"
    
    trusted_sources = ['eater', 'timeout', 'cntraveler', 'culturetrip', 'wanderlog', 'tripsavvy']
    aggregator_domains = ["tripadvisor", "zomato", "swiggy", "yelp", "opentable", "justdial", "eater", "timeout", "dineout", "magicpin", "restaurantguru", "restaurant-guru", "wanderlog", "makemytrip", "agoda", "booking", "klook", "google", "maps", "instagram", "facebook", "foursquare", "lbb.in", "eazydiner", "wikipedia", "hungryfoody", "jetlygo", "directory", "menu"]
    
    browser_config = BrowserConfig(headless=True, browser_type="chromium", verbose=False)
    
    results_output = []
    
    old_stdout, old_stderr = sys.stdout, sys.stderr
    fnull = open(os.devnull, 'w', encoding='utf-8')
    sys.stdout = sys.stderr = fnull
    
    try:
        async with AsyncWebCrawler(config=browser_config) as crawler:
            try:
                # STEP 1: Search DuckDuckGo for the ranking query
                search_url_html = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
                result_html = await crawler.arun(url=search_url_html)
                
                ranking_urls = []
                if result_html.html:
                    soup_html = BeautifulSoup(result_html.html, 'html.parser')
                    links_html = soup_html.find_all('a', class_='result__snippet')
                    
                    for link in links_html:
                        href = link.get("href", "").strip()
                        if href.startswith("//duckduckgo.com/l/?uddg="):
                            actual_url = urllib.parse.unquote(href.split("uddg=")[1].split("&")[0])
                            ranking_urls.append(actual_url)
                        elif href.startswith("http") and "duckduckgo.com" not in href:
                            ranking_urls.append(href)
                
                # STEP 2: Identify Ranking Sources
                selected_ranking_url = None
                
                ignore_ranking_domains = ["tripadvisor", "zomato", "yelp", "swiggy", "justdial", "booking", "agoda", "makemytrip", "klook", "magicpin", "opentable", "restaurantguru", "restaurant-guru", "google", "dineout", "eazydiner", "holidify", "rome2rio", "ixigo", "hungryfoody", "jetlygo", "wikipedia", "shop", "directory", "menu", "thekolkatabuzz", "portal", "listing"]
                
                for url in ranking_urls:
                    if not any(agg in url.lower() for agg in ignore_ranking_domains):
                        selected_ranking_url = url
                        break
                    
                if not selected_ranking_url:
                    return "Failed to find any ranking articles for this query."
                
                # STEP 3: Extract Restaurant Names
                page_result = await crawler.arun(url=selected_ranking_url)
                if not page_result.success or not page_result.html:
                    return f"Failed to load the ranking article: {selected_ranking_url}"
                    
                extracted_names = extract_names_from_html(page_result.html, target_num, location_base)
                
                if not extracted_names:
                    return f"Could not extract restaurant names from the ranking article: {selected_ranking_url}"
                    
                # STEP 4: Find Official Websites
                fetched_count = 0
                for name in extracted_names:
                    if fetched_count >= target_num:
                        break
                        
                    # Search DuckDuckGo again for this specific restaurant
                    restaurant_query = f"{name} restaurant {location_base} official website"
                    restaurant_search_url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(restaurant_query)}"
                    
                    rest_result_html = await crawler.arun(url=restaurant_search_url)
                    if not rest_result_html.html:
                        continue
                        
                    soup_rest = BeautifulSoup(rest_result_html.html, 'html.parser')
                    rest_links = soup_rest.find_all('a', class_='result__snippet')
                    
                    official_url = None
                    for link in rest_links:
                        href = link.get("href", "").strip()
                        if href.startswith("//duckduckgo.com/l/?uddg="):
                            actual_url = urllib.parse.unquote(href.split("uddg=")[1].split("&")[0])
                        elif href.startswith("http") and "duckduckgo.com" not in href:
                            actual_url = href
                        else:
                            continue
                            
                        # Double-check translated URLs for DDG ads
                        if "duckduckgo.com/y.js" in actual_url:
                            continue
                            
                        # Ignore aggregators
                        if any(agg in actual_url.lower() for agg in aggregator_domains):
                            continue
                        if any(ext in actual_url.lower() for ext in [".pdf", "article", "blog", "list", "top-"]):
                            continue
                            
                        # Ignore hotel homepages unless specific to the restaurant
                        parsed_url = urllib.parse.urlparse(actual_url)
                        domain = parsed_url.netloc.lower()
                        path = parsed_url.path.lower()
                        is_hotel = any(h in domain for h in ['hotel', 'resort', 'leela', 'taj', 'marriott', 'hyatt', 'hilton', 'itc', 'oberoi'])
                        if is_hotel:
                            if len(path.strip('/')) < 5:
                                continue
                            name_slug = name.lower().replace(" ", "").replace("'", "")
                            if not ('dining' in path or 'restaurant' in path or name_slug[:4] in path):
                                continue
                            
                        official_url = actual_url
                        break
                        
                    if not official_url:
                        continue
                        
                    # STEP 5: Visit Website
                    try:
                        site_result = await asyncio.wait_for(crawler.arun(url=official_url), timeout=15)
                        if site_result.success and site_result.html:
                            soup_site = BeautifulSoup(site_result.html, 'html.parser')
                            text = soup_site.get_text(separator=' ', strip=True)
                            words = text.split()
                            content = ' '.join(words[:400])
                            
                            # Formatting output
                            output_block = (
                                f"Restaurant {fetched_count + 1}\n"
                                f"Name: {name}\n"
                                f"URL: {official_url}\n"
                                f"First 400 Words: {content}\n"
                            )
                            results_output.append(output_block)
                            fetched_count += 1
                    except Exception:
                        pass
                        
            except Exception as e:
                import traceback
                traceback.print_exc()
                return f"Agent execution failed: {str(e)}"
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr
        fnull.close()
            
    if not results_output:
        return "Search completed but failed to extract official websites for the extracted names."
        
    final_output = "\n\n".join(results_output).strip()
    return final_output.encode("ascii", "ignore").decode("ascii")


@tool
async def search_duckduckgo(query: str) -> str:
    """Use this tool to search for information on the internet. Use this when the user asks a question that requires external knowledge or searching."""
    return await execute_duckduckgo_search(query)
