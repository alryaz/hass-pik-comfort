import asyncio
import logging
from itertools import chain
from typing import Any, Dict, List, Mapping, Optional, Type, TypeVar

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ATTRIBUTION, STATE_UNAVAILABLE
from homeassistant.helpers.typing import HomeAssistantType

from custom_components.pik_comfort import DOMAIN
from custom_components.pik_comfort._base import (
    BasePikComfortEntity,
    async_setup_entry_for_platforms,
)
from custom_components.pik_comfort.api import (
    PaymentStatus,
    PikComfortAPI,
    PikComfortAccount,
    PikComfortTicket,
    TicketStatus,
)
from custom_components.pik_comfort.const import ATTRIBUTION, DATA_ENTITIES

_TBasePikComfortEntity = TypeVar("_TBasePikComfortEntity", bound=BasePikComfortEntity)


async def async_process_update(
    hass: HomeAssistantType, config_entry_id: str, async_add_entities
) -> None:
    api_object: PikComfortAPI = hass.data[DOMAIN][config_entry_id]

    new_entities = []
    remove_tasks = []

    # Retrieve entities with their types
    entities: Dict[
        Type[_TBasePikComfortEntity], List[_TBasePikComfortEntity]
    ] = hass.data[DATA_ENTITIES][config_entry_id]

    last_payment_entities = entities.get(PikComfortLastPaymentSensor, [])
    old_last_payment_entities = list(last_payment_entities)

    last_receipt_entities = entities.get(PikComfortLastReceiptSensor, [])
    old_last_receipt_entities = list(last_receipt_entities)

    ticket_entities = entities.get(PikComfortTicketSensor, [])
    old_ticket_entities = list(ticket_entities)

    # Process accounts
    for account in api_object.user_data.accounts:
        # Process last payment per account
        key = (account.type, account.uid)
        existing_entity = None

        for entity in last_payment_entities:
            if (entity.account_type, entity.account_uid) == key:
                existing_entity = entity
                old_last_payment_entities.remove(entity)
                break

        if existing_entity is None:
            new_entities.append(PikComfortLastPaymentSensor(config_entry_id, *key))
        else:
            existing_entity.async_schedule_update_ha_state(force_refresh=False)

        # Process last receipt per account
        # key is the same
        existing_entity = None
        for entity in last_receipt_entities:
            if (entity.account_type, entity.account_uid) == key:
                existing_entity = entity
                old_last_receipt_entities.remove(entity)
                break

        if existing_entity is None:
            new_entities.append(PikComfortLastReceiptSensor(config_entry_id, *key))
        else:
            existing_entity.async_schedule_update_ha_state(force_refresh=False)

        # Process tickets per account
        for ticket in account.tickets:
            key = (ticket.type, ticket.uid)
            existing_entity = None

            for entity in ticket_entities:
                if (entity.ticket_type, entity.ticket_uid) == key:
                    existing_entity = entity
                    old_ticket_entities.remove(existing_entity)
                    break

            if existing_entity is None:
                new_entities.append(PikComfortTicketSensor(config_entry_id, *key))
            else:
                existing_entity.async_schedule_update_ha_state(force_refresh=False)

    for entity in chain(
        old_ticket_entities,
        old_last_payment_entities,
        old_last_receipt_entities,
    ):
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


_LOGGER = logging.getLogger(__name__)


class _PikComfortAccountSensor(BasePikComfortEntity):
    def __init__(
        self, config_entry_id: str, account_type: str, account_uid: str
    ) -> None:
        super().__init__(config_entry_id)

        self.account_type: str = account_type
        self.account_uid: str = account_uid

    @property
    def _account_object(self) -> Optional[PikComfortAccount]:
        user_data = self.api_object.user_data

        if user_data is None:
            return None

        key = (self.account_uid, self.account_type)
        for account in user_data.accounts:
            if (account.uid, account.type) == key:
                return account

        return None


class PikComfortLastPaymentSensor(_PikComfortAccountSensor):
    @property
    def icon(self) -> str:
        account_object = self._account_object

        if account_object is not None:
            last_payment = account_object.last_payment

            if last_payment is not None:
                if last_payment.status == PaymentStatus.ACCEPTED:
                    return "mdi:cash-check"
                elif last_payment.status == PaymentStatus.DECLINED:
                    return "mdi:cash-remove"

        return "mdi:cash"

    @property
    def name(self) -> str:
        account_object = self._account_object
        if account_object is None:
            return f"Last Payment {self.account_uid}"

        return f"Last Payment {account_object.number or account_object.premise_number or account_object.uid}"

    @property
    def unique_id(self) -> str:
        return f"last_payment__{self.account_type}__{self.account_uid}"

    @property
    def available(self) -> bool:
        account_object = self._account_object
        return bool(account_object and account_object.last_payment)

    @property
    def state(self) -> str:
        last_payment = self._account_object.last_payment
        if last_payment is None:
            return STATE_UNAVAILABLE

        return last_payment.status.name.lower()

    @property
    def device_class(self) -> str:
        return "pik_comfort_last_payment"

    @property
    def device_state_attributes(self) -> Optional[Mapping[str, Any]]:
        last_payment = self._account_object.last_payment

        if last_payment is None:
            return None

        account_object = self._account_object

        return {
            "amount": last_payment.amount,
            "status_id": last_payment.status_id,
            "check_url": last_payment.check_url,
            "bank_id": last_payment.bank_id,
            "timestamp": last_payment.timestamp.isoformat(),
            "payment_type": last_payment.payment_type,
            "source_name": last_payment.source_name,
            "uid": last_payment.uid,
            "type": last_payment.type,
            "account_uid": account_object.uid,
            "account_type": account_object.type,
            "account_number": account_object.number,
            ATTR_ATTRIBUTION: ATTRIBUTION,
        }


class PikComfortTicketSensor(BasePikComfortEntity):
    def __init__(self, config_entry_id: str, ticket_type: str, ticket_uid: str) -> None:
        super().__init__(config_entry_id)

        self.ticket_type: str = ticket_type
        self.ticket_uid: str = ticket_uid

    @property
    def _ticket_object(self) -> Optional[PikComfortTicket]:
        user_data = self.api_object.user_data
        if not user_data:
            return None

        key = (self.ticket_type, self.ticket_uid)
        for account in user_data.accounts:
            for ticket in account.tickets:
                if (ticket.type, ticket.uid) == key:
                    return ticket

        return None

    @property
    def _account_object(self) -> Optional[PikComfortAccount]:
        user_data = self.api_object.user_data
        if not user_data:
            return None

        key = (self.ticket_type, self.ticket_uid)
        for account in user_data.accounts:
            for ticket in account.tickets:
                if (ticket.type, ticket.uid) == key:
                    return account

        return None

    @property
    def unique_id(self) -> str:
        return f"ticket__{self.ticket_type}__{self.ticket_uid}"

    @property
    def available(self) -> bool:
        return bool(self._ticket_object)

    @property
    def device_class(self) -> str:
        return "pik_comfort_ticket"

    @property
    def icon(self) -> str:
        ticket_object = self._ticket_object

        suffix = ""
        if ticket_object is not None:
            if ticket_object.is_viewed:
                suffix = "-outline"

            status = ticket_object.status
            if status == TicketStatus.RECEIVED:
                return "mdi:comment-processing" + suffix
            elif status == TicketStatus.DENIED:
                return "mdi:comment-remove" + suffix
            elif status == TicketStatus.PROCESSING:
                return "mdi:comment-arrow-right" + suffix
            elif status == TicketStatus.COMPLETED:
                return "mdi:comment-check" + suffix
            elif status == TicketStatus.UNKNOWN:
                return "mdi:comment-question" + suffix

        return "mdi:chat" + suffix

    @property
    def name(self) -> str:
        ticket_object = self._ticket_object
        ticket_id = self.ticket_uid if ticket_object is None else ticket_object.number
        return f"Ticket №{ticket_id}"

    @property
    def state(self) -> str:
        ticket_object = self._ticket_object

        if ticket_object is None:
            return STATE_UNAVAILABLE

        return ticket_object.status.name.lower()

    @property
    def device_state_attributes(self) -> Optional[Mapping[str, Any]]:
        ticket_object = self._ticket_object

        if ticket_object is None:
            return None

        account_object = self._account_object

        return {
            "number": ticket_object.number,
            "description": ticket_object.description,
            "created": ticket_object.created.isoformat(),
            "updated": ticket_object.updated.isoformat(),
            "last_status_changed": ticket_object.last_status_changed.isoformat(),
            "is_viewed": ticket_object.is_viewed,
            "is_commentable": ticket_object.is_commentable,
            "is_liked": ticket_object.is_liked,
            "comments_count": len(ticket_object.comments),
            "attachments_count": len(ticket_object.attachments),
            "account_uid": account_object.uid,
            "account_type": account_object.type,
            "account_number": account_object.number,
            ATTR_ATTRIBUTION: ATTRIBUTION,
        }


class PikComfortLastReceiptSensor(_PikComfortAccountSensor):
    @property
    def icon(self) -> str:
        account_object = self._account_object

        if account_object is not None:
            last_receipt = account_object.last_receipt

            if last_receipt is not None:
                if (last_receipt.paid or 0.0) >= last_receipt.total:
                    return "mdi:text-box-check"

        return "mdi:text-box"

    @property
    def unit_of_measurement(self) -> str:
        return "RUB"

    @property
    def name(self) -> str:
        account_object = self._account_object
        if account_object is None:
            return f"Last Receipt {self.account_uid}"

        account_id = (
            account_object.number or account_object.premise_number or account_object.uid
        )
        return f"Last Receipt {account_id}"

    @property
    def unique_id(self) -> str:
        return f"last_receipt__{self.account_type}__{self.account_uid}"

    @property
    def available(self) -> bool:
        account_object = self._account_object
        return bool(account_object and account_object.last_receipt)

    @property
    def state(self) -> float:
        last_receipt = self._account_object.last_receipt

        if last_receipt is None:
            return STATE_UNAVAILABLE

        return last_receipt.total - (last_receipt.paid or 0.0)

    @property
    def device_class(self) -> str:
        return "monetary"

    @property
    def device_state_attributes(self) -> Optional[Mapping[str, Any]]:
        last_receipt = self._account_object.last_receipt

        if last_receipt is None:
            return None

        account_object = self._account_object

        return {
            "type": last_receipt.type,
            "period": last_receipt.period.isoformat(),
            "charge": last_receipt.charge,
            "corrections": last_receipt.corrections,
            "payment": last_receipt.payment,
            "initial": last_receipt.initial,
            "subsidy": last_receipt.subsidy,
            "total": last_receipt.total,
            "penalty": last_receipt.penalty,
            # "contents": last_receipt.contents,
            "paid": last_receipt.paid or 0.0,
            "debt": last_receipt.debt or 0.0,
            "account_uid": account_object.uid,
            "account_type": account_object.type,
            "account_number": account_object.number,
            ATTR_ATTRIBUTION: ATTRIBUTION,
        }
