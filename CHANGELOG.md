# Changelog

## 1.3.1

- Исправлена отрисовка трека поездки при фактическом интервале журнала чуть больше 5 секунд.
- Разрывы линии теперь определяются адаптивно по частоте записей поездки, поэтому обычные GPS/FakeGPS точки соединяются, а реальные длительные пропуски остаются разрывами.

## 1.3.0

- Trip routes can now be displayed on the simulator map.
- Separate real-GPS and FakeGPS tracks plus correction markers make drift and
  subsequent GPS catch-up visible for calibration analysis.

## 1.2.2

- Decoupled direct GU route access from HA/Relay real-trip logging.

## 1.2.1

- Persistent remote-Gateway configuration and control token.
- Removed Supervisor-token exposure and browser-side `x50test` overwrite.

## 1.2.0

- Persistent trip journal with one-second diagnostic samples.
- Explicit GPS outage/reacquisition and signed progress-correction events.
- Responsive trip timeline, summaries and correction table in HA Ingress.
- HA-mode ingestion through `sensor.x50_trip_diagnostics`.

## 1.1.3

- Added separate GPS-selection and MapKit-segment inspection click modes.
- Preserved map center and zoom while captured-route overlays are refreshed.

## 1.1.2

- Fixed native AVD GPS positioning through the restricted Windows host agent.
- Added automatic `geo_bridge_url` configuration for existing HA installs.

## 1.1.1

- Fixed AVD control through a remote Windows ADB server.
- Added native AVD GPS delivery through `adb emu geo fix`.
- Preserved the configured Gateway URL during initial browser startup.

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
