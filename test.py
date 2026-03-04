import urllib.parse
import asyncio
import os
import sys
from crawl4ai import AsyncWebCrawler, BrowserConfig
from bs4 import BeautifulSoup
from langchain.tools import tool

async def execute_duckduckgo_search(query: str) -> str:
    import re
    
    results_output = []
    
    # STEP 1: Extract N from query, default 5
    n_match = re.search(r'\b(?:top|best)\s+(\d+)\b', query.lower())
    target_n = int(n_match.group(1)) if n_match else 5
    
    # 1. Use crawler config without headless detection triggers
    browser_config = BrowserConfig(headless=True, browser_type="chromium", verbose=False)
    
    # Mute all stdout and stderr while crawling to prevent crawl4ai logs
    old_stdout, old_stderr = sys.stdout, sys.stderr
    fnull = open(os.devnull, 'w', encoding='utf-8')
    sys.stdout = sys.stderr = fnull
    
    try:
        async with AsyncWebCrawler(config=browser_config) as crawler:
            try:
                urls = []
                
                # STEP 2: Smart Query
                if "restaurant" in query.lower() and "kolkata" in query.lower():
                    search_query = "official website restaurant Kolkata"
                else:
                    # Fallback for generic queries
                    search_query = query
                    
                # We need to loop with offset `s` to fetch enough pages until we hit N valid restaurants
                offset = 0
                max_pages = 5 # Prevent infinite loops
                
                for attempt in range(max_pages):
                    if len(urls) >= target_n:
                        break
                        
                    # 2 & 3. Open DuckDuckGo HTML version with pagination (s parameter)
                    search_url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(search_query)}"
                    if offset > 0:
                        search_url += f"&s={offset}"
                        
                    result = await asyncio.wait_for(crawler.arun(url=search_url), timeout=15)
                    
                    if not result.html:
                        break # No more results
                        
                    soup = BeautifulSoup(result.html, 'html.parser')
                    links = soup.find_all('a', class_='result__url')
                    
                    if not links:
                        break
                        
                    for link in links:
                        href = link.get("href", "")
                        title = link.get_text(strip=True) or href
                        
                        if href:
                            real_url = None
                            if "uddg=" in href:
                                parsed_url = urllib.parse.urlparse(href)
                                query_params = urllib.parse.parse_qs(parsed_url.query)
                                if "uddg" in query_params:
                                    real_url = query_params["uddg"][0]
                            elif href.startswith("http") and "duckduckgo.com" not in href:
                                real_url = href
                                
                            if real_url:
                                lower_url = real_url.lower()
                                
                                # STEP 3: Strict Exclusion Filter
                                bad_keywords = [
                                    "duckduckgo.com", "y.js", "ad_domain", "bing.com/aclick",
                                    "blog", "best", "top", "list", "article",
                                    "wanderon", "thetoptours", "makemytrip", "restaurant-guru",
                                    "tripadvisor", "justdial", "zomato", "swiggy", "dineout", 
                                    "eazydiner", "food delivery", "lbb.in", "magicpin", "whatshot",
                                    "facebook.com", "instagram.com", "twitter.com", "wikipedia.org",
                                    "youtube.com", "pinterest", "tiktok"
                                ]
                                
                                is_bad = any(kw in lower_url for kw in bad_keywords)
                                
                                if not is_bad and not any(u == real_url for u, t in urls):
                                    urls.append((real_url, title))
                                    
                                if len(urls) >= target_n:
                                    break
                                    
                    offset += 30 # DuckDuckGo standard HTML pagination offset
                    
                if not urls:
                    return f"Search completed but no relevant individual restaurant URLs could be extracted. Aggregators dominated."
                    
                # STEP 5 & 6: Visit each URL and extract content
                for i, (url, title) in enumerate(urls[:target_n], 1):
                    try:
                        page_result = await asyncio.wait_for(crawler.arun(url=url), timeout=20)
                        if page_result.success:
                            content = ""
                            page_title = title
                            
                            if page_result.html:
                                page_soup = BeautifulSoup(page_result.html, 'html.parser')
                                
                                # Extract Name
                                h1_tags = page_soup.find_all('h1')
                                valid_h1 = None
                                # Find first non-empty H1 that doesn't just say "Welcome"
                                for h1 in h1_tags:
                                    text_content = h1.get_text(separator=" ", strip=True)
                                    if len(text_content) > 3 and "welcome" not in text_content.lower():
                                        valid_h1 = text_content
                                        break
                                
                                # Priority: og:title -> Title -> Valid H1 -> Original Search Link Title
                                og_title = page_soup.find("meta", property="og:title")
                                if og_title and og_title.get("content"):
                                    extracted_name = og_title["content"].split("|")[0].split("-")[0].strip()
                                elif page_soup.title and page_soup.title.string:
                                    extracted_name = page_soup.title.string.split("|")[0].split("-")[0].strip()
                                elif valid_h1:
                                    extracted_name = valid_h1
                                else:
                                    extracted_name = title
                                    
                                page_title = extracted_name or title
                                    
                                # STEP 6: First 400 words ONLY
                                text = page_soup.get_text(separator=' ', strip=True)
                                words = text.split()
                                content = ' '.join(words[:400])
                            elif page_result.markdown:
                                words = page_result.markdown.split()
                                content = ' '.join(words[:400])
                                
                            # STEP 8: Exact output format
                            results_output.append(f"Restaurant {i}:\nName: {page_title}\nURL: {url}\nFirst 400 Words: {content}\n")
                        else:
                            results_output.append(f"Restaurant {i}:\nName: {title}\nURL: {url}\nFirst 400 Words: Failed to fetch ({page_result.error_message})\n")
                    except asyncio.TimeoutError:
                        results_output.append(f"Restaurant {i}:\nName: {title}\nURL: {url}\nFirst 400 Words: Failed to fetch (Request timed out)\n")
                    except Exception as e:
                        results_output.append(f"Restaurant {i}:\nName: {title}\nURL: {url}\nFirst 400 Words: Failed to fetch ({str(e)})\n")
                        
            except asyncio.TimeoutError:
                return f"Search failed: Request timed out"
            except Exception as e:
                return f"Search failed: {str(e)}"
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr
        fnull.close()
            
    return "\n".join(results_output)


@tool
async def search_duckduckgo(query: str) -> str:
    """Use this tool to search for information on the internet. Use this when the user asks a question that requires external knowledge or searching."""
    return await execute_duckduckgo_search(query)
