# DF ↔ LLM local — reglas del proyecto

Puente entre Dwarf Fortress y un LLM que corre en la misma máquina. El lado Lua vive
dentro del juego y **extrae estado**; el lado Python es el **servicio** que decide qué
hacer con él. El transporte es el RPC `RunCommand` de DFHack sobre TCP.

Entorno de referencia: DF 53.16 (Steam), DFHack 53.16-r1.1, Player2, Windows, Python 3.

---

## Invariantes

Cada uno de estos costó una ejecución fallida. No son preferencias de estilo.

### 1. Los contenedores de DFHack son 0-indexados

Las tablas de Lua empiezan en 1; los vectores y arrays de DF **no**, y **lanzan error
al salirse** en vez de devolver `nil`. Con `#v == n`, los índices válidos son `0..n-1`.

```lua
for i = 0, #vec - 1 do ... end          -- vector de DF
for i, x in ipairs(tabla_lua) do ... end -- tabla que devuelve getCitizens()
```

Las dos cosas conviven en el mismo bucle: `getCitizens()` devuelve una tabla Lua
1-indexada cuyos elementos contienen vectores DF 0-indexados. No mezclarlos.

> Fuente: `docs/dev/Lua API.rst:250` — *"using a 0-based numerical index"*.
> Síntoma: `Cannot read field vector<...>.N: index out of bounds`.

### 2. `df2utf` solo sobre cadenas que vienen de DF, nunca sobre los delimitadores

DF trata los bytes `0x00–0x1F` como **glifos dibujables**, no como caracteres de
control. `df2utf` convierte el tabulador `0x09` en `○`. Pasar una línea entera por esa
función destroza cualquier separador propio.

```lua
out('NOMBRE|' .. dfstr(nombre))   -- correcto: solo el nombre se convierte
out(dfstr('NOMBRE|' .. nombre))   -- MAL: el separador acaba siendo ○
```

Lo mismo al escribir: `utf2df` sobre el texto que va a DF, y solo sobre él. El códec
`cp437` de Python **no** coincide con la tabla de DF: mapea `0x09` a tabulador.

> Fuente: `library/MiscUtils.cpp:573` — `character_table[9] = 0x25CB`.

### 3. Nunca formatear números en Lua con `%f`

`string.format` de Lua pasa por el `printf` de C, que **respeta el locale**. En un
Windows en español devuelve `0,0020` con coma decimal y el `float()` de Python lo
rechaza. Los enteros con `%d` no se ven afectados.

```lua
out('LUA_US|' .. string.format('%d', math.floor(seg * 1000000)))  -- microsegundos
out('LUA_SEG|' .. string.format('%.4f', seg))                     -- MAL: coma decimal
```

Regla general de la que este caso es un ejemplo: **Lua y Python no comparten
convenciones**. Cada vez que cruza un dato entre los dos hay que fijar el formato
explícitamente — texto, números y separadores.

### 4. Un diagnóstico nunca adivina la causa si hay respuesta que leer

DFHack devuelve un traceback con fichero y línea cuando un script falla. Un mensaje
que ignore eso y proponga una causa inventada manda a mirar al sitio equivocado.

```python
if err is not None:
    print("DFHack devolvio error %d" % err)
    for linea in salida.splitlines():      # primero, lo que dijo el motor
        print("  | " + linea)
    if "stack traceback" in salida:
        print("-> error DENTRO del script lua; la linea del traceback dice donde")
    elif not salida.strip():               # solo si NO hay nada que leer
        print("-> sin salida: revisa que el .lua este en dfhack-config/scripts/")
```

---

## Cómo se prueba aquí

**Contra el juego real, no contra un DFHack falso.** Durante el spike se montaron
servidores de prueba que imitaban el protocolo, y **tres de las cuatro trampas se
colaron igual**: los stubs reproducían nuestras suposiciones (tabuladores tal cual,
tablas 1-indexadas) en vez del comportamiento del motor.

Un stub sirve para el framing del protocolo, que es una especificación escrita. No
sirve para indexación, codificación de texto ni reglas del juego. Eso se ejercita
contra DFHack o no se ha probado.

**Y al editar código: comprobar que la rama es ALCANZABLE, no que la constante existe.**
Una sustitución de texto puede no encajar en silencio. Pasó con la narración en tercera
persona: `EN_TERCERA` estaba definido, el fichero compilaba, y la rama no se usaba en
ninguna parte. Verificar lo fácil en vez de lo que importa da una falsa sensación de
haber probado.

---

## Qué puede inventar el modelo y qué no

Esta sección se escribe **antes** de construir las interacciones, no después de que
fallen. Es la única regla del fichero que no viene de una ejecución fallida, y está aquí
porque el riesgo crece con cada cosa que le dejamos hacer al modelo: hablar solo tiene
una superficie de invención pequeña; hablar con otro, o actuar, la multiplica.

### La distinción de fondo: textura contra afirmación

Que un enano diga *"noto el frío de la piedra en las manos"* no es una invención: es
**textura**, no añade nada al mundo. Que diga *"mi hermano murió el invierno pasado"* sí
lo es: es una **afirmación** sobre hechos del mundo, y o sale del estado de DF o es
falsa. Un texto convincente y falso es peor que uno soso y cierto, porque el jugador no
tiene forma de distinguirlo.

Ya pasó dos veces con distinta cara: `Unidad 338` —un valor de relleno mío— acabó
convertido en un personaje con vida propia, y un niño narró que estaba minando cuando
los niños no pueden minar. Ninguna de las dos fue culpa del modelo: le dimos permiso.

### Las cinco reglas

1. **Cada frase que afirma algo tiene que tener un ancla visible en el prompt.** Si no
   se puede señalar el campo del que sale, no puede estar en el texto.
2. **Ningún valor de relleno llega al prompt.** Si un dato no se resuelve, se omite. No
   hay sustituto neutro: el modelo convierte cualquier cosa en material narrativo.
3. **Una escena entre dos enanos se BUSCA en los datos, no se inventa.** Nunca poner a
   dos modelos a charlar a ver qué sale. Primero se localiza el suceso que los dos ya
   comparten (misma causa con marcas de tiempo cercanas, o un vínculo real en
   `relationship_ids`) y el intercambio se genera **desde ahí**. Sin escena encontrada
   no hay conversación.
4. **Cuando el LLM pueda actuar, la salida es una lista cerrada, no texto libre.** El
   modelo elige entre acciones enumeradas y el código valida cada una contra el estado
   real antes de ejecutarla. Que una acción sea plausible en la frase no la hace posible
   en el juego.
5. **La tasa de invención se mide en cada escalón, no se supone.** Cada vez que se
   amplía lo que el modelo puede hacer: N respuestas, contar cuántas afirman algo que no
   está en el prompt, y anotar el número en `HALLAZGOS.md`. Sin ese número no se pasa al
   escalón siguiente.

### El corolario que ya nos ha costado tiempo

Las reglas 1 y 2 son de **construcción del prompt**, y ahí es donde han aparecido
**cuatro de los cinco** defectos de calidad del texto: el niño minero, las cartas a la
esposa, las cifras recitadas y la muletilla del «mientras». Antes de culpar al modelo de
un texto malo, leer el prompt entero que se le mandó. La probabilidad a priori dice que
el fallo está ahí.

---

## Hechos ya verificados (no hace falta volver a medirlos)

| Hecho | Dato |
|---|---|
| Interfaz remota de DFHack | Activa por defecto en `127.0.0.1:5000`, sin configurar nada |
| `RunCommand` | ID fijo **1**; no hace falta `BindMethod` ni generar protobuf |
| Scripts como comandos | Cualquier `.lua` en `dfhack-config/scripts/` es un comando |
| Coste de leer 20 enanos | **2 ms** en el lado Lua (0,2 frames a 98 FPS): no es el cuello de botella |
| Latencia del LLM | 0,49 s con prompt de 153 car.; **0,95 s** con prompt de 4 887 |
| Con el juego en pausa | `RunCommand` responde normal, no se cuelga ni encola |
| Descargar y recargar partida | **El mismo socket sobrevive**. Solo hay que comprobar `mundo`/`mapa` antes de tocar unidades |
| Cerrar DF | `ConnectionResetError` limpio y capturable |
| `unit_id` al guardar y recargar | **Estable**: 64 de 64 siguen apuntando al mismo enano. `hist_figure_id` también, y ninguno vale `-1` |
| Reutilización de `unit_id` | `unit_next_id` es un contador secuencial (5486, con el id más alto en 5483 y 5166 huecos). Indicio fuerte de que no se reciclan; aun así la memoria guarda huella |
| Rasgos en el prompt | **8**. Medido: el numero no afecta a que suenen distintos (la metrica se invierte entre tiradas), pero si a la riqueza (26 → 29 palabras distintas de 3 a 8) |
| Carpeta de guardados | En la version de Steam **NO** esta bajo la carpeta del juego: `%APPDATA%\Bay 12 Games\Dwarf Fortress\save` (verificado en esta instalacion). **`dfhack.getSavePath()` no devuelve esa ruta**, asi que no sirve de atajo: usar la de arriba |
| Interfaz de DFHack en esta build | **Todo presente y medido**: `plugins.overlay`, `plugins.eventful`, `repeat-util`, `gui.widgets`; y en `dfhack.gui` están `showZoomAnnouncement`, `showPopupAnnouncement`, `getCurFocus`, `getFocusStrings`, `getSelectedUnit`, `getWidget`. **No hace falta un plugin en C++** |
| CP437, ida y vuelta | Medido con los 15 caracteres del español: sobreviven 11; **`Á Í Ó Ú` vuelven como `?`**. El resto (`á é í ó ú ñ Ñ ü É ¿ ¡`) intactos |
| `world.status.reports` | 2004 anotados en el año 105, y el último es `Make yarn trousers (6) has been completed`. **Está dominado por finalización de trabajos**: usarlo como fuente de sucesos exige filtrar por tipo, no drenarlo entero |
| Coste de sondear los 64 | `sonda` 15 KB y 5 ms · `basico` 9 KB y 1 ms · `completo` **344 KB y 40 ms** (≈4 frames congelados) |

### Trampas de las APIs

- **`showAnnouncement` ignora el `\n`.** Un texto con saltos sale como un solo anuncio
  en una línea. Para varias líneas, **una llamada por línea**.
- **`json.encode` de DFHack usa `pretty=true` con tabulador por defecto.** Pasar
  siempre `{pretty = false}`.
- **Player2 devuelve `422`, no el `400` que promete su OpenAPI**, cuando la entrada no
  deserializa.
- **Player2 devuelve `500` con `messages: []`.** Es determinista: reintentarlo no
  arregla nada. No enviar nunca la lista vacía.
- **El RPC `RunLua` no sirve**: filtra por nombre de módulo con la condición invertida.
- **El RPC `ListUnits` tampoco**: da nombre y oficio, pero ni rasgos, ni pensamientos,
  ni relaciones. Todo eso solo se alcanza desde un script Lua.
- **Nunca sondear con `detalle=completo`.** Son 344 KB y 40 ms de Lua por vuelta: casi
  cuatro frames congelados. Para eso está `detalle=sonda`.
- **Todo texto de DF pasa por `df_llm.legible()` antes del prompt.** Es un punto de paso
  obligatorio, no una recomendación: detecta forma de identificador de máquina, lo
  humaniza y **apunta la fuga en `df_llm.FUGAS`** para que se vea qué camino se saltó la
  traducción, en vez de taparlo. **Se puso porque el invariante escrito no bastó**: la
  misma clase de fallo apareció tres veces en caminos distintos.
  Detecta **solo por forma** (`ALL_CAPS`, `CamelCase`, guiones bajos). Hubo una versión
  que además marcaba como sospechoso cualquier palabra suelta en los campos de enum
  (`siempre_enum=True`) y fue un error: `bravery`, `spouse`, `Crossbow` son la salida
  **correcta** de `enum_txt()`, y las marcaba como fuga — veinte avisos falsos por
  narración. El parámetro sigue en la firma por compatibilidad y **ya no cambia nada**;
  no escribir código nuevo que dependa de él. El precio de la vuelta atrás es que
  `Syndrome` es indistinguible de `Crossbow` por forma: ese caso se ataja en origen, con
  `enum_txt()` en el lado Lua.
  > Un guardián que grita lobo veinte veces por vuelta deja de ser un guardián: se
  > aprende a ignorarlo, y con él se ignora la fuga de verdad.
- **Hay sucesos que el propio enano no puede narrar.** Muerte, locura y desaparición van
  en **tercera persona** por `construir_epitafio()`. Con el prompt normal (*"esto es lo
  que acaba de pasarte"*) el difunto decía *"se acabó, todo se acabó"* en presente.
- **Cuidado con las palabras sueltas de la instruccion.** *"Hablas para ti mismo
  mientras trabajas"* hizo que 14 de 18 respuestas empezaran por *"Mientras..."*. Y no se
  arregla diciendo «no uses esa palabra»: nombrarla la vuelve a meter en el contexto.
- **Ningun valor de relleno debe llegar al prompt.** `unidad 338` (el sustituto de un
  pariente no resuelto) acabo convertido en un personaje. Si un dato no se puede
  resolver, se omite.
- **Nunca meter cifras crudas en el prompt.** El modelo las recita tal cual (*"mi estrés
  sigue en 11440"*). Dar siempre la lectura, no el valor.
- **Las captions de DF están en inglés y en tercera persona** (`pleasure near his own
  quality building`) y traen huecos entre corchetes. Hay que decirle al modelo que son un
  apunte del juego y que no las traduzca literalmente.
- **El `desde=` de `enanos` da la vuelta, no se acaba.** El índice se calcula con
  `((desde + k) % #pool) + 1`, así que pedir 20 sobre una lista de 5 devuelve 20
  repitiendo. Cualquier paginación que espere una página corta para saber que ha
  terminado **no termina nunca**: hay que contar contra `total_pool`.
- **`relaciones` trae el nombre del otro, no su `id`.** `relationship_ids` sí es un
  vector de `unit_id`, pero el lado Lua lo resuelve a `{tipo, txt, quien}` y tira el
  número. Para cualquier cosa que implique a dos enanos (una conversación, una entrada
  de memoria compartida) hace falta ese `id`: es el único cambio de extracción que
  requiere la fase de interacciones.
- **CP437 no tiene `Á Í Ó Ú`.** Sí tiene las minúsculas acentuadas, `ñ Ñ É ü Ü ç Ç ¿ ¡`.
  `utf2df` sustituye por `?` lo que no puede mapear, así que una frase del modelo que
  empiece por *"Últimamente"* o nombre a *"Ángeles"* sale con un interrogante en el juego.
  El lado Lua las degrada a `A I O U` antes de anunciar.
  **Nuestra propia comprobación de acentos dio verde sin tocar este caso**: solo probó
  minúsculas (`î ê` al leer, `ú ó` al escribir). Un ✅ sobre una muestra que no incluye el
  caso difícil no es una comprobación, es una coincidencia.
- **La cola del vector `personality.emotions` viene vacía.** DF poda las emociones viejas
  y deja entradas con `type` y `thought` a `-1`, cuyas captions son `anything` y `none`.
  Coger las últimas N del vector daba seis `anything none` seguidos en el prompt de un
  enano real. Hay que **ordenar por `(year, year_tick)` y descartar las que tengan los dos
  campos negativos**, que es lo que `enano_sonda()` ya hacía sin decirlo.
  > No se notó porque la sonda sí elegía bien, así que el disparador del suceso llegaba
  > correcto y la respuesta sonaba bien. Lo estropeado era el bloque de contexto. Es el
  > mismo patrón que el epitafio vacío: **prosa buena tapando datos malos**.
- **El español marca género en casi cada frase, y sin el dato el modelo lo inventa.**
  Con Tirist Atírshis y su cónyuge Tosid, sin sexo en el prompt, el modelo se contradijo
  **entre dos tiradas del mismo par**: en una Tosid se llamó a sí mismo *"un marido"*, en
  otra Tirist habló de él diciendo *"volver a **verla**"*, y la crónica escribió
  *"**esposo** de Tosid"*. No es que acierte o falle: es que **tira una moneda cada vez**,
  y una moneda distinta por frase.
  Hace falta el de **los dos**: el del que habla y el de cada relación —`spouse` no dice
  si es esposo o esposa, y el oficio viene en inglés, que tampoco lo marca. Va explícito
  (*"Eres MUJER: habla de ti en femenino"*), no implícito: decir el dato sin pedir la
  concordancia no basta.
  Y el castellano **nuestro** también cuenta: *"te sientes muy tranquilo"* era masculino
  para toda la fortaleza. Las frases propias van en **sustantivos**, no en adjetivos
  (*"por dentro sientes una gran calma"*).
  El sexo se saca de `unit.sex` leído como **`pronoun_type`**, que es un enum que **se
  nombra a sí mismo**: devuelve `she` o `he`, no un 0 y un 1 que haya que mapear de
  memoria. El camino usado va en `sexo_via` para poder verlo desde fuera, y cuando hay que
  suponer algo, la cadena lo dice.
- **No usar prosa generada como si fuera un dato.** Al documentar lo anterior escribí
  *"Tirist es mujer"* — y mi única fuente era **una frase que el propio modelo había
  escrito sin tener el dato**. Es circular: se toma la moneda del modelo por evidencia y
  se convierte en un hecho del proyecto. Un hecho sobre el mundo del juego solo vale si
  sale del estado de DF o de la pantalla del juego.
  > La regla 1 de la doctrina —cada afirmación con su ancla— **también se aplica a lo que
  > escribimos nosotros**, no solo a lo que escribe el modelo.
- **Si el prompt no dice cómo pasó algo, el modelo lo rellena.** El epitafio decía solo
  *"ha muerto"* y el cronista escribió *"murió **mientras trabajaba en las minas**"* —y
  luego añadió *"sin que se conozcan más detalles"*, después de haber dado el sitio por
  cierto en la primera frase. Hay que **prohibirlo explícitamente**: *"no inventes dónde
  ni cómo ocurrió; si no está escrito, no se sabe"*. Es la regla 1 de la doctrina, y el
  hueco de información es lo que la dispara.
- **La memoria se guarda por partida.** Los `unit_id` vuelven a empezar en cada mundo,
  así que sin separar por `dfhack.world.ReadWorldFolder()` el enano 272 de una fortaleza
  heredaría los recuerdos del 272 de otra.
