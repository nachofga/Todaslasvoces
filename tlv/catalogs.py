"""
Listas de partida de la plataforma. Se cargan con `python manage.py bootstrap`
y después se editan desde el panel de gestión.

IMPORTANTE: las 17 categorías de uso son PROVISIONALES hasta recibir la lista
oficial del documento «Especificaciones para presupuesto».
"""
import secrets

# (código, nombre ES, nombre EN, descripción ES, descripción EN, sensible, multiplicador de precio)
USE_CATEGORIES = [
    ("publicidad-tv-radio", "Publicidad en TV y radio", "TV and radio advertising",
     "Anuncios y cuñas en medios convencionales.", "Commercials on broadcast media.", False, "2.00"),
    ("publicidad-digital", "Publicidad digital", "Digital advertising",
     "Anuncios en web, redes sociales, vídeo y audio online.", "Ads on web, social media, online video and audio.", False, "1.50"),
    ("doblaje", "Doblaje de cine y series", "Film and series dubbing",
     "Doblaje o voz original de ficción audiovisual.", "Dubbing or original voice for scripted content.", False, "1.80"),
    ("animacion", "Animación", "Animation",
     "Personajes y narración de animación.", "Animated characters and narration.", False, "1.60"),
    ("videojuegos", "Videojuegos", "Video games",
     "Personajes, narradores e interfaz de videojuegos.", "Game characters, narrators and interface voices.", False, "1.60"),
    ("audiolibros", "Audiolibros", "Audiobooks",
     "Narración de libros completos o fragmentos.", "Narration of whole books or excerpts.", False, "1.20"),
    ("podcast", "Pódcast", "Podcasts",
     "Cabeceras, cuñas y narración en pódcast.", "Podcast intros, bumpers and narration.", False, "1.00"),
    ("documental", "Documentales y reportajes", "Documentaries",
     "Narración de no ficción.", "Non-fiction narration.", False, "1.40"),
    ("formacion", "E-learning y formación", "E-learning and training",
     "Cursos, tutoriales y material educativo.", "Courses, tutorials and educational material.", False, "0.90"),
    ("corporativo", "Vídeo corporativo", "Corporate video",
     "Vídeos internos o institucionales de empresa.", "Internal or institutional company videos.", False, "1.00"),
    ("ivr", "Centralitas e IVR", "Phone systems and IVR",
     "Mensajes de atención telefónica y menús de voz.", "Phone greetings and voice menus.", False, "0.80"),
    ("asistentes", "Asistentes virtuales y apps", "Virtual assistants and apps",
     "Voz de aplicaciones, dispositivos y asistentes.", "Voice for apps, devices and assistants.", True, "2.50"),
    ("redes-creadores", "Contenido para redes y creadores", "Social and creator content",
     "Vídeos cortos, directos y contenido de creadores.", "Short videos, streams and creator content.", False, "0.80"),
    ("accesibilidad", "Accesibilidad", "Accessibility",
     "Audiodescripción y lectura para personas con discapacidad.", "Audio description and reading for people with disabilities.", False, "0.70"),
    ("megafonia", "Megafonía y espacios públicos", "Public address and venues",
     "Avisos en transporte, tiendas, museos y eventos.", "Announcements in transport, shops, museums and events.", False, "1.00"),
    ("politica", "Comunicación política y electoral", "Political and electoral communication",
     "Campañas, partidos e instituciones políticas.", "Campaigns, parties and political institutions.", True, "2.50"),
    ("salud-finanzas", "Salud, apuestas y productos financieros", "Health, gambling and financial products",
     "Sectores regulados con mensajes delicados.", "Regulated sectors with sensitive messaging.", True, "2.00"),
]

LANGUAGES = [
    ("es", "Español", "Spanish"), ("en", "Inglés", "English"), ("ca", "Catalán", "Catalan"),
    ("gl", "Gallego", "Galician"), ("eu", "Euskera", "Basque"), ("fr", "Francés", "French"),
    ("pt", "Portugués", "Portuguese"), ("it", "Italiano", "Italian"), ("de", "Alemán", "German"),
]
ACCENTS = [
    ("castellano", "Castellano", "Castilian"), ("andaluz", "Andaluz", "Andalusian"),
    ("canario", "Canario", "Canarian"), ("neutro-latam", "Latino neutro", "Neutral Latin American"),
    ("mexicano", "Mexicano", "Mexican"), ("argentino", "Rioplatense", "Rioplatense"),
    ("colombiano", "Colombiano", "Colombian"), ("britanico", "Británico", "British"),
    ("americano", "Estadounidense", "American"),
]
STYLES = [
    ("calido", "Cálido", "Warm"), ("cercano", "Cercano", "Friendly"), ("corporativo", "Corporativo", "Corporate"),
    ("energico", "Enérgico", "Energetic"), ("narrativo", "Narrativo", "Narrative"), ("sobrio", "Sobrio", "Understated"),
    ("juvenil", "Juvenil", "Youthful"), ("epico", "Épico", "Epic"), ("sensual", "Sugerente", "Sultry"),
    ("comico", "Cómico", "Comedic"), ("informativo", "Informativo", "Newsy"), ("institucional", "Institucional", "Institutional"),
]
PROJECT_TYPES = [
    ("publicidad", "Publicidad", "Advertising"), ("doblaje", "Doblaje", "Dubbing"),
    ("documental", "Documental", "Documentary"), ("audiolibro", "Audiolibro", "Audiobook"),
    ("elearning", "E-learning", "E-learning"), ("corporativo", "Corporativo", "Corporate"),
    ("videojuegos", "Videojuegos", "Video games"), ("animacion", "Animación", "Animation"),
    ("ivr", "Telefonía", "Telephony"), ("podcast", "Pódcast", "Podcast"),
]

# Textos de las demostraciones que se generan al aprobar una voz.
DEMO_TEXTS = {
    "es": [
        ("Presentación", "Hola. Esta es mi voz digital, creada con mi permiso. Todo lo que escuchas aquí se ha generado con inteligencia artificial y con mi autorización."),
        ("Publicidad", "Hay cosas que solo pasan una vez. Este verano, que no te lo cuenten. Reserva ya tu plaza y prepárate para vivirlo."),
        ("Narración", "Al amanecer, la niebla cubre el valle. Durante siglos, estas montañas han guardado historias que hoy, por fin, podemos contar."),
    ],
    "en": [
        ("Introduction", "Hello. This is my digital voice, created with my permission. Everything you hear was generated with artificial intelligence and with my authorisation."),
        ("Commercial", "Some things only happen once. This summer, don't just hear about it. Book your place now and get ready to live it."),
        ("Narration", "At dawn, mist covers the valley. For centuries these mountains have kept stories that today, at last, we can tell."),
    ],
}

# Palabras para construir la frase aleatoria de la prueba de titularidad.
_WORDS = {
    "es": {
        "start": ["Hoy", "Esta mañana", "Ahora mismo", "En este momento", "Esta tarde"],
        "subject": ["el tren azul", "la ventana verde", "el faro antiguo", "la montaña blanca", "el río tranquilo",
                    "la plaza redonda", "el mercado viejo", "la lámpara amarilla", "el puente largo", "la casa del lago"],
        "verb": ["espera junto a", "aparece detrás de", "descansa cerca de", "brilla sobre", "se esconde bajo"],
        "place": ["la estación del norte", "el jardín de piedra", "la torre del reloj", "el camino de arena",
                  "la biblioteca pequeña", "el puerto pesquero", "la calle de los naranjos"],
        "end": "Autorizo a Todas las Voces a crear mi voz digital. Código {code}.",
    },
    "en": {
        "start": ["Today", "This morning", "Right now", "At this moment", "This afternoon"],
        "subject": ["the blue train", "the green window", "the old lighthouse", "the white mountain", "the quiet river",
                    "the round square", "the old market", "the yellow lamp", "the long bridge", "the house by the lake"],
        "verb": ["waits beside", "appears behind", "rests near", "shines above", "hides under"],
        "place": ["the northern station", "the stone garden", "the clock tower", "the sandy path",
                  "the small library", "the fishing harbour", "the street of orange trees"],
        "end": "I authorise Todas las Voces to create my digital voice. Code {code}.",
    },
}
_DIGITS = {
    "es": ["cero", "uno", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve"],
    "en": ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"],
}


def challenge_phrase(language="es"):
    """Frase imposible de tener grabada de antemano: palabras al azar + un código de 4 cifras."""
    lang = language if language in _WORDS else "es"
    w = _WORDS[lang]
    pick = secrets.choice
    code = " ".join(_DIGITS[lang][secrets.randbelow(10)] for _ in range(4))
    return (
        f"{pick(w['start'])}, {pick(w['subject'])} {pick(w['verb'])} {pick(w['place'])}. "
        + w["end"].format(code=code)
    )


RECORDING_GUIDE = {
    "es": [
        "Graba en una sala tratada o muy silenciosa, sin música, sin efectos y sin otras voces.",
        "Usa tu micrófono de trabajo a la distancia habitual, con antipop.",
        "Exporta en WAV o FLAC, mono, a 44,100 Hz o 48,000 Hz y 16 o 24 bits. Sin compresor, sin ecualizar y sin reducción de ruido.",
        "Sube entre 2 y 5 minutos en total, repartidos en varias tomas de 30 segundos a 2 minutos.",
        "Lee con tu voz natural y con variedad: una parte narrativa, otra comercial y otra conversacional.",
        "Deja los picos alrededor de -6 dB. Si la grabación satura, repítela.",
    ],
    "en": [
        "Record in a treated or very quiet room, with no music, effects or other voices.",
        "Use your working microphone at your usual distance, with a pop filter.",
        "Export WAV or FLAC, mono, at 44,100 Hz or 48,000 Hz and 16 or 24 bits. No compressor, EQ or noise reduction.",
        "Upload 2 to 5 minutes in total, split into several takes of 30 seconds to 2 minutes.",
        "Read in your natural voice with variety: some narrative, some commercial and some conversational.",
        "Keep peaks around -6 dB. If the recording clips, record it again.",
    ],
}
