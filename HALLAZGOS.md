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

### ¿Cuántos rasgos? Primera tirada

| Rasgos en el prompt | Solapamiento léxico | Prompt medio |
|---|---|---|
| 3 | 0,059 | 1 093 car. |
| 5 | 0,068 | 1 143 car. |
| 8 | 0,064 | 1 223 car. |

Diferencia máxima: 0,009 con 15 pares por grupo. En su momento se leyó como «el número
de rasgos no influye», pero **una sola tirada no sostiene esa conclusión**: la segunda
ejecución (más abajo) invierte el orden y demuestra que lo que hay es ruido de medida.
La conclusión buena está en *«La métrica de solapamiento es inconcluyente»*.

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

## Revisión externa de la fase 3 — punto de paso único

La revisión señaló que la fuga de enums **es un patrón y no un fallo**: tres veces la
misma clase de error en un camino nuevo (el tabulador por `df2utf`, el índice 0-based, y
`enum()` en vez de `enum_txt()` al añadir la sonda). Y que el invariante escrito en
`CLAUDE.md` no lo impidió, porque depende de que alguien se acuerde.

Implementado `df_llm.legible()`, por el que pasa **todo** texto de DF antes del prompt.
No lo arregla en silencio: apunta cada fuga en `df_llm.FUGAS` y el vigía las imprime, de
modo que un camino olvidadizo se delata en vez de quedar tapado.

**Al construirlo apareció una limitación real de la idea.** Detectar por forma
(`ALL_CAPS`, `CamelCase`, guiones bajos) deja pasar `Syndrome`: una palabra capitalizada
sin mayúscula interna es indistinguible de un nombre como `Tulon`.

La forma sola no basta, pero **el guardián sí sabe qué campo protege**. Los campos que
siempre vienen de un enum —rasgo, emoción, causa, habilidad, preferencia, tipo de
relación, detalle del evento— se marcan con `siempre_enum=True`, y ahí cualquier token
suelto es un identificador, porque los nombres propios viven en otros campos (`quien`,
`nombre`), que no cruzan por ese modo.

Verificado: con datos que simulan un camino que olvidó traducir, no sobrevive ningún
identificador crudo al prompt, los nombres propios quedan intactos y las cinco fugas se
registran.

### La carpeta de guardados no está donde parece

Se dio de memoria la ruta `<Dwarf Fortress>/save` y **no existe**. En la versión de
Steam los guardados están en:

```
%APPDATA%\Bay 12 Games\Dwarf Fortress\save
```

es decir, `C:\Users\<usuario>\AppData\Roaming\Bay 12 Games\Dwarf Fortress\save`.

La documentación de DFHack ya avisaba de esto para su propia instalación
(`Lua API.rst:942`: *"la carpeta de instalación es extremadamente probable que esté en
otro sitio cuando DFHack se instala desde Steam"*), y no se leyó esa advertencia como
aplicable también a los guardados.

Se intentó resolverlo preguntando al juego con `dfhack.getSavePath()`, pero **tampoco
devuelve esa ruta**, así que se revirtió: añadía dos campos al contrato sin resolver
nada. La ruta buena queda escrita en `CLAUDE.md` como hecho verificado, que para este
caso es suficiente.

Dos errores encadenados, y el segundo es el que enseña algo: al primero le busqué un
atajo automático y **lo di por bueno antes de comprobarlo**, en un proyecto cuya regla
principal es no dar nada por bueno sin verificarlo.

## Test de muerte controlada ✅ y un defecto narrativo grave

Ejecutado copiando el guardado y matando a un ciudadano con `exterminate this` de
DFHack, en vez de esperar a que ocurriera jugando.

**El camino técnico funciona.** La crónica dice:

```
[ano 105, mes 7, dia 25] Tosid Nishkesh -- ha muerto
```

`ha muerto`, no `ha desaparecido`. `isKilled` dispara correctamente y queda cerrado el
riesgo señalado en la revisión externa. Recordar que aquí ya hubo una corrección previa:
`flags1.dead` no existe y `inactive` también se activa para criaturas vivas que salen
del mapa.

### Pero el muerto narraba su propia muerte

> *"**Siento** un nudo en el pecho que no se afloja. **Se acabó, todo se acabó**, y solo
> me queda esta rabia sorda porque ya no puedo hacer nada más."*

Eso lo dice Tosid. El difunto. En primera persona y en presente.

El defecto es de diseño, no de detección: `_hablar()` construía siempre un prompt en
segunda persona (*"Esto es lo que acaba de pasarte..."*), y para una muerte eso equivale
a pedirle a un cadáver que hable.

**Es exactamente el riesgo que la revisión externa señaló como prioritario**: la muerte
es donde la crónica más importa y donde más duele equivocarse. El camino técnico estaba
bien y el narrativo mal, que es peor, porque el resultado *parece* correcto.

Corregido: `muerte`, `locura` y `desaparicion` pasan por `construir_epitafio()`, un
prompt en **tercera persona** escrito desde la voz del cronista, con los datos reales del
difunto —oficio, edad, a quién dejaba atrás, en qué era bueno— y prohibición explícita
de la primera persona.

### Nadie reaccionó a la muerte

En las vueltas siguientes al fallecimiento, ningún otro enano lo mencionó. Tosid estaba
casado con Tirist Atírshis (la pareja del caso de coherencia), así que la muerte debería
haber cambiado sus `relationship_ids` y disparado un evento de relación.

No se ha determinado si no saltó, si lo suprimieron los frenos, o si el cambio tarda más
en reflejarse. **Es la primera pregunta que debe responder la sesión larga.** Un fuerte
donde alguien muere y nadie lo nota es el fallo narrativo más grave que puede tener este
proyecto.

## Segundo test de muerte: tres bugs, ninguno del modelo

Con cinco enanos asesinados a propósito, la ejecución destapó tres fallos. **Los tres
eran míos**; el LLM hizo lo que se le pedía en cada caso.

### 1. El arreglo de la tercera persona nunca llegó a enchufarse

El difunto seguía narrando en primera persona (*"Me siento en paz, como si al fin todo
el peso se hubiera soltado de mis hombros"*). `EN_TERCERA` estaba definido pero **no se
usaba en ninguna parte**: la sustitución que insertaba la rama en `_hablar()` no encajó,
porque el texto que buscaba tenía un comentario en medio.

**Lo grave no es el fallo, es la verificación.** Comprobé que la constante existía y que
el fichero compilaba, no que la rama fuera alcanzable. Verifiqué lo fácil en vez de lo
que importaba, que es exactamente el error contra el que va todo este documento.

Ahora se comprueba la alcanzabilidad: que `EN_TERCERA` aparezca **dentro** del cuerpo de
`_hablar`, que llame a `construir_epitafio` y que salga con `return` antes del prompt
normal.

### 2. El guardián gritaba lobo: veinte avisos falsos por narración

```
[fuga] campo txt/n llego sin traducir: 'bravery'
[fuga] campo txt/n llego sin traducir: 'spouse'
[fuga] campo txt/n llego sin traducir: 'Crossbow'
```

**Ninguna de esas era una fuga.** `bravery` y `spouse` son la salida correcta de
`enum_txt()`, y `Crossbow` u `Observation` son las **captions reales de DF** para esas
habilidades.

La causa fue el `siempre_enum=True` que se añadió tras la revisión externa: marcaba como
sospechoso cualquier token suelto en los campos de enum. Sobre-alcance. El guardián
volvió a la detección **solo por forma** (`ALL_CAPS`, `CamelCase`, guiones bajos).

El precio, y conviene decirlo: una palabra capitalizada suelta como `Syndrome` se cuela,
porque por forma es indistinguible de `Crossbow`, que es legítima. Ese caso se ataja en
origen con `enum_txt()` en el lado Lua, no en la red de seguridad.

**Lección**: una red que avisa de veinte cosas por narración, todas falsas, es peor que
no tener red — entierra el aviso verdadero cuando llegue.

### 3. Cinco muertes se narraron como una

La ventana anti-repetición que se añadió para los sucesos colectivos —un síndrome
afectando a media fortaleza— **suprimió cuatro de las cinco muertes**, porque todas
comparten el texto `"ha muerto"`.

Son cinco personas distintas, no una repetición. Ahora los sucesos que le pasan a alguien
en concreto (`muerte`, `locura`, `desaparicion`, `relacion`, `llegada`) están exentos de
esa ventana. Verificado que cinco muertes se cuentan las cinco **y** que un síndrome
compartido por cinco se sigue contando una sola vez.

### Volumen de sucesos: 52 a 70 por vuelta

Con 89 ciudadanos (llegó una oleada de migrantes) el vigía detecta entre 20 y 70 sucesos
en cada vuelta de cinco segundos. Los frenos están haciendo todo el trabajo de selección.

No es un fallo, pero sí el dato que confirma la duda de la revisión externa sobre el
ritmo: **hay muchísimo más material del que se puede contar**, así que el criterio de
qué merece contarse importa más que la detección. Es la pregunta de la sesión larga.

## Revisión de código: el epitafio se construye vacío

Encontrado **leyendo**, no ejecutando, al releer el proyecto entero. Es el mismo tipo de
fallo que el de `EN_TERCERA`: una rama que existe, compila y no llega a hacer lo que
dice su comentario.

### La cadena

`_hablar()` necesita el expediente completo del enano que disparó el suceso, así que:

1. Pide `enanos(n=1, detalle="completo")` — que devuelve **el primero del pool**, no el
   que se busca. Acierta solo por casualidad. Es una petición tirada casi siempre.
2. Si falla, `_buscar()` pagina en bloques de 20 con `detalle=completo`.

El problema es **para quién** se hace esto. Los sucesos de más peso —`muerte` (100),
`locura` (90), `desaparicion` (60)— se detectan precisamente porque el enano **ya no
está en `getCitizens()`**. Buscarlo ahí es buscarlo donde por definición no puede estar.

Y no para pronto: el `desde=` del lado Lua da la vuelta (`((desde + k) % #pool) + 1`),
así que **siempre** devuelve los 20 pedidos aunque el pool tenga 5. La condición de
corte `if len(enanos) < 20: break` no se cumple nunca. Se hacen las 10 páginas enteras.

### El coste

Con la medida del paso 0 (`completo` = 344 KB y 40 ms para 64 → ~5,4 KB y 0,63 ms por
enano), cada muerte cuesta:

| | peticiones | tráfico | Lua |
|---|---|---|---|
| el `n=1` que se descarta | 1 | 5 KB | 0,6 ms |
| las 10 páginas de `_buscar` | 10 | **~1,1 MB** | **~125 ms** |
| resultado | | | **`None`** |

Unos 12 frames de juego congelados, por partes, para no encontrar nada.

### Lo grave no es el coste, es lo que se pierde

Tras el `None`, `_hablar()` cae en `enano = evento["enano"]`, que es **el objeto de la
sonda**. Y `enano_sonda()` no trae `profesion`, ni `edad`, ni `relaciones`, ni
`habilidades`.

`construir_epitafio()` usa exactamente esos cuatro campos. En esta misma página está
escrito que el epitafio se hace *"con los datos reales del difunto —oficio, edad, a quién
dejaba atrás, en qué era bueno"*. **No es cierto en ejecución.** El prompt que sale de
verdad para cada muerte es:

```
Se llamaba Tosid Nishkesh, sin oficio, de ? anos.
Lo que ha ocurrido: ha muerto.
```

Sin la línea *"Dejaba atrás a: …"* y sin la de *"Se le daba bien: …"*. El cronista está
escribiendo epitafios de un desconocido, y la prosa sale correcta, que es lo que impide
notarlo.

Esto explica en parte el *"nadie reaccionó a la muerte"*: aunque el evento de relación
hubiera saltado, la pieza que nombra a la viuda nunca llegó al prompt.

### El arreglo

Un `id=` en el subcomando `enanos` del lado Lua, que resuelva por `df.unit.find(id)` en
vez de recorrer `getCitizens()`. Una petición, un enano, funciona igual para vivos que
para muertos. **Sin probar contra el juego: queda pendiente, no aplicado.**

Mientras tanto, el orden correcto sería capturar el expediente **antes** de que el enano
desaparezca —la sonda ya pasa por todos en cada vuelta— pero eso son 344 KB por vuelta y
está descartado por coste. El `id=` es el camino.

### Lo que se lleva de aquí

Van **cinco** defectos del mismo patrón: código escrito, comentario escrito, rama
inalcanzable o alimentada con datos que no tienen lo que promete. `CLAUDE.md` ya dice
*"comprobar que la rama es ALCANZABLE"*; hay que añadirle en la práctica **"y que los
datos que le llegan traen los campos que usa"**. Un `.get()` con valor por defecto no da
error: da `sin oficio` y `?`.

## Segundo hallazgo de la revisión: `relaciones` no trae el `id` del otro

`relationship_ids` **sí** es un vector de `unit_id`, pero `enano_tabla()` lo resuelve a
`{tipo, txt, quien}` y descarta el número. Hoy da igual: el prompt solo necesita el
nombre.

Para las conversaciones no da igual. Para poner a dos enanos a hablar hay que poder:
comprobar que el otro sigue vivo y en el mapa, pedir su expediente, y escribir la entrada
de memoria compartida con `participantes: [A, B]` — y las tres cosas se hacen por `id`,
no por nombre (los nombres se repiten en DF).

Es **el único cambio de extracción** que la fase de interacciones necesita, y va en la
misma línea que el `id=` de arriba.

## Lo que aporta el proyecto del traductor

Eugenio lleva en paralelo un traductor inglés→español para DF que lee el texto de la
memoria del juego, sin OCR. Su investigación está verificada contra el código fuente de
DFHack y `df-structures`, **no contra esta instalación**, así que aquí se anota como
*procedente de allí* y se confirma con el subcomando `ui`. Cuatro cosas nos cambian algo.

### 1. El overlay en Lua existe: el plugin en C++ se cae de la lista

`overlay.OverlayWidget`, documentado en `docs/dev/overlay-dev-guide.rst`. Se registra con
un global `OVERLAY_WIDGETS` y `--@ module = true`, y tiene `viewscreens`, `default_pos` y
`overlay_onupdate_max_freq_seconds`.

Es exactamente lo que hacía falta para pintar dentro del juego, y es **Lua, en la misma
carpeta donde ya vive `df_estado.lua`**. Un plugin en C++ ata a compilar y se rompe con
cada actualización de DFHack; esto no. Decisión tomada: **no se abre un proyecto de plugin
en paralelo.**

### 2. El «botón» ya existe, y es una línea

```
keybinding add Ctrl-P@dwarfmode/ViewSheets/UNIT df_estado pensar
```

DFHack ata una tecla a una **pantalla concreta** por su *focus string*. Con
`dfhack.gui.getSelectedUnit(true)` el script sabe a quién tiene abierto el jugador.

Es decir: la petición explícita no necesita que escribas un id en una consola. Abres la
ficha de un enano, pulsas la tecla, y habla **ese**. Los tres problemas del panel de
anuncios —de quién es, cuándo, dónde— desaparecen porque el contexto lo pone el jugador
al preguntar.

### 3. La ficha abierta trae la prosa que DF ya ha escrito

`df.global.game.main_interface.view_sheets` guarda `unit_health_raw_str[0].value`
(descripción física), `personality_raw_str` (párrafos de personalidad) y
`raw_thought_str` (pensamientos **en prosa**). Texto completo, escrito por DF.

El pero: **solo se rellena cuando el jugador abre la pestaña**. No hay API que genere esa
prosa — el changelog de DFHack lo dice, y el script `markdown` la consigue **simulando
clics del ratón con coordenadas fijas**, con su autor avisando de que se romperán.

Para el vigía automático eso lo descarta: no vamos a simular clics. Pero para la
**petición explícita es gratis**, porque el jugador ya está en esa pantalla y ya hizo el
clic. Y es material mucho mejor que nuestro cóctel de enums: prosa del propio juego, que
es la fuente mejor anclada que puede tener un prompt.
Lleva marcado interno que hay que quitar o proteger: `[B]` párrafo, `[R]` subbloque,
`[P]` redundante, `[C:r:g:b]` color.

### 4. `world.status.reports`: DF ya nos está contando lo que pasa

Cada anuncio y cada línea de combate viven en `world.status.reports[]` con `text`,
`color`, `id`, `year`, `time` y `pos`. Y `eventful.onReport(id)` avisa de cada uno nuevo.

Nosotros deducimos que alguien ha muerto **comparando dos sondeos** y preguntando después
qué fue de él. DF lo tiene escrito, en prosa, fechado y **con la posición**. Es una fuente
candidata mejor para toda una clase de sucesos, y drenarla es barato: guardar el último
`id` visto y pedir los posteriores.

No se cambia nada todavía: hay que medir cuántos reports genera una fortaleza por vuelta
antes de meterlos en el bucle. Pero la comparación de sondeos deja de ser la única vía.

### 5. Un fallo nuestro que sale de aquí: `Á Í Ó Ú` no existen en CP437

CP437 tiene las minúsculas acentuadas, `ñ Ñ É ü Ü ç Ç ¿ ¡` — **pero no `Á Í Ó Ú`**, y
`utf2df` sustituye por `?` lo que no puede mapear.

Nuestra sección *«Acentos y CP437: confirmado en las dos direcciones ✅»* de más arriba
**dio verde sin tocar este caso**: probó `î ê` al leer y `ú ó` al escribir, todo
minúsculas. Una respuesta del modelo que empiece por *"Últimamente"* o nombre a
*"Ángeles"* lleva saliendo con un interrogante en el juego.

Corregido: el lado Lua las degrada a `A I O U` antes de `utf2df`. Y el subcomando `ui`
mide el ida y vuelta de los quince caracteres del español **en la build instalada**, en
vez de fiarse de una tabla.

> La lección no es el carácter. Es que un ✅ sobre una muestra que no incluye el caso
> difícil no es una comprobación: es una coincidencia con buena presentación.

### Lo que NO adoptamos

- **`RunLua`**: su documento lo da por utilizable. Aquí se midió y no sirve —filtra por
  nombre de módulo con la condición invertida. Puede depender de la versión; en esta
  instalación está medido y falla. Se mantiene lo medido.
- **Barrido de `readTile`**: leer la pantalla celda a celda es su vía universal porque
  necesitan *todo* el texto. Nosotros leemos estructuras, no píxeles ni celdas. No aplica.
- **`luasocket` para que Lua llame fuera**: nuestro sondeo cuesta 5 ms y lo dirige Python.
  No hay motivo para invertir la dirección.

## Primera tirada del `id=` y del sondeo de interfaz ✅ y un bug nuevo

### El `id=` funciona

```
python df_llm.py hablar id=335 --prompt --seco
--- Meng Melbilikud, chief medical dwarf, 29 anos (1275 caracteres de prompt)
```

Oficio, edad, rasgos y habilidades, todo resuelto en **una** petición. Y el epitafio:

> *Meng Melbilikud, jefe médico de la fortaleza, ha muerto. Gran cultivador y talentoso
> poeta y orador, su vida terminó a los veintinueve años.*

Con `Se le daba bien: Growing (Great), Poetry (Talented), Speaking (Talented)` en el
prompt — la línea que **antes no llegaba nunca**. Falta confirmar la de *"Dejaba atrás
a:"*: Meng no tiene relaciones resueltas, así que hay que repetirlo con un enano casado.

### Bug nuevo, visible en el propio prompt

```
Lo que has sentido ultimamente: anything none; anything none; anything none;
                                anything none; anything none; anything none.
```

Seis. `anything` y `none` son las captions del valor `-1` de `emotion_type` y
`unit_thought_type`: **entradas vacías que DF deja en la cola del vector** cuando poda las
emociones viejas. `enano_tabla()` cogía las últimas N del vector, así que cogía justo esas.

**Uno de los siete bloques del prompt ha sido ruido desde el principio.**

Por qué no se vio: `enano_sonda()` **sí** elige por `(year, year_tick)`, así que el
disparador del suceso llegaba correcto y la respuesta sonaba bien. Lo estropeado era el
bloque de contexto, que nadie lee tan de cerca. Mismo patrón que el epitafio vacío: prosa
buena tapando datos malos. Van seis.

Corregido: ordenar por marca de tiempo, descartar las que tengan tipo **y** causa
negativos, y devolver `emo_utiles` para que la próxima vez se vea en el JSON y no en el
prompt. El lado Python pasa a coger las **primeras** N, no las últimas.

### El sondeo de interfaz: todo verde

| | |
|---|---|
| `plugins.overlay`, `plugins.eventful`, `repeat-util`, `gui.widgets` | **presentes** |
| `showZoomAnnouncement`, `showPopupAnnouncement`, `makeAnnouncement` | **presentes** |
| `getCurFocus`, `getFocusStrings`, `getSelectedUnit`, `getWidget` | **presentes** |
| colores | `YELLOW=14 LIGHTCYAN=11 LIGHTMAGENTA=13 LIGHTGREEN=10 WHITE=15 GREY=7` |

**El plugin en C++ queda descartado con medida, no con lectura de fuente.** Y aparece algo
que no esperábamos: `showZoomAnnouncement` existe, así que un anuncio puede llevar
posición y **la cámara salta al enano al pulsarlo**. Eso resuelve el «dónde» del panel sin
overlay ninguno.

### `world.status.reports`: 2004, y casi todo es ruido

El último anotado: `Make yarn trousers (6) has been completed.`

Está **dominado por finalización de trabajos**. Como fuente de sucesos sigue valiendo —las
muertes y el combate están ahí, con causa y posición, que hoy no tenemos— pero **hay que
filtrar por tipo**, no drenarlo entero. La duda de si era un chorro queda respondida: lo es.

### CP437 confirmado en la build

Sobreviven 11 de 15. `Á Í Ó Ú` vuelven como `?`. Los demás (`á é í ó ú ñ Ñ ü É ¿ ¡`),
intactos. El filtro que degrada a `A I O U` se queda.

## La fortaleza SÍ reacciona a las muertes (sin habérselo programado)

La crónica del año 105 responde la pregunta que llevaba abierta desde el primer test de
muerte, y la respuesta tiene dos mitades.

**Nish Dedukudib muere el mes 8, día 16.** Lo que pasa después, sin que nada del código lo
busque:

> **día 20 · Oddom Stonoslan** — *"...y por un momento hasta **el horror de ver morir a
> alguien** se aleja un poco."*

> **día 20 · Cerol Urdimatöl** — *"...por un momento **olvido el horror que vi hace poco**
> y el agobio que me aplasta cada día."*

> **día 27 · Tun Ducimlòr** — *"...aunque la fortaleza esté **llena de muerte y hedor**,
> todavía tengo a alguien de mi sangre con quien compartir un momento sencillo."*

Tres enanos distintos, en tres narraciones independientes, mencionan las muertes. Y no hay
una línea de código que lo busque: **DF mete la muerte en las emociones de quien la
presencia**, la sonda la recoge como emoción nueva y llega al prompt. El sustrato social
ya estaba ahí.

**La otra mitad: Tirist Atírshis, la viuda, no dijo nada.** Tosid Nishkesh muere el mes 7,
día 25; Tirist aparece en la crónica el mes 2 y el mes 4, y **no vuelve a aparecer después
de la muerte**. Sí funcionó el aviso genérico en otro enano:

> **Dôbar Avuzidith** — `has perdido a alguien importante de tu vida` → *"He perdido a
> alguien muy importante para mí. Me duele tanto que no sé qué hacer ahora."*

Así que el evento de relación **existe y dispara**. Lo que falta no es la detección: es que
la viuda gane la competición de una narración por vuelta, y que el texto pueda decir **a
quién** ha perdido. Lo primero es peso; lo segundo es el `id` de la relación, que ya está.

> Que la reacción genérica funcione y la dirigida no, es exactamente la razón de ser de la
> tarea de las conversaciones. Y ahora sabemos que el material está en los datos.

### Defectos ya corregidos que la crónica documenta

Esta crónica cruza varias versiones, así que sirve de museo de lo que ya está arreglado:

| Línea | Lo que dijo | Estado |
|---|---|---|
| `Mi ánimo ha mejorado **de categoría 2 a 3**` | recitó la redacción del detalle | corregido |
| `aunque **mi estrés sigue en 11440**` | recitó la cifra cruda | corregido |
| `**¡Euphoria!** ¡Qué alegría...` | repitió el identificador del enum | corregido |
| `placer profundo **cerca de mi propia calidad al construir**` | tradujo la caption literal | corregido |
| Tosid y Nish, muertos, narrando en primera persona | el difunto hablaba | corregido |
| `mientras afilo mi pico en la penumbra` | la muletilla del «mientras» | corregido |

Los seis salen de la misma raíz y ninguno fue culpa del modelo.

## Los dos arreglos, confirmados — y el género, que no habíamos mirado

### Lo que se cierra

Las emociones ya son reales, y el `id=` resuelve a un enano casado:

```
Lo que has sentido ultimamente: interest after watching a performance; fondness talking
with a relation; ...; anything saw somebody's dead body.
Personas de tu vida: Tosid Nishkesh (spouse).
...
Dejaba atras a: Tosid Nishkesh (spouse).
```

**`Dejaba atrás a:` sale.** El epitafio vacío queda cerrado, medido contra el juego.

Y hay un detalle que vale la pena mirar: entre las emociones de Tirist está
`saw somebody's dead body`. **Es la muerte de su marido, registrada en sus datos.** El
material para que la viuda hable estaba ahí todo el tiempo; lo que faltaba era llevarlo al
prompt.

### El género: el modelo tira una moneda, y una distinta por frase

Sin el sexo en el prompt, el mismo par de enanos salió con géneros **contradictorios entre
tiradas**:

> *"Tirist merece algo mejor que **un marido** que se queda mirando el vacío"* — Tosid
>
> *"...lo mucho que me gustaría volver a **verla**"* — Tirist, hablando de Tosid
>
> *"Tirist Atírshis, minero de treinta y cuatro años, **esposo** de Tosid Nishkesh"* — la
> crónica

No es que acierte o falle: **no tiene el dato y lo sortea cada vez**.
**Ninguno de los dos géneros estaba en el prompt.** No se los inventó por capricho: no
tenía el dato. `spouse` no dice esposo ni esposa, `Miner` está en inglés y no marca nada,
y el sexo del enano no se extraía.

El proyecto del traductor lo tenía señalado como su ventaja principal —*"el español
necesita saber el sexo del enano"*—. A nosotros nos afecta más, porque generamos prosa
entera en vez de traducir frases.

Corregido en tres sitios:

1. `sexo_de()` en el lado Lua. El camino principal lee `unit.sex` como **`pronoun_type`**,
   un enum que **se nombra a sí mismo**: devuelve `she` o `he`, así que no hay que mapear
   un 0 y un 1 de memoria —que es justo donde se cuela un error invertido y silencioso—.
   Detrás quedan `isFemale`/`isMale`, y en último lugar el número crudo, marcado como
   `(SUPUESTO)` en `sexo_via` para que se vea. **Si no se resuelve, el campo se omite.**
2. El sexo de **cada relación** también: `Tosid Nishkesh (spouse, hombre)`. El género de
   «esposo» depende del otro, no de quien habla.
3. Instrucción explícita (*"Eres MUJER: habla de ti en femenino"*). Dar el dato sin pedir
   la concordancia no basta: el modelo arrastra el género del oficio en inglés.

Y un cuarto que es culpa nuestra y no del modelo: **`"Por dentro te sientes muy
tranquilo"` estaba en masculino para toda la fortaleza.** Era una frase escrita por mí, no
un dato de DF. Reescrita en sustantivos: *"por dentro sientes una gran calma"*.

> Cada frase que escribimos nosotros en el prompt es tan revisable como las que escribe el
> modelo. Cuatro de los cinco defectos de texto venían del prompt; este es el quinto.

### Y un error mío al documentarlo: prosa generada tomada por dato

Al escribir el hallazgo puse *"Tirist Atírshis es **mujer**"* y lo apoyé en que Tosid se
había llamado a sí mismo *"un marido"*. Pero **esa frase la escribió el modelo sin tener
el dato**: era exactamente una de las tiradas de moneda que el hallazgo denunciaba.

Usé la salida del modelo como evidencia de un hecho del mundo, en la misma página donde
estaba explicando que no se puede hacer eso.

**Resuelto, y yo estaba equivocado.** La tirada dice `via: pronoun_type=he` y la ficha del
enano en el juego dice hombre. Dos fuentes independientes, ninguna de ellas un LLM, y las
dos coinciden: **Tirist Atírshis es hombre**. Lo que yo había dado por hecho salía de una
frase generada sin el dato.

> La regla 1 —cada afirmación con su ancla— se aplica a lo que escribimos nosotros
> exactamente igual que a lo que escribe el modelo. Es el segundo defecto de esta ronda
> que no es del LLM sino del prompt o del documento.

### El cronista se inventó cómo murió

> *"Tirist Atírshis, minero de treinta y cuatro años, esposo de Tosid Nishkesh, **murió
> mientras trabajaba en las minas**. [...] dejó atrás a su cónyuge **sin que se conozcan
> más detalles de su fallecimiento**."*

El prompt decía `Lo que ha ocurrido: ha muerto.` y nada más. El sitio y la circunstancia
son **invención**, en el sentido exacto de la doctrina: una afirmación sobre el mundo que
no sale del estado de DF.

Lo revelador es la segunda mitad. El modelo **sabe** que no tiene los detalles —lo dice—,
y aun así ya había dado el lugar por cierto en la primera frase. No es que mienta: es que
un hueco de información en un prompt narrativo se rellena solo.

Corregido con una prohibición explícita: *"no inventes dónde ni cómo ocurrió; si no está
escrito, no se sabe, y una crónica no rellena huecos"*. Queda pendiente comprobar que
funciona, y **anotar la tasa** como manda la regla 5.

La solución de fondo no es el freno, sino el dato: `world.status.reports` tiene la causa
real de cada muerte. Cuando esa fuente entre, el hueco desaparece.

### Emociones repetidas

`interest after watching a performance` salía **tres veces** de los seis huecos. DF guarda
una entrada por cada vez que ocurre algo. Ahora se deduplica por (tipo, causa) quedándose
con la más reciente, y las entradas con un campo a `-1` mandan cadena vacía en vez de
`anything` o `none`.

## Las captions de DF vienen a medias, y el modelo las completa

Con el género ya resuelto, la misma tirada dejó ver otra cosa:

```
Lo que has sentido ultimamente: ...; loneliness after varying; uneasiness after varying; ...
```

Y la respuesta:

> *"...esta soledad me pesa un poco después de **tanto variar de un lado a otro**."*

`after varying` no significa nada por sí sola. Es una caption **incompleta**, y DF la
completa con el campo `subthought` de la emoción — un campo que estábamos tirando.

Es el mismo mecanismo que la muerte inventada en las minas, con otra cara: **un hueco en
el prompt no se queda vacío, se rellena**. Y esta vez el hueco no venía de un dato que
falta, sino de un dato que llega partido por la mitad.

Dos slots de los seis eran fragmentos. Lo que se ha hecho, por ahora, es **medirlo**:

```
python df_llm.py emociones id=311
```

devuelve la fila cruda de cada emoción —tipo, causa, `subthought`, la caption tal cual la
da DF sin quitarle los corchetes— y de paso pregunta si existe alguna función de DFHack
que componga el texto entero. Con eso se decide si el arreglo es resolver `subthought`, o
si hay que descartar las causas fragmentarias por la regla 2 (si un dato no se resuelve,
se omite).

No se ha tocado el prompt todavía: primero ver qué da DF, luego arreglar. Es la tercera
vez en esta ronda que el orden importa.

## Las 72 emociones de Tirist: los corchetes eran huecos, y hay un hallazgo grande

`emociones id=311` devuelve 72 filas. Tres cosas.

### 1. Nuestro tratamiento de los corchetes estaba mal

Las captions crudas de DF son **plantillas**:

```
'after [varying]'                          'due to [syndrome]'
'upon improving [skill]'                   "saw [somebody]'s dead body"
'near a [quality] tastefully arranged [building]'
'after sleeing in a [quality] bedroom'     'talking with a [relation]'
```

`enum_txt()` hacía `gsub('%[(.-)%]', '%1')`, que **quita los corchetes y deja la palabra
de dentro como si fuera contenido**. A veces cuela por casualidad —`saw somebody's dead
body` se lee bien— y a veces sale un sinsentido: `after varying`, `upon improving skill`,
y `due to syndrome`, que es de donde venía aquel *"¡Euphoria! ¡Qué alegría!"*.

Quien rellena el hueco es **`subthought`**, y su significado **depende del tipo de
pensamiento**. Y no hay atajo: `getThoughtText`, `getThoughtDescription` y
`getUnitThought` **no existen** en esta build. Lo comprobamos preguntando.

### 2. El presupuesto del prompt se lo come una sola causa

De las 72 filas, más de la mitad son `WatchPerform`. La deduplicación por (tipo, causa)
que se puso esta tarde estaba más justificada de lo que pensábamos: sin ella, seis huecos
de emoción se llenaban de la misma función de teatro.

### 3. `SawDeadBody` aparece doce veces, con doce `subthought` distintos

```
UNEASINESS  SawDeadBody  867 · 868 · 869 · 870 · 1260 · 1261
ANYTHING    SawDeadBody  1007 · 1012 · 1262 · 1264 · 1267
```

La caption es `"saw [somebody]'s dead body"`. **Si ese `subthought` es la persona, DF está
registrando quién vio el cadáver de quién.**

Eso no es un detalle de formato: es **el disparador de las conversaciones, servido por el
juego**. No hay que inferir que la viuda debería reaccionar cruzando `relationship_ids`
con una lista de bajas — DF ya anota, en las emociones de Tirist, que vio el cuerpo de
alguien concreto, doce veces y con doce identidades distintas.

La comprobación tiene un resultado **predicho**: Tosid Nishkesh, su cónyuge, murió en esta
partida. Si uno de esos doce números resuelve a Tosid, queda confirmado de golpe el
significado del campo y el mecanismo entero.

Por eso el sondeo no adivina: prueba **seis interpretaciones** de cada `subthought`
—`need_type`, `job_skill`, `unit_relationship_type`, `building_type`, figura histórica y
unidad— y devuelve las que resuelven a algo. La caption dice cuál encaja.

**Nada del prompt se ha tocado todavía.** Primero ver, luego arreglar.

## `subthought` resuelto — y el duelo estaba en los datos, tapado por nuestra propia regla

### La predicción falló

Dije que si uno de los doce `SawDeadBody` resolvía a Tosid Nishkesh, quedaba confirmado
todo. **No aparece.** Los once nombres son otros: Dema Asriemim, Dastot Likotnär,
Urvad Kilrudlised, Alåth Abansákrith, Ago Snangnûng, Secen Alocnesim, Atup Gulokasp,
Gorbe Gujegbekor Donu Cish, Chrobothlamer, Gachayrsnus, Aweme Thiwafamime.

Y hay una razón para desconfiar del camino entero: **los ids de figura histórica son
densos**, así que `df.historical_figure.find(n)` devuelve un nombre para casi cualquier
número. Que resuelva no prueba nada. Se ve en las filas pequeñas: `sub=17` también
«resuelve» a *Dema Oñecorma*, y ahí 17 es claramente un tipo de relación.

**`SawDeadBody` se queda sin rellenar.** Poner el nombre equivocado de un muerto en el
prompt sería el peor fallo posible de todos los que llevamos. Se queda en
`saw somebody's dead body`, que es cierto.

### Cuatro huecos sí quedan confirmados, y por coherencia

No por que «resuelvan», sino porque **cuadran con otro dato del mismo enano**:

| Hueco | `sub` | Resuelve a | Por qué se acepta |
|---|---|---|---|
| `[varying]` | 8 | `need=BeWithFriends` | la emoción de esa fila es **LONELINESS** |
| `[varying]` | 2 | `need=PrayOrMeditate` | la emoción es UNEASINESS |
| `[skill]` | 15 | `skill=CLOTHESMAKING` | Tirist **tiene** Clothes Making entre sus habilidades |
| `[building]` | 8 | `edificio=Door` | y `[building]` de otra fila da Table, Chair |
| `[relation]` | 17 | `rel=AcquaintancePassing` | ese mismo 17 sale en los **tres** pensamientos sociales |

`getThoughtText`, `getThoughtDescription` y `getUnitThought` **no existen**: confirmado, no
hay atajo.

### Y dos fallos en mi propio relleno, que el test destapó antes de que llegaran al juego

Probando la función con las captions **reales** medidas:

1. Emparejaba el valor con **el primer hueco libre**, no con el que le tocaba.
   `near a [quality] tastefully arranged [building]` con `table` daba
   *"near a table tastefully arranged"*. Ahora se empareja por el **nombre** del hueco.
2. `after [varying]` con `be with friends` daba *"after be with friends"*, que no es
   inglés. El tipo de pensamiento se llama `NeedsUnfulfilled`, así que se redacta
   *"after an unmet need to be with friends"* — describirlo, no inventarlo.

Y `due to [syndrome]` se descarta entero: sin el hueco queda *"due to"*, colgando.
Antes salía *"due to syndrome"*, que es de donde venía el *"¡Euphoria!"*.

## El duelo estaba ahí desde el principio, y lo tapaba nuestra regla de selección

Entre las 72 filas, dos veces:

```
SADNESS   LoveSeparated   'at being separated from a loved one'
```

**Sin hueco, sin ambigüedad, sin nada que resolver.** Es la viuda —o el viudo— y estaba
en la primera medición, mientras yo perseguía `SawDeadBody`.

**Por qué no llegó nunca al prompt:** seleccionábamos los seis pensamientos **solo por
recencia**, y este enano tiene **veintiuna** entradas de `WatchPerform`. La tristeza por
la pareja quedaba enterrada bajo el teatro.

> Lo más reciente no es lo que más pesa. Un prompt que solo mira el reloj cuenta las
> funciones de teatro y se calla el duelo.

Corregido: **mitad de los huecos por recencia, mitad por fuerza**. Lo que acaba de pasar
sigue entrando, y lo que pesa deja de perderse.

Esto cambia el plan de la tarea 9. No hace falta cruzar `relationship_ids` con una lista
de bajas para saber que alguien ha perdido a su pareja: **DF lo anota como emoción**, con
su propia caption, y solo había que dejarla llegar.

## El duelo llega al prompt ✅ y aparece una invención de otra clase

```
Lo que has sentido ultimamente: delight after watching a performance; satisfaction at
work; uneasiness after an unmet need to pray or meditate; sadness at being separated
from a loved one; pleasure near a tastefully arranged chair; uneasiness saw somebody's
dead body.
```

Los seis huecos funcionan y **la tristeza por la pareja está dentro**. También se ve el
relleno de plantilla trabajando: `after an unmet need to pray or meditate` y `near a
tastefully arranged chair` —que con el emparejamiento por posición habría salido *"near a
chair tastefully arranged"*—.

Y la respuesta:

> *"Echo de menos a Tosid de una forma que me aprieta el pecho, y al mismo tiempo me
> invade una alegría tonta al recordar cómo brillaba su mirada **después de la última
> función que vimos juntos**."*

La primera mitad es exactamente lo que buscábamos. **La segunda es una invención nueva, y
de otra clase.**

### La costura

`delight after watching a performance` está en el prompt. `sadness at being separated from
a loved one` está en el prompt. **"que vimos juntos" no está**, ni que ella estuviera allí,
ni que fuera la última, ni cómo tenía la mirada.

No es rellenar un hueco, como las minas del epitafio o el `after varying`. Aquí **los dos
ingredientes son ciertos y la unión es falsa**. El modelo puede señalar dos campos reales
del prompt y aun así haber afirmado algo que no sostiene ninguno de los dos.

La regla 1 de la doctrina —cada afirmación con su ancla— **no basta contra esto**, porque
formalmente hay ancla. Hace falta decirlo aparte.

### El freno, y lo que deliberadamente no frena

Prohibir toda conexión mataría lo único que hace que esto suene a persona. Así que el
freno separa dos cosas:

- **Mezclar sentimientos**: permitido. *"Estoy en paz y a la vez le echo de menos"* es
  interioridad, no afirmación sobre el mundo.
- **Fabricar sucesos**: prohibido. Con quién estabas, dónde, qué pasó antes o después.

> Cuantas más piezas ciertas le das, más costuras posibles hay. Esto **crece** cuando
> entren las conversaciones: dos enanos son el doble de anclas y el cuádruple de uniones.

### Cómo se mide

`hablar id=N --veces=10 --seco` repite el **mismo** prompt diez veces y numera las
respuestas. Es la herramienta de la regla 5: contar cuántas de diez fabrican una escena.
Sin ese número no se pasa a las conversaciones.

## Primera medición de la tasa de invención: 0 de 10 ✅ y un efecto secundario

Diez respuestas al **mismo** prompt, con el freno de la costura puesto. Contando escenas
fabricadas —un acompañante, un lugar, un antes o un después que no estén en el prompt—:

| | |
|---|---|
| Escenas fabricadas | **0 de 10** |
| Antes del freno | 1 de 1 (*"la última función que vimos juntos"*) |

Ninguna de las diez inventa con quién estaba, dónde ocurrió, ni qué pasó antes. Todo lo
que afirman se puede señalar en el prompt: la mesa, la separación de Tosid, la inquietud
por no haber rezado, la satisfacción del trabajo, la amargura de la discusión.

Y lo que **sí** hacen —mezclar sentimientos— es lo que queríamos conservar:

> *"La mesa bien puesta me ha dejado una calma que casi borra la tristeza de no tener a
> Tosid cerca."*

Es interioridad, no una afirmación sobre el mundo. El freno distingue bien las dos cosas.

La muestra es de diez y de un solo enano: no dice que la costura sea imposible, dice que
**dejó de aparecer en las diez veces que antes aparecía**. Se vuelve a medir en cuanto
entren las conversaciones, que es donde la superficie se multiplica.

### Lo único que roza la raya

Dos marcas de tiempo que nadie puso: *"la satisfacción del trabajo **de hoy**"* y *"la
mesa bien puesta **de ayer**"*. No afirman un suceso nuevo —el trabajo y la mesa están en
el prompt—, solo lo fechan. Queda anotado como el borde de la categoría, no como fallo.

### El efecto secundario: quitar invención quitó variedad

**Siete de diez empiezan por la misma frase**: *"La mesa bien puesta me ha dejado..."*. Y
*"Echo de menos a Tosid con una tristeza que se me agarra al pecho"* aparece **palabra por
palabra** en cuatro. Las respuestas 8 y 9 son casi la misma.

La causa no es el freno: es que **la lista de emociones iba siempre en el mismo orden** y
el modelo se agarra al primer elemento. Es el mismo sesgo de posición que la muletilla del
«mientras», con otra forma.

Y se arregla igual que aprendimos entonces: **barajando y sin decírselo**. Pedirle *"no
empieces por el primero"* le mete la idea en el contexto, que es exactamente lo que falló
la otra vez. El orden de esa lista no significa nada —la selección ya se hizo en Lua, por
recencia y fuerza—, así que perderlo no cuesta nada.

Comprobado sobre 200 prompts: la primera posición se reparte 60/49/49/42 entre las cuatro
emociones, en vez de caer siempre en la misma.

> Un freno contra la invención estrecha el espacio de lo que se puede decir. Si además el
> prompt tiene un sesgo de posición, lo que queda es una sola frase repetida. **Hay que
> mirar las dos cosas juntas**: bajar la invención sin mirar la variedad da un enano que
> no miente y no dice nada.

## Segunda tirada: el barajado ayuda a medias, y la medición estaba coja

### Los aperturas

| | antes | después |
|---|---|---|
| La frase de apertura más repetida | 7 de 10 | **3 de 10** |

Pero aparece un segundo imán: **cinco de diez** empiezan por *"Echo de menos a Tosid…"*.
Entre los dos, ocho de diez arrancan igual.

Y el barajado no puede hacer nada contra eso, porque **la causa es otra**. Los dos imanes
son (a) la línea fija del estrés —*"Por dentro sientes una gran calma"*, que no está en
ninguna lista barajable— y (b) la emoción que más pesa. Lo primero fue sesgo de posición y
se arregló; esto es **saliencia**, y es distinto.

Conviene separar dos cosas que estaba mezclando:

- Que **mencione a Tosid** en cinco de diez **no es un fallo**. Está de duelo. Una persona
  también volvería sobre eso.
- Que use **las mismas palabras** sí lo es. Y de hecho solo tres respuestas (1, 2 y 4) son
  literalmente la misma; las demás son variaciones — *"se me agarra al pecho"*, *"se me
  clava en el pecho"*, *"una calma que casi duele"*.

### La costura vuelve, más pequeña

```
9 · "como si parte de mí siguiera dormido en NUESTRA cama"
10 · "como si una parte de mí se hubiera quedado dormida DESDE QUE NOS SEPARARON"
```

Dos de diez. La primera une `contentment after sleeping in a bedroom` con `spouse` y
concluye una cama compartida. La segunda convierte *"at being separated"* —pasivo, sin
agente— en *"nos separaron"*, que sí tiene agente.

Es el mismo mecanismo de la *"última función que vimos juntos"*, a menor escala. **El
freno lo redujo, no lo cerró.**

### Lo honesto sobre las dos tiradas

Tirada 1: 0 de 10 escenas, 2 marcas de tiempo en el borde.
Tirada 2: 0 de 10 escenas, 2 costuras pequeñas.

Con muestras de diez, **la diferencia entre las dos está dentro del ruido**. Lo único
sólido es la comparación con el antes del freno, donde la costura salió en la única
respuesta que había. No se va a declarar una tendencia con esto: ya nos pasó con la
medición de rasgos, donde la métrica se invirtió entre tiradas y se reportó como nula.

### Y el fallo de método: la medición tenía la memoria apagada

`construir_prompt()` acepta `recuerdos`, y `para_prompt()` de `df_memoria` construye
exactamente esto:

> *"Cosas que ya has dicho en voz alta. NO las repitas ni te contradigas con ellas."*

El vigía se los pasa. **La orden `hablar` no.** O sea que llevamos dos tiradas midiendo la
repetición de un enano **sin memoria**, y culpando al prompt de un problema que el
proyecto ya tiene resuelto por otro lado.

Añadido `--memoria`: cada vuelta ve lo que dijo en las anteriores. Escribe en
`memoria_prueba/`, aparte de la de verdad. Con eso la medición prueba el sistema entero y
no una pieza suelta.

> Tercer defecto de esta ronda que no está en el modelo: uno en el prompt, uno en mi
> documento, y este en el banco de pruebas. Un banco que apaga una pieza sin decirlo mide
> otra cosa distinta de la que crees.

## Tercera tirada: la memoria empeoró la repetición y rompió el personaje

Resultado **negativo**, y de los útiles.

### El modelo se salió del personaje

Respuesta 6 de 10, después de dos frases correctas:

> *"(Lo siento, pero no puedo continuar con este roleplay porque repetí frases que ya
> habías dicho antes, lo cual va en contra de tus instrucciones.)"*

En seco no pasó nada. **Con el vigía en marcha, eso se anuncia en el juego como si lo
hubiera dicho el enano**, y acaba en la crónica.

Es la primera vez que vemos este fallo, y enseña algo que no teníamos: **hacía falta un
filtro de salida**. Todo lo construido hasta ahora vigila lo que *entra* al prompt
—`legible()`, los huecos de plantilla, el género, la costura—. Nada miraba lo que *sale*.
Y ningún prompt es infalible.

`limpiar_meta()` trabaja **por frases**, no sobre el texto entero: esa respuesta tenía dos
frases buenas y una coletilla mala. Tirarlo todo habría perdido texto válido; dejarlo
entero habría anunciado la coletilla. Probado con el caso real, con una respuesta que es
solo meta, y con un enano que se disculpa legítimamente —*"Lo siento por lo que dije ayer
en la forja"*—, que **no** se filtra.

### Y la memoria, tal como estaba escrita, empeoraba lo que venía a arreglar

`para_prompt()` pegaba las tres respuestas anteriores **enteras** y decía *"NO las
repitas"*. Lo que salió:

- Las respuestas 2, 3, 4 y 6 comparten casi palabra por palabra *"Echo de menos a Tosid
  con una tristeza que se me agarra al pecho, ojalá estuviera aquí para compartir esta paz
  que llevo dentro"*. La 6 es **copia literal de la 3**.
- Las 7, 8, 9 y 10 convergen en otro bloque igual de parecido entre sí.

O sea que **ver su propio texto lo ceba**: las respuestas se parecieron **más**, no menos.

Y esto ya estaba escrito en `CLAUDE.md`, con la muletilla del «mientras»: *"no se arregla
diciendo «no uses esa palabra»: nombrarla la vuelve a meter en el contexto"*. Lo
aprendimos con **una palabra** y lo repetimos con **párrafos enteros**, en otro fichero.

Encima, la prohibición es **imposible de cumplir**: solo hay tantas formas de decir «echo
de menos a mi pareja». Puesto entre repetirse y desobedecer, el modelo hizo lo tercero:
salirse a explicar el problema.

### Lo que se cambia

`para_prompt()` manda el **tema**, no la prosa —el campo `detalle`, que lo escribimos
nosotros— y pide **avanzar** en vez de prohibir:

> *Ya has hablado en voz alta de esto: sadness at being separated from a loved one;
> pleasure near a tastefully arranged table. Hoy fíjate en otra cosa de las de arriba.*

### El recuento de las tres tiradas

| | escenas fabricadas | apertura más repetida | fuera de personaje |
|---|---|---|---|
| sin freno | 1 de 1 | — | — |
| freno | 0 de 10 | 7 de 10 | 0 |
| + barajado | 0 de 10 | 3 de 10 | 0 |
| + memoria (prosa) | 0 de 10 | 4 de 10 | **1 de 10** |

Las escenas fabricadas se mantienen en cero en las tres. Lo demás se mueve poco y con
muestras de diez; **no se declara ninguna tendencia** más allá de eso.

> Cuarto defecto de esta ronda fuera del modelo: el prompt, mi documento, el banco de
> pruebas, y ahora una pieza que hacía lo contrario de lo que decía su nombre.

## Cuarta tirada: la mejor hasta ahora, y un fallo mío en 7 de 10

### Lo que se cierra

| | |
|---|---|
| Fuera de personaje | **0 de 10** — el filtro de salida no tuvo que recortar nada |
| Respuestas idénticas | **0** — en la tirada anterior la 6 era copia literal de la 3 |
| Apertura más repetida | 3 de 10 (*"La verdad es que me siento en paz"*) |
| Escenas fabricadas | 0 de 10 |

La memoria por temas hace lo que se le pide sin cebar la repetición, y las diez recorren
material distinto: la discusión, la mina, la planta, la ausencia de Tosid, la calma.

### Pero siete de diez dicen que mejoró una planta

> *"Me alegra haber **mejorado esa planta**"* · *"Saber que **mejoré las plantas**"* ·
> *"lo bien que ha quedado **la planta que mejoré**"*

Tirist no mejoró ninguna planta. Mejoró **su habilidad** de cultivo.

La culpa es del relleno que puse ayer. `upon improving [skill]` con el valor a secas queda
**`upon improving plant`** — y eso, en inglés, significa exactamente lo que el modelo
escribió. **No se lo inventó: lo leyó bien.** `improving` pide una competencia y le dimos
una cosa.

Es una categoría que no teníamos: **el hueco se rellenó con el valor correcto y la frase
montada dice otra cosa**. Un dato bueno dentro de una frase mal construida hace el mismo
daño que un dato inventado, y encima tiene mejor aspecto.

Corregido: cada hueco lleva su **forma**, no solo su valor.

| Hueco | Antes | Ahora |
|---|---|---|
| `[skill]` | `upon improving plant` | `upon improving **skill at** plant` |
| `[relation]` | `talking with a acquaintance passing` | `talking with a **person of the** acquaintance passing **sort**` |
| `[varying]` | `after varying` | `after **an unmet need to** be with friends` |

Y la lección de método: **la forma se comprueba leyendo la frase entera montada**, no
viendo que el valor resolvió. Ayer di por bueno `[skill]` porque `sub=15` devolvía
`CLOTHESMAKING` y eso cuadraba con las habilidades del enano. Cuadraba el **dato**. La
**frase** no la leí.

### Roza la raya, 1 de 10

> *"el vacío que dejó Tosid **al marcharse**"*

`at being separated from a loved one` es pasivo y sin agente; *"al marcharse"* pone a
Tosid haciendo algo. Misma familia que el *"nos separaron"* de la tirada anterior, y con
la misma frecuencia baja.

### Las cuatro tiradas

| | escenas | idénticas | apertura top | fuera de personaje |
|---|---|---|---|---|
| freno | 0/10 | — | 7/10 | 0 |
| + barajado | 0/10 | — | 3/10 | 0 |
| + memoria en prosa | 0/10 | **1** | 4/10 | **1/10** |
| + memoria por temas + filtro | 0/10 | **0** | 3/10 | **0** |

## Quinta tirada: la planta arreglada, y aquí se para de ajustar el prompt

### La habilidad ya no es una cosa

> *"cómo va mejorando **mi habilidad con las plantas**"* · *"haber **mejorado un poco más
> con las plantas**"* · *"**mejorado con las plantas**"*

De **7 de 10 mal** a **0**. Y el otro relleno también se lee bien:

> *"ese aburrimiento por **no haber podido crear algo con mis manos**"*

Eso es `NeedsUnfulfilled` con su `need_type` puesto, en una frase que dice lo que el dato
dice. El sistema de huecos está funcionando.

### Lo que queda, y por qué se deja

| | |
|---|---|
| Fuera de personaje | 0 de 10 |
| Respuestas idénticas | 0 |
| «Mejoré una planta» | **0** (era 7 de 10) |
| Afirmaciones sin ancla | **2 de 10** |

Las dos:

> 3 · *"me alegra saber que **pronto podré volver a verla**"* — un reencuentro futuro que
> no está en ninguna parte.
> 5 · *"cómo va creciendo **lo que planté**"* — la habilidad es de cultivo; que él plantara
> algo concreto, no está dicho.

Y un tic nuevo: **`"La verdad es que"` abre 5 de 10**. Es muletilla, no falsedad.

**Aquí se para.** Van cinco tiradas ajustando el prompt y las últimas tres se mueven
dentro del ruido con muestras de diez. Además, la muletilla no se puede atacar
nombrándola —esa lección ya está escrita y ya la hemos violado una vez— y no tiene el
arreglo estructural que sí tenían las anteriores. Seguir aquí es pulir la superficie del
único escalón que ya está medido, en vez de subir al siguiente.

### El arco entero, en una tabla

| | escenas | idénticas | apertura top | fuera de personaje | «planta» |
|---|---|---|---|---|---|
| sin freno | 1 de 1 | — | — | — | — |
| freno de la costura | 0/10 | — | 7/10 | 0 | — |
| + barajado | 0/10 | — | 3/10 | 0 | — |
| + memoria en prosa | 0/10 | 1 | 4/10 | **1/10** | — |
| + memoria por temas + filtro | 0/10 | 0 | 3/10 | 0 | **7/10** |
| + forma de los huecos | **2/10** | 0 | 5/10 | 0 | **0** |

Lo único que sale de estas cinco tiradas como resultado sólido son **dos piezas de
arquitectura** —el filtro de salida y la memoria por temas— y **dos defectos de datos**
—la forma de los huecos y el orden de selección—. Los números de estilo se mueven poco y
no sostienen ninguna conclusión.

> Y de los defectos encontrados en toda la ronda, **ninguno estaba en el modelo**: uno en
> el prompt, uno en mi documentación, uno en el banco de pruebas, uno en una pieza que
> hacía lo contrario de su nombre, y uno en la forma de una frase en inglés.

## La sonda, al día — y el bloqueante de la viuda, quitado

Todo lo aprendido ayer se había aplicado a `enano_tabla()`, el expediente completo. Pero
**el vigía no decide con el expediente: decide con la sonda**, que se había quedado atrás.

Tres cosas que le faltaban:

1. **Los huecos de plantilla.** `emo_causa` salía por el `enum_txt()` viejo, así que el
   `detalle` del suceso podía ser `after varying` o `due to syndrome`. Y ese detalle no se
   queda en el prompt: va también a la **crónica** y a la **memoria**, donde se quedaría
   para siempre. Ahora pasa por `causa_legible()`, igual que el expediente, y si la frase
   queda coja no se manda.
2. **Las entradas vacías.** Una emoción con `type` y `thought` a `-1` que fuera la más
   reciente **ganaba la carrera**, y el suceso salía como `anything none`. Mismo fallo que
   ya habíamos corregido en el expediente, en la función de al lado.
3. **Solo miraba la más reciente.** Aquí estaba el bloqueante de verdad.

### Por qué lo tercero dejaba muda a la viuda

Si a un enano le llega el duelo por su pareja y **detrás, en la misma vuelta de cinco
segundos**, una función de teatro, la sonda solo reportaba el teatro. El vigía disparaba
`emocion / interest after watching a performance` y el duelo no existía para él.

La sonda devuelve ahora **dos**: la más reciente (`emo_*`, que es lo que dispara) y la más
fuerte (`fue_*`). Y `detectar()` mira las dos. Probado con ese caso exacto:

```
emocion          peso  20  interest after watching a performance
emocion_fuerte   peso  45  sadness at being separated from a loved one
```

Las dos se detectan, y **la de peso 45 gana la vuelta**. El 45 está elegido para que quede
por encima del cambio de categoría de estrés (40) —que solo dice *que* cambió, mientras
que la emoción dice *por qué*— y por debajo de `relacion` (50), que es un vínculo ganado o
perdido y no un estado de ánimo.

`emocion_fuerte` **no** entra en `UNICOS_POR_PERSONA`, a propósito: un síndrome que afecte
a media fortaleza produce la misma emoción fuerte en veinte enanos, y eso es una
repetición, no veinte sucesos.

### Y un arreglo de paso

`"%s %s" % (tipo, causa)` sin `strip()`: cuando DF no da el tipo de emoción, el lado Lua
manda cadena vacía y el detalle empezaba por un espacio — *" saw somebody's dead body"* —
que acababa así en la crónica y en la memoria.

## La sonda arreglada, confirmada contra el juego ✅

Crónica del año 106, siete narraciones. Los tres controles pasan y hay una confirmación
que se ve sin buscarla:

```
[ano 106, mes 7, dia 3] Udil Idkulet -- satisfaction upon improving SKILL AT brewing
  "Por fin noto que mis manos entienden mejor el arte de la cerveza"
```

Ese `skill at brewing` viene de la **sonda**, no del expediente. Ayer habría dicho
`upon improving brewing` y el modelo habría escrito *"mejoré esa cerveza"*, igual que con
la planta. La corrección viaja de punta a punta: Lua → sonda → detalle del suceso →
prompt → texto → crónica.

| Control | Resultado |
|---|---|
| `after varying` / `due to syndrome` en algún detalle | **ninguno** |
| `anything none` | **ninguno** |
| Detalle que empieza por espacio | **ninguno** |
| Huecos rellenados | `skill at brewing`, `a tastefully arranged chair` ✅ |

Y una prueba indirecta del descarte: la primera entrada es **`euphoria` a secas**, sin
causa. Eso es `causa_legible()` devolviendo `nil` para una caption que quedaba colgando
—casi seguro `due to [syndrome]`— y el detalle quedándose solo con la emoción. Antes ahí
ponía `EUPHORIA Syndrome`, que es de donde salía el famoso *"¡Euphoria! ¡Qué alegría!"*.

El género también aguanta en producción: Shorast dice *"sigo **agobiada**"* y *"me ha
pillado **desprevenida**"*; Tirist, *"me siento **satisfecho**"*.

### No ha salido ningún `emocion_fuerte`, y es lo esperado

Es un detector de **cambio**: solo dispara cuando la emoción más fuerte de un enano pasa a
ser **otra**. La más fuerte de alguien suele ser estable durante mucho tiempo, así que en
tres minutos de partida tranquila no tiene por qué aparecer ninguno.

El momento en que sí disparará es exactamente el que nos interesa: cuando muere la pareja
de alguien, `LoveSeparated` llega **nueva y fuerte** a la vez, así que salen los dos
sucesos y el de peso 45 gana la vuelta.

**Eso no se puede confirmar sin una muerte.** Queda como la prueba pendiente.

### Lo que ya funciona sin haberlo programado

```
[ano 106, mes 7, dia 4] Tirist Atírshis -- satisfaction at work
  "...aunque echo de menos a Tosid con esa tristeza que no se marcha del todo."
```

El suceso era **la satisfacción del trabajo**. Lo de Tosid sale del bloque de contexto
—`Personas de tu vida` y la lista de emociones—, no del disparador. O sea que **nombrar a
la pareja ya funciona** y no hace falta código nuevo para eso.

Lo que hay que vigilar en el test de muerte es lo contrario: un enano con varias
relaciones (cónyuge, madre, hijo) y un `at being separated from a loved one` que no dice
**cuál**. Ahí el modelo puede atribuirlo a la persona equivocada — una costura, con la
misma forma que las anteriores. No se ataja por adelantado: se mide primero.

## Tirada larga con 128 ciudadanos: cinco cosas confirmadas y un defecto

40 vueltas, ~3,5 minutos, entre 18 y 73 sucesos por vuelta, diez narraciones.

### Lo que queda demostrado en producción

**`emocion_fuerte` dispara, y no es raro.** Cinco veces en 40 vueltas. La duda de si sería
un evento casi inalcanzable queda respondida: en una fortaleza con gente, la emoción más
fuerte de alguien cambia a menudo.

**No haber rellenado `[somebody]` fue la decisión correcta.** Dos enanos narraron haber
visto un cadáver, y ninguno inventó un nombre:

> **Rîsen Olonemal** — *"**La muerte que vi** aún me revuelve por dentro. Siento un miedo
> frío que se me pega a las costillas…"*
>
> **Ast Iridäs** — *"**La muerte de ese pobre alma** aún me pesa en el pecho."*

Si hubiéramos rellenado ese hueco con la figura histórica que «resolvía», ahí habría un
nombre — y muy probablemente el equivocado.

**El epitafio, completo y sin inventar nada:**

> *"Zasit Engiglikot, molinero de treinta años, **hijo de Domas Dodókgatiz**, ha muerto.
> Era competente con el martillo, adecuado en el molido y novato con el escudo."*

Oficio, edad, relación **con género** (*hijo de*), tres habilidades, y **ni una palabra
sobre cómo murió**. Los dos defectos del epitafio —el vacío y la muerte inventada— cerrados
a la vez, en el juego.

**Los huecos de plantilla, en producción:** *"lo mucho que he mejorado **con las
armaduras**"* (`skill at armor`), *"haber tenido que **dirigir una reunión oficial desde el
dormitorio**"*, *"**la luz del sol** me irrita otra vez"*.

**El género, en producción:** *"Estoy tan **tranquila** y feliz"*.

### El defecto: un detalle vago da un enano confundido

> **Mörul Larzulban** — *"Alguien nuevo se ha vuelto importante en mi vida y eso me
> inquieta y me alegra al mismo tiempo, **aunque no entiendo del todo lo que significa**."*

El detalle que le mandamos era *"alguien nuevo ha pasado a ser importante en tu vida"*.
Vago **a propósito**, porque solo contábamos cuántos `relationship_ids` había.

Y el modelo hizo lo único que podía: **reprodujo nuestra falta de información como si fuera
un rasgo del personaje**. Un enano siempre sabe quién se ha vuelto importante para él.

Es el reverso de la regla 2. No basta con no meter rellenos: **lo que se mete tiene que
decir algo**, o el modelo llena el hueco hablando de su propia niebla.

### El arreglo, y lo que trae de regalo

`rel` es la lista de `relationship_ids` **en orden**: la posición *i* es el vínculo de tipo
*i*. Comparando posición a posición sale **qué id** entró y cuál salió. Con el expediente
delante se le pone nombre; al que se ha ido ya no está en `relaciones`, así que se pregunta
por él con `df.unidad()`, que lo encuentra aunque esté muerto.

```
GANA        : Ast Iridas (friend) ha pasado a ser importante en tu vida
PIERDE      : has perdido a Tosid Nishkesh de tu vida
SIN RESOLVER: alguien nuevo ha pasado a ser importante en tu vida   ← se mantiene el generico
```

El regalo: ahora el suceso conoce **a los dos**, así que la entrada de memoria se guarda
con `participantes: [311, 272]`. Es el gancho que `df_memoria` lleva puesto desde el primer
día esperando a las interacciones, y se enchufa hoy sin escribir nada nuevo.

### Un dato para la sesión larga

Con 128 ciudadanos, el vigía narra **una vez cada 20 segundos, sin parar** — está saturando
el `DESCANSO_GLOBAL`. En tres horas serían unas 540 narraciones. No es un fallo, pero es el
número que dice que el reparto por canales (tarea 7) hace falta antes que después.

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
