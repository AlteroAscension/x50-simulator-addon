# Changelog

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
