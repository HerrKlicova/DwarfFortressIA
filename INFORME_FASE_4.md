# Fase 4 — De «funciona» a «es fiable»

Informe para consulta externa. Continúa `INFORME_FASE_3.md`.
Rama `claude/stoic-ride-2xec00`, 23 commits, ~2 800 líneas.

**Resumen en una frase:** el puente ya funcionaba; esta fase ha ido de descubrir que
producía texto convincente construido sobre datos rotos, y de cerrar esa brecha uno a uno
contra el juego real.

---

## 1. El patrón que domina la fase

**Prosa buena tapando datos malos.** Apareció **seis veces** con seis caras distintas.
Todas compartían la misma forma: la salida sonaba perfecta, así que nadie miraba el
prompt.

| Defecto | Lo que se leía | Lo que había debajo |
|---|---|---|
| Epitafio vacío | *"Tosid ha muerto. Se acabó todo."* | El prompt decía `sin oficio, de ? anos` y sin relaciones |
| Emociones | narración correcta | `Lo que has sentido: anything none; anything none; …` ×6 |
| Captions | *"¡Euphoria! ¡Qué alegría!"* | `due to [syndrome]` con el corchete tratado como texto |
| Género | *"esposo de Tosid"* | El sexo no estaba en el prompt: el modelo tiraba una moneda por frase |
| `[skill]` | *"mejoré esa planta"* | `upon improving plant` — inglés correcto, significado equivocado |
| Relación | *"he perdido a mi madre"* | El detalle decía el nombre y **nada** sobre el parentesco |

**La inflexión fue un flag de tres líneas.** `hablar --prompt` imprime el prompt entero
antes de llamar al modelo. Antes de existir, juzgábamos por la salida; después, por la
entrada. **Cinco de los seis defectos se encontraron leyendo el prompt**, no la respuesta.

> Recomendación transferible: en cualquier sistema con LLM, poder ver el prompt exacto que
> se envió no es una comodidad de depuración. Es el instrumento de medida.

---

## 2. Categorías de invención, separadas

Lo que empezó como «el modelo se inventa cosas» resultó ser **cuatro mecanismos
distintos**, y solo uno es del modelo.

1. **Hueco vacío.** El prompt no dice cómo murió → *"murió mientras trabajaba en las
   minas"*. Se ataja prohibiéndolo explícitamente **y**, mejor, cerrando el hueco.
2. **La costura.** Dos hechos ciertos unidos en un tercero falso. `delight after watching
   a performance` + `sadness at being separated from a loved one` → *"la última función
   que **vimos juntos**"*. **La regla «cada afirmación con su ancla» no la caza**: el
   modelo puede señalar dos campos reales. Freno específico: se le permite mezclar
   *sentimientos*, se le prohíbe fabricar *sucesos* —con quién, dónde, antes o después—.
3. **Frase mal montada.** El dato es correcto y la frase dice otra cosa.
   `upon improving [skill]` + `plant` = `upon improving plant`, que en inglés significa
   «mejoré una planta». **No es invención: el modelo lo leyó bien.**
4. **Hueco al lado de un ancla.** El caso más instructivo. Detalle: `has perdido a Stukos
   Ralfash de tu vida`. Respuesta: *"he perdido a **mi madre**"* — **tiró el nombre y
   rellenó el parentesco**. Ante un dato cierto y un hueco contiguo, **el hueco tira más**.

**Medición (regla 5), 10 respuestas al mismo prompt:**

| | escenas fabricadas | fuera de personaje |
|---|---|---|
| Sin freno | 1 de 1 | — |
| Con el freno de la costura | 0 de 10 | 0 |
| + memoria en prosa | 0 de 10 | **1 de 10** |
| + memoria por temas + filtro de salida | 0 de 10 | 0 |

Con n=10 y tres tiradas dentro del ruido, **no se declara tendencia** más allá del
contraste con el «sin freno».

---

## 3. Dos piezas de arquitectura nuevas

### Filtro de salida

Con la memoria encendida, el modelo escribió dos frases correctas y detrás:

> *"(Lo siento, pero no puedo continuar con este roleplay porque repetí frases que ya
> habías dicho antes, lo cual va en contra de tus instrucciones.)"*

En seco no pasó nada. **Con el servicio en marcha, eso se anuncia dentro del juego como si
lo hubiera dicho el personaje.**

Todo lo construido hasta entonces vigilaba lo que *entraba* al prompt. Nada miraba lo que
*salía*. `limpiar_meta()` trabaja **por frases**: salva las buenas, tira la coletilla, y
apunta lo recortado en vez de tragárselo. Si no queda una frase entera, no se anuncia.

> **Ningún prompt es infalible.** Un sistema que solo se defiende por instrucciones está
> apostando a que el modelo siempre obedezca.

### Memoria por temas, no por prosa

`para_prompt()` pegaba las tres respuestas anteriores **enteras** y decía *"NO las
repitas"*. Medido: **aumenta la repetición** —cuatro de diez respuestas compartían una
frase palabra por palabra, y una era copia literal de otra— y la prohibición es imposible
de cumplir cuando solo hay una forma de decir algo, así que el modelo **se sale del
personaje para disculparse**.

Es la misma lección que ya teníamos escrita sobre una muletilla (*nombrar la palabra la
vuelve a meter en el contexto*), repetida con párrafos enteros en otro fichero. Ahora se
manda el **tema** y se pide **avanzar**, no se prohíbe.

> Escribir la regla no basta para cumplirla.

---

## 4. Dos canales de salida

El número que lo forzó: con 128 ciudadanos, el servicio narraba **una vez cada 20 s sin
parar** —unos 540 anuncios en tres horas— compitiendo con los avisos del propio juego.

Firmas verificadas en `docs/dev/Lua API.rst` antes de escribir código:
`showZoomAnnouncement(type, pos, text[, color[, is_bright]])` y `writeToGamelog(text)`
*«sin hacer un anuncio»*. La segunda era la pieza que faltaba.

| Canal | Contenido | Destino | Descanso |
|---|---|---|---|
| **noticia** | muerte, locura, desaparición, relación | panel, **con posición** (la cámara salta al personaje), color propio | 10 s |
| **ambiente** | emociones, ánimo, llegadas | gamelog + crónica, **no interrumpe** | 90 s |

Lo que de verdad arregla el descanso por canal no es la frecuencia: es que **con uno solo
global, cualquier suceso bloqueaba a cualquier otro**. Una muerte esperaba detrás de un
cambio de humor. Verificado con las cuatro combinaciones.

---

## 5. Hechos nuevos sobre DF/DFHack

- **Las captions de `unit_thought_type` son plantillas**, no texto: `after [varying]`,
  `upon improving [skill]`, `near a [quality] [building]`. Quitar los corchetes y dejar la
  palabra dentro es tratar un hueco como contenido.
- Quien rellena el hueco es **`subthought`**, y su significado **depende del tipo de
  pensamiento**. No hay API que componga el texto: `getThoughtText`,
  `getThoughtDescription` y `getUnitThought` **no existen** en 53.16-r1.1 (comprobado
  preguntando a la build).
- **Que un `subthought` «resuelva» no prueba nada.** Los ids de figura histórica son
  densos: `find(n)` devuelve un nombre para casi cualquier número. Solo se rellena un
  hueco cuando **cuadra con otro dato del mismo personaje** —la emoción de la fila, una
  habilidad que tiene, el mismo valor repetido en pensamientos hermanos—. `SawDeadBody`
  no cumple eso y **se deja sin rellenar**; en producción, dos personajes narraron haber
  visto un cadáver sin inventar un nombre.
- **La posición en `relationship_ids` ES el tipo de vínculo.** Comparando posición a
  posición entre dos sondeos se sabe qué id entró o salió **y de qué tipo era**.
- **`unit.sex` leído como `pronoun_type` se nombra a sí mismo**: devuelve `she`/`he`, no
  un 0 y un 1 que haya que mapear de memoria. Verificado de punta a punta contra la ficha
  del personaje en el juego.
- **CP437 no tiene `Á Í Ó Ú`** (sí las minúsculas, `ñ Ñ É ü ç ¿ ¡`). Medido el ida y
  vuelta de los 15 caracteres del español en la build: sobreviven 11.
- **`world.status.reports`**: 2 004 entradas en un año, dominadas por finalización de
  trabajos. Como fuente de sucesos exige filtrar por tipo.
- **No hace falta un plugin en C++**: `plugins.overlay`, `plugins.eventful`,
  `repeat-util`, `getCurFocus`, `getSelectedUnit` y `getWidget` están todos presentes.

---

## 6. Estado y dirección

**Funciona y está medido contra el juego:** extracción, detección de 8 tipos de suceso,
selección por peso y canal, prompt con género y huecos resueltos, filtro de salida,
memoria por partida con huella, crónica fechada, y entradas de memoria con **dos
participantes** —el gancho que esperaba a las interacciones, enchufado sin código nuevo—.

**Lo que falta, por orden de valor:**

1. **Petición explícita.** `keybinding` atado a un *focus string* + `getSelectedUnit()`:
   abres la ficha de un personaje, pulsas una tecla, habla ese. Invierte el control y
   hace barata cualquier prueba futura.
2. **La prosa de la ficha.** `view_sheets` guarda descripción física, personalidad y
   pensamientos **en prosa escrita por el juego**. Solo se rellena tras abrir la pestaña
   —y no hay API que la genere—, así que para un servicio automático está descartado;
   **para la petición explícita es gratis**, porque el jugador ya está en esa pantalla.
3. **Conversaciones.** El duelo por una pareja existe como emoción propia
   (`SADNESS / at being separated from a loved one`), y la entrada de memoria ya guarda a
   los dos implicados. Falta el intercambio.
4. **Sesión larga de 2-3 h**, que es la que decide si el ritmo aguanta.

**El objetivo declarado es distribuirlo dentro de Player2 como integración
seleccionable**, y eso abre **una decisión de arquitectura que conviene tomar pronto**:

> Hoy el servicio es un proceso Python que el usuario arranca a mano. Player2 expone su
> API en **HTTP plano sobre localhost** (`127.0.0.1:4315`), no HTTPS — y `luasocket`, el
> plugin de DFHack, habla TCP en claro. **Eso significa que el lado Lua podría hablar con
> Player2 directamente, sin Python y sin segundo proceso.**

Tres caminos, con costes distintos:

| | Ventaja | Coste |
|---|---|---|
| **Todo en Lua** | Se instala como mod de DFHack. Sin Python, sin proceso aparte, sin que el jugador arranque nada | Portar prompt, memoria, filtros y canales a Lua. Y la llamada al LLM (0,7 s) **no puede bloquear el bucle del juego**: exige socket no bloqueante y sondeo |
| **Python congelado** (.exe) | Se conserva todo lo escrito y medido | DFHack **no puede lanzar programas externos** desde Lua, así que alguien tiene que arrancarlo: el jugador, o Player2 si su integración lo permite |
| **Híbrido** | Lua hace lo barato y sensible al bloqueo; Python lo demás | Dos piezas que distribuir y sincronizar |

**Pregunta abierta al consultor:** ¿qué sabe de cómo Player2 registra e inicia una
integración de juego? Nuestro cliente ya manda la cabecera `player2-game-key`, lo que
sugiere que existe un registro por juego, pero **no hemos verificado el proceso** y no se
va a suponer.

---

## 7. Lo que costó, y por qué

Unas **quince tandas de prueba** en la sesión, todas ejecutadas por el usuario porque
**nadie del lado del desarrollo puede ejecutar el juego**. Desglose honesto:

- **≈7 inevitables.** Cada una respondía una pregunta que solo el motor podía responder.
- **≈4 por errores de quien escribe el código.** Documentar un hecho apoyándose en prosa
  generada; rellenar un hueco sin leer la frase montada; arreglar el expediente y no la
  sonda de al lado; dar el nombre de una relación y no su tipo.
- **≈3 por insistir en afinar una métrica que ya estaba dentro del ruido.** Cinco tiradas
  de 10 respuestas cuando las tres últimas no distinguían nada.

**Contramedida propuesta:** una orden `comprobar` que ejecute toda la batería de una vez
—prompt completo, epitafio, sondeo de interfaz, filas crudas de emociones, una vuelta del
servicio— y devuelva una tabla de pasa/falla. Convierte cinco idas y venidas en una.
