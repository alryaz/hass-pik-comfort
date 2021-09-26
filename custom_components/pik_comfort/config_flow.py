import logging
import re
from abc import ABC, abstractmethod
from collections import ChainMap
from datetime import datetime
from time import time
from typing import Any, ClassVar, Dict, Final, Optional, Tuple

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.const import CONF_BASE, CONF_SCAN_INTERVAL, CONF_TOKEN
from homeassistant.data_entry_flow import FlowHandler
from homeassistant.helpers import config_validation as cv
from homeassistant.util.dt import as_local

from custom_components.pik_comfort import mask_username
from custom_components.pik_comfort.api import (
    PikComfortAPI,
    PikComfortException,
    RequestError,
    ServerError,
    get_random_device_name,
)
from custom_components.pik_comfort.const import (
    CONF_DEVICE_NAME,
    CONF_PHONE_NUMBER,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MIN_SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

CONF_REQUEST_NEW_TOKEN: Final = "request_new_token"
CONF_REQUEST_NEW_OTP_CODE: Final = "request_new_otp_code"
CONF_OTP_CODE: Final = "otp_code"


def _format_phone_number(phone_number: str) -> str:
    return (
        f"+{phone_number[0]} ({phone_number[1:4]}) "
        f"{phone_number[4:7]}-{phone_number[7:9]}-"
        f"{phone_number[9:11]}"
    )


def _handle_exception(
    phone_number: str, error: BaseException
) -> Optional[Tuple[str, str, Optional[str]]]:
    """Handle exception

    :param error:
    :return: (Intl error code, error message, error code)
    """
    log_prefix = f"[{mask_username(phone_number)}] "

    if isinstance(error, ServerError):
        error_code = error.error_code
        error_message = error.error_message
        log_message = f"Ошибка сервера ({error_code}): {error_message}"
        intl_error_code = "server_error"
    else:
        error_code = None
        error_message = str(error)
        if isinstance(error, RequestError):
            log_message = f"Ошибка запроса: {error_message}"
            intl_error_code = "request_error"
        elif isinstance(error, PikComfortException):
            log_message = f"Ошибка API: {error_message}"
            intl_error_code = "api_error"
        else:
            log_message = f"Неизвестная ошибка: {error_message}"
            intl_error_code = "unknown_error"

    _LOGGER.exception(log_prefix + log_message, exc_info=error)
    return intl_error_code, error_message, error_code


class _WithOTPInput(FlowHandler, ABC):
    def __init__(self) -> None:
        self._device_name: str = get_random_device_name()
        self._phone_number: Optional[str] = None
        self._auth_token: Optional[str] = None
        self._otp_expires_at: Optional[float] = None

    @abstractmethod
    def _create_entry(self) -> Dict[str, Any]:
        raise NotImplementedError

    async def async_step_otp_input(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        errors = {}
        error_code = None
        error_message = None
        phone_number = self._phone_number
        otp_expires_at = self._otp_expires_at

        if user_input:
            request_new_otp_code = user_input.get(CONF_REQUEST_NEW_OTP_CODE)
            if request_new_otp_code or time() < otp_expires_at:
                try:
                    async with PikComfortAPI(
                        username=phone_number,
                        device_name=self._device_name,
                    ) as api_object:
                        if request_new_otp_code:
                            await self._async_request_otp_code(api_object)
                        else:
                            await api_object.async_authenticate_otp(
                                user_input[CONF_TOKEN]
                            )
                            self._auth_token = api_object.token
                            return self._create_entry()
                except BaseException as error:
                    intl_error_string, error_message, error_code = _handle_exception(
                        phone_number, error
                    )
                    if intl_error_string == "server_error" and error_code == "invalid":
                        errors[CONF_TOKEN] = "otp_token_invalid"
                    else:
                        errors[CONF_BASE] = intl_error_string
            else:
                errors[CONF_TOKEN] = "otp_token_expired"

        return self.async_show_form(
            step_id="otp_input",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_OTP_CODE): cv.string,
                    vol.Optional(CONF_REQUEST_NEW_OTP_CODE, default=False): cv.boolean,
                }
            ),
            description_placeholders={
                "will_expire_at": as_local(
                    datetime.fromtimestamp(otp_expires_at)
                ).isoformat(),
                "phone_number": _format_phone_number(phone_number),
                "error_code": error_code or "<?>",
                "error_message": error_message or "<?>",
            },
            errors=errors,
        )

    async def _async_request_otp_code(self, api_object: PikComfortAPI) -> None:
        ttl = await api_object.async_request_otp_code()
        self._otp_expires_at = time() + ttl

    async def _async_test_authentication(self) -> Dict[str, Any]:
        phone_number, auth_token = self._phone_number, self._auth_token
        log_prefix = f"[{mask_username(phone_number)}] "

        async with PikComfortAPI(username=phone_number, token=auth_token) as api_object:
            if not api_object.is_authenticated:
                _LOGGER.debug(
                    f"[{mask_username(phone_number)}] "
                    f"Попытка запроса кода подтверждения СМС"
                )

                await self._async_request_otp_code(api_object)
                return await self.async_step_otp_input()

            _LOGGER.debug(
                log_prefix + "Попытка авторизации с помощью "
                "введённого токена авторизации"
            )
            await api_object.async_update_info()

        _LOGGER.debug(log_prefix + "Авторизация успешна, сохранение данных")

        return self._create_entry()


class PikComfortConfigFlow(ConfigFlow, _WithOTPInput, domain=DOMAIN):
    """Configuration flow for the `pik_comfort` integration"""

    VERSION: ClassVar[int] = 4

    def __init__(self) -> None:
        super().__init__()

        self._api_object: Optional[PikComfortAPI] = None

    def _create_entry(self) -> Dict[str, Any]:
        phone_number, auth_token = self._phone_number, self._auth_token
        assert auth_token is not None, "Auth token not filled"
        assert phone_number is not None, "Phone number not filled"

        return self.async_create_entry(
            title=_format_phone_number(phone_number),
            data={
                CONF_PHONE_NUMBER: phone_number,
                CONF_TOKEN: auth_token,
                CONF_DEVICE_NAME: self._device_name,
                CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
            },
        )

    async def async_step_user(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        errors = {}
        error_code = None
        error_message = None

        if user_input:
            phone_number = re.sub(r"\D", "", user_input[CONF_PHONE_NUMBER])

            if len(phone_number) == 13 and phone_number[:2] == "00":
                phone_number = phone_number[2:]

            if len(phone_number) == 10:
                phone_number = "7" + phone_number
            elif len(phone_number) == 11 and phone_number[0] == "8":
                phone_number = "7" + phone_number[1:]

            if len(phone_number) != 11 or phone_number[0] != "7":
                _LOGGER.error(f"Неправильный номер телефона: {phone_number}")
                errors[CONF_PHONE_NUMBER] = "phone_number_invalid"
            else:
                self._device_name = user_input[CONF_DEVICE_NAME]
                self._auth_token = (user_input.get(CONF_TOKEN) or "").strip() or None
                self._phone_number = phone_number

                try:
                    return await self._async_test_authentication()
                except BaseException as error:
                    errors[CONF_BASE], error_message, error_code = _handle_exception(
                        phone_number, error
                    )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_PHONE_NUMBER,
                        default=(user_input or {}).get(CONF_PHONE_NUMBER),
                    ): cv.string,
                    vol.Optional(CONF_TOKEN): str,
                    vol.Required(
                        CONF_DEVICE_NAME,
                        default=self._device_name,
                    ): vol.All(cv.string, vol.Length(min=3)),
                }
            ),
            errors=errors,
            description_placeholders={
                "error_code": error_code,
                "error_message": error_message,
            },
        )

    @staticmethod
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> "PikComfortOptionsFlow":
        return PikComfortOptionsFlow(config_entry)


class PikComfortOptionsFlow(OptionsFlow, _WithOTPInput):
    def __init__(self, config_entry: ConfigEntry) -> None:
        super().__init__()

        data = ChainMap(config_entry.data, config_entry.options)

        self._device_name: str = data[CONF_DEVICE_NAME]
        self._phone_number: str = data[CONF_PHONE_NUMBER]
        self._auth_token: str = data[CONF_TOKEN]
        self._scan_interval: float = data[CONF_SCAN_INTERVAL]

    def _create_entry(self) -> Dict[str, Any]:
        return self.async_create_entry(
            title="",
            data={
                CONF_DEVICE_NAME: self._device_name,
                CONF_TOKEN: self._auth_token,
                CONF_SCAN_INTERVAL: self._scan_interval,
            },
        )

    async def async_step_init(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        errors = {}
        description_placeholders = {}

        if user_input:
            auth_token = user_input[CONF_TOKEN]

            self._scan_interval = user_input[CONF_SCAN_INTERVAL].total_seconds()
            self._device_name = user_input[CONF_DEVICE_NAME]
            self._auth_token = (
                None if user_input.get(CONF_REQUEST_NEW_TOKEN) else auth_token
            )

            if self._scan_interval < MIN_SCAN_INTERVAL:
                errors[CONF_SCAN_INTERVAL] = "scan_interval_too_low"
                description_placeholders["min_scan_interval"] = MIN_SCAN_INTERVAL

            else:

                try:
                    return await self._async_test_authentication()
                except BaseException as error:
                    (
                        errors[CONF_BASE],
                        description_placeholders["error_message"],
                        description_placeholders["error_code"],
                    ) = _handle_exception(self._phone_number, error)

            self._auth_token = auth_token
        else:
            auth_token = self._auth_token

        scan_interval = self._scan_interval
        hours = scan_interval // 3600
        minutes = (scan_interval % 3600) // 60
        seconds = scan_interval % 60

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_TOKEN, default=auth_token): cv.string,
                    vol.Required(CONF_DEVICE_NAME, default=self._device_name): vol.All(
                        cv.string, vol.Length(min=3)
                    ),
                    vol.Optional(
                        CONF_SCAN_INTERVAL,
                        default={
                            "hours": hours,
                            "minutes": minutes,
                            "seconds": seconds,
                        },
                    ): cv.positive_time_period_dict,
                    vol.Optional(CONF_REQUEST_NEW_TOKEN, default=False): cv.boolean,
                }
            ),
            errors=errors,
            description_placeholders=description_placeholders,
        )
