import base64
import shlex
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from math import ceil
from operator import attrgetter
from typing import ClassVar
from uuid import uuid4

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from compute_horde.base.output_upload import (
    MultiUpload,
    SingleFileUpload,
    ZipAndHttpPutUpload,
)
from compute_horde.base.volume import (
    HuggingfaceVolume,
    MultiVolume,
    ZipUrlVolume,
)
from compute_horde.executor_class import DEFAULT_EXECUTOR_CLASS
from compute_horde.fv_protocol.facilitator_requests import (
    JobRequest,
    Signature,
    V0JobRequest,
    V1JobRequest,
    V2JobRequest,
)
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import CheckConstraint, F, Max, OuterRef, Prefetch, Q, QuerySet, Subquery, UniqueConstraint
from django.urls import reverse
from django.utils.timezone import now
from django_prometheus.models import ExportModelOperationsMixin
from django_pydantic_field import SchemaField
from structlog import get_logger
from structlog.contextvars import bound_contextvars
from tenacity import retry, retry_if_exception_type, stop_after_delay, wait_fixed

from .schemas import (
    JobStatusMetadata,
    MuliVolumeAllowedVolume,
)
from .utils import create_signed_download_url, create_signed_upload_url, safe_config

log = get_logger(__name__)


class MutuallyExclusiveFieldsError(ValidationError):
    pass


class JobNotFinishedError(Exception):
    pass


class SignatureInfo(models.Model):
    """
    Model for storing signed requests issued through Facilitator SDK.
    """

    signature_type = models.CharField(max_length=255, db_comment="type of the signature (e.g. 'bittensor')")
    signatory = models.CharField(
        max_length=1000, db_comment="identity of the signer (e.g. sa58 address if signature_type == bittensor"
    )
    timestamp_ns = models.BigIntegerField(
        db_comment="UNIX timestamp in nanoseconds; required for signature verification"
    )
    signature = models.BinaryField(db_comment="signature of the payload")

    signed_payload = models.JSONField(db_comment="raw payload that was signed")

    @classmethod
    def from_signature(cls, user, signature):
        return cls.objects.create(
            user=user,
            timestamp_ns=signature.timestamp_ns,
            signature=signature.signature,
            signed_payload=signature.signed_payload,
        )


class AbstractNodeQuerySet(models.QuerySet):
    def with_last_job_time(self) -> QuerySet:
        return self.annotate(last_job_time=Max("jobs__created_at"))


class AbstractNode(models.Model):
    ss58_address = models.CharField(max_length=48, unique=True)
    is_active = models.BooleanField()

    objects = AbstractNodeQuerySet.as_manager()

    class Meta:
        abstract = True
        constraints = [
            UniqueConstraint(fields=["ss58_address"], name="unique_%(class)s_ss58_address"),
        ]

    def __str__(self) -> str:
        return self.ss58_address


class Validator(AbstractNode):
    version = models.CharField(max_length=255, blank=True, default="")
    runner_version = models.CharField(max_length=255, blank=True, default="")


class Miner(AbstractNode):
    pass


class MinerVersion(models.Model):
    miner = models.ForeignKey(Miner, on_delete=models.CASCADE, related_name="versions")
    version = models.CharField(max_length=255, blank=True, default="")
    runner_version = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["created_at"]),
        ]


class UserPreferences(models.Model):
    user = models.OneToOneField("auth.User", on_delete=models.CASCADE, related_name="preferences")
    validators = models.ManyToManyField(
        Validator,
        blank=True,
        related_name="preferred_by_users",
        help_text="validators which will be prioritized for this user; if none of them are present, \
                   other validators will be used",
    )
    miners = models.ManyToManyField(
        Miner,
        blank=True,
        related_name="preferred_by_users",
        help_text="miners which will be prioritized for this user; if none of them are present, \
                   other miners will be used",
    )
    exclusive = models.BooleanField(
        default=False, help_text="If set only preference miners/validators are used. Error rised if unavailable."
    )

    def __str__(self) -> str:
        return f"Preferences for {self.user.username}"


class Channel(models.Model):
    """
    This is a simple model to remember Validator-channel association.

    When new WS connection is established, new instance of this class is instantiated,
    thus we always know which channel(s) belong to which validator. When validator
    disconnects from WS, this instance is deleted as well.

    This is a more straightforward approach than using Django Channels groups which
    don't have a built-in method to get all channels within a group.
    """

    name = models.CharField(max_length=255)
    validator = models.ForeignKey(Validator, on_delete=models.CASCADE, related_name="channels")
    last_heartbeat = models.DateTimeField(default=now)

    class Meta:
        constraints = [
            UniqueConstraint(fields=["name"], name="unique_channel_name"),
        ]

    def __str__(self) -> str:
        return self.name


class JobQuerySet(models.QuerySet):
    def with_statuses(self) -> QuerySet:
        return self.prefetch_related(Prefetch("statuses", queryset=JobStatus.objects.order_by("created_at")))


class Job(ExportModelOperationsMixin("job"), models.Model):
    JOB_TIMEOUT: ClassVar = timedelta(minutes=5, seconds=30)

    uuid = models.UUIDField(primary_key=True, editable=False, blank=True)
    user = models.ForeignKey("auth.User", on_delete=models.PROTECT, related_name="jobs")
    validator = models.ForeignKey(Validator, blank=True, on_delete=models.PROTECT, related_name="jobs")
    miner = models.ForeignKey(Miner, blank=True, null=True, on_delete=models.PROTECT, related_name="jobs")
    signature_info = models.ForeignKey(
        SignatureInfo, blank=True, default=None, null=True, on_delete=models.PROTECT, related_name="jobs"
    )
    created_at = models.DateTimeField(default=now)

    executor_class = models.CharField(
        max_length=255, default=DEFAULT_EXECUTOR_CLASS, help_text="executor hardware class"
    )
    docker_image = models.CharField(max_length=255, blank=True, help_text="docker image for job execution")
    raw_script = models.TextField(blank=True, help_text="raw script to be executed")
    args = models.TextField(blank=True, help_text="arguments passed to the script or docker image")
    env = models.JSONField(blank=True, default=dict, help_text="environment variables for the job")
    use_gpu = models.BooleanField(default=False, help_text="Whether to use GPU for the job")
    input_url = models.URLField(blank=True, help_text="URL to the input data source", max_length=1000)
    hf_repo_id = models.CharField(max_length=255, blank=True, default="", help_text="Huggingface model repo id")
    hf_revision = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Huggingface model revision id: branch name / tag / commit hash",
    )
    output_upload_url = models.TextField(blank=True, help_text="URL for uploading output")
    output_download_url = models.TextField(blank=True, help_text="URL for retrieving output")
    output_download_url_expires_at = models.DateTimeField(blank=True)
    target_validator_hotkey = models.TextField(blank=True, default=None, null=True, help_text="target validator")
    volumes = SchemaField(schema=list[MuliVolumeAllowedVolume], blank=True, default=list)
    uploads = SchemaField(schema=list[SingleFileUpload], blank=True, default=list)

    tag = models.CharField(max_length=255, blank=True, default="", help_text="may be used to group jobs")

    objects = JobQuerySet.as_manager()

    class Meta:
        constraints = [
            CheckConstraint(
                check=(Q(docker_image="") & ~Q(raw_script="") | ~Q(docker_image="") & Q(raw_script="")),
                name="docker_image_or_raw_script",
            ),
        ]
        indexes = [
            models.Index(fields=["validator", "-created_at"], name="idx_job_validator_created_at"),
        ]

    @property
    def filename(self) -> str:
        assert self.uuid
        return f"{self.uuid}.zip"

    def save(self, *args, **kwargs) -> None:
        is_new = self.pk is None

        self.uuid = self.uuid or uuid4()
        self.output_upload_url = self.output_upload_url or create_signed_upload_url(self.filename)
        if self.is_download_url_expired():
            self.reset_download_url()

        # if there is no validator selected -> we need a transaction for locking
        # active validators during selection process
        with transaction.atomic(), bound_contextvars(job=self):
            self.validator = getattr(self, "validator", None) or self.select_validator()
            if self.target_validator_hotkey is None:
                self.miner = getattr(self, "miner", None) or self.select_miner()
            else:
                self.miner = None
            super().save(*args, **kwargs)
            if is_new:
                self.send_to_validator()

    def select_validator(self) -> Validator:
        """
        Select a validator for the job.

        Currently the one with least recent job request is selected.
        This method is expected to be run within a transaction.
        """

        # select validators which are currently connected via WS
        validator_ids: set[int] = set(
            Channel.objects.filter(last_heartbeat__gte=now() - timedelta(minutes=3)).values_list(
                "validator_id", flat=True
            )
        )
        log.debug("connected validators", validator_ids=validator_ids)

        if self.target_validator_hotkey is not None:
            if self.signature_info is None:
                raise ValueError("Request must be signed when target_validator_hotkey is set")
            validator = Validator.objects.filter(ss58_address=self.target_validator_hotkey).first()
            if validator and validator.id in validator_ids:
                log.debug("selected (targeted) validator", validator=validator)
                return validator
            raise Validator.DoesNotExist

        # use user's preferences to select specific validators
        with suppress(UserPreferences.DoesNotExist):
            exclusive_preference = self.user.preferences.exclusive
            if preferred_validator_ids := set(self.user.preferences.validators.values_list("id", flat=True)):
                # we either select one of preferred validators, but if none of them is available - select any
                log.debug("preferred validators", user=self.user, preferred_validators=preferred_validator_ids)
                available_preferred_validator_ids = validator_ids & preferred_validator_ids
                if available_preferred_validator_ids or exclusive_preference:
                    validator_ids = available_preferred_validator_ids

        log.debug("choosing from validators", validator_ids=validator_ids)
        debug_validator = Validator.objects.filter(
            ss58_address="5HBVrFGy6oYhhh71m9fFGYD7zbKyAeHnWN8i8s9fJTBMCtEE"
        ).first()
        if debug_validator and debug_validator.id in validator_ids:
            return debug_validator
        try:
            validator = (
                Validator.objects.filter(
                    pk__in=validator_ids,
                )
                .with_last_job_time()
                .order_by(F("last_job_time").asc(nulls_first=True))
            )[0]
        except IndexError as exc:
            raise Validator.DoesNotExist from exc

        log.debug("selected validator", validator=validator)
        return validator

    def select_miner(self) -> Miner:
        """
        Select a miner for the job.

        Currently, the one with the least number of jobs being executed is selected.
        This method is expected to be called from within a transaction.
        """

        last_jobs_subquery = Subquery(
            Job.objects.filter(miner=OuterRef("miner_id")).order_by("-created_at").values_list("pk", flat=True)[:10]
        )
        miners = (
            Miner.objects.select_for_update()
            .filter(is_active=True)
            .prefetch_related(
                Prefetch(
                    "jobs",
                    queryset=(
                        Job.objects.filter(pk__in=last_jobs_subquery)
                        .order_by("-created_at")
                        .prefetch_related(Prefetch("statuses", queryset=JobStatus.objects.order_by("-created_at")))
                    ),
                )
            )
        )

        # select miners which don't have active jobs
        miners = [miner for miner in miners if all(job.is_completed() for job in miner.jobs.all())]

        # use user's preferences to select specific miners
        with suppress(UserPreferences.DoesNotExist):
            exclusive_preference = self.user.preferences.exclusive
            if preferred_miners := self.user.preferences.miners.all():
                # we either select one of preferred miners, but if none of them is available - select any
                available_preferred_miners = [miner for miner in miners if miner in preferred_miners]
                if available_preferred_miners or exclusive_preference:
                    miners = available_preferred_miners

        if not miners:
            raise Miner.DoesNotExist

        for miner in miners:
            if miner.ss58_address == "5HBVrFGy6oYhhh71m9fFGYD7zbKyAeHnWN8i8s9fJTBMCtEE":
                return miner
        # sort miners by last job time
        miners.sort(
            key=lambda miner: jobs[0].created_at if (jobs := miner.jobs.all()) else datetime.min.replace(tzinfo=UTC)
        )
        return miners[0]

    def is_download_url_expired(self) -> bool:
        return (
            not self.output_download_url
            or not self.output_download_url_expires_at
            or now() > self.output_download_url_expires_at
        )

    def reset_download_url(self) -> None:
        self.output_download_url = create_signed_download_url(self.filename)
        self.output_download_url_expires_at = now() + settings.DOWNLOAD_PRESIGNED_URL_LIFETIME

    def clean(self, *args, **kwargs) -> None:
        if (self.docker_image == "") == (self.raw_script == ""):
            raise MutuallyExclusiveFieldsError("Either docker_image or raw_script should be provided, but not both")
        return super().clean(*args, **kwargs)

    def __str__(self) -> str:
        return f"Job {self.pk} by {self.user.username}"

    def get_absolute_url(self) -> str:
        return reverse("job/detail", kwargs={"pk": self.pk})

    @property
    def statuses_ordered(self) -> list["JobStatus"]:
        # sort in python to reuse the prefetch cache
        return sorted(self.statuses.all(), key=attrgetter("created_at"))

    @property
    def status(self) -> "JobStatus":
        return self.statuses_ordered[-1]

    def is_completed(self) -> bool:
        return self.status.status in JobStatus.FINAL_STATUS_VALUES or self.created_at < now() - self.JOB_TIMEOUT

    @property
    def elapsed(self) -> timedelta:
        """Time between first and last statuses."""
        statuses = self.statuses_ordered
        return statuses[-1].created_at - statuses[0].created_at

    def as_job_request(self) -> JobRequest:
        if safe_config.JOB_REQUEST_VERSION == 0:
            if self.uploads or self.volumes:
                raise ValueError("upload and volumes are not supported in version 0 of job protocol")
            return V0JobRequest(
                uuid=str(self.uuid),
                miner_hotkey=self.miner.ss58_address,
                executor_class=self.executor_class,
                docker_image=self.docker_image,
                raw_script=self.raw_script,
                args=shlex.split(self.args),
                env=self.env,
                use_gpu=self.use_gpu,
                input_url=self.input_url,
                output_url=self.output_upload_url,
            )
        else:
            if self.input_url or self.volumes or self.hf_repo_id:
                subvolumes = []
                if self.input_url:
                    subvolumes.append(
                        ZipUrlVolume(
                            contents=self.input_url,
                            relative_path="",
                        )
                    )
                if self.hf_repo_id:
                    subvolumes.append(
                        HuggingfaceVolume(
                            repo_id=self.hf_repo_id,
                            revision=self.hf_revision,
                            relative_path="",
                        )
                    )
                subvolumes.extend(self.volumes)
                volume = MultiVolume(volumes=subvolumes)
            else:
                volume = None
            if self.output_upload_url or self.uploads:
                output_upload = MultiUpload(
                    uploads=self.uploads,
                    system_output=ZipAndHttpPutUpload(url=self.output_upload_url) if self.output_upload_url else None,
                )
            else:
                output_upload = None
            if self.signature_info is not None:
                assert self.miner is None
                signature = Signature(
                    signature_type=self.signature_info.signature_type,
                    signatory=self.signature_info.signatory,
                    timestamp_ns=self.signature_info.timestamp_ns,
                    signature=base64.b64encode(self.signature_info.signature).decode("utf8"),
                )
                return V2JobRequest(
                    uuid=str(self.uuid),
                    executor_class=self.executor_class,
                    docker_image=self.docker_image,
                    raw_script=self.raw_script,
                    args=shlex.split(self.args),
                    env=self.env,
                    use_gpu=self.use_gpu,
                    volume=volume,
                    output_upload=output_upload,
                    signature=signature,
                )
            else:
                assert self.miner is not None
                return V1JobRequest(
                    uuid=str(self.uuid),
                    miner_hotkey=self.miner.ss58_address,
                    executor_class=self.executor_class,
                    docker_image=self.docker_image,
                    raw_script=self.raw_script,
                    args=shlex.split(self.args),
                    env=self.env,
                    use_gpu=self.use_gpu,
                    volume=volume,
                    output_upload=output_upload,
                )

    def send_to_validator(self) -> None:
        channels_names = Channel.objects.filter(validator=self.validator).values_list("name", flat=True)
        log.debug("sending job to validator", job=self, validator=self.validator, channels_names=channels_names)
        channel_layer = get_channel_layer()

        send = async_to_sync(channel_layer.send)
        job_request = self.as_job_request().dict()
        for channel_name in channels_names:
            send(channel_name, job_request)

        JobStatus.objects.create(job=self, status=JobStatus.Status.SENT)

    @retry(
        stop=stop_after_delay(JOB_TIMEOUT),
        wait=wait_fixed(3),
        retry=retry_if_exception_type(JobNotFinishedError),
        reraise=True,
    )
    def wait_completed(self: str, on_check: Callable = lambda job: None) -> "JobStatus.Status":
        job = self.__class__.objects.prefetch_related("statuses").get(uuid=self.uuid)
        on_check(job)
        if job.is_completed():
            return job.status

        log.debug("Still waiting for results from job %s", self.uuid)
        raise JobNotFinishedError


class JobStatus(ExportModelOperationsMixin("job_status"), models.Model):
    class Status(models.IntegerChoices):
        FAILED = -2
        REJECTED = -1
        SENT = 0
        ACCEPTED = 1
        COMPLETED = 2

    FINAL_STATUS_VALUES = (
        Status.COMPLETED,
        Status.REJECTED,
        Status.FAILED,
    )

    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="statuses")
    status = models.SmallIntegerField(choices=Status.choices)
    metadata = models.JSONField(blank=True, default=dict)
    created_at = models.DateTimeField(default=now)

    class Meta:
        verbose_name_plural = "Job statuses"
        constraints = [
            UniqueConstraint(fields=["job", "status"], name="unique_job_status"),
        ]

    def __str__(self) -> str:
        return self.get_status_display()

    @property
    def meta(self) -> JobStatusMetadata | None:
        if self.metadata:
            return JobStatusMetadata.parse_obj(self.metadata)


class JobFeedback(models.Model):
    """
    Represents end user feedback for a job.
    """

    job = models.OneToOneField(Job, on_delete=models.CASCADE, related_name="feedback")
    user = models.ForeignKey("auth.User", on_delete=models.PROTECT, related_name="feedback")
    created_at = models.DateTimeField(default=now)

    result_correctness = models.FloatField(default=1, help_text="<0-1> where 1 means 100% correct")
    expected_duration = models.FloatField(blank=True, null=True, help_text="Expected duration of the job in seconds")
    signature_info = models.ForeignKey(SignatureInfo, on_delete=models.CASCADE)

    def __str__(self) -> str:
        return f"Feedback for job {self.job.uuid} by {self.user.username}"


class Subnet(models.Model):
    name = models.CharField(max_length=255)
    uid = models.PositiveSmallIntegerField()

    class Meta:
        constraints = [
            UniqueConstraint(fields=["name"], name="unique_subnet_name"),
            UniqueConstraint(fields=["uid"], name="unique_subnet_uid"),
        ]

    def __str__(self) -> str:
        return f"{self.name} (SN{self.uid})"


class GPU(models.Model):
    name = models.CharField(max_length=255, unique=True)
    capacity = models.PositiveIntegerField(default=0, help_text="in GB")
    memory_type = models.CharField(max_length=255, default="")
    bus_width = models.PositiveIntegerField(default=0, help_text="in bits")
    core_clock = models.PositiveIntegerField(default=0, help_text="in MHz")
    memory_clock = models.PositiveIntegerField(default=0, help_text="in MHz")
    fp16 = models.FloatField(default=0, help_text="in TFLOPS")
    fp32 = models.FloatField(default=0, help_text="in TFLOPS")
    fp64 = models.FloatField(default=0, help_text="in TFLOPS")

    def __str__(self) -> str:
        return self.name

    def get_verbose_name(self) -> str:
        memory_gb = ceil(self.capacity / 1024)
        return f"{self.name} {memory_gb}GB"


class GpuCount(models.Model):
    subnet = models.ForeignKey(Subnet, on_delete=models.CASCADE, related_name="gpu_counts")
    gpu = models.ForeignKey(GPU, on_delete=models.CASCADE, related_name="counts")
    count = models.PositiveIntegerField()
    measured_at = models.DateTimeField(blank=True, default=now)

    class Meta:
        constraints = [
            UniqueConstraint(fields=["subnet", "gpu", "measured_at"], name="unique_gpu_count"),
        ]

    def __str__(self) -> str:
        return f"{self.count}x {self.gpu.name}"


class HardwareState(models.Model):
    subnet = models.ForeignKey(Subnet, on_delete=models.CASCADE, related_name="hardware_states")
    state = models.JSONField()
    measured_at = models.DateTimeField(default=now)

    def __str__(self) -> str:
        return f"{self.measured_at}"


class OtherSpecs(models.Model):
    # other specs
    os = models.CharField(max_length=255, blank=True, null=True, default="")
    virtualization = models.CharField(max_length=255, blank=True, null=True, default="")
    total_ram = models.PositiveIntegerField(default=0, blank=True, null=True, help_text="in GB")
    total_hdd = models.PositiveIntegerField(default=0, blank=True, null=True, help_text="in GB")
    asn = models.PositiveIntegerField(default=0, blank=True, null=True)

    class Meta:
        constraints = [
            UniqueConstraint(
                fields=["os", "virtualization", "total_ram", "total_hdd", "asn"], name="unique_other_specs"
            ),
        ]


class CpuSpecs(models.Model):
    # cpu specs
    cpu_model = models.CharField(max_length=255, default="")
    cpu_count = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            UniqueConstraint(fields=["cpu_model", "cpu_count"], name="unique_cpu_specs"),
        ]


class RawSpecsData(models.Model):
    data = models.JSONField()

    class Meta:
        constraints = [
            UniqueConstraint(fields=["data"], name="unique_raw_specs_data"),
        ]
        indexes = [
            models.Index(fields=["data"], name="idx_raw_spec_data"),
        ]


class ExecutorSpecsSnapshot(ExportModelOperationsMixin("executor_specs_snapshot"), models.Model):
    batch_id = models.UUIDField(default=None, null=True, blank=True, help_text="job batch id")
    miner = models.ForeignKey(Miner, on_delete=models.CASCADE)
    validator = models.ForeignKey(Validator, on_delete=models.CASCADE)
    measured_at = models.DateTimeField(default=now)

    raw_specs = models.ForeignKey(RawSpecsData, on_delete=models.PROTECT, related_name="raw_specs_data")

    def __str__(self) -> str:
        return (
            f"raw spec for miner: {self.miner.ss58_address}, batch_id {self.batch_id} measured_at: {self.measured_at}"
        )


class ParsedSpecsData(models.Model):
    id = models.OneToOneField(RawSpecsData, primary_key=True, on_delete=models.PROTECT)
    cpu_specs = models.ForeignKey(CpuSpecs, on_delete=models.PROTECT, related_name="cpu_specs")
    other_specs = models.ForeignKey(OtherSpecs, on_delete=models.PROTECT, related_name="other_specs")


class GpuSpecs(ExportModelOperationsMixin("gpu_specs"), models.Model):
    parsed_specs = models.ForeignKey(ParsedSpecsData, on_delete=models.CASCADE, related_name="specs")

    gpu_model = models.ForeignKey(GPU, default=0, on_delete=models.CASCADE)
    gpu_count = models.PositiveIntegerField(default=0)

    capacity = models.PositiveIntegerField(default=0, help_text="in MB")
    cuda = models.CharField(max_length=255, default="", help_text="version")
    driver = models.CharField(max_length=255, default="", help_text="version")
    graphics_speed = models.PositiveIntegerField(default=0, help_text="in MHz")
    memory_speed = models.PositiveIntegerField(default=0, help_text="in MHz")
    power_limit = models.FloatField(default=0, help_text="in MHz")
    uuid = models.CharField(max_length=255, blank=True, null=True, default="")
    serial = models.CharField(max_length=255, blank=True, null=True, default="")

    class Meta:
        constraints = [
            UniqueConstraint(
                fields=[
                    "parsed_specs",
                    "gpu_model",
                    "gpu_count",
                    "capacity",
                    "cuda",
                    "driver",
                    "graphics_speed",
                    "memory_speed",
                    "power_limit",
                    "uuid",
                    "serial",
                ],
                name="unique_gpu_specs",
            ),
        ]


# TO BE DEPRECATED
class RawSpecsSnapshot(models.Model):
    miner = models.ForeignKey(Miner, on_delete=models.CASCADE, related_name="raw_specs")
    validator = models.ForeignKey(Validator, on_delete=models.CASCADE, related_name="raw_specs")
    state = models.JSONField()
    measured_at = models.DateTimeField(default=now)

    def __str__(self) -> str:
        return f"raw spec for miner: {self.miner.ss58_address} measured_at: {self.measured_at}"

    class Meta:
        constraints = [
            UniqueConstraint(fields=["miner", "measured_at"], name="unique_raw_spec_miner_measured_at"),
        ]
        indexes = [
            models.Index(fields=["miner", "measured_at"], name="idx_raw_spec_miner_measured_at"),
        ]
