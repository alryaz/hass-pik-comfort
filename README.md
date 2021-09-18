<img src="https://raw.githubusercontent.com/alryaz/hass-pik-comfort/master/images/header.png" height="100" alt="Home Assistant + ПИК Домофон">

_&#xab;ПИК Комфорт&#xbb;_ для _Home Assistant_
==================================================

> Интеграция для личного кабинета услуг ЖКХ группы компаний ПИК. Поддержка передачи показаний по счётчикам.
>
> Integration for communal services personal cabinet from PIK Group. Supports meter readings submission.
> 
> [![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/custom-components/hacs)
> [![Лицензия](https://img.shields.io/badge/%D0%9B%D0%B8%D1%86%D0%B5%D0%BD%D0%B7%D0%B8%D1%8F-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
> [![Поддержка](https://img.shields.io/badge/%D0%9F%D0%BE%D0%B4%D0%B4%D0%B5%D1%80%D0%B6%D0%B8%D0%B2%D0%B0%D0%B5%D1%82%D1%81%D1%8F%3F-%D0%B4%D0%B0-green.svg)](https://github.com/alryaz/hass-pik-comfort/graphs/commit-activity)
>
> [![Пожертвование Yandex](https://img.shields.io/badge/%D0%9F%D0%BE%D0%B6%D0%B5%D1%80%D1%82%D0%B2%D0%BE%D0%B2%D0%B0%D0%BD%D0%B8%D0%B5-Yandex-red.svg)](https://money.yandex.ru/to/410012369233217)
> [![Пожертвование PayPal](https://img.shields.io/badge/%D0%9F%D0%BE%D0%B6%D0%B5%D1%80%D1%82%D0%B2%D0%BE%D0%B2%D0%B0%D0%BD%D0%B8%D0%B5-Paypal-blueviolet.svg)](https://www.paypal.me/alryaz)

> **Интеграция для домофонов системы «ПИК Домофон»: [alryaz/hass-pik-intercom](https://github.com/alryaz/hass-pik-intercom)**

## Установка

1. Установите
   HACS ([инструкция по установке на оф. сайте](https://hacs.xyz/docs/installation/installation/))
1. Добавьте репозиторий в список дополнительных:
    1. Откройте главную страницу _HACS_
    1. Откройте раздел _Интеграции (Integrations)_
    1. Нажмите три точки сверху справа (допонительное меню)
    1. Выберите _Пользовательские репозитории_
    1. Скопируйте `https://github.com/alryaz/hass-pik-comfort` в поле вводавыберите _Интеграция (Integration)_ в выпадающем списке -> Нажмите _Добавить (Add)_
    1. Выберите _Интеграция (Integration)_ в выпадающем списке
    1. Нажмите _Добавить (Add)_
1. Найдите `PIK Comfort` (`ПИК Комфорт`) в поиске по интеграциям
1. Установите последнюю версию компонента, нажав на кнопку `Установить` (`Install`)
1. Перезапустите Home Assistant

## Конфигурация компонента

Компонент требует авторизацию через получение СМС-кода. Ввиду этого поддержку конфигурации
посредством YAML пришлось отложить на неопределённый срок.

Таким образом, конфигурация компонента возможна через ерез раздел _Интеграции_
(в поиске - _PIK Comfort_ или _ПИК Комфорт_)

## Использование компонента

> ⚠️ **Внимание!** Данный раздел находится в разработке.

### Служба передачи показаний - `pik_comfort.push_readings`

Служба передачи показаний позволяет отправлять показания по счётчикам в личный кабинет, и
имеет следующий набор параметров:

| Название | Описание |
| --- | --- |
| `target` | Выборка целевых объектов, для которых требуется передавать показания |
| `data`.`readings` | Список / именованный массив показаний, передаваемых в ЛК |
| `data`.`incremental` | Суммирование текущих показаний с передаваемыми |
| `data`.`ignore_indications` | Игнорировать ограничения по значениям |

Результатом вызова службы будет событие с идентификатором `pik_comfort_push_readings`
и следующими значениями:

| Название | Тип | Описание |
| -------- | --- | -------- |
| `comment` | `str` | Коментарий (об состоянии передачи показаний) |
| `success` | `bool` | Успешность передачи показаний |
| `readings` | `Dict[int, float]`/`None` | Передаваемые показания (идентификатор тарифа => показание)<br>_(отсутствует при возникновении ошибки до фактической попытки передачи)_ |
| `meter_uid` | `str` | Уникальный идентификатор счётчика |
| `meter_type` | `str` | Тип объекта счётчика |
| `meter_code` | `str`/`None` | Серийный номер счётчика (если доступен) |
| `call_params` | `Dict[str, Any]` | Параметры вызова службы |
| `entity_id` | `str` | Идентификатор объекта, над которым производился вызов службы |

#### Примеры вызова службы

##### 1. Обычная передача показаний

- Например, если передача показаний активна с 15 по 25 число, а сегодня 11, то показания
  <font color="red">**не будут**</font> отправлены<sup>1</sup>.
- Например, если текущие, последние или принятые значения по счётчику &ndash; 321, 654 и 987 по зонам
  _Т1_, _Т2_ и _Т3_ соответственно, то показания <font color="red">**не будут**</font>
  отправлены<sup>1</sup>.
  
```yaml
service: pik_comfort.push_readings
data:
  indications: "123, 456, 789"
target:
  entity_id: binary_sensor.1243145122_meter_123456789
```

... или, с помощью именованного массива:

```yaml
service: pik_comfort.push_readings
data:
  indications:
    t1: 123
    t2: 456
    t3: 789
target:
  entity_id: binary_sensor.1243145122_meter_123456789
```

... или, с помощью списка:

```yaml
service: pik_comfort.push_readings
data:
  indications: [123, 456, 789]
target:
  entity_id: binary_sensor.1243145122_meter_123456789
```

##### 2. Форсированная передача показаний

Отключение всех ограничений по показаниям.

- Например, если передача показаний активна с 15 по 25 число, а сегодня 11, то показания
  <font color="green">**будут**</font> отправлены<sup>1</sup>.
- Например, если текущие, последние или принятые значения по счётчику &ndash; 321, 654 и 987 по зонам
  _Т1_, _Т2_ и _Т3_ соответственно, то показания <font color="green">**будут**</font>
  отправлены<sup>1</sup>.
  
```yaml
service: pik_comfort.push_readings
data_template:
  indications: [123, 456, 789]
  ignore_indications: true
  ignore_periods: true
target:
  entity_id: binary_sensor.1243145122_meter_123456789
```

##### 3. Сложение показаний

- Например, если передача показаний активна с 15 по 25 число, а сегодня 11, то показания
  <font color="red">**не будут**</font> отправлены<sup>1</sup>.
- Например, если текущие, последние или принятые значения по счётчику &ndash; 321, 654 и 987 по зонам
  _Т1_, _Т2_ и _Т3_ соответственно, то показания <font color="green">**будут**</font>
  отправлены<sup>1</sup>.
  
**Внимание:** в данном примере будут отправлены показания _444_, _1110_ и _1776_,
а не _123_, _456_ и _789_. 
  
```yaml
service: pik_comfort.push_readings
data_template:
  indications: [123, 456, 789]
  incremental: true
target:
  entity_id: binary_sensor.1243145122_meter_123456789
```