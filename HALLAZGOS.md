# HALLAZGOS — spike Dwarf Fortress ↔ LLM local

Entorno objetivo: Dwarf Fortress 53.16 (Steam), DFHack 53.16-r1.1, app de escritorio
Player2, Windows, Python 3.14.7.

## Estado: leído la fuente, no ejecutado contra el juego

Hay que ser claro sobre esto, porque cambia cuánto te puedes fiar de cada línea de
abajo. Este spike se escribió desde un contenedor Linux **sin acceso a la máquina
donde corre Dwarf Fortress**. Todo lo marcado ✅ está verificado contra el código
fuente o la documentación oficial, y el cliente está probado contra dos servidores
de prueba que implementan el protocolo documentado al pie de la letra. Pero
**nada se ha ejecutado contra el juego real todavía**.

Las secciones marcadas 🔲 son las que solo se pueden rellenar ejecutando `spike.py`.

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

**No probado ❌** — todo lo que necesita el juego delante:

- Que DFHack cargue `dfhack_spike.lua` y lo exponga como comando
- Que `dfhack.units.getCitizens()` devuelva algo en tu partida
- Que `dfhack.translation.translateName()` dé un nombre legible
- Que el anuncio aparezca de verdad en el log
- La latencia real de Player2
- Qué campos de personalidad existen en **tu** build concreta de df-structures

---

## 🔲 Resultados de la ejecución real

*Rellenar tras ejecutar `python spike.py`.*

### Latencia del LLM de punta a punta

| Medida | Valor |
|---|---|
| `POST /v1/chat/completions` | _pendiente_ |
| Modelo que sirvió la respuesta | _pendiente_ |

### Campos del enano disponibles de verdad

Salida de `dfhack_spike fields` (paso 1b del script). Esta tabla es la que sustituye
a la lectura teórica de df-structures por datos de tu instalación:

| Ruta | ¿Existe? | Notas |
|---|---|---|
| `unit.id` | _pendiente_ | |
| `unit.race` / `caste` / `sex` | _pendiente_ | |
| `unit.civ_id` / `hist_figure_id` | _pendiente_ | |
| `unit.relationship_ids` | _pendiente_ | |
| `unit.status.current_soul` | _pendiente_ | |
| `soul.skills` | _pendiente_ | |
| `soul.preferences` | _pendiente_ | |
| `soul.mental_attrs` | _pendiente_ | |
| `personality.traits` | _pendiente_ | |
| `personality.values` | _pendiente_ | |
| `personality.emotions` (pensamientos) | _pendiente_ | |
| `personality.dreams` | _pendiente_ | |
| `personality.stress` | _pendiente_ | |
| `getReadableName` / `getProfessionName` / `getAge` | _pendiente_ | |

### Qué funcionó / qué no

| Paso | Resultado |
|---|---|
| 1 — leer nombre de enano | _pendiente_ |
| 2 — llamada al LLM | _pendiente_ |
| 3 — anuncio en el juego | _pendiente_ |

### Veredicto

_¿Existe el tubo? Sí / No, y por qué._

---

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
