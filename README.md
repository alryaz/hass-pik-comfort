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
