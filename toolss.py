async def execute_duckduckgo_search(query: str) -> str:
    from bs4 import BeautifulSoup
    import urllib.parse
    import re
    
    # 1. Parse target number from query (e.g., "top 5")
    target_matches = re.findall(r'\b(\d+)\b', query)
    target_num = int(target_matches[0]) if target_matches else 5
    
    # Words commonly found in aggregators or directories (must avoid these)
    blocked_patterns = [
        "tripadvisor", "wanderon", "makemytrip", "zomato", "restaurantguru", "vogue",
        "justdial", "swiggy", "yelp", "eazydiner", "dineout", "magicpin", "cntraveller",
        "lbb", "facebook", "fb.com", "instagram", "twitter", "foursquare", "so.city",
        "timeout", "eater", "trip.", "agoda", "booking", "thrillophilia", "whatshot",
        "holidify", "traveltriangle", "easemytrip", "gqindia", "lifestyleasia", "sluurpy",
        "indiatimes", "curlytales", "tasteatlas", "bhookad", "us.justdial", "nicelocal",
        "condenast", "gq", "10-best", "top-10", "top-5", "listicle", "blog", "article", "guide",
        "directory", "places", "zumvu", "top-places", "top-restaurants", "list", 
        "opentable", "wanderlog", "restaurant-guru", "justdial", "jetlygo", "tripsavvy", "travel",
        "scribd", "nearbuy"
    ]
    
    # Domains or extensions indicating non-restaurant sites
    invalid_extensions = [".pdf", ".edu", ".gov", "ac.in", "gov.in", "nic.in", ".org"]
    
    browser_config = BrowserConfig(
        headless=True,
        browser_type="chromium"
    )
    
    # Clean up the query so it's a natural search (e.g., removing "search findtop 5")
    clean_query = query.lower()
    clean_query = re.sub(r'^(search\s*for|search|find|show\s*me|give\s*me|i\s*want)\s+', '', clean_query)
    clean_query = re.sub(r'\b(top|best)\s*\d+\b', '', clean_query)
    clean_query = re.sub(r'\b\d+\s+(top|best)\b', '', clean_query)
    clean_query = re.sub(r'\b(top|best)\b', '', clean_query)
    clean_query = clean_query.strip()
    if not clean_query:
        clean_query = query
        
    location_base = re.sub(r'\brestaurants?\b', '', clean_query).strip()
    location_base = re.sub(r'\s+', ' ', location_base).strip()
    
    search_queries = [
        clean_query,
        f"{location_base} restaurant official website",
        f"{location_base} fine dining restaurant",
        f"{location_base} restaurant menu",
        f"{location_base} restaurant book table",
        f"best restaurant in {location_base} official website"
    ]
    
    results_output = []
    fetched_count = 0
    seen_urls = set()
    
    restaurant_verification_keywords = ['menu', 'reservation', 'book a table', 'cuisine', 'food', 'dining', 'chef', 'meal', 'delicious', 'taste', 'restaurant', 'order']
    
    async with AsyncWebCrawler(config=browser_config) as crawler:
        try:
            for sq in search_queries:
                if fetched_count >= target_num:
                    break
                    
                # Negative filters to push aggregators down
                negative_filters = "-tripadvisor -zomato -swiggy -yelp -opentable -lbb -wanderlog -restaurantguru"
                search_url_html = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(sq + ' ' + negative_filters)}"
                
                try:
                    urls = []
                    # Fetch multiple pages (deep pagination) to gather 30-40 links per query
                    for page_num in range(0, 3):  # Fetch up to 3 pages
                        dc = page_num * 15  # DuckDuckGo's 's' parameter is sometimes used, but 'dc' in html version
                        if page_num == 0:
                            search_url_html = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(sq + ' ' + negative_filters)}"
                        else:
                            # Try appending standard paging offsets
                            search_url_html = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(sq + ' ' + negative_filters)}&s={dc}&dc={dc}"
                            
                        result_html = await crawler.arun(url=search_url_html)
                        
                        if result_html.html:
                            soup_html = BeautifulSoup(result_html.html, 'html.parser')
                            links_html = soup_html.find_all('a', class_='result__snippet')
                            
                            for link in links_html:
                                href = link.get("href", "").strip()
                                if href.startswith("//duckduckgo.com/l/?uddg="):
                                    actual_url = urllib.parse.unquote(href.split("uddg=")[1].split("&")[0])
                                    urls.append(actual_url)
                                elif href.startswith("http") and "duckduckgo.com" not in href:
                                    urls.append(href)
                            
                        if len(urls) >= 40:
                            break
                            
                    # Remove duplicates but preserve order
                    seen_urls_in_query = set()
                    unique_query_urls = []
                    for u in urls:
                        if u not in seen_urls_in_query:
                            seen_urls_in_query.add(u)
                            unique_query_urls.append(u) 
                    # Filter non-restaurant links strictly
                    valid_urls = []
                    for u in unique_query_urls:
                        if u in seen_urls:
                            continue
                        seen_urls.add(u)
                        
                        u_lower = u.lower()
                        is_blocked = any(bp in u_lower for bp in blocked_patterns)
                        if any(ext in u_lower for ext in invalid_extensions):
                            is_blocked = True
                        if any(bad_path in u_lower for bad_path in ["top-10", "top-5", "top-20", "best", "listicles", "blogs", "articles", "news"]):
                            is_blocked = True
                        
                        if not is_blocked:
                            valid_urls.append(u)
                            
                    for url in valid_urls:
                        if fetched_count >= target_num:
                            break
                            
                        try:
                            page_result = await crawler.arun(url=url)
                            if page_result.success:
                                raw_text = ""
                                title = "Unknown Restaurant"
                                
                                if page_result.html:
                                    page_soup = BeautifulSoup(page_result.html, 'html.parser')
                                    if page_soup.title and page_soup.title.string:
                                        title = page_soup.title.string.strip()
                                        
                                    # Post-scrape title check to aggressively drop listicles disguised as real sites
                                    if re.search(r'(?i)\b(\d+\s+best|\d+\s+top|top\s+\d+|best\s+\d+|guide|directory|10|12|14|15|19|20)\b', title):
                                        continue
                                        
                                    # Extract meaningful sentences from body, prioritizing primary content blocks
                                    content_blocks = page_soup.find_all(['article', 'main'])
                                    if content_blocks:
                                        paragraphs = []
                                        for block in content_blocks:
                                            paragraphs.extend(block.find_all(['p', 'h1', 'h2', 'h3']))
                                    else:
                                        paragraphs = page_soup.find_all('p')
                                        
                                    meaningful_text = []
                                    for p in paragraphs:
                                        # Clean text
                                        p_text = p.get_text(separator=' ', strip=True)
                                        p_text = re.sub(r'\s+', ' ', p_text).strip()
                                        if len(p_text.split()) > 5:
                                            meaningful_text.append(p_text)
                                    
                                    if meaningful_text:
                                        raw_text = " ".join(meaningful_text)
                                    elif page_result.markdown:
                                        # Fallback to markdown if no meaningful p tags found
                                        text = re.sub(r'!\[.*?\]\(.*?\)', '', page_result.markdown)
                                        text = re.sub(r'\[(.*?)\]\(.*?\)', r'\1', text)
                                        text = re.sub(r'[#*`_>]+', '', text)
                                        raw_text = re.sub(r'\s+', ' ', text).strip()
                                    else:
                                        raw_text = ""
                                else:
                                    if page_result.markdown:
                                        text = re.sub(r'!\[.*?\]\(.*?\)', '', page_result.markdown)
                                        text = re.sub(r'\[(.*?)\]\(.*?\)', r'\1', text)
                                        text = re.sub(r'[#*`_>]+', '', text)
                                        raw_text = re.sub(r'\s+', ' ', text).strip()
                                    else:
                                        raw_text = ""
                                
                                words = raw_text.split()
                                # Require at least some meaningful text and verify it's arguably a restaurant
                                if len(words) > 10:
                                    raw_text_lower = raw_text.lower()
                                    is_verified_restaurant = any(kw in raw_text_lower for kw in restaurant_verification_keywords)
                                    
                                    if is_verified_restaurant:
                                        # Exact 300-400 words fallback slice as per new instructions
                                        description = " ".join(words[:350])
                                        
                                        output_block = (
                                            f"Restaurant {fetched_count + 1}\n"
                                            f"Name: {title}\n"
                                            f"Website: {url}\n"
                                            f"Description: {description}\n"
                                        )
                                        results_output.append(output_block)
                                        fetched_count += 1
                        except Exception:
                            # Timeouts or errors should be skipped per instructions
                            continue
                except Exception:
                    pass
                    
        except Exception as e:
            if not results_output:
                return f"Search failed: {str(e)}"
            
    if not results_output:
        return "Search completed but no relevant individual restaurant websites could be extracted based on the strict criteria."
        
    return "\n\n".join(results_output).strip()


@tool
async def search_duckduckgo(query: str) -> str:
    """Use this tool to search for information on the internet. Use this when the user asks a question that requires external knowledge or searching."""
    return await execute_duckduckgo_search(query)
