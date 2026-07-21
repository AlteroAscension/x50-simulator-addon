# Home Assistant Add-on: X50 Navigation Simulator

Асинхронный браузерный симулятор движения автомобиля и подмены GPS-координат для Belgee X50 / Geely Coolray прямо внутри интерфейса Home Assistant.

## Возможности

- **Интеграция через HA Ingress**: Симулятор выводится во весь экран на левой панели Home Assistant (`panel_icon: mdi:car-sports`).
- **Связь с ГУ / ADB**: Автоматическое подключение ADB к ГУ или эмулятору по сети.
- **Интеграция с X50 Gateway**: Прямая передача одометра, скоростей и сырых GPS-точек в Gateway (`/api/telemetry`).
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
```

For an AVD hosted on another computer, `adb_server_mode: true` connects the
add-on to that computer's ADB server. The ADB server must listen on the LAN
interface, and TCP 5037 must be restricted by the host firewall to the Home
Assistant address. `gateway_url` points to the host-side port forwarded to
port 8080 inside the AVD.
