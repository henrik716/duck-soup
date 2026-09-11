// Categorized DuckDB SQL snippets shown in the Expression Builder's sidebar. Function
// names listed here also feed expr-highlight.ts's keyword/function coloring, so the two
// stay in sync — add a function here to get both a snippet entry and syntax highlighting.
export interface ExprSnippet {
  name: string
  code: string
  doc: string
}

export interface ExprSnippetCategory {
  category: string
  snippets: ExprSnippet[]
}

export const EXPR_SNIPPET_CATEGORIES: ExprSnippetCategory[] = [
  {
    category: 'Conditional',
    snippets: [
      { name: 'COALESCE(col, val)', code: 'COALESCE(column, 0)', doc: 'First non-NULL value in the list' },
      { name: 'CASE WHEN', code: 'CASE WHEN condition THEN true_val ELSE false_val END', doc: 'Branch on one or more conditions' },
      { name: 'NULLIF(a, b)', code: "NULLIF(column, '')", doc: 'NULL if the two values are equal, else the first' },
      { name: 'IFF(cond, a, b)', code: 'IFF(condition, true_val, false_val)', doc: 'Inline if/else' }
    ]
  },
  {
    category: 'String',
    snippets: [
      { name: 'CONCAT(a, b)', code: "CONCAT(col1, ' ', col2)", doc: 'Join strings together' },
      { name: 'LOWER(str)', code: 'LOWER(column)', doc: 'Lowercase a string' },
      { name: 'UPPER(str)', code: 'UPPER(column)', doc: 'Uppercase a string' },
      { name: 'TRIM(str)', code: 'TRIM(column)', doc: 'Strip leading/trailing whitespace' },
      { name: 'REGEXP_REPLACE', code: "REGEXP_REPLACE(column, '[0-9]+', '')", doc: 'Replace text matching a regex' },
      { name: 'SPLIT_PART', code: "SPLIT_PART(column, ',', 1)", doc: 'Nth field of a delimited string' }
    ]
  },
  {
    category: 'Numeric',
    snippets: [
      { name: 'ROUND(val, dec)', code: 'ROUND(column, 2)', doc: 'Round to N decimal places' },
      { name: 'CAST(val AS type)', code: 'CAST(column AS DOUBLE)', doc: 'Convert a value to another type' },
      { name: 'TRY_CAST(val AS type)', code: 'TRY_CAST(column AS DOUBLE)', doc: 'Convert, or NULL on failure' }
    ]
  },
  {
    category: 'Date',
    snippets: [
      { name: 'STRFTIME(date, fmt)', code: "STRFTIME(column, '%Y-%m-%d')", doc: 'Format a date/timestamp as text' },
      { name: 'DATE_TRUNC(part, date)', code: "DATE_TRUNC('month', column)", doc: 'Truncate a date to a unit' },
      { name: 'EXTRACT(part FROM date)', code: 'EXTRACT(year FROM column)', doc: 'Pull one field out of a date' },
      { name: 'CURRENT_DATE', code: 'CURRENT_DATE', doc: "Today's date" }
    ]
  },
  {
    category: 'Spatial',
    snippets: [
      { name: 'ST_Area(geom)', code: 'ST_Area(geom)', doc: 'Polygon area in working_crs units' },
      { name: 'ST_Length(geom)', code: 'ST_Length(geom)', doc: 'Line length in working_crs units' },
      { name: 'ST_X(geom)', code: 'ST_X(ST_Centroid(geom))', doc: 'Centroid X coordinate' },
      { name: 'ST_Y(geom)', code: 'ST_Y(ST_Centroid(geom))', doc: 'Centroid Y coordinate' },
      { name: 'ST_AsGeoJSON(geom)', code: 'ST_AsGeoJSON(geom)', doc: 'Geometry as a GeoJSON string' },
      { name: 'ST_Transform(geom, crs)', code: "ST_Transform(geom, 'EPSG:25833', 'EPSG:4326')", doc: 'Reproject a geometry' }
    ]
  },
  {
    category: 'List / JSON',
    snippets: [
      { name: 'list_transform', code: 'list_transform(list_column, x -> UPPER(x))', doc: 'Apply an expression to every list element' },
      { name: 'list_filter', code: 'list_filter(list_column, x -> x IS NOT NULL)', doc: 'Keep only elements matching a condition' },
      { name: 'from_json', code: `from_json(json_column, '["VARCHAR"]')`, doc: 'Parse a JSON string into a typed value' }
    ]
  }
]

// Flat function-name list (upper- and lower-case forms as typically written), used by
// expr-highlight.ts to color known functions and by expr-autocomplete.ts to suggest them.
export const EXPR_FUNCTION_NAMES: string[] = Array.from(new Set(
  EXPR_SNIPPET_CATEGORIES.flatMap(cat => cat.snippets.map(s => {
    const m = s.name.match(/^[A-Za-z_][A-Za-z0-9_]*/)
    return m ? m[0] : s.name
  }))
))
