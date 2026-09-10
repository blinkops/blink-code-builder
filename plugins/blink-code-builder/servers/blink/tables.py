"""Reads and writes Blink Tables: the workspace schema snapshot (`get_tables_schema`,
for the `tables` skill) and creating new tables (`create_table`).
"""

from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

import yaml

from blink_shared.client import build_client, raise_for_status

DEFAULT_OUTPUT = Path("tables") / "tables-schema.yaml"

# Mirrors backend's field attribute names
_ATTR_REQUIRED = "required"
_ATTR_UNIQUE = "is_unique"
_ATTR_TABLE_REFERENCE = "table_reference"
_ATTR_DEFAULT_VALUE = "default_value"
_ATTR_OPTIONS = "options"
_ATTR_SYSTEM = "system"
_ATTR_PROTECTED = "protected"

# The field types a user can create. Excludes the system-only types Blink adds on
# its own (sys-id, sys-time, sys-record-modifier, auto-increment-id, incident, generic-id).
VALID_FIELD_TYPES = {
    "text", "long-text", "number", "duration", "risk-level", "checkbox",
    "single-select", "multi-select", "list", "reference", "status", "user",
    "date", "button", "attachment", "sla", "vendor", "vendors",
}

_SELECT_TYPES = {"single-select", "multi-select"}


@dataclass
class ColumnSummary:
    name: str
    type: str
    required: bool = False
    unique: bool = False
    references_table: str = ""


@dataclass
class TableSchema:
    table: str
    display_name: str
    id: str
    description: str
    row_count: int
    columns: list[ColumnSummary]


def _build_column_summary(field: dict[str, Any]) -> ColumnSummary:
    """Summarize a table field's schema and rules for drafting workflow steps."""
    attributes = field.get("attributes") or {}
    reference = attributes.get(_ATTR_TABLE_REFERENCE) or {}
    return ColumnSummary(
        name=field.get("name") or "",
        type=field.get("type") or "",
        required=bool(attributes.get(_ATTR_REQUIRED)),
        unique=bool(attributes.get(_ATTR_UNIQUE)),
        references_table=reference.get("table_name") or "",
    )


def get_tables_schema(output: str = "") -> str:
    """Write every table's schema to a YAML file, overwriting it in place.

    Use `table` (not `display_name`) when writing a workflow step.
    """
    with build_client() as api:
        raw_tables: list[dict[str, Any]] = raise_for_status(api.get("/tables")).json().get("results") or []

        tables = []
        for raw_table in raw_tables:
            name = raw_table.get("name") or ""
            fields: list[dict[str, Any]] = raise_for_status(
                api.get(f"/table/{name}/fields")
            ).json().get("results") or []
            tables.append(TableSchema(
                table=name,
                display_name=raw_table.get("display_name") or "",
                id=raw_table.get("id") or "",
                description=raw_table.get("description") or "",
                row_count=raw_table.get("estimated_records_count") or 0,
                columns=[_build_column_summary(field) for field in fields],
            ))
    tables.sort(key=lambda table: table.table)

    out_path = Path(output) if output else DEFAULT_OUTPUT
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(yaml.dump(
        {"tables": [asdict(table) for table in tables]},
        sort_keys=False, allow_unicode=True, width=100,
    ))

    return f"ok: wrote {out_path} ({len(tables)} tables)"


@dataclass
class FieldSpec:
    """One column, as the agent supplies it. `name` is left out on purpose — the
    server derives it from `display_name`.
    """
    display_name: str
    type: str
    description: str = ""
    required: bool = False
    unique: bool = False
    default_value: Any = None
    options: list[str] | None = None
    references_table: str = ""

    def to_body(self) -> dict[str, Any]:
        """This field as one entry of a create_table request body's `fields` list."""
        attributes: dict[str, Any] = {}
        if self.required:
            attributes[_ATTR_REQUIRED] = True
        if self.unique:
            attributes[_ATTR_UNIQUE] = True
        if self.default_value is not None:
            attributes[_ATTR_DEFAULT_VALUE] = self.default_value
        if self.options:
            attributes[_ATTR_OPTIONS] = self.options
        if self.references_table:
            attributes[_ATTR_TABLE_REFERENCE] = {"table_name": self.references_table}

        return {
            "display_name": self.display_name,
            "description": self.description,
            "type": self.type,
            "attributes": attributes,
        }


def _field_spec_from_raw(field: dict[str, Any]) -> FieldSpec:
    """Rebuild a FieldSpec from a field as the API returns it — the merge base for an
    update, since UpdateField replaces a field's whole attribute set rather than patching it.
    """
    attributes = field.get("attributes") or {}
    reference = attributes.get(_ATTR_TABLE_REFERENCE) or {}
    return FieldSpec(
        display_name=field.get("display_name") or "",
        type=field.get("type") or "",
        description=field.get("description") or "",
        required=bool(attributes.get(_ATTR_REQUIRED)),
        unique=bool(attributes.get(_ATTR_UNIQUE)),
        default_value=attributes.get(_ATTR_DEFAULT_VALUE),
        options=attributes.get(_ATTR_OPTIONS),
        references_table=reference.get("table_name") or "",
    )


def _validate_field(spec: FieldSpec) -> list[str]:
    """Errors for one field, or an empty list if it's fine. A free function, not a method,
    because a future check (at most one `reference` field per target table) needs to see
    every field, not just this one.
    """
    errors = []
    if spec.type not in VALID_FIELD_TYPES:
        errors.append(f"field '{spec.display_name}': unknown type '{spec.type}'")
    if spec.type in _SELECT_TYPES and not spec.options:
        errors.append(f"field '{spec.display_name}': type '{spec.type}' needs options")
    if spec.type == "reference" and not spec.references_table:
        errors.append(f"field '{spec.display_name}': type 'reference' needs references_table")
    return errors


def create_table(
    display_name: str,
    fields: list[dict[str, Any]],
    description: str = "",
    with_default_fields: bool = False,
    with_default_records: bool = False,
) -> str:
    """Create a table with the given columns. Live in the workspace immediately —
    there is no draft to save first. Syncs `tables/tables-schema.yaml` on success.
    """
    specs = [FieldSpec(**field) for field in fields]

    errors = [error for spec in specs for error in _validate_field(spec)]
    if errors:
        raise RuntimeError("validation failed — fix before creating:\n" + "\n".join(errors))

    options = []
    if with_default_fields:
        options.append("with_default_fields")
    if with_default_records:
        options.append("with_default_records")

    body = {
        "display_name": display_name,
        "description": description,
        "options": options,
        "fields": [spec.to_body() for spec in specs],
    }

    with build_client() as api:
        created = raise_for_status(api.post("/table", json=body)).json()

    table = created.get("table") or {}
    try:
        get_tables_schema()
    except Exception:
        pass  # table was created; the local file just didn't sync — rerun get_tables_schema

    return f"ok: created table '{table.get('name')}' ({table.get('display_name')})"


def _fetch_table_metadata(api, table: str) -> dict[str, Any]:
    rows = raise_for_status(api.get("/tables")).json().get("results") or []
    for row in rows:
        if row.get("name") == table:
            return row
    raise RuntimeError(f"no table named '{table}'")


def _find_field(api, table: str, field_name: str) -> dict[str, Any]:
    fields = raise_for_status(api.get(f"/table/{table}/fields")).json().get("results") or []
    for field in fields:
        if field.get("name") == field_name:
            return field
    raise RuntimeError(f"no field '{field_name}' in table '{table}'")


def _check_field_removable(field: dict[str, Any], field_name: str) -> None:
    attributes = field.get("attributes") or {}
    if attributes.get(_ATTR_SYSTEM) or attributes.get(_ATTR_PROTECTED):
        raise RuntimeError(f"field '{field_name}' cannot be removed (system or protected)")


def edit_table(
    table: str,
    display_name: str = "",
    description: str = "",
    add_fields: list[dict[str, Any]] | None = None,
    update_fields: list[dict[str, Any]] | None = None,
    remove_fields: list[str] | None = None,
    acknowledge_risks: bool = False,
) -> str:
    """Change an existing table's display name/description, and/or add, update, or
    remove fields. Live immediately, same as create_table. A field's type can never
    change — only its display name, description, and value rules.

    update_fields entries need a `name` (the field's real name, not display_name) plus
    whichever of display_name/description/required/unique/options/default_value are
    changing; anything left out keeps its current value.

    Removing a field deletes its values in every existing row, with no way to undo it.
    The first call (without acknowledge_risks) returns a preview instead of making any
    change; call again with acknowledge_risks=True to actually remove.
    """
    add_fields = add_fields or []
    update_fields = update_fields or []
    remove_fields = remove_fields or []

    add_specs = [FieldSpec(**field) for field in add_fields]
    errors = [error for spec in add_specs for error in _validate_field(spec)]
    if errors:
        raise RuntimeError("validation failed — fix before editing:\n" + "\n".join(errors))

    with build_client() as api:
        row = _fetch_table_metadata(api, table)

        if remove_fields and not acknowledge_risks:
            lines = []
            for field_name in remove_fields:
                field = _find_field(api, table, field_name)
                _check_field_removable(field, field_name)
                lines.append(
                    f"remove '{field_name}' ({field.get('type')}) — deletes its values in all "
                    f"~{row.get('estimated_records_count') or 0} rows, permanently"
                )
            return (
                "[BLOCKED — irreversible]\n" + "\n".join(lines)
                + "\nCall edit_table again with acknowledge_risks=True to proceed."
            )

        if display_name or description:
            raise_for_status(api.put(f"/table/{table}", json={
                "display_name": display_name or row.get("display_name") or "",
                "description": description or row.get("description") or "",
                "settings": row.get("settings") or {},
            }))

        for spec in add_specs:
            raise_for_status(api.post(f"/table/{table}/fields", json=spec.to_body()))

        for update in update_fields:
            field_name = update.get("name") or ""
            if not field_name:
                raise RuntimeError("update_fields entry missing 'name'")
            if "type" in update:
                raise RuntimeError(f"field '{field_name}': type cannot be changed once created")

            current = _field_spec_from_raw(_find_field(api, table, field_name))
            changes = {key: value for key, value in update.items() if key != "name"}
            merged = replace(current, **changes)

            errors = _validate_field(merged)
            if errors:
                raise RuntimeError("validation failed — fix before editing:\n" + "\n".join(errors))

            raise_for_status(api.put(f"/table/{table}/fields/{field_name}", json=merged.to_body()))

        for field_name in remove_fields:
            field = _find_field(api, table, field_name)
            _check_field_removable(field, field_name)
            raise_for_status(api.delete(f"/table/{table}/fields/{field_name}"))

    try:
        get_tables_schema()
    except Exception:
        pass  # table was edited; the local file just didn't sync — rerun get_tables_schema

    return f"ok: updated table '{table}'"


def delete_table(table: str, acknowledge_risks: bool = False) -> str:
    """Delete `table` entirely — schema and all rows. Irreversible — the first call
    returns a preview with the row count; call again with acknowledge_risks=True to
    actually delete.
    """
    with build_client() as api:
        row = _fetch_table_metadata(api, table)

        if not acknowledge_risks:
            return (
                f"[BLOCKED — irreversible]\ndelete table '{table}' ({row.get('display_name')}) "
                f"— removes the schema and all ~{row.get('estimated_records_count') or 0} rows, "
                "permanently.\nCall delete_table again with acknowledge_risks=True to proceed."
            )

        raise_for_status(api.delete(f"/table/{table}"))

    try:
        get_tables_schema()
    except Exception:
        pass  # table was deleted; the local file just didn't sync — rerun get_tables_schema

    return f"ok: deleted table '{table}'"
