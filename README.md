# Sistema de monitoreo distribuido

Implementación de las fases 2 y 3 a partir de `descripcion proyecto 1.docx`.
Servidor **C11 + sockets Berkeley + pthreads**, dos o más nodos simulados y
cliente de administración **Python 3**, protocolo ASCII sobre TCP y autenticación
externa **Supabase Auth**. La propuesta original se conserva sin modificar.

## Estado de la entrega

El código incluye registro, telemetría, eventos con ACK y deduplicación, consultas,
cinco muestras y cinco eventos por nodo, perfiles LECTOR/ADMIN, concurrencia,
resolución de nombres, reconexión y logs. `make test` ejecuta pruebas de integración
contra el servidor C usando sockets reales y un proveedor de identidad simulado.

**Pendiente de configuración externa:** crear el proyecto Supabase y las cuentas
de prueba; seguir [la guía](docs/SUPABASE.md). No hay cuentas ni claves de respaldo
en el servidor. Las pruebas automatizadas no acreditan una sesión con Supabase real.

## Preparación (Debian/Ubuntu o Windows con WSL Debian)

Desde Windows abrir Debian con `wsl -d Debian`. En Debian:

```sh
sudo apt-get update
sudo apt-get install -y build-essential pkg-config libcurl4-openssl-dev libjson-c-dev python3 ca-certificates
cd '/mnt/c/Users/juanj/OneDrive/Documentos/ChatGPT/PROYECTO 1/proyecto'
make
make test
```

En otra máquina, usar la ruta donde se clonó este repositorio. Python no requiere
paquetes de pip. El servidor usa POSIX/Linux; los clientes también funcionan en Windows.

## Demostración completa en cuatro terminales

Primero configurar Supabase según `docs/SUPABASE.md`. En la terminal del servidor:

```sh
export SUPABASE_URL='https://TU-PROYECTO.supabase.co'
export SUPABASE_PUBLISHABLE_KEY='TU-CLAVE-PUBLICABLE'
export MONITOR_BIND_HOST='localhost'
./build/server 5000 monitor.log
```

`MONITOR_BIND_HOST` es opcional: sin él se escucha en una dirección local disponible
de todas las interfaces. Para la demostración local se recomienda `localhost`.
El puerto y archivo de logs son obligatoriamente argumentos de consola.

Terminales de los dos nodos (iniciar una vez por ejecución del servidor):

```sh
python3 clients/node.py --host localhost --port 5000 --node nodo1 --stable-id --seed 1 --event-every 3
```

```sh
python3 clients/node.py --host localhost --port 5000 --node nodo2 --stable-id --seed 2 --event-every 3
```

Terminal del cliente:

```sh
python3 clients/client.py --host localhost --port 5000 --email TU-CORREO-DE-PRUEBA
```

La clave se solicita sin mostrarla. Esperar al menos 25 segundos para acumular cinco
muestras. Escribir en el cliente:

```text
CURRENT nodo1
HISTORY nodo1
CURRENT nodo2
EVENTS nodo1
salir
```

`EVENTS` requiere ADMIN. Abrir otro cliente con LECTOR y verificar que puede consultar
CURRENT/HISTORY, pero recibe FORBIDDEN para EVENTS. Los resultados aparecen en tablas
en la terminal. No se requiere una interfaz gráfica ni un navegador.

Sin `--stable-id`, el nodo agrega un sufijo aleatorio y muestra el nombre exacto que se
debe consultar. Es el modo recomendado al reiniciar nodos: evita que el contador de
eventos reiniciado se confunda con eventos antiguos. Con `--stable-id`, reiniciar
también el servidor o elegir otro nombre al reiniciar el proceso del nodo.

Para varias máquinas, configurar un nombre DNS alcanzable del servidor en `--host`,
el enlace de escucha y el firewall del puerto elegido. No hay IP fija en el código.
Si DNS o la conexión fallan, nodos y clientes informan el error y reintentan cada 5 s.

## Qué demostrar

1. Dos nodos y dos clientes simultáneos; estados independientes e históricos de cinco filas.
2. Un evento confirmado; después, su consulta como ADMIN.
3. Un LECTOR rechazado al consultar eventos.
4. Interrumpir un nodo con Ctrl+C y consultar DESCONECTADO; un nodo conectado sin
   muestras recientes pasa a SIN_DATOS después de 15 s.
5. Interrumpir y reiniciar el servidor: los procesos activos reconectan. Los históricos
   del servidor se reinician porque el diseño es en memoria.
6. Ejecutar `make test`: incluye entradas inválidas, fragmentación/coalescencia TCP,
   timeout de línea, concurrencia y reenvío de eventos sin duplicarlos.
7. Revisar `monitor.log`: fecha, origen IP:puerto, peticiones y respuestas; AUTH se redacta.

## Archivos

| Archivo | Responsabilidad |
|---|---|
| `server/server.c` | TCP, framing, validación, estados, hilos, consultas y logs |
| `server/auth.c` | Consulta HTTPS a Supabase y validación de `app_metadata.perfil` |
| `clients/protocol.py` | Envío/recepción con límites, timeout y correlación |
| `clients/node.py` | Telemetría simulada, cola de eventos y reconexión |
| `clients/client.py` | Autenticación y tablas de consulta |
| `tests/test_integration.py` | Pruebas del servidor C con sockets reales |
| `docs/PROTOCOLO.md` | Especificación implementable del protocolo |
| `docs/INFORME.md` | Matriz de requisitos, decisiones y sustentación |
| `docs/SUPABASE.md` | Preparación del proveedor externo |

## Límites del diseño

- TCP de laboratorio sin cifrado entre clientes/nodos y servidor. Usar red aislada y
  credenciales exclusivas de prueba; HTTPS protege únicamente servidor-Supabase.
- Memoria volátil: cinco muestras y cinco eventos por nodo; los logs no reconstruyen estado.
- 256 identificadores de nodo y 128 conexiones simultáneas por proceso. No se expulsan
  nodos históricos automáticamente. Al alcanzar capacidad se responde INTERNAL.
- REGISTER identifica un nodo, pero no autentica criptográficamente el dispositivo.
- Los perfiles se validan al abrir sesión; cambios posteriores requieren reconexión.
- Un ACK de EVENT confirma aceptación en memoria, no persistencia en disco.
- Datos de CPU/temperatura son simulados y reproducibles con `--seed`.
- No se fabricó historial de las fases previas. El historial existente del repositorio
  y los cambios de esta implementación reflejan el trabajo realmente disponible.
