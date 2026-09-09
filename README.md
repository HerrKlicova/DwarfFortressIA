# Puente Dwarf Fortress ↔ LLM local

**Esto es un spike, no un mod.** Código deliberadamente feo, de usar y tirar, escrito
para responder a una sola pregunta en un día:

> ¿Se puede leer un enano vivo de una fortaleza desde un proceso externo, preguntarle
> algo a un LLM que corre en la misma máquina, y escribir la respuesta dentro del juego?

**Sí se puede.** Funciona de punta a punta, con ~0,6 s de latencia y cero dependencias
de Python.

No lo instales esperando un mod: hace una demo con un enano al azar y se para. Lo que
vale de este repositorio no es el código, sino lo que se aprendió escribiéndolo.

## Qué hay aquí

| Fichero | Qué es |
|---|---|
| `spike.py` | Cliente de la interfaz remota de DFHack (socket + protobuf a mano) y de la API local de Player2. Sin dependencias, sin asincronía. |
| `dfhack_spike.lua` | Script que va dentro del juego. DFHack lo convierte en un comando invocable por RPC. |
| `HALLAZGOS.md` | **Lo importante.** Bitácora completa: qué funcionó, qué no, latencias medidas, qué datos del enano existen de verdad, y las trampas encontradas, con cita a fichero y línea del código de DFHack. |
| `informe.html` | Informe de traspaso, pensado para alguien que llega sin contexto. |

## Requisitos

- Dwarf Fortress 53.16 con DFHack 53.16-r1.1, **con una fortaleza cargada**
- App de escritorio de Player2 abierta y con sesión iniciada
- Python 3 (solo biblioteca estándar)

## Cómo se ejecuta

1. Copia `dfhack_spike.lua` a `<Dwarf Fortress>/dfhack-config/scripts/`
2. Abre el juego y carga una fortaleza
3. Abre Player2 e inicia sesión
4. `python spike.py`

La interfaz remota de DFHack está activa por defecto en `127.0.0.1:5000`; no hay que
habilitar nada. Si el puerto no es el estándar, exporta `DFHACK_PORT`.

## Lo que hay que saber antes de construir encima

Tres cosas que cuestan una tarde si las descubres por tu cuenta:

- **El RPC estructurado no sirve para lo interesante.** `ListUnits` da nombre, oficio y
  poco más. Rasgos de personalidad, pensamientos, relaciones y preferencias solo se
  alcanzan desde un script Lua.
- **Los vectores de DFHack son 0-indexados** y lanzan error al salirse, al contrario que
  las tablas de Lua.
- **DF trata los bytes `0x00–0x1F` como glifos dibujables**, no como caracteres de
  control. Pasar tu propio texto por `df2utf()` te destroza los separadores.

Los detalles, con sus fuentes, están en [`HALLAZGOS.md`](HALLAZGOS.md).
