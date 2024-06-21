import django_filters
from django.core.exceptions import ObjectDoesNotExist
from django_filters import fields
from django_filters.rest_framework import DjangoFilterBackend
from django_pydantic_field.rest_framework import SchemaField
from rest_framework import mixins, routers, serializers, status, viewsets
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.generics import get_object_or_404
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from structlog import get_logger

from .middleware.signature_middleware import require_signature
from .models import Job, JobFeedback
from .schemas import MuliVolumeAllowedVolume, SingleFileUpload
from .utils import safe_config

logger = get_logger(__name__)


class SmartSchemaField(SchemaField):
    def get_initial(self, *args, **kwargs):
        value = super().get_initial(*args, **kwargs)
        if value is None and getattr(self.schema, "__origin__", None) is list:
            return []
        return value


class Conflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "A conflict occurred."
    default_code = "conflict"


class DefaultModelPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = "page_size"
    max_page_size = 256


class JobSerializer(serializers.HyperlinkedModelSerializer):
    class Meta:
        model = Job
        fields = (
            "uuid",
            "created_at",
            "last_update",
            "status",
            "docker_image",
            "raw_script",
            "args",
            "env",
            "use_gpu",
            "input_url",
            "output_download_url",
            "tag",
            "stdout",
            "volumes",
            "uploads",
        )
        read_only_fields = (
            "created_at",
            "output_download_url",
        )

    uploads = SmartSchemaField(schema=list[SingleFileUpload], required=False)
    volumes = SmartSchemaField(schema=list[MuliVolumeAllowedVolume], required=False)

    status = serializers.SerializerMethodField()
    last_update = serializers.SerializerMethodField()
    stdout = serializers.SerializerMethodField()

    def get_status(self, obj):
        return obj.status.get_status_display()

    def get_stdout(self, obj):
        meta = obj.status.meta
        if meta and meta.miner_response:
            return meta.miner_response.docker_process_stdout
        return ""

    def get_last_update(self, obj):
        return obj.status.created_at


class DynamicJobFields:
    def get_fields(self):
        fields = super().get_fields()
        # Check the Constance config value
        if safe_config.JOB_REQUEST_VERSION == 0:
            fields.pop("uploads", None)
            fields.pop("volumes", None)
        return fields


class RawJobSerializer(DynamicJobFields, JobSerializer):
    class Meta:
        model = Job
        fields = JobSerializer.Meta.fields
        read_only_fields = tuple(
            set(JobSerializer.Meta.fields) - {"raw_script", "input_url", "tag", "volumes", "uploads"}
        )


class DockerJobSerializer(DynamicJobFields, JobSerializer):
    class Meta:
        model = Job
        fields = JobSerializer.Meta.fields
        read_only_fields = tuple(
            set(JobSerializer.Meta.fields)
            - {"docker_image", "args", "env", "use_gpu", "input_url", "tag", "volumes", "uploads"}
        )


class JobFeedbackSerializer(serializers.ModelSerializer):
    result_correctness = serializers.FloatField(min_value=0, max_value=1)
    expected_duration = serializers.FloatField(min_value=0, required=False)

    class Meta:
        model = JobFeedback
        fields = ["result_correctness", "expected_duration"]


class BaseCreateJobViewSet(mixins.CreateModelMixin, viewsets.GenericViewSet):
    queryset = Job.objects.with_statuses()

    def perform_create(self, serializer):
        try:
            serializer.save(user=self.request.user)
        except ObjectDoesNotExist as exc:
            model_name = exc.__class__.__qualname__.partition(".")[0]
            raise ValidationError(f"Could not select {model_name}")


class NonValidatingMultipleChoiceField(fields.MultipleChoiceField):
    def validate(self, value):
        pass


class NonValidatingMultipleChoiceFilter(django_filters.MultipleChoiceFilter):
    field_class = NonValidatingMultipleChoiceField


class JobViewSetFilter(django_filters.FilterSet):
    uuid = NonValidatingMultipleChoiceFilter(field_name="uuid")

    class Meta:
        model = Job
        fields = ["tag", "uuid"]


class JobViewSet(mixins.RetrieveModelMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    queryset = Job.objects.with_statuses()
    serializer_class = JobSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = DefaultModelPagination
    filter_backends = [DjangoFilterBackend]
    filterset_class = JobViewSetFilter

    def get_queryset(self):
        return self.queryset.filter(user=self.request.user)


class RawJobViewset(BaseCreateJobViewSet):
    serializer_class = RawJobSerializer


class DockerJobViewset(BaseCreateJobViewSet):
    serializer_class = DockerJobSerializer


class JobFeedbackViewSet(mixins.CreateModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    serializer_class = JobFeedbackSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        job_uuid = self.kwargs["job_uuid"]
        return JobFeedback.objects.filter(
            job__uuid=job_uuid,
            job__user=self.request.user,
            user=self.request.user,
        )

    def get_object(self):
        return self.get_queryset().get()

    def get_parent_job(self):
        job_uuid = self.kwargs.get("job_uuid")
        return get_object_or_404(Job, uuid=job_uuid, user=self.request.user)

    def perform_create(self, serializer):
        require_signature(self.request)
        job = self.get_parent_job()
        if JobFeedback.objects.filter(job=job, user=self.request.user).exists():
            raise Conflict("Feedback already exists")

        serializer.save(
            job=job,
            user=self.request.user,
            signature_info=self.request.signature_info,
        )

    def get(self, request, *args, **kwargs):
        return self.retrieve(request, *args, **kwargs)

    def put(self, request, *args, **kwargs):
        return self.create(request, *args, **kwargs)


class APIRootView(routers.DefaultRouter.APIRootView):
    description = "api-root"


class APIRouter(routers.DefaultRouter):
    APIRootView = APIRootView


router = APIRouter()
router.register(r"jobs", JobViewSet)
router.register(r"job-docker", DockerJobViewset, basename="job_docker")
router.register(r"job-raw", RawJobViewset, basename="job_raw")
router.register(r"jobs/(?P<job_uuid>[^/.]+)/feedback", JobFeedbackViewSet, basename="job_feedback")
