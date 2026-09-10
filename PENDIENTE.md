# Lo que queda por hacer

Lista viva. Lo de arriba bloquea a lo de abajo.

---

## Antes de medir nada más

**1 · Aplicar los ficheros con los tres bugs corregidos** (commit `3f12b54`)
`df_llm.py` y `df_vigia.py` en la máquina del juego. Corrige la tercera persona que
nunca se enchufaba, el guardián que daba veinte falsos positivos por narración, y las
cinco muertes narradas como una. **Sin esto se mide la versión mala.**

**2 · `id=` en el subcomando `enanos` del lado Lua** → resuelve por `df.unit.find(id)`
Arregla el epitafio vacío (ver `HALLAZGOS.md`) y desbloquea las conversaciones.
Devolver además el `unit_id` de cada relación, que hoy se tira.

**3 · Verificar que el epitafio ya sale con los datos del difunto** *(bloqueada por 1 y 2)*
Con `exterminate this` sobre una copia del guardado. **Imprimir el prompt entero, no
solo la respuesta**: el defecto se coló porque la prosa salía correcta.

---

## La sesión que decide la fase 4

**4 · Sesión larga de 2-3 horas con el vigía puesto** *(bloqueada por 1 y 2)*
Dos preguntas: ¿aguanta el ritmo con 20-70 sucesos por vuelta? ¿**Reacciona alguien
cuando muere su pareja?** Nunca se ha comprobado. Un fuerte donde alguien muere y nadie
lo nota es el fallo narrativo más grave que puede tener esto.

**12 · Comprobar si la lista de ciudadanos parpadea** *(se vigila en la misma sesión)*
Si `getCitizens()` devolviera alguna vez una lista parcial, cada ausente dispara una
petición `unidad(id)`: con 89 ciudadanos serían 89 de golpe, y quizá una tanda de falsas
muertes. No se ha observado, pero tampoco se ha buscado. Si en una vuelta salen muchas
ausencias a la vez, es esto y no una desgracia.

---

## Entrega: el problema que no habíamos puesto sobre la mesa

Hemos resuelto la generación y **no** la entrega. El panel de anuncios de DFHack no
tiene identidad, ni fecha, ni sitio — y compite con los anuncios de verdad del juego,
que son los que hay que leer para no perder la fortaleza.

**5 · Sondear qué ofrece la interfaz en la build instalada**
Subcomando `ui`, con el patrón de `enums`: no suponer, **preguntar a la build**.
Comprobar si hay sistema de *overlay* en Lua (pintar sobre la interfaz sin plugin en
C++), si `showAnnouncement` tiene variantes con posición de mapa (pulsar el mensaje y
que la cámara salte al enano), si se puede pintar en la ficha de una unidad, y qué
colores admite. **Esta tarea decide si hace falta el plugin o no**, y por eso va antes
de abrir nada en paralelo: un plugin en C++ ata a compilar y se rompe con cada
actualización de DFHack; un overlay en Lua no tiene ninguna de las dos cosas.

**6 · Petición explícita: preguntar a un enano concreto cuando quieras**
La mejor de las ideas de interfaz, y no por la interfaz: por la **economía**. Hoy el
sistema adivina qué te interesa, y toda la maquinaria de pesos, frenos y ventanas existe
para eso. Si lo pides tú, el contexto lo aportas al preguntar y los tres problemas del
panel se resuelven a la vez. Efecto secundario valioso: **hace barato probar la calidad
del texto**, que hoy obliga a esperar a que pase algo.
Primera versión por línea de comandos; el botón dentro del juego depende de la 5.

**7 · Separar los canales**

| Qué | Dónde |
|---|---|
| Muerte, locura, relaciones | Anuncio. Es noticia, y DF ya te enseñó a leer ahí las noticias |
| Pensamiento ambiental | **Fuera del anuncio**: donde esté el enano, o un registro que abres tú |
| Lo que pides a un enano | Inmediato, porque lo pediste y estás mirando |

Apaño barato mientras tanto: **color propio** para las líneas del LLM (hoy todas van en
`COLOR_YELLOW`, igual que todo lo demás). La crónica en fichero ya cubre el «cuándo»
fechado en años enanos; lo que falta es que esté dentro del juego.

**8 · Agrupar la oleada de migrantes en un solo suceso**
Los migrantes **sí** se detectan en cada ciclo: la sonda pide `n=0` cada vuelta y
`detectar()` marca `llegada` para cualquier id nuevo. Ya se observó con 89 ciudadanos.
El problema es otro: `llegada` pesa **15**, el mínimo de los siete; `decidir()` narra uno
por vuelta y hay 20 s de descanso global. Once llegadas de peso 15 compiten entre sí y
pierden todas contra cualquier emoción (20) o cambio de estrés (40). Es un suceso que el
sistema **ve y descarta sistemáticamente**. Agruparlos en uno («han llegado once enanos
nuevos») en vez de subirle el peso a cada uno.

---

## Conversaciones

**9 · Primer corte: la viuda responde** *(bloqueada por 2)*
Solo el caso más sólido y solo con la muerte: muere alguien, su cónyuge está vivo y en el
mapa (resuelto **por id**, no por nombre: los nombres se repiten en DF), el cónyuge dice
una frase. Sin ida y vuelta todavía — el muerto no puede responder.
Elegido porque hace tres cosas de golpe: responde la pregunta abierta desde el primer
test de muerte, obliga a poner el `id=` que ya hacía falta, y es el trozo más pequeño
que se puede probar con `exterminate this` sobre una copia del guardado.
Ambas partes guardan **la misma entrada** con `participantes: [A, B]`; el gancho lleva
puesto en `df_memoria.py` desde el principio.

**11 · Medir la tasa de invención** *(regla 5 de la doctrina)*
N respuestas, cuántas afirman algo que no está en el prompt, número a `HALLAZGOS.md`.
Distinguir **textura** (*"noto el frío de la piedra"*: no añade nada al mundo) de
**afirmación** (*"mi hermano murió el invierno pasado"*: o sale de DF o es falsa).
Se aplica al primer corte y al ida y vuelta por separado. **Sin ese número no se sube de
escalón.**

**10 · Ida y vuelta completo** *(bloqueada por 9 y 11)*
Tres formas de encontrar la escena, de más a menos sólida:
1. **El vínculo nombrado** — a A le pasa algo y sus `relaciones` nombran a B, vivo y
   presente. La más fuerte: el vínculo lo afirma DF, no nosotros.
2. **La coincidencia** — misma causa de emoción con marcas de tiempo cercanas
   (`emo_a`/`emo_t` ya están en la sonda). Vivieron lo mismo, a la vez.
3. **El cambio mutuo** — los `relationship_ids` de A y de B cambian en la misma vuelta.
   Pasó algo *entre ellos*.

A habla con el pipeline actual, sin tocar nada. B responde con un prompt que lleva **solo**
quién es A para él, lo que A dijo, y su propio estado. B no recibe instrucción sobre qué
sentir ni ningún hecho del mundo que no esté en el estado: la única afirmación nueva que
introduce es *"te he oído"*, que es verdad por construcción.

Encaja como `emparejar()` entre `detectar()` y `decidir()`. La frontera de las cuatro
fases se diseñó para esto.

> La prueba de que el enfoque funciona ya la tenemos sin haber programado nada: Tirist y
> Tosid, los casados que se sentían distantes, encajaron en **dos llamadas
> independientes sin memoria compartida**, porque los datos de los dos venían del mismo
> sitio. Lo que falta es solo hacer explícito ese encuentro.

---

## Administrativo

**13 · Cambiar la rama por defecto del repo a `main`**
En `github.com/HerrKlicova/DwarfFortressIA/settings`. El tag `spike-v0` no se pudo subir:
el gateway de git de estas sesiones rechaza `refs/tags/*`. Si se quiere, hay que crearlo
desde la interfaz de GitHub o desde la máquina local.
