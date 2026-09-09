# -*- coding: utf-8 -*-
"""
medir_rasgos.py -- ultimo paso de la fase 3. Instrumento de usar y tirar.

¿Cuantos rasgos de personalidad deben entrar en el prompt?

El diagnostico de partida era que un rasgo extremo secuestra la salida, y la
reaccion natural es bajar de 8 a 3. Pero la revision externa aviso del efecto
contrario: con pocos rasgos y el registro fijado se puede acabar con 64 enanos
que suenan todos igual, y en un fuerte grande eso se nota mas que un enano
demasiado intenso.

Asi que el numero se mide, no se elige. Los MISMOS enanos con 3, 5 y 8 rasgos,
y dos senales:

  * solapamiento lexico entre las respuestas de un mismo grupo (una cifra):
    cuanto mas alto, mas se parecen entre si
  * las respuestas impresas agrupadas (el juicio, que es tuyo)

  python medir_rasgos.py [n_enanos]      por defecto 6

Gasta n x 3 llamadas al LLM (unos 20 s con 6). Requiere Player2 abierto.
"""

import itertools
import re
import sys
import time

import df_llm

TOPES = (3, 5, 8)
RES = "mediciones_rasgos.txt"

# Palabras vacias del castellano: contarlas inflaria el parecido entre
# cualquier par de frases.
VACIAS = set("""
a al algo ante antes aqui asi aun aunque cada como con contra cual cuando de del
desde donde dos el ella ellas ellos en entre era eran es esa ese eso esta estan
este esto estoy fue ha hace hacia han hasta hay la las le les lo los mas me mi
mis mucho muy nada ni no nos o otra otro para pero poco por porque que se sea
segun ser si sin sobre solo son su sus tan te tiene todo todos tu un una uno
unos y ya yo me mismo misma siempre nunca ahora bien tanto vez
""".split())


def log(s=""):
    print(s)
    with open(RES, "a", encoding="utf-8") as f:
        f.write(s + "\n")


def contenido(texto):
    """Palabras con carga semantica, normalizadas."""
    # 3 letras y no 4: "paz", "mal" o "sol" tienen carga y la lista de vacias
    # ya filtra las que no.
    pal = re.findall(r"[a-záéíóúüñ]{3,}", (texto or "").lower())
    return {p for p in pal if p not in VACIAS}


def solapamiento(textos):
    """Jaccard medio entre todos los pares. 0 = nada en comun, 1 = identicos."""
    conj = [contenido(t) for t in textos]
    pares = [(a, b) for a, b in itertools.combinations(conj, 2) if a or b]
    if not pares:
        return 0.0
    return sum(len(a & b) / len(a | b) for a, b in pares) / len(pares)


def main(argv):
    cuantos = int(argv[0]) if argv and argv[0].isdigit() else 6

    with open(RES, "a", encoding="utf-8") as f:
        f.write("\n\n" + "#" * 68 + "\n")
        f.write("# CUANTOS RASGOS -- %s\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
        f.write("#" * 68 + "\n")

    df = df_llm.DFHack()
    try:
        df.conectar()
        d = df.enanos(n=cuantos, detalle="completo",
                      pensamientos=df_llm.TOPE_PENSAMIENTOS)
    except (df_llm.SinDFHack, df_llm.SinPartida) as e:
        print("%s" % e)
        return 1
    finally:
        df.cerrar()

    enanos = d.get("enanos", [])
    if len(enanos) < 2:
        print("Hacen falta al menos 2 enanos para comparar.")
        return 1

    p2 = df_llm.Player2()
    log("Enanos: %s" % ", ".join(e["nombre"] for e in enanos))
    log("Rasgos disponibles por enano: %d" % len(enanos[0].get("rasgos", [])))

    resultados = {}
    for tope in TOPES:
        log("")
        log("=" * 66)
        log("CON %d RASGOS EN EL PROMPT" % tope)
        log("=" * 66)
        textos, tam = [], 0
        for e in enanos:
            prompt = df_llm.construir_prompt(e, tope_rasgos=tope)
            tam += len(prompt)
            try:
                t = p2.completar([{"role": "user", "content": prompt}],
                                 max_tokens=df_llm.__dict__.get("MAX_TOKENS", 160))
            except df_llm.SinPlayer2 as err:
                log("  [Player2] %s" % err)
                return 1
            textos.append(t)
            log("  %-22s %s" % (e["nombre"][:22], re.sub(r"\s+", " ", t)))
        resultados[tope] = (solapamiento(textos), tam // len(enanos))

    log("")
    log("=" * 66)
    log("RESULTADO")
    log("=" * 66)
    log("  rasgos   solapamiento entre enanos   prompt medio")
    for tope in TOPES:
        sol, tam = resultados[tope]
        barra = "#" * int(sol * 60)
        log("  %6d   %5.3f  %-22s %5d car." % (tope, sol, barra, tam))
    log("")
    log("  Solapamiento alto = los enanos se parecen entre si.")
    log("  Bajo = cada uno suena distinto, que es lo que se busca.")
    log("  Pero LEE las respuestas: un solapamiento bajo con textos malos no sirve.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
