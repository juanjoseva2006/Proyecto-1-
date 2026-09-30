# Protocolo de monitoreo, versión 1

Especificación final correspondiente al diseño original. Aplicación cliente-servidor
sobre TCP. Nodos y clientes abren conexiones independientes al mismo puerto del
servidor; no existe comunicación directa cliente-nodo.

## Servicio y transporte

Primitivas: registrar nodo, publicar estado, reportar evento, autenticar cliente y
consultar información. TCP proporciona orden y retransmisión de segmentos. El ACK
de aplicación confirma registro/autenticación/aceptación de evento. STATUS no tiene
ACK exitoso. Una consulta puede responder hasta cinco líneas.

TCP se eligió para todo por el bajo volumen: una muestra cada 5 s, eventos esporádicos,
consultas bajo demanda. UDP sería apropiado para telemetría descartable, pero habría
que resolver asociación, secuencia y confiabilidad de eventos por separado. TCP puede
retrasar muestras recientes detrás de segmentos perdidos; no se promete tiempo real.

## Sintaxis y restricciones

ASCII imprimible, campos separados por `|`, una línea terminada en LF, máximo **1024
bytes incluido LF**. CRLF no es válido. No hay campos vacíos ni espacios alrededor de
números. Los tamaños son en bytes. TCP puede fragmentar o agrupar líneas arbitrariamente.

| Campo | Dominio |
|---|---|
| tipo | REGISTER, STATUS, EVENT, AUTH, QUERY, RESPONSE, ACK, ERROR |
| id | Decimal 1..2147483647; ERROR puede usar 0 si no se recupera id |
| nodo | 1..32 caracteres `[A-Za-z0-9_-]` |
| cpu | Entero 0..100 |
| temperatura | Entero -50..150 grados C |
| estado | NORMAL o ALERTA |
| código de evento | FALLA, UMBRAL, CAMBIO_ESTADO |
| detalle de evento | 1..80 ASCII imprimibles, sin `|` ni `;` |
| correo | 3..254 ASCII sin espacios, una @ con partes no vacías |
| clave | 1..64 ASCII imprimibles sin `|`, CR o LF |
| consulta | CURRENT, HISTORY, EVENTS |
| fin | 0: quedan filas; 1: última fila |
| tiempo | Segundos Unix de recepción del servidor |

Los nombres de tipo y valores enumerados distinguen mayúsculas. El id de respuesta
copia el de petición. Los ids de operaciones nuevas aumentan por conexión; el nodo
los genera con un contador por proceso. EVENT es la excepción: permite reenvío de un
id antiguo tras un fallo o mientras otras muestras han avanzado el contador.

## Mensajes exactos

```text
REGISTER|id|nodo
STATUS|id|nodo|cpu|temperatura|estado
EVENT|id|nodo|codigo|detalle
AUTH|id|correo|clave
QUERY|id|consulta|nodo
ACK|id|REGISTER|OK
ACK|id|EVENT|OK
ACK|id|AUTH|LECTOR
ACK|id|AUTH|ADMIN
ERROR|id|codigo|Peticion_rechazada
RESPONSE|id|consulta|nodo|fin|datos
```

Todas las líneas anteriores llevan LF real al final. ACK y RESPONSE son solamente
servidor→emisor. El cliente no envía su perfil.

Para CURRENT/HISTORY, `datos = tiempo;cpu;temperatura;estado;enlace`.
Para EVENTS, `datos = tiempo;id_evento;codigo;detalle`.
`datos = -` y `fin=1` representan un nodo conocido sin registros.
Filas ordenadas por llegada de la más antigua a la más reciente. CURRENT devuelve
solo la última muestra. HISTORY y EVENTS devuelven hasta cinco registros.

`enlace` representa el estado **actual** del nodo, incluso en filas históricas:

- ACTIVO: conexión registrada y muestra recibida hace menos de 15 s.
- SIN_DATOS: conexión registrada sin muestra reciente.
- DESCONECTADO: servidor detectó cierre/error de conexión.

La antigüedad y los deadlines usan reloj monotónico; los tiempos publicados usan Unix.
Un cierre físico de red que el sistema operativo aún no detecta puede aparecer como
SIN_DATOS. Un nodo sin ninguna muestra produce `-`, como definió la propuesta.

## Máquina de estados y procedimiento

| Estado del servidor por conexión | Entrada | Transición |
|---|---|---|
| SIN_ROL | REGISTER válido y nombre libre | NODO; ACK |
| SIN_ROL | AUTH con identidad/perfil válidos | CLIENTE; ACK con perfil |
| SIN_ROL | Otra operación | ERROR INVALID_STATE |
| NODO | STATUS válido del nodo registrado | Actualiza histórico; sin ACK |
| NODO | EVENT válido | Deduplica, guarda si es nuevo y envía ACK |
| CLIENTE | QUERY autorizada | Copia estado, libera mutex, envía RESPONSE(s) |
| NODO/CLIENTE | REGISTER/AUTH u operación de otro rol | INVALID_STATE |
| Cualquiera | Línea inválida recuperable | ERROR, conserva conexión |
| Cualquiera | Cierre, exceso de línea, byte no ASCII o timeout parcial | Cierra y libera conexión |

El nodo resuelve host, conecta, registra y espera ACK. Publica cada 5 s. Conserva cola
de eventos en memoria, uno pendiente de ACK a la vez, sin detener las muestras.
Un timeout de ACK de 5 s cierra la conexión; espera 5 s y reconecta, conservando el
evento y su id. No reenvía muestras viejas. Un error de evento se muestra y no se
considera entregado. Cola limitada a 1000: detiene el nodo si se agota.

El servidor conserva el mayor id de evento por nodo. EVENT con id menor o igual se
confirma sin volver a guardarlo. El contrato exige eventos nuevos en orden creciente;
la deduplicación no compara contenido. Reiniciar el proceso del nodo exige nombre
nuevo (sufijo automático por defecto) o reiniciar el estado del servidor.

El cliente autentica al conectar y mantiene una consulta pendiente. Valida id, tipo,
nodo y fin de todas las filas; solo muestra el conjunto cuando termina. El deadline
de respuesta completa es 5 s. Al fallar conexión descarta filas parciales, reconecta,
autentica y repite la consulta de lectura. AUTH_FAILED/FORBIDDEN permiten corregir
credenciales; AUTH_UNAVAILABLE reintenta después de 5 s.

## Autorización y errores

LECTOR: CURRENT/HISTORY. ADMIN: CURRENT/HISTORY/EVENTS. Identidad y perfil se obtienen
por HTTPS de Supabase; solamente `user.app_metadata.perfil` permite autorizar.
No hay cambio de rol ni renovación de tokens dentro de una conexión.

| Código | Causa / recuperación |
|---|---|
| BAD_FORMAT | Tipo, número de campos, campo vacío o id ilegible; corregir |
| BAD_VALUE | Número/rango/texto/consulta inválido o id nuevo no creciente |
| UNKNOWN_NODE | Nodo consultado inexistente o emisor no coincide con registro |
| NODE_IN_USE | Otra conexión ya tiene ese identificador; usar nombre distinto o esperar cierre |
| INVALID_STATE | Operación no permitida en el estado de sesión |
| AUTH_FAILED | Proveedor rechaza las credenciales; corregir |
| AUTH_UNAVAILABLE | Configuración/red/DNS/TLS/timeout/respuesta inesperada; reintentar |
| FORBIDDEN | Perfil faltante o consulta sin permiso |
| INTERNAL | Capacidad agotada; cerrar conexiones o reiniciar deliberadamente |

Un mensaje rechazado no cambia métricas, eventos, perfil ni registro. Su id nuevo
puede quedar consumido; usar otro id en el reintento corregido. Bytes no imprimibles
producen BAD_FORMAT y cierre porque no forman parte del protocolo. Las líneas que
superan 1024 bytes o tardan 5 s desde su primer byte se cierran sin respuesta; la
causa queda en logs. Una conexión ociosa sin línea parcial permanece abierta.

## Concurrencia, logs y ejemplo

Un hilo por conexión hasta 128, tabla de hasta 256 nodos protegida por mutex. Los
históricos se copian bajo el mutex y se envían después de liberarlo. HTTPS tampoco
sostiene el mutex. Otro mutex serializa logs para evitar entremezclar líneas.
Logs incluyen fecha, IP y puerto de origen, dirección y peticiones/respuestas. AUTH
y entradas inválidas registran resumen sin cuerpo para no filtrar claves malformadas.

```text
N -> S: REGISTER|1|nodo1
S -> N: ACK|1|REGISTER|OK
N -> S: STATUS|2|nodo1|35|42|NORMAL
N -> S: EVENT|3|nodo1|UMBRAL|Temperatura_alta
S -> N: ACK|3|EVENT|OK
C -> S: AUTH|1|cuenta@example.com|ClaveDeLaboratorio
S -> C: ACK|1|AUTH|ADMIN
C -> S: QUERY|2|CURRENT|nodo1
S -> C: RESPONSE|2|CURRENT|nodo1|1|1790800000;35;42;NORMAL;ACTIVO
```

Tiempo ilustrativo. Tras reconexión, REGISTER usa un id nuevo y EVENT reenvía 3;
el servidor confirma sin duplicarlo. Los ACK, históricos y colas no garantizan
durabilidad tras caída de procesos; es el límite explícito del diseño inicial.
