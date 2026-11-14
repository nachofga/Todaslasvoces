"""Administrador de datos (/admin/): acceso directo a las tablas, solo para superusuarios."""
from django.apps import apps
from django.contrib import admin

from .models import LedgerEntry, PlatformSettings

admin.site.site_header = "Todas las Voces · datos"
admin.site.site_title = "Todas las Voces"


@admin.register(LedgerEntry)
class LedgerAdmin(admin.ModelAdmin):
    """El registro de trazabilidad se puede consultar pero no tocar."""

    list_display = ("seq", "created_at", "event", "actor")
    list_filter = ("event",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PlatformSettings)
class SettingsAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return not PlatformSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


for model in apps.get_app_config("tlv").get_models():
    if not admin.site.is_registered(model):
        fields = [f.name for f in model._meta.fields if f.get_internal_type() not in ("TextField", "JSONField", "FileField")][:7]
        admin.site.register(model, type(f"{model.__name__}Admin", (admin.ModelAdmin,), {"list_display": fields}))
