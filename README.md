# Todas las Voces

Marketplace de voces profesionales generadas con IA. Los locutores crean su voz
digital, deciden para qué se puede usar y aprueban cada proyecto; los clientes
buscan una voz, solicitan una licencia y descargan la locución con su certificado.

Versión: PMV sin pasarela de pago (los cobros son por transferencia y los
confirma gestión a mano).

## Probarlo en tu ordenador (5 minutos)

Necesitas Python 3.12 o superior.

```bash
python -m venv .venv
source .venv/bin/activate            # en Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                 # en Windows: copy .env.example .env
python manage.py migrate             # crea la base de datos (SQLite, en var/)
python manage.py bootstrap           # listas, 17 categorías y usuario de gestión
python manage.py demo                # opcional: 3 voces, 1 cliente y 1 licencia de ejemplo
python manage.py runserver
```

Abre http://localhost:8000. Con `demo`, todas las cuentas usan la contraseña
`demo-TodasLasVoces-2026`:

| Papel   | Correo                               |
|---------|--------------------------------------|
| Gestión | el de `TLV_ADMIN_EMAIL` en tu `.env`  |
| Locutor | lucia@demo.todaslasvoces.test        |
| Cliente | cliente@demo.todaslasvoces.test      |

Sin clave de ElevenLabs la plataforma usa un **motor de voz simulado**: una voz
sintética sencilla que no clona nada pero deja recorrer todo el flujo sin coste.
Los correos salen por la consola.

## Ponerlo en internet

Guía paso a paso en [docs/DESPLIEGUE.md](docs/DESPLIEGUE.md): cuentas que hay que
crear, despliegue en Render (Fráncfort) con PostgreSQL y conexión con ElevenLabs.

## Dónde está cada cosa

```
config/            configuración (settings.py lee todo de variables de entorno)
tlv/models/        la base de datos: usuarios, voces, licencias, cobros, registro
tlv/services/      la lógica, sin nada de web:
    policy.py        control de usos: TODA decisión de «¿se puede?» pasa por aquí
    engines.py       motores de voz (ElevenLabs y simulado); aquí se añade otro proveedor
    studio.py        único punto que genera audio: sintetiza, marca y apunta en el registro
    watermark.py     marca de agua invisible (explicada paso a paso en el propio fichero)
    audio.py         lectura de audio y control de calidad de las muestras
    voiceflow.py     ciclo de una voz: muestras, titularidad, contrato, clonación, revocación
    licenseflow.py   ciclo de una licencia: solicitud, aprobación, pago, tomas, caducidad
    money.py         presupuesto, IVA, factura y reparto (fórmulas al principio del fichero)
    ledger.py        registro de trazabilidad encadenado y firmado
    disputes.py      incidencias e identificación de audios
    documents.py     PDF: certificado, factura, contrato, justificante
    privacy.py       copia de datos y supresión de cuentas (RGPD)
tlv/views/         las pantallas, por tipo de usuario (público, locutor, cliente, gestión)
tlv/templates/     HTML        tlv/static/   CSS, JS y tipografía
tlv/locale/en/     traducción al inglés (django.po)
tlv/tests/         42 pruebas automáticas:  python manage.py test tlv
```

## Órdenes útiles

```bash
python manage.py test tlv            # pasar todas las pruebas
python manage.py verify_ledger       # comprobar que nadie ha tocado el registro
python manage.py daily               # tarea diaria: caducar licencias vencidas
python manage.py createsuperuser     # otro usuario de gestión
python scripts/i18n.py               # tras cambiar textos: actualiza y compila la traducción
python scripts/i18n.py missing       # textos que faltan por traducir al inglés
```

## Consultar la base de datos

- **Sin SQL:** entra con un usuario de gestión en `/admin/`. Ahí están todas las tablas.
- **Con SQL:** conéctate con la `DATABASE_URL` (en Render: *Dashboard → base de datos → Connect → External Database URL*)
  desde DBeaver, TablePlus o `psql`. Las tablas empiezan por `tlv_`. Ejemplos:

```sql
-- Ingresos por voz
SELECT v.name, COUNT(*) AS licencias, SUM(s.net) AS neto, SUM(s.talent_amount) AS para_el_locutor
FROM tlv_split s
JOIN tlv_payment p        ON p.id = s.payment_id
JOIN tlv_licenserequest r ON r.id = p.request_id
JOIN tlv_voice v          ON v.id = r.voice_id
GROUP BY v.name ORDER BY neto DESC;

-- Qué autoriza cada voz
SELECT v.name, c.name_es, vc.allowed, vc.price
FROM tlv_voiceconsent vc
JOIN tlv_voice v       ON v.id = vc.voice_id
JOIN tlv_usecategory c ON c.id = vc.category_id
ORDER BY v.name, c."order";

-- Trazabilidad de un audio a partir de su identificador de marca de agua
SELECT g.id, g.kind, g.created_at, v.name AS voz, r.code AS solicitud, cp.company_name AS cliente, g.contract_code, g.engine_model
FROM tlv_generation g
JOIN tlv_voice v ON v.id = g.voice_id
LEFT JOIN tlv_licenserequest r ON r.id = g.request_id
LEFT JOIN tlv_clientprofile cp ON cp.id = r.client_id
WHERE g.watermark_id = 1234567;
```

No modifiques a mano `tlv_ledgerentry`: es el registro firmado y cualquier cambio lo rompe (a propósito).

## Lo que NO hace todavía

- **Cobrar con tarjeta.** El modelo `Payment` ya tiene los campos `method` y `provider_ref`
  para conectar una pasarela (Stripe Connect, Mangopay…) sin cambiar el resto.
- **Textos legales definitivos.** Contrato, condiciones y privacidad son borradores marcados como tales.
- **Facturas con validez fiscal.** Salen como proforma hasta que el asesor fiscal lo valide (ajuste en gestión).
- **Verificación biométrica de la voz.** La titularidad se comprueba con una frase aleatoria grabada en
  directo, transcripción automática y revisión humana; el «parecido de voz» es solo orientativo.
- **Tareas en segundo plano.** Clonar y generar se hacen en la misma petición (segundos). Para guiones muy
  largos o mucho tráfico habrá que añadir una cola de trabajos.
- La marca de agua está validada con voz sintética; hay que **validarla con voces reales** de ElevenLabs
  antes de usarla como prueba en una disputa (ver límites en `tlv/services/watermark.py`).
