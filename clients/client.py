"""Cliente de administracion con tablas de estado, historico y eventos."""
import argparse
from datetime import datetime
import getpass
import time
from protocol import Connection, Counter, ProtocolError, Rejected


def table(kind, rows):
    if not rows:
        print("Sin datos.")
        return
    headers = ["Fecha (local)", "ID evento", "Codigo", "Detalle"] if kind == "EVENTS" else ["Fecha (local)", "CPU %", "Temp C", "Estado", "Enlace"]
    display = [[datetime.fromtimestamp(int(r[0])).strftime("%Y-%m-%d %H:%M:%S"), *r[1:]] for r in rows]
    widths = [max(len(str(row[i])) for row in [headers, *display]) for i in range(len(headers))]
    for row in [headers, *display]:
        print(" | ".join(str(cell).ljust(width) for cell, width in zip(row, widths)))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--host", default="localhost")
    p.add_argument("--port", type=int, default=5000)
    p.add_argument("--email")
    args = p.parse_args()
    if not 1 <= args.port <= 65535:
        p.error("Puerto invalido")
    email = args.email or input("Correo: ").strip()
    password = getpass.getpass("Clave de prueba: ")
    ids, pending = Counter(), None
    connection = None
    try:
        while True:
            try:
                connection = Connection(args.host, args.port)
                request_id = ids.next()
                connection.send("AUTH", request_id, email, password)
                profile = connection.ack(request_id, "AUTH")
                print(f"Sesion {profile}. Comandos: CURRENT nodo, HISTORY nodo" + (", EVENTS nodo" if profile == "ADMIN" else "") + ", salir")
                while True:
                    if pending is None:
                        command = input("consulta> ").strip()
                        if command.lower() in {"salir", "exit", "quit"}:
                            return
                        fields = command.split()
                        if len(fields) != 2 or fields[0].upper() not in {"CURRENT", "HISTORY", "EVENTS"}:
                            print("Usa CURRENT/HISTORY/EVENTS identificador_del_nodo")
                            continue
                        pending = (fields[0].upper(), fields[1])
                    try:
                        rows = connection.query(ids.next(), *pending)
                        table(pending[0], rows)
                    except Rejected as exc:
                        print(exc)
                    pending = None
            except Rejected as exc:
                print(f"Autenticacion: {exc}")
                if exc.code == "AUTH_UNAVAILABLE":
                    time.sleep(5)
                else:
                    email = input("Correo (o salir): ").strip()
                    if email.lower() == "salir":
                        return
                    password = getpass.getpass("Clave de prueba: ")
            except (OSError, ConnectionError) as exc:
                print(f"Conexion: {exc}. Reintento en 5 s; se descarta cualquier respuesta parcial.")
                time.sleep(5)
            except ProtocolError as exc:
                print(f"Protocolo: {exc}. Se reiniciara la sesion en 5 s.")
                time.sleep(5)
            finally:
                if connection:
                    connection.close()
                    connection = None
    except (KeyboardInterrupt, EOFError):
        print("\nSesion cerrada.")


if __name__ == "__main__":
    main()
