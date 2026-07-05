# Collection database lifecycle hotfix (v1.2.7)

The collection RPC helper previously called `__enter__()` on a temporary
`DatabaseGateway.connection()` context manager.  The temporary could be
finalized immediately, closing SQLite before `collection.list` used it and
causing `Cannot operate on a closed database`.

The handlers now retain the context manager for the full request with `with`,
so commit/rollback/close happen exactly once after the response is built.
A regression test covers list, create, get, list-items and delete.
