import enum
import json
import re
from collections.abc import Mapping
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Annotated, Literal
from urllib.parse import urlparse

import bittensor
import pydantic
from compute_horde.executor_class import ExecutorClass
from pydantic import BaseModel, Extra, Field, field_serializer, field_validator

SAFE_DOMAIN_REGEX = re.compile(r".*")


class OutputUploadType(str, enum.Enum):
    zip_and_http_put = "zip_and_http_put"
    multi_upload = "multi_upload"
    single_file_post = "single_file_post"
    single_file_put = "single_file_put"

    def __str__(self):
        return str.__str__(self)


class ZipAndHttpPutUpload(pydantic.BaseModel):
    output_upload_type: Literal[OutputUploadType.zip_and_http_put] = OutputUploadType.zip_and_http_put
    url: str


class SingleFilePostUpload(pydantic.BaseModel):
    output_upload_type: Literal[OutputUploadType.single_file_post] = OutputUploadType.single_file_post
    url: str
    form_fields: Mapping[str, str] | None = None
    relative_path: str
    signed_headers: Mapping[str, str] | None = None

    def is_safe(self) -> bool:
        domain = urlparse(self.url).netloc
        if SAFE_DOMAIN_REGEX.fullmatch(domain):
            return True
        return False


class SingleFilePutUpload(pydantic.BaseModel):
    output_upload_type: Literal[OutputUploadType.single_file_put] = OutputUploadType.single_file_put
    url: str
    relative_path: str
    signed_headers: Mapping[str, str] | None = None

    def is_safe(self) -> bool:
        domain = urlparse(self.url).netloc
        if SAFE_DOMAIN_REGEX.fullmatch(domain):
            return True
        return False


SingleFileUpload = Annotated[
    SingleFilePostUpload | SingleFilePutUpload,
    Field(discriminator="output_upload_type"),
]


class MultiUpload(pydantic.BaseModel):
    output_upload_type: Literal[OutputUploadType.multi_upload] = OutputUploadType.multi_upload
    uploads: list[SingleFileUpload]
    # allow custom uploads for stdout and stderr
    system_output: ZipAndHttpPutUpload | None = None


class VolumeType(str, enum.Enum):
    inline = "inline"
    zip_url = "zip_url"
    single_file = "single_file"
    multi_volume = "multi_volume"

    def __str__(self):
        return str.__str__(self)


class ZipUrlVolume(pydantic.BaseModel):
    volume_type: Literal[VolumeType.zip_url] = VolumeType.zip_url
    contents: str  # backwards compatible
    relative_path: str | None = Field(default=None)

    def is_safe(self) -> bool:
        domain = urlparse(self.contents).netloc
        if SAFE_DOMAIN_REGEX.fullmatch(domain):
            return True
        return False


class SingleFileVolume(pydantic.BaseModel):
    volume_type: Literal[VolumeType.single_file] = VolumeType.single_file
    url: str
    relative_path: str

    def is_safe(self) -> bool:
        domain = urlparse(self.url).netloc
        if SAFE_DOMAIN_REGEX.fullmatch(domain):
            return True
        return False


MuliVolumeAllowedVolume = Annotated[ZipUrlVolume | SingleFileVolume, Field(discriminator="volume_type")]


class MultiVolume(pydantic.BaseModel):
    volume_type: Literal[VolumeType.multi_volume] = VolumeType.multi_volume
    volumes: list[MuliVolumeAllowedVolume]

    def is_safe(self) -> bool:
        return all(volume.is_safe() for volume in self.volumes)


class MinerResponse(BaseModel, extra=Extra.allow):
    job_uuid: str
    message_type: str
    docker_process_stderr: str
    docker_process_stdout: str


class JobStatusMetadata(BaseModel, extra=Extra.allow):
    comment: str
    miner_response: MinerResponse | None = None


class JobStatusUpdate(BaseModel, extra=Extra.forbid):
    """
    Message sent from validator to this app in response to NewJobRequest.
    """

    message_type: Literal["V0JobStatusUpdate"] = Field(default="V0JobStatusUpdate")
    uuid: str
    status: Literal["failed", "rejected", "accepted", "completed"]
    metadata: JobStatusMetadata | None = None


class ForceDisconnect(BaseModel, extra=Extra.forbid):
    """Message sent when validator is no longer valid and should be disconnected"""

    type: Literal["validator.disconnect"] = Field("validator.disconnect")


class CpuSpec(BaseModel, extra=Extra.forbid):
    model: str | None = None
    count: int
    frequency: Decimal | None = None
    clocks: list[float] | None = None


class GpuDetails(BaseModel, extra=Extra.forbid):
    name: str
    capacity: int | float | None = Field(default=None, description="in MB")
    cuda: str | None = None
    driver: str | None = None
    graphics_speed: int | None = Field(default=None, description="in MHz")
    memory_speed: int | None = Field(default=None, description="in MHz")
    power_limit: float | None = Field(default=None, description="in W")
    uuid: str | None = None
    serial: str | None = None

    @field_validator("power_limit", mode="before")
    @classmethod
    def parse_age(cls, v):
        try:
            return float(v)
        except Exception:
            return None


class GpuSpec(BaseModel, extra=Extra.forbid):
    capacity: int | float | None = None
    count: int | None = None
    details: list[GpuDetails] = []
    graphics_speed: int | None = Field(default=None, description="in MHz")
    memory_speed: int | None = Field(default=None, description="in MHz")


class HardDiskSpec(BaseModel, extra=Extra.forbid):
    total: int | float | None = Field(default=None, description="in kiB")
    free: int | float | None = Field(default=None, description="in kiB")
    used: int | float | None = Field(default=None, description="in kiB")
    read_speed: Decimal | None = None
    write_speed: Decimal | None = None

    @field_validator("*", mode="before")
    @classmethod
    def empty_str_to_none(cls, v):
        if v == "":
            return None
        return v

    def get_total_gb(self) -> float | None:
        if self.total is None:
            return None
        return self.total / 1024 / 1024


class RamSpec(BaseModel, extra=Extra.forbid):
    total: int | float | None = Field(default=None, description="in kiB")
    free: int | float | None = Field(default=None, description="in kiB")
    available: int | float | None = Field(default=None, description="in kiB")
    used: int | float | None = Field(default=None, description="in kiB")
    read_speed: Decimal | None = None
    write_speed: Decimal | None = None
    swap_free: int | None = Field(default=None, description="in kiB")
    swap_total: int | None = Field(default=None, description="in kiB")
    swap_used: int | None = Field(default=None, description="in kiB")

    @field_validator("*", mode="before")
    @classmethod
    def empty_str_to_none(cls, v):
        if v == "":
            return None
        return v

    def get_total_gb(self) -> float | None:
        if self.total is None:
            return None
        return self.total / 1024 / 1024


class HardwareSpec(BaseModel, extra=Extra.allow):
    cpu: CpuSpec
    gpu: GpuSpec | None = None
    hard_disk: HardDiskSpec
    has_docker: bool | None = None
    ram: RamSpec
    virtualization: str | None = None
    os: str | None = None


# Origin of receipt models may be found in ComputeHorde repo:
# https://github.com/backend-developers-ltd/ComputeHorde
# from compute_horde.mv_protocol.validator_requests
class ReceiptPayload(BaseModel):
    job_uuid: str
    miner_hotkey: str
    validator_hotkey: str

    def blob_for_signing(self):
        # pydantic v2 does not support sort_keys anymore.
        return json.dumps(self.model_dump(), sort_keys=True, default=_json_dumps_default)


class JobFinishedReceiptPayload(ReceiptPayload):
    time_started: datetime
    time_took_us: int  # micro-seconds
    score_str: str

    @property
    def time_took(self):
        return timedelta(microseconds=self.time_took_us)

    @property
    def score(self):
        return float(self.score_str)

    @field_serializer("time_started")
    def serialize_dt(self, dt: datetime, _info):
        return dt.isoformat()


class JobStartedReceiptPayload(ReceiptPayload):
    executor_class: ExecutorClass
    time_accepted: datetime
    max_timeout: int  # seconds

    @field_serializer("time_accepted")
    def serialize_dt(self, dt: datetime, _info):
        return dt.isoformat()


class Receipt(BaseModel):
    payload: JobStartedReceiptPayload | JobFinishedReceiptPayload
    validator_signature: str
    miner_signature: str

    def verify_miner_signature(self):
        miner_keypair = bittensor.Keypair(ss58_address=self.payload.miner_hotkey)
        return miner_keypair.verify(self.payload.blob_for_signing(), self.miner_signature)

    def verify_validator_signature(self):
        validator_keypair = bittensor.Keypair(ss58_address=self.payload.validator_hotkey)
        return validator_keypair.verify(self.payload.blob_for_signing(), self.validator_signature)


def _json_dumps_default(obj):
    if isinstance(obj, datetime):
        return obj.isoformat()

    raise TypeError
