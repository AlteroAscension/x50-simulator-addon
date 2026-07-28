# Home Assistant Add-on: X50 Navigation Simulator

Асинхронный браузерный симулятор движения автомобиля и подмены GPS-координат для Belgee X50 / Geely Coolray прямо внутри интерфейса Home Assistant.

## Возможности

- **Интеграция через HA Ingress**: Симулятор выводится во весь экран на левой панели Home Assistant (`panel_icon: mdi:car-sports`).
- **Связь с ГУ / ADB**: Автоматическое подключение ADB к ГУ или эмулятору по сети.
- **Интеграция с X50 Gateway**: передача стендовых скорости, одометра и GPS,
  а также доступ к X50 Navigation через защищённый compatibility proxy.
- **Захват маршрута из Навигатора**: Отображение точного MapKit-маршрута Яндекс.Навигатора на интерактивной карте.
- **MapKit route metadata v2**: Посегментные ограничения скорости, пробки,
  камеры, дорожные события, светофоры, переходы и схемы полос из
  `mapkit_route`.

---

## Настройка

В вкладке **Конфигурация** (Configuration) аддона в HA доступна гибкая настройка:

```yaml
adb_enabled: true
adb_host: "192.168.3.75"
adb_port: 5037
adb_server_mode: true
adb_device: "emulator-5554"
gateway_url: "http://192.168.3.75:18080"
gateway_token: "x50test"
geo_bridge_url: "http://192.168.3.75:18081"
geo_bridge_token: "x50test"
```

For an AVD hosted on another computer, `adb_server_mode: true` connects the
add-on to that computer's ADB server. Prefer a restricted Windows TCP proxy
to the loopback-only ADB server instead of making ADB listen on the whole LAN.
`gateway_url` points to the host-side port forwarded to port 8080 in the AVD.

The main `X50_telemetry` repository also provides
`start-x50-ha-avd-bridge.cmd`. On Windows it creates restricted TCP proxies
for the local ADB server and Gateway without exposing ADB to the whole LAN.

## Журнал поездок и GPS-коррекций

Аддон начинает поездку, когда скорость автомобиля или стенда достигает
`1 км/ч`, и автоматически завершает её через три минуты стоянки. Для коротких
стендовых экспериментов в панели **Журнал поездок** есть кнопка ручного
завершения. Данные хранятся в `/data/x50-trips`, то есть переживают обновление
контейнера аддона. Автоматического удаления сейчас нет.

Раз в секунду сохраняются скорости автомобиля/GPS, одометр и его приращение,
прогресс маршрута, проекция реального GPS, возраст/точность GPS, коэффициенты
и счётчики коррекции X50 Navigation. В режиме HA эти поля читаются через
Gateway compatibility proxy. Дополнительно создаются события:

- `gps_progress_correction` — X50 Navigation применил коррекцию прогресса;
- `gps_reacquired` — после разрыва снова появился хороший GPS fix.

Для `gps_reacquired` записываются длительность разрыва, путь по одометру, путь
по интегралу скорости, изменение прогресса и итоговый GPS-сдвиг. По этим
значениям можно понять, врёт ли масштаб одометра или ошибка возникает при
интегрировании скорости.

API для последующего анализа:

```text
GET  /api/controller/trips
GET  /api/controller/trips/<trip-id>
POST /api/controller/trips/finish
```

From add-on 1.4.0, `gps_good` uses X50 Navigation's strict
`real_gps_quality_good` flag (age <= 3 s and accuracy <= 35 m), exposed to HA
through Gateway. Correction events use differences between Navigation's
cumulative `correction_total_m` and `correction_abs_total_m`, so a five-second
HA poll preserves every internal correction in that interval. Samples also
retain fix source/receive age, time-alignment prediction, recovery state/mode
and discarded Navigation tick time.
The validation procedure is documented in the main repository at
`Yandex_navi/GPS_CORRECTION_VALIDATION.md`.

From add-on 1.5.0, a trip also owns the Navigator routes that were active
during it. Every distinct route is written once as an immutable
`route_snapshot` containing geometry and the complete captured `mapkit_route`.
Every activation is a separate `route_switch` record with both the exact
Navigation activation timestamp and the later add-on observation timestamp. The
trip drawer draws all versions in stable colours, places a numbered marker at
the switch position and shows the exact active interval. The **Маршруты**
button controls this layer independently from the GPS/FakeGPS selector.

The current supported combination is Gateway 2.24+ as compatibility proxy and
X50 Navigation 0.10.1+. Older pre-split Gateway builds may still produce route
snapshots, but the add-on must approximate their switch time from capture/poll
time. Trips recorded before add-on 1.5.0 remain readable and show an explicit
message that route geometry was not recorded.

From add-on 1.6.0, every trip sample and event also retains the complete
FakeGPS passthrough state: off-route distance, trigger/recovery fix counters,
GPS-to-vehicle speed difference, route generation/activation/identity and
exact-route freshness. This makes it possible to distinguish a real route
departure from a stale MapKit route and to verify automatic rejoin afterward.
