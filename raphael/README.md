# Raphael

Chatbot web que ayuda con la vida cotidiana, usando la API de Claude.

## Instalación

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.lock
```

## Configuración

1. Copia `.env.example` a `.env` si todavía no existe:
   ```bash
   cp .env.example .env
   ```
2. Consigue una API key en https://console.anthropic.com y pégala en `.env`:
   ```
   ANTHROPIC_API_KEY=sk-ant-...
   ```
3. Deja estos valores por defecto salvo que quieras cambiarlos:
   ```
   ANTHROPIC_MODEL=claude-sonnet-4-6
   ANTHROPIC_MAX_TOKENS=2048
   ANTHROPIC_WEB_SEARCH_TOOL=web_search_20250305
   FLASK_PORT=5000
   ```

## Ejecutar

```bash
source .venv/bin/activate
python3 app.py
```

Abre http://localhost:5000 en el navegador.

Si no usas entorno virtual porque ya instalaste las dependencias globalmente, basta con:

```bash
python3 app.py
```

## Voz

Pulsa `Voz` en el compositor y permite el uso del micrófono cuando el navegador
lo pida. Raphael transcribirá lo que digas, enviará el mensaje y leerá la
respuesta en voz alta si el navegador soporta síntesis de voz.

La voz usa las APIs del navegador (`SpeechRecognition`/`webkitSpeechRecognition`
y `speechSynthesis`), así que funciona mejor en Chrome, Edge o Safari modernos.
Si el navegador bloquea el micrófono, verás un aviso dentro del chat.

## Estructura

```
raphael/
├── app.py              # servidor Flask + llamadas a la API de Claude
├── templates/
│   └── index.html      # interfaz de chat
├── static/
│   ├── style.css        # estilos
│   └── script.js        # lógica del chat en el navegador
├── requirements.txt
└── .env.example
```

## Cómo personalizar a Raphael

Edita `BASE_SYSTEM_PROMPT` en `app.py` para cambiar la personalidad, el tono o
las áreas en las que se especializa (por ejemplo: finanzas personales,
salud, productividad, etc.).

## Problemas comunes

- `zsh: command not found: python`: usa `python3 app.py`.
- `Falta configurar ANTHROPIC_API_KEY`: abre `.env` y reemplaza `tu_api_key_aqui` por tu clave real.
- `La ANTHROPIC_API_KEY no es valida`: revisa que la clave esté completa y activa en Anthropic.

## Próximos pasos sugeridos

- Añadir borrado o renombrado manual de conversaciones.
- Añadir autenticación si vas a tener varios usuarios.
- Desplegar en un servicio como Render, Railway o Fly.io.


## Acceso privado por Internet

La rama de seguridad incorpora inicio de sesión de propietario. Antes de desplegar:

1. Instala las dependencias: `python3 -m pip install -r requirements.txt`.
2. Copia `.env.example` a `.env` y configura `FLASK_SECRET_KEY`, `RAPHAEL_USERNAME`, `RAPHAEL_PASSWORD_HASH` y `ANTHROPIC_API_KEY`.
3. Genera la clave secreta con `python -c "import secrets; print(secrets.token_hex(32))"`.
4. Genera el hash de contraseña con `python -c "from werkzeug.security import generate_password_hash; print(generate_password_hash('TU_CONTRASEÑA'))"`.
5. Mantén `RAPHAEL_COOKIE_SECURE=true` detrás de HTTPS.
6. Ejecuta pruebas con `pytest -q`.
7. Define `RAPHAEL_ENV=production`, `RAPHAEL_TRUSTED_HOSTS`, `RAPHAEL_TRUSTED_PROXY_HOPS=0` si no has verificado la cadena de proxies, y `RAPHAEL_LIMITER_STORAGE` con Redis-compatible Key Value.
8. Desde el directorio `raphael`, ejecuta `waitress-serve --listen=127.0.0.1:8080 wsgi:app` detrás de un proxy HTTPS. No uses `python app.py` como servidor público de producción.

Consulta [SECURITY.md](SECURITY.md) antes de desplegar. No subas nunca el archivo `.env` ni una base de datos con conversaciones reales.


## Memoria estructurada (fase inicial)

La memoria se gestiona en `memory_manager.py` y permanece en SQLite. Las categorías admitidas son `personal`, `project`, `contextual`, `decision` y `general`.

- Los recuerdos sugeridos por Claude se guardan como `pending` y no se incorporan al prompt.
- Los recuerdos creados explícitamente por el propietario se guardan como `active`.
- El propietario autenticado puede listar, editar, activar, volver a poner en revisión y eliminar recuerdos mediante `/api/memories`.
- Cada registro incluye origen, confianza, fechas de creación y actualización, caducidad opcional y referencia opcional al recuerdo sustituido.
- La migración añade columnas a la tabla existente y conserva el texto de los recuerdos anteriores; los registros heredados pasan a revisión (`pending`) para evitar tratarlos como hechos verificados.

Esta fase no implementa todavía detección semántica automática de contradicciones. Las actualizaciones son explícitas y el campo `supersedes_id` queda preparado para registrar sustituciones controladas.


## Memoria inteligente v2: propuestas supervisadas y contexto

- La recuperación contextual puntúa los recuerdos activos por coincidencia de términos con el mensaje actual, confianza y categoría. Solo incorpora hasta ocho recuerdos relevantes al prompt.
- La búsqueda de candidatos relacionados usa similitud léxica (Jaccard sobre tokens normalizados). Es una señal de relación, no una prueba semántica de contradicción.
- El propietario puede crear una propuesta de sustitución desde el panel de memoria. La propuesta compara el recuerdo vigente con el texto nuevo y queda pendiente.
- Aprobar una propuesta conserva el recuerdo anterior con estado `pending`, crea el nuevo recuerdo activo y enlaza ambos mediante `supersedes_id`.
- Rechazar una propuesta no modifica el recuerdo original.
- No se sustituye ni elimina automáticamente ningún recuerdo.

API adicional (todas las rutas requieren sesión del propietario y protección CSRF):
- `POST /api/memories/{id}/proposals`
- `GET /api/memory-proposals?status=pending`
- `PATCH /api/memory-proposals/{id}` con `decision=approved` o `decision=rejected`

La detección actual es deliberadamente conservadora y basada en coincidencia léxica. No debe describirse como comprensión semántica ni como detección infalible de contradicciones.


## Memoria inteligente v3: temporalidad, relaciones y auditoría

- Los recuerdos admiten `valid_from` y `valid_until`; la recuperación contextual, los candidatos de actualización y la lista temporal excluyen recuerdos fuera de su ventana de vigencia.
- Al aprobar una sustitución, el recuerdo anterior pasa a revisión y su ventana de validez se cierra. La nueva versión queda activa, registra su fecha de inicio y enlaza el identificador anterior mediante `supersedes_id`.
- Las aprobaciones, rechazos y relaciones generan eventos consultables en el historial de auditoría.
- Los recuerdos pueden relacionarse como `related_to`, `supports`, `contradicts`, `derived_from` o `same_topic`; las relaciones se muestran en ambos sentidos.
- La revisión semántica solicita a Claude una clasificación orientativa entre contradicción, actualización, complemento o ausencia de relación. La respuesta se valida y nunca cambia recuerdos automáticamente.
- Las rutas de historial, relaciones, línea temporal y revisión semántica requieren autenticación; la revisión semántica tiene límite de cinco solicitudes por minuto.

La clasificación de Claude es una ayuda, no una verdad verificada. La similitud léxica solo selecciona candidatos; el propietario debe revisar y aprobar cualquier sustitución. Las fechas de vigencia no sustituyen a las fechas de caducidad `expires_at`, que siguen siendo un control independiente.


## Adjuntos temporales en el chat

El compositor permite adjuntar hasta cinco archivos por mensaje: PDF, DOCX, TXT,
JPG/JPEG, PNG, WEBP, ZIP, PPTX, CSV y XLSX.

- Imágenes y PDF se envían como bloques multimodales compatibles con la API de Anthropic.
- DOCX, PPTX, XLSX, CSV y TXT se procesan localmente para extraer texto.
- ZIP se inspecciona en memoria y procesa archivos compatibles sin extraer rutas al disco; no se admiten ZIP anidados.
- Límites: 8 MB por archivo, 15 MB en total y 16 MB por petición multipart.
- Los adjuntos no se guardan en disco ni se incorporan como archivos a SQLite. El texto extraído y las referencias a los adjuntos sí forman parte del historial de conversación, como contenido del mensaje.
- La carga requiere autenticación, CSRF y un límite específico de 10 solicitudes por minuto.
- La infraestructura adicional no requiere servicios de pago ni nuevas dependencias externas.

Los archivos se procesan durante la petición y se descartan al terminar. Los datos de texto extraídos permanecen en el historial normal del chat; no adjuntes información sensible si no quieres que forme parte de ese historial.
