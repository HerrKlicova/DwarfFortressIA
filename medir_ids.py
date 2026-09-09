# -*- coding: utf-8 -*-
"""
medir_ids.py -- PASO 0 de la fase 3. Instrumento de usar y tirar.

Responde a la pregunta que bloquea todo el sistema de memoria:
¿sobreviven unit_id y hist_figure_id a guardar y recargar la partida?

Si la clave cambia, la memoria se corrompe EN SILENCIO: un enano empieza a
recordar la vida de otro y no hay sintoma hasta que el texto deja de tener
sentido. Por eso se mide de verdad, con guardado y recarga reales.

De paso mide el coste real del sondeo (detalle=sonda frente a completo), que
en el plan original se daba por gratis citando los 2 ms de la fase 1 -- pero
ese numero medía lectura de campos en Lua, no serializacion JSON ni transporte.

  python medir_ids.py            hace las dos fases, con una pausa en medio
  python medir_ids.py --fase a   solo la foto inicial (la guarda en disco)
  python medir_ids.py --fase b   solo la comparacion, contra la foto guardada

Requiere df_estado.lua actualizado en <Dwarf Fortress>/dfhack-config/scripts/
y df_llm.py en esta carpeta.
"""

import json
import os
import sys
import time

import df_llm

FOTO = "ids_fase_a.json"
RES = "mediciones_ids.txt"


def log(s=""):
    print(s)
    with open(RES, "a", encoding="utf-8") as f:
        f.write(s + "\n")


def titulo(s):
    log("")
    log("=" * 68)
    log(s)
    log("=" * 68)


def crudo(df, *args):
    """Devuelve (json_parseado, bytes_de_la_linea, segundos_ida_y_vuelta)."""
    t0 = time.perf_counter()
    salida, err = df.comando("df_estado", list(args))
    dt = time.perf_counter() - t0
    if err is not None:
        log("  DFHack devolvio el codigo %d" % err)
        for linea in salida.splitlines():
            log("    | " + linea)
        if "stack traceback" in salida or ".lua:" in salida:
            log("  -> error DENTRO de df_estado.lua; la linea del traceback dice donde")
        elif not salida.strip():
            log("  -> sin salida: ¿copiaste el df_estado.lua nuevo a dfhack-config/scripts/?")
        return None, 0, dt
    for linea in salida.splitlines():
        if linea.startswith("JSON|"):
            cru = linea[5:]
            try:
                return json.loads(cru), len(cru.encode("utf-8")), dt
            except json.JSONDecodeError as e:
                log("  el JSON no parsea: %s" % e)
                log("  crudo: %s" % cru[:300])
                return None, len(cru), dt
    log("  no vino ninguna linea JSON|")
    return None, 0, dt


def foto(df):
    """Sonda de TODOS los ciudadanos (adultos=0), que es lo que hay que seguir."""
    d, tam, dt = crudo(df, "enanos", "n=0", "detalle=sonda", "adultos=0")
    if d is None:
        return None
    log("  %d ciudadanos, %d bytes de JSON, %.3f s de ida y vuelta"
        % (len(d.get("enanos", [])), tam, dt))
    return d


def huella(e):
    """Lo que identifica a un enano y no cambia nunca."""
    return (e.get("nombre"), e.get("nac_a"), e.get("nac_t"), e.get("hfid"))


def fase_a(df):
    titulo("FASE A · foto antes de guardar")
    est, _, _ = crudo(df, "estado")
    if est:
        log("  unit_next_id = %s   (si es un contador monotono, los ids no se reciclan)"
            % est.get("unit_next_id"))
        log("  frame = %s   ciudadanos = %s" % (est.get("frame"), est.get("n_ciudadanos")))
    d = foto(df)
    if d is None:
        return False
    d["unit_next_id"] = (est or {}).get("unit_next_id", -1)
    with open(FOTO, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    enanos = d["enanos"]
    ids = sorted(e["id"] for e in enanos)
    sin_hfid = sum(1 for e in enanos if e.get("hfid", -1) < 0)
    log("  rango de unit_id: %d .. %d   (huecos: %d)"
        % (ids[0], ids[-1], ids[-1] - ids[0] + 1 - len(ids)))
    log("  ciudadanos sin hist_figure_id (vale -1): %d de %d" % (sin_hfid, len(enanos)))
    log("  foto guardada en %s" % FOTO)
    return True


def fase_b(df):
    titulo("FASE B · comparacion despues de recargar")
    if not os.path.exists(FOTO):
        log("  no existe %s. Ejecuta antes: python medir_ids.py --fase a" % FOTO)
        return False
    with open(FOTO, encoding="utf-8") as f:
        antes = json.load(f)

    est, _, _ = crudo(df, "estado")
    if est:
        log("  unit_next_id antes = %s   ahora = %s"
            % (antes.get("unit_next_id"), est.get("unit_next_id")))
    d = foto(df)
    if d is None:
        return False

    a = {e["id"]: e for e in antes["enanos"]}
    b = {e["id"]: e for e in d["enanos"]}

    log("")
    log("  --- unit_id como clave ---")
    comunes = set(a) & set(b)
    iguales = [i for i in comunes if huella(a[i]) == huella(b[i])]
    distintos = [i for i in comunes if huella(a[i]) != huella(b[i])]
    log("  ids presentes antes: %d   ahora: %d   en ambos: %d" % (len(a), len(b), len(comunes)))
    log("  ids que siguen apuntando AL MISMO enano : %d" % len(iguales))
    log("  ids que apuntan a OTRO enano            : %d   <-- si no es 0, unit_id NO vale"
        % len(distintos))
    log("  ids desaparecidos: %d   ids nuevos: %d" % (len(set(a) - set(b)), len(set(b) - set(a))))
    for i in distintos[:5]:
        log("    id %s:  antes %r   ahora %r" % (i, a[i].get("nombre"), b[i].get("nombre")))

    log("")
    log("  --- hist_figure_id como clave ---")
    ah = {e["hfid"]: e for e in antes["enanos"] if e.get("hfid", -1) >= 0}
    bh = {e["hfid"]: e for e in d["enanos"] if e.get("hfid", -1) >= 0}
    ch = set(ah) & set(bh)
    ig_h = [i for i in ch if huella(ah[i]) == huella(bh[i])]
    di_h = [i for i in ch if huella(ah[i]) != huella(bh[i])]
    log("  con hist_figure_id valido: %d de %d antes, %d de %d ahora"
        % (len(ah), len(antes["enanos"]), len(bh), len(d["enanos"])))
    log("  hfid que siguen apuntando AL MISMO enano: %d" % len(ig_h))
    log("  hfid que apuntan a OTRO enano           : %d" % len(di_h))

    log("")
    if not distintos and len(iguales) == len(comunes) and comunes:
        log("  VEREDICTO: unit_id sobrevive a guardar y recargar. Sirve como clave.")
    elif not di_h and ig_h:
        log("  VEREDICTO: unit_id NO es fiable, pero hist_figure_id si. Usar hfid.")
    else:
        log("  VEREDICTO: ninguna de las dos claves aguanta. PARAR y replantear.")
    return True


def coste_sondeo(df):
    titulo("COSTE REAL DEL SONDEO · sonda frente a completo")
    log("  (los 2 ms de la fase 1 median lectura de campos, no esto)")
    log("")
    for etiqueta, args in (
            ("sonda    todos", ("enanos", "n=0", "detalle=sonda", "adultos=0")),
            ("basico   todos", ("enanos", "n=0", "detalle=basico", "adultos=0")),
            ("completo todos", ("enanos", "n=0", "detalle=completo", "pensamientos=8", "adultos=0")),
    ):
        mejores = []
        for _ in range(3):
            d, tam, dt = crudo(df, *args)
            if d is None:
                break
            mejores.append((tam, dt, d.get("lua_us", 0), len(d.get("enanos", []))))
        if not mejores:
            continue
        tam = mejores[0][0]
        dt = min(m[1] for m in mejores)
        lua = min(m[2] for m in mejores)
        n = mejores[0][3]
        log("  %-16s %2d enanos  %7d bytes  Lua %6.2f ms  ida y vuelta %6.1f ms"
            % (etiqueta, n, tam, lua / 1000.0, dt * 1000))

    log("")
    log("  Con un sondeo cada 5 s, el trafico por minuto seria 12 veces la cifra de arriba.")


def main(argv):
    fase = None
    if "--fase" in argv:
        i = argv.index("--fase")
        fase = argv[i + 1] if i + 1 < len(argv) else None

    with open(RES, "a", encoding="utf-8") as f:
        f.write("\n\n" + "#" * 68 + "\n")
        f.write("# PASO 0 FASE 3 -- %s\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
        f.write("#" * 68 + "\n")

    df = df_llm.DFHack()
    try:
        df.conectar()
    except df_llm.SinDFHack as e:
        print("[DFHack] %s" % e)
        return 1
    log("Conectado a DFHack 127.0.0.1:%d" % df.port)

    try:
        if fase == "a":
            fase_a(df)
        elif fase == "b":
            fase_b(df)
            coste_sondeo(df)
        else:
            if not fase_a(df):
                return 1
            coste_sondeo(df)
            print()
            print("  >>> GUARDA la partida y VUELVE A CARGARLA (menu principal y")
            print("  >>> cargar otra vez la misma fortaleza). No cierres DFHack.")
            input("  >>> Pulsa Enter cuando este cargada de nuevo...")
            fase_b(df)
    except df_llm.SinPartida as e:
        log("\n[Partida] %s" % e)
        return 1
    except df_llm.SinDFHack as e:
        log("\n[DFHack] %s" % e)
        return 1
    finally:
        df.cerrar()

    titulo("FIN. Resultados en %s -- pegaselos a Claude." % RES)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
