"""Nodo simulado: telemetria periodica y eventos confirmados/reintentados."""
import argparse
from collections import deque
import random
import re
import select
import time
import uuid
from protocol import Connection, Counter, ProtocolError, Rejected


def run(args):
    name = args.node if args.stable_id else f"{args.node}-{uuid.uuid4().hex[:8]}"
    print(f"Identificador para consultas: {name}", flush=True)
    ids, pending = Counter(), deque()
    rng = random.Random(args.seed)
    connection = None
    previous = "NORMAL"
    samples = 0
    try:
        while True:
            try:
                connection = Connection(args.host, args.port)
                request_id = ids.next()
                connection.send("REGISTER", request_id, name)
                connection.ack(request_id, "REGISTER")
                print(f"Registrado: {name}", flush=True)
                next_sample = time.monotonic()
                outstanding = None
                while True:
                    now = time.monotonic()
                    if now >= next_sample and (not args.samples or samples < args.samples):
                        cpu, temperature = rng.randint(5, 100), rng.randint(25, 90)
                        state = "ALERTA" if temperature >= args.threshold else "NORMAL"
                        connection.send("STATUS", ids.next(), name, cpu, temperature, state)
                        samples += 1
                        print(f"{name}: CPU={cpu}% temperatura={temperature} estado={state}", flush=True)
                        if state != previous or (args.event_every and samples % args.event_every == 0):
                            if len(pending) >= 1000:
                                raise ProtocolError("Cola llena; detener para no perder eventos")
                            pending.append((ids.next(), "UMBRAL" if state == "ALERTA" else "CAMBIO_ESTADO", f"Temperatura_{temperature}_estado_{state}"))
                        previous = state
                        next_sample = now + args.interval
                    if pending and outstanding is None:
                        eid, code, detail = pending[0]
                        connection.send("EVENT", eid, name, code, detail)
                        outstanding = time.monotonic() + 5
                    if args.samples and samples >= args.samples and not pending:
                        return
                    if outstanding and time.monotonic() >= outstanding:
                        raise TimeoutError("ACK de evento no recibido")
                    readable, _, _ = select.select([connection.sock], [], [], 0.1)
                    if readable or connection.buffer:
                        f = connection.receive(outstanding)
                        connection.check_error(f)
                        if not pending or f != ["ACK", str(pending[0][0]), "EVENT", "OK"]:
                            raise ProtocolError(f"Respuesta inesperada: {f}")
                        print(f"Evento confirmado: {pending[0][0]}", flush=True)
                        pending.popleft()
                        outstanding = None
            except Rejected as exc:
                # A rejected event remains pending; do not report it delivered.
                print(f"Rechazo del servidor: {exc}; se conserva la cola", flush=True)
            except (OSError, ConnectionError) as exc:
                print(f"Comunicacion: {exc}; reintento en 5 s", flush=True)
            finally:
                if connection:
                    connection.close()
                    connection = None
            time.sleep(5)
    except KeyboardInterrupt:
        print(f"Nodo detenido. Eventos pendientes en memoria: {len(pending)}")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--host", default="localhost")
    p.add_argument("--port", type=int, default=5000)
    p.add_argument("--node", required=True)
    p.add_argument("--stable-id", action="store_true", help="No agregar sufijo; coordinar reinicios con el servidor")
    p.add_argument("--interval", type=float, default=5)
    p.add_argument("--threshold", type=int, default=70)
    p.add_argument("--seed", type=int)
    p.add_argument("--samples", type=int, default=0, help="0: ejecucion continua")
    p.add_argument("--event-every", type=int, default=0, help="Generar tambien un evento cada N muestras")
    args = p.parse_args()
    limit = 32 if args.stable_id else 23
    if not re.fullmatch(r"[A-Za-z0-9_-]{1," + str(limit) + "}", args.node):
        p.error(f"--node debe tener 1 a {limit} caracteres alfanumericos, - o _")
    if args.interval <= 0 or args.samples < 0 or args.event_every < 0 or not 1 <= args.port <= 65535:
        p.error("Intervalo, puerto o cantidad invalidos")
    try:
        run(args)
    except ProtocolError as exc:
        p.exit(1, f"Error de protocolo: {exc}\n")


if __name__ == "__main__":
    main()
