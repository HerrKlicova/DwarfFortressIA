# Fase 3 — De orden manual a servicio

Continuación de `INFORME_FASES_1_Y_2.md`. Plan, no resultados: nada de esto está
construido todavía.

## Objetivo

Dejar de ser un comando que se lanza a mano y pasar a un proceso que corre mientras se
juega. **Sondeo barato en bucle, gasto de LLM solo cuando hay algo que contar.**

## Diseño

```
sondear()  →  detectar()  →  decidir()  →  ejecutar()
  cada N s     qué cambió    qué merece    anuncio en el juego
                             contarse      + crónica en fichero
```

`decidir()` devuelve una lista de acciones y `ejecutar()` las realiza. La frontera es
explícita **para que el día que el LLM pueda actuar en el juego, y no solo hablar, la
acción nueva entre ahí sin tocar la generación**.

Eventos que disparan: emoción nueva por encima de umbral, estrés cruzando umbral,
desaparición de un ciudadano, cambio en `relationship_ids`.

## Objeción al punto de partida: el sondeo no es gratis

El encargo daba por bueno que sondear sale gratis, citando los 2 ms medidos en fase 1.
**Ese número mide otra cosa**: lectura de campos en Lua, no serialización JSON ni
transporte.

Sondear 64 ciudadanos con `detalle=completo` implica codificar ~8 000 objetos JSON
(50 rasgos + 28 pensamientos + 18 preferencias + 27 habilidades por enano) y mover del
orden de 400 KB por vuelta. Para acabar comprobando si a alguien le cambió el humor.

**Propuesta:** añadir un `detalle=sonda` con lo mínimo para detectar cambios, y pedir
el detalle completo solo del enano que disparó el evento. Es un camino nuevo al lado
del existente, no una modificación del medido. **Su coste se mide en el paso 0**, no se
da por supuesto.

## Hallazgo que mejora la detección

`personality_moodst` (df.personality.xml) incluye `year` y `year_tick`
(`last_used_year`, `last_used_season_count`): **cada emoción lleva marca de tiempo del
juego**.

Sin eso, detectar «emoción nueva» exigiría comparar contadores, y falla: DF también
**poda** emociones viejas, así que el vector encoge y crece a la vez. Con la marca de
tiempo es exacto — se guarda el máximo `(year, year_tick)` visto por enano y todo lo
posterior es nuevo.

También hay `relative_strength` y `severity` además de `strength`, para calibrar
umbrales.

## Paso 0 — Medición que bloquea el resto

**¿Son estables `unit_id` y `hist_figure_id` al guardar y recargar?** De esto depende la
clave de la memoria. Si cambia, la memoria se corrompe **en silencio**: un enano empieza
a recordar la vida de otro y no hay síntoma hasta que el texto deja de tener sentido.

Método: foto de (id, hist_figure_id, nombre, profesión, edad) → guardar y recargar de
verdad → segunda foto → comprobar que **cada id sigue apuntando al mismo enano**, no
solo que exista. Se cuenta además cuántos `hist_figure_id` valen `-1`, porque no todo
ciudadano es figura histórica.

En la misma pasada se mide el coste real de `sonda` frente a `completo` sobre los 64:
milisegundos de Lua, bytes de JSON, ida y vuelta.

Requiere tres añadidos mínimos al Lua: `hist_figure_id`, `birth_year` y `birth_time` en
la tabla base (hoy no están), y el nivel `sonda`.

### Sobre la reutilización de ids — revisión tras consulta externa

Observación recibida: que `unit_id` sea estable al recargar **no garantiza** que DF no
reasigne el id de un enano muerto a uno nuevo meses después, lo que daría los recuerdos
de un difunto a un recién llegado. El fallo silencioso, otra vez, pero diferido.

La preocupación es correcta. **El método propuesto no la resuelve**: un ciclo de
guardado y recarga no ejercita la reutilización, porque para eso hace falta que alguien
muera y llegue otro después — tiempo de juego, no un ciclo de guardado. El paso 0 no
puede observarlo, y una sola sesión tampoco probaría nada sobre meses de partida.

Se ataca por dos vías, ninguna dependiente de esperar a que ocurra:

1. **Evidencia sobre el esquema de asignación.** El paso 0 lee
   `df.global.unit_next_id` (probado con `pcall`, no se da por hecho que exista) y
   registra la distribución de ids presentes. Un contador global monótono con ids
   dispersos y crecientes —los observados hasta ahora son 272, 273, 335, 1661, 1664—
   apunta a asignación secuencial sin reciclaje. Es evidencia, no prueba.

2. **Huella que hace irrelevante la respuesta.** Cada entrada de memoria guarda, junto a
   la clave, una huella del enano: `nombre`, `birth_year`, `birth_time` y
   `hist_figure_id` (`df.unit.xml:2719`). Al cargar, si la clave coincide pero la huella
   no, **la memoria se descarta en vez de atribuirse**. Un id reutilizado deja de ser
   corrupción silenciosa y pasa a ser un enano que simplemente empieza sin pasado.

La vía 2 es la que cierra el riesgo. La 1 solo sirve para saber con qué frecuencia
esperamos que salte.

## Pasos siguientes

| Paso | Entrega |
|---|---|
| 1 | `df_memoria.py` — historial por enano en JSON, clave = la que salga estable, podado a las últimas N, escritura atómica |
| 2 | `df_vigia.py` — el bucle con las cuatro fases separadas |
| 3 | Ajustes de prompt |

**Memoria:** cada entrada lleva `participantes: [claves]` desde el principio, aunque hoy
siempre tenga uno. Es el gancho para interacciones entre enanos; añadirlo después
obligaría a migrar el fichero.

**Ajustes de prompt:**
- `max_tokens`, apoyado en el dato de fase 2: la latencia va con lo que escribe (r = 0,80), no con lo que lee.
- **Rasgos: el número se mide, no se elige.** El diagnóstico (un rasgo extremo secuestra
  la salida) es correcto, pero bajar de 8 a 3 y fijar el registro puede provocar el
  efecto contrario: 64 enanos que suenan todos igual, que en un fuerte grande se nota
  más que un enano demasiado intenso. Se generarán respuestas para los mismos 6 enanos
  con 3, 5 y 8 rasgos, y se comparará **solapamiento léxico entre respuestas** (una
  cifra) junto a la lectura humana (el juicio). El número sale de ahí, no de una
  corazonada.
- El historial entra en el prompt para que el enano no se repita ni se contradiga.

## Detalles de robustez

- **Muerte vs desaparición:** `getCitizens()` devuelve solo vivos y cuerdos, así que una
  muerte se ve igual que enloquecer, ser enjaulado o irse del mapa. Se hará
  `df.unit.find(id)` sobre los ausentes y se mirará `flags1.dead`. Si no se puede
  distinguir, se reporta como «desaparecido», no como muerte.
- El bucle comprueba `mundo`/`mapa` cada vuelta y, sin partida cargada, duerme sin tocar
  unidades. La fase 1 confirmó que el socket sobrevive a descargar y recargar.
- `Ctrl+C` cierra el socket limpio.

## Restricciones

Sin dependencias nuevas. No se toca la extracción existente. El bucle no puede bloquear
el juego ni dejar el socket colgado. Nada que dependa del motor se prueba con stubs: en
dos fases seguidas los stubs reprodujeron nuestras suposiciones en vez del
comportamiento real.
