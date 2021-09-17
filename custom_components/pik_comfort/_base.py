import asyncio
import logging
from abc import ABC, abstractmethod
from datetime import timedelta
from typing import Awaitable, Callable, Dict, Optional

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.typing import HomeAssistantType

from custom_components.pik_comfort.const import (
    DATA_FINAL_CONFIG,
    DATA_PLATFORM_ENTITY_REGISTRARS,
    DATA_UPDATE_ROUTINES,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    DATA_ENTITIES,
    SUPPORTED_PLATFORMS,
)
from custom_components.pik_comfort.api import PikComfortAPI, PikComfortException

_LOGGER = logging.getLogger(__name__)


class BasePikComfortEntity(Entity, ABC):
    def __init__(self, config_entry_id: str) -> None:
        self._config_entry_id = config_entry_id
        self._collective_update_future: Optional[asyncio.Future] = None
        self._collective_update_counter: int = 0
        self._available: bool = True

    @property
    @abstractmethod
    def unique_id(self) -> str:
        raise NotImplementedError

    @property
    def api_object(self) -> PikComfortAPI:
        return self.hass.data[DOMAIN][self._config_entry_id]

    @property
    def should_poll(self) -> bool:
        return False

    async def async_added_to_hass(self) -> None:
        self.hass.data[DATA_ENTITIES][self._config_entry_id].setdefault(
            self.__class__, []
        ).append(self)

    async def async_will_remove_from_hass(self) -> None:
        self.hass.data[DATA_ENTITIES][self._config_entry_id][self.__class__].remove(
            self
        )

    async def async_update(self) -> None:
        await self.hass.data[DATA_UPDATE_ROUTINES][self._config_entry_id][0]()


async def async_handle_data_update(
    hass: HomeAssistantType,
    config_entry_id: str,
) -> None:
    platform_entity_registrars = hass.data[DATA_PLATFORM_ENTITY_REGISTRARS][
        config_entry_id
    ]

    tasks: Dict[str, asyncio.Task] = {}

    for key, value in platform_entity_registrars.items():
        _LOGGER.debug(f"Scheduling data update for platform {key}")
        tasks[key] = hass.async_create_task(value())

    if tasks:
        await asyncio.wait(tasks.values(), return_when=asyncio.ALL_COMPLETED)

        for domain, task in tasks.items():
            exc = task.exception()
            if exc:
                _LOGGER.error(
                    f"Error occurred during update of platform {domain}: {repr(exc)}"
                )
    else:
        _LOGGER.warning("No updates scheduled. Did the component load correctly?")


async def async_setup_entry_for_platforms(
    hass: HomeAssistantType,
    config_entry: ConfigEntry,
    async_process_update: Callable[[], Awaitable[None]],
) -> None:
    platform = entity_platform.async_get_current_platform()

    if platform is None:
        raise ConfigEntryNotReady("Could not retrieve current platform")

    if platform.domain not in SUPPORTED_PLATFORMS:
        raise ConfigEntryNotReady(
            "Platform is not within the list of supported platforms"
        )

    config_entry_id = config_entry.entry_id

    platform_entity_registrars = hass.data[DATA_PLATFORM_ENTITY_REGISTRARS][
        config_entry_id
    ]
    platform_entity_registrars[platform.domain] = async_process_update

    if len(platform_entity_registrars) != len(SUPPORTED_PLATFORMS):
        return None

    collective_update_future: Optional[asyncio.Future] = None
    collective_update_counter: int = 0
    api_object: PikComfortAPI = hass.data[DOMAIN][config_entry_id]

    async def _async_update_delegator(*_) -> None:
        nonlocal collective_update_future, collective_update_counter

        if collective_update_future:
            # tap into existing future to await update
            await collective_update_future
            return

        collective_update_counter += 1
        collective_update_future = hass.loop.create_future()
        collective_update_future = collective_update_future

        # sleep for three seconds to collect simultaneous updates
        await asyncio.sleep(3)

        try:
            await api_object.async_update_data()

        except PikComfortException as update_error:
            _LOGGER.error(f"Could not update data: {update_error}")
            collective_update_future.set_exception(update_error)
            raise update_error

        else:
            collective_update_future.set_result(None)

        finally:
            collective_update_future = None

        # Perform entities updates
        await async_handle_data_update(hass, config_entry_id)

    # Perform initial update with retrieved data
    # (assuming data is available when platforms are set up)
    try:
        await async_handle_data_update(hass, config_entry_id)

    except BaseException as error:
        _LOGGER.error(f"Could not process updates! Error: {error}")

    user_cfg = hass.data[DATA_FINAL_CONFIG][config_entry_id]
    update_interval = timedelta(
        seconds=user_cfg.get(CONF_SCAN_INTERVAL) or DEFAULT_SCAN_INTERVAL
    )

    hass.data[DATA_UPDATE_ROUTINES][config_entry_id] = (
        _async_update_delegator,
        async_track_time_interval(
            hass,
            _async_update_delegator,
            update_interval,
        ),
    )
