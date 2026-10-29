# Custom HTML widgets

A Custom HTML widget (`type: html`) shows one full HTML page, with its own `<style>` and
`<script>`, in an iframe on the dashboard. Not available on on-prem installs.

To make it look like Blink, read [html-design.md](html-design.md) and start from
[html-starter.html](html-starter.html).

## Attributes

| Attribute | Meaning |
|---|---|
| `name` | The widget title |
| `html_content` | The full page, `<!doctype html>` included. Up to 20MB |
| `connections` | Optional. Names of workspace connections the page may use for HTTP requests (below) |

## Getting data into the page

The page is static: Blink doesn't bind table data to it. Pick one way:

1. **Data in the page, refreshed by a workflow.** The page draws from a JSON constant
   (`const DATA = __DATA__;` in the starter). A workflow reads the table, replaces `__DATA__`
   with the rows as JSON, and pushes the page with `system.UpdateWidgetContent`:
   `widget_type: Custom HTML`, `app` = dashboard id, `resource` = widget id, `html_content` = the
   page. Build that workflow with the `generating-workflow` skill. Until it runs, save the widget
   with a few sample rows in place of `__DATA__`.
2. **HTTP requests from the page.** The script asks Blink to send the request, optionally with
   one of the widget's connections:

   ```js
   function blinkRequest(payload) {
     const requestId = crypto.randomUUID();
     return new Promise((resolve, reject) => {
       window.addEventListener('message', function onResult(e) {
         if (e.data?.type !== 'html-widget:execute-result' || e.data.requestId !== requestId) return;
         window.removeEventListener('message', onResult);
         e.data.success ? resolve(e.data.data) : reject(new Error(e.data.error));
       });
       parent.postMessage({ type: 'html-widget:execute', requestId, payload }, '*');
     });
   }
   // blinkRequest({ connection: 'my-jira', url: 'https://...', method: 'GET' })
   ```

   - `connection` must be listed in the widget's `connections`, or the request fails.
   - Methods: GET, POST, PUT, PATCH, DELETE. Set `Content-Type` yourself when you send a body.
   - A JSON response comes back parsed.
   - Every viewer of the dashboard can send requests with a listed connection. List one only
     when the user agrees to that.

## Tell Blink when the page is drawn

Call `parent.postMessage('html-widget:ready', '*')` when the content is fully drawn: on
`load` for a static page, or after the last async load or delayed render. Without it, a PDF
export waits 5 seconds and takes whatever is on screen.

## Limits

- `confirm()`, `alert()` and `prompt()` don't show in the iframe. Use an in-page modal.
- Never put an API key or token in the HTML. Every viewer can read the page source. Use
  `connections` instead.
- The editor canvas can show an old or blank page right after an update. Check the result in
  the dashboard's Preview or in the Portal.
