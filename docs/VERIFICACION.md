# Resultado de verificación local

Fecha: 30 de septiembre de 2026. Entorno: Debian en WSL, gcc, Python 3.11,
libcurl y json-c instalados mediante paquetes del sistema.

Compilación de `build/server` y `build/server-test` con C11,
`-Wall -Wextra -Wpedantic -Werror -pthread`: correcta, sin advertencias.

Comando de la suite automatizada:

```sh
make test
```

Resultado de la ejecución inicial:

```text
Ran 19 tests in 27.084s
OK
```

| Prueba | Resultado |
|---|---|
| Cliente se recupera de fallo DNS | OK |
| Nodo se recupera de fallo DNS | OK |
| ACK no leído, reenvío y últimos cinco eventos | OK |
| Timeout de autenticación no bloquea nodos | OK |
| Errores de autenticación y nuevo intento | OK |
| 40 consultas, 20 trabajadores concurrentes | OK |
| Estados ACTIVO, SIN_DATOS y DESCONECTADO | OK |
| Evento duplicado después de reconectar | OK |
| Registro duplicado | OK |
| Fragmentación y agrupación de líneas TCP | OK |
| Entradas inválidas no alteran los datos | OK |
| Logs ocultan claves, incluso en AUTH malformado | OK |
| Ids inválidos y bytes binarios | OK |
| Línea excesiva y timeout de línea parcial | OK |
| Binario normal rechaza proveedor HTTP de pruebas | OK |
| Cliente de administración ejecutado como proceso | OK |
| Nodo ejecutado como proceso | OK |
| Dos nodos, históricos y perfiles | OK |
| Nodo desconocido y consultas sin autenticar | OK |

La prueba del cliente de terminal se ejecuta en una sesión independiente para que
getpass lea la entrada de prueba en lugar de esperar el terminal del ejecutor.

Posteriormente se corrigió un fallo intermitente de la prueba concurrente ampliando
la cola de conexiones del proveedor simulado a 128. Tras esa corrección pasaron las
19 pruebas en 26.384 s y diez repeticiones adicionales de la prueba concurrente.
Las ejecuciones de GitHub Actions para el commit `6a955db` también finalizaron
correctamente, tanto para push como para pull request.

## Prueba manual con Supabase real

Fecha: 30 de septiembre de 2026. Fuente de evidencia: capturas de pantalla aportadas
por el equipo durante la ejecución en Windows Terminal con Debian/WSL. Esta sección
registra lo observado en esas capturas; no constituye una nueva ejecución automatizada.

Se creó el proyecto Monitoreo-proyecto en Supabase. La consulta administrativa mostró
las cuentas `admin@example.com` y `user@example.com`, con perfiles ADMIN y LECTOR
respectivamente y `confirmado = true`. Son cuentas de laboratorio del proveedor real.

| Comprobación manual | Resultado observado |
|---|---|
| Inicio de sesión de admin@example.com | El cliente mostró `Sesion ADMIN` |
| ADMIN: CURRENT nodo1 | Muestra con enlace ACTIVO |
| ADMIN: HISTORY nodo1 | Cinco muestras, con tiempos separados por 5 s |
| ADMIN: CURRENT nodo2 | Muestra independiente con enlace ACTIVO |
| ADMIN: EVENTS nodo1 | Cinco eventos, incluidos UMBRAL y CAMBIO_ESTADO |
| Inicio de sesión de user@example.com | El cliente mostró `Sesion LECTOR` |
| LECTOR: CURRENT nodo1 | Consulta permitida, enlace ACTIVO |
| LECTOR: HISTORY nodo2 | Consulta permitida con cinco muestras |
| LECTOR: EVENTS nodo1 | `FORBIDDEN: Peticion_rechazada`, resultado esperado |
| Detener nodo1 y consultar CURRENT nodo1 | Última muestra conservada, enlace DESCONECTADO |
| Consultar CURRENT nodo2 después de detener nodo1 | Enlace ACTIVO; el otro nodo continuó funcionando |

En la última captura, la muestra conservada de nodo1 tenía fecha local
`2026-09-30 19:34:42`, CPU 27 y temperatura 63, con enlace DESCONECTADO.
La muestra de nodo2 tenía fecha `2026-09-30 19:35:08`, CPU 8 y temperatura 35,
con enlace ACTIVO. Las métricas son simuladas por los nodos; la autenticación se
realizó contra Supabase real.

## Alcance y límites de la evidencia

La prueba manual confirma el flujo local con dos nodos, autenticación real,
perfiles, consultas, históricos, eventos y aislamiento de la desconexión de un nodo.
Las capturas originales se compartieron en la conversación de trabajo; no se
incluyen como archivos en este repositorio. No se publican contraseñas ni `.env`.

El proveedor de las pruebas automatizadas sigue siendo simulado, solo para el
binario compilado con TEST_AUTH_HTTP. El binario normal no acepta ese proveedor.
La prueba manual real complementa esa suite y no implica que todos sus escenarios
de error se hayan repetido contra Supabase.

**No verificado todavía:** despliegue entre máquinas distintas y configuración de
firewall/DNS de la red del equipo. La demostración manual utilizó procesos locales
en el mismo equipo y no acredita esos escenarios de despliegue.
