# Web

Search the web and turn fetched pages into Markdown for agent tasks.

## Using this App

Use `duckduckgo_search` with a query and optional result/time limits to locate pages. Use `web_crawl` with known URLs to fetch their contents as Markdown. Search results and page contents are separate operations, and either can fail because of the remote site or network.

This service needs network access on its execution node and has no desktop window. Browser is the shared interactive Chromium App; Scraper is the separate ScraperAPI-backed service.

## Agent interface

Available tools: `duckduckgo_search`, `web_crawl`. See [app.json](app.json) for the declared tool contract; runtime discovery provides the current parameter schema.

## Package and source

This is a system App bundled with Pantheon. Its identity, capabilities and entry points are declared in [app.json](app.json). Updates ship with the Pantheon runtime.

Backend implementation: [__init__.py](__init__.py).
