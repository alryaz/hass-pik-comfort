import logging
import re
from time import time
from typing import Any, Dict, Optional

from homeassistant.config_entries import ConfigFlow

import voluptuous as vol
from homeassistant.const import CONF_TOKEN, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv

from custom_components.pik_comfort.const import DOMAIN
from custom_components.pik_comfort.api import PikComfortAPI, PikComfortException

_LOGGER = logging.getLogger(__name__)


class PikComfortConfigFlow(ConfigFlow, domain=DOMAIN):
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
                        CONF_USERNAME, default=(user_input or {}).get(CONF_USERNAME)
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

        phone_number = re.sub(r"\D", "", user_input[CONF_USERNAME])

        if len(phone_number) == 10:
            phone_number = "7" + phone_number

        elif len(phone_number) == 11:
            if phone_number[0] != "7":
                phone_number = "7" + phone_number[1:]
        else:
            return self.async_show_user_form(
                user_input, errors={CONF_USERNAME: "invalid_digits_count"}
            )

        api_object = PikComfortAPI(phone_number, user_input.get(CONF_TOKEN))

        if api_object.is_authenticated:
            try:
                await api_object.async_update_data()
            except PikComfortException as error:
                _LOGGER.error(f"Ошибка API: {error}")
                await api_object.async_close()

                return self.async_show_user_form(
                    user_input, errors={CONF_TOKEN: "token_invalid"}
                )
            else:
                return self.async_create_entry(
                    title=self._format_phone_number(phone_number),
                    data={
                        CONF_USERNAME: phone_number,
                        CONF_TOKEN: user_input[CONF_TOKEN],
                    },
                )

        self._api_object = api_object

        try:
            ttl = await api_object.async_request_otp_token()

        except PikComfortException as error:
            _LOGGER.debug(f"API error: {error}")
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
                        vol.Required(CONF_TOKEN): vol.All(
                            vol.Match(r"\d{6}"), cv.string
                        ),
                    }
                )

            return self.async_show_form(
                step_id="otp_input", data_schema=self._otp_input_schema
            )

        phone_number = self._phone_number
        if time() >= self._otp_expires_at:
            self._otp_expires_at = None
            return self.async_show_user_form(
                {CONF_USERNAME: phone_number},
                errors={"base": "token_expired"},
            )

        otp_token = user_input[CONF_TOKEN]

        try:
            await self._api_object.async_authenticate_otp(otp_token)

        except PikComfortException as error:
            _LOGGER.debug(f"API error: {error}")
            return self.async_show_form(
                user_input,
                errors={CONF_TOKEN: "token_invalid"},
            )

        return self.async_create_entry(
            title=self._format_phone_number(phone_number),
            data={CONF_USERNAME: phone_number, CONF_TOKEN: self._api_object.token},
        )
