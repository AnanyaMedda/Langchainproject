import asyncio
from test import execute_duckduckgo_search

async def main():
    res = await execute_duckduckgo_search("find  top 5 restaurants inkolkata")
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    print(res)

if __name__ == "__main__":
    asyncio.run(main())
