# Designing an HTML widget

The Blink look for HTML widgets, from the Blink design system (`blink-ui`, brand guidelines,
the product). Copy [html-starter.html](html-starter.html) and build on it: it holds every token
(colors, fonts, spacing, radius, shadow, motion), the dark and light themes, a filter bar,
tabs, clickable KPI cards, a donut, a bar list, a sortable table with severity pills, a row
detail modal, live and snapshot loading with their empty and error states, and the ready
message.

## Five rules

1. **Dark by default.** `<html data-theme="dark">`. Light (`data-theme="light"`) is only for
   print or an external audience.
2. **One brand blue.** `--accent` (`#18A0FB`) is for chrome: links, focus, primary buttons.
   Never use it as a chart series color.
3. **Numbers first.** Large tabular numbers (`font-variant-numeric: tabular-nums`), quiet
   labels around them.
4. **Quiet surfaces.** Near-black canvas, hairline borders. Less than 10% of the page in
   saturated color; color always means something.
5. **Use the tokens for anything new.** A map, a timeline, a graph: surface `--bg-surface`,
   border `--border-subtle`, radius `--radius-lg`. Never invent a grey or a hex color.

## Color

| Need | Tokens |
|---|---|
| Page, card, hover, popover | `--bg-app`, `--bg-surface`, `--bg-surface-2`, `--bg-surface-3`, `--bg-elevated` |
| Severity (the data *is* severity) | `--sev-critical`, `--sev-high`, `--sev-medium`, `--sev-low`, `--sev-info` (+ `-bg`) |
| Good / warning / bad | `--success`, `--warning`, `--danger` (+ `-bg`) |
| Chart series, tags | `--cat-1` to `--cat-8`, in order; a series keeps its color in every chart |
| Magnitude (heatmap) | 5 steps from `--bg-surface-2` to `--accent` |

Severity shows as a filled pill with **dark text** (`#16181D`) on the color, never white.

## Type

- The system font stack (`--font-sans`), and the system mono stack (`--font-mono`) for ids,
  hashes and CVEs. Web fonts are blocked in the widget.
- **12px is the floor.** Make labels different with weight, letter spacing and uppercase,
  not a smaller size.
- KPI value 32–44px / 700; card title 18px / 600; body 14px; labels 12px / 600 uppercase.

## Charts

- Draw charts with inline SVG (the starter has a donut and a bar list). A chart library from a
  CDN is blocked in the widget.
- Donut, not pie: inner radius 60–70%, the headline number in the center.
- Bars: vertical for time, horizontal for ranking. No gradients on bars.
- Axis text `--text-tertiary` 12px; gridlines `--border-subtle`, dashed.

## Layout and size

- The widget sits in a dashboard cell, not a full page. Design for the cell's width
  (`display_config.width` of 12 columns): use CSS grid with `auto-fit` / `minmax` so cards
  wrap on narrow cells.
- Cards: `--bg-surface`, `1px solid var(--border-subtle)`, `--radius-lg`, padding 16–20px.
- KPI cards have no accent border by default; the blue ring is only for hover and active.
- Hover and active states change border and shadow, never the layout.

## Filters

Every dashboard gets filters unless the user says no. They run in the page's own script, on the
loaded rows, and every chart, KPI and table reads the same filtered rows:

- **Filter bar** above the content: a search box, a dropdown per low-cardinality column
  (severity, status, source, owner — options built from the data, with counts), and a date range
  or lookback (7 / 14 / 30 / 60 days) on the main date column.
- **Clickable KPIs:** a click filters by that KPI, a second click clears it; the active KPI gets
  the blue ring.
- **Click a chart slice or bar** to filter by that value.
- Label each dropdown's first option as `<Column>: All` (for example `Severity: All`), so no
  plural is needed.
- Show the active filters as chips with one "Clear all", and "N of M rows" next to the table.
- Keep the filter state in one `state` object and re-render from it.
- Live data: when a filter narrows a large table, send it to Blink in the request's `filter`
  instead of loading every row (see [html-widgets.md](html-widgets.md)).

## Layout patterns

- Top bar: title, a one-line subtitle, and "Updated <time>" (the live load time, or the
  snapshot's `generated_at`).
- Tabs when there is more than one view, for example *Overview* (KPIs, charts) and *Records*
  (the filtered table).
- Click a table row to open its full record in an in-page modal.
- States: loading (live), empty ("no data yet" for a snapshot that hasn't run, "no rows match"
  for filters), and error per data source. Never show made-up numbers.

## Interaction

- Draw every data value with `textContent`. Where you build markup as a string, pass every
  value through `esc()` (in the starter). Never put a raw value in `innerHTML`.
- Respect `prefers-reduced-motion` (in the starter).
- No Export, Download or Print buttons: the iframe blocks them (see
  [html-widgets.md](html-widgets.md)).
- Show where each value came from (the source column, or "last run" time), so every number can
  be checked.
- Action buttons: disabled while their request runs, a clear result message after it, and an
  in-page confirm step before anything that writes.

## Before you save

1. Open the page in a browser: outside Blink it runs in sample-data mode. Check it at the
   widget's size, in every state: empty data, one row, many rows, long names, each filter.
2. Check the page sends `html-widget:ready` after the last render.
3. Check no text is under 12px and no hex color is outside the token block.
