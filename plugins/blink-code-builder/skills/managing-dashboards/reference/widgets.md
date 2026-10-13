# Dashboard file and widget reference

Referenced from [../SKILL.md](../SKILL.md). The keys are the controller's own names, so they
match what the API and the dashboard editor use.

## The file

```yaml
id: 3f2a…                    # set by save_dashboard / fetch_dashboard. Leave out for a new dashboard
display_name: SOC Overview   # unique in the workspace
description: Incidents at a glance
icon: Blink                  # an action collection name, like Slack or AWS. Default: Blink
categories: [SOC]
resources:                   # the widgets
  - id: 9c1d…                # set by the server. Leave out for a new widget
    type: table_data
    attributes:
      name: Open incidents   # the widget's title — required
      type: number
      table_name: incidents
      column: created_at
      aggregation_function: count
    display_config:
      width: 2
      height: 2
      position: {x: 1, y: 1}
```

## The grid

- 12 columns wide. `position.x` and `position.y` start at 1 (top-left).
- `width` counts columns, `height` counts rows. `x + width - 1` must be 12 or less.
- Overlapping widgets are a warning: the editor pushes one of them down.
- Editor defaults: number 2×2, chart 6×3, text 2×2, Custom HTML 6×4, table 6×3.

A common layout: a row of number widgets (`width: 3`, four across) at `y: 1`, then charts
two across (`width: 6`) below them.

## Widget types

| What the user sees | `type` | Key attributes |
|---|---|---|
| Number | `table_data` | `type: number`, `table_name`, `column`, `aggregation_function` |
| Pie, Donut, Line, Bar, Scatter chart | `table_data` | `type: chart`, `chart_type: pie / doughnut / line / bar / scatter`, `table_name`, `column`, `aggregation_function` |
| Table of records | `table` | `table_name`; optional `rql` filter (the table's default view when left out) |
| Text | `textbox` | `text` (up to 5MB) |
| Custom HTML | `html` | `html_content`: a full HTML page, `<style>` and `<script>` included (up to 5MB). Not available on on-prem installs |
| Filter | `filter` | Added in the editor only. Can be moved here |
| Flow chart | `flow` | Added in the editor only. Can be moved here |

## Number and chart attributes (`table_data`)

| Attribute | Meaning |
|---|---|
| `table_name` | The table to read: a `table` value from tables-schema.yaml |
| `column` | The main column. Charts: the X axis / the slices. Numbers: the column counted |
| `aggregation_function` | `count` (rows), or `sum` / `avg` / `min` / `max` / `median` |
| `aggregation_column` | The number column the function applies to. Required for anything but `count` |
| `group_by_column` | A second breakdown — stacked bars, multiple lines |
| `date_bucket` | For a date `column`: `hour`, `day`, `week`, `month`, `quarter`, `year`, or `hour_of_the_day`, `day_of_the_week`, `month_of_the_year`, `quarter_of_the_year`, `minutes_of_the_day` |
| `sort_by` | `x-ascending`, `x-descending`, `y-ascending`, `y-descending` |
| `limit` | Show only the top N slices/bars |
| `include_empty_values` | `true` to keep rows where `column` is empty |
| `user_count`, `group_by_user_count` | For a user column: `distinct_count` or `aggregated_view` |
| `rql` | A row filter, in the format the editor writes. Copy one from a fetched widget rather than writing it from scratch |

Display-only attributes, kept as given: `palette` (`{name: Blink | Sunny | Autumn | Dawn |
Access}` plus an optional `colorIndex` — never hex colors), `legend_position`,
`show_legend_title`, `legend_title`, `show_primary_axis_label`, `primary_axis_label`,
`show_secondary_axis_label`, `secondary_axis_label`, `format` (`""`, `percentage`,
`currency`), `symbol`.

## Examples

```yaml
- type: table_data
  attributes:
    name: Incidents by severity
    type: chart
    chart_type: doughnut
    table_name: incidents
    column: severity
    aggregation_function: count
  display_config: {width: 6, height: 3, position: {x: 1, y: 3}}

- type: table_data
  attributes:
    name: New incidents per day
    type: chart
    chart_type: line
    table_name: incidents
    column: created_at
    date_bucket: day
    sort_by: x-ascending
    aggregation_function: count
  display_config: {width: 6, height: 3, position: {x: 7, y: 3}}

- type: table_data
  attributes:
    name: Average risk score
    type: number
    table_name: incidents
    column: risk_score
    aggregation_function: avg
    aggregation_column: risk_score
  display_config: {width: 3, height: 2, position: {x: 1, y: 1}}

- type: table
  attributes:
    name: Open incidents
    table_name: incidents
  display_config: {width: 12, height: 4, position: {x: 1, y: 6}}
```

## What the server adds

`table_id`, `table_display_name` and `html_url` are filled in by the server. They're left out
of the file and handled by `save_dashboard` — never write them.
