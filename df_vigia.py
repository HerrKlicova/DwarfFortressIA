# -*- coding: utf-8 -*-
"""
df_vigia.py -- el servicio. Mira la fortaleza y hace hablar a quien le pasa algo.

  python df_vigia.py                     sondea cada 5 s, anuncia y escribe cronica
  python df_vigia.py --cada 10           otro intervalo
  python df_vigia.py --seco              no escribe en el juego (solo consola y cronica)
  python df_vigia.py --vueltas 20        para despues de N vueltas, para probar

Ctrl+C cierra limpio.

CUATRO FASES CON FRONTERA EXPLICITA

    sondear()  ->  detectar()  ->  decidir()  ->  ejecutar()
                     que cambio    que merece    anuncio en el juego
                                   contarse      + cronica en fichero

'decidir' devuelve una lista de acciones y 'ejecutar' las realiza. La frontera
esta ahi para que el dia que el LLM pueda ACTUAR en el juego, y no solo hablar,
la accion nueva entre en 'ejecutar' sin tocar la generacion de texto.

COSTE (medido en el paso 0, sobre 64 ciudadanos)
  sondeo con detalle=sonda : 15 KB y 5 ms de Lua -> medio frame a 98 FPS
  con detalle=completo     : 344 KB y 40 ms      -> casi 4 frames congelados
Se sondea con 'sonda' y solo se pide el expediente completo del enano que
disparo el evento.

Sin dependencias.
"""

import argparse
import json
import os
import sys
import time

import re

import df_llm
import df_memoria

# --- que se considera digno de contarse -------------------------------
FUERZA_MINIMA = 30        # fuerza de la emocion por debajo de la cual se ignora
DESCANSO_GLOBAL = 20      # segundos minimos entre dos llamadas al LLM
DESCANSO_ENANO = 900      # un mismo enano no vuelve a hablar hasta pasado esto
MAX_TOKENS = 160          # la latencia va con lo que escribe, no con lo que lee
VENTANA_REPETIDOS = 5     # no repetir el mismo suceso aunque le pase a otro enano

PESOS = {"muerte": 100, "locura": 90, "desaparicion": 60,
         "relacion": 50,
         # Una emocion FUERTE dice por que se siente asi; el cambio de
         # categoria de estres solo dice que ha cambiado. Por eso pesa mas.
         # Y va por debajo de 'relacion' (50), que es un vinculo perdido o
         # ganado, no un estado de animo.
         "emocion_fuerte": 45, "estres": 40, "emocion": 20, "llegada": 15}

# Sucesos que el propio enano NO puede narrar en primera persona. La primera
# ejecucion con una muerte real le pidio al muerto que hablara en presente:
# "Se acabo, todo se acabo". Estos los cuenta el cronista, en tercera persona.
EN_TERCERA = {"muerte", "locura", "desaparicion"}

# Sucesos que le pasan a UNA persona concreta. Aunque dos compartan el texto
# ("ha muerto"), son sucesos distintos y hay que contar los dos.
UNICOS_POR_PERSONA = {"muerte", "locura", "desaparicion", "relacion", "llegada"}


def ahora():
    return time.time()


class Vigia(object):

    def __init__(self, df, memoria, seco=False, cada=5.0):
        self.df = df
        self.memoria = memoria
        self.seco = seco
        self.cada = cada
        self.p2 = None                 # perezoso: no se toca Player2 hasta que hace falta
        self.antes = None              # sondeo anterior
        self.ultima_llamada = 0.0
        self.ultimo_de = {}            # clave -> cuando hablo por ultima vez
        self.recientes = []            # ultimos detalles narrados, por CUALQUIER enano
        self.cronica = None
        self.rel_tipos = None          # nombres de unit_relationship_type, por indice

    # ---------------------------------------------------------- sondear
    def sondear(self):
        d = self.df.sonda()
        self.cronica = self.cronica or Cronica(d.get("partida") or "sin_partida")
        return d

    # ---------------------------------------------------------- detectar
    def detectar(self, antes, ahora_):
        """Compara dos sondeos. Devuelve la lista de sucesos."""
        if antes is None:
            return []                  # primera vuelta: solo fija la referencia

        a = {e["id"]: e for e in antes.get("enanos", [])}
        b = {e["id"]: e for e in ahora_.get("enanos", [])}
        eventos = []

        for cid, act in b.items():
            viejo = a.get(cid)
            if viejo is None:
                eventos.append(self._evento("llegada", act,
                                            "acaba de incorporarse a la fortaleza"))
                continue

            # Emocion nueva: se compara la MARCA DE TIEMPO del juego, no el
            # contador. DF tambien poda emociones viejas, asi que el numero de
            # emociones sube y baja y no sirve como senal.
            if self._mas_nueva(act, viejo) and act.get("emo_fuerza", 0) >= FUERZA_MINIMA:
                eventos.append(self._evento("emocion", act, self._texto_emocion(act)))

            # Y la mas FUERTE, aparte. Si en la misma vuelta de cinco segundos
            # a un enano le llega el duelo por su pareja y detras una funcion de
            # teatro, con solo mirar la mas reciente se cuenta el teatro. Esto
            # es lo que dejaba muda a la viuda.
            if self._mas_fuerte_nueva(act, viejo) \
                    and abs(act.get("fue_fuerza", 0)) >= FUERZA_MINIMA \
                    and act.get("fue_causa") != act.get("emo_causa"):
                eventos.append(self._evento("emocion_fuerte", act,
                                            self._texto_emocion(act, "fue")))

            # Estres: se usa la categoria de DF (0 mas estresado, 6 menos), no
            # umbrales inventados.
            ca, cv = act.get("estres_cat", -1), viejo.get("estres_cat", -1)
            if ca >= 0 and cv >= 0 and ca != cv:
                eventos.append(self._evento("estres", act, self._que_animo(cv, ca)))

            if act.get("rel") != viejo.get("rel"):
                ganados, perdidos = self._cambio_relacion(viejo, act)
                ev = self._evento("relacion", act,
                                  self._que_relacion(ganados, perdidos))
                ev["ganados"], ev["perdidos"] = ganados, perdidos
                eventos.append(ev)

        for cid, viejo in a.items():
            if cid not in b:
                eventos.append(self._ausente(cid, viejo))

        return [e for e in eventos if e]

    @staticmethod
    def _que_animo(antes, ahora_):
        """En lenguaje llano. Con la redaccion anterior ("su animo ha mejorado
        (categoria 2 a 3)") el modelo la recitaba tal cual en la respuesta."""
        salto = abs(ahora_ - antes)          # categorias de DF: 0 peor, 6 mejor
        if ahora_ > antes:
            return ("te has quitado un gran peso de encima" if salto > 1
                    else "te sientes algo mejor que hace un rato")
        return ("algo te ha hundido el animo de golpe" if salto > 1
                else "te sientes algo peor que hace un rato")

    @staticmethod
    def _huecos_rel(cad):
        """'rel' son los relationship_ids unidos por comas, EN ORDEN: la
        posicion i es el vinculo de tipo i (unit_relationship_type). Un -1
        significa que ese hueco esta vacio."""
        salida = []
        for x in (cad or "").split(","):
            x = x.strip()
            try:
                salida.append(int(x))
            except ValueError:
                salida.append(-1)
        return salida

    @classmethod
    def _cambio_relacion(cls, viejo, act):
        """Que ids concretos han entrado y salido. Antes solo se CONTABAN, y
        por eso el suceso decia "alguien nuevo" -- y el enano acababa
        narrando su propio desconocimiento: "no entiendo del todo lo que
        significa". Un enano siempre sabe quien se ha vuelto importante."""
        a, b = cls._huecos_rel(viejo.get("rel")), cls._huecos_rel(act.get("rel"))
        ganados, perdidos = [], []
        for i in range(max(len(a), len(b))):
            antes = a[i] if i < len(a) else -1
            ahora = b[i] if i < len(b) else -1
            if antes == ahora:
                continue
            # Se guarda el INDICE junto al id: la posicion dice el TIPO de
            # vinculo (unit_relationship_type), que es lo que permite decir
            # "tu madre" en vez de dejar que el modelo lo adivine.
            if ahora >= 0:
                ganados.append((i, ahora))
            if antes >= 0:
                perdidos.append((i, antes))
        return ganados, perdidos

    @staticmethod
    def _que_relacion(ganados, perdidos):
        """El texto de respaldo, sin nombres. Solo se usa si luego no se puede
        resolver quien es: los nombres se ponen en _hablar(), que es donde hay
        expediente."""
        if ganados and not perdidos:
            return "alguien nuevo ha pasado a ser importante en tu vida"
        if perdidos and not ganados:
            return "has perdido a alguien importante de tu vida"
        return "una de tus relaciones ha cambiado"

    @staticmethod
    def _mas_nueva(act, viejo):
        return (act.get("emo_a", -1), act.get("emo_t", -1)) > \
               (viejo.get("emo_a", -1), viejo.get("emo_t", -1))

    @staticmethod
    def _mas_fuerte_nueva(act, viejo):
        """La emocion mas fuerte del enano ha cambiado de marca de tiempo: es
        otra, no la misma de siempre."""
        return (act.get("fue_a", -1), act.get("fue_t", -1)) > \
               (viejo.get("fue_a", -1), viejo.get("fue_t", -1))

    @staticmethod
    def _texto_emocion(enano, prefijo="emo"):
        """'sadness at being separated from a loved one'.

        Con .strip(): cuando DF no da el tipo de emocion, el lado Lua manda
        cadena vacia, y sin esto el detalle empezaba por un espacio y acababa
        en la cronica y en la memoria con el."""
        return ("%s %s" % (enano.get(prefijo + "_tipo", ""),
                           enano.get(prefijo + "_causa", ""))).strip()

    @staticmethod
    def _evento(tipo, enano, detalle):
        return {"tipo": tipo, "clave": enano["id"], "enano": enano,
                "detalle": detalle, "peso": PESOS.get(tipo, 10)}

    def _ausente(self, cid, viejo):
        """getCitizens() solo devuelve vivos y cuerdos, asi que faltar puede ser
        morir, enloquecer, ser enjaulado o irse. Se pregunta cual de las cuatro."""
        try:
            u = self.df.unidad(cid)
        except (df_llm.SinDFHack, df_llm.SinPartida):
            return None
        if not u.get("existe"):
            return self._evento("desaparicion", viejo, "ha desaparecido de la fortaleza")
        if u.get("muerto"):
            return self._evento("muerte", viejo, "ha muerto")
        if u.get("fantasma"):
            return self._evento("muerte", viejo, "vaga como fantasma")
        if not u.get("cuerdo", True):
            return self._evento("locura", viejo, "ha perdido la cordura")
        if not u.get("activo"):
            return self._evento("desaparicion", viejo,
                                "ya no esta en el mapa (enjaulado, o se marcho)")
        return None

    # ---------------------------------------------------------- decidir
    def decidir(self, eventos):
        """De todo lo que ha pasado, que merece gastar una llamada al LLM.
        Aqui es donde entrarian acciones nuevas cuando el LLM pueda actuar."""
        if not eventos:
            return []
        if ahora() - self.ultima_llamada < DESCANSO_GLOBAL:
            return []

        candidatos = []
        for e in eventos:
            clave = e["clave"]
            if ahora() - self.ultimo_de.get(clave, 0) < DESCANSO_ENANO:
                continue
            huella = df_memoria.huella_de(e["enano"])
            if self.memoria.ya_contado(clave, huella, e["detalle"]):
                continue
            # Un suceso que afecta a media fortaleza (un sindrome, por ejemplo)
            # dispara en muchos enanos a la vez y llena la cronica de lo mismo.
            # Pero eso solo vale para causas COMPARTIDAS: cinco muertes distintas
            # comparten el texto "ha muerto" y son cinco personas, no una repeticion.
            # Con la version anterior, matar a cinco enanos narraba uno solo.
            if e["tipo"] not in UNICOS_POR_PERSONA and e["detalle"] in self.recientes:
                continue
            candidatos.append(e)

        if not candidatos:
            return []
        # Uno por vuelta: el mas grave. Lo demas seguira ahi si sigue importando.
        candidatos.sort(key=lambda e: e["peso"], reverse=True)
        return [{"tipo": "hablar", "evento": candidatos[0]}]

    # ---------------------------------------------------------- ejecutar
    def ejecutar(self, acciones, estado):
        for accion in acciones:
            if accion["tipo"] == "hablar":
                self._hablar(accion["evento"], estado)

    def _hablar(self, evento, estado):
        clave = evento["clave"]
        # El expediente completo, solo de este: 344 KB para los 64 no compensa.
        # Se pide POR ID, que no pasa por getCitizens(). Antes se paginaba esa
        # lista de 20 en 20, y para muerte, locura y desaparicion no podia
        # funcionar nunca: esos sucesos se detectan precisamente porque el
        # enano ya no esta ahi. Se gastaban 11 peticiones y ~1,1 MB de Lua para
        # devolver None, y el epitafio salia con los datos de la sonda, que no
        # traen oficio, ni edad, ni relaciones, ni habilidades -- los cuatro
        # campos que usa construir_epitafio().
        enano = self.df.uno(clave)
        if enano is None:
            enano = evento["enano"]            # se tira con lo que dio la sonda
            print("  [aviso] id %s no resuelto: el prompt ira con los datos de la sonda"
                  % clave)

        huella = df_memoria.huella_de(enano)

        # Con el expediente delante ya se puede decir QUIEN. Va aqui y no en
        # detectar() porque es aqui donde hay relaciones con nombre.
        if evento["tipo"] == "relacion":
            self._nombrar_relacion(evento, enano)

        # Un muerto no narra su muerte. Sin esto, el difunto decia "se acabo,
        # todo se acabo" en primera persona y en presente.
        if evento["tipo"] in EN_TERCERA:
            self._decir(evento, enano, huella,
                        df_llm.construir_epitafio(enano, evento["detalle"]), estado)
            return

        recuerdos = self.memoria.para_prompt(clave, huella, 3)
        # Las captions de DF estan en ingles y en tercera persona ("pleasure near
        # his own quality building"). Sin avisar, el modelo las traducia literal
        # y salia "placer cerca de mi propia calidad al construir".
        instr = ("Esto es lo que acaba de pasarte, tal como lo anota el juego, en ingles "
                 "y en tercera persona: \"%s\". No lo traduzcas literalmente: cuenta "
                 "con TUS palabras lo que eso significa para ti, en UNA o DOS frases, "
                 "en primera persona y en espanol. Hablas para ti mismo: no te dirijas "
                 "a nadie ni saludes. Sin comillas, sin cifras y sin jerga."
                 % df_llm.legible(evento["detalle"], "evento.detalle", siempre_enum=True))
        prompt = df_llm.construir_prompt(enano, instruccion=instr, recuerdos=recuerdos)
        self._decir(evento, enano, huella, prompt, estado)

    def _tipo_rel(self, indice, respaldo=None):
        """'mother', 'spouse'... por el INDICE del hueco en relationship_ids.

        La lista viene de 'estado' y se cachea: es la misma para todos los
        ciudadanos, asi que pedirla en la sonda seria repetirla 128 veces."""
        if self.rel_tipos is None:
            try:
                self.rel_tipos = self.df.estado().get("rel_tipos") or []
            except (df_llm.SinDFHack, df_llm.SinPartida):
                self.rel_tipos = []
        if 0 <= indice < len(self.rel_tipos):
            return self.rel_tipos[indice]
        return respaldo or "alguien cercano"

    def _nombrar_relacion(self, evento, enano):
        """Pone nombre al vinculo que ha cambiado, y apunta al OTRO como
        participante.

        El suceso decia "alguien nuevo ha pasado a ser importante en tu vida",
        y el enano acababa narrando su propio desconocimiento: "no entiendo del
        todo lo que significa". Un enano siempre sabe quien.

        Si no se puede resolver el nombre, se deja el texto generico: un id
        suelto en el prompt acaba convertido en personaje (fue el caso de
        'unidad 338')."""
        por_id = {r.get("id"): r for r in (enano.get("relaciones") or [])}
        partes, otros = [], []

        for i, oid in evento.get("ganados", []):
            r = por_id.get(oid)
            if r and r.get("quien"):
                partes.append("%s ha pasado a ser tu %s"
                              % (r["quien"], self._tipo_rel(i, r.get("txt"))))
                otros.append(oid)

        for i, oid in evento.get("perdidos", []):
            # El que se ha ido ya NO esta en 'relaciones', asi que ni su nombre
            # ni su tipo salen de ahi. El nombre se pregunta con df.unidad(),
            # que lo encuentra aunque este muerto; el TIPO sale del indice del
            # hueco, que es lo unico que queda del vinculo.
            nombre = None
            try:
                u = self.df.unidad(oid)
                if u.get("existe"):
                    nombre = u.get("nombre")
            except (df_llm.SinDFHack, df_llm.SinPartida):
                nombre = None
            if nombre:
                partes.append("has perdido a %s, tu %s" % (nombre, self._tipo_rel(i)))
                otros.append(oid)

        if partes:
            evento["detalle"] = "; ".join(partes)
        # Los dos implicados, para que la entrada de memoria sea compartida.
        evento["participantes"] = [evento["clave"]] + otros

    def _decir(self, evento, enano, huella, prompt, estado):
        """Llama al LLM, anuncia, apunta en la cronica y en la memoria."""
        clave = evento["clave"]
        self.p2 = self.p2 or df_llm.Player2()
        t0 = time.perf_counter()
        try:
            bruto = self.p2.completar([{"role": "user", "content": prompt}],
                                      max_tokens=MAX_TOKENS)
        except df_llm.SinPlayer2 as e:
            print("  [Player2] %s" % e)
            return
        dt = time.perf_counter() - t0

        # Red de seguridad de SALIDA. Si el modelo se sale del personaje, eso
        # NO se anuncia en el juego ni entra en la cronica. Paso de verdad:
        # "no puedo continuar con este roleplay" detras de una respuesta buena.
        texto = df_llm.limpiar_meta(bruto, "vigia.%s" % evento["tipo"])
        for origen, trozo in df_llm.DESCARTES_META:
            print("  [fuera de personaje] %s: %r" % (origen, trozo[:100]))
        del df_llm.DESCARTES_META[:]
        if texto is None:
            print("  [%s] %s: respuesta descartada entera, no se anuncia"
                  % (evento["tipo"], enano.get("nombre")))
            self.ultima_llamada = ahora()      # el gasto ya se hizo
            return

        self.ultima_llamada = ahora()
        self.ultimo_de[clave] = ahora()
        self.recientes.append(evento["detalle"])
        del self.recientes[:-VENTANA_REPETIDOS]

        cuando = {"anio": estado.get("anio", -1), "mes": estado.get("mes", -1),
                  "dia": estado.get("dia", -1)}
        print("  [%s] %s (%.2f s): %s"
              % (evento["tipo"], enano.get("nombre"), dt, Cronica._plano(texto)))

        # 'participantes' lleva al otro cuando el suceso implica a dos. Es el
        # gancho que df_memoria tiene puesto desde el principio para las
        # interacciones: la misma entrada, en la ficha de cada uno.
        self.memoria.recordar(clave, huella, evento["tipo"], evento["detalle"],
                              texto, cuando=cuando,
                              participantes=evento.get("participantes") or [clave])
        for d_ in self.memoria.descartes:
            print("  [memoria] descartado el historial de %s: era %r y ahora es %r"
                  % (d_["clave"], d_["antes"], d_["ahora"]))
        self.memoria.descartes = []

        if df_llm.FUGAS:
            for origen, crudo in df_llm.FUGAS:
                print("  [fuga] %s llego sin traducir: %r (humanizado al vuelo)"
                      % (origen, crudo))
            del df_llm.FUGAS[:]

        self.cronica.escribir(cuando, enano.get("nombre"), evento["detalle"], texto)

        if not self.seco:
            try:
                r = self.df.anunciar("%s: %s" % (enano.get("nombre"), texto))
                print("       anunciado en %d linea(s)" % r.get("lineas", 0))
            except (df_llm.SinDFHack, df_llm.SinPartida) as e:
                print("       no se pudo anunciar: %s" % e)

    # ---------------------------------------------------------- bucle
    def vuelta(self):
        estado = self.df.estado()
        if not (estado.get("mundo") and estado.get("mapa")):
            return "sin partida cargada"
        d = self.sondear()
        eventos = self.detectar(self.antes, d)
        primera = self.antes is None
        self.antes = d
        if primera:
            return "referencia fijada con %d ciudadanos" % len(d.get("enanos", []))
        acciones = self.decidir(eventos)
        self.ejecutar(acciones, estado)
        if eventos and not acciones:
            return "%d suceso(s), ninguno para contar todavia" % len(eventos)
        return "%d suceso(s)" % len(eventos) if eventos else None


class Cronica(object):
    """Registro en texto de todo lo que se ha narrado, fechado en anos enanos."""

    def __init__(self, partida, carpeta="cronica"):
        self.carpeta = carpeta
        self.ruta = os.path.join(carpeta, df_memoria._nombre_fichero(partida)[:-5] + ".txt")

    @staticmethod
    def _plano(texto):
        return re.sub(r"\s+", " ", (texto or "")).strip()

    def escribir(self, cuando, nombre, detalle, texto):
        os.makedirs(self.carpeta, exist_ok=True)
        with open(self.ruta, "a", encoding="utf-8") as f:
            f.write("[ano %s, mes %s, dia %s] %s -- %s\n  %s\n\n"
                    % (cuando.get("anio"), cuando.get("mes"), cuando.get("dia"),
                       nombre, detalle, self._plano(texto)))


def main(argv):
    p = argparse.ArgumentParser(add_help=True, description=__doc__.splitlines()[1])
    p.add_argument("--cada", type=float, default=5.0, help="segundos entre sondeos")
    p.add_argument("--seco", action="store_true", help="no escribir en el juego")
    p.add_argument("--vueltas", type=int, default=0, help="parar tras N vueltas (0 = sin fin)")
    args = p.parse_args(argv)

    df = df_llm.DFHack()
    try:
        df.conectar()
    except df_llm.SinDFHack as e:
        print("[DFHack] %s" % e)
        return 1

    try:
        estado = df.estado()
    except df_llm.SinDFHack as e:
        print("[DFHack] %s" % e)
        df.cerrar()
        return 1

    partida = estado.get("partida") or "sin_partida"
    memoria = df_memoria.Memoria(partida)
    print("Vigilando la partida %r cada %.1f s%s"
          % (partida, args.cada, "  (en seco)" if args.seco else ""))
    print("Memoria: %s" % memoria.resumen())
    print("Ctrl+C para parar.\n")

    vig = Vigia(df, memoria, seco=args.seco, cada=args.cada)
    n = 0
    try:
        while True:
            n += 1
            try:
                nota = vig.vuelta()
            except df_llm.SinPartida as e:
                nota = "sin partida: %s" % e
                vig.antes = None            # al recargar hay que volver a fijar referencia
            except df_llm.SinDFHack as e:
                print("  [DFHack] %s" % e)
                print("  esperando a que vuelva...")
                nota = None
                vig.antes = None
            if nota:
                print("  vuelta %-4d %s" % (n, nota))
            if args.vueltas and n >= args.vueltas:
                break
            time.sleep(args.cada)
    except KeyboardInterrupt:
        print("\nParando.")
    finally:
        df.cerrar()
        print("Memoria: %s" % memoria.resumen())
        if vig.cronica:
            print("Cronica: %s" % vig.cronica.ruta)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
