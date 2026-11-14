from django.core.management.base import BaseCommand

from tlv.services import ledger, licenseflow


class Command(BaseCommand):
    help = "Tarea diaria: caduca licencias vencidas y comprueba el registro."

    def handle(self, *args, **options):
        expired = licenseflow.expire_licenses()
        status = ledger.verify()
        self.stdout.write(f"Licencias caducadas hoy: {expired}. Registro íntegro: {status['ok']} ({status['entries']} entradas).")
