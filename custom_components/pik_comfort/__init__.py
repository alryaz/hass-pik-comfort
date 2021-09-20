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
)

import asyncio
import logging
from typing import Final

from homeassistant import config_entries
from homeassistant.const import CONF_TOKEN, CONF_USERNAME
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.typing import ConfigType, HomeAssistantType

from custom_components.pik_comfort.api import PikComfortAPI
from custom_components.pik_comfort.const import (
    CONF_ACCOUNTS,
    CONF_BRANCH,
    CONF_CHARGES,
    CONF_METERS,
    CONF_NAME_FORMAT,
    CONF_PHONE_NUMBER,
    CONF_USER_AGENT,
    DATA_ENTITIES,
    DATA_FINAL_CONFIG,
    DATA_PLATFORM_ENTITY_REGISTRARS,
    DATA_UPDATE_LISTENERS,
    DATA_UPDATE_ROUTINES,
    DATA_YAML_CONFIG,
    DEFAULT_NAME_FORMAT_EN_ACCOUNTS,
    DEFAULT_NAME_FORMAT_EN_CHARGES,
    DEFAULT_NAME_FORMAT_EN_METERS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


def mask_username(username: str):
    return "***" + username[-3:]


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

    log_prefix = f"[{mask_username(phone_number)}] "
    _LOGGER.info(
        log_prefix + f"Обновление конфигурационной записи {config_entry.version}"
    )

    if config_entry.version == 1:
        data = dict(config_entry.data)
        data[CONF_PHONE_NUMBER] = data.pop(CONF_USERNAME)

        config_entry.version = 2
        hass.config_entries.async_update_entry(config_entry, data=data)

    return True


async def async_setup_entry(
    hass: HomeAssistantType, config_entry: config_entries.ConfigEntry
):
    username = config_entry.data[CONF_PHONE_NUMBER]

    entry_id = config_entry.entry_id

    log_prefix = f"[{mask_username(username)}] "
    hass_data = hass.data

    # Source and convert configuration from input post_fields
    user_cfg = {**config_entry.data}

    if config_entry.options:
        if config_entry.options.get(CONF_PHONE_NUMBER):
            _LOGGER.error("Options entry for configuration contains a username")
            return False

        user_cfg.update(config_entry.options)

    phone_number, auth_token = user_cfg[CONF_PHONE_NUMBER], user_cfg[CONF_TOKEN]

    _LOGGER.info(log_prefix + "Применение конфигурационной записи")

    from custom_components.pik_comfort.api import PikComfortException

    api_object = PikComfortAPI(
        username=phone_number,
        token=auth_token,
    )

    try:
        await api_object.async_update_data()

    except PikComfortException as error:
        _LOGGER.error(log_prefix + "Ошибка при получении данных: {error}")
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

    _LOGGER.debug(log_prefix + "Применение конфигурации успешно")
    return True


SUPPORTED_PLATFORMS: Final = ("binary_sensor", "sensor")


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
