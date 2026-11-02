# Custom HTML widgets

A Custom HTML widget (`type: html`) shows one full HTML page in an iframe on the dashboard and in
the Portal. Not available on on-prem installs.

To make it look like Blink, read [html-design.md](html-design.md) and start from
[html-starter.html](html-starter.html).

## Attributes

| Attribute | Meaning |
|---|---|
| `name` | The widget title |
| `html_content` | The full page, `<!doctype html>` included. Up to 20MB |
| `connections` | The connections the page may send requests with. Use one Blink connection (below) |

## How a live dashboard works

Three pieces, all in the workspace:

1. **Tables** hold the current answer. Their columns are the contract between the workflows and
   the page.
2. **Workflows** do the work. A scheduled workflow reads the vendors (with the vendor
   connections, on its steps) and writes the tables. Small action workflows change one row when
   a viewer clicks a button.
3. **The page** reads the tables and runs workflows through **one Blink connection**. It never
   calls a vendor, and it never holds a key.

So vendor data reaches the page through a workflow and a table, not through a vendor connection
on the widget. If a table or workflow the page needs isn't there, ask the user first; build it
only when they agree (`managing-tables`, `generating-workflow`).

| Table column needs | Why |
|---|---|
| A unique key column (`asset_key`, `signal_key`) | Workflows update rows in place with Create or Update Records |
| Single-select for anything the page counts or filters | Clean counts and filter options |
| A run summary row (for example `signal_key = run|latest`) | The page's "last run" line |

## The Blink connection

- Type `blink`, API Key. Its API address must be the tenant address the page calls. Use a key
  of a service user whose role can only read these tables and run these workflows, never a
  personal admin key: every viewer of the dashboard can do what the key can do.
- Pick it from `workspace/connections/connections.tsv` (`type_name` `blink`). If there is none,
  stop and ask the user to create one.
- Before you save, tell the user which connection the widget uses and what every viewer can do
  with it.
- Runs started from the page show as Manual runs by the connection's user, not by the viewer.

## Requests from the page

Put the API host, workspace id, connection name, table names and workflow ids in one `CONFIG`
object at the top of the script. The API host is the user's Blink URL from the session start
(never a guessed domain: each region has its own). It must be the same as the Blink
connection's API address. Send every request through the bridge:

```js
function blinkRequest(payload) {   // payload: { connection, url, method, body }
  const requestId = crypto.randomUUID();
  return new Promise((resolve, reject) => {
    window.addEventListener('message', function onResult(e) {   // listen before you send
      if (e.source !== window.parent || e.data?.type !== 'html-widget:execute-result' || e.data.requestId !== requestId) return;
      window.removeEventListener('message', onResult);
      e.data.success ? resolve(e.data.data) : reject(new Error(e.data.error));
    });
    parent.postMessage({ type: 'html-widget:execute', requestId, payload }, '*');
  });
}
```

| To | Request | Notes |
|---|---|---|
| Read a table | `GET <apiHost>/api/v1/workspace/<ws>/table/<table>?user_table=true&q=<query>` | `<table>` is the table's SQL name (`table` in `tables-schema.yaml`). `<query>` is URL-encoded JSON, `{"limit":100,"offset":0}`. The answer is `{results, total}`. Without a limit you get 25 rows: page with `offset` until you have `total` |
| Run a short workflow | `POST .../workspace/<ws>/playbooks/<id>/execute?run_sync=true&timeout=45`, body `{"input_values": {...}}` | Must finish well under 60 seconds. Every output comes back as a string: parse numbers and JSON |
| Run a long workflow | `POST .../playbooks/<id>/execute` → `execution_id`; poll `GET .../executions/<id>` every 10 seconds until `state` is `Completed` or `Failed`; then `GET .../executions/<id>/outputs` | For a hunt or a refresh |

- `body` is a JSON **string** (`JSON.stringify(...)`), not an object.
- Multi-select workflow inputs are comma strings (`"Critical,High"`), never arrays.
- `connection` is the connection's **name** as `connections.tsv` shows it (the editor's tooltip
  name), not its display name.
- A request has about 60 seconds. On "failed to get response in time" the run still goes on: never
  re-send a request that writes.
- Disable a button while its request runs. Action workflows must be safe to click twice: update
  the same row, and check before you create (for example an existing case).
- Requests run only in the dashboard's **Preview** (and the Portal), never in Edit Mode.

## Sample-data mode

When the page is opened outside Blink (`window.parent === window`), answer every request from
invented rows that match the table schema. Build and check the page in a browser this way,
before it touches Blink. Never put real rows in the page or in a sample.

## Tell Blink when the page is drawn

After the first draw, call `parent.postMessage('html-widget:ready', '*')`. Blink's dashboard PDF
export waits for it; without it, the export waits 5 seconds.

## What the widget blocks

Blink adds a content security policy to every HTML widget, and the iframe is sandboxed:

- **External scripts, stylesheets and web fonts don't load** (`script-src` and `style-src` are
  inline only). Inline all CSS and JavaScript. Use the system font stack. No chart library from a
  CDN: draw charts with inline SVG. Images from `https:` or `data:` URLs work.
- `confirm()`, `alert()`, `prompt()` and `print()` do nothing. Use an in-page modal.
- File downloads are blocked. Don't add Export or Print buttons. For a PDF, point the user to
  the dashboard's own export; for a CSV, use a workflow.
- Links and `window.open` work.

## Snapshot, when there is no connection

If the user can't add a Blink connection, a workflow can push the whole page instead:
`system.UpdateWidgetContent` with `widget_type: Custom HTML`, `app` = dashboard id, `resource` =
widget id, `html_content` = the page with the rows in its `<script id="data">` block. The data is
only as fresh as the last run, and the whole page must stay under 20MB.

## Common errors

| What you see | Why | Fix |
|---|---|---|
| Nothing happens | Requests don't run in Edit Mode | Switch to Preview |
| "Connection is not selected for HTML widget" | The connection isn't in `connections`, or the page uses its display name | Add it; use the name from `connections.tsv` |
| "does not match the connection allowed URLs `<url>`" | The page calls a host other than the connection's API address | Make them the same: set `CONFIG.apiHost` to the user's Blink URL, and ask the user to fix the connection's API address if it differs |
| "failed to get response in time" | Over about 60 seconds; the run goes on | Start long workflows and poll them |
| Styles, fonts or charts missing | External files are blocked | Inline everything |
| Only 25 rows | No `limit` in the query | `limit` 100 and page with `offset` |
| A number shows as text | Workflow outputs are strings | Parse them |
| A workflow input is refused | A multi-select was sent as an array | Send a comma string |

Errors inside the page never reach your tool output. After the save, ask the user to open
Preview and tell you what they see.
