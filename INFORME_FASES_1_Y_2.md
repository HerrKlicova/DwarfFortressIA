# Informe de fase — proyecto Dwarf Fortress ↔ LLM local

Entorno: DF 53.16 (Steam), DFHack 53.16-r1.1, Player2 0.10.78, Windows, Python 3.14.7.
Fortaleza de prueba: 64 ciudadanos, 40 adultos.

## Estado

El puente ya no es un spike: es **base estable, medida y documentada**. Se hicieron dos
fases y **ninguna medición obligó a replantear la arquitectura**.

Arquitectura: el lado Lua vive dentro del juego y **extrae estado**; el lado Python es
el **servicio** que decide qué hacer con él. Transporte: el RPC `RunCommand` de DFHack
sobre TCP, con protobuf codificado a mano (dos mensajes, sin dependencias).

---

## Fase 1 — Cerrar incógnitas

Seis mediciones contra el juego real, no simuladas.

| # | Incógnita | Resultado |
|---|---|---|
| 1 | Latencia con prompt largo | 4 887 car. → **0,95 s**. 32× más contexto cuesta solo 2× tiempo |
| 2 | Juego en pausa | `RunCommand` responde normal. No se cuelga ni encola |
| 3 | Descargar/recargar partida | **El mismo socket sobrevive**. Cerrar DF da `ConnectionResetError` limpio. Sin crasheos |
| 4 | Bloqueo del juego | **2 ms para 20 enanos** (2 245 campos) = 0,2 frames a 98 FPS. No medible |
| 5 | Anuncios largos | Hasta 2 000 car. entran. **El `\n` se ignora**: una llamada por línea |
| 6 | Player2 real | Su OpenAPI miente: devuelve **422** donde promete 400, y **500** si mandas `messages: []` |

**Lo más importante del punto 4:** el cuello de botella es exclusivamente el LLM. Leer
datos del juego es unas 300 veces más barato que generar la respuesta. No hay nada que
optimizar en la extracción.

**Lo más importante del punto 3:** no hace falta reconectar en cada cambio de partida.
Basta capturar el reset al cerrar el juego y comprobar `mundo`/`mapa` antes de tocar
unidades.

**Sobre el punto 6:** el `500` con `messages` vacío es determinista, así que reintentarlo
no arregla nada. El cliente debe rechazar la lista vacía antes de enviarla.

---

## Fase 2 — Consolidación

Tres piezas con responsabilidad separada:

| Fichero | Responsabilidad |
|---|---|
| `CLAUDE.md` | Los invariantes del proyecto (ver más abajo) |
| `df_estado.lua` | Vive en el juego. Extrae estado y devuelve **JSON**. N enanos en una sola llamada, con paginación y topes |
| `df_llm.py` | El servicio. Autónomo, sin dependencias, lleva el protocolo dentro |

Los **tres puntos de fallo** son excepciones distintas —`SinDFHack`, `SinPartida`,
`SinPlayer2`— cada una con un diagnóstico que dice qué mirar. Reconexión automática
ante caída del socket.

Órdenes disponibles: `estado`, `listar N`, `hablar N [--seco]`, `medir N`, `enums`.

---

## Calidad del texto generado

**Prompt acotado:** de 4 887 caracteres del volcado crudo a ~1 100. Los 50 rasgos se
reducen a los que se salen de la media, las habilidades se ordenan por nivel numérico,
las preferencias se deduplican, y los pensamientos se limitan a los más recientes.

**Enums traducidos usando el texto del propio DF.** df-structures trae `caption` en
algunos enums, accesible desde Lua con `df.<enum>.attrs[v].caption`
(`docs/dev/Lua API.rst:377`). Preguntado a la instalación real:

| Enum | ¿Caption? | Ejemplo |
|---|---|---|
| `unit_thought_type` | **Sí, prosa real** | `Conflict` → *"while in conflict"* (281 valores) |
| `job_skill` | Sí, cosmético | `MINING` → *"Mining"* |
| `skill_rating` | Sí, inútil | `Dabbling` → *"Dabbling"* (idéntico) |
| `emotion_type`, `personality_facet_type`, `unitpref_type`, `unit_relationship_type`, `value_type` | No | se humaniza el identificador |

Donde no hay caption se humaniza mecánicamente: `WatchPerform` → *watch perform*. Es
una transformación de texto, no una interpretación.

```
Antes:  DELIGHT por WatchPerform; GRIEF por WitnessDeath
Ahora:  delight while watching a performance; grief after seeing somebody die
```

**Dos correcciones de prompt que hicieron falta:**

1. Hay que explicarle que **en DF el estrés negativo es bueno**, o lo interpreta al revés.
2. Hay que decirle **para quién habla**, o escribe cartas a su cónyuge en vez de pensar
   en voz alta.

**Efecto secundario a vigilar:** se seleccionan los rasgos más extremos, y un rasgo muy
alto domina la salida entera. Un enano con lujuria alta produce respuestas marcadamente
eróticas. Es coherente con sus datos, no es un fallo, pero el sitio para tocarlo es el
criterio de selección de rasgos.

---

## Latencia — dato corregido

Aparecieron dos muestras de 1,4 s que parecían una regresión frente a los 0,95 s de la
fase 1. **Re-medido con el método original (5 vueltas, mediana y rango): 0,967 s frente
a 0,952 s. Eran ruido.**

No se le buscó explicación en su momento, y fue lo correcto: cualquier causa que se
hubiera inventado habría sido falsa.

**Patrón que sí sostienen los datos:** la latencia va con el tamaño de la **respuesta**,
no con el del prompt. Correlación entre caracteres generados y tiempo: **r = 0,80**.

Consecuencia práctica: se puede dar todo el contexto que se quiera; lo que cuesta es lo
que escribe. **`max_tokens` está en la API y no se está usando** — es la optimización
pendiente más barata.

---

## Invariantes del proyecto

Cada uno costó una ejecución fallida. Están en `CLAUDE.md` con sus fuentes.

1. **Los contenedores de DFHack son 0-indexados** y lanzan error al salirse, en vez de
   devolver `nil`. Las tablas Lua que devuelve `getCitizens()` son 1-indexadas. Las dos
   cosas conviven en el mismo bucle.
2. **`df2utf` solo sobre cadenas que vienen de DF, nunca sobre los delimitadores.** DF
   trata los bytes `0x00–0x1F` como glifos dibujables: el tabulador `0x09` se convierte
   en `○` y destroza cualquier separador propio.
3. **Nunca formatear números en Lua con `%f`.** El `printf` de C respeta el locale: en
   un Windows en español devuelve coma decimal y `float()` de Python lo rechaza. Usar
   microsegundos enteros con `%d`.
4. **Un diagnóstico nunca adivina la causa si hay respuesta del motor que leer.** DFHack
   devuelve un traceback con fichero y línea; un mensaje que lo ignore manda a mirar al
   sitio equivocado.

Regla general de la que 2 y 3 son ejemplos: **Lua y Python no comparten convenciones**.
Cada dato que cruza entre los dos —texto, números, separadores— necesita un formato
decidido explícitamente.

---

## Advertencia de método

Se montaron servidores de prueba que imitaban el protocolo de DFHack. **Tres de las
cinco trampas se colaron igual**, porque los stubs reproducían nuestras suposiciones
(tabuladores tal cual, tablas 1-indexadas) en vez del comportamiento del motor.

Un stub sirve para el framing del protocolo, que es una especificación escrita. **No
sirve para indexación, codificación de texto ni reglas del juego.** Eso se ejercita
contra DFHack real o no se ha probado.

---

## Decisiones abiertas

1. **Qué dispara una llamada.** Sigue siendo manual. Hay que decidir si se sondea
   periódicamente, si se engancha a eventos, o si lo lanza el jugador.
2. **Memoria.** Cada llamada parte de cero. Un enano que no recuerda lo que dijo hace un
   minuto rompe la ilusión antes que cualquier fallo técnico.
3. **Acotar la respuesta** con `max_tokens`, ahora que se sabe que es lo que cuesta.
4. **Criterio de selección de rasgos**, por lo dicho arriba.
5. **Dependencia de Player2:** sin clave de API, pero exige la app abierta y con sesión
   iniciada. Si eso es aceptable como requisito de instalación es decisión de producto.

---

## Nota

`HALLAZGOS.md` es el registro largo, con todas las mediciones y citas a fichero y línea
del código de DFHack. Este informe es el resumen de las dos fases.
