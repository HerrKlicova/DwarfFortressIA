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
- **Cada camino de datos hacia el prompt debe traducir sus enums.** La corrección no es
  global: al añadir `detalle=sonda` se copió el patrón viejo con `enum()` y los
  identificadores crudos volvieron al prompt (`EUPHORIA Syndrome`), con el modelo
  repitiéndolos literalmente. Usar siempre `enum_txt()` en lo que acabe en el prompt.
- **La memoria se guarda por partida.** Los `unit_id` vuelven a empezar en cada mundo,
  así que sin separar por `dfhack.world.ReadWorldFolder()` el enano 272 de una fortaleza
  heredaría los recuerdos del 272 de otra.
