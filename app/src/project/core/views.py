from allauth.account.views import SignupView as AllauthSignupView
from constance import config
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import QuerySet
from django.forms.models import ModelForm
from django.http.response import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.utils.decorators import method_decorator
from django.views.generic import CreateView, DetailView, ListView
from fingerprint.views import fingerprint
from rest_framework.authtoken.models import Token

from .forms import DockerImageJobForm, GenerateAPITokenForm, RawScriptJobForm
from .models import Job, Miner, Validator


class SignupView(AllauthSignupView):
    """Regular view but redirects if public registration is disabled"""

    def dispatch(self, *args, **kwargs) -> HttpResponse:
        if not config.ENABLE_PUBLIC_REGISTRATION:
            return render(self.request, "allauth/signup_disabled.html")
        return super().dispatch(*args, **kwargs)


@method_decorator(login_required, name="dispatch")
class JobListView(ListView):
    model = Job
    template_name = "core/job_list.html"
    context_object_name = "jobs"
    paginate_by = 20

    def get_queryset(self) -> QuerySet:
        return self.request.user.jobs.with_statuses().order_by("-created_at")


@method_decorator(login_required, name="dispatch")
@method_decorator(fingerprint, name="get")
class DockerImageJobCreateView(CreateView):
    model = Job
    form_class = DockerImageJobForm
    template_name = "core/job_create_docker_image.html"

    def get_form_kwargs(self) -> dict:
        """Prefill the form with the values from the referenced job"""

        form_kwargs = super().get_form_kwargs()
        if reference_pk := self.request.GET.get("ref"):
            job = get_object_or_404(Job, pk=reference_pk)
            form_kwargs.setdefault("initial", {}).update(
                {
                    "docker_image": job.docker_image,
                    "raw_script": job.raw_script,
                    "args": job.args,
                    "env": job.env,
                    "use_gpu": job.use_gpu,
                    "input_url": job.input_url,
                }
            )
        return form_kwargs

    def form_valid(self, form: ModelForm) -> HttpResponse:
        try:
            job = form.save(commit=False)
            job.user = self.request.user
            job.save()
        except Validator.DoesNotExist:
            messages.add_message(
                self.request,
                messages.ERROR,
                "No validators available, please try again later",
            )
            return super().get(self.request)
        except Miner.DoesNotExist:
            messages.add_message(
                self.request,
                messages.ERROR,
                "No miners available, please try again later",
            )
            return super().get(self.request)

        return HttpResponseRedirect(job.get_absolute_url())


@method_decorator(login_required, name="dispatch")
@method_decorator(fingerprint, name="get")
class RawScriptJobCreateView(CreateView):
    model = Job
    form_class = RawScriptJobForm
    template_name = "core/job_create_raw_script.html"

    def get_form_kwargs(self) -> dict:
        """Prefill the form with the values from the referenced job"""

        form_kwargs = super().get_form_kwargs()
        if reference_pk := self.request.GET.get("ref"):
            job = get_object_or_404(Job, pk=reference_pk)
            form_kwargs.setdefault("initial", {}).update(
                {
                    "raw_script": job.raw_script,
                    "input_url": job.input_url,
                }
            )
        return form_kwargs

    def form_valid(self, form: ModelForm) -> HttpResponse:
        try:
            job = form.save(commit=False)
            job.user = self.request.user
            job.save()
        except Validator.DoesNotExist:
            messages.add_message(
                self.request,
                messages.ERROR,
                "No validators available, please try again later",
            )
            return super().get(self.request)
        except Miner.DoesNotExist:
            messages.add_message(
                self.request,
                messages.ERROR,
                "No miners available, please try again later",
            )
            return super().get(self.request)

        return HttpResponseRedirect(job.get_absolute_url())


@method_decorator(fingerprint, name="get")
class JobDetailView(DetailView):
    model = Job
    template_name = "core/job_detail.html"
    context_object_name = "job"

    def get_queryset(self) -> QuerySet:
        return super().get_queryset().with_statuses()

    def get_object(self, queryset: QuerySet | None = None) -> Job:
        """
        Regenerate download URL if it's expired before displaying the object to user
        """
        job: Job = super().get_object(queryset)
        if job.is_download_url_expired():
            job.save()
        return job


@login_required
def api_token_view(request):
    user = request.user

    always_show_token = False
    is_new_token = False
    if request.method == "POST":
        token, created = Token.objects.get_or_create(user=user)
        if not created:
            token.delete()
            token = Token.objects.create(user=user)
        is_new_token = True
    else:
        token = Token.objects.filter(user=user).first()

    token_key = None
    if token is not None:
        token_key = token.key if is_new_token or always_show_token else "*************"
    context = {
        "form": GenerateAPITokenForm(),
        "token": token,
        "safe_token_key": token_key,
        "is_new_token": is_new_token,
        "always_show_token": always_show_token,
    }
    return render(request, "core/api_token.html", context)
