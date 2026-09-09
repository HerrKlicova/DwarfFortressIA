# -*- coding: utf-8 -*-
"""
probar_contrato.py -- valida df_estado.lua contra el juego real.

Se ejecuta antes de construir el servicio encima: si el contrato JSON no sale
limpio, lo demas no tiene sentido.

  1. Copiar df_estado.lua a  <Dwarf Fortress>/dfhack-config/scripts/
  2. Fortaleza cargada. Player2 no hace falta aqui.
  3. python probar_contrato.py
"""

import json
import sys

import spike

FALLOS = []


def dfhack(sock, *args):
    """Ejecuta df_estado y devuelve el JSON ya parseado, o None."""
    salida, err = spike.dfhack_run_command(sock, "df_estado", list(args))

    if err is not None:
        print("    DFHack devolvio error %d" % err)
        for linea in salida.splitlines():          # primero lo que dijo el motor
            print("      | " + linea)
        if "stack traceback" in salida or ".lua:" in salida:
            print("    -> error DENTRO del script lua; la linea del traceback dice donde")
        elif not salida.strip():
            print("    -> sin salida: ¿esta df_estado.lua en dfhack-config/scripts/ ?")
        return None

    cruda = None
    for linea in salida.splitlines():
        if linea.startswith("JSON|"):
            cruda = linea[5:]
    if cruda is None:
        print("    No vino ninguna linea JSON|. Salida cruda:")
        for linea in salida.splitlines():
            print("      | " + linea)
        return None

    try:
        return json.loads(cruda)
    except json.JSONDecodeError as e:
        print("    El JSON no parsea: %s" % e)
        print("    Crudo (primeros 400 car.): %s" % cruda[:400])
        return None


def exige(condicion, etiqueta):
    print("    %-52s %s" % (etiqueta, "OK" if condicion else "FALLA"))
    if not condicion:
        FALLOS.append(etiqueta)


def es_lista(x):
    """Lua no distingue lista vacia de objeto vacio: {} puede llegar como dict."""
    return isinstance(x, list) or x == {}


def main():
    try:
        sock = spike.dfhack_connect()
    except OSError as e:
        print("No hay conexion con DFHack: %s" % e)
        print("¿Esta abierto Dwarf Fortress?")
        return 1
    print("Conectado a DFHack 127.0.0.1:%d\n" % spike.DFHACK_PORT)

    try:
        print("[1] df_estado estado")
        d = dfhack(sock, "estado")
        if d is None:
            return 1
        print("    %s" % json.dumps(d, ensure_ascii=False)[:200])
        for k in ("ok", "mundo", "mapa", "pausa", "frame", "n_ciudadanos"):
            exige(k in d, "tiene la clave '%s'" % k)
        exige(d.get("ok") is True, "ok es true")
        exige(d.get("mundo") is True and d.get("mapa") is True,
              "hay partida cargada (si no, el resto fallara)")

        print("\n[2] df_estado enanos n=3 detalle=basico")
        d = dfhack(sock, "enanos", "n=3", "detalle=basico")
        if d is None:
            return 1
        exige(d.get("ok") is True, "ok es true")
        exige(isinstance(d.get("enanos"), list), "'enanos' es una lista")
        exige(len(d.get("enanos", [])) == 3, "devuelve 3 enanos en UNA sola llamada")
        if d.get("enanos"):
            e = d["enanos"][0]
            for k in ("id", "nombre", "profesion", "edad", "adulto", "estres"):
                exige(k in e, "el enano tiene '%s'" % k)
            exige("rasgos" not in e, "detalle=basico NO trae rasgos")
            print("    primero: %s, %s, %s anos, estres %s"
                  % (e.get("nombre"), e.get("profesion"), e.get("edad"), e.get("estres")))
            ids = [x.get("id") for x in d["enanos"]]
            exige(len(set(ids)) == len(ids), "los 3 enanos son distintos")

        print("\n[3] df_estado enanos n=1 detalle=completo pensamientos=5")
        d = dfhack(sock, "enanos", "n=1", "detalle=completo", "pensamientos=5")
        if d is None:
            return 1
        e = (d.get("enanos") or [{}])[0]
        for k in ("rasgos", "pensamientos", "relaciones", "preferencias", "habilidades"):
            exige(es_lista(e.get(k)), "'%s' llega como lista" % k)
            if isinstance(e.get(k), dict):
                print("      OJO: '%s' llego como {} en vez de []. Lua no distingue" % k)
                print("      lista vacia de objeto vacio; el servicio debe normalizarlo.")
        pens = e.get("pensamientos") or []
        exige(len(pens) <= 5, "respeta el tope pensamientos=5 (llegaron %d)" % len(pens))
        exige(len(e.get("rasgos") or []) > 0, "trae rasgos")
        exige("lua_us" in d, "reporta lua_us (microsegundos enteros, sin coma decimal)")
        if "lua_us" in d:
            exige(isinstance(d["lua_us"], int), "lua_us es un entero")
            print("    lado Lua: %.1f ms" % (d["lua_us"] / 1000.0))
        print("    %d rasgos, %d pensamientos, %d relaciones, %d preferencias, %d habilidades"
              % (len(e.get("rasgos") or []), len(pens), len(e.get("relaciones") or []),
                 len(e.get("preferencias") or []), len(e.get("habilidades") or [])))
        if e.get("nombre"):
            exige(isinstance(e["nombre"], str) and "|" not in e["nombre"],
                  "el nombre llega como texto limpio: %r" % e["nombre"])

        print("\n[4] df_estado enanos n=10 detalle=completo  (N en una llamada)")
        d = dfhack(sock, "enanos", "n=10", "detalle=completo", "pensamientos=8")
        if d is not None:
            exige(len(d.get("enanos", [])) == 10, "devuelve 10 enanos completos de una vez")
            if "lua_us" in d:
                print("    lado Lua para 10 completos: %.1f ms" % (d["lua_us"] / 1000.0))

        print("\n[5] df_estado anuncio con 3 lineas")
        d = dfhack(sock, "anuncio", "Linea uno del contrato.\\nLinea dos.\\nLinea tres.")
        if d is not None:
            exige(d.get("ok") is True, "ok es true")
            exige(d.get("lineas") == 3, "parte el texto en 3 anuncios (llegaron %s)"
                  % d.get("lineas"))
            print("    >>> MIRA EL LOG DEL JUEGO: deberian verse TRES lineas separadas.")

    finally:
        spike.dfhack_quit(sock)

    print("\n" + "=" * 60)
    if FALLOS:
        print("CONTRATO INCOMPLETO. %d comprobaciones fallaron:" % len(FALLOS))
        for f in FALLOS:
            print("  - " + f)
        return 1
    print("CONTRATO OK. Se puede construir el servicio encima.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
