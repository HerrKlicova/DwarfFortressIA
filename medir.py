# -*- coding: utf-8 -*-
"""
medir.py -- FASE 1: cerrar las incognitas abiertas. Instrumento de usar y tirar.

No simula nada: todo se mide contra el juego real. Reutiliza el cliente de
protocolo de spike.py en vez de duplicarlo.

ANTES DE EJECUTAR
  1. Copiar dfhack_medir.lua a  <Dwarf Fortress>/dfhack-config/scripts/
  2. spike.py tiene que estar en esta misma carpeta (se importa).
  3. Dwarf Fortress abierto, fortaleza cargada y CORRIENDO (sin pausa).
  4. Player2 abierto con sesion iniciada.
  5. GUARDA LA PARTIDA. El ultimo experimento descarga el mundo y cierra DF.

     python medir.py            <- los seis, en orden
     python medir.py 4 5 6 3    <- solo esos, para retomar sin repetir

Los resultados se escriben en mediciones.txt SEGUN SE OBTIENEN, no al final:
si DF se cae en el experimento 3 no perdemos los cinco anteriores.

ORDEN: del mas seguro al mas destructivo. El 3 va el ultimo a proposito.
"""

import json
import re
import sys
import statistics
import time
import urllib.error
import urllib.request

import spike

RES = "mediciones.txt"


def log(s=""):
    print(s)
    with open(RES, "a", encoding="utf-8") as f:
        f.write(s + "\n")


def titulo(s):
    log("")
    log("=" * 70)
    log(s)
    log("=" * 70)


def pausa(msg):
    print()
    print("  >>> " + msg)
    input("  >>> Pulsa Enter cuando lo hayas hecho...")


def cmd(sock, *args):
    """Ejecuta dfhack_medir y devuelve (lineas, error_o_None, segundos)."""
    t0 = time.perf_counter()
    salida, err = spike.dfhack_run_command(sock, "dfhack_medir", list(args))
    dt = time.perf_counter() - t0
    return salida.splitlines(), err, dt


def cmd_seguro(sock, *args):
    """Como cmd(), pero si el socket muere lo registra en vez de reventar.
    Necesario en E3: la gracia del experimento es justo que DF puede caerse."""
    try:
        return cmd(sock, *args), None
    except Exception as e:
        return ([], None, 0.0), "%s: %s" % (type(e).__name__, e)


def parsea(lineas):
    """Convierte lineas CLAVE|... en un dict de listas."""
    d = {}
    for ln in lineas:
        if "|" not in ln:
            continue
        k, resto = ln.split("|", 1)
        d.setdefault(k, []).append(resto)
    return d


def uno(d, k, por_defecto="?"):
    return d.get(k, [por_defecto])[0]


def num(x, por_defecto=0.0):
    """Tolera coma decimal. El string.format de Lua pasa por printf de C, que
    respeta el locale: en un Windows en espanol devuelve "0,0020"."""
    try:
        return float(str(x).replace(",", "."))
    except (TypeError, ValueError):
        return por_defecto


def lua_seg(d):
    """Segundos consumidos por el lado Lua. LUA_US son microsegundos enteros,
    que printf no toca; LUA_SEG es el formato viejo, por si queda alguno."""
    if "LUA_US" in d:
        return num(uno(d, "LUA_US", "0")) / 1000000.0
    return num(uno(d, "LUA_SEG", "0"))


def resumen(nombre, muestras):
    log("  %-22s n=%d  mediana=%.3f s  min=%.3f  max=%.3f"
        % (nombre, len(muestras), statistics.median(muestras), min(muestras), max(muestras)))


# ====================================================================== E1
def e1_latencia(sock):
    titulo("E1 · Latencia: prompt largo con contexto real vs prompt minimo")

    lineas, err, _ = cmd(sock, "contexto", "0")
    if err is not None:
        log("  ABORTADO: DFHack devolvio error %d" % err)
        for l in lineas:
            log("    | " + l)
        return
    d = parsea(lineas)

    nombre = uno(d, "NOMBRE")
    prof = uno(d, "PROFESION")
    edad = uno(d, "EDAD")

    log("  Enano: %s, %s, %s anos" % (nombre, prof, edad))
    log("  Recogido: %d rasgos, %d pensamientos, %d relaciones, %d preferencias, %d habilidades"
        % (len(d.get("RASGO", [])), len(d.get("PENSAMIENTO", [])), len(d.get("RELACION", [])),
           len(d.get("PREFERENCIA", [])), len(d.get("HABILIDAD", []))))
    log("  Tiempo del lado Lua para este volcado: %.4f s" % lua_seg(d))

    corto = ("Eres %s, %s, de %s anos, en una fortaleza enana. "
             "Di UNA sola frase en primera persona, en espanol. "
             "Maximo 15 palabras. Sin comillas." % (nombre, prof, edad))

    partes = ["Eres %s, %s, de %s anos, en una fortaleza enana." % (nombre, prof, edad),
              "Tu nivel de estres es %s (negativo = tranquilo)." % uno(d, "ESTRES")]
    if d.get("RASGO"):
        partes.append("Tus rasgos de personalidad, con su valor: "
                      + "; ".join(r.replace("|", " ") for r in d["RASGO"]) + ".")
    if d.get("PENSAMIENTO"):
        partes.append("Tus emociones recientes: "
                      + "; ".join(p.replace("|", " por ", 1).replace("|", " intensidad ")
                                  for p in d["PENSAMIENTO"]) + ".")
    if d.get("RELACION"):
        partes.append("Tus relaciones: " + "; ".join(r.replace("|", ": ") for r in d["RELACION"]) + ".")
    if d.get("PREFERENCIA"):
        partes.append("Tus preferencias: " + ", ".join(d["PREFERENCIA"]) + ".")
    if d.get("HABILIDAD"):
        partes.append("Tus habilidades: " + "; ".join(h.replace("|", " nivel ") for h in d["HABILIDAD"]) + ".")
    partes.append("Habla de ti en primera persona, en espanol, en DOS O TRES frases "
                  "coherentes con todo lo anterior. Sin comillas.")
    largo = "\n".join(partes)

    log("  Prompt minimo: %d caracteres" % len(corto))
    log("  Prompt largo:  %d caracteres" % len(largo))

    port = spike.player2_port()
    cortos, largos, resp_c, resp_l = [], [], [], []

    # Intercalados, para que una posible deriva del modelo afecte igual a los dos.
    for i in range(5):
        for etiqueta, prompt, tiempos, respuestas in (
                ("minimo", corto, cortos, resp_c), ("largo", largo, largos, resp_l)):
            t0 = time.perf_counter()
            try:
                r = spike.player2_call(port, "POST", "/v1/chat/completions",
                                       {"messages": [{"role": "user", "content": prompt}],
                                        "stream": False})
                dt = time.perf_counter() - t0
                txt = r["choices"][0]["message"]["content"].strip()
            except (urllib.error.HTTPError, urllib.error.URLError, KeyError, IndexError) as e:
                log("  vuelta %d %s: FALLO %s" % (i + 1, etiqueta, e))
                continue
            tiempos.append(dt)
            respuestas.append(txt)
            log("  vuelta %d %-6s  %.3f s  respuesta %d car." % (i + 1, etiqueta, dt, len(txt)))

    log("")
    if cortos:
        resumen("prompt minimo", cortos)
    if largos:
        resumen("prompt largo", largos)
    if cortos and largos:
        log("  Diferencia de medianas: %+.3f s (%.1fx)"
            % (statistics.median(largos) - statistics.median(cortos),
               statistics.median(largos) / statistics.median(cortos)))
    if resp_l:
        log("")
        log("  Ejemplo de respuesta larga:")
        log("    " + resp_l[-1].replace("\n", " "))


# ====================================================================== E2
def e2_pausa(sock):
    titulo("E2 · Comportamiento con Dwarf Fortress en pausa")

    lineas, err, dt = cmd(sock, "estado")
    d = parsea(lineas)
    log("  Antes de pausar: PAUSA=%s FRAME=%s (ida y vuelta %.3f s)"
        % (uno(d, "PAUSA"), uno(d, "FRAME"), dt))

    pausa("PAUSA el juego (barra espaciadora) y deja la ventana como esta.")

    lineas, err, dt = cmd(sock, "estado")
    d = parsea(lineas)
    log("  Con pausa: PAUSA=%s FRAME=%s (ida y vuelta %.3f s) error=%s"
        % (uno(d, "PAUSA"), uno(d, "FRAME"), dt, err))
    f1 = uno(d, "FRAME")

    lineas, err, dt = cmd(sock, "contexto", "0")
    d2 = parsea(lineas)
    log("  Lectura completa en pausa: error=%s  ida y vuelta %.3f s  lado Lua %.4f s"
        % (err, dt, lua_seg(d2)))
    log("  Enano leido en pausa: %s" % uno(d2, "NOMBRE"))

    lineas, err, dt = cmd(sock, "anuncio", "Prueba de anuncio con el juego en pausa.")
    log("  Anuncio en pausa: %s (error=%s, ida y vuelta %.3f s)"
        % (" ".join(lineas) if lineas else "sin salida", err, dt))

    time.sleep(2)
    lineas, _, _ = cmd(sock, "estado")
    f2 = uno(parsea(lineas), "FRAME")
    log("  FRAME tras 2 s de pausa: %s -> %s (si no cambia, el juego esta parado de verdad)" % (f1, f2))

    pausa("QUITA la pausa (barra espaciadora otra vez).")


# ====================================================================== E4
def e4_bloqueo(sock):
    titulo("E4 · Cuanto bloquea el juego el lado Lua")

    pausa("Asegurate de que el juego esta CORRIENDO, sin pausa.")

    # FPS en reposo: dos lecturas separadas en el tiempo, sin carga entre medias.
    l1, _, _ = cmd(sock, "estado"); t1 = time.perf_counter()
    time.sleep(4)
    l2, _, _ = cmd(sock, "estado"); t2 = time.perf_counter()
    f1 = int(uno(parsea(l1), "FRAME", "-1"))
    f2 = int(uno(parsea(l2), "FRAME", "-1"))
    if f1 < 0 or f2 < 0 or f2 == f1:
        log("  No se pudo medir FPS en reposo (FRAME %s -> %s). ¿El juego esta en pausa?" % (f1, f2))
        fps_reposo = None
    else:
        fps_reposo = (f2 - f1) / (t2 - t1)
        log("  FPS en reposo: %.1f  (%d frames en %.2f s)" % (fps_reposo, f2 - f1, t2 - t1))

    # Coste puro del lado Lua, sin red: bench de N enanos en UNA sola llamada.
    log("")
    for n in (1, 5, 20):
        lineas, err, dt = cmd(sock, "bench", str(n))
        d = parsea(lineas)
        if err is not None:
            log("  bench %d: error %d" % (n, err))
            continue
        lua = lua_seg(d)
        enanos = int(uno(d, "ENANOS", "0"))
        campos = int(uno(d, "CAMPOS", "0"))
        log("  bench %-2d enanos: lado Lua %.4f s (%.4f s/enano, %d campos) | ida y vuelta total %.3f s"
            % (n, lua, lua / max(enanos, 1), campos, dt))

    # FPS durante una rafaga de llamadas: esto es lo que nota el jugador.
    log("")
    l3, _, _ = cmd(sock, "estado"); t3 = time.perf_counter()
    f3 = int(uno(parsea(l3), "FRAME", "-1"))
    vueltas = []
    for _ in range(20):
        _, err, dt = cmd(sock, "contexto", "0")
        vueltas.append(dt)
    l4, _, _ = cmd(sock, "estado"); t4 = time.perf_counter()
    f4 = int(uno(parsea(l4), "FRAME", "-1"))

    log("  20 lecturas completas seguidas: mediana %.3f s, min %.3f, max %.3f, total %.2f s"
        % (statistics.median(vueltas), min(vueltas), max(vueltas), t4 - t3))
    if f3 >= 0 and f4 > f3:
        fps_carga = (f4 - f3) / (t4 - t3)
        log("  FPS durante la rafaga: %.1f  (%d frames en %.2f s)" % (fps_carga, f4 - f3, t4 - t3))
        if fps_reposo:
            log("  IMPACTO: %.1f%% de los FPS en reposo" % (100.0 * fps_carga / fps_reposo))
    else:
        log("  FRAME no avanzo durante la rafaga (%s -> %s): el juego quedo bloqueado del todo." % (f3, f4))

    # Varios enanos distintos en la misma ejecucion.
    log("")
    nombres = []
    for i in range(6):
        lineas, err, _ = cmd(sock, "contexto", str(i * 7))
        nombres.append(uno(parsea(lineas), "NOMBRE"))
    log("  6 lecturas con indices distintos: %s" % ", ".join(nombres))
    log("  Enanos distintos obtenidos: %d de 6" % len(set(nombres)))


# ====================================================================== E5
def e5_anuncios(sock):
    titulo("E5 · showAnnouncement con texto largo y multilinea")

    base = ("Urist se queja del trabajo en la cantera y de la falta de cerveza. ")
    for n in (100, 300, 800, 2000):
        texto = (base * 40)[:n]
        lineas, err, dt = cmd(sock, "anuncio", texto)
        log("  %4d caracteres: %s (error=%s, ida y vuelta %.3f s)"
            % (n, " ".join(lineas) if lineas else "sin salida", err, dt))

    multi = "Primera linea del anuncio.\\nSegunda linea.\\nTercera linea."
    lineas, err, dt = cmd(sock, "anuncio", multi)
    log("  multilinea (3 lineas con \\n): %s (error=%s)"
        % (" ".join(lineas) if lineas else "sin salida", err))

    log("")
    log("  >>> MIRA AHORA EL LOG DE ANUNCIOS DEL JUEGO Y ANOTA:")
    log("      - ¿aparecen los cuatro de longitud creciente?")
    log("      - ¿se cortan? ¿a cuantos caracteres?")
    log("      - el multilinea, ¿sale como 1 anuncio o como 3?")
    pausa("Mira el log de anuncios en el juego y apunta lo que ves.")
    obs = input("  >>> Describe en una linea lo que has visto: ").strip()
    log("  OBSERVADO POR EL USUARIO: " + (obs or "(sin respuesta)"))


# ====================================================================== E6
def e6_player2():
    titulo("E6 · Player2 real: contrastar contra lo que dice HALLAZGOS.md")

    port = spike.player2_port()
    base = "http://127.0.0.1:%d" % port

    def crudo(metodo, ruta, cuerpo=None):
        url = base + ruta
        data = json.dumps(cuerpo).encode() if cuerpo is not None else None
        cab = {"Content-Type": "application/json"} if cuerpo is not None else {}
        req = urllib.request.Request(url, data=data, headers=cab, method=metodo)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")
        except urllib.error.URLError as e:
            return None, str(e)

    st, cuerpo = crudo("GET", "/v1/health")
    log("  GET /v1/health -> %s %s" % (st, cuerpo[:200]))

    # El HTML de /docs suele nombrar la URL del spec. Lo guardamos y lo miramos.
    st, html = crudo("GET", "/docs")
    log("  GET /docs -> %s (%d bytes)" % (st, len(html or "")))
    candidatos = []
    if html:
        with open("player2_docs.html", "w", encoding="utf-8") as f:
            f.write(html)
        log("  guardado en player2_docs.html")
        candidatos = list(dict.fromkeys(re.findall(r'["\'](/[^"\']*?(?:openapi|api-doc|swagger|api)[^"\']*?\.(?:json|yaml))["\']', html)))
        if candidatos:
            log("  el HTML menciona: %s" % ", ".join(candidatos))

    for ruta in candidatos + ["/openapi.json", "/v1/openapi.json", "/api-docs/openapi.json",
                              "/swagger.json", "/api.yaml", "/v1/api.yaml"]:
        st, cuerpo = crudo("GET", ruta)
        if st == 200 and cuerpo and len(cuerpo) > 500:
            nombre = "player2_openapi" + (".yaml" if ruta.endswith("yaml") else ".json")
            with open(nombre, "w", encoding="utf-8") as f:
                f.write(cuerpo)
            log("  ESPEC ENCONTRADO en %s -> %s (%d bytes)" % (ruta, nombre, len(cuerpo)))
            try:
                spec = json.loads(cuerpo)
                log("  rutas del spec: %d" % len(spec.get("paths", {})))
                cc = spec.get("paths", {}).get("/chat/completions", {}).get("post", {})
                log("  codigos de /chat/completions: %s" % sorted(cc.get("responses", {}).keys()))
            except (json.JSONDecodeError, AttributeError):
                log("  (no es JSON parseable, revisalo a mano)")
            break
        log("  GET %s -> %s" % (ruta, st))

    log("")
    log("  Codigos de error REALES (esto es lo que HALLAZGOS.md tiene sin verificar):")
    for etiqueta, metodo, ruta, cuerpo_env in (
            ("ruta inexistente", "GET", "/v1/no-existe-esto", None),
            ("cuerpo vacio", "POST", "/v1/chat/completions", {}),
            ("messages vacio", "POST", "/v1/chat/completions", {"messages": []}),
            ("rol invalido", "POST", "/v1/chat/completions",
             {"messages": [{"role": "marciano", "content": "hola"}]}),
    ):
        st, resp = crudo(metodo, ruta, cuerpo_env)
        log("  %-18s %s %-24s -> %s  %s" % (etiqueta, metodo, ruta, st, (resp or "")[:160].replace("\n", " ")))


# ====================================================================== E3
def e3_descarga(sock):
    titulo("E3 · Partida descargada y DF cerrado con el socket abierto")
    log("  (el socket se mantiene ABIERTO durante todo el experimento)")

    pausa("GUARDA la partida y vuelve al MENU PRINCIPAL. No cierres Dwarf Fortress.")

    (lineas, err, dt), muerto = cmd_seguro(sock, "estado")
    if muerto:
        log("  a) LA CONEXION MURIO al volver al menu principal: %s" % muerto)
        log("     Esto es un hallazgo importante: descargar el mundo tumba el socket.")
        return
    d = parsea(lineas)
    log("  a) En el menu principal:")
    log("     estado -> MUNDO=%s MAPA=%s CIUDADANOS=%s (error=%s, %.3f s)"
        % (uno(d, "MUNDO"), uno(d, "MAPA"), uno(d, "CIUDADANOS"), err, dt))

    lineas, err, dt = cmd(sock, "contexto", "0")
    log("     contexto -> %s (error=%s, %.3f s)"
        % (" ".join(lineas) if lineas else "sin salida", err, dt))
    log("     ¿fallo limpio en vez de crasheo? %s" % ("SI" if lineas or err is not None else "REVISAR"))

    lineas, err, dt = cmd(sock, "anuncio", "Anuncio sin partida cargada.")
    log("     anuncio -> %s (error=%s)" % (" ".join(lineas) if lineas else "sin salida", err))

    pausa("VUELVE A CARGAR la misma partida. Espera a que termine de cargar.")

    (lineas, err, dt), muerto = cmd_seguro(sock, "estado")
    if muerto:
        log("  b) LA CONEXION MURIO tras recargar la partida: %s" % muerto)
        log("     Hallazgo: el socket no sobrevive a un ciclo descarga/carga.")
        return
    d = parsea(lineas)
    log("  b) Tras recargar, POR EL MISMO SOCKET:")
    log("     estado -> MUNDO=%s MAPA=%s CIUDADANOS=%s (error=%s, %.3f s)"
        % (uno(d, "MUNDO"), uno(d, "MAPA"), uno(d, "CIUDADANOS"), err, dt))
    lineas, err, _ = cmd(sock, "contexto", "0")
    log("     contexto -> %s (error=%s)" % (uno(parsea(lineas), "NOMBRE"), err))
    log("     ¿sobrevive la conexion a un ciclo descarga/carga? %s"
        % ("SI" if err is None else "NO"))

    pausa("Ahora CIERRA Dwarf Fortress del todo (Alt+F4 o salir por el menu).")

    log("  c) Con DF cerrado:")
    try:
        lineas, err, dt = cmd(sock, "estado")
        log("     estado -> %s (error=%s, %.3f s)" % (lineas, err, dt))
        log("     El socket seguia respondiendo, cosa rara. Revisar.")
    except Exception as e:
        log("     Excepcion en Python: %s: %s" % (type(e).__name__, e))
        log("     Es lo esperado: el servicio real tiene que capturar esto y reconectar.")


# ====================================================================== main
EXPERIMENTOS = {}      # se rellena abajo, cuando ya existen las funciones
ORDEN = ["1", "2", "4", "5", "6", "3"]   # del mas seguro al mas destructivo


def main():
    global sel
    sel = [a for a in sys.argv[1:] if a in ORDEN] or list(ORDEN)
    print("Experimentos a ejecutar: %s" % " ".join(sel))

    with open(RES, "a", encoding="utf-8") as f:
        f.write("\n\n" + "#" * 70 + "\n")
        f.write("# MEDICIONES FASE 1 -- %s\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
        f.write("#" * 70 + "\n")

    try:
        sock = spike.dfhack_connect()
    except OSError as e:
        print("FALLO conectando a DFHack: %s" % e)
        print("¿Esta abierto Dwarf Fortress con DFHack?")
        return 1
    log("Conectado a DFHack 127.0.0.1:%d" % spike.DFHACK_PORT)

    lineas, err, _ = cmd(sock, "estado")
    if err is not None:
        log("FALLO: dfhack_medir no responde (error %d)." % err)
        for l in lineas:
            log("  | " + l)
        log("¿Copiaste dfhack_medir.lua a dfhack-config/scripts/ ?")
        spike.dfhack_quit(sock)
        return 1

    try:
        for clave in sel:
            if clave == "6":
                e6_player2()
            else:
                EXPERIMENTOS[clave](sock)
    finally:
        try:
            spike.dfhack_quit(sock)
        except Exception:
            pass

    titulo("FIN. Resultados en %s -- pegaselos a Claude." % RES)
    return 0


EXPERIMENTOS = {"1": e1_latencia, "2": e2_pausa, "3": e3_descarga,
                "4": e4_bloqueo, "5": e5_anuncios}

if __name__ == "__main__":
    raise SystemExit(main())
