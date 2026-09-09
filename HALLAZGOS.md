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

> ✅ **Verificado después contra la instancia real.** Cuando se escribió esta sección,
> `player2.game` estaba bloqueado por el proxy de red y los datos salieron de una copia
> espejo del OpenAPI en un repositorio de terceros. Ese riesgo ya está cerrado: el spec
> se descargó de `http://127.0.0.1:4315/v1/openapi.json` (119 792 bytes, 47 rutas,
> OpenAPI 3.1.0) y **todo lo de arriba coincide**: base `/v1`, puerto 4315, las rutas de
> `api.port` por plataforma, el header `player2-game-key`, el esquema de petición
> completo, y la ausencia total de `Authorization`/`Bearer` en el documento, que
> confirma que la API local no lleva clave. Lo que **no** coincide es el comportamiento
> real de los errores: ver la sección E6 de la fase 1.

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

### E4 · Bloqueo del juego ✅ Por debajo del ruido de medida

| Medida | Valor |
|---|---|
| FPS en reposo | 98,6 (397 frames en 4,03 s) |
| `bench` 20 enanos, 2 245 campos | **0,0020 s** en el lado Lua (0,0001 s/enano) |
| 20 lecturas completas seguidas | mediana 0,007 s, total 0,17 s |
| 6 índices distintos | 6 enanos distintos ✅ |

**Cuidado con el número que escupe el instrumento.** Dice «impacto: 122,9% de los FPS
en reposo», que leído literalmente significaría que el juego va *más rápido* mientras
se le consulta. No es así: **la ráfaga solo duró 21 frames**, y a esa escala un frame
de más o de menos mueve el resultado 6 puntos porcentuales. Es ruido de cuantización.

La lectura correcta es la del lado Lua, que no depende de esa ventana:
**2,0 ms para 20 enanos y 2 245 campos**. Un frame a 98,6 FPS dura 10,1 ms, así que
el trabajo entero equivale a **0,2 frames**. El impacto sobre el juego no es que sea
pequeño: es que **no se puede medir** con esta carga.

### E5 · Anuncios largos y multilínea ✅ con una limitación

Los cuatro (100, 300, 800 y 2 000 caracteres) entraron sin error, y el de 2 000
apareció. Observado en el juego: **el texto se ajusta al ancho del log**, y los cortes
se corresponden con el final de cada anuncio, no con un truncamiento.

**El `\n` NO se respeta.** El anuncio de tres líneas salió como **un solo anuncio en
una sola línea**. Para varias líneas hay que hacer varias llamadas a
`showAnnouncement`, una por línea.

No se determinó un límite máximo exacto de caracteres: a 2 000 todavía no falla, y
localizar visualmente dónde acaba cada anuncio se vuelve difícil porque entre el
segundo y el tercero ya no queda separación visible.

### E6 · Player2 real ⚠️ HALLAZGOS.md tenía datos incorrectos

Spec encontrado en **`/v1/openapi.json`** (119 792 bytes, 47 rutas). El `/docs` son
734 bytes de HTML que solo carga Swagger UI.

Códigos que el spec **documenta** para `/chat/completions`:
`200`, `400`, `401`, `402`, `429`, `500`.

Códigos **reales**, provocados a propósito:

| Prueba | Código real | Respuesta |
|---|---|---|
| Ruta inexistente | `404` | cuerpo vacío |
| Cuerpo `{}` | **`422`** | `missing field 'messages'` |
| `messages: []` | **`500`** | `Internal server error` con `request_id` y `trace_id` |
| Rol `marciano` | **`422`** | `unknown variant, expected one of user, assistant, system, developer` |

Tres correcciones a lo que este documento afirmaba antes:

1. **El `422` no está documentado.** El spec promete `400` para entrada inválida, pero
   la implementación devuelve `422` con el mensaje de deserialización de Serde. Quien
   valide contra el spec se equivoca.
2. **`messages: []` devuelve `500`, no un 4xx.** Es un fallo del servidor de Player2:
   una lista vacía es error del cliente. Consecuencia práctica: **nunca enviar
   `messages` vacío**, y no tratar todo `500` como «reintentar más tarde», porque este
   es determinista y reintentarlo no arregla nada.
3. **El spec se contradice a sí mismo con los roles.** El enum `Role` declara **cinco**
   valores — `user`, `assistant`, `system`, `developer`, `tool` — pero la descripción de
   `Message.role`, en ese mismo documento, dice literalmente *"must be one of user,
   assistant, system, developer"*: cuatro. El mensaje de error real que devolvió el
   servidor quedó cortado en nuestro log justo en `develope`, así que **no sabemos cuál
   de los dos hace caso la implementación**. Para el proyecto da igual: solo se usan
   `system` y `user`. Pero si algún día hace falta `tool`, hay que probarlo antes de
   confiar en él.

El `client_version` sigue siendo `0.10.78` y `/v1/health` responde `200`.

### E3 · Descarga de partida y cierre de DF ✅ Sin crasheos

Era la incógnita más importante. **No se rompió nada.** Todo con el mismo socket
abierto de principio a fin:

| Situación | Resultado |
|---|---|
| a) En el menú principal | `estado` → `MUNDO=false MAPA=false CIUDADANOS=0`, sin error |
| a) `contexto` sin partida | `ERROR\|sin partida cargada` — **fallo limpio, no crasheo** |
| a) `anuncio` sin partida | Devuelve `OK` (no falla, aunque no haya dónde mostrarlo) |
| b) Tras recargar la partida | `MUNDO=true MAPA=true CIUDADANOS=50`, lectura correcta |
| b) ¿Sobrevive la conexión? | **SÍ**, el mismo socket sigue sirviendo tras descargar y volver a cargar |
| c) Con DF cerrado | `ConnectionResetError: [WinError 10054]` — excepción limpia y capturable |

**Conclusión para la arquitectura:** el socket es más robusto de lo que se temía. No
hace falta reconectar en cada cambio de partida; basta con **capturar
`ConnectionResetError` y reconectar cuando el juego se cierra**, y **preguntar por
`MUNDO`/`MAPA` antes de tocar unidades**, que es lo que ya hace el guardián
`hay_partida()`.

### Veredicto de la fase 1

**Ninguna de las seis incógnitas obliga a replantear la arquitectura.** El único
cambio de diseño que sale de aquí es táctico: un anuncio por línea, nunca `messages`
vacío, y validar contra el comportamiento real de Player2 en vez de contra su spec.

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

## FASE 2 — Base estable ✅ funcionando

`CLAUDE.md` (invariantes), `df_estado.lua` (contrato JSON) y `df_llm.py` (servicio).
Validado contra el juego el 2026-09-09: fortaleza de 64 ciudadanos, 40 adultos.

| Orden | Resultado |
|---|---|
| `df_llm.py estado` | mundo/mapa cargados, 64 ciudadanos |
| `df_llm.py listar 5` | 5 enanos con oficio y estrés, incluido uno con estrés **positivo** (+17067) |
| `df_llm.py hablar 2` | Dos enanos hablan y se anuncian en el juego |

**El prompt acotado funciona:** de los 4 887 caracteres del volcado crudo a **952 y
1 030** en casos reales, sin perder sustancia. Y **el troceado de anuncios se ejercitó
solo**: una de las respuestas traía un salto de línea y salió como **2 anuncios**, que
es exactamente para lo que estaba la lección del E5.

### Anomalía de latencia — RESUELTA: era ruido

| | Prompt | Respuesta | Latencia |
|---|---|---|---|
| E1 (fase 1) | 4 887 car. | ~509-615 car. | **0,93 – 1,04 s** |
| Servicio | 952 car. | ~290 car. | **1,45 s** |
| Servicio | 1 030 car. | ~330 car. | **1,41 s** |

Se volvió a medir con el método del E1 (cinco vueltas, mediana y rango) sobre el
prompt del servicio:

| | Prompt | Mediana | Rango |
|---|---|---|---|
| E1 (fase 1) | 4 887 car. | 0,952 s | 0,932 – 1,038 |
| Servicio, re-medido | 1 156 car. | **0,967 s** | 0,734 – 1,172 |

**Diferencia: 15 milisegundos.** Aquellas dos muestras de 1,41 y 1,45 s eran valores
sueltos, no una tendencia. Sirve de recordatorio de por qué no se les buscó explicación
en su momento: cualquier causa que se hubiera inventado habría sido falsa.

Un patrón que sí sostienen los datos: **la latencia va con el tamaño de la RESPUESTA,
no con el del prompt.** Correlación entre caracteres generados y tiempo: **r = 0,80**
en estas cinco muestras. Encaja con que un prompt cuatro veces más corto no cambie
nada. Para el proyecto: **acotar la respuesta abarata más que acotar el contexto.**

### Enums traducidos ✅ usando el texto del propio DF

df-structures trae `caption` en algunos enums, accesibles desde Lua con
`df.<enum>.attrs[v].caption` (`Lua API.rst:377`). Preguntado a la instalación real con
`df_llm.py enums`:

| Enum | ¿Caption? | Ejemplo |
|---|---|---|
| `unit_thought_type` | ✅ **prosa real** | `Conflict` → *"while in conflict"* |
| `job_skill` | ✅ cosmético | `MINING` → *"Mining"* |
| `skill_rating` | ✅ inútil | `Dabbling` → *"Dabbling"* (idéntico) |
| `emotion_type` | ❌ | se humaniza |
| `personality_facet_type` | ❌ | se humaniza |
| `unitpref_type`, `unit_relationship_type`, `value_type` | ❌ | se humanizan |

El premio gordo es `unit_thought_type`: **281 valores con prosa escrita por DF**, que es
justo la causa de cada emoción. Para el resto se humaniza el identificador de forma
mecánica (`WatchPerform` → *watch perform*), que no interpreta ni inventa nada.

```
Antes:  DELIGHT por WatchPerform; GRIEF por WitnessDeath
Ahora:  delight while watching a performance; grief after seeing somebody die
```

### Tono corregido ✅

El modelo ya no escribe cartas. Bastó decirle explícitamente para quién habla:
*"Hablas para ti mismo mientras trabajas: NO te dirijas a nadie"*.

> *Mientras tallo esta culata de ballesta, siento una satisfacción profunda al ver cómo
> la madera enana se adapta a mis manos. Qué pena que mis padres nunca llegaran a ver
> lo bien que me ha quedado esta fortaleza.*

Efecto secundario a vigilar: `_rasgos_marcados` selecciona los rasgos más extremos, y
un rasgo muy alto **domina la salida**. Un enano con lujuria alta produce respuestas
marcadamente eróticas. Es coherente con los datos, pero si algún día molesta, el sitio
para tocarlo es el criterio de selección de rasgos, no el prompt.

### El modelo habla *a* alguien en vez de *sobre* sí mismo

Un alcalde con esposa registrada respondió *"Mi querida Catten, mientras tallo esta
flauta..."*: una carta, no un pensamiento. Es la misma forma del *"tu actuación"* del
spike. El prompt lista `Personas que te importan: Catten (SPOUSE)` y el modelo asume un
interlocutor presente.

No es un fallo técnico —la frase es coherente y en primera persona— pero en un log de
anuncios queda raro. Se arregla en la instrucción final del prompt, diciendo
explícitamente que hable para sí mismo y que no se dirija a nadie.

## FASE 3 — Paso 0: estabilidad de ids y coste del sondeo ✅

Ejecutado el 2026-09-09 con guardado y recarga reales. Fortaleza de 64 ciudadanos.

### `unit_id` sobrevive. Sirve como clave.

| Métrica | Resultado |
|---|---|
| ids presentes antes y después | 64 y 64, los mismos |
| **ids que siguen apuntando al mismo enano** | **64 de 64** |
| ids que apuntan a otro enano | **0** |
| `hist_figure_id` válidos | **64 de 64** (ninguno vale `-1`) |
| hfid que apuntan a otro enano | 0 |

La comparación no cuenta supervivientes sino **huellas**: nombre + año y momento de
nacimiento + `hist_figure_id`. Un id que existiera pero hubiera cambiado de dueño
habría saltado, que es el fallo silencioso que se buscaba.

**Las dos claves valen.** Se usa `unit_id` por ser directa; `hist_figure_id` queda como
verificación cruzada dentro de la huella.

### Reutilización de ids: evidencia fuerte de que no ocurre

Era la duda planteada en revisión externa, y un ciclo de guardado no puede resolverla
—hace falta que alguien muera y llegue otro después—. Pero los números son elocuentes:

- `unit_next_id` = **5486**, y el id de ciudadano más alto es **5483**
- Rango de ids: 254 – 5483, con **5166 huecos**
- El contador **no se reinició** al recargar: 5486 antes y después

Los huecos son todo lo demás que DF ha creado alguna vez (fauna, invasores, difuntos).
**El contador va justo por delante del máximo**: patrón de asignación secuencial desde
un contador global, no de reciclaje desde un pool. Es evidencia, no prueba — y por eso
la huella se implementa igualmente.

### Coste del sondeo: la objeción estaba justificada

| Nivel | Bytes | Lua | Ida y vuelta | Por minuto (cada 5 s) | Frames parados |
|---|---|---|---|---|---|
| `basico` | 9,3 KB | 1 ms | 9,9 ms | 109 KB · 12 ms | 0,1 |
| **`sonda`** | **15,0 KB** | **5 ms** | **10,7 ms** | **176 KB · 60 ms** | **0,5** |
| `completo` | **344,5 KB** | **40 ms** | **104,9 ms** | 4 037 KB · 480 ms | **3,9** |

Sondear con `completo` habría costado **23 veces más tráfico y 8 veces más tiempo de
juego bloqueado**: 40 ms por vuelta son casi **4 frames congelados** cada 5 segundos, a
98 FPS. Un tirón perceptible, y todo para comprobar si a alguien le cambió el humor.

Con `sonda` son 0,5 frames y el 0,1% del tiempo. **Los 2 ms de la fase 1 no eran
extrapolables**: medían lectura de campos, no serialización ni transporte.

`sonda` cuesta 5× más Lua que `basico` porque recorre todas las emociones de cada enano
(entre 28 y 69) para quedarse con la marca de tiempo más reciente. Es lo que compra la
detección exacta de emoción nueva, y sigue siendo despreciable.

## FASE 3 — El vigía funciona ✅

Primera ejecución real del servicio en bucle. **Ocho sucesos narrados** a lo largo de
dos semanas de juego, con enanos distintos cada vez y los tres tipos de disparador
funcionando:

| Tipo | Ejemplo real |
|---|---|
| Emoción | `INTEREST WatchPerform` → *"Acabo de ver una actuación maravillosa, tan perfecta que casi me molesta no haberla escrito yo mismo."* |
| Estrés | `su ánimo ha mejorado (categoría 3 a 4)` → *"Mi ánimo ha mejorado bastante, ahora me siento en una paz profunda."* |
| Relación | `has perdido a alguien importante` → *"He perdido a alguien muy importante para mí. Me duele tanto que no sé qué hacer ahora."* |

La memoria salió limpia: 8 fichas, **ninguna vacía** (la corrección de la lectura que no
crea ficha funcionó), todas con huella completa, y los `participantes` en su sitio.

### Defecto encontrado: los enums crudos volvieron por la puerta de atrás

Cuatro de las ocho entradas tenían `detalle = "EUPHORIA Syndrome"`, y el modelo lo
repetía literalmente: *"¡Euphoria! ¡Qué alegría tan grande...!"*.

La causa: el `detalle` que dispara el evento sale de la **sonda**, y la sonda usaba
`enum()` en lugar de `enum_txt()`. El camino de detalle completo sí traducía; el de la
sonda no. **Es exactamente la trampa que se arregló en la fase 2, reaparecida en un
camino nuevo** — porque al añadir la sonda se copió el patrón viejo sin pensar.

Lección para el proyecto: **cada vez que se añade un camino de datos hacia el prompt hay
que preguntarse si traduce los enums**, porque la corrección de la fase 2 no es global,
es por sitio.

### Defecto de diseño: los sucesos colectivos llenan la crónica de lo mismo

Un síndrome afectó a media fortaleza y cuatro enanos narraron la misma euforia. Los
frenos por enano funcionaban —cada uno habló una vez— pero no había ninguno **entre**
enanos.

Añadida una ventana de los últimos 5 sucesos narrados por cualquiera: si el mismo
`detalle` ya se contó, se pasa al siguiente candidato. Un suceso que afecta a todos se
narra una vez, no una por cabeza.

### Segunda ejecución: los enums arreglados, y tres fugas nuevas

El arreglo funcionó. Los disparadores pasaron de `EUPHORIA Syndrome` a prosa de DF:
*"bliss after sleeing in a quality bedroom"*, *"relief discussing their problems with
somebody"*, *"delight after watching a performance"*.

**La errata «sleeing» es de DF, no nuestra**: `df.personality.xml:763` dice literalmente
`'after sleeing in a [quality] bedroom'`. Se está pasando su texto fielmente.

Pero aparecieron tres fugas de jerga en las respuestas, y **la peor era culpa del prompt**:

| Fuga | Ejemplo | Causa |
|---|---|---|
| Cifras crudas | *"aunque mi estrés sigue en **11440**"* | El prompt decía `"Tu nivel de estres es %d"`. Se lo dábamos nosotros |
| Recitar el disparador | *"Mi ánimo ha mejorado **de categoría 2 a 3**"* | El detalle era `su animo ha mejorado (categoria 2 a 3)`: lenguaje de máquina |
| Traducción literal | *"placer profundo cerca de **mi propia calidad al construir**"* | Las captions de DF están en **inglés y tercera persona** (`pleasure near his own quality building`) |

Correcciones:

1. **El prompt da la lectura, nunca el número**: *"Por dentro te sientes muy agobiado, al
   límite"* en vez del valor. Si le das una cifra, la recita.
2. **El disparador de ánimo pasa a lenguaje llano**: *"te sientes algo peor que hace un
   rato"*, *"te has quitado un gran peso de encima"*. Sin categorías.
3. **Se le avisa de que la caption es un apunte del juego**, en inglés y tercera persona,
   y que no la traduzca sino que cuente lo que significa para él.
4. Instrucción explícita de no usar cifras, categorías ni nombres de sistema.

La lección se repite: **el modelo devuelve lo que le das**. Las tres veces que hemos
visto «alucinar» al LLM en este proyecto —la cantera del niño, las cartas a la esposa, y
ahora las cifras— el fallo estaba en el prompt, no en el modelo.

### ¿Cuántos rasgos? La medición dice que da igual

| Rasgos en el prompt | Solapamiento léxico | Prompt medio |
|---|---|---|
| 3 | 0,059 | 1 093 car. |
| 5 | 0,068 | 1 143 car. |
| 8 | 0,064 | 1 223 car. |

Diferencia máxima: **0,009**. Con 6 enanos son 15 pares por grupo, así que eso es ruido.

**El número de rasgos no influye en que los enanos suenen distintos.** Contradice tanto
el plan de bajar a 3 como el temor de que 3 los homogeneizara: la variedad no sale de
los rasgos, sale de las relaciones, el oficio, el estrés y el pensamiento que dispara.

Se queda en **8**, porque no penaliza y da textura más específica —a 8 rasgos aparecen
la poesía y el celo por el taller que a 3 no salen— y el coste extra son 130 caracteres
de prompt, que la fase 2 demostró que no afectan a la latencia.

### El tic del «Mientras»: 16 de 18 respuestas

Con cualquiera de los tres topes, casi todas las respuestas tenían la misma forma:

> *"**Mientras** tallo esta piedra, pienso..."* · *"**Mientras** afilo esta hoja, pienso..."*
> *"**Mientras** pico la roca, pienso..."*

14 de 18 **empezaban** por esa palabra. La causa estaba en la instrucción:
*"Hablas para ti mismo **mientras trabajas**"*. El modelo cogió la palabra del prompt y
la convirtió en muletilla.

Corregido quitándola y pidiendo explícitamente que no arranque describiendo la tarea.
**No se nombra la fórmula prohibida**, porque decir «no uses *mientras*» vuelve a meter
la palabra en el contexto.

### `Unidad 338` no era un enano, era mi relleno

Dos respuestas hablaban de una tal *"Unidad 338"* como si fuera una persona:
*"Unidad 338 me espera esta noche con su sonrisa callada"*.

Cuando `df.unit.find()` no resuelve al pariente —está fuera del mapa, o muerto— el lado
Lua emitía `'unidad ' .. id` de relleno, y el modelo lo tomó por un nombre propio. Ahora
**la relación se omite** si no hay nombre: una relación anónima no aporta nada.

### El patrón, por cuarta vez

Las cuatro veces que el texto ha salido mal en este proyecto, la causa estaba en el
prompt, no en el modelo: el niño quejándose de la cantera, las cartas a la esposa, las
cifras recitadas y ahora la muletilla y el nombre de relleno. **El modelo devuelve lo
que le das.**

### Verificación: la muletilla desapareció

| | Antes | Después |
|---|---|---|
| Respuestas con «mientras» | **16 de 18** | **2 de 18** |
| Que **empiezan** por «Mientras» | **14 de 18** | **0** |

Las dos restantes lo usan a mitad de frase, que es castellano normal. Aperturas ahora:
*Me siento · La brisa fría · La verdad es · Los hongos · Por fin · La paz*.
`Unidad 338` tampoco reaparece.

### La métrica de solapamiento es inconcluyente, y eso es el resultado

| Rasgos | Ejecución 1 | Ejecución 2 |
|---|---|---|
| 3 | 0,059 | 0,046 |
| 5 | **0,068** (el peor) | **0,031** (el mejor) |
| 8 | 0,064 | 0,041 |

**El orden se invierte entre tiradas.** Con 6 enanos y una muestra por configuración, la
varianza entre ejecuciones supera la diferencia entre configuraciones: la métrica no
distingue 3 de 5 de 8. Para que sirviera harían falta más enanos y varias repeticiones.

Lo que sí es consistente en ambas tiradas es la **riqueza**:

| Rasgos | Longitud media | Palabras distintas |
|---|---|---|
| 3 | 210 car. | 26 |
| 5 | 217 car. | 27 |
| 8 | **237 car.** | **29** |

**Decisión: se queda en 8 rasgos.** Y conviene ser explícito en que **no lo sostiene la
métrica de solapamiento**, que salió inconcluyente, sino la riqueza medida más la
lectura: a 8 rasgos aparece la duda sobre uno mismo (*"me pregunto si no estaré
demasiado apegado a Tobul"*, *"echo de menos no sentir tanto esta paz vacía"*) que a 3
no sale.

Ninguno de los dos temores era cierto: ni un rasgo extremo secuestra la salida con 8,
ni bajar a 3 homogeneiza. **La variedad no sale de los rasgos**, sale de las relaciones,
el oficio, el estrés y el suceso que dispara.

### Coherencia entre enanos, sin haberla programado

Del mismo grupo, dos enanos casados entre sí:

> **Tirist**: *"Cada vez que pienso en **Tosid** me invade una calidez tranquila, aunque a
> veces esa misma calma me hace preguntarme **por qué no soy más efusivo con ella**"*

> **Tosid**: *"**Tirist** merece algo mejor que **un marido que se queda mirando el
> vacío** después de cada guardia"*

Cada uno reflexiona sobre la misma relación desde su lado y **coinciden en el
diagnóstico**. No hay nada en el código que lo produzca: sale de que ambos leen el mismo
`relationship_ids`. Es la señal de que los datos del juego, bien traducidos, bastan para
sostener la ilusión.

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
