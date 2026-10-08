# Changelog

## 1.11.23

- Assemble linked Navigation archive parts into one trip, including reverse-order uploads and HA synchronization.
- Validate part identities, ordering and predecessor links; retain original physical archives.
- Read compact checkpoint/pose-delta columns while preserving older journal formats.

## 1.11.22

- Tension the reference rope against locked nodes instead of rejecting cursor movements outside its reach. Adjacent nodes slide along a fixed-radius circle; two locks constrain the point from both sides.
- Construct taut and folded chains geometrically when iterative fitting stalls, retaining fixed anchors and measured link lengths. Continue each drag from the preceding position for smoother deformation.
- Cover full-circle drags, distant cursor positions, two tangent anchors, long taut chains, zero-length samples and unequal-link minimum reach.

## 1.11.21

- Make the reference editor toolbar compact and collapsible; keep save, undo and exit available when collapsed.
- Move range tools, fragment operations, export and help into closed expandable sections. Limit panel height and scroll its contents on small screens.

## 1.11.20

- Keep the original inertial trajectory visible while editing; add time-aligned dashed links to original inertial nodes alongside GPS and FakeGPS.
- Save node locks and preserve their exact positions during rope edits. Solve free links between locks with FABRIK; reject impossible movements without partial changes.
- Add manual breaks, inclusive range removal and range restoration. Preserve excluded measurements and original evidence; do not connect the reference across removed sections.
- Display the saved reference as a magenta trajectory when viewing a trip, including after exiting the editor. Read saved references without generating a draft for every trip view.
- Add Esri World Imagery satellite basemap with attribution and persistent source selection.

## 1.11.19

- Create and explicitly save a per-trip manual reference trajectory from recorded inertial nodes; resume editing after restarting the add-on. Original trip data and sensor evidence remain immutable.
- Drag nodes with a length-preserving rope inside each fragment, inspect time-aligned GPS/FakeGPS links and sensor consistency colours, undo edits and align fragment boundaries without counting their gap as traveled distance.
- Export the reference with wall times, provenance and measurements for offline model evaluation. Guard unsaved edits and conflicting saves from multiple tabs.
- Render reference nodes on a separate canvas pane and disable map GPS commands while editing.

## 1.11.18

- Serve `basemaps.js` through the add-on's static-file allowlist, fixing the 404/MIME error and `X50Basemaps is not defined` in HA Ingress.
- Check every local script and stylesheet referenced by the HTML through the actual Simulator HTTP handler, including version query strings and MIME types.

## 1.11.17

- Choose OpenStreetMap, OSM France HOT, or CARTO Voyager/Light/Dark in settings; default to OpenStreetMap instead of relying on one CARTO endpoint.
- Remember the selected map source in the browser and switch only the basemap, preserving routes, trajectories and viewport.
- Restore visible provider attribution and show tile-loading errors with a prompt to choose another source.
- Refresh JavaScript/CSS cache versions so updated controls load after upgrading the add-on.

## 1.11.16

- Import completed uploads directly from disk rather than loading the entire compressed archive into RAM.
- Retain the original gzip before parsing so a process failure does not lose the fully uploaded file.
- Keep the same trip measurements as live recording without duplicating large diagnostic histories in every sample; full diagnostics remain in the original archive.
- Stream atomic JSON writes instead of constructing large JSON strings in memory.
- Verify the reported 37 MiB real archive in isolated storage: about 109 MiB peak during import and 128 MiB when opening the trip on the test host; 37 Python tests pass.

## 1.11.15

- Upload trip archives in 1 MiB chunks to pass the HA HTTP proxy's 16 MiB request limit without changing Home Assistant settings.
- Show upload progress, safely retry chunks, verify ordering and total size, and clean up cancelled or expired uploads.
- Test a complete archive larger than 37 MiB through an HTTP endpoint enforcing the 16 MiB proxy limit.

## 1.11.14

- Increase trip archive upload limit to 128 MiB in both the browser and server, allowing recordings larger than 32 MiB.
- Stream journals up to 1 GiB decompressed and one million records while retaining per-record and corruption checks.
- Verify binary HTTP import with a synthetic archive larger than 37 MiB.

## 1.11.13

- Upload Navigation `.jsonl.gz` archives in the trip drawer to create a trip without live telemetry or enrich an existing trip.
- Restore recorded route geometry and switches, GPS/FakeGPS measurements, raw steering and revised inertial trajectories, including inertial-only recordings.
- Reject incompatible attachments using time overlap, device type, odometer and usable GPS fixes. Preserve original live logs and prevent duplicate imports or replacing a complete archive with a partial one.
- Bound compressed/decompressed upload sizes and reject corrupt archives; show imported layer counts and missing completion records.

## 1.11.12

- Import recorded inertial fusion revisions from Navigation journals, replacing old poses and preserving separate segments instead of redrawing the uncorrected curve.
- Reimport previously cached journals with import version 2 and show inertial fragments without bridges across re-anchors.
- Protect completed/newer HA trajectory snapshots from delayed active snapshots and duplicate downloads.

## 1.11.11

- Fix trajectory `started_at_ms` fallback to first point timestamp when missing or zero in attached journals.

## 1.11.10

- Fix trip preview and original JSONL download in the Ingress diagnostics page when trips are stored by `TripLogRegistry`.

## 1.11.9

- Add an Ingress diagnostics page with search, preview and original-file downloads for Simulator trips and complete or partial head-unit journals.
- Show the latest Gateway and Relay log lines delivered to the native HA integration and allow JSON export.

## 1.11.8

- Draw the inertial trajectory from live Navigation snapshots and completed trip journals as a separate map line.
- Show the inertial point count in trip summaries.

## 1.11.7

- Apply the FakeGPS route alignment to native Navigation trajectory snapshots as well as completed full-journal trajectories. The previous viewer only aligned full journals, which may arrive later or not at all.
- Keep the raw x/y projection behind the diagnostic toggle. When no route and no FakeGPS anchors exist, report that the trajectory cannot be geographically aligned instead of displaying a misleading displaced line.

## 1.11.6

- Draw dense steering points along the published FakeGPS route position in the trip map.
- Recover aligned positions from recorded FakeGPS progress and captured route geometry for older journals, with breaks at route changes and unavailable FakeGPS intervals.
- Keep the raw sensor trajectory available through the separate diagnostic toggle.

## 1.11.5

- Draw Navigation's route-aligned steering positions as a separate line on the captured route.
- Preserve the raw steering trace and break the aligned line at rejected fits, gaps and route changes.

## 1.11.4

- Show applied steering corrections and steering-detected route departures on
  the live trip map, chart and event table, separate from GPS corrections.
- Recover steering fit, correction, departure and route-rebuild events from
  completed Navigation journals retained by Home Assistant.
- Keep journal event markers when a native trajectory takes precedence over
  the reconstructed journal trajectory, without duplicating the line.
- Restore steering traces and events from full journals after a Home Assistant
  restart, with original timestamps and source labels.

## 1.11.3

- Automatically import live and archived steering trajectories from the native
  HA integration into persistent add-on storage.
- Attach traces to matching head-unit trip records and draw each steering-data
  fragment over the trip map without connecting across missing-data gaps.
- Show the HA-delivered steering angle and freshness in simulator diagnostics.

## 1.11.0

- Added Virtual Trajectory Engine support: persistent storage, API endpoints (/api/controller/trajectories/*), and dynamic Leaflet visualization in Electric Magenta (#d946ef).
- Interactive calibration overlay controls: initial bearing (theta_0) rotation slider and steering ratio scale multiplier slider with live flat-earth projection onto map.
- Live CAN ID 0x0E0 steering wheel angle (deg) and rotation rate (deg/s) metrics display in simulator telemetry bar.
- Trajectory file upload, export, deletion, and direct fetch from Head Unit navigation endpoints.

## 1.10.6

- Preserve complete 2GIS route snapshots in live route transport and trip history alongside MapKit.

## 1.10.5

- In HA / Internet mode, read live MapKit geometry through the native
  integration transport instead of sending a protected Gateway reload request
  to Home Assistant (HTTP 401).

## 1.10.4

- Correct the native integration compatibility entity ID used by Home
  Assistant's entity registry.

## 1.10.3

- prefer fresh `sensor.belgee_x50_trip_diagnostics` data from the native
  Belgee X50 integration, while retaining the legacy YAML sensor as fallback.

## 1.10.2

- Separate archived-trip overlays from the direct live MapKit layer.
- Hide the live layer when selecting a trip and provide an explicit `Live
  MapKit` switch to show it again.
- Do not expose a cached AVD route as live when its direct Gateway source is
  offline.

## 1.10.1

- Read full MapKit route snapshots from the existing
  `sensor.x50_trip_diagnostics` navigation attributes.
- Keep the dedicated retained route entity as an optional compatibility
  fallback, not a required Home Assistant package update.
- Strip the compressed transport envelope before writing one-second trip
  samples.

## 1.10.0

- Receive complete MapKit route snapshots from the real head unit through
  Relay and a dedicated retained Home Assistant MQTT entity.
- Decode gzip+base64 geometry and metadata inside the add-on and journal it
  only in `head_unit` trips.
- Record the exact activation and removal time for every transported route;
  AVD route polling remains independent.

## 1.9.0

- Store X50 Navigation `0.7.0` persistent-calibration diagnostics in trip
  journals for post-drive analysis.
- Include active/candidate factors, confidence, MAD, evidence counters,
  calibrated distance and systematic normal-correction bias.

## 1.8.0

- Make live MapKit `DrivingRoute` the only route source.
- Remove Guidance, History and stale-history controls from the simulator UI.
- Treat MapKit geometry as authoritative and never smooth it.

## 1.7.1

- Rename the FakeGPS control feedback to identify X50 Navigation as the owner;
  Gateway remains only the compatibility transport.

## 1.7.0

- Split the trip journal by device identity so HA/Relay and AVD data never
  share one session.
- Label trip cards and details with their source device.
- Keep route snapshots scoped to the device that produced them.

## 1.6.0

- Persist off-route passthrough state, distance, confirmation/recovery counters
  and GPS-to-vehicle speed difference in every trip sample and event.
- Persist route generation, activation, identity and exact-route freshness so a
  stuck passthrough can be distinguished from a stale MapKit capture.

## 1.5.0

- Persist every Navigator route geometry used during a trip, including full
  captured MapKit metadata.
- Record route switches separately with Gateway activation time, observation
  time, progress and the nearest real/FakeGPS position.
- Display all trip route versions in distinct stable colours with numbered
  switch markers and exact active intervals.
- Add an independent Navigator-route layer toggle; old trips remain readable.

## 1.4.0

- Trip GPS quality now follows Gateway's strict freshness and accuracy result;
  a newly received coordinate with unusable accuracy no longer closes an outage.
- Trip events use Gateway's cumulative signed and absolute correction counters,
  preserving all corrections made between HA polls.
- Store time-alignment, prediction, recovery-mode and tick-loss diagnostics for
  evaluating Gateway 2.16.0 drives.

## 1.3.1

- Исправлена отрисовка трека поездки при фактическом интервале журнала чуть больше 5 секунд.
- Разрывы линии теперь определяются адаптивно по частоте записей поездки, поэтому обычные GPS/FakeGPS точки соединяются, а реальные длительные пропуски остаются разрывами.

## 1.3.0

- Added trip playback on the main map from the persistent journal.
- Real Carlinkit GPS and injected FakeGPS are separate selectable tracks.
- GPS correction/reacquisition events are shown as markers; when both positions
  exist, a connector visualizes the actual correction vector.
- Selecting a trip updates overlays without moving the map. The explicit
  "Show on map" action fits the selected track and closes the journal drawer.

## 1.2.2

- Decoupled live route/control access from trip telemetry: routes may be read
  directly from the GU VPN address while the journal continuously follows
  Relay data through `sensor.x50_trip_diagnostics`.
- Added a 20-second HA sample freshness guard and recorded the journal source
  in every trip summary.

## 1.2.1

- Persisted Gateway URL, mode and control token under the add-on `/data`
  directory so Core/add-on restarts no longer reset remote GU access.
- Stopped exposing the injected Home Assistant Supervisor token through the
  controller state API.
- Removed the browser's `x50test` default and automatic token persistence,
  preventing page startup from overwriting the real head-unit token.

## 1.2.0

- Added a persistent trip journal under the Home Assistant add-on `/data`
  directory. One-second telemetry snapshots are retained across add-on
  updates and restarts.
- Added explicit `gps_progress_correction` and `gps_reacquired` events with
  vehicle/corrected speed, odometer deltas, route progress, GPS quality,
  correction weight and signed progress shift.
- GPS outage events compare odometer distance, speed-integrated distance and
  route progress before/after reacquisition, making calibration drift visible.
- Added a responsive trip drawer with trip summaries, speed/GPS timeline and
  a correction-event table. A manual finish action is available for bench
  tests; otherwise a trip closes after three stationary minutes.
- Added read-only trip APIs: `GET /api/controller/trips` and
  `GET /api/controller/trips/<id>`.

## 1.1.3

- Added explicit map-click modes: send a GPS point or inspect the nearest
  MapKit route segment without changing AVD position.
- Added a segment data card with speed limit, traffic speed/type, section,
  road objects, coordinates and complete segment JSON.
- Route refreshes now replace only map overlays and preserve the current map
  center and zoom. Automatic fitting happens only on the initial untouched
  view; the existing fit button remains available on demand.

## 1.1.2

- Fixed manual AVD positioning from HA: native emulator-console commands are
  now executed by a restricted Windows host agent instead of inside HA.
- Added `geo_bridge_url` and `geo_bridge_token` settings with automatic URL
  derivation from `adb_host` for existing installations.

## 1.1.1

- Fixed AVD control from Home Assistant by using the ADB server on the
  Windows emulator host instead of looking for a local container emulator.
- Added remote `adb emu geo fix` delivery, preserving native AVD GPS updates.
- Prevented the browser defaults from overwriting the configured Gateway URL
  before the first controller-state response arrives.

## 1.1.0

- Added end-to-end `x50.exact-route.v2` and `mapkit_route` support.
- Loaded legal speed limits and live jam speeds for every original MapKit
  segment without losing alignment during operational-route cleanup.
- Added route sections, camera and road-event data, traffic lights, speed
  bumps, pedestrian crossings, lane guidance, HD/standing sections and route
  metadata to the browser API.
- Added speed-limit coloring, road-object markers and a compact MapKit data
  completeness card to the responsive web interface.
- Made the Home Assistant add-on the canonical maintained simulator; the old
  standalone local server is archived in the main telemetry repository.

## 1.0.10

- **Graphical Layer Icons on Mobile**: Replaced text inside topbar layer-switch buttons with crisp graphical icons (`•` Points, `╱` Line, `❖` Both) on smartphones to resolve text overflow.
- **Fixed Mobile Drawer Expansion**: Re-ordered `mode-card` in DOM hierarchy so tapping `🎛 Панель сценариев` smoothly slides open the GPS scenario, MapKit exact, and Gateway toggles upwards above the speed dock.

- **Home Assistant Update Changelog Support**: Integrated `CHANGELOG.md` and `changelog` property into add-on manifest so HA Update dialog displays version changes directly inside the modal window.

## 1.0.8

- **Ultra-Compact Mobile Layout**: Redesigned UI for smartphones with a 85%+ visible interactive map, compressed ~70px speed dock, hidden telemetry footer, and a slide-up mobile sheet drawer (`🎛 Панель сценариев`).

## 1.0.7

- **Mobile Responsive UI**: Added mobile layout with collapsible panel for portrait screens.

## 1.0.6

- **Home Assistant Ingress Fix**: Dynamically resolved Ingress proxy path (`location.pathname`) to prevent HTTP 404 errors. Improved non-JSON error handling.

## 1.0.5

- **Remote HA / Internet Mode**: Integrated Home Assistant API and automatic `SUPERVISOR_TOKEN` to queue fake navigation & location commands into `input_text.x50_pending_command` for remote cars over the internet.

## 1.0.4

- **Dynamic Gateway Target Selector**: Added Gateway target IP selection UI with presets for `💻 AVD (127.0.0.1:8080)`, `🚗 ГУ (192.168.66.124:8080)`, and custom IP input.

## 1.0.3

- **Alpine 3.19 & Async ADB**: Switched base image to Alpine 3.19 with `dos2unix` line normalization and non-blocking ADB connection in `run.sh` to eliminate HA Ingress health check timeouts.

## 1.0.0

- Initial standalone Home Assistant add-on release.
