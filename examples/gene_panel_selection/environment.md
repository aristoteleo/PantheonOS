Environment summary update (2025-11-28)

- Python: 3.10.19 (/home/erwinpi/miniconda3/envs/gps/bin/python3)
- Pip: 25.3

Installed/updated for web tools:
- ddgs 9.9.1
- duckduckgo-search 8.1.1 (deprecated; use ddgs instead)
- crawl4ai 0.7.7
- playwright 1.56.0 (Chromium installed)

Notes:
- crawl4ai API uses AsyncWebCrawler and returns a CrawlResultContainer. Access first item via result[0]. For content, use item.html or item.markdown.raw_markdown.
- The domain tisch2.comp-genomics.org does not currently resolve (NXDOMAIN). The accessible TISCH2 site is at http://tisch.comp-genomics.org/home/.
