# -*- coding: utf-8 -*-
"""
df_llm.py -- servicio del puente Dwarf Fortress <-> LLM local.

Lee estado del juego por la interfaz remota de DFHack, se lo da a un LLM que
corre en la misma maquina (Player2) y devuelve la respuesta al log de anuncios.

  python df_llm.py estado          estado del juego, sin tocar el LLM
  python df_llm.py listar [n]      n enanos, resumen por linea
  python df_llm.py hablar [n]      elige un enano al azar, le hace hablar y lo anuncia
  python df_llm.py hablar id=272   hace hablar a ESE enano (peticion explicita)
  python df_llm.py hablar id=272 --prompt --seco
                                   imprime el prompt ENTERO y no toca el juego
  python df_llm.py hablar id=272 --epitafio
                                   lo que se anunciaria si acabara de morir
  python df_llm.py medir [n]       n llamadas al LLM con el mismo prompt: mediana y rango
  python df_llm.py enums           que enums traen texto legible de DF
  python df_llm.py ui              que ofrece la interfaz de DFHack en esta build

Requiere df_estado.lua en <Dwarf Fortress>/dfhack-config/scripts/.
No tiene dependencias: solo biblioteca estandar.

Las reglas del proyecto estan en CLAUDE.md. Este fichero es autonomo a
proposito: no importa spike.py, que es codigo de usar y tirar.
"""

import json
import os
import re
import random
import socket
import struct
import sys
import time
import urllib.error
import urllib.request

# ---------------------------------------------------------------- errores
# Los tres puntos de fallo reales, separados para poder decir algo util en
# cada uno en vez de un "no funciona" generico.


class SinDFHack(Exception):
    """Dwarf Fortress no esta escuchando, o la conexion se cayo."""


class SinPartida(Exception):
    """DFHack responde pero no hay fortaleza cargada."""


class SinPlayer2(Exception):
    """La app de Player2 no responde, no hay sesion, o rechaza la peticion."""


# ------------------------------------------------- protocolo DFHack
# Protobuf codificado a mano: solo hacen falta dos mensajes y RunCommand
# tiene ID fijo 1, asi que no hay que generar codigo ni instalar nada.
# Especificacion: dfhack/docs/dev/Remote.rst

RPC_REPLY_RESULT = -1
RPC_REPLY_FAIL = -2
RPC_REPLY_TEXT = -3
RPC_REQUEST_QUIT = -4
RPC_RUN_COMMAND = 1


def _varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _leer_varint(buf, i):
    desp, val = 0, 0
    while True:
        b = buf[i]
        i += 1
        val |= (b & 0x7F) << desp
        if not (b & 0x80):
            return val, i
        desp += 7


def _campo_str(num, s):
    datos = s.encode("utf-8")
    return bytes([(num << 3) | 2]) + _varint(len(datos)) + datos


def _recorrer(buf):
    i, n = 0, len(buf)
    while i < n:
        clave, i = _leer_varint(buf, i)
        num, wt = clave >> 3, clave & 7
        if wt == 0:
            val, i = _leer_varint(buf, i)
            yield num, wt, val
        elif wt == 2:
            ln, i = _leer_varint(buf, i)
            yield num, wt, buf[i:i + ln]
            i += ln
        elif wt == 5:
            yield num, wt, buf[i:i + 4]; i += 4
        elif wt == 1:
            yield num, wt, buf[i:i + 8]; i += 8
        else:
            raise ValueError("wire type %d no soportado" % wt)


def _fragmentos_texto(buf):
    """CoreTextNotification { repeated CoreTextFragment fragments = 1 }"""
    trozos = []
    for num, wt, val in _recorrer(buf):
        if num == 1 and wt == 2:
            for n2, w2, v2 in _recorrer(val):
                if n2 == 1 and w2 == 2:
                    trozos.append(v2.decode("utf-8", "replace"))
    return trozos


class DFHack(object):
    """Conexion con la interfaz remota. Se reconecta sola una vez si el juego
    se cerro y volvio a abrirse: cerrar DF da ConnectionResetError."""

    def __init__(self, host="127.0.0.1", port=None):
        self.host = host
        self.port = int(port or os.environ.get("DFHACK_PORT", "5000"))
        self.sock = None

    # -- conexion ---------------------------------------------------
    def conectar(self):
        try:
            s = socket.create_connection((self.host, self.port), timeout=20)
        except OSError as e:
            raise SinDFHack(
                "no hay nadie escuchando en %s:%d (%s).\n"
                "  Comprueba que Dwarf Fortress esta abierto con DFHack, y que\n"
                "  stderr.log dice 'Listening on port %d'."
                % (self.host, self.port, e, self.port))
        s.sendall(b"DFHack?\n" + struct.pack("<i", 1))
        respuesta = self._exacto(s, 12)
        if respuesta != b"DFHack!\n" + struct.pack("<i", 1):
            s.close()
            raise SinDFHack("el handshake fallo; respondio %r" % respuesta)
        self.sock = s
        return self

    def cerrar(self):
        if not self.sock:
            return
        try:
            self._enviar(RPC_REQUEST_QUIT, b"")
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass
        self.sock = None

    def __enter__(self):
        return self.conectar()

    def __exit__(self, *_):
        self.cerrar()

    # -- transporte -------------------------------------------------
    @staticmethod
    def _exacto(sock, n):
        buf = b""
        while len(buf) < n:
            trozo = sock.recv(n - len(buf))
            if not trozo:
                raise SinDFHack("DFHack cerro la conexion a mitad de un mensaje")
            buf += trozo
        return buf

    def _enviar(self, msg_id, carga):
        # cabecera: int16 id, int16 relleno, int32 tamano, little-endian
        self.sock.sendall(struct.pack("<hhI", msg_id, 0, len(carga)) + carga)

    def _cabecera(self):
        h = self._exacto(self.sock, 8)
        return (struct.unpack("<h", h[0:2])[0],
                struct.unpack("<I", h[4:8])[0],
                struct.unpack("<i", h[4:8])[0])  # en FAIL, el tamano es el codigo

    def comando(self, cmd, args, _reintento=True):
        """Devuelve (texto, codigo_error_o_None). Reconecta una vez si el
        socket murio, que es lo que pasa al cerrar y reabrir el juego."""
        if self.sock is None:
            self.conectar()
        carga = _campo_str(1, cmd) + b"".join(_campo_str(2, a) for a in args)
        try:
            self._enviar(RPC_RUN_COMMAND, carga)
            trozos = []
            while True:
                msg_id, tam, codigo = self._cabecera()
                if msg_id == RPC_REPLY_TEXT:
                    trozos.extend(_fragmentos_texto(self._exacto(self.sock, tam)))
                elif msg_id == RPC_REPLY_RESULT:
                    if tam:
                        self._exacto(self.sock, tam)
                    return self._unir(trozos), None
                elif msg_id == RPC_REPLY_FAIL:
                    return self._unir(trozos), codigo
                else:
                    raise SinDFHack("cabecera inesperada de DFHack: id=%d" % msg_id)
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, OSError):
            self.sock = None
            if _reintento:
                return self.comando(cmd, args, _reintento=False)
            raise SinDFHack(
                "se perdio la conexion y no se pudo restablecer.\n"
                "  Es lo que pasa si Dwarf Fortress se cerro. Vuelve a abrirlo.")

    @staticmethod
    def _unir(trozos):
        crudo = "".join(trozos)
        if "\n" not in crudo and len(trozos) > 1:
            crudo = "\n".join(trozos)
        return crudo

    # -- contrato con df_estado.lua ---------------------------------
    def _json(self, *args):
        salida, err = self.comando("df_estado", list(args))

        if err is not None:
            # INVARIANTE 4: primero lo que dijo el motor, y solo se sugiere una
            # causa cuando NO hay nada que leer.
            detalle = "\n".join("  | " + l for l in salida.splitlines())
            if "stack traceback" in salida or ".lua:" in salida:
                pista = "  -> error DENTRO de df_estado.lua; la linea del traceback dice donde"
            elif not salida.strip():
                pista = ("  -> sin ninguna salida: comprueba que df_estado.lua esta en\n"
                         "     <Dwarf Fortress>/dfhack-config/scripts/")
            else:
                pista = ""
            raise SinDFHack("DFHack devolvio el codigo %d\n%s\n%s"
                            % (err, detalle, pista))

        cruda = None
        for linea in salida.splitlines():
            if linea.startswith("JSON|"):
                cruda = linea[5:]
        if cruda is None:
            raise SinDFHack("df_estado no devolvio ninguna linea JSON|. Dijo:\n"
                            + "\n".join("  | " + l for l in salida.splitlines()))
        try:
            d = json.loads(cruda)
        except json.JSONDecodeError as e:
            raise SinDFHack("el JSON de df_estado no parsea (%s)\n  crudo: %s"
                            % (e, cruda[:300]))

        if not d.get("ok"):
            raise SinPartida(d.get("error", "df_estado devolvio ok=false sin motivo"))
        return d

    def estado(self):
        return self._json("estado")

    def sonda(self):
        """Sondeo barato de TODOS los ciudadanos: lo minimo para detectar
        cambios. Medido: 15 KB y 5 ms frente a los 344 KB y 40 ms de completo."""
        return self._json("enanos", "n=0", "detalle=sonda", "adultos=0")

    def enanos(self, n=5, detalle="basico", pensamientos=8, desde=0, adultos=True):
        d = self._json("enanos", "n=%d" % n, "desde=%d" % desde,
                       "detalle=%s" % detalle, "pensamientos=%d" % pensamientos,
                       "adultos=%d" % (1 if adultos else 0))
        for e in d.get("enanos", []):
            _normalizar(e)
        return d

    def uno(self, id_, detalle="completo", pensamientos=None):
        """El expediente de UN enano, por id. Devuelve None si no existe.

        Una peticion, un enano. Antes esto se hacia paginando por getCitizens()
        de 20 en 20, y para los sucesos de mas peso -- muerte, locura,
        desaparicion -- no podia funcionar nunca: esos se detectan justamente
        porque el enano ya NO esta en getCitizens(). Eran 11 peticiones y
        ~1,1 MB de Lua para devolver None, y el epitafio acababa construyendose
        con los datos de la sonda, que no traen ni oficio, ni edad, ni
        relaciones, ni habilidades."""
        # TOPE_PENSAMIENTOS se define mas abajo en el fichero: no puede ser el
        # valor por defecto del argumento, que se evalua al importar.
        d = self._json("enanos", "id=%d" % int(id_), "detalle=%s" % detalle,
                       "pensamientos=%d" % (TOPE_PENSAMIENTOS if pensamientos is None
                                            else pensamientos))
        lista = d.get("enanos") or []
        return _normalizar(lista[0]) if lista else None

    def unidad(self, id_):
        """Que fue de una unidad que ya no esta entre los ciudadanos."""
        return self._json("unidad", "id=%d" % int(id_))

    def anunciar(self, texto):
        """El lado Lua parte por saltos de linea: showAnnouncement ignora \\n."""
        return self._json("anuncio", texto.replace("\n", "\\n"))


def _normalizar(enano):
    """Lua no distingue una lista vacia de un objeto vacio, asi que un enano
    sin relaciones puede llegar como {} en vez de []. Se corrige aqui.
    El validador no cazo este caso porque el enano de prueba tenia de todo."""
    for clave in ("rasgos", "pensamientos", "relaciones", "preferencias", "habilidades"):
        if clave in enano and not isinstance(enano[clave], list):
            enano[clave] = []
    return enano


# ---------------------------------------------------------------- Player2

class Player2(object):
    """API local. No lleva clave: solo exige que la app este abierta y con
    sesion iniciada."""

    def __init__(self, host="127.0.0.1", port=None):
        self.host = host
        self.port = port or self._descubrir_puerto()
        self.clave_juego = os.environ.get("PLAYER2_GAME_KEY", "")

    @staticmethod
    def _descubrir_puerto():
        """4315 por defecto, pero si estaba ocupado la app escribe el real en
        api.port. Ese fichero solo existe mientras la app corre."""
        appdata = os.environ.get("APPDATA") or os.path.expanduser("~/.config")
        for ruta in (os.path.join(appdata, "game.player2.client", "api.port"),
                     os.path.expanduser("~/Library/Application Support/"
                                        "game.player2.client/api.port")):
            try:
                with open(ruta) as f:
                    return int(f.read().strip())
            except (OSError, ValueError):
                continue
        return 4315

    def _llamar(self, metodo, ruta, cuerpo=None, timeout=120):
        # La doc avisa: 127.0.0.1 y no localhost, por conflictos con IPv6.
        url = "http://%s:%d%s" % (self.host, self.port, ruta)
        datos = json.dumps(cuerpo).encode("utf-8") if cuerpo is not None else None
        cab = {"Content-Type": "application/json"} if cuerpo is not None else {}
        if self.clave_juego:
            cab["player2-game-key"] = self.clave_juego
        req = urllib.request.Request(url, data=datos, headers=cab, method=metodo)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            cuerpo_err = e.read().decode("utf-8", "replace")[:300]
            raise SinPlayer2(self._explicar(e.code, cuerpo_err))
        except urllib.error.URLError as e:
            raise SinPlayer2(
                "la app de Player2 no responde en %s:%d (%s).\n"
                "  Abrela e inicia sesion. Si el puerto no es el 4315, el real\n"
                "  esta en %%APPDATA%%/game.player2.client/api.port"
                % (self.host, self.port, e.reason))

    @staticmethod
    def _explicar(codigo, cuerpo):
        # Codigos comprobados a mano contra la instancia real, no copiados del
        # OpenAPI: su spec promete 400 donde la implementacion devuelve 422.
        mensajes = {
            401: "no has iniciado sesion en la app de Player2. Abrela y entra.",
            402: "no quedan creditos en Player2.",
            429: "demasiadas peticiones seguidas; espera un poco.",
            422: "Player2 rechazo la peticion por invalida (su spec dice 400, "
                 "devuelve 422). Es un fallo de construccion, no de red.",
            500: "error interno de Player2. Ojo: devuelve 500 tambien cuando se "
                 "le manda 'messages' vacio, y en ese caso reintentar no sirve.",
        }
        base = mensajes.get(codigo, "Player2 respondio HTTP %d." % codigo)
        return "%s\n  respuesta: %s" % (base, cuerpo)

    def salud(self):
        return self._llamar("GET", "/v1/health", timeout=15)

    def completar(self, mensajes, temperatura=None, max_tokens=None):
        if not mensajes:
            # No es paranoia: con la lista vacia Player2 devuelve 500.
            raise SinPlayer2("no se puede pedir una respuesta sin mensajes")
        cuerpo = {"messages": mensajes, "stream": False}
        if temperatura is not None:
            cuerpo["temperature"] = temperatura
        if max_tokens is not None:
            cuerpo["max_tokens"] = max_tokens
        r = self._llamar("POST", "/v1/chat/completions", cuerpo)
        try:
            return r["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError):
            raise SinPlayer2("respuesta con forma inesperada:\n  %s"
                             % json.dumps(r)[:400])


# ---------------------------------------------------------------- prompt

# Un enano tenia 28 pensamientos y otro 69: sin topes el prompt no tiene
# tamano predecible. Estos numeros acotan sin dejarlo sin sustancia.
TOPE_RASGOS = 8
TOPE_PENSAMIENTOS = 6
TOPE_PREFERENCIAS = 6
TOPE_HABILIDADES = 6


def _rasgos_marcados(rasgos, tope=TOPE_RASGOS):
    """Los 50 rasgos son ruido; los interesantes son los que se salen de la
    media. Se ordenan por distancia al centro (50) y se cogen los extremos."""
    ordenados = sorted(rasgos, key=lambda r: abs(r.get("v", 50) - 50), reverse=True)
    return [r for r in ordenados[:tope] if abs(r.get("v", 50) - 50) >= 10]


def _mejores_habilidades(habilidades, tope=TOPE_HABILIDADES):
    """skill_rating es un enum ordenado; nivel_n permite quedarse con las
    mejores sin cablear el orden de los nombres."""
    return sorted(habilidades, key=lambda h: h.get("nivel_n", -1), reverse=True)[:tope]


# ------------------------------------------- punto de paso unico
# Tres veces ha aparecido la misma clase de fallo en un camino NUEVO: el
# tabulador por df2utf, el indice 0-based, y los enums crudos al anadir
# detalle=sonda. El invariante escrito en CLAUDE.md no lo impidio, porque
# depende de que alguien se acuerde. Esto lo impone la estructura: TODO texto
# que venga de DF cruza por legible() antes de llegar al prompt.
#
# No lo arregla en silencio: apunta cada fuga en FUGAS para que se vea que un
# camino se salto la traduccion, en vez de taparlo.

FUGAS = []


def _parece_identificador(t):
    """Forma de identificador de maquina: EUPHORIA, LIKE_FOOD, WatchPerform."""
    if " " in t:
        return False                       # ya es una frase
    if "_" in t and t == t.upper():
        return True
    if t.isupper() and len(t) > 2:
        return True
    return bool(re.search(r"[a-z][A-Z]", t))


def _humanizar(t):
    t = t.replace("_", " ")
    t = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", t)
    t = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", t)
    return t.lower()


def legible(valor, origen="?", siempre_enum=False):
    """Punto de paso obligatorio para el texto que va al prompt.

    Detecta SOLO por forma: ALL_CAPS, CamelCase o guiones bajos. Hubo una version
    que ademas marcaba como sospechoso cualquier token suelto en los campos de
    enum, y fue un error: 'bravery' y 'spouse' son la salida correcta de
    enum_txt(), y 'Crossbow' u 'Observation' son las captions reales de DF para
    esas habilidades. Los marcaba como fuga y llenaba la consola de avisos
    falsos, veinte por narracion.

    El precio es que una palabra capitalizada suelta como 'Syndrome' se cuela:
    por forma es indistinguible de 'Crossbow', que es legitima. Ese caso se
    ataja en origen, con enum_txt() en el lado Lua.

    siempre_enum se conserva por compatibilidad y ya no cambia nada."""
    t = str(valor if valor is not None else "").strip()
    if t and _parece_identificador(t):
        FUGAS.append((origen, t))
        return _humanizar(t)
    return t


def _txt(d, por_defecto="?"):
    """Prefiere el texto legible sobre el identificador de maquina. DF trae
    captions reales para unit_thought_type ("after seeing somebody die"); para
    los demas el lado Lua humaniza el identificador."""
    # rasgos, tipo de relacion, habilidades y preferencias: siempre de enum.
    # Los nombres propios van en 'quien'/'nombre', que no pasan por aqui.
    return legible(d.get("txt") or d.get("n") or por_defecto, "campo txt/n",
                   siempre_enum=True)


def construir_prompt(enano, instruccion=None, recuerdos=None, tope_rasgos=None):
    """Convierte un enano en un prompt acotado y legible."""
    partes = ["Eres %s, %s, de %s anos, en una fortaleza enana."
              % (enano.get("nombre", "un enano"),
                 enano.get("profesion", "sin oficio"),
                 enano.get("edad", "?"))]

    if not enano.get("adulto", True):
        partes.append("Eres un nino: en Dwarf Fortress los ninos no tienen oficio "
                      "asignado ni trabajan. No hables de tu trabajo.")

    estres = enano.get("estres")
    if isinstance(estres, int):
        # Se da la LECTURA, nunca el numero: cuando el prompt incluia el valor
        # crudo, el modelo lo recitaba ("aunque mi estres sigue en 11440").
        como = "muy tranquilo, en paz" if estres < -10000 else \
               "tranquilo" if estres < 0 else \
               "muy agobiado, al limite" if estres > 10000 else "algo tenso"
        partes.append("Por dentro te sientes %s." % como)

    rasgos = _rasgos_marcados(enano.get("rasgos", []), tope_rasgos or TOPE_RASGOS)
    if rasgos:
        partes.append("Rasgos tuyos que destacan: "
                      + ", ".join("%s (%d de 100)" % (_txt(r), r["v"]) for r in rasgos) + ".")

    pens = (enano.get("pensamientos") or [])[-TOPE_PENSAMIENTOS:]
    if pens:
        partes.append("Lo que has sentido ultimamente: "
                      + "; ".join(("%s %s" % (
                          legible(p.get("emocion_txt") or p.get("emocion"),
                                  "pensamiento.emocion", siempre_enum=True),
                          legible(p.get("causa_txt") or p.get("causa"),
                                  "pensamiento.causa", siempre_enum=True))).strip()
                          for p in pens) + ".")

    rel = enano.get("relaciones") or []
    if rel:
        partes.append("Personas de tu vida: "
                      + ", ".join("%s (%s)" % (r.get("quien"), _txt(r, r.get("tipo", "")))
                                  for r in rel) + ".")

    hab = _mejores_habilidades(enano.get("habilidades", []))
    if hab:
        partes.append("Se te da bien: "
                      + ", ".join("%s (%s)" % (_txt(h), h.get("nivel", "?")) for h in hab) + ".")

    # unitpref_type tiene pocos valores, asi que 18 preferencias se agrupan en
    # unos pocos tipos repetidos. Sin deduplicar, el prompt dice seis veces lo mismo.
    pref = list(dict.fromkeys(_txt(x) for x in (enano.get("preferencias") or [])))
    if pref:
        partes.append("Te gustan cosas de estos tipos: "
                      + ", ".join(pref[:TOPE_PREFERENCIAS]) + ".")

    if recuerdos:
        partes.append(recuerdos)

    partes.append(instruccion or
                  "Di un pensamiento tuyo en voz alta, en primera persona y en espanol, "
                  "en DOS frases. Es un pensamiento suelto, tuyo: NO te dirijas a nadie, "
                  "no saludes y no escribas una carta, aunque menciones a alguien. "
                  "Sin comillas, y sin repetir estos datos tal cual.")
    # Con la redaccion anterior ("hablas para ti mismo MIENTRAS trabajas"), 14 de
    # 18 respuestas empezaban por "Mientras tallo/afilo/pico...". El modelo cogio
    # la palabra del prompt y la convirtio en muletilla.
    partes.append("Empieza por donde te apetezca, pero NO arranques describiendo la "
                  "tarea que tienes entre manos: eso ya se ve. Ve directo a lo que "
                  "sientes o piensas.")
    partes.append("Habla como hablaria una persona: NADA de cifras, porcentajes, "
                  "categorias ni nombres de sistema, aunque aparezcan arriba.")
    return "\n".join(partes)


def construir_epitafio(enano, detalle):
    """Prompt en TERCERA persona, para sucesos que el propio enano no puede
    contar. Un muerto no narra su muerte: la primera version le pedia a un
    cadaver que dijera "se acabo, todo se acabo" en presente y primera persona.

    Lo escribe el cronista de la fortaleza, no el enano."""
    quien = enano.get("nombre", "un enano")
    partes = ["Eres el cronista de una fortaleza enana. Anota lo que le ha ocurrido a "
              "uno de sus habitantes."]
    partes.append("Se llamaba %s, %s, de %s anos."
                  % (quien, enano.get("profesion", "sin oficio"), enano.get("edad", "?")))

    rel = enano.get("relaciones") or []
    if rel:
        partes.append("Dejaba atras a: "
                      + ", ".join("%s (%s)" % (r.get("quien"), _txt(r, r.get("tipo", "")))
                                  for r in rel) + ".")
    hab = _mejores_habilidades(enano.get("habilidades", []), 3)
    if hab:
        partes.append("Se le daba bien: "
                      + ", ".join("%s (%s)" % (_txt(h), h.get("nivel", "?")) for h in hab) + ".")

    partes.append("Lo que ha ocurrido: %s." % legible(detalle, "epitafio.detalle",
                                                      siempre_enum=True))
    partes.append("Escribelo en TERCERA persona, en espanol, en UNA o DOS frases secas, "
                  "como una anotacion de cronica. NUNCA en primera persona: el o ella no "
                  "puede contarlo. Sin comillas, sin cifras y sin jerga.")
    return "\n".join(partes)


# ---------------------------------------------------------------- ordenes

def orden_estado(df, _args):
    e = df.estado()
    print("mundo cargado : %s" % e.get("mundo"))
    print("mapa cargado  : %s" % e.get("mapa"))
    print("en pausa      : %s" % e.get("pausa"))
    print("frame         : %s" % e.get("frame"))
    print("ciudadanos    : %s" % e.get("n_ciudadanos"))
    print("partida       : %s" % e.get("partida"))
    print("fecha del juego: ano %s, mes %s, dia %s"
          % (e.get("anio"), e.get("mes"), e.get("dia")))
    print("unit_next_id  : %s" % e.get("unit_next_id"))

    if not (e.get("mundo") and e.get("mapa")):
        print("\nNo hay fortaleza cargada: 'listar' y 'hablar' no funcionaran.")


def orden_listar(df, args):
    n = int(args[0]) if args and args[0].isdigit() else 5
    d = df.enanos(n=n, detalle="basico")
    print("%d de %d adultos" % (len(d["enanos"]), d.get("total_pool", 0)))
    for e in d["enanos"]:
        print("  %-6s %-28s %-22s %3s anos  estres %+7d"
              % (e["id"], e["nombre"][:28], e["profesion"][:22], e["edad"], e["estres"]))


def orden_hablar(df, args):
    """Peticion explicita: hacer hablar a quien tu digas, cuando tu quieras.

      hablar              un enano al azar
      hablar 3            tres al azar
      hablar id=272       ESE enano
      hablar id=272 --epitafio   como si acabara de morir (tercera persona)
      --seco              no anunciarlo en el juego
      --prompt            imprimir el prompt ENTERO antes de la respuesta

    El id= invierte el control: en vez de que el servicio adivine que te
    interesa, lo pides tu. Y hace barato probar la calidad del texto, que hasta
    ahora obligaba a esperar a que pasara algo en la partida.

    --prompt existe porque la prosa correcta oculta los prompts vacios: el
    epitafio llevaba tiempo construyendose sin oficio, sin edad, sin relaciones
    y sin habilidades, y las respuestas seguian sonando bien."""
    seco = "--seco" in args
    ver_prompt = "--prompt" in args
    epitafio = "--epitafio" in args
    ids = [a.split("=", 1)[1] for a in args if a.startswith("id=")]
    args = [a for a in args if not a.startswith("--") and not a.startswith("id=")]

    if ids:
        enanos = []
        for crudo in ids:
            enano = df.uno(int(crudo))
            if enano is None:
                print("  no existe ninguna unidad con id %s" % crudo)
                continue
            enanos.append(enano)
        if not enanos:
            return
    else:
        cuantos = int(args[0]) if args and args[0].isdigit() else 1
        enanos = df.enanos(n=cuantos, detalle="completo",
                           pensamientos=TOPE_PENSAMIENTOS,
                           desde=random.randint(0, 9999))["enanos"]

    p2 = Player2()
    print("Player2 en 127.0.0.1:%d -> %s" % (p2.port, p2.salud()))

    for enano in enanos:
        if epitafio:
            prompt = construir_epitafio(enano, "ha muerto")
        else:
            prompt = construir_prompt(enano)
        print("\n--- %s, %s, %s anos (%d caracteres de prompt)"
              % (enano.get("nombre"), enano.get("profesion", "?"),
                 enano.get("edad", "?"), len(prompt)))
        if ver_prompt:
            for linea in prompt.splitlines():
                print("    | " + linea)
        t0 = time.perf_counter()
        texto = p2.completar([{"role": "user", "content": prompt}])
        print("    (%.2f s) %s" % (time.perf_counter() - t0, texto))
        for origen, crudo in FUGAS:
            print("    [fuga] %s llego sin traducir: %r" % (origen, crudo))
        del FUGAS[:]
        if not seco:
            r = df.anunciar("%s: %s" % (enano.get("nombre"), texto))
            print("    anunciado en %d linea(s) del log" % r.get("lineas", 0))


def orden_ui(df, _args):
    """Que ofrece la interfaz de DFHack en ESTA build. No supone: pregunta.

    Decide si hace falta un plugin en C++ (que ata a compilar y se rompe en
    cada actualizacion) o basta con un overlay en Lua."""
    d = df._json("ui")
    print("dfhack.gui:")
    for f in d.get("gui", []):
        marca = "SI" if f.get("tipo") == "function" else "no"
        print("  [%s] %-24s %s" % (marca, f["n"], f.get("tipo")))
    print("\nmodulos que se pueden requerir:")
    for m in d.get("modulos", []):
        print("  [%s] %s" % ("SI" if m.get("hay") else "no", m["n"]))
    print("\ndfhack.screen: %s   dfhack.textures: %s"
          % (d.get("screen"), d.get("textures")))
    print("\ncolores del anuncio (hoy todo sale en COLOR_YELLOW):")
    print("  " + ", ".join("%s=%s" % (c["n"], c["v"]) for c in d.get("colores", [])))


def orden_enums(df, _args):
    """Que enums traen caption real de DF y cuales hay que humanizar."""
    d = df._json("enums")
    for e in d.get("enums", []):
        estado = "CAPTION REAL" if e.get("caption") else \
                 ("solo identificador" if e.get("existe") else "NO EXISTE en esta build")
        print("  %-26s %-22s %s" % (e["tipo"], estado, e.get("muestra") or ""))


def orden_medir(df, args):
    """Re-mide la latencia con el metodo de la fase 1: N vueltas, mediana y
    rango. Sirve para saber si los 1,4 s del servicio son reales o ruido."""
    import statistics
    vueltas = int(args[0]) if args and args[0].isdigit() else 5

    d = df.enanos(n=1, detalle="completo", pensamientos=TOPE_PENSAMIENTOS)
    enano = d["enanos"][0]
    prompt = construir_prompt(enano)
    p2 = Player2()
    print("Enano: %s, %s" % (enano["nombre"], enano["profesion"]))
    print("Prompt: %d caracteres" % len(prompt))
    print("Referencia de la fase 1: prompt de 4887 car. -> 0,93-1,04 s\n")

    tiempos, largos = [], []
    for i in range(vueltas):
        t0 = time.perf_counter()
        texto = p2.completar([{"role": "user", "content": prompt}])
        dt = time.perf_counter() - t0
        tiempos.append(dt)
        largos.append(len(texto))
        print("  vuelta %d  %.3f s  respuesta %d car." % (i + 1, dt, len(texto)))

    print("\n  mediana %.3f s   min %.3f   max %.3f   respuesta media %d car."
          % (statistics.median(tiempos), min(tiempos), max(tiempos),
             sum(largos) // len(largos)))
    print("  ultima respuesta: %s" % texto)


ORDENES = {"estado": orden_estado, "listar": orden_listar, "hablar": orden_hablar,
           "enums": orden_enums, "medir": orden_medir, "ui": orden_ui}


def main(argv):
    if not argv or argv[0] not in ORDENES:
        print(__doc__.strip())
        return 2
    orden, args = argv[0], argv[1:]

    df = DFHack()
    try:
        df.conectar()
        ORDENES[orden](df, args)
    except SinDFHack as e:
        print("\n[DFHack] %s" % e)
        return 1
    except SinPartida as e:
        print("\n[Partida] %s" % e)
        print("  Carga una fortaleza en Dwarf Fortress y vuelve a intentarlo.")
        return 1
    except SinPlayer2 as e:
        print("\n[Player2] %s" % e)
        return 1
    finally:
        df.cerrar()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
