# Five-minute quickstart

This complete example is executed by `task docs_test`:

{!> ../../../../docs_src/getting_started/quickstart.py !}

## What happened

`User(...)` is the strict Python path. Talea accepts exact Python values and
does not convert a string into an integer. The caught `ValidationError` exposes
a stable `type` code and the field path `id`; application code should consume
those structured values instead of parsing message text.

`User.from_json(...)` owns JSON decoding and schema-aware conversion. It can
therefore construct standard-library Python values from documented JSON
representations without weakening ordinary construction.

`to_dict()` returns a detached Python mapping and `to_json()` produces JSON
text. `json_schema()` returns a fresh Draft 2020-12 document.

Next, follow the [tutorial](tutorial.md). Use the [public API
reference](../reference/api.md) when you already know the operation you need.
