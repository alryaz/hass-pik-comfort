import asyncio
import logging
import random
import string
from abc import ABC, abstractmethod
from datetime import date, datetime
from enum import IntEnum
from typing import (
    Any,
    ClassVar,
    Final,
    Iterable,
    List,
    Mapping,
    Optional,
    Set,
    Tuple,
    Type,
    TypeVar,
    Union,
)

import aiohttp
import attr
from multidict import CIMultiDict
from pytz import timezone

_LOGGER = logging.getLogger(__name__)


DEFAULT_SDK_VERSION: Final = 30
DEFAULT_VERSION_NAME: Final = "1.10.0"
DEFAULT_VERSION_CODE: Final = 81

MOSCOW_TIMEZONE: Final = timezone("Europe/Moscow")


def get_random_device_name() -> str:
    return "".join(
        random.choices(string.ascii_uppercase + string.digits, k=random.randint(4, 8))
    )


class PikComfortException(Exception):
    pass


class RequestError(PikComfortException):
    def __str__(self) -> str:
        return self.args[0]


class ServerError(RequestError):
    @property
    def error_code(self) -> str:
        return self.args[3]

    @property
    def error_message(self) -> Optional[str]:
        return self.args[4]


class PikComfortAPI:
    ORIGIN_PIK_URL: ClassVar[str] = "https://new.pik-comfort.ru/"
    BASE_PIK_URL: ClassVar[str] = "https://new-api.pik-software.ru"

    def __init__(
        self,
        username: Optional[str] = None,
        token: Optional[str] = None,
        authentication_ttl: int = 31536000,
        *,
        device_name: Optional[str] = None,
        version_name: str = DEFAULT_VERSION_NAME,
        version_code: int = DEFAULT_VERSION_CODE,
        sdk_version: int = DEFAULT_SDK_VERSION,
    ) -> None:
        self.username = username
        self.token = token
        self._authentication_ttl = authentication_ttl

        self.sdk_version = sdk_version
        self.device_name = device_name or get_random_device_name()
        self.version_name = version_name
        self.version_code = version_code

        self._info: Optional[InfoResult] = None

        self._user_id: Optional[str] = None
        self._classifiers: Optional[List[TicketClassifier]] = None

        self._session = aiohttp.ClientSession(
            headers={
                "X-Source": "Android",
                aiohttp.hdrs.USER_AGENT: "okhttp/4.4.1",
                "Origin": self.ORIGIN_PIK_URL,
            }
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self._session.__aexit__(exc_type, exc_val, exc_tb)

    def __repr__(self) -> str:
        return (
            f"<{self.__class__.__name__}("
            f"username={repr(self.username)}, "
            f"is_authenticated={self.is_authenticated}"
            f">"
        )

    def __str__(self) -> str:
        return f"{self.__class__.__name__}({self.username}, {self.is_authenticated})"

    @property
    def session(self) -> aiohttp.ClientSession:
        return self._session

    @property
    def classifiers(self) -> Optional[List["TicketClassifier"]]:
        return self._classifiers

    @property
    def is_authenticated(self) -> bool:
        return self.token is not None

    @property
    def info(self) -> Optional["InfoResult"]:
        return self._info

    async def async_close(self) -> None:
        await self._session.close()

    async def async_request(
        self,
        sub_url: str,
        *,
        authenticated: Optional[bool] = None,
        action_title: Optional[str] = None,
        expected_status: Optional[int] = 200,
        method: Optional[str] = None,
        **kwargs,
    ):
        full_url = self.BASE_PIK_URL + sub_url

        if action_title is None:
            action_title = f"request to {full_url}"

        if authenticated is not False:
            if self.is_authenticated:
                headers = CIMultiDict(
                    {aiohttp.hdrs.AUTHORIZATION: "Token " + self.token}
                )
                if "headers" in kwargs:
                    headers.update(kwargs["headers"])
                kwargs["headers"] = headers
            elif authenticated is True:
                _LOGGER.error(
                    f"[{self}] Account is not authenticated during {action_title}"
                )
                raise PikComfortException("Account is not authenticated")

        if method is None:
            if "data" in kwargs or "json" in kwargs:
                method = aiohttp.hdrs.METH_POST
            else:
                method = aiohttp.hdrs.METH_GET

        response_status = None
        response_data = None
        _LOGGER.debug(
            f"[{self}] Performing {action_title} ({method} request to: {full_url})"
        )
        try:
            # noinspection PyArgumentList
            async with self._session.request(method, full_url, **kwargs) as response:
                response_status = response.status
                if expected_status is not None and response_status != expected_status:
                    try:
                        response_contents = await response.json()
                    except aiohttp.ClientError:
                        response_contents = await response.text()
                    else:
                        try:
                            error_code = response_contents["code"]
                        except KeyError:
                            _LOGGER.error(
                                f"[{self}] Unexpected response while performing "
                                f"{action_title} ({response_status})"
                            )
                            raise RequestError(
                                "Unexpected response",
                                response_status,
                                response_contents,
                            )

                        else:
                            error_message = response_contents.get("message")
                            _LOGGER.error(
                                f"[{self}] Server error while performing "
                                f"{action_title} (code: {error_code}): {error_message}"
                            )
                            raise ServerError(
                                "Server error",
                                response_status,
                                response_contents,
                                error_code,
                                error_message,
                            )

                    _LOGGER.error(
                        f"[{self}] Error performing {action_title} "
                        f"({response.status}): {response_contents}"
                    )

                response_data = await response.json()

        except aiohttp.ClientError as error:
            _LOGGER.error(f"[{self}] Error performing {action_title}: {error}")
            raise RequestError("Client error", response_status, response_data)

        except asyncio.TimeoutError:
            _LOGGER.error(
                f"[{self}] Timeout performing {action_title} "
                f"(total timeout: {self._session.timeout.total})"
            )
            raise RequestError("Timeout error", None, None)

        response_data_str = str(response_data)
        if len(response_data_str) > 150:
            response_data_str = response_data_str[:147] + "..."

        _LOGGER.debug(
            f"[{self}] Received response after {action_title}: {response_data_str}"
        )

        return response_data

    async def async_request_otp_code(self) -> int:
        if self.username is None:
            raise PikComfortException("Username is not set")

        response_data = await self.async_request(
            "/request-sms-password/",
            data={"phone": self.username},
            action_title="OTP token request",
            authenticated=False,
        )

        try:
            return response_data["ttl"]
        except KeyError:
            _LOGGER.error(
                f"[{self}] Response does not contain TTL details: {response_data}"
            )
            raise PikComfortException("Response does not contain TTL details")

    async def async_authenticate_otp(self, otp_token: str) -> None:
        if self.username is None:
            raise PikComfortException("Username is not set")

        resp_data = await self.async_request(
            "/api-token-auth/",
            data={
                "username": self.username,
                "password": otp_token,
                "ttl": self._authentication_ttl,
            },
            action_title=f"authentication with OTP token ({otp_token})",
            authenticated=False,
        )

        try:
            user_id = resp_data["user"]
            token = resp_data["token"]
        except (KeyError, TypeError) as error:
            _LOGGER.error(
                f"[{self}] Could not extract user/token information "
                f"({error}): {resp_data}"
            )
            raise PikComfortException("Did not retrieve user/token information")
        else:
            self._user_id = user_id
            self.token = token

    async def async_update_info(self) -> "InfoResult":
        response_data = await self.async_request(
            "/api/v8/aggregate/dashboard-list/",
            params={"tickets_size": "10"},
            action_title="data retrieval",
            authenticated=True,
        )

        data_count = response_data.get("count") or 0
        if data_count < 1:
            _LOGGER.error(f"[{self}] Retrieve data does not contain user information")
            raise PikComfortException("Could not retrieve user information")
        elif data_count > 1:
            _LOGGER.warning(
                f"[{self}] Received more than one response for data retrieval"
            )

        info_data = next(iter(response_data["results"]))
        info_object = self._info

        if info_object is None:
            info_object = InfoResult.create_from_json(info_data, self)
            self._info = info_object
        else:
            info_object.update_from_json(info_data)

        return info_object

    async def async_update_classifiers(self) -> List["TicketClassifier"]:
        response_data = await self.async_request(
            "/api/v3/classifier-list/",
            params={"page_size": 500},
            action_title="classifiers retrieval",
            authenticated=True,
        )

        current_classifiers = self._classifiers
        if current_classifiers is None:
            current_classifiers = TicketClassifier.create_from_json_list(
                response_data["results"], self
            )
            self._classifiers = current_classifiers
        else:
            TicketClassifier.update_list_with_models(
                current_classifiers, response_data["results"], self
            )

        return current_classifiers

    async def async_create_ticket(
        self,
        classifier_id: str,
        description: str,
        account_id: Optional[str] = None,
        *,
        check_account: bool = True,
        check_classifier: bool = True,
    ):
        if account_id is None or check_account:
            info = self.info
            if info is None:
                raise PikComfortException("Information must be updated")

            accounts = info.accounts
            if account_id is None and len(accounts) > 1:
                raise PikComfortException("More than one account to guess")
            elif not accounts:
                raise PikComfortException("No account to derive ID from")
            if account_id is None:
                account_id = next(iter(accounts)).id
            else:
                found_account = None
                for account in info.accounts:
                    if account.id == account_id:
                        found_account = account
                        break

                if found_account is None:
                    raise PikComfortException("No matching account within info")

        if check_classifier:
            classifiers = self._classifiers
            if classifiers is None:
                raise PikComfortException("Classifiers must be updated")

            found_classifier = None
            for classifier in classifiers:
                if classifier.id == classifier_id:
                    found_classifier = classifier
                    break

            if found_classifier is None:
                raise PikComfortException("Classifier was not found")

            children = found_classifier.children
            if children:
                _LOGGER.error(
                    f'Classifier with ID "{found_classifier.id}" contains '
                    f"{len(children)} children. Requests are expected "
                    f"to be made using classifiers without children."
                )
                raise PikComfortException("Classifier contains children")

        moscow_time = datetime.now(tz=MOSCOW_TIMEZONE)
        return PikComfortTicket.create_from_json(
            await self.async_request(
                "/api/v3/ticket-list/",
                data={
                    "classifier_id": classifier_id,
                    "technical_data": (
                        f"MobileApp, "
                        f"Android SDK {self.sdk_version}, "
                        f"{self.device_name}, "
                        f"version name: {self.version_name} "
                        f"version code: {self.version_code}, "
                        f"{moscow_time.strftime('%d.%m.%Y %H:%M:%S')} "
                        f"Москва, стандартное время"
                    ),
                    "account_id": account_id,
                    "description": description,
                },
            ),
            self,
        )


@attr.s(slots=True)
class _BaseModel(ABC):
    api: PikComfortAPI = attr.ib(repr=False)

    @classmethod
    @abstractmethod
    def create_from_json(cls, json_data: Mapping[str, Any], api_object: PikComfortAPI):
        raise NotImplementedError

    @abstractmethod
    def update_from_json(self, json_data: Mapping[str, Any]) -> None:
        raise NotImplementedError

    @classmethod
    def create_from_json_list(
        cls, json_data_list: Iterable[Mapping[str, Any]], api_object: PikComfortAPI
    ):
        return [
            cls.create_from_json(json_data, api_object) for json_data in json_data_list
        ]


_T = TypeVar("_T")


@attr.s(slots=True)
class _BaseIdentifiableModel(_BaseModel, ABC):
    id: str = attr.ib()
    type: str = attr.ib()

    @classmethod
    def update_list_with_models(
        cls: Type[_T],
        target_list: List[_T],
        json_data_list: List[Mapping[str, Any]],
        api_object: PikComfortAPI,
    ) -> None:
        cleanup: Set[Tuple[str, str]] = set()
        for item_data in json_data_list:
            object_id, object_type = item_data["_uid"], item_data["_type"]
            cleanup.add((object_id, object_type))
            current_object = None

            for existing_object in target_list:
                if (
                    existing_object.type == object_type
                    and existing_object.id == object_id
                ):
                    current_object = existing_object
                    break

            if current_object is None:
                target_list.append(cls.create_from_json(item_data, api_object))

            else:
                current_object.update_from_json(item_data)

        for current_object in tuple(reversed(target_list)):
            if (current_object.id, current_object.type) not in cleanup:
                target_list.remove(current_object)
            else:
                cleanup.remove((current_object.id, current_object.type))


@attr.s(slots=True)
class InfoResult(_BaseIdentifiableModel):
    phone: str = attr.ib()
    gender: str = attr.ib()
    first_name: str = attr.ib()
    middle_name: str = attr.ib()
    last_name: str = attr.ib()
    snils: Optional[str] = attr.ib()
    passport_type: Optional[str] = attr.ib()
    passport_number: Optional[str] = attr.ib()
    birth_date: str = attr.ib()
    email: Optional[str] = attr.ib()
    email_verified: bool = attr.ib()
    accounts: List["PikComfortAccount"] = attr.ib()
    completed_tutorials: List[str] = attr.ib()
    hot_categories: List["HotCategory"] = attr.ib()

    @classmethod
    def create_from_json(cls, json_data: Mapping[str, Any], api_object: PikComfortAPI):
        accounts = PikComfortAccount.create_from_json_list(
            json_data["accounts"], api_object
        )

        return cls(
            api=api_object,
            id=json_data["_uid"],
            type=json_data["_type"],
            phone=json_data["phone"],
            gender=json_data["gender"],
            first_name=json_data["first_name"],
            middle_name=json_data["middle_name"],
            last_name=json_data["last_name"],
            snils=json_data["snils"],
            passport_type=json_data["passport_type"],
            passport_number=json_data["passport_number"],
            birth_date=json_data["birth_date"],
            email=json_data["email"],
            email_verified=json_data["email_verified"],
            # These attributes will be filled afterwards
            accounts=accounts,
            completed_tutorials=[
                # @TODO
            ],
            hot_categories=[
                # @TODO
            ],
        )

    def update_from_json(self, json_data: Mapping[str, Any]) -> None:
        PikComfortAccount.update_list_with_models(
            self.accounts, json_data["accounts"], self.api
        )

        self.id = json_data["_uid"]
        self.type = json_data["_type"]
        self.phone = json_data["phone"]
        self.gender = json_data["gender"]
        self.first_name = json_data["first_name"]
        self.middle_name = json_data["middle_name"]
        self.last_name = json_data["last_name"]
        self.snils = json_data["snils"]
        self.passport_type = json_data["passport_type"]
        self.passport_number = json_data["passport_number"]
        self.birth_date = json_data["birth_date"]
        self.email = json_data["email"]
        self.email_verified = json_data["email_verified"]


@attr.s(slots=True)
class PikComfortAccount(_BaseIdentifiableModel):
    banned: bool = attr.ib()
    address: str = attr.ib()
    premise_number: str = attr.ib()
    has_account_number: bool = attr.ib()
    import_id: str = attr.ib()
    number: Optional[str] = attr.ib()
    debt: float = attr.ib()
    userpayment_in_processing: float = attr.ib()
    bill_type: str = attr.ib()
    brand_code: str = attr.ib()
    is_active: bool = attr.ib()
    is_moe: bool = attr.ib()
    is_prepaid: bool = attr.ib()
    new_receipt_day: int = attr.ib()
    is_partial_pay_available: bool = attr.ib()
    pay_methods_available: List[str] = attr.ib()
    terminal_key: str = attr.ib()
    available_services: List[str] = attr.ib()
    tickets_count: int = attr.ib()
    tickets_are_viewed: bool = attr.ib()
    pik_rent_available: bool = attr.ib()
    # requisites: None = attr.ib()
    final_payment_day: int = attr.ib()
    final_reading_day: int = attr.ib()
    chat_state: int = attr.ib()
    chat_schedule_description: str = attr.ib()
    emergency_phone_number: Optional[str] = attr.ib()
    last_readings_date: Optional[date] = attr.ib()
    last_turnover_date: Optional[date] = attr.ib()
    linked_at: datetime = attr.ib()
    premise: "PikComfortPremise" = attr.ib()
    building: "PikComfortBuilding" = attr.ib()
    address_formats: "PikComfortAddressFormat" = attr.ib()
    # notifications: List[Any] = attr.ib()
    payments: List["PikComfortPayment"] = attr.ib()
    receipts: List["PikComfortReceipt"] = attr.ib()
    meters: List["PikComfortMeter"] = attr.ib()
    account_notifications: List["AccountNotification"] = attr.ib()
    tickets: List["PikComfortTicket"] = attr.ib()
    insurance: Optional["Insurance"] = attr.ib(default=None)

    @property
    def last_payment(self) -> Optional["PikComfortPayment"]:
        try:
            # noinspection PyUnresolvedReferences
            return next(
                iter(
                    sorted(
                        enumerate(self.payments),
                        key=lambda x: (x[1].timestamp, -x[0]),
                        reverse=True,
                    )
                )
            )[1]
        except StopIteration:
            return None

    @property
    def last_receipt(self) -> Optional["PikComfortReceipt"]:
        try:
            # noinspection PyUnresolvedReferences
            return next(
                iter(
                    sorted(
                        enumerate(self.receipts),
                        key=lambda x: (x[1].period, -x[0]),
                        reverse=True,
                    )
                )
            )[1]
        except StopIteration:
            return None

    @classmethod
    def _prepare_dates(
        cls, json_data: Mapping[str, Any]
    ) -> Tuple[Optional[date], Optional[date], datetime]:
        last_readings_date = (
            datetime.fromisoformat(json_data["last_readings_date"]).date()
            if json_data.get("last_readings_date")
            else None
        )
        last_turnover_date = (
            datetime.fromisoformat(json_data["last_turnover_date"]).date()
            if json_data.get("last_turnover_date")
            else None
        )
        linked_at = datetime.fromisoformat(json_data["linked_at"])

        return last_readings_date, last_turnover_date, linked_at

    @classmethod
    def create_from_json(cls, json_data: Mapping[str, Any], api_object: PikComfortAPI):
        last_readings_date, last_turnover_date, linked_at = cls._prepare_dates(
            json_data
        )

        premise = PikComfortPremise.create_from_json(json_data["premise"], api_object)
        address_formats = PikComfortAddressFormat.create_from_json(
            json_data["address_formats"], api_object
        )
        building = PikComfortBuilding.create_from_json(
            json_data["building"], api_object
        )

        tickets = PikComfortTicket.create_from_json_list(
            json_data["tickets"], api_object
        )
        receipts = PikComfortReceipt.create_from_json_list(
            json_data["receipts"], api_object
        )
        meters = PikComfortMeter.create_from_json_list(json_data["meters"], api_object)
        payments = PikComfortPayment.create_from_json_list(
            json_data["payments"], api_object
        )

        return cls(
            api=api_object,
            id=json_data["_uid"],
            type=json_data["_type"],
            banned=json_data["banned"],
            address=json_data["address"],
            premise_number=json_data["premise_number"],
            has_account_number=json_data["has_account_number"],
            import_id=json_data["import_id"],
            number=json_data.get("number") or None,
            debt=json_data["debt"],
            last_readings_date=last_readings_date,
            last_turnover_date=last_turnover_date,
            userpayment_in_processing=json_data["userpayment_in_processing"],
            bill_type=json_data["bill_type"],
            brand_code=json_data["brand_code"],
            is_active=json_data["is_active"],
            is_moe=json_data["is_moe"],
            is_prepaid=json_data["is_prepaid"],
            new_receipt_day=json_data["new_receipt_day"],
            is_partial_pay_available=json_data["is_partial_pay_available"],
            pay_methods_available=json_data["pay_methods_available"],
            terminal_key=json_data["terminal_key"],
            available_services=json_data["available_services"],
            tickets_count=json_data["tickets_count"],
            tickets_are_viewed=json_data["tickets_are_viewed"],
            pik_rent_available=json_data["pik_rent_available"],
            # requisites=account_data["requisites"],
            final_payment_day=json_data["final_payment_day"],
            final_reading_day=json_data["final_reading_day"],
            chat_state=json_data["chat_state"],
            chat_schedule_description=json_data["chat_schedule_description"],
            emergency_phone_number=json_data["emergency_phone_number"],
            linked_at=linked_at,
            premise=premise,
            address_formats=address_formats,
            building=building,
            # Filled later on
            tickets=tickets,
            receipts=receipts,
            meters=meters,
            payments=payments,
            account_notifications=[],
        )

    def update_from_json(self, json_data: Mapping[str, Any]) -> None:
        assert self.id == json_data["_uid"], "UID does not match"
        assert self.type == json_data["_type"], "type does not match"

        self.address_formats.update_from_json(json_data["address_formats"])
        self.building.update_from_json(json_data["building"])
        self.premise.update_from_json(json_data["premise"])

        PikComfortTicket.update_list_with_models(
            self.tickets, json_data["tickets"], self.api
        )
        PikComfortReceipt.update_list_with_models(
            self.receipts, json_data["receipts"], self.api
        )

        last_readings_date, last_turnover_date, linked_at = self._prepare_dates(
            json_data
        )

        self.banned = json_data["banned"]
        self.address = json_data["address"]
        self.premise_number = json_data["premise_number"]
        self.has_account_number = json_data["has_account_number"]
        self.import_id = json_data["import_id"]
        self.number = json_data["number"]
        self.debt = json_data["debt"]
        self.last_readings_date = last_readings_date
        self.last_turnover_date = last_turnover_date
        self.userpayment_in_processing = json_data["userpayment_in_processing"]
        self.bill_type = json_data["bill_type"]
        self.brand_code = json_data["brand_code"]
        self.is_active = json_data["is_active"]
        self.is_moe = json_data["is_moe"]
        self.is_prepaid = json_data["is_prepaid"]
        self.new_receipt_day = json_data["new_receipt_day"]
        self.is_partial_pay_available = json_data["is_partial_pay_available"]
        self.pay_methods_available = json_data["pay_methods_available"]
        self.terminal_key = json_data["terminal_key"]
        self.available_services = json_data["available_services"]
        self.tickets_count = json_data["tickets_count"]
        self.tickets_are_viewed = json_data["tickets_are_viewed"]
        self.pik_rent_available = json_data["pik_rent_available"]
        # requisites=account_data["requisites"]
        self.final_payment_day = json_data["final_payment_day"]
        self.final_reading_day = json_data["final_reading_day"]
        self.chat_state = json_data["chat_state"]
        self.chat_schedule_description = json_data["chat_schedule_description"]
        self.emergency_phone_number = json_data["emergency_phone_number"]
        self.linked_at = linked_at

    async def async_create_ticket(
        self,
        classifier_id: str,
        description: str,
        *,
        check_classifier: bool = True,
        check_account: bool = True,
    ) -> "PikComfortTicket":
        return await self.api.async_create_ticket(
            classifier_id,
            description,
            self.id,
            check_classifier=check_classifier,
            check_account=check_account,
        )


@attr.s(slots=True)
class PikComfortPremise(_BaseIdentifiableModel):
    number: str = attr.ib()
    address: str = attr.ib()
    building: str = attr.ib()
    type_id: int = attr.ib()
    common_space: float = attr.ib()
    living_space: float = attr.ib()
    nonliving_space: Optional[float] = attr.ib()
    pay_space: Optional[float] = attr.ib()
    user_premise_name: Optional[str] = attr.ib()
    address_formats: "PikComfortAddressFormat" = attr.ib()

    @classmethod
    def create_from_json(cls, json_data: Mapping[str, Any], api_object: PikComfortAPI):
        address_formats = PikComfortAddressFormat.create_from_json(
            json_data["address_formats"], api_object
        )

        return cls(
            api=api_object,
            id=json_data["_uid"],
            type=json_data["_type"],
            number=json_data["number"],
            address=json_data["address"],
            building=json_data["building"],
            type_id=json_data["type"],
            common_space=json_data["common_space"],
            living_space=json_data["living_space"],
            nonliving_space=json_data["nonliving_space"],
            pay_space=json_data["pay_space"],
            user_premise_name=json_data["user_premise_name"],
            address_formats=address_formats,
        )

    def update_from_json(self, json_data: Mapping[str, Any]) -> None:
        self.address_formats.update_from_json(json_data["address_formats"])

        self.id = json_data["_uid"]
        self.type = json_data["_type"]
        self.number = json_data["number"]
        self.address = json_data["address"]
        self.building = json_data["building"]
        self.type_id = json_data["type"]
        self.common_space = json_data["common_space"]
        self.living_space = json_data["living_space"]
        self.nonliving_space = json_data["nonliving_space"]
        self.pay_space = json_data["pay_space"]
        self.user_premise_name = json_data["user_premise_name"]


@attr.s(slots=True)
class PikComfortBuilding(_BaseIdentifiableModel):
    address: str = attr.ib()
    type_id: int = attr.ib()
    geo_location: Tuple[float, float] = attr.ib()
    common_space: Optional[float] = attr.ib()
    nonliving_space: Optional[float] = attr.ib()
    living_space: Optional[float] = attr.ib()
    address_formats: "PikComfortAddressFormat" = attr.ib()

    @classmethod
    def create_from_json(cls, json_data: Mapping[str, Any], api_object: PikComfortAPI):
        address_formats = PikComfortAddressFormat.create_from_json(
            json_data["address_formats"], api_object
        )
        geo_location = tuple(json_data["geo_location"])

        return cls(
            api=api_object,
            id=json_data["_uid"],
            type=json_data["_type"],
            address=json_data["address"],
            type_id=json_data["type"],
            geo_location=geo_location,
            common_space=json_data["common_space"],
            nonliving_space=json_data["nonliving_space"],
            living_space=json_data["living_space"],
            address_formats=address_formats,
        )

    def update_from_json(self, json_data: Mapping[str, Any]) -> None:
        self.address_formats.update_from_json(json_data["address_formats"])

        geo_location = tuple(json_data["geo_location"])

        self.id = json_data["_uid"]
        self.type = json_data["_type"]
        self.address = json_data["address"]
        self.type_id = json_data["type"]
        self.geo_location = geo_location
        self.common_space = json_data["common_space"]
        self.nonliving_space = json_data["nonliving_space"]
        self.living_space = json_data["living_space"]


@attr.s(slots=True)
class PikComfortAddressFormat(_BaseModel):
    all: str = attr.ib()
    street_only: str = attr.ib()
    finishing_with_village: str = attr.ib()
    starting_with_street: str = attr.ib()
    finishing_with_street: str = attr.ib()

    @classmethod
    def create_from_json(cls, json_data: Mapping[str, Any], api_object: PikComfortAPI):
        return cls(
            api=api_object,
            all=json_data["all"],
            street_only=json_data["street_only"],
            finishing_with_village=json_data["finishing_with_village"],
            finishing_with_street=json_data["finishing_with_street"],
            starting_with_street=json_data["starting_with_street"],
        )

    def update_from_json(self, json_data: Mapping[str, Any]) -> None:
        self.all = json_data["all"]
        self.street_only = json_data["street_only"]
        self.finishing_with_village = json_data["finishing_with_village"]
        self.finishing_with_street = json_data["finishing_with_street"]
        self.starting_with_street = json_data["starting_with_street"]


class TicketStatus(IntEnum):
    UNKNOWN = 0
    RECEIVED = 200
    PROCESSING = 201
    COMPLETED = 202
    DENIED = 203


@attr.s(slots=True)
class PikComfortTicket(_BaseIdentifiableModel):
    number: str = attr.ib()
    description: str = attr.ib()
    classifier_id: str = attr.ib()
    status_id: int = attr.ib()
    is_viewed: bool = attr.ib()
    last_status_changed: datetime = attr.ib()
    created: datetime = attr.ib()
    updated: datetime = attr.ib()
    is_commentable: bool = attr.ib()
    attachments: List["PikComfortAttachmentImage"] = attr.ib()
    comments: List["PikComfortComment"] = attr.ib()
    is_liked: Optional[bool] = attr.ib(default=None)

    @property
    def status(self) -> TicketStatus:
        return TicketStatus(self.status_id)

    @staticmethod
    def _prepare_dates(
        json_data: Mapping[str, Any]
    ) -> Tuple[datetime, datetime, datetime]:
        last_status_changed = datetime.fromisoformat(json_data["last_status_changed"])
        created = datetime.fromisoformat(json_data["created"])
        updated = datetime.fromisoformat(json_data["updated"])

        return last_status_changed, created, updated

    @classmethod
    def create_from_json(cls, json_data: Mapping[str, Any], api_object: PikComfortAPI):
        last_status_changed, created, updated = cls._prepare_dates(json_data)
        attachments = PikComfortAttachmentImage.create_from_json_list(
            json_data["attachments"], api_object
        )
        comments = PikComfortComment.create_from_json_list(
            json_data["comments"], api_object
        )

        return cls(
            api=api_object,
            id=json_data["_uid"],
            type=json_data["_type"],
            number=json_data["number"],
            description=json_data["description"],
            classifier_id=json_data["classifier_id"],
            status_id=json_data["status"],
            is_viewed=json_data["is_viewed"],
            last_status_changed=last_status_changed,
            created=created,
            updated=updated,
            is_commentable=json_data["is_commentable"],
            attachments=attachments,
            comments=comments,
        )

    def update_from_json(self, json_data: Mapping[str, Any]) -> None:
        assert self.id == json_data["_uid"], "UID does not match"
        assert self.type == json_data["_type"], "type does not match"

        PikComfortComment.update_list_with_models(
            self.comments, json_data["comments"], self.api
        )
        PikComfortAttachmentImage.update_list_with_models(
            self.attachments, json_data["attachments"], self.api
        )

        last_status_changed, created, updated = self._prepare_dates(json_data)

        self.number = json_data["number"]
        self.description = json_data["description"]
        self.classifier_id = json_data["classifier_id"]
        self.status_id = json_data["status"]
        self.is_viewed = json_data["is_viewed"]
        self.last_status_changed = last_status_changed
        self.created = created
        self.updated = updated
        self.is_commentable = json_data["is_commentable"]


@attr.s(slots=True)
class PikComfortComment(_BaseIdentifiableModel):
    ticket: str = attr.ib()
    text: str = attr.ib()
    source_created: str = attr.ib()
    source_updated: str = attr.ib()
    attachments: List["PikComfortAttachmentImage"] = attr.ib()
    is_system: bool = attr.ib()
    notification_channel: str = attr.ib()
    notification_status: str = attr.ib()
    sender: str = attr.ib()

    @classmethod
    def create_from_json(cls, json_data: Mapping[str, Any], api_object: PikComfortAPI):
        attachments = PikComfortAttachmentImage.create_from_json_list(
            json_data["attachments"], api_object
        )

        return cls(
            api=api_object,
            id=json_data["_uid"],
            type=json_data["_type"],
            ticket=json_data["ticket"],
            text=json_data["text"],
            source_updated=json_data["source_updated"],
            attachments=attachments,
            is_system=json_data["is_system"],
            notification_channel=json_data["notification_channel"],
            notification_status=json_data["notification_status"],
            sender=json_data["sender"],
            source_created=json_data["source_created"],
        )

    def update_from_json(self, json_data: Mapping[str, Any]) -> None:
        assert self.id == json_data["_uid"], "UID does not match"
        assert self.type == json_data["_type"], "type does not match"

        self.ticket = json_data["ticket"]
        self.text = json_data["text"]
        self.source_updated = json_data["source_updated"]
        self.is_system = json_data["is_system"]
        self.notification_channel = json_data["notification_channel"]
        self.notification_status = json_data["notification_status"]
        self.sender = json_data["sender"]


@attr.s(slots=True)
class PikComfortAttachmentImage(_BaseModel):
    id: str = attr.ib()
    created: datetime = attr.ib()
    name: str = attr.ib()
    size: int = attr.ib()
    content_type: str = attr.ib()
    tags: Tuple[str, ...] = attr.ib()
    linked_from: Optional[str] = attr.ib()
    file_link: str = attr.ib()

    @classmethod
    def create_from_json(cls, json_data: Mapping[str, Any], api_object: PikComfortAPI):
        created = datetime.fromisoformat(json_data["created"])
        tags = tuple(json_data["tags"])

        return cls(
            api=api_object,
            id=json_data["uid"],
            created=created,
            name=json_data["name"],
            size=json_data["size"],
            content_type=json_data["content_type"],
            tags=tags,
            linked_from=json_data.get("linked_from"),
            file_link=json_data["file_link"],
        )

    def update_from_json(self, json_data: Mapping[str, Any]) -> None:
        assert self.id == json_data["uid"], "UID does not match"

        tags = tuple(json_data["tags"])
        created = datetime.fromisoformat(json_data["created"])

        self.created = created
        self.name = json_data["name"]
        self.size = json_data["size"]
        self.content_type = json_data["content_type"]
        self.tags = tags
        self.linked_from = json_data.get("linked_from")
        self.file_link = json_data["file_link"]

    @classmethod
    def update_list_with_models(
        cls: Type[_T],
        target_list: List[_T],
        json_data_list: List[Mapping[str, Any]],
        api_object: PikComfortAPI,
    ) -> None:
        cleanup: Set[str] = set()
        for item_data in json_data_list:
            attachment_id = item_data["uid"]
            cleanup.add(attachment_id)
            current_attachment = None

            for existing_attachment in target_list:
                if existing_attachment.id == attachment_id:
                    current_attachment = existing_attachment
                    break

            if current_attachment is None:
                target_list.append(cls.create_from_json(item_data, api_object))

            else:
                current_attachment.update_from_json(item_data)

        for current_attachment in tuple(reversed(target_list)):
            if current_attachment.id not in cleanup:
                target_list.remove(current_attachment)
            else:
                cleanup.remove(current_attachment.id)


@attr.s(slots=True)
class PikComfortReceipt(_BaseModel):
    type: str = attr.ib()
    period: date = attr.ib()
    charge: float = attr.ib()
    corrections: float = attr.ib()
    payment: float = attr.ib()
    initial: float = attr.ib()
    subsidy: float = attr.ib()
    total: float = attr.ib()
    penalty: float = attr.ib()
    contents: List["PikComfortReceiptContent"] = attr.ib()
    # additional: List[Any] = attr.ib()
    paid: Optional[float] = attr.ib(default=None)
    debt: Optional[float] = attr.ib(default=None)

    @classmethod
    def create_from_json(cls, json_data: Mapping[str, Any], api_object: PikComfortAPI):
        contents = PikComfortReceiptContent.create_from_json_list(
            json_data["main"], api_object
        )

        period = datetime.fromisoformat(json_data["period"]).date()

        return cls(
            api=api_object,
            type=json_data["_type"],
            period=period,
            charge=json_data["charge"],
            corrections=json_data["charge_correct"],
            payment=json_data["payment"],
            initial=json_data["incoming_balance"],
            subsidy=json_data["subsidy"],
            total=json_data["total"],
            penalty=json_data["penalty"],
            contents=contents,
            # additional=json_data["additional"],
            paid=json_data.get("paid"),
            debt=json_data.get("debt"),
        )

    def update_from_json(self, json_data: Mapping[str, Any]) -> None:
        assert self.type == json_data["_type"], "type does not match"

        period = datetime.fromisoformat(json_data["period"]).date()

        assert self.period == period, "period does not match"

        PikComfortReceiptContent.update_list_with_models(
            self.contents, json_data["main"], self.api
        )

        self.charge = json_data["charge"]
        self.corrections = json_data["charge_correct"]
        self.payment = json_data["payment"]
        self.initial = json_data["incoming_balance"]
        self.subsidy = json_data["subsidy"]
        self.total = json_data["total"]
        self.penalty = json_data["penalty"]
        self.contents = json_data["main"]
        # self.additional = json_data["additional"]
        self.paid = json_data.get("paid")
        self.debt = json_data.get("debt")

    @classmethod
    def update_list_with_models(
        cls: Type[_T],
        target_list: List[_T],
        json_data_list: List[Mapping[str, Any]],
        api_object: PikComfortAPI,
    ) -> None:
        cleanup: Set[Tuple[str, date]] = set()
        for item_data in json_data_list:
            receipt_type = item_data["_type"]
            receipt_period = datetime.fromisoformat(item_data["period"]).date()
            cleanup.add((receipt_type, receipt_period))
            current_receipt = None

            for existing_receipt in target_list:
                if (
                    existing_receipt.type == receipt_type
                    and existing_receipt.period == receipt_period
                ):
                    current_receipt = existing_receipt
                    break

            if current_receipt is None:
                target_list.append(cls.create_from_json(item_data, api_object))
            else:
                current_receipt.update_from_json(item_data)

        for current_receipt in tuple(reversed(target_list)):
            if (current_receipt.type, current_receipt.period) not in cleanup:
                target_list.remove(current_receipt)
            else:
                cleanup.remove((current_receipt.type, current_receipt.period))


@attr.s(slots=True)
class PikComfortReceiptContent(_BaseIdentifiableModel):
    import_id: str = attr.ib()
    title: str = attr.ib()
    display_name: Optional[str] = attr.ib()
    address: str = attr.ib()
    request_phone: str = attr.ib()
    dispatcher_phone: str = attr.ib()
    charge: float = attr.ib()
    corrections: float = attr.ib()
    payment: float = attr.ib()
    initial: float = attr.ib()
    subsidy: float = attr.ib()
    penalty: float = attr.ib()
    total: float = attr.ib()
    turnover_balance_records: List["TurnoverBalanceRecord"] = attr.ib()

    @classmethod
    def create_from_json(cls, json_data: Mapping[str, Any], api_object: PikComfortAPI):
        turnover_balance_records = TurnoverBalanceRecord.create_from_json_list(
            json_data["turnover_balance_records"], api_object
        )

        return cls(
            api=api_object,
            id=json_data["_uid"],
            type=json_data["_type"],
            import_id=json_data["import_id"],
            title=json_data["title"],
            display_name=json_data["display_name"],
            address=json_data["address"],
            request_phone=json_data["request_phone"],
            dispatcher_phone=json_data["dispatcher_phone"],
            charge=json_data["charge"],
            corrections=json_data["charge_correct"],
            payment=json_data["payment"],
            initial=json_data["incoming_balance"],
            subsidy=json_data["subsidy"],
            penalty=json_data["penalty"],
            total=json_data["total"],
            turnover_balance_records=turnover_balance_records,
        )

    def update_from_json(self, json_data: Mapping[str, Any]) -> None:
        assert self.id == json_data["_uid"], "UID does not match"
        assert self.type == json_data["_type"], "type does not match"

        TurnoverBalanceRecord.update_list_with_models(
            self.turnover_balance_records,
            json_data["turnover_balance_records"],
            self.api,
        )

        self.import_id = json_data["import_id"]
        self.title = json_data["title"]
        self.display_name = json_data["display_name"]
        self.address = json_data["address"]
        self.request_phone = json_data["request_phone"]
        self.dispatcher_phone = json_data["dispatcher_phone"]
        self.charge = json_data["charge"]
        self.corrections = json_data["charge_correct"]
        self.payment = json_data["payment"]
        self.initial = json_data["incoming_balance"]
        self.subsidy = json_data["subsidy"]
        self.penalty = json_data["penalty"]
        self.total = json_data["total"]


@attr.s(slots=True)
class TurnoverBalanceRecord(_BaseIdentifiableModel):
    service_name: str = attr.ib()
    service_code: str = attr.ib()
    initial: float = attr.ib()
    charge: float = attr.ib()
    boosted_charge: float = attr.ib()
    corrections: float = attr.ib()
    subsidy: float = attr.ib()
    payment: float = attr.ib()
    total: float = attr.ib()

    @classmethod
    def create_from_json(cls, json_data: Mapping[str, Any], api_object: PikComfortAPI):
        return cls(
            api=api_object,
            id=json_data["_uid"],
            type=json_data["_type"],
            service_name=json_data["service_name"],
            service_code=json_data["service_code"],
            initial=json_data["incoming_balance"],
            charge=json_data["charge"],
            boosted_charge=json_data["boosted_charge"],
            corrections=json_data["charge_correct"],
            subsidy=json_data["subsidy"],
            payment=json_data["payment"],
            total=json_data["total"],
        )

    def update_from_json(self, json_data: Mapping[str, Any]) -> None:
        assert self.id == json_data["_uid"], "UID does not match"
        assert self.type == json_data["_type"], "type does not match"

        self.service_name = json_data["service_name"]
        self.service_code = json_data["service_code"]
        self.initial = json_data["incoming_balance"]
        self.charge = json_data["charge"]
        self.boosted_charge = json_data["boosted_charge"]
        self.corrections = json_data["charge_correct"]
        self.subsidy = json_data["subsidy"]
        self.payment = json_data["payment"]
        self.total = json_data["total"]


class MeterResourceType(IntEnum):
    UNKNOWN = 0
    COLD_WATER = 1
    HOT_WATER = 2
    ELECTRICITY = 3
    GAS = 4
    HEATING = 5
    GAS_TANKS = 6
    SOLID_FUEL = 7
    WASTE_WATER = 8

    @classmethod
    def _missing_(cls, value):
        return cls.UNKNOWN


@attr.s(slots=True)
class PikComfortMeter(_BaseIdentifiableModel):
    factory_number: str = attr.ib()
    resource_type_id: int = attr.ib()
    has_user_readings: bool = attr.ib()
    is_auto: bool = attr.ib()
    import_id: str = attr.ib()
    meter_type: int = attr.ib()
    is_individual: bool = attr.ib()
    unit_name: str = attr.ib()
    recalibration_status: str = attr.ib()
    last_period: str = attr.ib()
    tariffs: List["Tariff"] = attr.ib()
    user_meter_name: Optional[str] = attr.ib(default=None)
    date_next_recalibration: Optional[date] = attr.ib(default=None)

    @property
    def resource_type(self) -> MeterResourceType:
        return MeterResourceType(self.resource_type_id)

    @classmethod
    def create_from_json(cls, json_data: Mapping[str, Any], api_object: PikComfortAPI):
        tariffs = Tariff.create_from_json_list(json_data["tariffs"], api_object)

        date_next_recalibration = (
            datetime.fromisoformat(json_data["date_next_recalibration"]).date()
            if json_data.get("date_next_recalibration")
            else None
        )

        return cls(
            api=api_object,
            id=json_data["_uid"],
            type=json_data["_type"],
            factory_number=json_data["factory_number"],
            resource_type_id=json_data["resource_type"],
            has_user_readings=json_data["has_user_readings"],
            is_auto=json_data["is_auto"],
            import_id=json_data["import_id"],
            meter_type=json_data["meter_type"],
            is_individual=json_data["is_individual"],
            unit_name=json_data["unit_name"],
            recalibration_status=json_data["recalibration_status"],
            last_period=json_data["last_period"],
            user_meter_name=json_data.get("user_meter_name"),
            tariffs=tariffs,
            date_next_recalibration=date_next_recalibration,
        )

    def update_from_json(self, json_data: Mapping[str, Any]) -> None:
        assert self.id == json_data["_uid"], "UID does not match"
        assert self.type == json_data["_type"], "type does not match"

        Tariff.update_list_with_models(self.tariffs, json_data["tariffs"])

        date_next_recalibration = (
            datetime.fromisoformat(json_data["date_next_recalibration"]).date()
            if json_data.get("date_next_recalibration")
            else None
        )

        self.factory_number = json_data["factory_number"]
        self.resource_type_id = json_data["resource_type"]
        self.has_user_readings = json_data["has_user_readings"]
        self.is_auto = json_data["is_auto"]
        self.import_id = json_data["import_id"]
        self.meter_type = json_data["meter_type"]
        self.is_individual = json_data["is_individual"]
        self.unit_name = json_data["unit_name"]
        self.recalibration_status = json_data["recalibration_status"]
        self.last_period = json_data["last_period"]
        self.user_meter_name = json_data.get("user_meter_name")
        self.date_next_recalibration = date_next_recalibration

    async def async_submit_readings(
        self, values: Union[Mapping[int, float], Iterable[float], float]
    ) -> List["PikComfortMeterReading"]:
        api = self.api

        if not api.is_authenticated:
            raise PikComfortException("API is not authenticated")

        request = []
        meter_id = self.id

        if isinstance(values, Mapping):
            iterator = values.items()
        elif isinstance(values, float):
            iterator = ((1, values),)
        else:
            iterator = enumerate(values, start=1)

        for tariff_id, value in iterator:
            existing_tariff = None
            for tariff in self.tariffs:
                if tariff_id == tariff.type:
                    existing_tariff = tariff
                    break

            if existing_tariff is None:
                raise ValueError(f"tariff {tariff_id} does not exist")

            request.append(
                {
                    "value": value,
                    "tariff_type": existing_tariff.type,
                    "meter": meter_id,
                    "meter_reading_uid": meter_id + str(existing_tariff.type),
                }
            )

        _LOGGER.debug(f"[{self}] Performing readings submission: {request}")

        async with api.session.post(
            api.BASE_PIK_URL + "/api/v2/mobile/usermeterreading-list/",
            headers={aiohttp.hdrs.AUTHORIZATION: "Token " + api.token},
            json=request,
        ) as request:
            if request.status != 201:
                # @TODO: read error codes
                response_data = await request.text()
                _LOGGER.error(
                    f"[{self}] Could not submit readings "
                    f"({request.status}): {response_data}"
                )
                raise PikComfortException("Could not submit readings")

            resp_data = await request.json()

        if not isinstance(resp_data, list):
            _LOGGER.error(f"[{self}] Response data does not contain submission updates")
            raise PikComfortException("Invalid response data")

        return PikComfortMeterReading.create_from_json_list(resp_data, self.api)


@attr.s(slots=True)
class Tariff(_BaseModel):
    type: int = attr.ib()
    value: float = attr.ib()
    average_in_month: float = attr.ib()
    user_value: Optional[float] = attr.ib(default=None)
    user_value_updated: Optional[datetime] = attr.ib(default=None)
    user_value_created: Optional[datetime] = attr.ib(default=None)

    @staticmethod
    def _prepare_dates(
        json_data: Mapping[str, Any]
    ) -> Tuple[Optional[datetime], Optional[datetime]]:
        user_value_created = (
            datetime.fromisoformat(json_data["user_value_created"])
            if json_data.get("user_value_created")
            else None
        )
        user_value_updated = (
            datetime.fromisoformat(json_data["user_value_updated"])
            if json_data.get("user_value_updated")
            else None
        )

        return user_value_created, user_value_updated

    @classmethod
    def create_from_json(cls, json_data: Mapping[str, Any], api_object: PikComfortAPI):
        user_value_created, user_value_updated = cls._prepare_dates(json_data)

        return cls(
            api=api_object,
            type=json_data["type"],
            value=json_data["value"],
            average_in_month=json_data["average_in_month"],
            user_value=json_data["user_value"],
            user_value_created=user_value_created,
            user_value_updated=user_value_updated,
        )

    def update_from_json(self, json_data: Mapping[str, Any]) -> None:
        assert self.type == json_data["type"], "types do not match"

        user_value_created, user_value_updated = self._prepare_dates(json_data)

        self.value = json_data["value"]
        self.average_in_month = json_data["average_in_month"]
        self.user_value = json_data["user_value"]
        self.user_value_created = user_value_created
        self.user_value_updated = user_value_updated

    @classmethod
    def update_list_with_models(
        cls: Type[_T], target_list: List[_T], json_data_list: List[Mapping[str, Any]]
    ) -> None:
        cleanup: Set[int] = set()
        for item_data in json_data_list:
            tariff_type = item_data["type"]
            cleanup.add(tariff_type)
            current_tariff = None

            for existing_object in target_list:
                if tariff_type.type == tariff_type:
                    current_tariff = existing_object
                    break

            if current_tariff is None:
                target_list.append(cls.create_from_json(item_data))

            else:
                current_tariff.update_from_json(item_data)

        for current_tariff in tuple(reversed(target_list)):
            if current_tariff.type not in cleanup:
                target_list.remove(current_tariff)
            else:
                cleanup.remove(current_tariff.type)


class PaymentStatus(IntEnum):
    UNKNOWN = 0
    PROCESSING = 1
    ACCEPTED = 2
    DECLINED = 3

    @classmethod
    def _missing_(cls, value):
        return cls.UNKNOWN


@attr.s(slots=True)
class PikComfortPayment(_BaseIdentifiableModel):
    amount: float = attr.ib()
    status_id: int = attr.ib()
    check_url: str = attr.ib()
    bank_id: str = attr.ib()
    timestamp: datetime = attr.ib()
    payment_type: int = attr.ib()
    source_name: str = attr.ib()
    source_details: "PaymentPointDetails" = attr.ib()

    @classmethod
    def create_from_json(cls, json_data: Mapping[str, Any], api_object: PikComfortAPI):
        payment_point_details = PaymentPointDetails.create_from_json(
            json_data["payment_point_details"], api_object
        )

        timestamp = datetime.fromisoformat(json_data["payment_date"])

        return cls(
            api=api_object,
            id=json_data["_uid"],
            type=json_data["_type"],
            amount=json_data["amount"],
            status_id=json_data["status"],
            check_url=json_data["check_url"],
            bank_id=json_data["bank_id"],
            timestamp=timestamp,
            payment_type=json_data["payment_type"],
            source_name=json_data["payment_point"],
            source_details=payment_point_details,
        )

    @property
    def status(self) -> PaymentStatus:
        return PaymentStatus(self.status_id)

    def update_from_json(self, json_data: Mapping[str, Any]) -> None:
        assert self.id == json_data["_uid"], "UID does not match"
        assert self.type == json_data["_type"], "type does not match"

        timestamp = datetime.fromisoformat(json_data["payment_date"])

        self.source_details.update_from_json(json_data["payment_point_details"])

        self.amount = json_data["amount"]
        self.status_id = json_data["status"]
        self.check_url = json_data["check_url"]
        self.bank_id = json_data["bank_id"]
        self.timestamp = timestamp
        self.payment_type = json_data["payment_type"]
        self.source_name = json_data["payment_point"]


@attr.s(slots=True)
class PaymentPointDetails(_BaseModel):
    icon_name: str = attr.ib()
    normalized_name: str = attr.ib()
    color: str = attr.ib()

    @classmethod
    def create_from_json(cls, json_data: Mapping[str, Any], api_object: PikComfortAPI):
        return cls(
            api=api_object,
            icon_name=json_data["icon_name"],
            normalized_name=json_data["normalized_name"],
            color=json_data["color"],
        )

    def update_from_json(self, json_data: Mapping[str, Any]) -> None:
        self.icon_name = json_data["icon_name"]
        self.normalized_name = json_data["normalized_name"]
        self.color = json_data["color"]


@attr.s(slots=True)
class Insurance:
    # @TODO
    _id: str = attr.ib()
    _type: str = attr.ib()
    is_active: bool = attr.ib()
    in_progress: bool = attr.ib()
    is_paid: bool = attr.ib()
    rate: float = attr.ib()


@attr.s(slots=True)
class HotCategory:
    _id: str = attr.ib()
    _type: str = attr.ib()
    title: str = attr.ib()
    icon_name: str = attr.ib()
    classifier_id: str = attr.ib()


@attr.s(slots=True)
class AccountNotification:
    _id: str = attr.ib()
    _type: str = attr.ib()
    created: str = attr.ib()
    title: str = attr.ib()
    short_text: str = attr.ib()
    date_to: None = attr.ib()
    scopes: List[int] = attr.ib()
    notification_type: int = attr.ib()
    full_text: str = attr.ib()
    image_x2: None = attr.ib()
    is_urgent: bool = attr.ib()
    is_viewed: bool = attr.ib()
    actions: List["Action"] = attr.ib()
    date_from: Optional[str] = attr.ib(default=None)
    image: Optional["PikComfortAttachmentImage"] = attr.ib(default=None)
    image_x1: Optional["PikComfortAttachmentImage"] = attr.ib(default=None)


@attr.s(slots=True)
class Action:
    _id: str = attr.ib()
    _type: str = attr.ib()
    device_type: int = attr.ib()
    action_type: int = attr.ib()
    payload: str = attr.ib()
    button_type: int = attr.ib()
    button_title: str = attr.ib()
    data: Optional["Datum"] = attr.ib(default=None)


@attr.s(slots=True)
class Datum:
    reason: str = attr.ib()


@attr.s(slots=True)
class PikComfortMeterReading(_BaseIdentifiableModel):
    value: float = attr.ib()
    tariff_type: int = attr.ib()
    date: date = attr.ib()
    meter: "PikComfortMeterReadingMeterInfo" = attr.ib()

    @classmethod
    def create_from_json(cls, json_data: Mapping[str, Any], api_object: PikComfortAPI):
        date_ = datetime.fromisoformat(json_data["date"]).date()
        meter = PikComfortMeterReadingMeterInfo.create_from_json(
            json_data["meter"], api_object
        )

        return cls(
            api=api_object,
            id=json_data["_uid"],
            type=json_data["_type"],
            value=json_data["value"],
            tariff_type=json_data["tariff_type"],
            date=date_,
            meter=meter,
        )

    def update_from_json(self, json_data: Mapping[str, Any]) -> None:
        assert self.id == json_data["_uid"], "UID does not match"
        assert self.type == json_data["_type"], "type does not match"

        date_ = datetime.fromisoformat(json_data["date"]).date()

        self.meter.update_from_json(json_data["meter"])

        self.value = json_data["value"]
        self.tariff_type = json_data["tariff_type"]
        self.date = date_


@attr.s(slots=True)
class PikComfortMeterReadingMeterInfo(_BaseIdentifiableModel):
    import_id: str = attr.ib()
    resource_type_id: int = attr.ib()
    is_auto: bool = attr.ib()
    factory_number: str = attr.ib()
    meter_type: int = attr.ib()

    @classmethod
    def create_from_json(cls, json_data: Mapping[str, Any], api_object: PikComfortAPI):
        return cls(
            api=api_object,
            id=json_data["_uid"],
            type=json_data["_type"],
            import_id=json_data["import_id"],
            resource_type_id=json_data["resource_type"],
            is_auto=json_data["is_auto"],
            factory_number=json_data["factory_number"],
            meter_type=json_data["meter_type"],
        )

    def update_from_json(self, json_data: Mapping[str, Any]) -> None:
        assert self.id == json_data["_uid"], "UID does not match"
        assert self.type == json_data["_type"], "type does not match"

        self.import_id = json_data["import_id"]
        self.resource_type_id = json_data["resource_type"]
        self.is_auto = json_data["is_auto"]
        self.factory_number = json_data["factory_number"]
        self.meter_type = json_data["meter_type"]

    @property
    def meter(self) -> Optional[PikComfortMeter]:
        info = self.api.info
        if info is None:
            return None

        meter_id, meter_type = self.id, self.type

        for account in info.accounts:
            for meter in account.meters:
                if meter.id == meter_id and meter.type == meter_type:
                    return meter

        return None

    @property
    def resource_type(self) -> MeterResourceType:
        return MeterResourceType(self.resource_type_id)


@attr.s(slots=True)
class TicketClassifier(_BaseIdentifiableModel):
    name: str = attr.ib()
    level: int = attr.ib()
    created_at: datetime = attr.ib()
    updated_at: datetime = attr.ib()
    parent_id: Optional[str] = attr.ib(default=None)
    hint: Optional[str] = attr.ib(default=None)

    @classmethod
    def create_from_json(cls, json_data: Mapping[str, Any], api_object: PikComfortAPI):
        created_at = datetime.fromisoformat(json_data["created"])
        updated_at = datetime.fromisoformat(json_data["updated"])
        id_ = json_data["_uid"]
        parent_id = json_data.get("parent")
        if parent_id == id_:
            parent_id = None

        return cls(
            api=api_object,
            id=id_,
            type=json_data["_type"],
            name=json_data["name"],
            level=json_data["level"],
            created_at=created_at,
            updated_at=updated_at,
            parent_id=parent_id,
            hint=json_data.get("hint") or None,
        )

    def update_from_json(self, json_data: Mapping[str, Any]) -> None:
        id_ = json_data["_uid"]
        assert self.id == id_, "UID does not match"
        assert self.type == json_data["_type"], "type does not match"

        created_at = datetime.fromisoformat(json_data["created"])
        updated_at = datetime.fromisoformat(json_data["updated"])

        parent_id = json_data.get("parent")
        if parent_id == id_:
            parent_id = None

        self.name = json_data["name"]
        self.level = json_data["level"]
        self.created_at = created_at
        self.updated_at = updated_at
        self.parent_id = parent_id
        self.hint = json_data.get("hint") or None

    @property
    def parent(self) -> Optional["TicketClassifier"]:
        parent_id = self.parent_id
        if not parent_id:
            return None

        classifiers = self.api.classifiers
        if classifiers is None:
            raise PikComfortException("Classifiers must be updated")

        for classifier in classifiers:
            if classifier.id == parent_id:
                return classifier

        return None

    @property
    def has_children(self) -> bool:
        classifiers = self.api.classifiers
        if classifiers is None:
            raise PikComfortException("Classifiers must be updated")

        for classifier in classifiers:
            if classifier.parent_id == self.id:
                return True

        return False

    @property
    def children(self) -> Tuple["TicketClassifier", ...]:
        classifiers = self.api.classifiers
        if classifiers is None:
            raise PikComfortException("Classifiers must be updated")

        return tuple(
            classifier for classifier in classifiers if classifier.parent_id == self.id
        )

    @property
    def path_to(self) -> Tuple["TicketClassifier", ...]:
        return tuple(reversed(self.path_from))

    @property
    def path_from(self) -> Tuple["TicketClassifier", ...]:
        path = []
        path_item = self
        while path_item is not None:
            if path_item in path:
                _LOGGER.error(
                    f"Detected loop while building classifier path: "
                    f"for={self.id}, "
                    f"current_path={tuple(map(lambda x: x.id, reversed(path)))}, "
                    f"path_item={path_item}"
                )
                raise PikComfortException("Path loop detected")

            path.append(path_item)
            path_item = path_item.parent

        return tuple(path)

    @property
    def parents(self) -> Tuple["TicketClassifier", ...]:
        return self.path_from[1:]

    async def async_create_ticket(
        self,
        description: str,
        account_id: Optional[str] = None,
        *,
        check_classifier: bool = True,
        check_account: bool = True,
    ) -> PikComfortTicket:
        return await self.api.async_create_ticket(
            self.id,
            description,
            account_id,
            check_classifier=check_classifier,
            check_account=check_account,
        )
