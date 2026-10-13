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

// Each returns a geometry: in Update Attributes, set it as the `geom` column to replace the
// features' shapes (or as another column for a second geometry). Listed first in the
// expression builder of a row that sets `geom`.
export const GEOMETRY_SNIPPETS: ExprSnippetCategory = {
  category: 'Geometry (new shape)',
  snippets: [
    { name: 'ST_Boundary(geom)', code: 'ST_Boundary(geom)', doc: 'Polygon outline as lines (a line\'s end points)' },
    { name: 'ST_ExteriorRing(geom)', code: 'ST_ExteriorRing(geom)', doc: 'A polygon\'s outer ring as a line, without its holes' },
    { name: 'ST_BuildArea(geom)', code: 'ST_BuildArea(geom)', doc: 'Polygons from the closed rings a (multi)line forms' },
    { name: 'ST_ConvexHull(geom)', code: 'ST_ConvexHull(geom)', doc: 'Smallest convex polygon around the geometry' },
    { name: 'ST_ConcaveHull(geom, ratio, holes)', code: 'ST_ConcaveHull(geom, 0.5, false)', doc: 'Tighter hull: ratio 1 is the convex hull, lower follows the shape closer' },
    { name: 'ST_Envelope(geom)', code: 'ST_Envelope(geom)', doc: 'Bounding box as a polygon' },
    { name: 'ST_MinimumRotatedRectangle(geom)', code: 'ST_MinimumRotatedRectangle(geom)', doc: 'Smallest rectangle around the geometry, at any angle' },
    { name: 'ST_PointOnSurface(geom)', code: 'ST_PointOnSurface(geom)', doc: 'A point guaranteed to lie inside the polygon (unlike the centroid)' },
    { name: 'ST_Centroid(geom)', code: 'ST_Centroid(geom)', doc: 'Centre of mass (may fall outside a curved polygon)' },
    { name: 'ST_LineInterpolatePoint(geom, f)', code: 'ST_LineInterpolatePoint(geom, 0.5)', doc: 'The point a fraction along a line (0.5: the middle)' },
    { name: 'ST_StartPoint(geom)', code: 'ST_StartPoint(geom)', doc: 'First point of a line' },
    { name: 'ST_EndPoint(geom)', code: 'ST_EndPoint(geom)', doc: 'Last point of a line' },
    { name: 'ST_LineMerge(geom)', code: 'ST_LineMerge(geom)', doc: 'A multiline\'s touching parts joined into longer lines' },
    { name: 'ST_Reverse(geom)', code: 'ST_Reverse(geom)', doc: 'The line drawn the other way' },
    { name: 'ST_Buffer(geom, d)', code: 'ST_Buffer(geom, 10)', doc: 'Grown (or, negative, shrunk) by a distance in working_crs units' },
    { name: 'ST_Translate(geom, dx, dy)', code: 'ST_Translate(geom, 10, 0)', doc: 'Moved by dx, dy in working_crs units' },
    { name: 'ST_MakeValid(geom)', code: 'ST_MakeValid(geom)', doc: 'Repaired: self-intersections and the like fixed' },
    { name: 'ST_Force2D(geom)', code: 'ST_Force2D(geom)', doc: 'Z and M values dropped' },
    { name: 'ST_Multi(geom)', code: 'ST_Multi(geom)', doc: 'As a multi-geometry, so every row has the same type' },
    { name: 'ST_CollectionExtract(geom, 3)', code: 'ST_CollectionExtract(geom, 3)', doc: 'Only the parts of one kind: 1 points, 2 lines, 3 polygons' },
  ]
}

export const EXPR_SNIPPET_CATEGORIES: ExprSnippetCategory[] = [
  {
    category: 'Conditional',
    snippets: [
      { name: 'COALESCE(col, val)', code: 'COALESCE(column, 0)', doc: 'First non-NULL value in the list' },
      { name: 'CASE WHEN', code: 'CASE WHEN condition THEN true_val ELSE false_val END', doc: 'Branch on one or more conditions' },
      { name: 'NULLIF(a, b)', code: "NULLIF(column, '')", doc: 'NULL if the two values are equal, else the first' },
      { name: 'IFF(cond, a, b)', code: 'IFF(condition, true_val, false_val)', doc: 'Inline if/else' },
      { name: 'BETWEEN', code: 'column BETWEEN 0 AND 100', doc: 'True if a value lies in a range (inclusive)' },
      { name: 'IN (…)', code: "column IN ('a', 'b')", doc: 'True if a value is one of a list' }
    ]
  },
  {
    category: 'String',
    snippets: [
      { name: 'CONCAT(a, b)', code: "CONCAT(col1, ' ', col2)", doc: 'Join strings together' },
      { name: 'LOWER(str)', code: 'LOWER(column)', doc: 'Lowercase a string' },
      { name: 'UPPER(str)', code: 'UPPER(column)', doc: 'Uppercase a string' },
      { name: 'TRIM(str)', code: 'TRIM(column)', doc: 'Strip leading/trailing whitespace' },
      { name: 'LENGTH(str)', code: 'LENGTH(column)', doc: 'Number of characters' },
      { name: 'SUBSTRING(str, start, len)', code: 'SUBSTRING(column, 1, 3)', doc: 'Part of a string (1-based start)' },
      { name: 'REPLACE(str, from, to)', code: "REPLACE(column, 'old', 'new')", doc: 'Replace every occurrence of a text' },
      { name: 'LPAD(str, len, pad)', code: "LPAD(CAST(column AS VARCHAR), 4, '0')", doc: 'Left-pad to a fixed width, e.g. zero-padded codes' },
      { name: 'CONCAT_WS(sep, a, b)', code: "CONCAT_WS(' ', col1, col2)", doc: 'Join with a separator, skipping NULLs' },
      { name: 'CONTAINS(str, sub)', code: "CONTAINS(column, 'text')", doc: 'True if the string contains a text' },
      { name: 'STARTS_WITH(str, pre)', code: "STARTS_WITH(column, 'prefix')", doc: 'True if the string starts with a text' },
      { name: 'REGEXP_REPLACE', code: "REGEXP_REPLACE(column, '[0-9]+', '')", doc: 'Replace text matching a regex' },
      { name: 'SPLIT_PART', code: "SPLIT_PART(column, ',', 1)", doc: 'Nth field of a delimited string' },
      { name: 'REGEXP_EXTRACT', code: "REGEXP_EXTRACT(column, '([0-9]+)', 1)", doc: 'The text a regex group matched' },
      { name: 'REGEXP_MATCHES', code: "REGEXP_MATCHES(column, '^[A-Z]')", doc: 'True if the string matches a regex' }
    ]
  },
  {
    category: 'Numeric',
    snippets: [
      { name: 'ROUND(val, dec)', code: 'ROUND(column, 2)', doc: 'Round to N decimal places' },
      { name: 'LEAST(a, b, …)', code: 'LEAST(col1, col2)', doc: 'Smallest of the values (NULLs ignored)' },
      { name: 'GREATEST(a, b, …)', code: 'GREATEST(col1, col2)', doc: 'Largest of the values (NULLs ignored)' },
      { name: 'ABS(val)', code: 'ABS(column)', doc: 'Absolute value' },
      { name: 'FLOOR(val)', code: 'FLOOR(column)', doc: 'Round down to a whole number' },
      { name: 'CEIL(val)', code: 'CEIL(column)', doc: 'Round up to a whole number' },
      { name: 'CAST(val AS type)', code: 'CAST(column AS DOUBLE)', doc: 'Convert a value to another type' },
      { name: 'TRY_CAST(val AS type)', code: 'TRY_CAST(column AS DOUBLE)', doc: 'Convert, or NULL on failure' }
    ]
  },
  {
    category: 'Date',
    snippets: [
      { name: 'STRFTIME(date, fmt)', code: "STRFTIME(column, '%Y-%m-%d')", doc: 'Format a date/timestamp as text' },
      { name: 'DATE_TRUNC(part, date)', code: "DATE_TRUNC('month', column)", doc: 'Truncate a date to a unit' },
      { name: 'STRPTIME(str, fmt)', code: "STRPTIME(column, '%d.%m.%Y')", doc: 'Parse text into a timestamp' },
      { name: 'DATE_DIFF(part, a, b)', code: "DATE_DIFF('day', start_col, end_col)", doc: 'Units between two dates' },
      { name: 'EXTRACT(part FROM date)', code: 'EXTRACT(year FROM column)', doc: 'Pull one field out of a date' },
      { name: 'CURRENT_DATE', code: 'CURRENT_DATE', doc: "Today's date" }
    ]
  },
  {
    category: 'Spatial',
    snippets: [
      { name: 'ST_Area(geom)', code: 'ST_Area(geom)', doc: 'Polygon area in working_crs units' },
      { name: 'ST_Length(geom)', code: 'ST_Length(geom)', doc: 'Line length in working_crs units' },
      { name: 'ST_Perimeter(geom)', code: 'ST_Perimeter(geom)', doc: 'Polygon perimeter in working_crs units' },
      { name: 'ST_X(geom)', code: 'ST_X(ST_Centroid(geom))', doc: 'Centroid X coordinate' },
      { name: 'ST_Y(geom)', code: 'ST_Y(ST_Centroid(geom))', doc: 'Centroid Y coordinate' },
      { name: 'ST_AsGeoJSON(geom)', code: 'ST_AsGeoJSON(geom)', doc: 'Geometry as a GeoJSON string' },
      { name: 'ST_AsText(geom)', code: 'ST_AsText(geom)', doc: 'Geometry as WKT' },
      { name: 'ST_GeometryType(geom)', code: 'ST_GeometryType(geom)', doc: 'POINT, LINESTRING, POLYGON, …' },
      { name: 'ST_IsValid(geom)', code: 'ST_IsValid(geom)', doc: 'True if the geometry is valid' },
      { name: 'ST_Transform(geom, crs)', code: "ST_Transform(geom, 'EPSG:25833', 'EPSG:4326')", doc: 'Reproject a geometry' }
    ]
  },
  GEOMETRY_SNIPPETS,
  {
    category: 'List / JSON',
    snippets: [
      { name: 'list_transform', code: 'list_transform(list_column, x -> UPPER(x))', doc: 'Apply an expression to every list element' },
      { name: 'list_filter', code: 'list_filter(list_column, x -> x IS NOT NULL)', doc: 'Keep only elements matching a condition' },
      { name: 'from_json', code: `from_json(json_column, '["VARCHAR"]')`, doc: 'Parse a JSON string into a typed value' }
    ]
  }
]

// Shown (first) only where an aggregate is written in SQL: a dissolve or combine_lines step's
// aggregate columns, which compute one value per group (or line) from its rows.
export const AGGREGATE_SNIPPETS: ExprSnippetCategory = {
  category: 'Aggregate (per group)',
  snippets: [
    { name: 'count(*)', code: 'count(*)', doc: 'Number of rows in the group' },
    { name: 'sum', code: 'sum(column)', doc: 'Total of a numeric column' },
    { name: 'avg', code: 'avg(column)', doc: 'Mean of a numeric column' },
    { name: 'min', code: 'min(column)', doc: 'Smallest value' },
    { name: 'max', code: 'max(column)', doc: 'Largest value' },
    { name: 'string_agg', code: `string_agg(DISTINCT column, ', ' ORDER BY column)`, doc: 'The values joined into one string' },
    { name: 'list', code: 'list(DISTINCT column)', doc: 'The values as a list' },
    { name: 'any_value', code: 'any_value(column)', doc: 'A value from one of the rows (when they all share it)' },
    { name: 'mode', code: 'mode(column)', doc: 'The most common value' },
    { name: 'arg_max', code: 'arg_max(column, ST_Area(geom))', doc: 'The value from the row with the largest second argument (here: the largest area)' },
    { name: 'arg_max (longest)', code: 'arg_max(column, ST_Length(geom))', doc: 'The value from the longest line' },
    { name: 'bool_and', code: 'bool_and(column)', doc: 'True only if it is true on every row' },
    { name: 'bool_or', code: 'bool_or(column)', doc: 'True if it is true on any row' },
    { name: 'count(DISTINCT)', code: 'count(DISTINCT column)', doc: 'Number of different values' },
  ],
}

// Flat function-name list (upper- and lower-case forms as typically written), used by
// expr-highlight.ts to color known functions and by expr-autocomplete.ts to suggest them.
export const EXPR_FUNCTION_NAMES: string[] = Array.from(new Set(
  [...EXPR_SNIPPET_CATEGORIES, AGGREGATE_SNIPPETS].flatMap(cat => cat.snippets.map(s => {
    const m = s.name.match(/^[A-Za-z_][A-Za-z0-9_]*/)
    return m ? m[0] : s.name
  }))
))
