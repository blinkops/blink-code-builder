"""Blink dashboards: list -> fetch -> validate -> save -> publish.

The controller calls a dashboard an "app" and each widget on it a "resource", which is why
every route below is /apps. A dashboard is one YAML file locally: its identity (display_name,
description, icon, categories) plus its widgets, each a `type`, an `attributes` map (what the
widget shows and which table it reads) and a `display_config` (where it sits on the grid).
The keys are the controller's own names, kept verbatim — close to the solution-export format
(blink-controller components/solution/schema/app.go) — so a fetched dashboard round-trips.

Unlike a workflow or an agent, a dashboard has no draft: every save is live at once.

Backend contract (relative to {controller}/api/v1/workspace/{ws}):
  GET  /table/sys_application?q={...}  every dashboard, paged (a page holds 100 at most)
  GET  /apps/:id                       {app, resources}
  POST /apps                           create a dashboard and its widgets in one call
  PUT  /apps/:id                       metadata only, full replace — `resources` is ignored
  POST /apps/:id/resources             add one widget
  PUT  /apps/:id/resources/:rid        replace one widget, full replace — type can't change
  GET  /apps/:id/resources/:rid/data   run a widget's query. The only check that its columns
                                       exist: saving a widget never checks them
  PUT  /apps/:id/is_portal             {is_portal_app} — publish to the Blink Portal
  GET  /actions?q={...}                action collections: the icon name <-> URI lookup
Outside the workspace prefix (relative to {controller}/api/v1):
  GET  /tenant/entities?q={...}        users and groups, to resolve who to share with
  POST /workspaces/:ws/share           share a published dashboard with them

Not used, on purpose: the DELETE routes (this plugin deletes no dashboard and no widget) and
the bulk PUT /apps/:id/resources, which the controller marks internal.
"""

import copy
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from blink_shared.client import build_client, raise_for_status
from blink_shared.config import (dashboard_editor_url, require_env, workspace_base_url,
                                 workspace_root)
from .tables import DEFAULT_OUTPUT as TABLES_SCHEMA_PATH

DASHBOARDS_DIR = workspace_root() / "dashboards"
DASHBOARDS_LIST_PATH = DASHBOARDS_DIR / "dashboards-list.yaml"

# The controller caps one page of a list at 100 rows.
LIST_PAGE_SIZE = 100
# The editor's grid is 12 columns wide. Positions start at 1, sizes count grid cells.
GRID_COLUMNS = 12
# What the controller substitutes for an icon name it doesn't know.
DEFAULT_ICON = "Blink"
# AppResourceTextboxMaxSize: the cap on a Text widget's text and a Custom HTML widget's page.
MAX_CONTENT_BYTES = 5 * 1024 * 1024

# Widget (resource) types. Number and every chart are all `table_data`; they differ by
# `attributes.type` (number/chart) and `attributes.chart_type`.
TYPE_TABLE = "table"
TYPE_TABLE_DATA = "table_data"
TYPE_FILTER = "filter"
TYPE_TEXTBOX = "textbox"
TYPE_FLOW = "flow"
TYPE_HTML = "html"
WIDGET_TYPES = {TYPE_TABLE, TYPE_TABLE_DATA, TYPE_FILTER, TYPE_TEXTBOX, TYPE_FLOW, TYPE_HTML}
# The types this plugin can add. A filter or flow widget is kept and can be moved, but it
# links widgets or tables together in ways the editor wires up, so it's added there.
CREATABLE_TYPES = {TYPE_TABLE, TYPE_TABLE_DATA, TYPE_TEXTBOX, TYPE_HTML}
# Widgets with no query, so nothing to run after a save.
STATIC_TYPES = {TYPE_TEXTBOX, TYPE_HTML}

# Allowed values, mirroring blink-controller pkg/consts/apps_data_handlers.go.
DATA_KINDS = {"chart", "number"}
CHART_TYPES = {"pie", "doughnut", "line", "bar", "scatter"}
AGGREGATION_FUNCTIONS = {"count", "avg", "min", "max", "median", "sum"}
DATE_BUCKETS = {
    "minutes_of_the_day", "hour_of_the_day", "day_of_the_week", "month_of_the_year",
    "quarter_of_the_year", "hour", "day", "week", "month", "quarter", "year",
}
SORT_BY = {"x-ascending", "x-descending", "y-ascending", "y-descending"}
USER_COUNTS = {"distinct_count", "aggregated_view"}
PALETTE_NAMES = {"Blink", "Sunny", "Autumn", "Dawn", "Access"}

# Attributes the server fills in on every save. Left out of the local file; `table_id` is put
# back from the server's copy on update, since a widget PUT without it fails.
_SERVER_ATTRIBUTES = ("table_id", "table_display_name", "html_url")
# The attributes of a table_data widget that name a column of its table.
_COLUMN_ATTRIBUTES = ("column", "group_by_column", "aggregation_column")

# SharedPortalAppEntityType: the entity type a published dashboard is shared as.
PORTAL_APP_ENTITY_TYPE = "portal-app"
# The tenant entity types a dashboard can be shared with.
SHARE_TARGET_TYPES = ("user", "group")

# The dashboard UUID in an editor URL: .../applications/<uuid>/edit
_DASHBOARD_URL_RE = re.compile(r"/applications/([0-9a-fA-F-]{36})")


class _BlockStyleDumper(yaml.SafeDumper):
    """Writes multi-line strings — a Text widget's text, a Custom HTML widget's page — as `|`
    blocks, so the file stays readable and a diff shows the line that changed."""


def _represent_str(dumper, value):
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_BlockStyleDumper.add_representer(str, _represent_str)


def _dump_yaml(data):
    return yaml.dump(data, Dumper=_BlockStyleDumper, sort_keys=False, allow_unicode=True,
                     default_flow_style=False, width=100)


def _resolve_dashboard_ref(ref):
    """Accept a dashboard UUID or a dashboard-editor URL; return the UUID."""
    match = _DASHBOARD_URL_RE.search(ref or "")
    return match.group(1) if match else (ref or "").strip()


def _table_places(widget_type, attributes):
    """The attribute maps that each name one source table: the widget's own attributes, or a
    flow widget's `input` and `output`, in that order. Text and HTML widgets have none. A
    place that's missing comes back as an empty mapping, so two widgets' places line up."""
    if not isinstance(attributes, dict) or widget_type in STATIC_TYPES:
        return []
    if widget_type == TYPE_FLOW:
        return [place if isinstance(place, dict) else {}
                for place in (attributes.get("input"), attributes.get("output"))]
    return [attributes]


def _strip_server_attributes(widget_type, attributes):
    """A copy of a widget's attributes without the keys the server fills in itself."""
    stripped = copy.deepcopy(attributes or {})
    for place in [stripped] + _table_places(widget_type, stripped):
        for key in _SERVER_ATTRIBUTES:
            place.pop(key, None)
    return stripped


@dataclass
class Widget:
    """One widget. Field order is the order the keys are written to YAML.

    `id` is empty for a widget that isn't on the dashboard yet; save_dashboard adds it and
    writes the id back to the file.
    """
    id: str = ""
    type: str = ""
    attributes: Any = field(default_factory=dict)
    display_config: Any = field(default_factory=dict)

    @property
    def name(self):
        if not isinstance(self.attributes, dict):
            return ""
        return self.attributes.get("name") or ""

    @property
    def label(self):
        """How a message names this widget: its title, else its id, else its type."""
        return repr(self.name or self.id or self.type)

    @classmethod
    def from_mapping(cls, entry):
        """Build from one `resources:` entry of a dashboard file. Keeps odd values as they are,
        so _validate can report them instead of this failing on them."""
        if not isinstance(entry, dict):
            return cls(attributes=None, display_config=None)
        return cls(
            id=str(entry.get("id") or ""),
            type=entry.get("type") or "",
            attributes=entry.get("attributes") if entry.get("attributes") is not None else {},
            display_config=entry.get("display_config") or {},
        )

    @classmethod
    def from_resource(cls, resource):
        """Build from a resource as GET /apps/:id returns it."""
        widget_type = resource.get("type") or ""
        return cls(
            id=str(resource.get("id") or ""),
            type=widget_type,
            attributes=_strip_server_attributes(widget_type, resource.get("attributes")),
            display_config=resource.get("display_config") or {},
        )

    def to_body(self):
        """This widget as a request body — a POST/PUT resource, or one entry of POST /apps."""
        body = {"type": self.type, "attributes": self.attributes,
                "display_config": self.display_config}
        if self.id:
            # A filter widget's update checks its links against its own id, so send it.
            body["id"] = self.id
        return body

    def to_yaml_entry(self):
        entry = asdict(self)
        if not entry["id"]:
            del entry["id"]
        return entry


@dataclass
class Dashboard:
    """One dashboard — a dashboard YAML file, parsed. Field order is write order.

    `id` is in the file, unlike an agent's: the widgets need their ids to be updated in place,
    and with the dashboard's own id next to them, a rename in the file stays the same dashboard.
    A file without an `id` is a new dashboard.

    `icon` is a name, such as "Blink" or "Slack" — what POST/PUT /apps accept.
    """
    id: str = ""
    display_name: str = ""
    description: str = ""
    icon: str = ""
    categories: list[str] = field(default_factory=list)
    resources: list[Widget] = field(default_factory=list)

    @classmethod
    def from_yaml(cls, mapping):
        """Build from a parsed dashboard YAML file."""
        return cls(
            id=str(mapping.get("id") or ""),
            display_name=mapping.get("display_name") or "",
            description=mapping.get("description") or "",
            icon=mapping.get("icon") or "",
            # Kept as given, so _validate can report a non-list.
            categories=mapping.get("categories") or [],
            resources=[Widget.from_mapping(entry) for entry in mapping.get("resources") or []],
        )

    @classmethod
    def from_api(cls, payload, icon_names):
        """Build from a GET /apps/:id payload. `icon_names` maps icon URIs to names (see
        _icon_names); an icon it doesn't know stays a URI, which save_dashboard leaves alone."""
        app = payload.get("app") or {}
        icon_uri = app.get("icon") or ""
        return cls(
            id=str(app.get("id") or ""),
            display_name=app.get("display_name") or "",
            description=app.get("description") or "",
            icon=icon_names.get(icon_uri) or icon_uri,
            categories=list(app.get("categories") or []),
            resources=[Widget.from_resource(resource)
                       for resource in _sorted_resources(payload.get("resources"))],
        )

    def to_yaml(self):
        """Render as the text of a dashboard YAML file, empty values dropped."""
        ordered = {}
        for key, value in asdict(self).items():
            if key == "resources":
                value = [widget.to_yaml_entry() for widget in self.resources]
            if value in ("", [], None):
                continue
            ordered[key] = value
        return _dump_yaml(ordered)

    def to_metadata_body(self, icon):
        """The body PUT /apps/:id takes. It replaces all four fields, so all four are sent."""
        return {"display_name": self.display_name, "description": self.description,
                "icon": icon, "categories": self.categories}

    def to_create_body(self):
        """The body POST /apps takes: the dashboard and every widget, in one call."""
        body = self.to_metadata_body(self.icon or DEFAULT_ICON)
        body["resources"] = [{key: value for key, value in widget.to_body().items() if key != "id"}
                             for widget in self.resources]
        return body


def _sorted_resources(resources):
    """Widgets top-to-bottom, left-to-right, the way the editor shows them. GET /apps/:id
    returns them unordered, so this keeps a re-fetched file's diff about real changes."""
    def position(resource):
        config = resource.get("display_config") or {}
        place = config.get("position") or {}
        return place.get("y") or 0, place.get("x") or 0, resource.get("created_at") or 0
    return sorted(resources or [], key=position)


def _load_dashboard(path):
    """Read a dashboard YAML file into a Dashboard — the one entry point for a local file.
    Raises on a missing file, bad YAML, or a non-mapping, so callers can trust the shape."""
    file_path = Path(path)
    if not file_path.is_file():
        raise RuntimeError(f"file not found: {file_path}")
    try:
        spec = yaml.safe_load(file_path.read_text())
    except yaml.YAMLError as exc:
        raise RuntimeError(f"invalid YAML in {file_path}: {exc}")
    if not isinstance(spec, dict):
        raise RuntimeError(f"{file_path} does not contain a YAML mapping.")
    return Dashboard.from_yaml(spec)


# --------------------------------------------------------------------------
# reads
# --------------------------------------------------------------------------

def _read_dashboard(api, dashboard_id):
    """GET /apps/:id — the dashboard and all its widgets."""
    response = api.get(f"/apps/{dashboard_id}")
    if response.status_code == 404:
        raise RuntimeError(
            f"dashboard {dashboard_id!r} not found in this workspace (404). "
            "Check the id/URL and that it belongs to the configured workspace."
        )
    return raise_for_status(response).json()


def _list_dashboard_rows(api):
    """Every dashboard row in the workspace, all pages. A row is the dashboard alone, no widgets."""
    rows = []
    offset = 0
    while True:
        query = json.dumps({"limit": LIST_PAGE_SIZE, "offset": offset})
        page = raise_for_status(api.get("/table/sys_application", params={"q": query})).json()
        results = page.get("results") or []
        rows.extend(results)
        if len(results) < LIST_PAGE_SIZE:
            return rows
        offset += len(results)


def _find_dashboard(api, display_name):
    """The dashboard row with this display name, or None. Display names are unique per workspace."""
    query = json.dumps({"filter": {"display_name": display_name}})
    page = raise_for_status(api.get("/table/sys_application", params={"q": query})).json()
    for row in page.get("results") or []:
        if row.get("display_name") == display_name:
            return row
    return None


def _icon_names(api):
    """Map each icon URI to the name the controller accepts for it.

    GET /apps/:id returns a dashboard's icon as a URI, but POST/PUT /apps take an action
    collection's display name and look its URI up themselves — any other value silently
    becomes the default Blink icon. So the local file holds the name, and this is the lookup
    between the two: the same call the editor's icon picker makes.
    """
    query = json.dumps({"filter": {"search": "", "action_per_collection_limit": 1},
                        "limit": 0, "offset": 0})
    payload = raise_for_status(api.get("/actions", params={"q": query})).json()
    return {collection["icon_uri"]: collection["collection_display_name"]
            for collection in payload.get("collections") or []
            if collection.get("icon_uri") and collection.get("collection_display_name")}


# --------------------------------------------------------------------------
# list
# --------------------------------------------------------------------------

@dataclass
class WidgetSummary:
    id: str
    kind: str          # number, pie/doughnut/line/bar/scatter, table, textbox, html, filter, flow
    name: str
    table_name: str = ""


@dataclass
class DashboardSummary:
    id: str
    name: str
    display_name: str
    description: str
    categories: list[str]
    is_portal: bool
    widgets: list[WidgetSummary]


def _widget_kind(resource):
    """What a widget shows, in the editor's words: a chart type, `number`, or the widget type."""
    attributes = resource.get("attributes") or {}
    if resource.get("type") == TYPE_TABLE_DATA:
        return attributes.get("chart_type") or attributes.get("type") or TYPE_TABLE_DATA
    return resource.get("type") or ""


def _summarize(payload):
    app = payload.get("app") or {}
    widgets = []
    for resource in _sorted_resources(payload.get("resources")):
        attributes = resource.get("attributes") or {}
        widgets.append(WidgetSummary(
            id=str(resource.get("id") or ""),
            kind=_widget_kind(resource),
            name=attributes.get("name") or "",
            table_name=attributes.get("table_name") or "",
        ))
    return DashboardSummary(
        id=str(app.get("id") or ""),
        name=app.get("name") or "",
        display_name=app.get("display_name") or "",
        description=app.get("description") or "",
        categories=list(app.get("categories") or []),
        is_portal=bool(app.get("is_portal")),
        widgets=widgets,
    )


def list_dashboards(output=""):
    """Write every dashboard in the workspace, with a summary of its widgets, to a YAML file,
    overwriting it in place (default: workspace/dashboards/dashboards-list.yaml).

    Costs one call per dashboard, so like the tables schema it's written on demand and after
    every save_dashboard/publish_dashboard — never by the SessionStart hook.
    """
    with build_client() as api:
        dashboards = [_summarize(_read_dashboard(api, row["id"]))
                      for row in _list_dashboard_rows(api)]
    dashboards.sort(key=lambda dashboard: dashboard.display_name.lower())

    out_path = Path(output) if output else DASHBOARDS_LIST_PATH
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(yaml.dump(
        {"dashboards": [asdict(dashboard) for dashboard in dashboards]},
        sort_keys=False, allow_unicode=True, width=100,
    ))

    return f"ok: wrote {out_path} ({len(dashboards)} dashboards)"


def _sync_dashboards_list():
    try:
        list_dashboards()
    except Exception:
        pass  # best effort; rerun list_dashboards if the local file falls out of sync


# --------------------------------------------------------------------------
# fetch
# --------------------------------------------------------------------------

def fetch_dashboard(ref, stdout=False, output=""):
    """Download a dashboard from the workspace and write it as local YAML.

    Gets a dashboard id or a dashboard-editor URL. Returns the YAML text if `stdout`, else a
    summary of the file it wrote (`output`, or workspace/dashboards/<name>.yaml).
    """
    dashboard_id = _resolve_dashboard_ref(ref)
    with build_client() as api:
        payload = _read_dashboard(api, dashboard_id)
        dashboard = Dashboard.from_api(payload, _icon_names(api))
    yaml_text = dashboard.to_yaml()

    if stdout:
        return yaml_text

    # `name` is the server's slug of the display name — already safe as a filename.
    name = (payload.get("app") or {}).get("name") or dashboard_id
    out_path = Path(output) if output else DASHBOARDS_DIR / f"{name}.yaml"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(yaml_text)

    return (
        f"ok: wrote {out_path} (dashboard {dashboard_id}, {len(dashboard.resources)} widgets)\n"
        f"display_name: {dashboard.display_name}\n"
        f"editor: {dashboard_editor_url(dashboard_id)}\n"
        "note: save_dashboard updates this dashboard in place (its id is in the file)."
    )


# --------------------------------------------------------------------------
# validate
# --------------------------------------------------------------------------

def _load_table_columns():
    """{table name: set of column names} from workspace/tables/tables-schema.yaml, or None if
    the file is absent — a caller that can't check must say so, not report false problems."""
    try:
        spec = yaml.safe_load(TABLES_SCHEMA_PATH.read_text()) or {}
    except (OSError, yaml.YAMLError):
        return None
    return {table.get("table"): {column.get("name") for column in table.get("columns") or []}
            for table in spec.get("tables") or []}


def _is_whole_number(value, minimum):
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def _validate_display_config(widget, label, errors):
    """Check a widget's grid placement. Returns its (x, y, width, height), or None if invalid."""
    config = widget.display_config
    if not isinstance(config, dict):
        errors.append(f"{label}: `display_config` must be a mapping of width, height and position.")
        return None
    position = config.get("position") if isinstance(config.get("position"), dict) else {}
    width, height = config.get("width"), config.get("height")
    x, y = position.get("x"), position.get("y")

    problems = [f"`{key}` must be a whole number, 1 or more"
                for key, value in (("width", width), ("height", height),
                                   ("position.x", x), ("position.y", y))
                if not _is_whole_number(value, 1)]
    if problems:
        errors.append(f"{label}: " + "; ".join(problems) + " — the grid counts from 1.")
        return None
    if x + width - 1 > GRID_COLUMNS:
        errors.append(f"{label}: runs past the grid's {GRID_COLUMNS} columns "
                      f"(position.x {x} + width {width} - 1 > {GRID_COLUMNS}).")
        return None
    return x, y, width, height


def _validate_table_data(widget, label, errors):
    attributes = widget.attributes
    kind = attributes.get("type")
    if kind not in DATA_KINDS:
        errors.append(f"{label}: `attributes.type` must be `number` or `chart`, got {kind!r}.")
    if kind == "chart" and attributes.get("chart_type") not in CHART_TYPES:
        errors.append(f"{label}: `chart_type` must be one of {sorted(CHART_TYPES)}, "
                      f"got {attributes.get('chart_type')!r}.")
    if not attributes.get("column"):
        errors.append(f"{label}: `column` is empty — the column the widget counts or plots.")

    function = attributes.get("aggregation_function")
    if function not in AGGREGATION_FUNCTIONS:
        errors.append(f"{label}: `aggregation_function` must be one of "
                      f"{sorted(AGGREGATION_FUNCTIONS)}, got {function!r}.")
    elif function != "count" and not attributes.get("aggregation_column"):
        errors.append(f"{label}: `aggregation_function: {function}` needs `aggregation_column` — "
                      "the number column it applies to.")

    for key, allowed in (("date_bucket", DATE_BUCKETS), ("sort_by", SORT_BY),
                         ("user_count", USER_COUNTS), ("group_by_user_count", USER_COUNTS)):
        value = attributes.get(key)
        if value not in (None, "") and value not in allowed:
            errors.append(f"{label}: `{key}` must be one of {sorted(allowed)}, got {value!r}.")

    limit = attributes.get("limit")
    if limit not in (None, "") and not _is_whole_number(limit, 1) \
            and not (isinstance(limit, str) and limit.isdigit() and int(limit) >= 1):
        errors.append(f"{label}: `limit` must be a whole number, 1 or more, got {limit!r}.")

    palette = attributes.get("palette")
    if palette is not None:
        if not isinstance(palette, dict) or palette.get("name") not in PALETTE_NAMES:
            errors.append(f"{label}: `palette.name` must be one of {sorted(PALETTE_NAMES)} — "
                          "the controller rejects anything else, raw colors included.")
        elif any(str(color).startswith("#")
                 for color in [palette.get("color")] + list(palette.get("colors") or [])):
            errors.append(f"{label}: `palette` can't hold hex colors — pick a palette `name` "
                          "and, for a single series, a `colorIndex`.")


def _validate_columns(widget, label, table_columns, errors, warnings, unknown_tables):
    """Check that a widget's table, and each column it names, exist in tables-schema.yaml."""
    attributes = widget.attributes
    table_name = attributes.get("table_name")
    if not table_name or table_columns is None:
        return
    columns = table_columns.get(table_name)
    if columns is None:
        if table_name not in unknown_tables:
            unknown_tables.add(table_name)
            warnings.append(f"table {table_name!r} isn't in tables-schema.yaml, so its columns "
                            "weren't checked. A Blink Table? Run get_tables_schema. Case Management "
                            "tables aren't listed there.")
        return
    if widget.type != TYPE_TABLE_DATA:
        return
    for key in _COLUMN_ATTRIBUTES:
        column = attributes.get(key)
        if column and column not in columns:
            errors.append(f"{label}: `{key}: {column}` is not a column of table {table_name!r}.")


def _validate_widget(index, widget, table_columns, errors, warnings, unknown_tables):
    """Check one widget. Returns its grid rectangle, or None when it has no valid one."""
    label = f"resources[{index}] ({widget.label})"
    if widget.attributes is None:
        errors.append(f"resources[{index}]: every entry must be a mapping with `type`, "
                      "`attributes` and `display_config`.")
        return None
    if widget.type not in WIDGET_TYPES:
        errors.append(f"{label}: `type` must be one of {sorted(WIDGET_TYPES)}, got {widget.type!r}.")
        return None
    if not widget.id and widget.type not in CREATABLE_TYPES:
        errors.append(f"{label}: a new `{widget.type}` widget can't be added from here — it links "
                      "other widgets or tables together, so add it in the dashboard editor.")
    if not isinstance(widget.attributes, dict):
        errors.append(f"{label}: `attributes` must be a mapping.")
        return _validate_display_config(widget, label, errors)

    if not widget.name:
        errors.append(f"{label}: `attributes.name` is empty — it's the widget's title.")

    attributes = widget.attributes
    if widget.type in (TYPE_TABLE, TYPE_TABLE_DATA) and not attributes.get("table_name"):
        errors.append(f"{label}: `table_name` is empty — the table the widget reads.")
    if widget.type == TYPE_TABLE_DATA:
        _validate_table_data(widget, label, errors)

    for content_type, key in ((TYPE_TEXTBOX, "text"), (TYPE_HTML, "html_content")):
        if widget.type != content_type:
            continue
        content = attributes.get(key)
        if not isinstance(content, str):
            errors.append(f"{label}: `{key}` must be a string.")
        elif len(content.encode()) > MAX_CONTENT_BYTES:
            errors.append(f"{label}: `{key}` is over the controller's 5MB limit.")

    _validate_columns(widget, label, table_columns, errors, warnings, unknown_tables)
    return _validate_display_config(widget, label, errors)


def _overlaps(first, second):
    x1, y1, width1, height1 = first
    x2, y2, width2, height2 = second
    return x1 < x2 + width2 and x2 < x1 + width1 and y1 < y2 + height2 and y2 < y1 + height1


def _validate(dashboard):
    """Check a dashboard and collect every problem, without calling the API. Returns
    (errors, warnings). Errors block a save; warnings never do."""
    errors, warnings = [], []

    if not dashboard.display_name.strip():
        errors.append("`display_name` is empty — the controller rejects a dashboard without one.")
    if not isinstance(dashboard.categories, list) \
            or not all(isinstance(category, str) for category in dashboard.categories):
        errors.append("`categories` must be a list of names, e.g. [SOC, Cloud].")

    seen_ids = set()
    for widget in dashboard.resources:
        if widget.id and widget.id in seen_ids:
            errors.append(f"widget id {widget.id} is listed twice — keep one entry.")
        seen_ids.add(widget.id)

    table_columns = _load_table_columns()
    if table_columns is None and dashboard.resources:
        warnings.append("workspace/tables/tables-schema.yaml is missing, so no table or column "
                        "was checked — run get_tables_schema first.")

    unknown_tables = set()
    placed = []
    for index, widget in enumerate(dashboard.resources):
        rectangle = _validate_widget(index, widget, table_columns, errors, warnings, unknown_tables)
        if rectangle:
            placed.append((widget, rectangle))

    for position, (widget, rectangle) in enumerate(placed):
        for other, other_rectangle in placed[position + 1:]:
            if _overlaps(rectangle, other_rectangle):
                warnings.append(f"widgets {widget.label} and {other.label} overlap on the grid — "
                                "the editor pushes one of them down.")

    return errors, warnings


def validate_dashboard(path):
    """Validate a local dashboard YAML file and report the result as text.
    [ERROR] lines block save_dashboard; [WARN] lines never block."""
    dashboard = _load_dashboard(path)
    errors, warnings = _validate(dashboard)

    lines = [f"[ERROR] {msg}" for msg in errors] + [f"[WARN] {msg}" for msg in warnings]
    if errors:
        lines.append(f"\n{len(errors)} error(s) — fix before saving.")
    else:
        lines.append("[OK] dashboard is valid" + (" (with warnings)" if warnings else ""))
    new_widgets = sum(1 for widget in dashboard.resources if not widget.id)
    lines.append(f"{len(dashboard.resources)} widgets, {new_widgets} new")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# save
# --------------------------------------------------------------------------

def _blocked_exists(display_name, existing_id):
    """The [BLOCKED EXISTS] text for a display name another dashboard already has — nothing
    was written."""
    return (
        f"[BLOCKED EXISTS] a dashboard named {display_name!r} already exists in this workspace "
        f"(id {existing_id}). Nothing was written.\n"
        f"Decide with the user, then either:\n"
        f"  - editing that dashboard on purpose? fetch_dashboard it, make the change in that file, "
        f"and save that file — it carries the widget ids an update needs, or\n"
        f"  - want a separate new dashboard? change `display_name:` in the YAML to something unused."
    )


def _icon_matches(icon, icon_uri, icon_names):
    """Whether the file's icon is the one the server has: the same URI, or the URI's name."""
    return icon == icon_uri or (icon_names.get(icon_uri) or "").lower() == icon.lower()


def _icon_warnings(dashboard, icon_uri, icon_names):
    """Warn when the icon this save sends isn't one the controller knows — it would quietly
    show the default Blink icon instead."""
    if not dashboard.icon or _icon_matches(dashboard.icon, icon_uri, icon_names):
        return []
    known = {name.lower() for name in icon_names.values()}
    if dashboard.icon.lower() in known:
        return []
    return [f"icon {dashboard.icon!r} isn't an icon name Blink knows — the dashboard shows the "
            f"default {DEFAULT_ICON} icon instead. Names are action collection names, like "
            "\"Slack\" or \"AWS\"."]


def _table_ids_by_name(api):
    """{name: id} for every Blink Table — needed only when an existing widget switches table."""
    rows = raise_for_status(api.get("/tables")).json().get("results") or []
    return {row.get("name"): row.get("id") for row in rows}


def _restore_table_ids(widget, current, table_ids):
    """The widget's attributes with each `table_id` put back, as a widget PUT needs.

    The server blanks a PUT's `table_name` and re-reads the table from `table_id`, so a body
    without it fails. Unchanged table: the server's own id. New table: looked up by name among
    the Blink Tables (`table_ids`, a lazy callable). Raises if the new table isn't one.
    """
    attributes = copy.deepcopy(widget.attributes)
    for place, current_place in zip(_table_places(widget.type, attributes),
                                    _table_places(current.get("type"), current.get("attributes"))):
        table_name = place.get("table_name")
        if not table_name:
            continue
        if table_name == current_place.get("table_name") and current_place.get("table_id"):
            place["table_id"] = current_place["table_id"]
            continue
        table_id = table_ids().get(table_name)
        if not table_id:
            raise RuntimeError(
                f"widget {widget.label}: can't switch to table {table_name!r} — it isn't one of "
                "the workspace's Blink Tables. Change the source in the dashboard editor instead."
            )
        place["table_id"] = table_id
    return attributes


def _plan_widget_writes(api, dashboard, current_resources):
    """Decide every widget write before making any, so a problem stops the save with nothing
    half-written. Returns ([(method, widget, body)], unchanged count, warnings)."""
    current_by_id = {str(resource.get("id")): resource for resource in current_resources}
    cache = {}

    def table_ids():
        if "ids" not in cache:
            cache["ids"] = _table_ids_by_name(api)
        return cache["ids"]

    writes, problems = [], []
    unchanged = 0
    for widget in dashboard.resources:
        if not widget.id:
            writes.append(("post", widget, widget.to_body()))
            continue
        current = current_by_id.get(widget.id)
        if current is None:
            problems.append(f"widget {widget.label}: id {widget.id} is not on this dashboard — "
                            "removed in the editor, or copied from another dashboard. "
                            "fetch_dashboard again and redo the edit there.")
            continue
        if widget.type != current.get("type"):
            problems.append(f"widget {widget.label}: type can't change from "
                            f"{current.get('type')!r} to {widget.type!r}. Change it in the editor.")
            continue
        if widget == Widget.from_resource(current):
            unchanged += 1
            continue
        try:
            attributes = _restore_table_ids(widget, current, table_ids)
        except RuntimeError as exc:
            problems.append(str(exc))
            continue
        body = widget.to_body()
        body["attributes"] = attributes
        writes.append(("put", widget, body))

    if problems:
        raise RuntimeError("save stopped before writing anything:\n"
                           + "\n".join(f"[ERROR] {problem}" for problem in problems))

    in_file = {widget.id for widget in dashboard.resources}
    warnings = [
        f"widget {repr((resource.get('attributes') or {}).get('name') or resource.get('id'))} "
        f"(id {resource.get('id')}) is on the dashboard but not in the file — left as is. "
        "This plugin never deletes widgets; remove it in the editor if it should go."
        for resource in current_resources if str(resource.get("id")) not in in_file
    ]
    return writes, unchanged, warnings


def _update_dashboard(api, dashboard, current, icon_names, path):
    """Write the file's changes to an existing dashboard. Returns (summary, warnings).

    Sends only what differs from the server: the metadata when it changed, then one call per
    new or changed widget. Each new widget's id goes into `dashboard` as soon as it exists, so
    if a later call fails, the file written below still has it and a retry won't add it twice.
    """
    app = current.get("app") or {}
    writes, unchanged, warnings = _plan_widget_writes(api, dashboard, current.get("resources") or [])

    icon_uri = app.get("icon") or ""
    # An empty `icon:` in the file means: keep the current one.
    icon_changed = not _icon_matches(dashboard.icon or icon_uri, icon_uri, icon_names)
    metadata_changed = (
        icon_changed
        or dashboard.display_name != (app.get("display_name") or "")
        or dashboard.description != (app.get("description") or "")
        or list(dashboard.categories) != list(app.get("categories") or [])
    )
    warnings += _icon_warnings(dashboard, icon_uri, icon_names)

    done = []
    try:
        if metadata_changed:
            # PUT replaces the icon too, and only takes a name. A current icon with no known
            # name has nothing to send back, so it resets to the default — say so.
            icon = dashboard.icon if icon_changed else icon_names.get(icon_uri)
            if not icon:
                icon = DEFAULT_ICON
                warnings.append(f"the dashboard's icon has no name Blink can send back, so this "
                                f"save reset it to {DEFAULT_ICON}. Set `icon:` to a name to pick "
                                "another.")
            raise_for_status(api.put(f"/apps/{dashboard.id}", json=dashboard.to_metadata_body(icon)))
            done.append("metadata")

        for method, widget, body in writes:
            if method == "post":
                created = raise_for_status(
                    api.post(f"/apps/{dashboard.id}/resources", json=body)).json()
                widget.id = str(created.get("id") or "")
            else:
                raise_for_status(api.put(f"/apps/{dashboard.id}/resources/{widget.id}", json=body))
            done.append(f"widget {widget.label}")
    except RuntimeError as exc:
        # Half-written: keep the new widget ids in the file, and report exactly what landed.
        Path(path).write_text(dashboard.to_yaml())
        raise RuntimeError(
            f"PARTIAL SAVE of dashboard {dashboard.id}: "
            + (f"these landed: {', '.join(done)}; " if done else "nothing landed; ")
            + f"the rest did not. {path} now carries the ids of the widgets that were added, so "
            f"re-running save_dashboard finishes the job without adding them twice. "
            f"Server said: {exc}"
        ) from exc

    added = sum(1 for method, _, _ in writes if method == "post")
    changed = len(writes) - added
    summary = (f"updated: {added} widget(s) added, {changed} changed, {unchanged} unchanged"
               + (", metadata changed" if metadata_changed else ""))
    return summary, warnings


def _check_widget_data(api, dashboard_id, resources):
    """Run each widget's query once and return a warning per widget that fails.

    The only real check of a widget's columns and aggregation: saving one never checks them,
    so a broken widget otherwise shows up first as an error in the editor.
    """
    warnings = []
    for resource in resources:
        if resource.get("type") in STATIC_TYPES:
            continue
        response = api.get(f"/apps/{dashboard_id}/resources/{resource.get('id')}/data")
        if response.is_error:
            name = (resource.get("attributes") or {}).get("name") or resource.get("id")
            warnings.append(f"widget {name!r} can't load its data — HTTP {response.status_code}: "
                            f"{response.text[:300]}. Fix it in the file and save again.")
    return warnings


def save_dashboard(path):
    """Save a local dashboard YAML to the workspace — live at once, there is no draft.

    A file with an `id` updates that dashboard: only what differs is sent, and widgets missing
    from the file are left alone (never deleted). A file without one creates a dashboard,
    unless the display name is taken: then [BLOCKED EXISTS]. Validation errors raise.

    Afterwards the file is rewritten from the server, so it carries every new widget's id,
    and each widget's query is run once to catch a broken one.
    """
    dashboard = _load_dashboard(path)
    errors, warnings = _validate(dashboard)
    if errors:
        raise RuntimeError("validation failed — fix before saving:\n"
                           + "\n".join(f"[ERROR] {msg}" for msg in errors))

    with build_client() as api:
        icon_names = _icon_names(api)
        if dashboard.id:
            current = _read_dashboard(api, dashboard.id)
            summary, save_warnings = _update_dashboard(api, dashboard, current, icon_names, path)
            warnings += save_warnings
        else:
            existing = _find_dashboard(api, dashboard.display_name)
            if existing:
                return _blocked_exists(dashboard.display_name, existing.get("id"))
            warnings += _icon_warnings(dashboard, "", icon_names)
            created = raise_for_status(api.post("/apps", json=dashboard.to_create_body())).json()
            dashboard.id = str((created.get("app") or {}).get("id") or "")
            summary = f"created with {len(dashboard.resources)} widget(s)"

        saved = _read_dashboard(api, dashboard.id)
        Path(path).write_text(Dashboard.from_api(saved, icon_names).to_yaml())
        warnings += _check_widget_data(api, dashboard.id, saved.get("resources") or [])

    _sync_dashboards_list()

    lines = [f"[WARN] {msg}" for msg in warnings]
    lines.append(f"ok: saved dashboard {dashboard.id} ({summary})")
    lines.append(f"wrote: {path} (rewritten from the server, widget ids included)")
    lines.append(f"api: {workspace_base_url()}/apps/{dashboard.id}")
    lines.append(f"editor: {dashboard_editor_url(dashboard.id)}")
    lines.append("note: a dashboard has no draft — this is live now for everyone who can open it.")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# publish
# --------------------------------------------------------------------------

def _list_share_targets(api, controller):
    """Every user and group in the tenant, as GET /tenant/entities returns them — the same
    call the editor's portal-access dialog makes. Not workspace-scoped, hence the full URL."""
    query = json.dumps({"search": {"text": "", "fields": []}, "filter": {},
                        "limit": 1000, "offset": 0})
    payload = raise_for_status(api.get(f"{controller}/tenant/entities", params={"q": query})).json()
    return [entity for entity in payload.get("results") or []
            if entity.get("entity_type") in SHARE_TARGET_TYPES]


def _resolve_share_targets(entities, share_with):
    """Turn each name in `share_with` — a user's name or email, or a group's name — into one
    share target. Returns (targets, labels). Raises listing every name that matched no entity
    or several, before anything is written."""
    targets, labels, problems = [], [], []
    for wanted in share_with:
        key = (wanted or "").strip().lower()
        matches = [entity for entity in entities
                   if key and key in ((entity.get("name") or "").lower(),
                                      (entity.get("email") or "").lower())]
        if len(matches) == 1:
            entity = matches[0]
            targets.append({"id": entity.get("entity_id"), "type": entity.get("entity_type")})
            labels.append(f"{entity.get('entity_type')} {entity.get('name')}")
        elif not matches:
            problems.append(f"no user or group named {wanted!r} — check the spelling, or use the "
                            "user's email.")
        else:
            found = ", ".join(f"{entity.get('entity_type')} {entity.get('name')} "
                              f"<{entity.get('email') or entity.get('entity_id')}>"
                              for entity in matches)
            problems.append(f"{wanted!r} matches more than one: {found}. Use the email instead.")
    if problems:
        raise RuntimeError("publish stopped before writing anything:\n"
                           + "\n".join(f"[ERROR] {problem}" for problem in problems))
    return targets, labels


def publish_dashboard(dashboard, share_with):
    """Publish a dashboard to the Blink Portal and share it with users and groups.

    Gets the dashboard (id or editor URL) and the names to share with: users by name or email,
    groups by name. They will see the dashboard's data in the Portal — including the rows
    behind each chart, which they can also download — without needing access to the workspace.

    Gated by the plugin's PreToolUse hook (hooks/confirm_publish_dashboard.py): Claude Code asks
    the user before every call, so nothing here asks again.
    """
    share_with = [name for name in share_with or [] if (name or "").strip()]
    if not share_with:
        raise RuntimeError("`share_with` is empty — name at least one user (name or email) or group.")

    dashboard_id = _resolve_dashboard_ref(dashboard)
    controller, workspace, _ = require_env()
    with build_client() as api:
        app = _read_dashboard(api, dashboard_id).get("app") or {}
        targets, labels = _resolve_share_targets(_list_share_targets(api, controller), share_with)

        # Publish first: sharing a dashboard checks it is already a Portal one.
        raise_for_status(api.put(f"/apps/{dashboard_id}/is_portal", json={"is_portal_app": True}))
        try:
            raise_for_status(api.post(f"{controller}/workspaces/{workspace}/share", json={
                "entity_id": dashboard_id,
                "entity_type": PORTAL_APP_ENTITY_TYPE,
                "targets": targets,
            }))
        except RuntimeError as exc:
            raise RuntimeError(
                f"PARTIAL PUBLISH of dashboard {dashboard_id}: it IS published to the Portal, but "
                f"was NOT shared with anyone. Re-run publish_dashboard to share it. "
                f"Server said: {exc}"
            ) from exc

    _sync_dashboards_list()

    return (
        f"ok: published dashboard {dashboard_id} ({app.get('display_name')}) to the Blink Portal\n"
        f"shared with: {', '.join(labels)}\n"
        f"editor: {dashboard_editor_url(dashboard_id)}\n"
        "note: they now see its data in the Portal. To unpublish or change access, use "
        "\"Manage portal access\" in the dashboard editor."
    )
