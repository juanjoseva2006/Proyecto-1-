# Resultado de verificación local

Fecha: 30 de septiembre de 2026. Entorno: Debian en WSL, gcc, Python 3.11,
libcurl y json-c instalados mediante paquetes del sistema.

Compilación de `build/server` y `build/server-test` con C11,
`-Wall -Wextra -Wpedantic -Werror -pthread`: correcta, sin advertencias.

Comando final:

```sh
make test
```

Resultado observado:

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

**No verificado todavía:** Supabase real, despliegue entre máquinas distintas y
configuración de firewall/DNS de la red del equipo. El proveedor de las pruebas es
simulado, solo para el binario compilado con TEST_AUTH_HTTP. La implementación
normal no acepta ese proveedor. Estos resultados no sustituyen la demostración
con las cuentas reales que aún debe configurar el equipo.
