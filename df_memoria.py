# -*- coding: utf-8 -*-
"""
df_memoria.py -- historial persistido por enano.

Guarda lo que cada enano ha dicho o le ha pasado, para que el prompt pueda
recordarselo y no se repita ni se contradiga. Sobrevive a cerrar el juego.

TRES PROTECCIONES CONTRA LA CORRUPCION SILENCIOSA
La memoria mal atribuida no da error: un enano empieza a recordar la vida de
otro y no hay sintoma hasta que el texto deja de tener sentido. De ahi que:

  1. Un fichero por PARTIDA (dfhack.world.ReadWorldFolder). Los unit_id vuelven
     a empezar en cada mundo, asi que sin esto el enano 272 de la fortaleza
     nueva heredaria los recuerdos del 272 de la vieja.
  2. Cada enano guarda su HUELLA: nombre, ano y momento de nacimiento e
     hist_figure_id. Si la clave coincide pero la huella no, el historial se
     descarta en vez de atribuirse. Un unit_id reutilizado deja de ser
     corrupcion y pasa a ser un enano que empieza sin pasado, que es lo
     correcto: es otro enano.
  3. Escritura atomica (fichero temporal y os.replace). Un Ctrl+C a mitad no
     deja el historial a medio escribir.

El paso 0 midio que unit_id sobrevive a guardar y recargar (64 de 64) y que
unit_next_id es un contador secuencial, asi que la proteccion 2 deberia saltar
raras veces. Se implementa igual: es evidencia, no prueba.

Cada entrada lleva 'participantes' desde el principio, aunque hoy siempre tenga
uno. Es el gancho para las interacciones entre enanos; anadirlo despues
obligaria a migrar el fichero.

Sin dependencias.
"""

import json
import os
import re
import time

VERSION = 1
TOPE_POR_ENANO = 12          # intervenciones que se conservan por enano


def _nombre_fichero(partida):
    """El nombre de la carpeta de partida viene de DF: hay que sanearlo."""
    limpio = re.sub(r"[^A-Za-z0-9_.-]", "_", (partida or "sin_partida").strip())
    return (limpio or "sin_partida")[:64] + ".json"


def huella_de(enano):
    """Lo que identifica a un enano y no cambia nunca. Los nombres se repiten
    en DF, y los unit_id podrian reciclarse; los dos juntos mas la fecha de
    nacimiento y el hist_figure_id, no."""
    return {
        "nombre": enano.get("nombre"),
        "nac_a": enano.get("nac_a", -1),
        "nac_t": enano.get("nac_t", -1),
        "hfid": enano.get("hfid", -1),
    }


class Memoria(object):

    def __init__(self, partida, carpeta="memoria", tope=TOPE_POR_ENANO):
        self.partida = partida or "sin_partida"
        self.carpeta = carpeta
        self.tope = tope
        self.ruta = os.path.join(carpeta, _nombre_fichero(self.partida))
        self.datos = {"version": VERSION, "partida": self.partida, "enanos": {}}
        self.descartes = []      # claves cuyo historial se tiro por huella distinta
        self._cargar()

    # -- disco ------------------------------------------------------
    def _cargar(self):
        try:
            with open(self.ruta, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, json.JSONDecodeError):
            return          # no hay historial todavia, o esta ilegible: se empieza limpio
        if isinstance(d, dict) and d.get("version") == VERSION:
            self.datos = d
            self.datos.setdefault("enanos", {})

    def guardar(self):
        """Atomica: se escribe aparte y se reemplaza de golpe."""
        if self.carpeta:
            os.makedirs(self.carpeta, exist_ok=True)
        tmp = self.ruta + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.datos, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.ruta)

    # -- consulta ---------------------------------------------------
    def _ficha(self, clave, huella):
        """Devuelve la ficha del enano, creandola o vaciandola si la huella
        no cuadra. Aqui es donde se corta la atribucion equivocada."""
        clave = str(clave)
        ficha = self.datos["enanos"].get(clave)
        if ficha is None:
            ficha = {"huella": huella, "entradas": []}
            self.datos["enanos"][clave] = ficha
        elif ficha.get("huella") != huella:
            self.descartes.append({
                "clave": clave,
                "antes": ficha.get("huella", {}).get("nombre"),
                "ahora": huella.get("nombre"),
                "entradas_tiradas": len(ficha.get("entradas", [])),
            })
            ficha = {"huella": huella, "entradas": []}
            self.datos["enanos"][clave] = ficha
        return ficha

    def _entradas_ro(self, clave, huella):
        """Lectura que NO crea ficha. Consultar a los 64 ciudadanos en cada
        vuelta del bucle llenaria el fichero de registros vacios.
        Si la huella no cuadra devuelve vacio, sin tocar nada: el descarte solo
        se hace efectivo cuando de verdad se escribe algo."""
        ficha = self.datos["enanos"].get(str(clave))
        if ficha is None or ficha.get("huella") != huella:
            return []
        return ficha.get("entradas", [])

    def historial(self, clave, huella, n=None):
        """Entradas del enano, de la mas antigua a la mas reciente."""
        entradas = self._entradas_ro(clave, huella)
        return list(entradas[-n:]) if n else list(entradas)

    def ya_contado(self, clave, huella, detalle):
        """¿Ya se conto este mismo suceso? Evita repetir si el sondeo lo ve
        dos veces."""
        return any(e.get("detalle") == detalle
                   for e in self._entradas_ro(clave, huella)[-4:])

    # -- escritura --------------------------------------------------
    def recordar(self, clave, huella, evento, detalle, texto,
                 cuando=None, participantes=None):
        """Anade una intervencion, poda y persiste.

        'participantes' son las claves implicadas. Hoy siempre una; cuando haya
        interacciones entre enanos, la entrada se guarda en la ficha de cada uno
        con la misma lista, para que ambos recuerden el mismo suceso."""
        claves = [str(c) for c in (participantes or [clave])]
        entrada = {
            "cuando": cuando or {},
            "reloj": int(time.time()),
            "evento": evento,
            "detalle": detalle,
            "texto": texto,
            "participantes": claves,
        }
        ficha = self._ficha(clave, huella)
        ficha["entradas"].append(entrada)
        if len(ficha["entradas"]) > self.tope:
            del ficha["entradas"][:-self.tope]
        self.guardar()
        return entrada

    # -- para el prompt ---------------------------------------------
    def para_prompt(self, clave, huella, n=3):
        """De que ha hablado ya, en TEMAS y no en sus palabras.

        La primera version pegaba en el prompt las tres respuestas anteriores
        enteras y decia "NO las repitas". Medido: no funciona y hace dano.

          - No reduce la repeticion. Ver su propio texto lo ceba: en una tirada
            de diez, las respuestas se parecian MAS entre si, no menos.
          - Y CLAUDE.md ya lo tenia escrito: "nombrar la palabra la vuelve a
            meter en el contexto". Lo aprendimos con la muletilla del
            "mientras" y aqui lo repetimos con parrafos enteros.
          - Peor: la prohibicion es imposible de cumplir cuando solo hay una
            manera de decir algo, y el modelo se salio del personaje para
            disculparse -- "no puedo continuar con este roleplay".

        Ahora se manda el TEMA (el campo 'detalle', que lo escribimos nosotros)
        y no su prosa, y se pide avanzar en vez de prohibir."""
        entradas = self.historial(clave, huella, n)
        if not entradas:
            return ""
        temas = []
        for e in entradas:
            t = (e.get("detalle") or e.get("evento") or "").strip()
            if t and t not in temas:
                temas.append(t)
        if not temas:
            return ""
        return ("Ya has hablado en voz alta de esto: " + "; ".join(temas)
                + ". Hoy fijate en otra cosa de las de arriba, y no vuelvas "
                  "sobre lo mismo con las mismas palabras.")

    # -- informacion ------------------------------------------------
    def resumen(self):
        n_enanos = len(self.datos["enanos"])
        n_entradas = sum(len(f.get("entradas", [])) for f in self.datos["enanos"].values())
        return "%d enanos con memoria, %d intervenciones, en %s" % (
            n_enanos, n_entradas, self.ruta)


if __name__ == "__main__":
    import sys
    ruta = sys.argv[1] if len(sys.argv) > 1 else None
    if not ruta:
        print(__doc__.strip())
        raise SystemExit(0)
    with open(ruta, encoding="utf-8") as f:
        d = json.load(f)
    print("partida: %s   enanos: %d" % (d.get("partida"), len(d.get("enanos", {}))))
    for clave, ficha in sorted(d.get("enanos", {}).items(), key=lambda x: int(x[0])):
        h = ficha.get("huella", {})
        print("\n[%s] %s  (nacido %s, hfid %s)  %d entradas"
              % (clave, h.get("nombre"), h.get("nac_a"), h.get("hfid"),
                 len(ficha.get("entradas", []))))
        for e in ficha.get("entradas", []):
            c = e.get("cuando") or {}
            print("   ano %-5s %-10s %s" % (c.get("anio", "?"), e.get("evento"),
                                            (e.get("texto") or "")[:90]))
