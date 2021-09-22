import asyncio
import logging
from abc import ABC, abstractmethod
from datetime import timedelta
from typing import Any, Awaitable, Callable, Dict, Optional

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.typing import HomeAssistantType

from custom_components.pik_comfort.const import (
    CONF_PHONE_NUMBER,
    DATA_FINAL_CONFIG,
    DATA_PLATFORM_ENTITY_REGISTRARS,
    DATA_UPDATE_ROUTINES,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    DATA_ENTITIES,
    SUPPORTED_PLATFORMS,
)
from custom_components.pik_comfort.api import (
    PikComfortAPI,
    PikComfortAccount,
    PikComfortException,
)

_LOGGER = logging.getLogger(__name__)


class BasePikComfortEntity(Entity, ABC):
    def __init__(
        self, config_entry_id: str, account_type: str, account_id: str
    ) -> None:
        self._config_entry_id = config_entry_id
        self._collective_update_future: Optional[asyncio.Future] = None
        self._collective_update_counter: int = 0
        self._available: bool = True

        self.account_type: str = account_type
        self.account_id: str = account_id

    @property
    def account_object(self) -> Optional[PikComfortAccount]:
        info = self.api_object.info

        if info is None:
            return None

        key = (self.account_id, self.account_type)
        for account in info.accounts:
            if (account.id, account.type) == key:
                return account

        return None

    @property
    def device_info(self) -> Dict[str, Any]:
        device_info = {
            "manufacturer": "PIK Comfort",
            "identifiers": {(DOMAIN, self.account_id)},
            "model": "Account",
        }

        account_object = self.account_object

        if account_object is not None:
            device_info["suggested_area"] = account_object.address

            if account_object.has_account_number:
                account_number = account_object.number
                if account_number is not None:
                    device_info["name"] = "№ " + account_number

        return device_info

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

    phone_number = hass.data[DATA_FINAL_CONFIG][config_entry_id][CONF_PHONE_NUMBER]
    log_prefix = f"[{phone_number}] "

    tasks: Dict[str, asyncio.Task] = {}

    for key, value in platform_entity_registrars.items():
        _LOGGER.debug(log_prefix + f"Планирование загрузки платформы {key}")
        tasks[key] = hass.async_create_task(value())

    if tasks:
        await asyncio.wait(tasks.values(), return_when=asyncio.ALL_COMPLETED)

        for domain, task in tasks.items():
            exc = task.exception()
            if exc:
                _LOGGER.error(
                    log_prefix + f"Ошибка загрузки платформы {domain}: {repr(exc)}"
                )
    else:
        _LOGGER.warning(
            log_prefix + "Загрузка платформ не запланирована. "
            "Компонент запущен корректно?"
        )


async def async_setup_entry_for_platforms(
    hass: HomeAssistantType,
    config_entry: ConfigEntry,
    async_process_update: Callable[[], Awaitable[None]],
) -> None:
    platform = entity_platform.async_get_current_platform()

    log_prefix = f"[{config_entry.data[CONF_PHONE_NUMBER]}] "

    if platform is None:
        log_message = "Текущая платформа не получена"
        _LOGGER.error(log_prefix + log_message)
        raise ConfigEntryNotReady(log_message)

    if platform.domain not in SUPPORTED_PLATFORMS:
        log_message = f"Платформа {platform.domain} не входит в список поддерживаемых"
        _LOGGER.error(log_prefix + log_message)
        raise ConfigEntryNotReady(log_message)

    config_entry_id = config_entry.entry_id

    platform_entity_registrars = hass.data[DATA_PLATFORM_ENTITY_REGISTRARS][
        config_entry_id
    ]
    platform_entity_registrars[platform.domain] = async_process_update

    if len(platform_entity_registrars) != len(SUPPORTED_PLATFORMS):
        _LOGGER.debug(log_prefix + "Ожидание загрузки других платформ")
        return None

    _LOGGER.debug(log_prefix + "Планирование распределения загрузки платформ")

    collective_update_future: Optional[asyncio.Future] = None
    collective_update_counter: int = 0
    api_object: PikComfortAPI = hass.data[DOMAIN][config_entry_id]

    async def _async_update_delegator(*_) -> None:
        nonlocal collective_update_future, collective_update_counter

        if collective_update_future:
            # tap into existing future to await update
            await collective_update_future
            return

        _LOGGER.debug(log_prefix + "Выполнение запланированного обновления")

        collective_update_counter += 1
        collective_update_future = hass.loop.create_future()
        collective_update_future = collective_update_future

        # sleep for three seconds to collect simultaneous updates
        await asyncio.sleep(3)

        try:
            await api_object.async_update_info()

        except PikComfortException as update_error:
            _LOGGER.error(
                log_prefix + f"Запланированное обновление не выполнено: {update_error}"
            )
            collective_update_future.set_exception(update_error)
            raise collective_update_future.exception()

        else:
            _LOGGER.debug(log_prefix + "Запланированное обновление выполнено")
            collective_update_future.set_result(None)
            collective_update_future.result()

        finally:
            collective_update_future = None

        # Perform entities updates
        await async_handle_data_update(hass, config_entry_id)

    # Perform initial update with retrieved data
    # (assuming data is available when platforms are set up)
    try:
        await async_handle_data_update(hass, config_entry_id)

    except BaseException as error:
        _LOGGER.error(log_prefix + f"Первичное обновление не выполнено: {error}")

    user_cfg = hass.data[DATA_FINAL_CONFIG][config_entry_id]
    update_interval = timedelta(seconds=user_cfg[CONF_SCAN_INTERVAL])

    _LOGGER.debug(
        log_prefix
        + f"Планирование обновления (интервал: {update_interval.total_seconds()} секунд)"
    )
    hass.data[DATA_UPDATE_ROUTINES][config_entry_id] = (
        _async_update_delegator,
        async_track_time_interval(
            hass,
            _async_update_delegator,
            update_interval,
        ),
    )

    _LOGGER.debug(log_prefix + "Распределение загрузки платформ успешно")
