# Poner Todas las Voces en internet

Resultado: la web funcionando en una dirección `https://….onrender.com`, con base de
datos PostgreSQL y servidor en Fráncfort (UE), y con clonación real de voz por ElevenLabs.

Tiempo: una hora la primera vez. Coste mensual aproximado: 14 $ de Render + el plan de ElevenLabs.

## 1. Cuentas que necesitas

| Cuenta | Para qué | Coste |
|---|---|---|
| **GitHub** (ya tienes: `nachofga`) | Guardar el código; Render lo lee de ahí | Gratis |
| **Render** — render.com | Servidor web + PostgreSQL + disco | Web Starter + base de datos Basic + disco |
| **ElevenLabs** — elevenlabs.io | Clonar voces y generar audio | Plan de pago (la clonación por API no está en el gratuito) |
| **Correo SMTP** (Brevo, Resend, Mailgun…) | Enviar los avisos y la confirmación de cuenta | Suele bastar el plan gratuito |

## 2. Subir el código a GitHub

En la carpeta del proyecto:

```bash
git init
git add .
git commit -m "Todas las Voces: PMV"
git branch -M main
git remote add origin https://github.com/nachofga/Todaslasvoces.git
git push -u origin main
```

El repositorio debería ser **privado** (Settings → General → Danger Zone → Change visibility).

## 3. Crear el servicio en Render

1. Entra en https://dashboard.render.com y pulsa **New → Blueprint**.
2. Conecta tu cuenta de GitHub y elige el repositorio `Todaslasvoces`.
3. Render lee `render.yaml` y te enseña lo que va a crear: una web (`todaslasvoces`),
   una base de datos (`todaslasvoces-db`) y un disco de 5 GB. Todo en **Frankfurt**.
4. Te pedirá los valores marcados para rellenar a mano:
   - `TLV_ADMIN_EMAIL` y `TLV_ADMIN_PASSWORD`: tu usuario de gestión (contraseña de 10 caracteres o más).
   - `ELEVENLABS_API_KEY`: déjala vacía por ahora si aún no la tienes.
   - `EMAIL_HOST`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `DEFAULT_FROM_EMAIL`: los datos SMTP de tu proveedor.
   - `TLV_SITE_URL` y `ALLOWED_HOSTS`: vacíos mientras uses la dirección `onrender.com`.
5. Pulsa **Apply**. El primer despliegue tarda unos 5 minutos.
6. Abre la dirección que te da Render y entra con tu usuario de gestión.

Las claves `SECRET_KEY`, `TLV_WATERMARK_KEY` y `TLV_SIGNING_SEED` las genera Render.
**Cópialas a un gestor de contraseñas y no las cambies nunca**: con la segunda se leen las
marcas de agua y con la tercera se firma el registro.

## 4. Conectar ElevenLabs

1. Crea la cuenta y contrata un plan de pago. Cada locutor ocupa una «voz personalizada»
   del plan, así que el número de voces del plan es tu número máximo de locutores.
2. En ElevenLabs: **Developers → API Keys → Create API Key**. Permisos necesarios:
   *Text to Speech*, *Voices* (lectura y escritura), *Speech to Text* y *User* (lectura).
3. En Render: servicio `todaslasvoces` → **Environment** → `ELEVENLABS_API_KEY` → pega la clave → **Save**.
   Render reinicia la web sola.
4. Compruébalo en la web: **Gestión → Herramientas → Comprobar la conexión y el consumo**.
   Desaparece el aviso amarillo de «Modo de pruebas».

Opcional: `ELEVENLABS_MODEL_ID` (por defecto `eleven_multilingual_v2`) para usar otro modelo.

Antes de subir voces de locutores reales: firma con ElevenLabs su acuerdo de tratamiento de
datos y revisa con el abogado el contrato del locutor (ver «Pendiente legal» abajo).

## 5. Correo

Con Brevo, por ejemplo: crea la cuenta → **SMTP & API → SMTP** → copia servidor, usuario y
clave SMTP a las variables `EMAIL_*` de Render. Para que los correos no vayan a spam,
verifica tu dominio en el proveedor y usa un remitente de ese dominio en `DEFAULT_FROM_EMAIL`.

## 6. Primera puesta a punto (dentro de la web, como gestión)

1. **Ajustes y comisiones:** razón social, CIF, IBAN donde cobráis, comisión de la plataforma, IVA.
2. **Categorías de uso:** sustituye las 17 provisionales por la lista oficial cuando la tengáis.
3. Haz un recorrido completo con dos cuentas de prueba (un locutor y un cliente) antes de invitar a nadie.

## 7. Dominio propio (cuando queráis)

Render → servicio → **Settings → Custom Domains** → añade `www.tudominio.com` y crea en tu
proveedor de dominio el registro CNAME que te indica. Después pon en Environment
`TLV_SITE_URL=https://www.tudominio.com` y `ALLOWED_HOSTS=www.tudominio.com,tudominio.com`.

## 8. Tarea diaria y copias de seguridad

- **Caducidad de licencias:** Render → New → **Cron Job**, mismo repositorio, orden
  `python manage.py daily`, horario `0 4 * * *`, con las mismas variables de entorno.
  (Aunque no lo montes, una licencia caducada se bloquea igual al intentar usarla.)
- **Base de datos:** los planes de pago de Render hacen copias diarias; compruébalo en la pestaña *Backups*.
- **Audios y grabaciones:** están en el disco `/var/data`. Activa los *snapshots* del disco en Render.

## 9. Actualizar la web

Cada `git push` a `main` despliega solo. Las migraciones de base de datos se aplican al arrancar.

## Alternativas a Render

El proyecto no depende de Render: solo necesita Python, una `DATABASE_URL` de PostgreSQL y una
carpeta persistente (`TLV_MEDIA_ROOT`). Incluye `Dockerfile` para Railway, Fly.io o un servidor
propio (Hetzner, OVH…). Si preferís almacenamiento de objetos (Cloudflare R2, Supabase Storage,
S3) en vez de disco: `pip install "django-storages[s3]"` y las variables `TLV_S3_*` de `config/settings.py`.

## Pendiente legal antes de un piloto con locutores reales

- Revisión jurídica del contrato del locutor, condiciones y privacidad (hoy son borradores).
- La voz es un dato personal sensible: acuerdo de encargo de tratamiento con ElevenLabs y con Render,
  registro de actividades y evaluación de impacto.
- Con el plan estándar de ElevenLabs los audios se procesan fuera de la UE; la residencia de datos en
  la UE y la «retención cero» son de su plan Enterprise.
- Facturación: validar con el asesor fiscal antes de quitar el modo proforma.


## Activar el correo (por API, sin SMTP)

Render permite SMTP en los planes de pago (puertos 465 y 587; el 25 está siempre cerrado),
pero lo más sencillo y robusto es enviar por API web:

1. Crea una cuenta gratuita en https://www.brevo.com (300 correos al día).
2. En Brevo → *Remitentes*, añade y verifica la dirección desde la que se enviará.
   Sin dominio propio vale una dirección personal verificada, aunque con dominio
   propio (y sus registros DKIM) los correos llegan mejor.
3. En Brevo → *SMTP y API* → *Claves API*, crea una clave.
4. En Render → servicio `todaslasvoces` → *Environment*, añade:
   - `BREVO_API_KEY` = la clave
   - `DEFAULT_FROM_EMAIL` = `Todas las Voces <la-direccion-verificada>`
5. Guarda (Render vuelve a desplegar) y en Gestión → Herramientas → Correo pulsa
   «Enviar correo de prueba».

Desde ese momento las cuentas nuevas tienen que confirmar su correo y cada aviso
de la web se envía también por correo. `RESEND_API_KEY` funciona igual con Resend.

## Tono de la interpretación

El cliente elige el tono al probar una voz, al pedir la licencia y en cada toma.
«Natural» usa `ELEVENLABS_MODEL_ID` (eleven_multilingual_v2) con los cinco ajustes
(velocidad, estabilidad, similitud, exageración de estilo y realce de voz). El resto
de tonos usa `ELEVENLABS_EXPRESSIVE_MODEL` (eleven_v3), que entiende la indicación
de tono; se puede cambiar por `eleven_v4` desde las variables de entorno.
