from django.conf import settings
from django.contrib.admin.sites import site as admin_site
from django.urls import include, path
from fingerprint.views import FingerprintView

from .core.business_metrics import metrics_manager
from .core.consumers import ValidatorConsumer
from .core.metrics import metrics_view
from .core.views import SignupView

urlpatterns = [
    path("admin/", admin_site.urls),
    path("redirect/", FingerprintView.as_view(), name="fingerprint"),
    path("accounts/signup/", SignupView.as_view(), name="account_signup"),
    path("accounts/", include("allauth.urls")),
    path("", include("project.core.urls")),
    path("metrics", metrics_view, name="prometheus-django-metrics"),
    path("business-metrics", metrics_manager.view, name="prometheus-business-metrics"),
]

urlpatterns += [path("", include(f"{app}.urls")) for app in settings.ADDITIONAL_APPS]


if settings.DEBUG_TOOLBAR:
    urlpatterns += [
        path("__debug__/", include("debug_toolbar.urls")),
    ]

ws_urlpatterns = [
    path("ws/v0/", ValidatorConsumer.as_asgi()),
]
