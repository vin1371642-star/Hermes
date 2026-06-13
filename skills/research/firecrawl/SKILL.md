---
name: firecrawl
description: "Web scraping and crawling via Firecrawl MCP: scrape pages, crawl sites, extract structured data, map URLs."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [Firecrawl, Web Scraping, Crawling, Research, Data Extraction, MCP]
    related_skills: [research-paper-writing, blogwatcher, arxiv]
    mcp_servers: [firecrawl]
---

# Firecrawl Web Scraping & Crawling

Firecrawl is a web scraping and crawling service that converts any URL into clean, LLM-ready markdown, extracts structured data, and maps entire websites. It is available in Hermes as an MCP server providing 20 tools.

## A. Prerequisites

Firecrawl requires:
1. `FIRECRAWL_API_KEY` set in `~/.hermes/.env` (or `%APPDATA%\Local\hermes\.env` on Windows)
2. `firecrawl-mcp` npm package installed globally: `npm install -g firecrawl-mcp`
3. MCP server configured in `config.yaml` (see section D)

To get an API key: sign up at **https://www.firecrawl.dev** → Dashboard → API Keys.

## B. Available MCP Tools

When the Firecrawl MCP server is running (check with `hermes tools list | grep firecrawl`), these tools are available:

| Tool | Description |
|------|-------------|
| `firecrawl_scrape` | Scrape a single URL → clean markdown / HTML / screenshot |
| `firecrawl_crawl` | Crawl an entire website, follow links, return all pages |
| `firecrawl_map` | Get a sitemap / URL tree of a website |
| `firecrawl_search` | Search the web and return scraped results |
| `firecrawl_extract` | Extract structured data from pages using a schema |
| `firecrawl_deep_research` | Multi-step research agent over the web |
| `firecrawl_generate_llmstxt` | Generate llms.txt spec for a website |
| `firecrawl_check_crawl_status` | Poll status of an async crawl job |
| `firecrawl_cancel_crawl` | Cancel a running crawl job |
| `firecrawl_get_crawl_data` | Fetch results of a completed crawl |

## C. Common Usage Patterns

### Scrape a single page

Ask the agent:
> "Scrape https://example.com and summarize the content"

The agent will call `firecrawl_scrape` with:
```json
{
  "url": "https://example.com",
  "formats": ["markdown"]
}
```

### Crawl a site and extract all pages

> "Crawl https://docs.example.com and give me a summary of all pages"

The agent calls `firecrawl_crawl`:
```json
{
  "url": "https://docs.example.com",
  "maxDepth": 3,
  "limit": 50
}
```
This is async — the agent will poll `firecrawl_check_crawl_status` until done, then retrieve results with `firecrawl_get_crawl_data`.

### Map a website structure

> "Show me the URL structure of https://example.com"

```json
{
  "url": "https://example.com",
  "limit": 100
}
```

### Extract structured data

> "Extract all product names and prices from https://shop.example.com/products"

```json
{
  "urls": ["https://shop.example.com/products"],
  "prompt": "Extract product name and price for each item"
}
```

### Research a topic

> "Research the latest developments in quantum computing"

The agent will call `firecrawl_deep_research` which performs multi-step web searches and scraping autonomously.

## D. Account Authorization / API Key Setup

### Step 1: Get a Firecrawl API key

1. Go to **https://www.firecrawl.dev**
2. Sign up or log in
3. Navigate to **Dashboard → API Keys**
4. Create a new key and copy it

### Step 2: Add the key to Hermes

**Linux/macOS** — `~/.hermes/.env`:
```
FIRECRAWL_API_KEY=fc-your-key-here
```

**Windows** — `%LOCALAPPDATA%\hermes\.env`:
```
FIRECRAWL_API_KEY=fc-your-key-here
```

### Step 3: Configure the MCP server

Add to `config.yaml` (in the same folder as `.env`):

```yaml
mcp_servers:
  firecrawl:
    command: npx
    args:
      - -y
      - firecrawl-mcp
    env:
      FIRECRAWL_API_KEY: "${FIRECRAWL_API_KEY}"
```

### Step 4: Pre-install the npm package (recommended)

Without this, the first connection may time out (npx downloads on demand):
```bash
npm install -g firecrawl-mcp
```

### Step 5: Restart the gateway

```
hermes gateway restart
```

Then verify: `hermes tools list | grep firecrawl` — should show 20 Firecrawl tools.

## E. Troubleshooting

| Problem | Solution |
|---------|----------|
| `MCP call timed out after 40s` on first use | Run `npm install -g firecrawl-mcp` to pre-install |
| `Error: FIRECRAWL_API_KEY not set` | Check `.env` file path and that gateway was restarted after adding the key |
| Tools disappear after gateway restart | Ensure `FIRECRAWL_API_KEY` is in `.env` and `config.yaml` has the MCP server block |
| `firecrawl_crawl` returns nothing | The crawl runs async — use `firecrawl_check_crawl_status` to wait, then `firecrawl_get_crawl_data` |
| Rate limit errors | Firecrawl free tier has limits. Check your usage at firecrawl.dev/dashboard |
| Connection refused on port | `npx firecrawl-mcp` uses stdio transport, not HTTP — no port needed |

## F. Rate Limits and Costs

Firecrawl has a free tier with monthly credit limits. For heavy use:
- Check remaining credits: visit **firecrawl.dev/dashboard**
- Each scrape/crawl call costs credits proportional to pages processed
- `firecrawl_deep_research` is the most expensive operation
