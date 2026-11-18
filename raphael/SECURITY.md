# Seguridad y despliegue remoto de Raphael

## Estado
Esta rama añade autenticación de propietario, protección CSRF, límites básicos de peticiones y cookies de sesión endurecidas. Es una base de desarrollo, no una certificación de seguridad ni una autorización para desplegar directamente en Internet.

## Configuración obligatoria
1. Instala las versiones verificadas de `requirements.lock` con `python -m pip install -r requirements.lock`. Para actualizar dependencias, revisa `requirements.txt`, genera un nuevo lock y ejecuta toda la CI antes de aceptarlo.
2. Copia `.env.example` a `.env`.
3. Genera una clave Flask con `python -c "import secrets; print(secrets.token_hex(32))"`. Debe tener al menos 32 caracteres aleatorios; la aplicación rechaza claves cortas o valores de ejemplo.
4. Configura `RAPHAEL_TRUSTED_HOSTS` con el dominio exacto de publicación, separado por comas si hay varios (por ejemplo, `raphael.example.com`). No incluyas esquemas ni rutas.
5. Genera el hash de contraseña con `python -c "from werkzeug.security import generate_password_hash; print(generate_password_hash('CONTRASEÑA_LARGA'))"`.
6. Define `RAPHAEL_USERNAME`, `RAPHAEL_PASSWORD_HASH`, `FLASK_SECRET_KEY` y tu `ANTHROPIC_API_KEY`.
7. Define `RAPHAEL_ENV=production`, mantén `RAPHAEL_COOKIE_SECURE=true`, y configura `RAPHAEL_TRUSTED_HOSTS` con todos los nombres de host exactos.
8. Configura `RAPHAEL_LIMITER_STORAGE` con una instancia Redis (`redis://` o `rediss://`). La aplicación rechaza el arranque en producción si se usa `memory://`.
9. Define `RAPHAEL_TRUSTED_PROXY_HOPS` entre `0` y `5`. Usa `0` si no has verificado la cadena exacta de proxies: la aplicación no confiará en cabeceras reenviadas y el rate limiter verá la IP del proxy. Usa un valor mayor solo cuando la topología esté documentada y verificada.
10. Ejecuta Waitress detrás de un proxy TLS. Ejemplo desde el directorio `raphael`: `waitress-serve --listen=127.0.0.1:8080 wsgi:app`. No uses el servidor integrado de Flask en producción.

## Límites conocidos
- La cookie de sesión utiliza un nombre propio, `HttpOnly`, `SameSite=Lax`, `Secure` configurable y una duración de 12 horas. El inicio de sesión limpia cualquier estado previo de sesión y el cierre la vacía por completo.
- La aplicación puede restringir los encabezados `Host` mediante `RAPHAEL_TRUSTED_HOSTS`. Configúralo con el dominio real antes de exponer el servicio.
- En desarrollo y pruebas, el rate limiting usa memoria local. En producción, el arranque exige Redis para compartir contadores entre procesos e instancias.
- No existe recuperación de contraseña ni registro de usuarios.
- Deben probarse los flujos de login, logout, CSRF, sesiones y autorización de cada endpoint antes de publicar.
- `ProxyFix` solo se activa cuando `RAPHAEL_TRUSTED_PROXY_HOPS` es mayor que cero. Con `0`, no se confía en cabeceras reenviadas; en plataformas gestionadas el rate limiter puede ver una IP compartida del proxy, aceptable para una instancia privada de un solo propietario. Mantén la confianza en proxies desactivada hasta verificar la topología exacta.
- Configura HTTPS, HSTS en el proxy, firewall, copias cifradas y monitorización en el entorno de alojamiento.


## Continuous integration

GitHub Actions workflow `.github/workflows/security-tests.yml` runs the automated security tests on pull requests targeting `main` and pushes to `main` or `audit/**`. The workflow uses an ephemeral CI database and non-production test credentials. A passing workflow is required before treating the code as verified.
