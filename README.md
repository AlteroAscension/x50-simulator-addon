# X50 Telemetry — Home Assistant Add-on Repository

![Add-on Version](https://img.shields.io/badge/Add--on-v1.10.1-blue)
![HA Ingress](https://img.shields.io/badge/HA-Ingress%20Supported-brightgreen)

Репозиторий официального дополнения **X50 Navigation Simulator** (версия
**`1.10.1`**) для Home Assistant.

Версия 1.10.1 получает полный MapKit-маршрут реального ГУ прямо из уже
существующего `sensor.x50_trip_diagnostics` и сохраняет его в истории поездки
вместе с событием переключения. Дополнительная MQTT-сущность и ручная замена HA
package для этого не требуются; нужен Relay `2.15.1-addon-route-channel`.
Маршрут AVD по-прежнему читается напрямую и никогда не прикрепляется к поездке
реального ГУ.

## Статус проекта

Этот add-on остаётся поддерживаемым переходным симулятором и журналом поездок:
для ветки `1.x` выпускаются критические исправления и изменения совместимости.
Новый эксплуатационный интерфейс и HA-native integration развиваются отдельно:

- [Belgee X50 HA Integration](https://github.com/AlteroAscension/belgee-x50-ha-integration);
- [Belgee X50 Control Center](https://github.com/AlteroAscension/belgee-x50-control-center);
- [план переноса данных](https://github.com/AlteroAscension/belgee-x50-control-center/blob/main/MIGRATION.md).

До завершения параллельной проверки этот add-on не считается устаревшим и
остаётся рабочим способом симуляции/анализа. Новые control-center функции сюда
не добавляются, чтобы не ломать проверенный стенд.

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
