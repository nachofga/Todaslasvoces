from django.conf.urls.i18n import i18n_patterns
from django.contrib import admin
from django.urls import include, path

from tlv.views.public import health

urlpatterns = [
    path("salud/", health),
    path("i18n/", include("django.conf.urls.i18n")),
    path("admin/", admin.site.urls),
]
# Español sin prefijo (/voces/), inglés con prefijo (/en/voces/).
urlpatterns += i18n_patterns(path("", include("tlv.urls")), prefix_default_language=False)
