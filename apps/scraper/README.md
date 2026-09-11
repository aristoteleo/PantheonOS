# Scraper

Search Google and fetch web pages through ScraperAPI.

## Using this App

Configure `SCRAPER_API_KEY` for the workspace before using this service. `google_search` sends a search request; `fetch_web_page` fetches requested pages through ScraperAPI. Inspect the response for fetch failures and unavailable content before using it.

This is a network backend with no desktop window. ScraperAPI usage is governed by the configured account. For direct shared-browser interaction, use Browser; for the separate DuckDuckGo/page-crawl tools, use Web.

## Agent interface

Available tools: `fetch_web_page`, `google_search`. See [app.json](app.json) for the declared tool contract; runtime discovery provides the current parameter schema.

## Package and source

This is a system App bundled with Pantheon. Its identity, capabilities and entry points are declared in [app.json](app.json). Updates ship with the Pantheon runtime.

Backend implementation: [__init__.py](__init__.py).
