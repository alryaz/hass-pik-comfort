import logging
import re
from time import time
from typing import Any, Dict, Optional

from homeassistant.config_entries import ConfigFlow

import voluptuous as vol
from homeassistant.const import CONF_TOKEN
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv

from custom_components.pik_comfort import mask_username
from custom_components.pik_comfort.const import CONF_PHONE_NUMBER, DOMAIN
from custom_components.pik_comfort.api import PikComfortAPI, PikComfortException

_LOGGER = logging.getLogger(__name__)


class PikComfortConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 2

    def __init__(self):
        self._api_object: Optional[PikComfortAPI] = None
        self._otp_expires_at: Optional[float] = None
        self._otp_input_schema: Optional[vol.Schema] = None

        self._phone_number: Optional[str] = None

    @callback
    def async_show_user_form(
        self, user_input: Optional[Dict[str, Any]] = None, **kwargs: Any
    ) -> Dict[str, Any]:
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_PHONE_NUMBER,
                        default=(user_input or {}).get(CONF_PHONE_NUMBER),
                    ): cv.string,
                    vol.Optional(CONF_TOKEN): cv.string,
                }
            ),
            **kwargs,
        )

    @staticmethod
    def _format_phone_number(phone_number: str) -> str:
        return (
            f"+{phone_number[0]} ({phone_number[1:4]}) "
            f"{phone_number[4:7]}-{phone_number[7:9]}-"
            f"{phone_number[9:11]}"
        )

    async def async_step_user(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        if user_input is None:
            return self.async_show_user_form()

        phone_number = re.sub(r"\D", "", user_input[CONF_PHONE_NUMBER])

        if len(phone_number) == 10:
            phone_number = "7" + phone_number

        elif len(phone_number) == 11:
            if phone_number[0] != "7":
                phone_number = "7" + phone_number[1:]
        else:
            return self.async_show_user_form(
                user_input, errors={CONF_PHONE_NUMBER: "invalid_digits_count"}
            )

        log_prefix = f"[{mask_username(phone_number)}] "

        auth_token = user_input.get(CONF_TOKEN) or None

        api_object = PikComfortAPI(phone_number, auth_token)

        if api_object.is_authenticated:
            _LOGGER.debug(
                log_prefix
                + f"Попытка авторизации с помощью введённого токена авторизации"
            )
            try:
                await api_object.async_update_data()
            except PikComfortException as error:
                _LOGGER.error(log_prefix + f"Ошибка API: {error}")
                await api_object.async_close()

                return self.async_show_user_form(
                    user_input, errors={CONF_TOKEN: "auth_token_invalid"}
                )
            else:
                _LOGGER.debug(
                    log_prefix
                    + "Создание конфигурационной записи (метод: введённый токен авторизации)"
                )
                return self.async_create_entry(
                    title=self._format_phone_number(phone_number),
                    data={
                        CONF_PHONE_NUMBER: phone_number,
                        CONF_TOKEN: user_input[CONF_TOKEN],
                    },
                )

        self._api_object = api_object

        _LOGGER.debug(log_prefix + "Попытка запроса кода подтверждения СМС")
        try:
            ttl = await api_object.async_request_otp_token()

        except PikComfortException as error:
            _LOGGER.error(log_prefix + f"Ошибка API: {error}")
            await api_object.async_close()
            return self.async_show_user_form(
                user_input,
                errors={"base": "api_error"},
            )

        self._otp_expires_at = time() + ttl
        self._phone_number = phone_number

        return await self.async_step_otp_input()

    async def async_step_otp_input(
        self, user_input: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        if user_input is None:
            if self._otp_input_schema is None:
                self._otp_input_schema = vol.Schema(
                    {
                        vol.Required(CONF_TOKEN): cv.string,
                    }
                )

            return self.async_show_form(
                step_id="otp_input",
                data_schema=self._otp_input_schema,
                description_placeholders={
                    "phone_number": self._format_phone_number(self._phone_number)
                },
            )

        phone_number = self._phone_number
        log_prefix = f"[{mask_username(phone_number)}] "

        if time() >= self._otp_expires_at:
            _LOGGER.error(log_prefix + "Код подтверждения СМС истёк")
            self._otp_expires_at = None

            return self.async_show_user_form(
                {CONF_PHONE_NUMBER: phone_number},
                errors={"base": "otp_token_expired"},
            )

        otp_token = user_input[CONF_TOKEN]

        _LOGGER.debug(log_prefix + "Попытка запроса кода подтверждения СМС")
        try:
            await self._api_object.async_authenticate_otp(otp_token)

        except PikComfortException as error:
            _LOGGER.error(log_prefix + f"Ошибка API: {error}")
            return self.async_show_form(
                user_input,
                errors={CONF_TOKEN: "auth_token_invalid"},
            )

        _LOGGER.debug(
            log_prefix
            + "Создание конфигурационной записи (метод: новый токен авторизации)"
        )
        return self.async_create_entry(
            title=self._format_phone_number(phone_number),
            data={CONF_PHONE_NUMBER: phone_number, CONF_TOKEN: self._api_object.token},
        )
