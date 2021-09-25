import asyncio
import logging
from typing import Any, Dict, Final, Mapping, Optional, Union

import voluptuous as vol
from homeassistant.components import persistent_notification
from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_DEVICE_CLASS, ATTR_ENTITY_ID
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import HomeAssistantType

from custom_components.pik_comfort._base import (
    BasePikComfortEntity,
    async_setup_entry_for_platforms,
)
from custom_components.pik_comfort.api import (
    MeterResourceType,
    PikComfortAPI,
    PikComfortException,
    PikComfortMeter,
)
from custom_components.pik_comfort.const import (
    ATTR_COMMENT,
    ATTR_IGNORE_READINGS,
    ATTR_INCREMENTAL,
    ATTR_NOTIFICATION,
    ATTR_READINGS,
    ATTR_SUCCESS,
    DATA_ENTITIES,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


INDICATIONS_MAPPING_SCHEMA = vol.Schema(
    {
        vol.Required(vol.Match(r"t\d+")): cv.positive_float,
    }
)

INDICATIONS_SEQUENCE_SCHEMA = vol.All(
    vol.Any(vol.All(cv.positive_float, cv.ensure_list), [cv.positive_float]),
    lambda x: dict(map(lambda y: ("t" + str(y[0]), y[1]), enumerate(x, start=1))),
)

SERVICE_PUSH_READINGS: Final = "push_readings"
SERVICE_PUSH_READINGS_SCHEMA = {
    vol.Required(ATTR_READINGS): vol.Any(
        vol.All(
            cv.string,
            lambda x: list(map(str.strip, x.split(","))),
            INDICATIONS_SEQUENCE_SCHEMA,
        ),
        INDICATIONS_MAPPING_SCHEMA,
        INDICATIONS_SEQUENCE_SCHEMA,
    ),
    vol.Optional(ATTR_IGNORE_READINGS, default=False): cv.boolean,
    vol.Optional(ATTR_INCREMENTAL, default=False): cv.boolean,
    vol.Optional(ATTR_NOTIFICATION, default=False): vol.Any(
        cv.boolean,
        persistent_notification.SCHEMA_SERVICE_CREATE,
    ),
}


async def async_process_update(
    hass: HomeAssistantType, config_entry_id: str, async_add_entities
) -> None:
    api_object: PikComfortAPI = hass.data[DOMAIN][config_entry_id]
    entities = hass.data[DATA_ENTITIES][config_entry_id]

    new_entities = []
    remove_tasks = []

    meter_entities = entities.get(PikComfortMeterSensor, [])
    old_meter_entities = list(meter_entities)

    # Process accounts
    for account in api_object.info.accounts:
        account_key = (account.type, account.id)

        # Process meters per account
        for meter in account.meters:
            meter_key = (meter.type, meter.id)
            existing_entity = None

            for entity in meter_entities:
                if (entity.meter_type, entity.meter_id) == meter_key:
                    existing_entity = entity
                    old_meter_entities.remove(existing_entity)
                    break

            if existing_entity is None:
                new_entities.append(
                    PikComfortMeterSensor(config_entry_id, *account_key, *meter_key)
                )
            else:
                existing_entity.async_schedule_update_ha_state(force_refresh=False)

    for entity in old_meter_entities:
        _LOGGER.debug(f"Scheduling entity {entity} for removal")
        remove_tasks.append(hass.async_create_task(entity.async_remove()))

    if new_entities:
        async_add_entities(new_entities, False)

    if remove_tasks:
        await asyncio.wait(remove_tasks, return_when=asyncio.ALL_COMPLETED)


async def async_setup_entry(
    hass: HomeAssistantType, config_entry: ConfigEntry, async_add_entities
) -> bool:
    config_entry_id = config_entry.entry_id

    async def _async_process_update() -> None:
        return await async_process_update(hass, config_entry_id, async_add_entities)

    await async_setup_entry_for_platforms(hass, config_entry, _async_process_update)

    return True


class PikComfortMeterSensor(BasePikComfortEntity, BinarySensorEntity):
    def __init__(
        self,
        config_entry_id: str,
        account_type: str,
        account_id: str,
        meter_type: str,
        meter_id: str,
    ) -> None:
        BasePikComfortEntity.__init__(self, config_entry_id, account_type, account_id)
        BinarySensorEntity.__init__(self)

        self.meter_type: str = meter_type
        self.meter_id: str = meter_id

    @property
    def meter_object(self) -> Optional[PikComfortMeter]:
        info = self.api_object.info

        if info is None:
            return None

        key = (self.meter_type, self.meter_id)
        for account in info.accounts:
            for meter in account.meters:
                if (meter.type, meter.id) == key:
                    return meter

        return None

    @property
    def name(self) -> str:
        meter_object = self.meter_object

        if meter_object is None:
            return f"Meter {self.meter_id}"

        if meter_object.meter_type == MeterResourceType.UNKNOWN:
            type_suffix = "Unknown Type"
        else:
            type_suffix = meter_object.resource_type.name.replace("_", " ").title()

        meter_name = meter_object.user_meter_name
        if not meter_name:
            meter_name = f"№ {meter_object.factory_number}"

        return f"{meter_name} ({type_suffix})"

    @property
    def icon(self) -> str:
        return "mdi:counter"

    @property
    def unique_id(self) -> str:
        meter_object = self.meter_object
        return "meter__" + meter_object.type + "__" + meter_object.id

    # @property
    # def unit_of_measurement(self) -> str:
    #     return self._meter_object.unit_name

    @property
    def is_on(self) -> bool:
        meter_object = self.meter_object
        return meter_object.is_auto or meter_object.has_user_readings

    @property
    def device_class(self) -> str:
        return "pik_comfort_meter"

    @property
    def device_state_attributes(self) -> Mapping[str, Any]:
        meter_object = self.meter_object
        device_state_attributes = {
            ATTR_DEVICE_CLASS: "pik_comfort_meter",
            "has_user_readings": meter_object.has_user_readings,
            "factory_number": meter_object.factory_number,
            "resource_type_id": meter_object.resource_type_id,
            "resource_type": meter_object.resource_type.name.lower(),
        }

        for tariff in sorted(meter_object.tariffs, key=lambda x: x.type):
            for key, value in {
                "value": tariff.value,
                "monthly_average": tariff.average_in_month,
                "submitted_value": tariff.user_value,
                "submitted_at": (
                    tariff.user_value_updated.isoformat()
                    if tariff.user_value_updated
                    else None
                ),
            }.items():
                device_state_attributes[f"tariff_{tariff.type}_{key}"] = value

        device_state_attributes.update(
            {
                "is_auto": meter_object.is_auto,
                "is_individual": meter_object.is_individual,
                "unit_name": meter_object.unit_name,
                "last_period": meter_object.last_period,
                "checkup_status": meter_object.recalibration_status,
                "checkup_date": (
                    meter_object.date_next_recalibration.isoformat()
                    if meter_object.date_next_recalibration
                    else None
                ),
            }
        )

        return device_state_attributes

    @staticmethod
    def get_submit_call_args(
        meter_object: PikComfortMeter, call_data: Mapping
    ) -> Dict[int, float]:
        indications: Mapping[str, Union[int, float]] = call_data[ATTR_READINGS]
        meter_tariffs = meter_object.tariffs
        is_incremental = call_data[ATTR_INCREMENTAL]

        submit_call_args = {}

        for zone_id, new_value in indications.items():
            tariff_type = int(zone_id[1:])
            existing_tariff = None
            for tariff in meter_tariffs:
                if tariff.type == tariff_type:
                    existing_tariff = tariff
                    break

            if existing_tariff is None:
                raise ValueError(f"meter zone {zone_id} does not exist")

            if is_incremental:
                new_value += max(
                    existing_tariff.value or 0.0, existing_tariff.user_value or 0.0
                )

            submit_call_args[tariff_type] = float(new_value)

        return submit_call_args

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        meter_object = self.meter_object

        if not meter_object.is_auto:
            self.platform.async_register_entity_service(
                SERVICE_PUSH_READINGS,
                SERVICE_PUSH_READINGS_SCHEMA,
                "async_service_push_readings",
            )

    def _fire_callback_event(
        self,
        call_data: Mapping[str, Any],
        event_data: Mapping[str, Any],
        event_id: str,
        title: str,
    ):
        meter = self.meter_object
        hass = self.hass

        comment = event_data.get(ATTR_COMMENT)
        message = "Response comment not provided" if comment is None else str(comment)
        meter_number = "<unavailable>" if meter is None else meter.factory_number

        event_data = {
            ATTR_ENTITY_ID: self.entity_id,
            "meter_id": self.meter_id,
            "meter_type": self.meter_type,
            "meter_number": None if meter is None else meter.factory_number,
            "call_params": dict(call_data),
            ATTR_SUCCESS: False,
            ATTR_COMMENT: None,
            **event_data,
        }

        _LOGGER.debug("Firing event '%s' with post_fields: %s" % (event_id, event_data))

        hass.bus.async_fire(event_type=event_id, event_data=event_data)

        notification_content: Union[bool, Mapping[str, str]] = call_data[
            ATTR_NOTIFICATION
        ]

        if notification_content is not False:
            payload = {
                persistent_notification.ATTR_TITLE: title + " - №" + meter_number,
                persistent_notification.ATTR_NOTIFICATION_ID: event_id
                + "_"
                + meter_number,
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

    async def async_service_push_readings(self, **call_data):
        """
        Push indications entity service.
        :param call_data: Parameters for service call
        :return:
        """
        log_prefix = f"[{self.entity_id}] "
        _LOGGER.debug(log_prefix + "Начало обработки передачи показаний")

        meter_object = self.meter_object
        event_data = {ATTR_READINGS: None}

        try:
            if meter_object is None:
                raise Exception("Meter is unavailable")

            submit_call_args = self.get_submit_call_args(meter_object, call_data)

            event_data[ATTR_READINGS] = submit_call_args

            await meter_object.async_submit_readings(submit_call_args)

        except PikComfortException as e:
            event_data[ATTR_COMMENT] = "API error: %s" % e
            raise

        except BaseException as e:
            event_data[ATTR_COMMENT] = "Unknown error: %r" % e
            _LOGGER.error(event_data[ATTR_COMMENT])
            raise

        else:
            event_data[ATTR_COMMENT] = "Indications submitted successfully"
            event_data[ATTR_SUCCESS] = True
            self.async_schedule_update_ha_state(force_refresh=True)

        finally:
            self._fire_callback_event(
                call_data,
                event_data,
                DOMAIN + "_" + SERVICE_PUSH_READINGS,
                "Передача показаний",
            )

            _LOGGER.info(log_prefix + "End handling readings submission")
