import json
from collections.abc import Callable
from enum import Enum
from functools import cached_property, wraps
from typing import Annotated, ClassVar, Union

import structlog
from channels.generic.websocket import AsyncWebsocketConsumer
from compute_horde.fv_protocol.facilitator_requests import Error, Response
from compute_horde.fv_protocol.validator_requests import V0AuthenticationRequest, V0Heartbeat, V0MachineSpecsUpdate
from django.conf import settings
from django.db import IntegrityError
from django.utils.timezone import now
from pydantic import BaseModel, Field, TypeAdapter, ValidationError
from structlog.contextvars import bound_contextvars

from .models import Channel, Job, JobStatus, Validator
from .schemas import JobStatusUpdate
from .specs import save_machine_specs

log = structlog.get_logger(__name__)


class CloseCode(Enum):
    INVALID_SIGNATURE = 3000
    NOT_FOUND = 3001


def require_authentication(method: Callable) -> Callable:
    @wraps(method)
    async def wrapper(self, *args, **kwargs):
        if "ss58_address" not in self.scope:
            response = Response(
                status="error",
                errors=[
                    Error(
                        msg="Not authenticated",
                        type="auth.not_authenticated",
                        help="Please send authentication request first",
                    )
                ],
            )
            await self.send(text_data=response.json())
            return

        return await method(self, *args, **kwargs)

    return wrapper


class ValidatorConsumer(AsyncWebsocketConsumer):
    """A WS endpoint for validators to connect to"""

    async def connect(self) -> None:
        await super().connect()
        log.info("connected", scope=self.scope)

    async def disconnect(self, code: int | str) -> None:
        if self.scope.get("ss58_address"):
            await self.disconnect_channel_from_validator()
            # await self.channel_layer.group_discard(ss58_address, self.channel_name)
            # log.debug('channel removed from group', channel=self.channel_name, group=ss58_address)

        log.info("disconnected", scope=self.scope, code=code)

    @cached_property
    def headers(self):
        headers = self.scope.get("headers", [])

        return {key.decode().lower(): value.decode() for key, value in headers}

    async def connect_channel_to_validator(self, validator: Validator) -> None:
        """Associate this channel with specific validator"""
        await Channel.objects.acreate(
            validator=validator,
            name=self.channel_name,
        )
        validator_version = self.headers.get("x-validator-version")
        validator_runner_version = self.headers.get("x-validator-runner-version")
        if validator_version is not None:
            validator.version = validator_version
        if validator_runner_version is not None:
            validator.runner_version = validator_runner_version
        if validator_version is not None or validator_runner_version is not None:
            await validator.asave()

    async def disconnect_channel_from_validator(self) -> None:
        """Remove associaton of this channel with any validator"""
        await Channel.objects.filter(name=self.channel_name).adelete()

    async def receive(self, text_data: str | None = None, bytes_data: bytes | None = None) -> None:
        """
        Receive a message from the client, parse it to one of known message types
        (`MESSAGE_HANDLERS.keys()`) and handle it using registered handlers
        (again, see `MESSAGE_HANDLERS`).
        """

        with bound_contextvars(text_data=text_data, bytes_data=bytes_data):
            log.debug("message received")

            try:
                message: BaseModel = TypeAdapter(
                    Annotated[Union[*self.MESSAGE_HANDLERS.keys()], Field(discriminator="message_type")]
                ).validate_json(text_data)

            except ValidationError as exc:
                errors = [Error.parse_obj(error_dict) for error_dict in exc.errors()]
                log.debug("message parsing failed", errors=errors)
                response = Response(status="error", errors=errors)
                await self.send(text_data=response.json())
                return

            handler = self.MESSAGE_HANDLERS[type(message)]
            log.debug("selected message handler", handler=handler)
            await handler(self, message)

    async def authenticate(self, message: V0AuthenticationRequest) -> None:
        """Check some authentication details and store ss58 address in the scope"""

        with bound_contextvars(message=message):
            log.debug("authenticating")

            if "ss58_address" in self.scope:
                error = Error(
                    msg="Already authenticated",
                    type="auth.already_authenticated",
                    help="You are already authenticated, please do not " "send authentication request again.",
                )
                log.debug("authentication failed", error=error)
                response = Response(status="error", errors=[error])
                await self.send(text_data=response.json())
                return

            validator = await Validator.objects.filter(ss58_address=message.ss58_address, is_active=True).afirst()
            if not validator:
                error = Error(
                    msg="Validator not found",
                    type="auth.validator_not_found",
                    help=f"Validator with hotkey {message.ss58_address} was not found. "
                    f"If you are sure that the key belongs to an active validator, "
                    f"please wait at least {settings.METAGRAPH_SYNC_PERIOD} "
                    f"for the list of active validators to be updated and then retry.",
                )
                log.debug("authentication failed", error=error)
                response = Response(status="error", errors=[error])
                await self.send(text_data=response.json())
                await self.close(code=CloseCode.NOT_FOUND.value)
                return

            if not message.verify_signature():
                error = Error(
                    msg="Invalid signature",
                    type="auth.signature_invalid",
                    help="The signature provided does not match the public key",
                )
                log.debug("authentication failed", error=error)
                response = Response(status="error", errors=[error])
                await self.send(text_data=response.json())
                await self.close(code=CloseCode.INVALID_SIGNATURE.value)
                return

            self.scope["ss58_address"] = message.ss58_address
            log.debug("authenticated")

            await self.connect_channel_to_validator(validator)
            log.debug("channel connected to validator", channel=self.channel_name, validator=validator.ss58_address)
            # await self.channel_layer.group_add(message.ss58_address, self.channel_name)
            # log.debug('channel added to group', channel=self.channel_name, group=message.ss58_address)

            response = Response(status="success")
            await self.send(text_data=response.json())

    @require_authentication
    async def job_status_update(self, message: JobStatusUpdate) -> None:
        """Handle job status update message sent from validator to this app"""

        with bound_contextvars(message=message):
            log.debug("handling new job response")

            try:
                job = await Job.objects.aget(uuid=message.uuid)
            except Job.DoesNotExist:
                log.warning("job not found")
                response = Response(
                    status="error",
                    errors=[Error(msg="Job not found", type="job.not_found", help="Job with this UUID was not found")],
                )
                await self.send(text_data=response.json())
                return

            try:
                job = await JobStatus.objects.acreate(
                    job=job,
                    status=JobStatus.Status[message.status.upper()],
                    metadata=message.metadata.dict(),
                )
            except IntegrityError as exc:
                log.debug("job status update failed", exc=exc)
                response = Response(
                    status="error",
                    errors=[
                        Error(
                            msg="Integrity error",
                            type="job.integrity_error",
                            help="Probably you are trying to set the same status twice?",
                        )
                    ],
                )
                await self.send(text_data=response.json())
                return

            log.debug("job status updated")
            await self.send(text_data=Response(status="success").json())

    @require_authentication
    async def machine_specs_update(self, message: V0MachineSpecsUpdate) -> None:
        """Handle machine specs update message sent from validator to this app"""

        with bound_contextvars(message=message):
            await save_machine_specs(message)

    @require_authentication
    async def heartbeat(self, message: V0Heartbeat) -> None:
        await Channel.objects.filter(name=self.channel_name).aupdate(last_heartbeat=now())

    async def job_new(self, payload: dict) -> None:
        """Receive V0JobRequest from backend and forward it to validator via WS"""

        log.debug("forwarding new job request to validator", payload=payload)
        await self.send(text_data=json.dumps(payload))

    async def validator_disconnect(self, payload: dict) -> None:
        """Receive a request to disconnect from our side"""

        log.debug("app requested disconnect", payload=payload)
        await self.close(code=CloseCode.NOT_FOUND.value)
        # FIXME: this should not be required, but tests are failing
        #        tests should be fixed so self.close(...) calling self.disconnect(...) is enough
        await self.disconnect(code=CloseCode.NOT_FOUND.value)

    MESSAGE_HANDLERS: ClassVar[dict[BaseModel, Callable]] = {
        V0AuthenticationRequest: authenticate,
        JobStatusUpdate: job_status_update,
        V0MachineSpecsUpdate: machine_specs_update,
        V0Heartbeat: heartbeat,
    }
