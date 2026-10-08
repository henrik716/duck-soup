# Mapping

The **mapping** tab defines the output schema: which columns are written, in what order, and
how each is filled. The YAML side is covered in the
[Mapping & codelists reference](../reference/mapping.md), and every value source is
explained with examples in [Mapping by example](../tutorials/mapping.md).

!!! note "No mapping = everything"
    With no mapping rows at all, every upstream column (base columns plus every pulled field) is
    written as-is. Add rows when you want to choose, rename, reorder or compute columns.

The mapping applies to every output layer of the pipeline. To give one layer different
columns, see [Per-layer mapping](output-layers.md#per-layer-mapping).

## Layout

![The mapping tab: field pool on the left, mapping grid on the right](../assets/screenshots/mapping-tab-light.png#only-light){ .screenshot loading=lazy }
![The mapping tab: field pool on the left, mapping grid on the right](../assets/screenshots/mapping-tab-dark.png#only-dark){ .screenshot loading=lazy }

On the left is the **Available Source Fields** pool: every column available after the last
step, grouped by where each column comes from (**base**, then **step 1 · spatial_join** and
so on for fields pulled in by join steps). Hover a field to see its full name and type. On the right is the mapping grid, one row
per output column:

| output column | value source | value | cast | |
|---|---|---|---|---|
| `name` | `from` | `duck_name` | | 🗑 |
| `id` | `func` | `uuid` | | 🗑 |
| `crumbs` | `from` | `bread_crumbs` | `INTEGER` | 🗑 |

## Building the mapping quickly

- **Click a field in the pool** to add a `from` row for it.
- **✨ auto-map** maps every available column in one go. It counts as one action, so a single
  <kbd>Ctrl</kbd>+<kbd>Z</kbd> undoes it.
- **+ column** adds an empty row.
- **Reorder** rows by dragging the grip handle, or with <kbd>Alt</kbd>+<kbd>↑</kbd>/<kbd>↓</kbd>.
  Row order is column order in the output.

## Value sources

| value source | value field | example |
|---|---|---|
| `from` | pick an upstream column | `duck_name` |
| `const` | type a literal | `Annual Duck Census` |
| `expr` | a SQL expression, edited in the expression builder | `upper(duck_name)` |
| `func` | pick a built-in | `uuid`, `now`, `today`, `lon`, `lat`, `mgrs`, `wkb`, `area`, `length` |
| `codelist` | opens the codelist panel | see below |

**cast** wraps the value in `TRY_CAST(… AS type)`. The list offers `INTEGER`, `DOUBLE`,
`VARCHAR`, `BOOLEAN`, `DATE` and `TIMESTAMP`, and you can type any other DuckDB type. Values
that can't be cast become NULL rather than failing the run.

## Expression builder

Choosing `expr` and clicking the edit icon opens the expression builder, a side drawer with:

- a SQL editor with syntax highlighting, auto-closing brackets/quotes and column/function
  autocomplete,
- **Available Columns**: click a column to insert it,
- **SQL Snippets**: common DuckDB functions by category (conditional, string, numeric, date, …),
  click to insert,
- a **live check** that runs the expression against real sample rows from your pipeline and
  shows either sample results or the SQL error.

![The expression builder with a live check against sample rows](../assets/screenshots/expr-drawer-light.png#only-light){ .screenshot loading=lazy }
![The expression builder with a live check against sample rows](../assets/screenshots/expr-drawer-dark.png#only-dark){ .screenshot loading=lazy }

<kbd>Ctrl</kbd>+<kbd>Enter</kbd> applies the expression and <kbd>Esc</kbd> cancels.
Multi-line expressions keep their formatting in the saved YAML.

## Codelist panel

Choosing `codelist` and clicking **configure rules** opens a panel for translating one column's
values. Set the **source column** and whether matching is **case-insensitive** (the default),
then pick a mode:

=== "rules"

    An ordered list of rules. Each rule has a type (`match` for an exact value, `like` for a SQL
    LIKE pattern such as `%oak%`, `regex`, or *is blank/null*), a pattern and an output value.
    **Rules are evaluated top to bottom and the first match wins**, so use the arrows to put
    specific rules above general ones. Add a **default** for values no rule matches (leave it
    empty for NULL).

=== "file lookup"

    Point at a CSV file and name its **key column** (matched against the source column) and
    **value column** (the output). Use this for code tables with hundreds of entries.

![The codelist panel in rules mode](../assets/screenshots/codelist-drawer-light.png#only-light){ .screenshot loading=lazy }
![The codelist panel in rules mode](../assets/screenshots/codelist-drawer-dark.png#only-dark){ .screenshot loading=lazy }

Click **Apply Rules** to save. The mapping row's button then summarises what's configured
(for example `3 rules · default Other` or `lookup: species.csv`).
