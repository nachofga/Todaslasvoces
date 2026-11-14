"""
Deja la plataforma lista para arrancar. Se puede ejecutar todas las veces que
haga falta: no duplica ni pisa lo que ya hayas cambiado desde el panel.

    python manage.py bootstrap

Crea: listas (idiomas, acentos, estilos, tipos de proyecto), las 17 categorías
de uso provisionales, los ajustes de la plataforma y, si están definidas las
variables TLV_ADMIN_EMAIL y TLV_ADMIN_PASSWORD, el usuario de gestión.
"""
import os

from django.core.management import call_command
from django.core.management.base import BaseCommand

from tlv import catalogs
from tlv.models import Accent, Language, PlatformSettings, ProjectType, Style, UseCategory, User


class Command(BaseCommand):
    help = "Carga las listas iniciales y crea el usuario de gestión."

    def handle(self, *args, **options):
        call_command("createcachetable", verbosity=0)
        for model, rows in ((Language, catalogs.LANGUAGES), (Accent, catalogs.ACCENTS),
                            (Style, catalogs.STYLES), (ProjectType, catalogs.PROJECT_TYPES)):
            for order, (code, es, en) in enumerate(rows):
                model.objects.get_or_create(code=code, defaults={"name_es": es, "name_en": en, "order": order})
        if not UseCategory.objects.exists():
            for order, (code, es, en, des, den, sensitive, factor) in enumerate(catalogs.USE_CATEGORIES):
                UseCategory.objects.create(
                    code=code, name_es=es, name_en=en, description_es=des, description_en=den,
                    sensitive=sensitive, price_factor=factor, order=order,
                )
        PlatformSettings.load()
        email, password = os.environ.get("TLV_ADMIN_EMAIL", "").strip().lower(), os.environ.get("TLV_ADMIN_PASSWORD", "")
        if email and password and not User.objects.filter(email=email).exists():
            User.objects.create_superuser(email, password)
            self.stdout.write(f"Usuario de gestión creado: {email}")
        self.stdout.write(self.style.SUCCESS(
            f"Listo: {UseCategory.objects.count()} categorías de uso, {Language.objects.count()} idiomas."
        ))
