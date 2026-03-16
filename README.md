@tool
async def search_duckduckgo(query: str) -> str:
    """
    Use this tool whenever a user asks for ranked lists such as:

    - "top 5 restaurants in Delhi"
    - "best 3 gaming laptops"
    - "top smartphones 2025"

    The tool searches DuckDuckGo, finds a ranking article,
    extracts item names, finds official websites, and returns
    scraped content.

    Always use this tool for ranked queries instead of answering
    from memory.
    """
