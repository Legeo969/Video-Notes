# Gate 0 RPC Contract

Generated at: `2026-07-06T14:47:18.979621+00:00`

## Backend Registered Methods

- `collection.add_items`
- `collection.batch_process`
- `collection.create`
- `collection.delete`
- `collection.export`
- `collection.get`
- `collection.import_folder`
- `collection.list`
- `collection.list_items`
- `collection.remove_items`
- `collection.update`
- `components.install`
- `components.list`
- `components.remove`
- `components.verify`
- `diagnostics.bundle`
- `doctor.run`
- `logs.tail`
- `notes.delete`
- `notes.get`
- `notes.get_by_path`
- `notes.list`
- `notes.open`
- `notes.reveal`
- `notes.search`
- `notes.update`
- `process.cancel`
- `process.delete`
- `process.events`
- `process.events_since`
- `process.get`
- `process.list`
- `process.open_output`
- `process.pause`
- `process.permanent_clean`
- `process.resume`
- `process.retry`
- `process.start`
- `settings.bindings.set`
- `settings.get`
- `settings.models.local`
- `settings.models.scan`
- `settings.providers.add`
- `settings.providers.create`
- `settings.providers.delete`
- `settings.providers.list`
- `settings.providers.models`
- `settings.providers.remove`
- `settings.providers.set_active`
- `settings.providers.test`
- `settings.providers.update`
- `settings.secret.delete`
- `settings.secret.set`
- `settings.templates.list`
- `settings.update`
- `storage.cleanup_completed`
- `storage.cleanup_orphans`
- `storage.status`
- `system.capabilities`
- `system.info`
- `system.ping`
- `system.shutdown`
- `system.snapshot`

## Frontend Engine Calls

### `desktop/src/lib/stores/jobs.ts`
- `process.delete`
- `process.list`

### `desktop/src/pages/Collections.svelte`
- `collection.add_items`
- `collection.batch_process`
- `collection.create`
- `collection.delete`
- `collection.export`
- `collection.get`
- `collection.import_folder`
- `collection.list`
- `collection.remove_items`

### `desktop/src/pages/Notes.svelte`
- `notes.delete`
- `notes.get`
- `notes.list`
- `notes.open`
- `notes.reveal`
- `notes.search`
- `notes.update`

### `desktop/src/pages/Process.svelte`
- `process.start`
- `settings.get`

### `desktop/src/pages/Settings.svelte`
- `components.install`
- `components.list`
- `components.remove`
- `components.verify`
- `diagnostics.bundle`
- `doctor.run`
- `settings.get`
- `settings.providers.create`
- `settings.providers.delete`
- `settings.providers.list`
- `settings.providers.models`
- `settings.providers.set_active`
- `settings.providers.test`
- `settings.providers.update`
- `settings.secret.delete`
- `settings.secret.set`
- `settings.templates.list`
- `settings.update`
- `storage.cleanup_completed`
- `storage.cleanup_orphans`
- `storage.status`

## Diff

- Frontend calls missing backend handlers: `[]`
- Backend handlers not currently called by frontend: `['collection.list_items', 'collection.update', 'logs.tail', 'notes.get_by_path', 'process.cancel', 'process.events', 'process.events_since', 'process.get', 'process.open_output', 'process.pause', 'process.permanent_clean', 'process.resume', 'process.retry', 'settings.bindings.set', 'settings.models.local', 'settings.models.scan', 'settings.providers.add', 'settings.providers.remove', 'system.capabilities', 'system.info', 'system.ping', 'system.shutdown', 'system.snapshot']`
