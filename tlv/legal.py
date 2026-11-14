"""
Texto del contrato marco entre el locutor y la plataforma.

BORRADOR pendiente de revisión por un abogado. Sirve para que el flujo de firma
sea real (texto, huella, fecha, IP), pero NO debe usarse con locutores reales
sin validación jurídica.
"""
from .models import PlatformSettings

CONTRACT_ES = """1. Partes

De una parte, {company} («la Plataforma»). De otra, {legal_name}, con NIF {tax_id}, que actúa profesionalmente como {stage_name} («el Locutor»).

2. Objeto

El Locutor autoriza a la Plataforma a crear y alojar un modelo digital de su voz («la Voz Digital») a partir de las grabaciones que él mismo sube, y a ofrecer su uso a clientes únicamente en las condiciones de este contrato.

3. Titularidad

La voz es y sigue siendo del Locutor. Este contrato no cede su voz ni su imagen: concede una autorización limitada, no exclusiva y revocable. La Plataforma no usará las grabaciones ni la Voz Digital para entrenar modelos generales ni para crear otras voces.

4. Usos autorizados

Solo se permiten los usos de las categorías que el Locutor marca como autorizadas en el anexo de categorías. El Locutor puede cambiar esas autorizaciones en cualquier momento desde su panel, con efecto inmediato sobre los usos nuevos.

5. Aprobación de cada proyecto

Ningún cliente obtiene una locución con derechos de uso sin que el Locutor haya aprobado antes ese proyecto concreto: guion, finalidad, sector, territorio, canales y duración. El Locutor puede rechazar cualquier solicitud sin dar explicaciones.

6. Demostraciones

Los clientes registrados pueden generar demostraciones cortas para evaluar la Voz Digital. Las demostraciones no conceden ningún derecho de uso y llevan marca de agua.

7. Remuneración

De cada licencia cobrada, la Plataforma retiene una comisión del {fee} % sobre el importe sin IVA y liquida el resto al Locutor (o a su representante, en la parte que corresponda). La Plataforma pone a disposición del Locutor un panel con los ingresos, usos y liquidaciones.

8. Pausa y revocación

El Locutor puede pausar la Voz Digital o revocar esta autorización en cualquier momento desde su panel. Desde ese instante no se admiten usos nuevos y, en caso de revocación, la Plataforma elimina el modelo de voz del proveedor tecnológico. Las licencias ya pagadas siguen siendo válidas para las locuciones ya entregadas, en sus propios términos.

9. Trazabilidad y protección

Cada locución generada lleva una marca de agua invisible y queda apuntada en un registro firmado que la vincula con el Locutor, el cliente, el proyecto, este contrato y el modelo de IA utilizado. La Plataforma atenderá las denuncias de uso no autorizado y podrá bloquear usos mientras las investiga.

10. Proveedor tecnológico

La Voz Digital se crea con un proveedor externo de síntesis de voz, que trata las grabaciones como encargado del tratamiento. El Locutor acepta ese tratamiento con esta única finalidad.

11. Datos personales

La voz es un dato personal. El Locutor presta su consentimiento expreso para que la Plataforma trate sus grabaciones y su Voz Digital con las finalidades de este contrato. Puede retirar el consentimiento, acceder a sus datos y pedir su supresión desde su panel.

12. Declaraciones del Locutor

El Locutor declara que la voz de las grabaciones es la suya, que es mayor de edad y que no tiene compromisos de exclusividad que le impidan firmar este contrato.

13. Duración

Este contrato dura mientras la Voz Digital exista en la Plataforma y termina con la revocación.

14. Ley aplicable

Este contrato se rige por la ley española. Versión {version}.
"""

CONTRACT_EN = """1. Parties

On the one hand, {company} ("the Platform"). On the other, {legal_name}, tax ID {tax_id}, working professionally as {stage_name} ("the Voice Actor").

2. Purpose

The Voice Actor authorises the Platform to create and host a digital model of their voice ("the Digital Voice") from recordings they upload themselves, and to offer its use to clients only under the terms of this agreement.

3. Ownership

The voice is and remains the Voice Actor's. This agreement does not transfer their voice or likeness: it grants a limited, non-exclusive, revocable authorisation. The Platform will not use the recordings or the Digital Voice to train general models or to create other voices.

4. Authorised uses

Only uses in the categories the Voice Actor marks as authorised in the category annex are allowed. The Voice Actor can change those authorisations at any time from their dashboard, with immediate effect on new uses.

5. Approval of each project

No client obtains a recording with usage rights unless the Voice Actor has first approved that specific project: script, purpose, sector, territory, channels and duration. The Voice Actor may reject any request without giving reasons.

6. Demos

Registered clients may generate short demos to evaluate the Digital Voice. Demos grant no usage rights and are watermarked.

7. Remuneration

From each licence paid, the Platform keeps a {fee}% commission on the amount excluding VAT and pays the rest to the Voice Actor (or to their representative, for the corresponding share). The Platform provides a dashboard with earnings, uses and payouts.

8. Pause and revocation

The Voice Actor may pause the Digital Voice or revoke this authorisation at any time from their dashboard. From that moment no new uses are accepted and, on revocation, the Platform deletes the voice model from the technology provider. Licences already paid remain valid for recordings already delivered, on their own terms.

9. Traceability and protection

Every generated recording carries an invisible watermark and is entered in a signed log linking it to the Voice Actor, the client, the project, this agreement and the AI model used. The Platform will handle reports of unauthorised use and may block uses while investigating.

10. Technology provider

The Digital Voice is created with an external speech-synthesis provider, which processes the recordings as a data processor. The Voice Actor accepts that processing for this sole purpose.

11. Personal data

Voice is personal data. The Voice Actor gives explicit consent for the Platform to process their recordings and Digital Voice for the purposes of this agreement. They can withdraw consent, access their data and request erasure from their dashboard.

12. Voice Actor's statements

The Voice Actor states that the voice in the recordings is their own, that they are of legal age and that they have no exclusivity commitments preventing them from signing this agreement.

13. Term

This agreement lasts while the Digital Voice exists on the Platform and ends on revocation.

14. Governing law

This agreement is governed by Spanish law. The Spanish version prevails. Version {version}.
"""


def contract_text(talent, language="es"):
    from django.conf import settings

    from .services.money import fee_percent_for

    cfg = PlatformSettings.load()
    template = CONTRACT_EN if language == "en" else CONTRACT_ES
    return template.format(
        company=cfg.company_name, legal_name=talent.legal_name, tax_id=talent.tax_id or "—",
        stage_name=talent.stage_name, fee=f"{fee_percent_for(talent):g}", version=settings.TLV_CONTRACT_VERSION,
    )
