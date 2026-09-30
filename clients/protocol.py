"""Protocolo TCP compartido; solo biblioteca estandar de Python 3."""
import socket
import time


class ProtocolError(Exception):
    pass


class Rejected(ProtocolError):
    def __init__(self, fields):
        self.code = fields[2]
        super().__init__(f"{fields[2]}: {fields[3]}")


class Counter:
    def __init__(self):
        self.value = 0

    def next(self):
        if self.value >= 2147483647:
            raise ProtocolError("Contador agotado; iniciar una nueva ejecucion")
        self.value += 1
        return self.value


class Connection:
    def __init__(self, host, port):
        # create_connection uses getaddrinfo and tries the returned addresses.
        self.sock = socket.create_connection((host, port), timeout=5)
        self.buffer = bytearray()

    def close(self):
        self.sock.close()

    def send(self, *fields):
        strings = [str(x) for x in fields]
        if any(not s or any(c in s for c in "|\r\n") for s in strings):
            raise ProtocolError("Campo vacio o separador reservado")
        try:
            data = ("|".join(strings) + "\n").encode("ascii")
        except UnicodeEncodeError as exc:
            raise ProtocolError("El protocolo requiere ASCII") from exc
        if len(data) > 1024 or any(c < 32 or c > 126 for c in data[:-1]):
            raise ProtocolError("Mensaje invalido o demasiado largo")
        self.sock.settimeout(5)
        self.sock.sendall(data)

    def receive(self, deadline=None):
        deadline = deadline or time.monotonic() + 5
        while b"\n" not in self.buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Respuesta incompleta")
            self.sock.settimeout(remaining)
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("Servidor desconectado")
            self.buffer.extend(chunk)
            if b"\n" not in self.buffer and len(self.buffer) >= 1024:
                raise ProtocolError("Respuesta demasiado larga")
        line, _, rest = self.buffer.partition(b"\n")
        self.buffer = bytearray(rest)
        if len(line) > 1023 or any(c < 32 or c > 126 for c in line):
            raise ProtocolError("Respuesta invalida")
        return line.decode("ascii").split("|")

    def ack(self, request_id, operation):
        fields = self.receive()
        if len(fields) < 2 or fields[1] != str(request_id):
            raise ProtocolError("Respuesta con id inesperado")
        self.check_error(fields)
        if len(fields) != 4 or fields[:3] != ["ACK", str(request_id), operation]:
            raise ProtocolError(f"ACK inesperado: {fields}")
        allowed = {"LECTOR", "ADMIN"} if operation == "AUTH" else {"OK"}
        if fields[3] not in allowed:
            raise ProtocolError("Resultado ACK invalido")
        return fields[3]

    @staticmethod
    def check_error(fields):
        if fields and fields[0] == "ERROR":
            if len(fields) != 4:
                raise ProtocolError("ERROR mal formado")
            raise Rejected(fields)

    def query(self, request_id, kind, node):
        self.send("QUERY", request_id, kind, node)
        deadline = time.monotonic() + 5
        rows = []
        while True:
            f = self.receive(deadline)
            if len(f) < 2 or f[1] != str(request_id):
                raise ProtocolError("Respuesta con id inesperado")
            self.check_error(f)
            if len(f) != 6 or f[:4] != ["RESPONSE", str(request_id), kind, node] or f[4] not in {"0", "1"}:
                raise ProtocolError("Respuesta no corresponde a la consulta")
            if f[5] != "-":
                row = f[5].split(";")
                if len(row) != (4 if kind == "EVENTS" else 5):
                    raise ProtocolError("Fila mal formada")
                try:
                    if int(row[0]) < 0:
                        raise ValueError()
                    if kind == "EVENTS":
                        if not 1 <= int(row[1]) <= 2147483647 or row[2] not in {"FALLA", "UMBRAL", "CAMBIO_ESTADO"}:
                            raise ValueError()
                    elif not (0 <= int(row[1]) <= 100 and -50 <= int(row[2]) <= 150 and row[3] in {"NORMAL", "ALERTA"} and row[4] in {"ACTIVO", "SIN_DATOS", "DESCONECTADO"}):
                        raise ValueError()
                except ValueError as exc:
                    raise ProtocolError("Valores de respuesta invalidos") from exc
                rows.append(row)
            elif rows or f[4] != "1":
                raise ProtocolError("Respuesta vacia invalida")
            if len(rows) > 5:
                raise ProtocolError("Demasiadas filas")
            if f[4] == "1":
                return rows
