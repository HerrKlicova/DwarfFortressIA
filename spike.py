# -*- coding: utf-8 -*-
"""
spike.py -- script feo de un solo fichero. Se tira despues.

Demuestra que existe un tubo:  Dwarf Fortress  ->  LLM local (Player2)  ->  Dwarf Fortress

  Paso 1: leer el nombre de un enano vivo via la interfaz remota de DFHack
  Paso 2: mandar una frase a Player2 y recibir respuesta (cronometrado)
  Paso 3: escribir esa respuesta dentro del juego como anuncio

Cero dependencias: solo stdlib. Sin async, sin clases, sin abstracciones.

ANTES DE EJECUTAR
  1. Dwarf Fortress abierto, con una FORTALEZA CARGADA (no el menu principal).
  2. Copiar dfhack_spike.lua a:
       D:\\Steam\\steamapps\\common\\Dwarf Fortress\\dfhack-config\\scripts\\dfhack_spike.lua
  3. App de escritorio de Player2 abierta y con sesion iniciada.

  Luego:  python spike.py

REFERENCIAS (todo verificado contra fuente antes de escribir esto)
  - Protocolo de cable DFHack, byte a byte:  dfhack/docs/dev/Remote.rst
  - RunCommand tiene ID FIJO 1 (no hace falta BindMethod):  misma tabla
  - CoreRunCommandRequest / CoreTextNotification:  dfhack/library/proto/CoreProtocol.proto
  - Servidor arranca solo en 127.0.0.1:5000:  dfhack/library/Core.cpp + RemoteServer.cpp
  - API local de Player2 en 127.0.0.1:4315, POST /v1/chat/completions:  OpenAPI de Player2
"""

import json
import os
import socket
import struct
import sys
import time
import urllib.error
import urllib.request

# ---------------------------------------------------------------- configuracion

DFHACK_HOST = "127.0.0.1"
DFHACK_PORT = int(os.environ.get("DFHACK_PORT", "5000"))

# La doc de Player2 avisa: usar 127.0.0.1, NO localhost (conflicto IPv6).
PLAYER2_HOST = "127.0.0.1"
PLAYER2_PORT_DEFAULT = 4315

# Opcional. La doc lo marca "[For Game Developers Only]". Vacio = no se manda.
PLAYER2_GAME_KEY = os.environ.get("PLAYER2_GAME_KEY", "")

# ---------------------------------------------------------------- DFHack: protocolo

# De dfhack/library/RemoteClient.h, reproducidos en docs/dev/Remote.rst
RPC_REPLY_RESULT = -1
RPC_REPLY_FAIL = -2
RPC_REPLY_TEXT = -3
RPC_REQUEST_QUIT = -4

RPC_BIND_METHOD = 0
RPC_RUN_COMMAND = 1  # ID fijo, documentado


def _varint(n):
    """Codifica un entero como varint de protobuf."""
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _read_varint(buf, i):
    shift = 0
    val = 0
    while True:
        b = buf[i]
        i += 1
        val |= (b & 0x7F) << shift
        if not (b & 0x80):
            return val, i
        shift += 7


def _field_str(num, s):
    """Campo protobuf de tipo string (wire type 2)."""
    data = s.encode("utf-8")
    return bytes([(num << 3) | 2]) + _varint(len(data)) + data


def _walk(buf):
    """Recorre los campos de un mensaje protobuf sin conocer el esquema."""
    i = 0
    n = len(buf)
    while i < n:
        key, i = _read_varint(buf, i)
        num, wt = key >> 3, key & 7
        if wt == 0:
            val, i = _read_varint(buf, i)
            yield num, wt, val
        elif wt == 2:
            ln, i = _read_varint(buf, i)
            yield num, wt, buf[i:i + ln]
            i += ln
        elif wt == 5:
            yield num, wt, buf[i:i + 4]
            i += 4
        elif wt == 1:
            yield num, wt, buf[i:i + 8]
            i += 8
        else:
            raise ValueError("wire type %d no soportado" % wt)


def _parse_text_notification(buf):
    """CoreTextNotification { repeated CoreTextFragment fragments = 1 }
       CoreTextFragment    { required string text = 1; optional Color color = 2 }"""
    fragments = []
    for num, wt, val in _walk(buf):
        if num == 1 and wt == 2:
            for n2, w2, v2 in _walk(val):
                if n2 == 1 and w2 == 2:
                    fragments.append(v2.decode("utf-8", "replace"))
    return fragments


def _recv_exact(sock, n):
    """recv() puede devolver menos bytes de los pedidos. Aqui no."""
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise RuntimeError("DFHack cerro la conexion a mitad de un mensaje")
        buf += chunk
    return buf


def _send_msg(sock, msg_id, payload):
    # header: int16 id, int16 padding, int32 size -- todo little-endian
    sock.sendall(struct.pack("<hhI", msg_id, 0, len(payload)) + payload)


def _recv_header(sock):
    h = _recv_exact(sock, 8)
    msg_id = struct.unpack("<h", h[0:2])[0]
    size_unsigned = struct.unpack("<I", h[4:8])[0]
    # En una respuesta FAIL, el campo "size" se reutiliza como codigo de error
    # con signo (docs/dev/Remote.rst: "header(RPC_REPLY_FAIL, command_result)").
    size_signed = struct.unpack("<i", h[4:8])[0]
    return msg_id, size_unsigned, size_signed


def dfhack_connect():
    sock = socket.create_connection((DFHACK_HOST, DFHACK_PORT), timeout=15)
    sock.sendall(b"DFHack?\n" + struct.pack("<i", 1))
    reply = _recv_exact(sock, 12)
    if reply != b"DFHack!\n" + struct.pack("<i", 1):
        raise RuntimeError("handshake rechazado, respuesta: %r" % reply)
    return sock


def dfhack_run_command(sock, command, arguments):
    """Ejecuta un comando DFHack y devuelve (salida_de_texto, codigo_error_o_None)."""
    payload = _field_str(1, command)
    for a in arguments:
        payload += _field_str(2, a)
    _send_msg(sock, RPC_RUN_COMMAND, payload)

    fragments = []
    while True:
        msg_id, size_u, size_s = _recv_header(sock)
        if msg_id == RPC_REPLY_TEXT:
            fragments.extend(_parse_text_notification(_recv_exact(sock, size_u)))
        elif msg_id == RPC_REPLY_RESULT:
            if size_u:
                _recv_exact(sock, size_u)  # EmptyMessage, normalmente 0 bytes
            return _join(fragments), None
        elif msg_id == RPC_REPLY_FAIL:
            return _join(fragments), size_s  # sin cuerpo detras
        else:
            raise RuntimeError("cabecera inesperada de DFHack: id=%d" % msg_id)


def _join(fragments):
    """DFHack puede partir una linea en varios fragmentos (uno por color) o
       mandar una linea por fragmento sin el salto. Cubrimos los dos casos."""
    raw = "".join(fragments)
    if "\n" not in raw and len(fragments) > 1:
        raw = "\n".join(fragments)
    return raw


def dfhack_quit(sock):
    try:
        _send_msg(sock, RPC_REQUEST_QUIT, b"")
    except OSError:
        pass
    try:
        sock.close()
    except OSError:
        pass


# ---------------------------------------------------------------- Player2

def player2_port():
    """El puerto por defecto es 4315, pero si esta ocupado la app se mueve y
       escribe el puerto real en api.port. Ese fichero solo existe mientras
       la app esta abierta."""
    appdata = os.environ.get("APPDATA")
    if appdata:
        path = os.path.join(appdata, "game.player2.client", "api.port")
        try:
            with open(path, "r") as f:
                p = int(f.read().strip())
            print("    (puerto leido de %s: %d)" % (path, p))
            return p
        except (OSError, ValueError):
            pass
    return PLAYER2_PORT_DEFAULT


def player2_call(port, method, path, body=None):
    url = "http://%s:%d%s" % (PLAYER2_HOST, port, path)
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if PLAYER2_GAME_KEY:
        headers["player2-game-key"] = PLAYER2_GAME_KEY
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ---------------------------------------------------------------- pasos

def paso1_leer_enano(sock):
    print("[1] Leyendo un enano vivo por la interfaz remota de DFHack...")
    salida, err = dfhack_run_command(sock, "dfhack_spike", ["dwarf"])
    if err is not None:
        print("    FALLO: DFHack devolvio codigo de error %d" % err)
        print("    Salida: %r" % salida)
        print("    Causa mas probable: dfhack_spike.lua no esta en dfhack-config\\scripts\\")
        return None
    print("    Respuesta cruda de DFHack:")
    for line in salida.splitlines():
        print("      | " + line)

    nombre = None
    for line in salida.splitlines():
        if line.startswith("NOMBRE\t"):
            nombre = line.split("\t", 1)[1].strip()
    if not nombre:
        print("    FALLO: no vino ninguna linea NOMBRE.")
        return None
    print("    -> ENANO: %s" % nombre)
    return nombre


def paso1b_sondear_campos(sock):
    print("[1b] Sondeando que campos del enano existen de verdad en tu build...")
    salida, err = dfhack_run_command(sock, "dfhack_spike", ["fields"])
    if err is not None:
        print("    FALLO: codigo %d" % err)
        return ""
    for line in salida.splitlines():
        print("      | " + line)
    return salida


def paso2_player2(nombre):
    port = player2_port()
    print("[2] Hablando con Player2 en http://%s:%d ..." % (PLAYER2_HOST, port))

    # Preflight: confirma que la app esta viva y que la sesion esta iniciada.
    try:
        health = player2_call(port, "GET", "/v1/health")
        print("    /v1/health OK -> %s" % health)
    except urllib.error.HTTPError as e:
        print("    FALLO en /v1/health: HTTP %d" % e.code)
        if e.code == 401:
            print("    -> No has iniciado sesion en la app de Player2. Abrela y entra.")
        elif e.code == 402:
            print("    -> Sin creditos en Player2.")
        return None, None
    except OSError as e:
        print("    FALLO: no hay nadie escuchando en el puerto %d (%s)" % (port, e))
        print("    -> ¿Esta abierta la app de escritorio de Player2?")
        return None, None

    frase = ("Eres un enano de Dwarf Fortress llamado %s. "
             "Di una sola frase corta, en espanol, quejandote del trabajo. "
             "Maximo 15 palabras. Sin comillas." % nombre)
    print("    Prompt: %s" % frase)

    t0 = time.perf_counter()
    try:
        r = player2_call(port, "POST", "/v1/chat/completions", {
            "messages": [{"role": "user", "content": frase}],
            "stream": False,
        })
    except urllib.error.HTTPError as e:
        print("    FALLO en /v1/chat/completions: HTTP %d" % e.code)
        print("    Cuerpo: %s" % e.read().decode("utf-8", "replace")[:500])
        return None, None
    latencia = time.perf_counter() - t0

    try:
        texto = r["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError):
        print("    FALLO: respuesta con forma inesperada:")
        print("    %s" % json.dumps(r)[:800])
        return None, None

    print("    -> RESPUESTA (%.2f s): %s" % (latencia, texto))
    return texto, latencia


def paso3_anunciar(sock, texto):
    print("[3] Inyectando la respuesta en el log de anuncios del juego...")
    salida, err = dfhack_run_command(sock, "dfhack_spike", ["announce", texto])
    if err is not None:
        print("    FALLO: codigo %d -- %r" % (err, salida))
        return False
    for line in salida.splitlines():
        print("      | " + line)
    if "OK\t" in salida:
        print("    -> Mira el log de anuncios en el juego.")
        return True
    return False


def main():
    print("=" * 66)
    print("SPIKE: Dwarf Fortress <-> LLM local")
    print("=" * 66)

    try:
        sock = dfhack_connect()
    except OSError as e:
        print("[1] FALLO conectando a DFHack en %s:%d -- %s" % (DFHACK_HOST, DFHACK_PORT, e))
        print("    Comprueba, por este orden:")
        print("     a) Dwarf Fortress abierto con DFHack cargado.")
        print("     b) En stderr.log (carpeta de DF) debe aparecer 'Listening on port 5000'.")
        print("     c) Si cambiaste el puerto, mira dfhack-config\\remote-server.json")
        print("        o exporta DFHACK_PORT.")
        return 1
    print("    Conectado y handshake OK con DFHack %s:%d" % (DFHACK_HOST, DFHACK_PORT))

    try:
        nombre = paso1_leer_enano(sock)
        if not nombre:
            return 1
        paso1b_sondear_campos(sock)

        texto, latencia = paso2_player2(nombre)
        if not texto:
            return 1

        ok = paso3_anunciar(sock, texto)
    finally:
        dfhack_quit(sock)

    print("=" * 66)
    if ok:
        print("TUBO COMPLETO. Latencia del LLM de punta a punta: %.2f s" % latencia)
        return 0
    print("El tubo se rompio en el paso 3.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
