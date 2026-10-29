# Designing an HTML widget

The Blink look for HTML widgets, from the Blink design system (`blink-ui`, brand guidelines,
the product). Copy [html-starter.html](html-starter.html) and build on it: it holds every token
(colors, fonts, spacing, radius, shadow, motion), the dark and light themes, KPI cards, a
donut, a bar list, a table with severity pills, and the ready message.

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

- Inter for text, JetBrains Mono for ids, hashes and CVEs (loaded in the starter).
- **12px is the floor.** Make labels different with weight, letter spacing and uppercase,
  not a smaller size.
- KPI value 32–44px / 700; card title 18px / 600; body 14px; labels 12px / 600 uppercase.

## Charts

- Prefer inline SVG for simple charts (the starter has a donut and a bar list). A library from
  a CDN (Chart.js, D3) is fine, but style it with the tokens; never ship its default theme.
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

## Interaction

- Filters, search, sort and drill-down run in the page's own script, on `DATA`.
- Escape every value you put into HTML (the starter has `esc()`).
- Respect `prefers-reduced-motion` (in the starter).

## Before you save

1. Fill the page with sample rows and open it in a browser at the widget's size. Check every
   state: empty data, one row, many rows, long names.
2. Check the page sends `html-widget:ready`.
3. Check no text is under 12px and no hex color is outside the token block.
