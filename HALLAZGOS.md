# HALLAZGOS — spike Dwarf Fortress ↔ LLM local

Entorno objetivo: Dwarf Fortress 53.16 (Steam), DFHack 53.16-r1.1, app de escritorio
Player2, Windows, Python 3.14.7.

## Estado: ✅ el tubo existe y funciona de punta a punta

Ejecutado con éxito el 2026-09-09 contra la instalación real: Dwarf Fortress 53.16
(Steam), DFHack 53.16-r1.1, Player2 `0.10.78`, Windows, Python 3.14.7.

Los tres pasos pasaron: se leyó un enano vivo de la fortaleza, se llamó al LLM local
y su respuesta apareció en el log de anuncios del juego. Latencia del LLM: **0,53-0,64 s**.

Sin preguntas abiertas: los acentos funcionan en las dos direcciones, el contexto real
llega al prompt y la respuesta es coherente con los datos del enano.

**Veredicto: sí, se puede. Adelante con lo demás.**

---

## 1. ¿Está activa la interfaz remota de DFHack? ✅ Sí, por defecto

No hay que habilitar nada. En `library/Core.cpp`, dentro de la inicialización, el
arranque del listener es incondicional:

```cpp
std::cerr << "Starting the TCP listener.\n";
auto listen = ServerMain::listen(RemoteClient::GetDefaultPort());
```

| Cosa | Valor |
|---|---|
| Dirección | `127.0.0.1` |
| Puerto | `5000` |
| Config | `<DF>/dfhack-config/remote-server.json` |
| `allow_remote` | `false` por defecto → solo localhost. **Déjalo así.** |
| `port` | `5000` por defecto |
| Prioridad | La variable de entorno `DFHACK_PORT` gana sobre el JSON |

En `RemoteServer.cpp` el bind es `allow_remote ? NULL : "127.0.0.1"`, y hay un
filtro por método en la línea 339: el flag `SF_ALLOW_REMOTE` **solo** restringe a
clientes cuya IP no sea `127.0.0.1`. `RunCommand` no lleva ese flag, pero como
conectamos desde la misma máquina da igual.

**Cómo confirmarlo:** abre `stderr.log` en la carpeta de DF y busca
`Listening on port 5000`. Si el puerto estuviera ocupado, el error saldría ahí.

## 2. ¿Qué expone realmente sobre unidades? ✅ Depende del camino, y la diferencia es grande

Hay dos vías, y conviene no confundirlas.

### Vía A — RPC nativo `ListUnits`: solo datos básicos

Confirmado en `library/RemoteTools.cpp:681` (`addFunction("ListUnits", ListUnits, SF_ALLOW_REMOTE)`),
con los mensajes definidos en `library/proto/BasicApi.proto` y `library/proto/Basic.proto`.
Devuelve `BasicUnitInfo`:

| ¿Disponible? | Campo |
|---|---|
| ✅ | `NameInfo`: `first_name`, `nickname`, `last_name`, `english_name`, `language_id` |
| ✅ | `unit_id`, `pos_x`, `pos_y`, `pos_z` |
| ✅ | `race`, `caste`, `gender`, `civ_id`, `histfig_id` |
| ✅ | `flags1`, `flags2`, `flags3` (fixed32) |
| ✅ | `death_id`, `death_flags` |
| ✅ | `profession`, `custom_profession`, `squad_id`, `squad_position` |
| ✅ | `labors[]`, `skills[]` (id/nivel/experiencia), `misc_traits[]` — bajo `BasicUnitInfoMask` |
| ✅ | `curse` (`UnitCurseInfo`), `burrows[]` |
| ❌ | **rasgos de personalidad** |
| ❌ | **pensamientos / emociones** |
| ❌ | **relaciones** |

`ListUnitsIn` permite filtrar por `race`, `civ_id`, `dead`, `alive`, `sane`, o pedir
`id_list` concreto o `scan_all`.

**Conclusión: por RPC puro no llegas a nada de lo que le interesa a un LLM.**
Nombre y oficio, poco más.

### Vía B — script Lua propio invocado por `RunCommand`: acceso total

Aquí sí está todo, porque un script Lua ve las estructuras de datos completas.
Confirmado en df-structures (`df.unit.xml`, `df.soul.xml`, `df.personality.xml`):

| Ruta | Qué es |
|---|---|
| `unit.status.current_soul` | puntero a `unit_soul` (`df.unit.xml:2972`) |
| `…soul.personality.traits` | array indexado por `personality_facet_type` — **los rasgos** |
| `…soul.personality.emotions` | vector de `personality_moodst` — **los pensamientos** |
| `…soul.personality.values` | vector de `personality_valuest` — los valores |
| `…soul.personality.dreams` | vector de `personality_goalst` — las metas |
| `…soul.personality.stress` | `int32` |
| `…soul.preferences` | vector de `unit_preference` |
| `…soul.skills`, `…soul.mental_attrs` | habilidades y atributos mentales |
| `unit.relationship_ids` | array de 9, indexado por `unit_relationship_type` |

Cada `personality_moodst` (un "pensamiento") lleva:
`type` (`emotion_type`), `strength`, `thought` (`unit_thought_type`), `subthought`.

Es decir: **el material narrativo bueno existe, pero solo se alcanza por script Lua,
no por el RPC estructurado.** Eso condiciona cómo montes lo demás si el spike sale bien.

### Vía descartada — `RunLua` ⚠️

Existe (`RemoteTools.cpp:669`) y su firma es
`CoreRunLuaRequest{module, function, arguments[]} -> StringListMessage`.
Pero **no es un eval genérico**: solo llama funciones públicas de módulos cuyo
nombre pase un filtro. Leyendo `doRunLuaFunction`:

```cpp
if (module.substr(0,4) == "rpc.")
    valid = true;
else if ((module[len-4] == '.' || module[len-4] == '-') && module.substr(len-3) != "rpc")
    valid = true;
```

El mensaje de error dice *"Only modules named rpc.\* or \*.rpc or \*-rpc may be called"*,
pero la segunda rama exige que los últimos tres caracteres **no** sean `rpc`, así que
`mimodulo.rpc` y `mimodulo-rpc` en la práctica **no pasan el filtro**. La condición
está invertida respecto a lo que documenta. Camino frágil: descartado.

## 3. ¿Se puede inyectar texto en el log de anuncios? ✅ Sí

De la Lua API de DFHack (`docs/dev/Lua API.rst`):

```
dfhack.gui.showAnnouncement(text[,color[,is_bright]])
  Adds a regular announcement with given text, color, and brightness.
  The announcement type is always df.announcement_type.REACHED_PEAK,
  which uses the alert badge for df.announcement_alert_type.GENERAL.
```

También hay `showZoomAnnouncement(type,pos,text,…)` (permite elegir tipo y una
posición a la que hacer zoom desde el menú) y `showPopupAnnouncement(text,…)`
(ventana modal estilo megabestia; DF ignora el color salvo que metas `[C:n:0:b]`
al principio del texto).

**El mecanismo para llamarla desde fuera** es lo que hace que este spike funcione.
De la misma doc, sección *Scripts*:

> Any files with the `.lua` extension placed into the `hack/scripts` folder (or any
> other folder in your script-paths) are automatically made available as DFHack
> commands. The command corresponding to a script is simply the script's filename.

Así que `dfhack-config/scripts/dfhack_spike.lua` se convierte en el comando
`dfhack_spike`, y ese comando se invoca por RPC con `RunCommand`. El `print()` del
script vuelve al cliente como mensajes `CoreTextNotification` — es exactamente el
mecanismo que usa `dfhack-run`.

**Un solo método RPC resuelve lectura y escritura.** Por eso el cliente es tan corto.

**Codificación:** DF guarda las cadenas en CP437 y el socket lleva UTF-8. El script
usa `dfhack.df2utf()` al leer y `dfhack.utf2df()` al escribir (ambas documentadas).
Sin eso, los acentos y la ñ salen rotos en el anuncio.

## 4. Player2: endpoint y clave ✅ Sin clave, pero con sesión iniciada

| Cosa | Valor |
|---|---|
| Base | `http://127.0.0.1:4315` |
| Endpoint LLM | `POST /v1/chat/completions` |
| Salud | `GET /v1/health` → `{"client_version": "…"}` |
| Swagger | `http://127.0.0.1:4315/docs` |
| ¿API key? | **No** |
| Requisito real | Estar autenticado en la app de escritorio |
| Header opcional | `player2-game-key: <Game Client Id>` — marcado *"[For Game Developers Only]"* |

Dos avisos de la propia documentación que ahorran una tarde de depuración:

1. **Usa `127.0.0.1`, no `localhost`.** Textual: *"Because `http://localhost` doesn't
   work due to IPv6 conflicts, always use `http://127.0.0.1:<port>` directly."*
2. **El puerto puede no ser 4315.** Si está ocupado, la app coge otro y escribe el
   real en `%APPDATA%\game.player2.client\api.port` (texto plano). Ese fichero se
   borra al cerrar la app limpiamente, así que su presencia también indica que está
   corriendo. `spike.py` lo lee y cae a 4315 si no existe.

Cuerpo de la petición (estilo OpenAI):

```json
{"messages": [{"role": "user", "content": "..."}], "stream": false}
```

Campos opcionales: `max_tokens`, `temperature`, `response_format`, `tools`, `tool_choice`.
Respuesta: `choices[].message.content`.

Códigos de error que el script distingue: `401` (no has iniciado sesión),
`402` (sin créditos), `429` (demasiadas peticiones).

> ⚠️ **Salvedad sobre esta sección.** `player2.game` está bloqueado por el proxy de
> red desde donde se escribió el spike, así que la doc oficial no se pudo leer
> directamente. Lo de arriba sale del OpenAPI completo de Player2, leído desde una
> copia espejo en un repositorio de terceros. Es coherente y detallado, pero es una
> instantánea que podría estar desfasada. Por eso `spike.py` hace primero un
> `GET /v1/health`: si esa llamada responde, la base y el puerto están confirmados
> en vivo contra tu instalación.

---

## Decisión: no se usó `dfhack-client-python`

Se pidió esa librería. Tras leerla entera, se descartó por tres motivos concretos:

1. **No está en PyPI** (comprobado: la API de PyPI devuelve 404). Su `CMakeLists.txt`
   apunta a `../dfhack/library/proto`, o sea que necesitas clonar las **fuentes** de
   DFHack más `cmake` y `protoc` para generar los `_pb2.py`. En Windows eso es media
   tarde antes de escribir la primera línea útil.
2. **Es asyncio puro** (`async def connect()`, `await` por todas partes), y el
   encargo pedía explícitamente nada de asincronía.
3. **Para este camino solo hacen falta dos mensajes protobuf triviales**:
   `CoreRunCommandRequest` (un string y un repeated string) y `CoreTextNotification`
   (un repeated con un string dentro). Codificarlos a mano son unas 25 líneas.

Además, `RunCommand` tiene **ID fijo 1** según la tabla de mensajes built-in de
`docs/dev/Remote.rst`, así que ni siquiera hace falta `BindMethod`. El cliente
completo cabe en ~60 líneas sin una sola dependencia.

De paso, dos defectos menores del cliente oficial que confirman la decisión: lee el
tamaño de cabecera con `int.from_bytes(h[4:7])` (tres bytes en vez de cuatro) y
envía cuatro bytes de más en el mensaje de `quit`.

## Qué se probó y qué no

**Probado ✅** — el cliente se validó contra dos servidores de prueba que implementan
el protocolo documentado:

- Handshake `DFHack?\n` / `DFHack!\n` con versión 1
- Framing de cabecera: `int16` id + `int16` padding + `int32` size, little-endian
- Codificación de `CoreRunCommandRequest` byte a byte contra la salida esperada
- Parseo de `CoreTextNotification` con varios fragmentos y con campo de color
- Respuesta `RESULT` con `EmptyMessage` de cero bytes
- Respuesta `FAIL`, donde el campo `size` se reutiliza como código de error **con signo**
- Varints de más de un byte (cadenas de más de 127 bytes)
- Cadenas UTF-8 con acentos y `ñ` de ida y vuelta
- Reensamblado de fragmentos tanto si traen `\n` como si no
- Player2: `/v1/health` correcto, `401` sin sesión, y app cerrada (conexión rechazada)

**Confirmado después contra el juego real ✅** — todo lo que en su momento quedó
pendiente por no tener la partida delante:

- Que DFHack cargue `dfhack_spike.lua` y lo exponga como comando → ✅
- Que `dfhack.units.getCitizens()` devuelva algo en la partida → ✅
- Que `dfhack.translation.translateName()` dé un nombre legible → ✅ `Udib Nomalardes`
- Que el anuncio aparezca de verdad en el log → ✅ (con icono de alerta)
- La latencia real de Player2 → ✅ 0,56 s
- Qué campos de personalidad existen en esta build → ✅ los 19 sondeados

El único fallo que las pruebas con servidores falsos **no** cazaron fue el del
separador: los stubs emitían el tabulador tal cual, mientras que DFHack lo pasaba por
su tabla CP437. Un stub solo prueba lo que tú le programas.

---

## ✅ Resultados de la ejecución real

### Qué funcionó

| Paso | Resultado |
|---|---|
| Conexión + handshake DFHack | ✅ `127.0.0.1:5000`, sin configurar nada |
| 1 — leer nombre de enano | ✅ `Udib Nomalardes` (unit id 254) |
| 1b — sondeo de campos | ✅ **19 de 19 rutas disponibles** |
| 2 — llamada al LLM local | ✅ `0,56 s` |
| 3 — anuncio dentro del juego | ✅ visible en el log, con el icono de alerta |

Texto que generó el LLM y que acabó dentro de Dwarf Fortress:

> *Maldito trabajo en la cantera, mis huesos duelen como el demonio.*

### Qué no funcionó (y se arregló)

Un solo fallo, en el primer intento: el script Lua pasaba la línea entera por
`dfhack.df2utf()`, incluido el tabulador que hacía de separador. DF no trata los
bytes `0x00-0x1F` como caracteres de control sino como glifos dibujables, y en la
tabla de DFHack (`library/MiscUtils.cpp:573`) `character_table[9] = 0x25CB`, así que
el tabulador llegaba convertido en `○` y el cliente no reconocía ninguna línea.
El dato venía bien; solo se rompía el delimitador.

Arreglado convirtiendo únicamente las cadenas que vienen de DF, y cambiando el
separador a `|` (`0x7C`), que esa tabla mapea a sí mismo. El mismo fallo habría
hecho que el paso 3 reportara fracaso **pese a haber inyectado el anuncio
correctamente**.

### Latencia del LLM de punta a punta

| Medida | Valor |
|---|---|
| `POST /v1/chat/completions` | **0,56 s** |
| Versión del cliente Player2 (`/v1/health`) | `0.10.78` |
| Puerto | `4315`, leído de `%APPDATA%\game.player2.client\api.port` |

Es una sola muestra, con un prompt corto y un límite de 15 palabras. Suficiente para
saber que el orden de magnitud permite uso interactivo, no para dimensionar nada.

### Campos del enano disponibles de verdad

Salida real de `dfhack_spike fields` sobre `Udib Nomalardes`. Esta tabla sustituye a
la lectura teórica de df-structures:

| Ruta | ¿Existe? | Qué trajo |
|---|---|---|
| `unit.id` | ✅ | number |
| `unit.race` | ✅ | number |
| `unit.caste` | ✅ | number |
| `unit.sex` | ✅ | number |
| `unit.civ_id` | ✅ | number |
| `unit.hist_figure_id` | ✅ | number |
| `unit.relationship_ids` | ✅ | **n=9** |
| `unit.status.current_soul` | ✅ | compound |
| `soul.skills` | ✅ | **n=18** |
| `soul.preferences` | ✅ | **n=18** |
| `soul.mental_attrs` | ✅ | **n=13** |
| `personality.traits` | ✅ | **n=50** |
| `personality.values` | ✅ | n=2 |
| `personality.emotions` (pensamientos) | ✅ | **n=28** |
| `personality.dreams` | ✅ | n=1 |
| `personality.stress` | ✅ | number |
| `dfhack.units.getReadableName` | ✅ | string |
| `dfhack.units.getProfessionName` | ✅ | string |
| `dfhack.units.getAge` | ✅ | number |

**19 de 19.** No falló ninguna. Los `n=` bajos en `values` (2) y `dreams` (1) son
propios de este enano concreto, no un límite de la API.

### El tubo funciona, pero sin contexto produce mentiras

El primer resultado "bueno" era falso y no se notaba. El LLM dijo:

> *Maldito trabajo en la cantera, mis huesos duelen como el demonio.*

Suena perfecto. Pero el enano elegido, `Udib Nomalardes`, **es un niño de 8 años**, y
en Dwarf Fortress los niños no tienen oficio: DFHack los clasifica con estado `CHILD`
y los salta con `continue` en todos los bucles de asignación de labores
(`plugins/autolabor/autolabor.cpp:513` y `:619`), sin excepción.
`Units::isChild()` es literalmente `profession == profession::CHILD`
(`library/modules/Units.cpp:310`).

No fue una alucinación del modelo. Fue **culpa del prompt**, que decía exactamente:

> *"Eres un enano de Dwarf Fortress llamado Udib Nomalardes. Di una sola frase corta,
> en español, quejándote del trabajo."*

Un nombre y una orden de quejarse de un trabajo que no existe. El modelo no tenía de
dónde sacar nada más, así que se inventó la cantera. Cualquier LLM habría hecho lo mismo.

**Es el hallazgo más importante del spike**, y es fácil que pase desapercibido porque
la salida *parece* correcta. Un tubo que funciona mecánicamente puede producir texto
plausible y falso, y sin conocer el juego no lo detectas. Aquí lo detectó el usuario,
no el script.

El arreglo (ya aplicado): el script Lua manda profesión, edad, si es adulto, estrés y
los pensamientos recientes, y el prompt se construye con eso. Si el enano es un niño,
se le dice explícitamente que no tiene oficio.

Dos fallos más que salieron al arreglarlo, ambos encontrados **ejecutando** la lógica
Lua contra un DFHack falso, no leyéndola:

- Se cogía siempre `citizens[1]`, o sea el mismo enano en cada ejecución.
- Al pasar a elegir al azar, `math.randomseed(os.time())` seguía devolviendo el mismo:
  `os.time()` tiene resolución de un segundo. La aleatoriedad se movió a Python.

### Los vectores de DFHack son 0-indexados, y eso rompe el instinto de Lua

Tercer fallo real, en la primera ejecución del script con contexto:

```
Cannot read field vector<personality_moodst*>.54: index out of bounds.
dfhack_spike.lua:90
```

Las tablas de Lua empiezan en 1, pero los contenedores de DFHack **no**. De
`docs/dev/Lua API.rst:250`:

> *"Accesses the container element, using either a **0-based** numerical index"*

Con `#emo == 54`, los índices válidos son `0..53`. Mi bucle pedía del 52 al 54 y se
salía por el final. El arreglo es leer `math.max(0, n-3) .. n-1`.

Lo importante no es el fallo, sino **por qué no lo cazaron las pruebas**: el DFHack
falso usaba una tabla normal de Lua, 1-indexada y silenciosa al salirse. Reproducía
mi propia suposición equivocada en lugar del comportamiento real. Se arregló dando al
banco de pruebas un `__index` que **también** revienta fuera de rango, y entonces
reprodujo el error exacto, misma línea. Segunda vez que pasa lo mismo en este spike
(la primera fue el separador CP437). Es un patrón, no mala suerte.

### El mensaje de error mentía

Ante el fallo anterior, el script dijo: *"Causa más probable: dfhack_spike.lua no está
en dfhack-config\scripts\"*. Falso: el script estaba puesto y funcionando, y DFHack
había devuelto un traceback de Lua completo diciendo exactamente qué línea fallaba.
El mensaje adivinaba una causa en vez de leer la que venía en la respuesta.

Ahora se imprime lo que DFHack contesta y solo se sugiere una causa cuando **no** hay
salida ninguna. Un diagnóstico que adivina mal es peor que no tener diagnóstico:
manda a mirar la carpeta equivocada.

### Cierre: con contexto real, la salida deja de ser mentira

Ejecución final, enano `Udil Nolêthshorast`, `Fish Cleaner`, 24 años, estrés `-1100`,
con emociones `FONDNESS por Talked`, `INTEREST por WatchPerform` y `DELIGHT por
WatchPerform`. El LLM devolvió:

> *Estoy tranquilo limpiando peces, aún con el delicioso recuerdo de tu actuación.*

Cada pieza sale de un dato real, no de la imaginación del modelo:

| Trozo de la frase | De dónde sale |
|---|---|
| *"Estoy tranquilo"* | `ESTRES = -1100`, negativo |
| *"limpiando peces"* | `PROFESION = Fish Cleaner` |
| *"el delicioso recuerdo de tu actuación"* | `DELIGHT por WatchPerform` |

Comparado con el *"maldito trabajo en la cantera"* del niño de 8 años, la diferencia
no es de estilo: es que ahora **es verdad**. El tubo no cambió ni una línea; lo que
cambió fue lo que se le mete dentro.

### Acentos y CP437: confirmado en las dos direcciones ✅

- **Lectura** (`dfhack.df2utf`): nombres como `Fath Zolakîton` y `Udil Nolêthshorast`
  llegan con la `î` y la `ê` intactas.
- **Escritura** (`dfhack.utf2df`): el anuncio *"...aún con el delicioso recuerdo de tu
  actuación"* se renderiza correctamente en el log del juego, con `ú` y `ó`.

Era el último riesgo abierto y no da problemas. Escribir en español funciona.

### Un detalle a corregir cuando esto crezca

El modelo dijo *"tu actuación"*, hablando con alguien que no está ahí. Es porque se le
pasan los nombres crudos del enum (`WatchPerform`) sin explicar qué significan, y ha
supuesto un interlocutor. Para el proyecto de verdad, los `unit_thought_type` y
`emotion_type` habrá que traducirlos a lenguaje natural antes de meterlos en el prompt
(`WatchPerform` → *"vio una actuación"*), no volcarlos tal cual.

### Latencia en tres ejecuciones

| Ejecución | Latencia |
|---|---|
| 1 (prompt mínimo) | 0,56 s |
| 2 (prompt mínimo) | 0,53 s |
| 3 (prompt con contexto) | 0,64 s |

Añadir el contexto costó ~0,1 s. Sigue de sobra para uso interactivo.

### Lo que esto significa para lo que venga después

1. **La vía Lua es la buena, y está confirmada en vivo.** 50 rasgos de personalidad,
   28 pensamientos, 18 preferencias, 18 habilidades y 9 relaciones por enano. Es
   material narrativo de sobra. El RPC estructurado (`ListUnits`) no da nada de esto:
   si montas la arquitectura encima del protobuf, te quedas sin lo interesante.
2. **Un solo método RPC basta.** `RunCommand` sobre un script propio en
   `dfhack-config/scripts/` cubre lectura y escritura. No hizo falta `BindMethod`,
   ni `RunLua`, ni generar código protobuf.
3. **Cero dependencias es viable.** El cliente entero son ~60 líneas de stdlib y
   funcionó a la primera contra el juego real (salvo el fallo del separador).
4. **0,56 s permite interactividad**, pero habrá que medir con prompts largos, que
   es lo que pasará en cuanto le metas el contexto de rasgos y pensamientos.

### Lo que sigue sin probarse

- Comportamiento con muchas llamadas seguidas, o con varios enanos a la vez.
- Comportamiento con varios enanos, o llamadas repetidas seguidas.
- Qué ocurre si DF está pausado, o si se descarga la partida con el socket abierto.
- Textos largos: `showAnnouncement` no se ha probado con más de una línea.

## FASE 1 — Cerrar incógnitas (en curso)

Ejecutado el 2026-09-09 contra la instalación real. Instrumento: `dfhack_medir.lua`
y `medir.py`, sin tocar el spike.

### E1 · Latencia con prompt largo ✅

Enano `Tulon Åblelardes`, `Diagnoser`, 40 años. Volcado completo: **50 rasgos,
69 pensamientos, 3 relaciones, 18 preferencias, 27 habilidades**.

| | Prompt mínimo | Prompt largo |
|---|---|---|
| Tamaño | 153 caracteres | **4 887 caracteres** (32×) |
| Mediana | 0,486 s | **0,952 s** |
| Rango | 0,455 – 0,577 s | 0,932 – 1,038 s |
| Respuesta | ~57 caracteres | ~549 caracteres |

**Coste de meter todo el contexto: +0,465 s, exactamente 2,0×.** Cinco vueltas
intercaladas de cada tipo en la misma ejecución, así que la comparación es limpia;
los 0,56 s históricos eran una muestra suelta de otro día.

Escala mucho mejor de lo que sugería el tamaño: 32 veces más prompt cuesta solo el
doble de tiempo. La latencia la domina la generación, no la lectura del contexto.

**El lado Lua tarda 0,003 s en volcarlo todo.** Es 300 veces menos que el LLM. Para
la fase 2 esto significa que **el cuello de botella es exclusivamente el modelo**:
no hay que optimizar la extracción de datos, hay que gestionar la espera del LLM.

Y el resultado se apoya en datos reales, no en relleno — cita a la esposa (`Tobul`),
el estrés bajo, las habilidades de tallar piedra y poesía, y hasta un rasgo alto:

> *Soy Tulon Åblelardes, un enano de 40 años... Aunque extraño profundamente a mi
> esposa Tobul y siento una tristeza persistente por nuestra separación, mantengo una
> calma inusual con un estrés tan bajo que casi me siento en paz.*

Nota: 69 pensamientos frente a los 28 del enano del spike. **El volumen varía mucho
por individuo**, así que el prompt largo no tiene tamaño fijo y habrá que acotarlo.

### E2 · Dwarf Fortress en pausa ✅ No bloquea nada

| Operación | Con el juego pausado |
|---|---|
| `estado` (ida y vuelta) | 0,005 s |
| Lectura completa | 0,018 s (lado Lua: 0,002 s) |
| Anuncio | Funciona, 0,014 s |
| `frame_counter` tras 2 s | 1759 → 1759 (pausa real confirmada) |

**`RunCommand` responde con normalidad con el juego en pausa.** No se cuelga, no se
encola, no espera a que se reanude. Es una buena noticia para la arquitectura: se
puede leer y escribir mientras el jugador tiene el juego parado.

### E4 · Bloqueo — parcial ⚠️

Solo se llegó a medir **FPS en reposo: 100,1** (402 frames en 4,02 s). El resto se
abortó por un fallo del instrumento, no del juego (ver abajo).

### E3, E5, E6 — pendientes

No llegaron a ejecutarse: van después del E4 en el orden.

### Trampa nueva: `string.format` de Lua respeta el locale

El instrumento se cayó en el E4 con `could not convert string to float: '0,0000'`.

`string.format('%.4f', x)` en Lua pasa por el `printf` de C, que **usa el separador
decimal del locale**. En un Windows en español DFHack devuelve `0,0020` con coma, y
`float()` de Python lo rechaza. Python no tiene ese problema porque no usa locale al
formatear, y por eso sus propios números salían con punto en la misma línea de log:

```
Lectura completa en pausa: ... ida y vuelta 0.018 s   lado Lua 0,0020 s
                                            ↑ Python           ↑ Lua
```

Arreglado emitiendo **microsegundos enteros** desde Lua (`%d` no se ve afectado por
el locale) y parseando tolerante en Python. **Dwarf Fortress no se cayó**: el
traceback era de Python y el socket seguía sano.

Es la misma familia que la trampa de CP437: **el lado Lua no está en el mismo mundo
de convenciones que el lado Python**, y cada vez que cruzamos texto o números entre
los dos hay que decidir explícitamente el formato.

## Cómo ejecutarlo

1. Copia `dfhack_spike.lua` a
   `D:\Steam\steamapps\common\Dwarf Fortress\dfhack-config\scripts\dfhack_spike.lua`
2. Abre Dwarf Fortress y **carga una fortaleza** (no vale el menú principal:
   `getCitizens()` necesita un mundo cargado en modo fortaleza).
3. Abre la app de escritorio de Player2 e inicia sesión.
4. `python spike.py`

Si tienes un Game Client Id de Player2 y quieres mandarlo:
`set PLAYER2_GAME_KEY=tu-id` antes de ejecutar.
Si cambiaste el puerto de DFHack: `set DFHACK_PORT=otro`.
