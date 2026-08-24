# X50 Simulator Add-on agent guide

Standalone Home Assistant add-on repository. It is the supported 1.x
simulator/trip-journal and Ingress UI for the existing YAML HA package; it is
not the new native integration/control-center stack.

Read `README.md`, nested add-on `x50-simulator/README.md`, and the workspace
documents `../home-assistant/README.md` and
`../Yandex_navi/TRIP_ROUTE_JOURNAL.md`. Real head-unit MapKit route snapshots
come through diagnostics; do not attach AVD-only geometry to real vehicle
trips. The future projects are `../belgee-x50-ha-integration/` and
`../belgee-x50-control-center/`; keep interfaces compatible while they run in
parallel.

Validate add-on manifest/config and run any repository-local tests before
packaging. Do not put HA secrets, webhook IDs or captured private trip data in
fixtures or documentation.
