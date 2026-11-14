from django.core.management.base import BaseCommand, CommandError

from tlv.services import ledger


class Command(BaseCommand):
    help = "Comprueba que nadie ha modificado el registro de trazabilidad."

    def handle(self, *args, **options):
        result = ledger.verify()
        if not result["ok"]:
            raise CommandError(f"REGISTRO ROTO en la entrada #{result['broken_at']}: {result['reason']}")
        self.stdout.write(self.style.SUCCESS(f"Registro íntegro: {result['entries']} entradas."))
