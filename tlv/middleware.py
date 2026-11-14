from django.http import JsonResponse


class HealthMiddleware:
    """
    Responde a /salud/ antes que nada. El alojamiento llama a esta dirección
    desde su red interna (sin HTTPS y con otro nombre de servidor) para saber
    si la web está viva; no debe pasar por las comprobaciones de dominio.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path == "/salud/":
            from django.db import connection

            try:
                with connection.cursor() as cur:
                    cur.execute("SELECT 1")
            except Exception:
                return JsonResponse({"ok": False}, status=503)
            return JsonResponse({"ok": True})
        return self.get_response(request)
