# Fase 3 — De orden manual a servicio

Continuación de `INFORME_FASES_1_Y_2.md`. **Fase completada.** Todo lo de aquí está
ejecutado contra la instalación real (DF 53.16, DFHack 53.16-r1.1, Player2 0.10.78,
fortaleza de 64 ciudadanos), no planificado.

## Qué hay ahora

Un proceso que corre mientras se juega. Sondea barato en bucle y gasta LLM solo cuando
pasa algo.

```
sondear()  →  detectar()  →  decidir()  →  ejecutar()
  cada 5 s     qué cambió    qué merece    anuncio en el juego
                             contarse      + crónica en fichero
```

`decidir()` devuelve acciones y `ejecutar()` las realiza. La frontera es explícita para
que, cuando el LLM pueda **actuar** en el juego y no solo hablar, la acción nueva entre
ahí sin tocar la generación.

| Fichero | Responsabilidad |
|---|---|
| `df_estado.lua` | Extrae estado. Tres niveles: `sonda`, `basico`, `completo` |
| `df_llm.py` | Biblioteca: protocolo DFHack, cliente Player2, construcción del prompt |
| `df_memoria.py` | Historial por enano, persistido, con tres protecciones |
| `df_vigia.py` | El bucle |

---

## Paso 0 — La medición que bloqueaba el resto

**¿Sobrevive `unit_id` a guardar y recargar?** De ello dependía la clave de la memoria:
si cambia, un enano hereda los recuerdos de otro y **no hay síntoma** hasta que el texto
deja de tener sentido.

| Métrica | Resultado |
|---|---|
| ids que siguen apuntando **al mismo enano** | **64 de 64** |
| ids que apuntan a otro | **0** |
| `hist_figure_id` válidos y estables | **64 de 64**, ninguno vale `-1` |

La comparación no cuenta supervivientes sino **huellas** (nombre + año y momento de
nacimiento + `hist_figure_id`), que es lo único que detecta un id que cambió de dueño.

### Sobre la reutilización de ids (la observación de la revisión externa)

La preocupación era correcta pero **el método propuesto no la resuelve**: un ciclo de
guardado y recarga no ejercita el reciclaje, porque para eso hace falta que alguien
muera y llegue otro después. Se atacó por dos vías:

1. **Evidencia**: `unit_next_id` = 5486 y el id de ciudadano más alto es 5483, con 5166
   huecos en el rango. El contador va justo por delante del máximo y no se reinicia al
   recargar → asignación secuencial, no reciclaje desde un pool. Evidencia, no prueba.
2. **Huella que hace irrelevante la respuesta**: si la clave coincide pero la huella no,
   el historial **se descarta** en vez de atribuirse. Un id reutilizado deja de ser
   corrupción silenciosa y pasa a ser un enano que empieza sin pasado, que es lo correcto.

**Riesgo adicional que no estaba en el plan y es más probable**: los `unit_id` vuelven a
empezar en cada mundo. Sin separar por partida, el enano 272 de una fortaleza heredaría
los recuerdos del 272 de otra. Resuelto con un fichero por partida vía
`dfhack.world.ReadWorldFolder()`.

### El sondeo no era gratis

El encargo lo daba por gratis citando los 2 ms de la fase 1. **Ese número medía otra
cosa**: lectura de campos en Lua, no serialización JSON ni transporte.

| Nivel | Bytes | Lua | Por minuto (cada 5 s) | Frames congelados |
|---|---|---|---|---|
| `basico` | 9,3 KB | 1 ms | 109 KB | 0,1 |
| **`sonda`** | **15,0 KB** | **5 ms** | **176 KB** | **0,5** |
| `completo` | **344,5 KB** | **40 ms** | 4 037 KB | **3,9** |

Sondear con `completo` habría costado **23× más tráfico y 8× más tiempo de juego
bloqueado**: casi cuatro frames congelados cada cinco segundos a 98 FPS. Se añadió un
nivel `sonda` con lo mínimo para detectar cambios; el expediente completo se pide solo
del enano que disparó el evento.

---

## Detección: las definiciones del juego, no umbrales inventados

- **Emoción nueva** por la **marca de tiempo del juego** (`year`, `year_tick` de
  `personality_moodst`). Comparar contadores habría fallado siempre: DF también **poda**
  emociones viejas, así que el número sube y baja.
- **Estrés** por cambio de `getStressCategory` (0-6), la clasificación de DF.
- **Relaciones** por cambio en `relationship_ids`, distinguiendo ganar de perder vínculo.

### Corrección al plan: no existe `flags1.dead`

El plan proponía distinguir muerte de desaparición con ese campo. **No existe.** El que
hay se llama `inactive` y su comentario en df-structures dice que también se activa para
*"criaturas vivas que entran o salen del mapa"* — habría dado por muerto a quien vuelve
de una misión.

Se usan las funciones documentadas `isKilled`, `isGhost`, `isSane` e `isActive`, que sí
separan morir, enloquecer, ser enjaulado e irse. Si no se puede determinar, se reporta
«desapareció» sin inventar.

**Sin ejercitar**: en las ejecuciones no murió nadie, así que ese camino está
implementado pero no probado.

## Control del gasto

| Freno | Valor |
|---|---|
| Entre llamadas al LLM | 20 s |
| El mismo enano | 15 min |
| Por vuelta | Una sola, la del suceso más grave |
| Repetidos por enano | No se cuenta dos veces (lo mira en la memoria) |
| Repetidos **entre** enanos | Ventana de los últimos 5 sucesos narrados |
| `max_tokens` | 160 |

El último freno salió de una ejecución real: un síndrome afectó a media fortaleza y
cuatro enanos narraron la misma euforia. Los frenos por enano funcionaban; no había
ninguno entre enanos.

---

## Calidad del texto: cinco defectos, cuatro causados por el prompt

Medido sobre la crónica real: **31% de las respuestas tenían fuga de jerga → 0%**.

| Defecto | Síntoma | Causa |
|---|---|---|
| Enums crudos | *"¡Euphoria! ¡Qué alegría...!"* | La sonda usaba `enum()` y no `enum_txt()`. **La trampa de la fase 2 reaparecida en un camino nuevo** |
| Cifras recitadas | *"aunque mi estrés sigue en **11440**"* | El prompt decía `"Tu nivel de estres es %d"`. Se la dábamos nosotros |
| Jerga de máquina | *"Mi ánimo ha mejorado **de categoría 2 a 3**"* | El disparador era `su animo ha mejorado (categoria 2 a 3)` |
| Traducción literal | *"placer cerca de **mi propia calidad al construir**"* | Las captions de DF están en **inglés y tercera persona** |
| Muletilla | **16 de 18** respuestas con «mientras», **14 empezando por ahí** | La instrucción decía *"hablas para ti mismo **mientras trabajas**"* |
| Relleno como personaje | *"**Unidad 338** me espera con su sonrisa callada"* | Sustituto de un pariente no resuelto, tomado por nombre propio |

Tras corregir: «mientras» pasa de 16/18 a **2/18**, ninguna al principio y las dos a
mitad de frase, que es castellano normal.

**Nota sobre el método**: la muletilla no se arregla diciendo «no uses esa palabra».
Nombrarla vuelve a meterla en el contexto. Hay que quitarla y pedir otra cosa.

---

## Cuántos rasgos: la métrica no sirvió, y ese es el resultado

Se midió porque la revisión externa avisó de que bajar de 8 a 3 podía cambiar «un enano
demasiado intenso» por «64 que suenan igual». Los mismos 6 enanos con 3, 5 y 8 rasgos,
midiendo solapamiento léxico entre sus respuestas:

| Rasgos | Tirada 1 | Tirada 2 |
|---|---|---|
| 3 | 0,059 | 0,046 |
| 5 | **0,068** (el peor) | **0,031** (el mejor) |
| 8 | 0,064 | 0,041 |

**El orden se invierte entre tiradas.** Con 6 enanos y una muestra por configuración, la
varianza entre ejecuciones supera la diferencia entre configuraciones. La métrica no
distingue 3 de 5 de 8; para que sirviera harían falta más enanos y repeticiones.

Lo que sí es consistente en ambas tiradas es la **riqueza**:

| Rasgos | Longitud media | Palabras distintas |
|---|---|---|
| 3 | 210 car. | 26 |
| 5 | 217 car. | 27 |
| 8 | **237 car.** | **29** |

**Decisión: 8.** Y conviene ser explícito en que **no lo sostiene la métrica de
solapamiento**, sino la riqueza medida más la lectura: a 8 rasgos aparece la duda sobre
uno mismo (*"me pregunto si no estaré demasiado apegado a Tobul"*) que a 3 no sale.

**Ninguno de los dos temores era cierto.** Ni un rasgo extremo secuestra la salida con 8,
ni bajar a 3 homogeneiza. La variedad no sale de los rasgos: sale de las relaciones, el
oficio, el estrés y el suceso que dispara.

---

## Coherencia entre enanos, sin haberla programado

Dos enanos casados entre sí, del mismo lote:

> **Tirist**: *"Cada vez que pienso en **Tosid** me invade una calidez tranquila, aunque
> a veces esa misma calma me hace preguntarme **por qué no soy más efusivo con ella**"*

> **Tosid**: *"**Tirist** merece algo mejor que **un marido que se queda mirando el
> vacío** después de cada guardia"*

Cada uno reflexiona sobre la misma relación desde su lado y coinciden en el diagnóstico.
No hay nada en el código que lo produzca: sale de que ambos leen el mismo
`relationship_ids`.

---

## Memoria

Historial por enano en JSON, podado a las últimas 12 intervenciones, con **tres
protecciones contra la atribución equivocada**, que es un fallo que no da error:

1. Un fichero **por partida**
2. **Huella** por enano; si no cuadra, el historial se descarta y se anota
3. **Escritura atómica** (`os.replace`)

Cada entrada lleva `participantes: [claves]` desde el principio, con la entrada duplicada
en la ficha de cada implicado. Hoy siempre es uno; el día que dos enanos interactúen,
ambos lo recuerdan sin migrar el fichero.

**Defecto encontrado ejercitando el módulo, no leyéndolo**: consultar el historial creaba
una ficha vacía, así que el bucle habría llenado el fichero con 64 registros en blanco.

---

## Lo que queda sin probar

- **Muertes reales.** `isKilled` está implementado pero nunca se ha disparado.
- **Partidas largas.** Todo lo medido son minutos. Falta ver si la crónica se vuelve
  repetitiva en horas, si el descanso de 15 min por enano es el adecuado, y cómo crece
  la memoria.
- **Interacciones entre enanos.** La estructura está preparada; la lógica no existe.

## Decisiones abiertas

1. **Ritmo.** Los frenos actuales son una conjetura razonable, no una medición.
2. **Acciones en el juego.** La frontera `decidir`/`ejecutar` está lista para ello.
3. **Dependencia de Player2**: sin clave, pero exige la app abierta y con sesión.

---

`HALLAZGOS.md` es el registro largo con todas las mediciones y citas a fichero y línea.
`CLAUDE.md` recoge los invariantes y las trampas de API en forma operativa.
