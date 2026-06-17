Remote references inspected via browser for the station search fix:

- https://github.com/ChronicChannel-test/uk-aq-ops/tree/main/station_snapshot_v2
- https://raw.githubusercontent.com/ChronicChannel-test/uk-aq-ops/main/station_snapshot_v2/index.html
- https://raw.githubusercontent.com/ChronicChannel-test/uk-aq-ops/main/workers/uk_aq_dashboard_online_api_worker/src/lib/station_snapshot_v2.ts

Direct filesystem download from this container failed with a network tunnel 403, so the URLs and the relevant implementation notes are archived here instead. Key search implementation details used:

- Search endpoint returns `{ results: [...] }`.
- PostgREST search uses the `uk_aq_core` schema by default.
- Stations table fields are `id`, `station_ref`, `label`, `name`, `connector_id`, with related `connectors(id,label,name)`.
- Search matches `label`, `name`, `station_ref`, and numeric `id`.
- Connector id `1` is displayed as `GOV.UK AURN`.
