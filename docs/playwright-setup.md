# Playwright Setup

The `browser_use` capability requires the [Playwright MCP server](https://github.com/microsoft/playwright-mcp) and a vision-capable LLM. When either is absent the capability is automatically hidden at startup.

---

## Requirements

- Node.js 18+ (for `npx`)
- A vision-capable LLM configured in openDAGent (any model with `features: [vision]`)

---

## Install

### 1. Install the Playwright MCP server

No global install is needed — `npx` fetches the package on first run:

```bash
npx @playwright/mcp@latest --help
```

### 2. Install a browser

Playwright needs at least one browser binary. Chromium is the lightest option:

```bash
npx playwright install chromium
```

Install all supported browsers if you need cross-browser testing:

```bash
npx playwright install
```

---

## Configure openDAGent

Add the `playwright` server to the `mcp.servers` list in your config file:

```yaml
mcp:
  servers:
    - id: playwright
      transport: stdio
      command: npx
      args: ["@playwright/mcp@latest"]
```

> **The `id` must be exactly `playwright`** — that is the value the `browser_use`
> capability checks for in its `availability_conditions`.

### Headed mode (visible browser window)

Useful for debugging — opens a real browser window you can watch:

```yaml
    args: ["@playwright/mcp@latest", "--headed"]
```

### Custom browser

```yaml
    args: ["@playwright/mcp@latest", "--browser", "firefox"]
```

Supported values: `chromium` (default), `firefox`, `webkit`.

### Viewport size

```yaml
    args: ["@playwright/mcp@latest", "--viewport-size", "1920,1080"]
```

---

## Verify

Start openDAGent and check the startup logs. When Playwright is configured correctly you should see the `browser_use` capability registered:

```
INFO  Registered 11 capability/capabilities.
```

If `browser_use` is missing from the count, check:

1. **Vision LLM** — at least one model in `llm.providers` must declare `vision` in its `features` list.
2. **MCP server ID** — the entry in `mcp.servers` must have `id: playwright` (exact match).
3. **Node.js** — run `node --version` to confirm Node 18+ is available.
4. **Browser binary** — run `npx playwright install chromium` if not already done.

You can also open the **Capabilities** page in the web UI (`/capabilities`) — `browser_use` will appear there once all conditions are met.

---

## What the capability can do

The `browser_use` capability gives the LLM full control of a real browser:

| Action | Description |
|---|---|
| Navigate | Go to any URL |
| Screenshot | Capture the current page (sent to the vision LLM for analysis) |
| Click | Click buttons, links, and any element |
| Fill | Type into text fields |
| Select | Choose dropdown options |
| Press key | Send keyboard shortcuts (Enter, Tab, Escape, …) |
| Wait | Wait for an element or condition |
| Extract text | Pull text content from the page |
| Run JavaScript | Execute arbitrary JS in the page context |

The agent takes a screenshot after each significant action to verify the result before proceeding.

### Limitations

- The capability will stop and report rather than attempt to bypass **login walls**, **CAPTCHAs**, or **access restrictions**.
- **Form submissions and purchases** are not performed unless the task explicitly requests it.
- Sessions are not persisted between tasks — each task starts with a fresh browser context.
