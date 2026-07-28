# X50 Telemetry — Home Assistant Add-on Repository

![Add-on Version](https://img.shields.io/badge/Add--on-v1.10.0-blue)
![HA Ingress](https://img.shields.io/badge/HA-Ingress%20Supported-brightgreen)

Репозиторий официального дополнения **X50 Navigation Simulator** (версия
**`1.10.0`**) для Home Assistant.

Версия 1.10.0 получает полный MapKit-маршрут реального ГУ через retained
сущность `sensor.x50_navigation_route_transport` и сохраняет его в истории
поездки вместе с точным событием переключения. Для этого требуются Relay
`2.15-mapkit-route-transport` и актуальный пакет
`home-assistant/x50_telemetry.yaml` из основного репозитория. Маршрут AVD
по-прежнему читается напрямую и никогда не прикрепляется к поездке реального ГУ.

## Установка дополнения в Home Assistant

1. В Home Assistant перейдите в раздел **Настройки** → **Дополнения** → **Магазин дополнений**.
2. В правом верхнем углу нажмите **три точки** → **Репозитории**.
3. Вставьте ссылку на этот репозиторий:
   ```text
   https://github.com/AlteroAscension/x50-simulator-addon
   ```
4. В списке магазина появится дополнение **X50 Navigation Simulator**.
5. Нажмите **Установить**.
6. Включите тумблер **«Показывать на боковой панели»** (Show in sidebar) и нажмите **Запустить**.

---

## Доступные дополнения

| Дополнение | Описание | Ingress |
| --- | --- | --- |
| **X50 Navigation Simulator** | Асинхронный симулятор движения и подмены GPS-координат для Belgee X50 / Geely Coolray | Да (`mdi:car-sports`) |
