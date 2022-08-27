"""Energosbyt API"""
__all__ = (
    "async_unload_entry",
    "async_reload_entry",
    "async_setup",
    "async_setup_entry",
    "config_flow",
    "const",
    "sensor",
    "DOMAIN",
    "mask_username",
)

import asyncio
import logging
import re
from collections import ChainMap
from functools import partial
from typing import Dict, Final, List, Mapping, Optional

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.components import persistent_notification
from homeassistant.const import CONF_SCAN_INTERVAL, CONF_TOKEN, CONF_USERNAME
from homeassistant.core import ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType, HomeAssistantType

from custom_components.pik_comfort.api import (
    PikComfortAPI,
    PikComfortAccount,
    PikComfortException,
    TicketClassifier,
    get_random_device_name,
)
from custom_components.pik_comfort.const import (
    ATTR_ACCOUNT_ID,
    ATTR_CLASSIFIER_ID,
    ATTR_CONFIG_ENTRY_ID,
    ATTR_COUNT,
    ATTR_DESCRIPTION,
    ATTR_FORCE_UPDATE,
    ATTR_IGNORE_CLASSIFIER_CHECK,
    ATTR_MAX_RESULTS,
    ATTR_MESSAGE,
    ATTR_NOTIFICATION,
    ATTR_PHONE_NUMBER,
    ATTR_QUERY,
    ATTR_RESULTS,
    ATTR_SUCCESS,
    ATTR_TICKET_ID,
    CONF_ACCOUNTS,
    CONF_BRANCH,
    CONF_CHARGES,
    CONF_DEVICE_NAME,
    CONF_METERS,
    CONF_NAME_FORMAT,
    CONF_PHONE_NUMBER,
    CONF_SDK_VERSION,
    CONF_USER_AGENT,
    DATA_ENTITIES,
    DATA_FINAL_CONFIG,
    DATA_PLATFORM_ENTITY_REGISTRARS,
    DATA_UPDATE_LISTENERS,
    DATA_UPDATE_ROUTINES,
    DATA_YAML_CONFIG,
    DEFAULT_MAX_RESULTS,
    DEFAULT_NAME_FORMAT_EN_ACCOUNTS,
    DEFAULT_NAME_FORMAT_EN_CHARGES,
    DEFAULT_NAME_FORMAT_EN_METERS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    SUPPORTED_PLATFORMS,
    ATTR_CLASSIFIER_ID,
    ATTR_FORCE_UPDATE,
    ATTR_IGNORE_CLASSIFIER_CHECK,
    ATTR_MAX_RESULTS,
    ATTR_MESSAGE,
    ATTR_QUERY,
    ATTR_TICKET_ID,
)

_LOGGER = logging.getLogger(__name__)


_RE_MARKDOWN_PARSE = re.compile(r"([_*\[\]()~`>#+\-=|.!])")
_RE_MARKDOWN_REPARSE = re.compile(r"\\\\([_*\[\]()~`>#+\-=|.!])")


def escape_markdown(text: str):
    """Escape markdown.
    Adapted from: https://www.programcreek.com/python/?CodeExample=escape+markdown
    :param text: Markdown text
    :return: Escaped markdown text
    """
    parse = _RE_MARKDOWN_PARSE.sub(r"\\\1", text)
    reparse = _RE_MARKDOWN_REPARSE.sub(r"\1", parse)
    return reparse


def mask_username(username: str):
    return "***" + username[-3:]


# noinspection PyUnusedLocal
async def async_setup(hass: HomeAssistantType, config: ConfigType):
    """Set up the Pik Comfort component."""

    # @TODO: move placeholder creation here

    return True


async def async_migrate_entry(
    hass: HomeAssistantType, config_entry: config_entries.ConfigEntry
) -> bool:
    if config_entry.version > 1:
        phone_number = config_entry.data[CONF_PHONE_NUMBER]
    else:
        phone_number = config_entry.data[CONF_USERNAME]

    from custom_components.pik_comfort.config_flow import PikComfortConfigFlow

    log_prefix = f"[{mask_username(phone_number)}] "
    _LOGGER.info(
        log_prefix + f"Обновление конфигурационной записи с версии "
        f"{config_entry.version} до {PikComfortConfigFlow.VERSION}"
    )

    data = dict(config_entry.data)
    if config_entry.version < 2:
        data[CONF_PHONE_NUMBER] = data.pop(CONF_USERNAME)

    if config_entry.version < 3:
        data[CONF_DEVICE_NAME] = get_random_device_name()

    if config_entry.version < 4:
        data[CONF_SCAN_INTERVAL] = DEFAULT_SCAN_INTERVAL

    config_entry.version = PikComfortConfigFlow.VERSION
    hass.config_entries.async_update_entry(config_entry, data=data)

    return True


_RE_UUID_MATCH = re.compile(r"[a-f0-9]{8}-([a-f0-9]{4}-){3}[a-f0-9]{8}")

UUID_VALIDATOR: Final = vol.All(cv.string, str.lower, vol.Match(_RE_UUID_MATCH))

SERVICE_CREATE_TICKET: Final = "create_ticket"
SERVICE_CREATE_TICKET_SCHEMA: Final = vol.Schema(
    {
        vol.Required(ATTR_CLASSIFIER_ID): UUID_VALIDATOR,
        vol.Required(ATTR_ACCOUNT_ID): UUID_VALIDATOR,
        vol.Required(ATTR_MESSAGE): cv.string_with_no_html,
        vol.Optional(ATTR_IGNORE_CLASSIFIER_CHECK, default=False): cv.boolean,
    }
)

SERVICE_SEARCH_TICKET_CLASSIFIERS: Final = "search_ticket_classifiers"
SERVICE_SEARCH_TICKET_CLASSIFIERS_SCHEMA: Final = vol.Schema(
    {
        vol.Required(ATTR_QUERY): cv.string_with_no_html,
        vol.Optional(ATTR_NOTIFICATION, default=False): vol.Any(
            cv.boolean,
            persistent_notification.SCHEMA_SERVICE_NOTIFICATION,
        ),
        vol.Optional(ATTR_MAX_RESULTS, default=DEFAULT_MAX_RESULTS): vol.All(
            vol.Coerce(int), vol.Range(min=1, min_included=True)
        ),
        vol.Optional(ATTR_FORCE_UPDATE, default=False): cv.boolean,
    }
)


async def async_service_create_ticket(
    hass: HomeAssistantType, service_call: ServiceCall
):
    _LOGGER.debug(f"Creating ticket: {service_call} {hass}")

    account: Optional[PikComfortAccount] = None
    account_id: str = service_call.data[ATTR_ACCOUNT_ID]
    config_entry_id: Optional[str] = None

    api_objects: Dict[str, PikComfortAPI] = hass.data[DOMAIN]
    for existing_config_entry_id, existing_api_object in api_objects.items():
        existing_info = existing_api_object.info
        if existing_info:
            existing_accounts = existing_info.accounts
            if existing_accounts:
                for existing_account in existing_accounts:
                    if existing_account.id == account_id:
                        config_entry_id = existing_config_entry_id
                        account = existing_account
                        break

    if account is None:
        error_message = f'Лицевой счёт с идентификатором "{account_id}" не найден'
        _LOGGER.error(error_message)
        raise Exception(error_message)

    phone_number = hass.data[DATA_FINAL_CONFIG][config_entry_id][CONF_PHONE_NUMBER]
    log_prefix = f"[{mask_username(phone_number)}] "

    classifier_id: str = service_call.data[ATTR_CLASSIFIER_ID]
    message: str = service_call.data[ATTR_MESSAGE]
    ignore_classifier_check: Optional[bool] = service_call.data.get(
        ATTR_IGNORE_CLASSIFIER_CHECK
    )

    event_data = {
        ATTR_ACCOUNT_ID: account_id,
        ATTR_CLASSIFIER_ID: classifier_id,
        ATTR_MESSAGE: message,
        ATTR_IGNORE_CLASSIFIER_CHECK: bool(ignore_classifier_check),
        ATTR_TICKET_ID: None,
        ATTR_SUCCESS: False,
    }

    _LOGGER.info(log_prefix + "Выполнение запроса на создание нового обращения")
    try:
        ticket = await account.async_create_ticket(
            classifier_id,
            message,
            check_classifier=not ignore_classifier_check,
        )
    except PikComfortException as error:
        _LOGGER.error(f"Ошибка API: {error}")
        raise Exception("Ошибка API (данные записаны в лог)")
    else:
        event_data[ATTR_TICKET_ID] = ticket.id
        event_data[ATTR_SUCCESS] = True
        _LOGGER.info(log_prefix + "Создание нового обращения успешно выполнено")
    finally:
        hass.bus.async_fire(DOMAIN + "_" + SERVICE_CREATE_TICKET, event_data)


async def async_service_search_ticket_classifiers(
    hass: HomeAssistantType, service_call: ServiceCall
):
    _LOGGER.debug(f"Searching ticket classifiers: {service_call} {hass}")

    config_entry_id: Optional[str] = None
    api_object: Optional[PikComfortAPI] = None
    force_update: bool = bool(service_call.data.get(ATTR_FORCE_UPDATE))

    api_objects: Dict[str, PikComfortAPI] = hass.data[DOMAIN]
    for existing_config_entry_id, existing_api_object in api_objects.items():
        if existing_api_object.classifiers:
            api_object = existing_api_object
            config_entry_id = existing_config_entry_id
            break

    if api_object is None:
        try:
            config_entry_id, api_object = next(iter(api_objects.items()))
        except StopIteration:
            error_message = "Нет доступных конфигурационных записей / объектов API"
            _LOGGER.error(error_message)
            raise Exception(error_message)
        else:
            force_update = True

    phone_number = hass.data[DATA_FINAL_CONFIG][config_entry_id][CONF_PHONE_NUMBER]
    log_prefix = f"[{mask_username(phone_number)}] "

    if force_update:
        try:
            await api_object.async_update_classifiers()
        except PikComfortException as error:
            error_message = f"Ошибка при получении классификаторов: {error}"
            _LOGGER.error(log_prefix + error_message)
            raise Exception(error_message)

    search_query = service_call.data[ATTR_QUERY].lower().strip()

    _LOGGER.debug(
        log_prefix + f'Выполняется поиск классификаторов по запросу "{search_query}"'
    )

    max_results = max(service_call.data[ATTR_MAX_RESULTS], 1)
    results_list: List[TicketClassifier] = []

    for classifier in api_object.classifiers:
        if search_query in classifier.name.lower() and not classifier.has_children:
            results_list.append(classifier)

            if len(results_list) == max_results:
                break

    if results_list:
        results_text = "\n".join(
            (
                f"- `{classifier.id}`\n  _"
                + "_ > _".join(
                    escape_markdown(path_classifier.name)
                    for path_classifier in classifier.path_to
                )
                + "_"
            )
            for classifier in results_list
        )
    else:
        results_text = "_Нет результатов_"

    message = (
        f"Найденные классификаторы по запросу "
        f'_"{escape_markdown(search_query)}"_:'
        f"\n{results_text}"
    )

    _LOGGER.info(log_prefix + message)

    event_data = {
        ATTR_CONFIG_ENTRY_ID: config_entry_id,
        ATTR_PHONE_NUMBER: api_object.username,
        ATTR_RESULTS: {classifier.id: classifier.name for classifier in results_list},
        ATTR_COUNT: len(results_list),
        ATTR_MESSAGE: message,
    }

    event_id = DOMAIN + "_" + SERVICE_SEARCH_TICKET_CLASSIFIERS

    hass.bus.async_fire(event_id, event_data)

    notification_content = service_call.data.get(ATTR_NOTIFICATION)
    if notification_content is not False:
        payload = {
            persistent_notification.ATTR_TITLE: f"Результаты поиска классификаторов",
            persistent_notification.ATTR_NOTIFICATION_ID: event_id,
            persistent_notification.ATTR_MESSAGE: message,
        }

        if isinstance(notification_content, Mapping):
            for key, value in notification_content.items():
                payload[key] = str(value).format_map(event_data)

        hass.async_create_task(
            hass.services.async_call(
                persistent_notification.DOMAIN,
                persistent_notification.SERVICE_CREATE,
                payload,
            )
        )


async def async_setup_entry(
    hass: HomeAssistantType, config_entry: config_entries.ConfigEntry
):
    username = config_entry.data[CONF_PHONE_NUMBER]
    entry_id = config_entry.entry_id
    log_prefix = f"[{mask_username(username)}] "
    hass_data = hass.data
    user_cfg = ChainMap(config_entry.data, config_entry.options)
    phone_number, auth_token = user_cfg[CONF_PHONE_NUMBER], user_cfg[CONF_TOKEN]

    _LOGGER.info(log_prefix + f"Применение конфигурационной записи")

    from custom_components.pik_comfort.api import PikComfortException

    api_object = PikComfortAPI(
        username=phone_number,
        token=auth_token,
        device_name=user_cfg[CONF_DEVICE_NAME],
    )

    try:
        await api_object.async_update_info()

    except PikComfortException as error:
        _LOGGER.error(log_prefix + f"Ошибка при получении данных: {error}")
        await api_object.async_close()
        raise ConfigEntryNotReady(f"{error}")

    # Create placeholders
    hass_data.setdefault(DOMAIN, {})[entry_id] = api_object
    hass_data.setdefault(DATA_ENTITIES, {})[entry_id] = {}
    hass_data.setdefault(DATA_FINAL_CONFIG, {})[entry_id] = user_cfg
    hass_data.setdefault(DATA_PLATFORM_ENTITY_REGISTRARS, {})[entry_id] = {}
    hass_data.setdefault(DATA_UPDATE_ROUTINES, {})

    # Forward entry setup to sensor platform
    for domain in SUPPORTED_PLATFORMS:
        hass.async_create_task(
            hass.config_entries.async_forward_entry_setup(
                config_entry,
                domain,
            )
        )

    # Create options update listener
    update_listener = config_entry.add_update_listener(async_reload_entry)
    hass_data.setdefault(DATA_UPDATE_LISTENERS, {})[entry_id] = update_listener

    # Register relevant services
    hass_services = hass.services
    if not hass_services.has_service(DOMAIN, SERVICE_CREATE_TICKET):
        hass_services.async_register(
            DOMAIN,
            SERVICE_CREATE_TICKET,
            partial(async_service_create_ticket, hass),
            SERVICE_CREATE_TICKET_SCHEMA,
        )

    if not hass_services.has_service(DOMAIN, SERVICE_SEARCH_TICKET_CLASSIFIERS):
        hass_services.async_register(
            DOMAIN,
            SERVICE_SEARCH_TICKET_CLASSIFIERS,
            partial(async_service_search_ticket_classifiers, hass),
            SERVICE_SEARCH_TICKET_CLASSIFIERS_SCHEMA,
        )

    _LOGGER.debug(log_prefix + "Применение конфигурации успешно")
    return True


async def async_reload_entry(
    hass: HomeAssistantType,
    config_entry: config_entries.ConfigEntry,
) -> None:
    """Reload Pik Comfort entry"""
    log_prefix = f"[{mask_username(config_entry.data[CONF_PHONE_NUMBER])}] "
    _LOGGER.info(log_prefix + "Перезагрузка интеграции")
    await hass.config_entries.async_reload(config_entry.entry_id)


async def async_unload_entry(
    hass: HomeAssistantType,
    config_entry: config_entries.ConfigEntry,
) -> bool:
    """Unload Pik Comfort entry"""
    log_prefix = f"[{mask_username(config_entry.data[CONF_PHONE_NUMBER])}] "
    entry_id = config_entry.entry_id

    tasks = [
        hass.config_entries.async_forward_entry_unload(config_entry, domain)
        for domain in SUPPORTED_PLATFORMS
    ]

    unload_ok = all(await asyncio.gather(*tasks))

    if unload_ok:
        # Cancel updater
        hass.data[DATA_UPDATE_ROUTINES].pop(entry_id)[1]()

        # Cancel reload listener
        hass.data[DATA_UPDATE_LISTENERS].pop(entry_id)()

        # Close API object
        await hass.data[DOMAIN].pop(entry_id).async_close()

        # Remove final configuration from entry
        hass.data[DATA_FINAL_CONFIG].pop(entry_id)

        # Remove entity holders
        hass.data[DATA_ENTITIES].pop(entry_id)

        # Remove platform entity registrars
        hass.data[DATA_PLATFORM_ENTITY_REGISTRARS].pop(entry_id)

        _LOGGER.info(log_prefix + "Интеграция выгружена")

    else:
        _LOGGER.warning(log_prefix + "При выгрузке конфигурации произошла ошибка")

    return unload_ok
