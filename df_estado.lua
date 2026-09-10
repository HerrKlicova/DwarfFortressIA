-- df_estado.lua -- lado Lua del puente DF <-> LLM.
--
-- RESPONSABILIDAD: extraer estado del juego y devolverlo como JSON. Nada mas.
-- Las decisiones (a quien preguntar, que meter en el prompt, cuando hablar) son
-- del lado Python.
--
-- INSTALACION: <Dwarf Fortress>/dfhack-config/scripts/df_estado.lua
--
-- CONTRATO
--   df_estado estado
--     -> {ok, mundo, mapa, pausa, frame, n_ciudadanos}
--   df_estado enanos [n=5] [desde=0] [detalle=basico|completo] [pensamientos=8] [adultos=1]
--   df_estado enanos id=<unit_id> [detalle=completo] [pensamientos=8]
--     -> UN enano concreto, resuelto por df.unit.find(). NO pasa por getCitizens(),
--        asi que tambien encuentra a los muertos, locos y ausentes -- que es
--        justo el caso de los sucesos de mas peso.
--     -> {ok, ...estado..., enanos:[{id,nombre,profesion,edad,adulto,estres,
--                                    rasgos,pensamientos,relaciones,preferencias,habilidades}]}
--   df_estado anuncio <texto>
--     -> {ok, lineas}     (parte el texto por \n: showAnnouncement ignora los saltos)
--   df_estado ui
--     -> {ok, gui:[...], modulos:[...]}  que ofrece la interfaz en ESTA build.
--        No supone nada: pregunta. Mismo patron que 'enums'.
--
-- Siempre responde UNA sola linea, con el prefijo JSON| para poder distinguirla
-- de cualquier aviso que DFHack imprima por su cuenta.
-- En caso de fallo: {"ok":false,"error":"..."}
--
-- Ver CLAUDE.md para los invariantes. Los tres que aplican aqui:
--   * los vectores de DF son 0-indexados; la tabla de getCitizens() es 1-indexada
--   * df2utf solo sobre cadenas de DF, nunca sobre la estructura del JSON
--   * nada de %f: el locale mete coma decimal

local json = require('json')

local args = {...}
local sub = args[1]

-- Opciones estilo clave=valor a partir del segundo argumento.
local opt = {}
for i = 2, #args do
    local k, v = tostring(args[i]):match('^([%a_][%w_]*)=(.*)$')
    if k then opt[k] = v end
end

local function try(fn, por_defecto)
    local ok, v = pcall(fn)
    if ok and v ~= nil then return v end
    return por_defecto
end

-- DF guarda las cadenas en CP437; el socket lleva UTF-8. Solo las cadenas de DF.
local function dfstr(s) return dfhack.df2utf(tostring(s)) end

-- CP437 no tiene A I O U con tilde. Si tiene las minusculas (a e i o u con
-- tilde), la n con virgulilla en ambas cajas, E con tilde, u con dieresis,
-- c cedilla, y los signos de apertura. utf2df sustituye por '?' lo que no
-- puede mapear, asi que una frase que empiece por "Ultimamente" o nombre a
-- "Angeles" saldria con un interrogante en el juego.
--
-- Nuestra comprobacion de acentos solo probo MINUSCULAS ('i' y 'e' con
-- circunflejo al leer, 'u' y 'o' con tilde al escribir), asi que dio verde sin
-- tocar este caso. El subcomando 'ui' lo mide de verdad en la build instalada.
local MAYUS_SIN_GLIFO = {
    ['\195\129'] = 'A',   -- A con tilde
    ['\195\141'] = 'I',   -- I con tilde
    ['\195\147'] = 'O',   -- O con tilde
    ['\195\154'] = 'U',   -- U con tilde
}

local function a_cp437(s)
    s = tostring(s)
    for utf8, llano in pairs(MAYUS_SIN_GLIFO) do
        s = s:gsub(utf8, llano)
    end
    return dfhack.utf2df(s)
end

-- pretty=true con tabulador es el defecto de DFHack: lo apagamos para que quepa
-- en una linea y para no meter bytes 0x09 en el transporte.
local function responder(t)
    if t.ok == nil then t.ok = true end
    print('JSON|' .. json.encode(t, {pretty = false}))
end

local function fallo(msg)
    print('JSON|' .. json.encode({ok = false, error = msg}, {pretty = false}))
end

local function enum(tipo, valor)
    local nombre = try(function() return df[tipo][valor] end, nil)
    if type(nombre) == 'string' then return nombre end
    if type(valor) == 'string' then return valor end
    return tostring(valor)
end

-- Convierte un identificador de maquina en algo que un LLM pueda leer:
-- 'WatchPerform' -> 'watch perform', 'ANXIETY_PROPENSITY' -> 'anxiety propensity'.
local function humanizar(id)
    local t = tostring(id):gsub('_', ' ')
    t = t:gsub('(%l)(%u)', '%1 %2'):gsub('(%u)(%u%l)', '%1 %2')
    return t:lower()
end

-- Texto legible de un valor de enum. DF trae captions de verdad para algunos
-- enums (unit_thought_type tiene 281 con prosa como "after seeing somebody
-- die"); cuando no hay, se humaniza el identificador. Nunca se inventa.
-- QUE RELLENA CADA HUECO, por el NOMBRE del hueco y no por su posicion.
-- Emparejar por posicion era un error: 'near a [quality] [building]' con el
-- valor 'door' daba "near a door" y 'near a [quality] tastefully arranged
-- [building]' daba "near a table tastefully arranged". El nombre del hueco dice
-- de que tipo es, asi que se usa eso.
--
-- Solo estan los CONFIRMADOS por la medicion, cada uno por coherencia con otro
-- dato del mismo enano:
--   [varying]  sub=8  -> need=BeWithFriends,  y la emocion era LONELINESS
--   [skill]    sub=15 -> skill=CLOTHESMAKING, y el enano tiene esa habilidad
--   [building] sub=8  -> edificio=Door
--   [relation] sub=17 -> rel=AcquaintancePassing, y ese mismo 17 sale en los
--                        tres pensamientos sociales del enano
--
-- SawDeadBody NO se rellena: 'histfig' es lo unico que resuelve en ese rango,
-- pero eso no lo prueba -- los ids de figura son densos y casi cualquier numero
-- devuelve un nombre. Meter el nombre EQUIVOCADO de un muerto en el prompt
-- seria el peor fallo posible, asi que se queda en "saw somebody's dead body",
-- que es cierto.
local HUECO_TIPO = {
    varying  = 'need_type',
    skill    = 'job_skill',
    building = 'building_type',
    relation = 'unit_relationship_type',
}

-- Como se lee el valor ya resuelto dentro de la frase. 'after [varying]' con
-- 'be with friends' daba "after be with friends", que no es ingles. El tipo de
-- pensamiento se llama NeedsUnfulfilled, asi que decirlo es describirlo, no
-- inventarlo.
local HUECO_FORMA = {
    varying = function(v) return 'an unmet need to ' .. v end,
    -- 'upon improving [skill]' con el hueco a secas daba "upon improving
    -- plant", y el modelo lo leyo como un OBJETO: "me alegra haber mejorado
    -- esa planta". Salio en 7 de 10 respuestas. No es que se lo inventara --
    -- es que ese ingles dice eso. 'improving' pide una competencia, no una
    -- cosa, asi que se nombra como lo que es.
    skill = function(v) return 'skill at ' .. v end,
    -- 'talking with a acquaintance passing' no es ingles ni concuerda. El
    -- valor es un TIPO de vinculo, no un nombre.
    relation = function(v) return 'person of the ' .. v .. ' sort' end,
}

-- Huecos que se pueden dejar como palabra porque son indefinidos de verdad en
-- ingles: "saw somebody's dead body" se lee bien y no afirma nada falso.
local HUECO_GENERICO = {somebody = true}

-- Si al quitar los huecos la frase acaba en una de estas, se ha quedado
-- colgando ('due to', 'after') y no se manda. Lista cerrada, no heuristica.
local COLGANDO = {
    ['after'] = true, ['due'] = true, ['to'] = true, ['of'] = true, ['near'] = true,
    ['with'] = true, ['in'] = true, ['on'] = true, ['at'] = true, ['from'] = true,
    ['by'] = true, ['a'] = true, ['an'] = true, ['the'] = true, ['upon'] = true,
    ['about'] = true, ['for'] = true,
}

local function enum_txt(tipo, valor)
    local cap = try(function() return df[tipo].attrs[valor].caption end, nil)
    if type(cap) == 'string' and cap ~= '' then
        return (cap:gsub('%[(.-)%]', '%1'))   -- "[somebody]" son huecos de DF
    end
    return humanizar(enum(tipo, valor))
end

local function estado_base()
    return {
        mundo = try(function() return dfhack.isWorldLoaded() end, false),
        mapa  = try(function() return dfhack.isMapLoaded() end, false),
        pausa = try(function() return dfhack.world.ReadPauseState() end, false),
        frame = math.floor(try(function() return df.global.world.frame_counter end, -1)),
        n_ciudadanos = math.floor(try(function()
            local c = dfhack.units.getCitizens(); return c and #c or 0
        end, 0)),
        -- Si existe y es un contador monotono, apunta a asignacion secuencial de
        -- unit_id sin reciclaje. Es indicio, no prueba. Se prueba que exista.
        unit_next_id = math.floor(try(function() return df.global.unit_next_id end, -1)),
        -- Identifica la PARTIDA. Sin esto, empezar otra fortaleza haria que el
        -- enano 272 nuevo heredase los recuerdos del 272 viejo: los unit_id
        -- vuelven a empezar en cada mundo.
        partida = try(function() return dfhack.world.ReadWorldFolder() end, ''),
        -- Fecha del juego: para fechar la cronica en anos enanos, no en hora local.
        anio = math.floor(try(function() return dfhack.world.ReadCurrentYear() end, -1)),
        mes  = math.floor(try(function() return dfhack.world.ReadCurrentMonth() end, -1)),
        dia  = math.floor(try(function() return dfhack.world.ReadCurrentDay() end, -1)),
    }
end

local function hay_partida()
    return try(function() return dfhack.isWorldLoaded() end, false)
       and try(function() return dfhack.isMapLoaded() end, false)
end

local function nombre_de(u)
    local n = try(function()
        return dfhack.translation.translateName(dfhack.units.getVisibleName(u))
    end, nil)
    if not n or n == '' then
        n = try(function() return dfhack.units.getReadableName(u) end, '(sin nombre)')
    end
    return n
end

-- El sexo del enano. El espanol lo necesita en casi cada frase: 'minera',
-- 'esposa', 'verla'. Sin este dato el modelo lo adivina, y acierta la mitad de
-- las veces -- lo vimos con Tirist, mujer, narrada como 'esposo de Tosid' y
-- refiriendose a su marido como 'verla'.
--
-- Dos caminos porque no esta comprobado cual existe en esta build, y se apunta
-- cual funciono en 'sexo_via'. Si no se resuelve devuelve nil y el campo se
-- OMITE: ningun valor de relleno llega al prompt.
-- Rellena los huecos de una caption y decide si vale la pena mandarla.
-- Devuelve nil si la frase se queda coja: mejor un pensamiento menos que un
-- pensamiento a medias, porque un hueco en el prompt lo rellena el modelo.
local function causa_legible(causa_id, sub)
    local cap = try(function()
        return df.unit_thought_type.attrs[causa_id].caption end, nil)
    if type(cap) ~= 'string' or cap == '' then return nil end
    if not cap:find('%[') then return cap end

    local salida = cap:gsub('%[(.-)%]', function(hueco)
        local tipo = HUECO_TIPO[hueco]
        if tipo and type(sub) == 'number' and sub >= 0 then
            local v = try(function() return df[tipo][sub] end, nil)
            if type(v) == 'string' and v ~= '' then
                local txt = humanizar(v)
                local forma = HUECO_FORMA[hueco]
                return forma and forma(txt) or txt
            end
        end
        if HUECO_GENERICO[hueco] then return hueco end
        return ''                      -- hueco que no se puede rellenar: fuera
    end)

    salida = salida:gsub('%s+', ' '):gsub('^%s+', ''):gsub('%s+$', '')
    salida = salida:gsub(" 's", "'s")  -- por si el hueco iba pegado al genitivo
    if salida == '' then return nil end
    local ultima = salida:match('(%a+)%s*$')
    if ultima and COLGANDO[ultima:lower()] then return nil end
    return salida
end

local function sexo_de(u)
    -- Primero, el camino que NO exige suponer nada: 'unit.sex' es un
    -- pronoun_type, y ese enum SE NOMBRA A SI MISMO -- devuelve 'she', 'he' o
    -- 'it'. Nada de mapear 0 y 1 de memoria, que es donde se cuela el error.
    local pron = enum('pronoun_type', try(function() return u.sex end, nil))
    if pron == 'she' then return 'f', 'pronoun_type=she' end
    if pron == 'he'  then return 'm', 'pronoun_type=he'  end

    -- Segundo, funciones cuyo NOMBRE dice lo que devuelven.
    if try(function() return dfhack.units.isFemale(u) end, nil) == true then
        return 'f', 'isFemale'
    end
    if try(function() return dfhack.units.isMale(u) end, nil) == true then
        return 'm', 'isMale'
    end

    -- Ultimo recurso: el numero crudo. Se marca como SUPUESTO en 'sexo_via'
    -- porque aqui si hay una suposicion y tiene que verse desde fuera.
    local s = tonumber(try(function() return u.sex end, nil))
    if s == 0 then return 'f', 'unit.sex=0 (SUPUESTO)' end
    if s == 1 then return 'm', 'unit.sex=1 (SUPUESTO)' end
    return nil, 'sin resolver (u.sex=' .. tostring(s) .. ')'
end

-- Recorre un vector de DF de forma segura. 0-INDEXADO: 0..n-1.
local function cada(vec, fn, maximo)
    if not vec then return end
    local n = try(function() return #vec end, 0)
    local desde = 0
    if maximo and maximo > 0 and n > maximo then desde = n - maximo end  -- los mas recientes
    for i = desde, n - 1 do
        local elem = try(function() return vec[i] end, nil)
        if elem ~= nil then fn(elem, i) end
    end
end

-- Lo minimo para detectar cambios entre dos vueltas del bucle. Evita serializar
-- ~123 objetos por enano (50 rasgos, 28 pensamientos, 18 preferencias, 27
-- habilidades) solo para comprobar si a alguien le cambio el humor.
local function enano_sonda(u)
    local e = {
        id     = math.floor(try(function() return u.id end, -1)),
        hfid   = math.floor(try(function() return u.hist_figure_id end, -1)),
        nombre = dfstr(nombre_de(u)),
        nac_a  = math.floor(try(function() return u.birth_year end, -1)),
        nac_t  = math.floor(try(function() return u.birth_time end, -1)),
        estres = math.floor(try(function() return u.status.current_soul.personality.stress end, 0)),
        -- Categoria de estres segun DF (0 mas estresado, 6 menos). Mejor que
        -- inventarse umbrales: es la clasificacion del propio juego.
        estres_cat = math.floor(try(function() return dfhack.units.getStressCategory(u) end, -1)),
    }

    -- Emociones. Cada una lleva marca de tiempo del juego (year, year_tick, de
    -- df.personality.xml). Con ella la deteccion de "emocion nueva" es exacta;
    -- comparar contadores fallaria porque DF tambien PODA emociones viejas.
    --
    -- SE DEVUELVEN DOS, no una:
    --   emo_*  la mas RECIENTE, que es lo que dispara el suceso
    --   fue_*  la mas FUERTE, para que un duelo no se pierda porque en la misma
    --          vuelta de cinco segundos llego una funcion de teatro detras
    --
    -- El expediente completo ya elige mitad por recencia y mitad por fuerza; la
    -- sonda se habia quedado atras y el vigia detecta CON ESTOS CAMPOS. De poco
    -- sirve que el prompt sepa contar el duelo si quien decide no lo ve.
    local emo = try(function() return u.status.current_soul.personality.emotions end, nil)
    e.emo_n, e.emo_a, e.emo_t = 0, -1, -1
    e.fue_a, e.fue_t, e.fue_fuerza = -1, -1, 0
    if emo then
        local n = try(function() return #emo end, 0)
        e.emo_n = n
        local mejor_f = -1
        for i = 0, n - 1 do                       -- vector de DF: 0-indexado
            local m = try(function() return emo[i] end, nil)
            if m then
                local tp = math.floor(try(function() return m.type end, -1))
                local th = math.floor(try(function() return m.thought end, -1))
                -- Una entrada con los dos campos a -1 esta vacia. Si encima es
                -- la mas reciente, ganaba la carrera y el suceso salia como
                -- "anything none". Mismo fallo que tenia el expediente.
                if tp >= 0 or th >= 0 then
                    -- causa_legible rellena los huecos de la plantilla y
                    -- devuelve nil si la frase queda coja. Sin esto, el detalle
                    -- del suceso -- que acaba en el prompt, en la cronica Y en
                    -- la memoria -- podia ser "after varying" o "due to
                    -- syndrome", que no significan nada.
                    local causa = (th >= 0) and causa_legible(th, math.floor(
                        try(function() return m.subthought end, -1))) or ''
                    if th < 0 or causa then
                        local a = math.floor(try(function() return m.year end, -1))
                        local t = math.floor(try(function() return m.year_tick end, -1))
                        local f = math.floor(try(function() return m.strength end, 0))
                        local tipo = (tp >= 0) and enum_txt('emotion_type', tp) or ''

                        if a > e.emo_a or (a == e.emo_a and t > e.emo_t) then
                            e.emo_a, e.emo_t = a, t
                            e.emo_tipo, e.emo_causa, e.emo_fuerza = tipo, causa or '', f
                        end
                        if math.abs(f) > mejor_f then
                            mejor_f = math.abs(f)
                            e.fue_a, e.fue_t, e.fue_fuerza = a, t, f
                            e.fue_tipo, e.fue_causa = tipo, causa or ''
                        end
                    end
                end
            end
        end
    end

    -- Firma compacta de las relaciones, para ver si cambian sin traerlas enteras.
    local partes = {}
    cada(try(function() return u.relationship_ids end, nil), function(id)
        partes[#partes + 1] = tostring(id)
    end)
    e.rel = table.concat(partes, ',')

    return e
end

local function enano_tabla(u, detalle, max_pens)
    local e = {
        id        = math.floor(try(function() return u.id end, -1)),
        nombre    = dfstr(nombre_de(u)),
        profesion = dfstr(try(function() return dfhack.units.getProfessionName(u) end, 'desconocida')),
        edad      = math.floor(try(function() return dfhack.units.getAge(u) end, -1)),
        adulto    = try(function() return dfhack.units.isAdult(u) end, false),
        estres    = math.floor(try(function() return u.status.current_soul.personality.stress end, 0)),
        -- Huella del enano. No cambia nunca, asi que sirve para detectar que un
        -- unit_id reutilizado ya no apunta a la misma persona (df.unit.xml:2719).
        hfid      = math.floor(try(function() return u.hist_figure_id end, -1)),
        nac_a     = math.floor(try(function() return u.birth_year end, -1)),
        nac_t     = math.floor(try(function() return u.birth_time end, -1)),
    }
    local sx, via = sexo_de(u)
    if sx then e.sexo = sx end          -- si no se resuelve, no hay campo
    e.sexo_via = via
    if detalle ~= 'completo' then return e end

    local alma = try(function() return u.status.current_soul end, nil)
    e.rasgos, e.pensamientos, e.relaciones = {}, {}, {}
    e.preferencias, e.habilidades = {}, {}

    if alma then
        cada(try(function() return alma.personality.traits end, nil), function(v, i)
            e.rasgos[#e.rasgos + 1] = {
                n = enum('personality_facet_type', i),
                txt = enum_txt('personality_facet_type', i),
                v = math.floor(v),
            }
        end)

        -- Emociones: las MAS RECIENTES por marca de tiempo del juego, no las
        -- ultimas del vector.
        --
        -- Coger las ultimas N era un fallo silencioso. En el prompt de un enano
        -- real salieron las seis asi:
        --   Lo que has sentido ultimamente: anything none; anything none; ...
        -- 'anything' y 'none' son las captions del valor -1 de emotion_type y
        -- unit_thought_type: entradas vacias que DF deja en la cola del vector
        -- cuando poda las viejas. El bloque entero era ruido.
        --
        -- Por que no se noto: enano_sonda() SI elige por (year, year_tick), asi
        -- que el disparador del suceso llegaba bien y la respuesta sonaba
        -- correcta. El bloque estropeado era el de contexto, que no se lee tan
        -- de cerca. Otra vez el mismo patron: prosa buena tapando datos malos.
        local emos = {}
        cada(try(function() return alma.personality.emotions end, nil), function(m)
            local tipo_e  = math.floor(try(function() return m.type end, -1))
            local causa_e = math.floor(try(function() return m.thought end, -1))
            -- Una entrada sin tipo NI causa esta vacia: no dice nada y ocupa un
            -- hueco de los pocos que caben en el prompt.
            if tipo_e >= 0 or causa_e >= 0 then
                emos[#emos + 1] = {
                    tipo = tipo_e, causa = causa_e,
                    -- El complemento de la causa. Las captions de
                    -- unit_thought_type vienen a medias ('after varying') y DF
                    -- las completa con esto. Sin el, el hueco lo rellena el
                    -- modelo: 'despues de tanto variar de un lado a otro'.
                    sub = math.floor(try(function() return m.subthought end, -1)),
                    sev = math.floor(try(function() return m.severity end, -1)),
                    a = math.floor(try(function() return m.year end, -1)),
                    t = math.floor(try(function() return m.year_tick end, -1)),
                    f = math.floor(try(function() return m.strength end, 0)),
                }
            end
        end)
        -- MITAD POR RECIENTE, MITAD POR FUERZA.
        --
        -- Con solo recencia, la tristeza de este enano por estar separado de su
        -- pareja (SADNESS / LoveSeparated) quedaba fuera del prompt, enterrada
        -- bajo VEINTIUNA entradas de 'after watching a performance'. Lo mas
        -- reciente no es lo que mas pesa, y un prompt que solo mira el reloj
        -- cuenta el teatro y se calla el duelo.
        local vistas, sin_hueco = {}, {}

        local function mete(m)
            if #e.pensamientos >= max_pens then return end
            local clave = m.tipo .. ':' .. m.causa
            if vistas[clave] then return end
            -- La causa pasa por causa_legible(): rellena los huecos de la
            -- plantilla y devuelve nil si la frase se queda coja.
            local causa_txt = nil
            if m.causa >= 0 then
                causa_txt = causa_legible(m.causa, m.sub)
                if not causa_txt then return end     -- coja: no se manda
            end
            vistas[clave] = true
            e.pensamientos[#e.pensamientos + 1] = {
                -- Un campo a -1 no dice nada: sus captions son 'anything' y
                -- 'none'. Va vacio y el lado Python lo recorta.
                emocion     = (m.tipo >= 0) and enum('emotion_type', m.tipo) or '',
                emocion_txt = (m.tipo >= 0) and enum_txt('emotion_type', m.tipo) or '',
                causa       = (m.causa >= 0) and enum('unit_thought_type', m.causa) or '',
                causa_txt   = causa_txt or '',
                fuerza      = m.f,
                a           = m.a,
                t           = m.t,
                sub         = m.sub,
            }
        end

        for i = 1, #emos do sin_hueco[i] = emos[i] end

        table.sort(emos, function(x, y)          -- por recencia
            if x.a ~= y.a then return x.a > y.a end
            return x.t > y.t
        end)
        local mitad = math.ceil(max_pens / 2)
        for i = 1, #emos do
            if #e.pensamientos >= mitad then break end
            mete(emos[i])
        end

        table.sort(sin_hueco, function(x, y)     -- por fuerza, para el resto
            return math.abs(x.f) > math.abs(y.f)
        end)
        for i = 1, #sin_hueco do
            if #e.pensamientos >= max_pens then break end
            mete(sin_hueco[i])
        end

        e.emo_utiles = #emos

        cada(try(function() return alma.preferences end, nil), function(p)
            local tp = try(function() return p.type end, -1)
            e.preferencias[#e.preferencias + 1] = {
                n = enum('unitpref_type', tp), txt = enum_txt('unitpref_type', tp),
            }
        end)

        cada(try(function() return alma.skills end, nil), function(s)
            -- Se manda tambien el nivel NUMERICO: skill_rating es un enum
            -- ordenado, y sin el numero el lado Python no puede quedarse con
            -- las mejores habilidades sin cablear el orden de memoria.
            local bruto = try(function() return s.rating end, -1)
            local nivel_n = tonumber(bruto)
            if nivel_n == nil then
                nivel_n = tonumber(try(function() return df.skill_rating[bruto] end, -1)) or -1
            end
            local sid = try(function() return s.id end, -1)
            e.habilidades[#e.habilidades + 1] = {
                n = enum('job_skill', sid),
                txt = enum_txt('job_skill', sid),
                nivel = enum('skill_rating', bruto),
                nivel_n = math.floor(nivel_n),
            }
        end)
    end

    cada(try(function() return u.relationship_ids end, nil), function(id, i)
        if id and id >= 0 then
            local otro = try(function() return df.unit.find(id) end, nil)
            -- Si no se puede resolver el nombre se OMITE la relacion. Antes se
            -- mandaba 'unidad 338' de relleno y el modelo lo tomaba por un
            -- nombre propio: "Unidad 338 me espera esta noche con su sonrisa
            -- callada". Una relacion sin nombre no aporta nada al prompt.
            local nombre = otro and dfstr(nombre_de(otro)) or nil
            if nombre and nombre ~= '' and nombre ~= '(sin nombre)' then
                e.relaciones[#e.relaciones + 1] = {
                    tipo = enum('unit_relationship_type', i),
                    txt = enum_txt('unit_relationship_type', i),
                    quien = nombre,
                    -- El sexo del otro: 'esposo' o 'esposa' depende de el, no
                    -- de quien habla, y 'spouse' no lo dice.
                    sexo = (select(1, sexo_de(otro))),
                    -- El unit_id del OTRO. Antes se tiraba y solo quedaba el
                    -- nombre; los nombres se repiten en DF, asi que sin esto no
                    -- se puede comprobar si el otro sigue vivo, ni pedir su
                    -- expediente, ni escribir una memoria compartida.
                    id = math.floor(id),
                }
            end
        end
    end)

    return e
end

-- ---------------------------------------------------------------- estado
if sub == 'estado' then
    responder(estado_base())
    return
end

-- ---------------------------------------------------------------- enanos
if sub == 'enanos' then
    if not hay_partida() then fallo('sin partida cargada'); return end

    -- UN enano concreto, por id. Va ANTES de getCitizens() a proposito:
    -- getCitizens() solo devuelve vivos y cuerdos, y los sucesos de mas peso
    -- (muerte, locura, desaparicion) se detectan precisamente porque el enano
    -- ya no esta ahi. Buscarlo en esa lista era buscarlo donde no puede estar.
    local solo = math.floor(tonumber(opt.id) or -1)
    if solo >= 0 then
        local t0u = os.clock()
        local r = estado_base()
        r.total_pool = 1
        r.enanos = {}
        local u = try(function() return df.unit.find(solo) end, nil)
        if u then
            if (opt.detalle or 'basico') == 'sonda' then
                r.enanos[1] = enano_sonda(u)
            else
                r.enanos[1] = enano_tabla(u, opt.detalle or 'completo',
                                          math.floor(tonumber(opt.pensamientos) or 8))
            end
        end
        r.lua_us = math.floor((os.clock() - t0u) * 1000000)
        responder(r)
        return
    end

    local cs = try(function() return dfhack.units.getCitizens() end, nil)
    if not cs or #cs == 0 then fallo('sin ciudadanos en la fortaleza'); return end

    -- getCitizens() devuelve tabla Lua: 1-INDEXADA. No confundir con los vectores DF.
    local pool = cs
    if opt.adultos ~= '0' then
        local adultos = {}
        for _, c in ipairs(cs) do
            if try(function() return dfhack.units.isAdult(c) end, false) then
                adultos[#adultos + 1] = c
            end
        end
        if #adultos > 0 then pool = adultos end
    end

    local desde   = math.max(0, math.floor(tonumber(opt.desde) or 0))
    -- n=0 significa "todos": es lo que necesita el bucle de vigilancia.
    local pedidos = math.floor(tonumber(opt.n) or 5)
    local cuantos = (pedidos <= 0) and #pool or math.max(1, pedidos)
    local detalle = opt.detalle or 'basico'
    local max_pens = math.floor(tonumber(opt.pensamientos) or 8)

    local t0 = os.clock()
    local r = estado_base()
    r.total_pool = #pool
    r.enanos = {}
    for k = 0, cuantos - 1 do
        local idx = ((desde + k) % #pool) + 1        -- pool es tabla Lua: 1-indexada
        if detalle == 'sonda' then
            r.enanos[#r.enanos + 1] = enano_sonda(pool[idx])
        else
            r.enanos[#r.enanos + 1] = enano_tabla(pool[idx], detalle, max_pens)
        end
    end
    -- microsegundos enteros: %f llevaria coma decimal segun el locale
    r.lua_us = math.floor((os.clock() - t0) * 1000000)
    responder(r)
    return
end

-- ---------------------------------------------------------------- anuncio
if sub == 'anuncio' then
    local partes = {}
    for i = 2, #args do partes[#partes + 1] = args[i] end
    local texto = table.concat(partes, ' ')
    if texto == '' then fallo('sin texto'); return end

    -- showAnnouncement IGNORA el \n: un texto con saltos sale como una sola
    -- linea. Se parte aqui, que es donde vive el conocimiento de esa rareza.
    local lineas = {}
    for trozo in (texto:gsub('\\n', '\n') .. '\n'):gmatch('([^\n]*)\n') do
        if trozo ~= '' then lineas[#lineas + 1] = trozo end
    end

    local puestas = 0
    for _, l in ipairs(lineas) do
        local ok = pcall(function()
            dfhack.gui.showAnnouncement(a_cp437(l), COLOR_YELLOW, true)
        end)
        if ok then puestas = puestas + 1 end
    end

    if puestas == #lineas and puestas > 0 then
        responder({lineas = puestas})
    else
        fallo(('solo se anunciaron %d de %d lineas'):format(puestas, #lineas))
    end
    return
end

-- ---------------------------------------------------------------- unidad
-- Que fue de un ciudadano que ya no aparece en getCitizens(), que solo
-- devuelve vivos y cuerdos. OJO: flags1 NO tiene un bit 'dead'; el que hay se
-- llama 'inactive' y su propio comentario dice que tambien se activa para
-- criaturas VIVAS que entran o salen del mapa. Se usan las funciones
-- documentadas, que si distinguen los casos (Lua API.rst:1536-1562).
if sub == 'unidad' then
    local id = math.floor(tonumber(opt.id) or -1)
    local u = try(function() return df.unit.find(id) end, nil)
    if not u then responder({existe = false, id = id}); return end
    responder({
        existe   = true,
        id       = id,
        nombre   = dfstr(nombre_de(u)),
        muerto   = try(function() return dfhack.units.isKilled(u) end, false),
        fantasma = try(function() return dfhack.units.isGhost(u) end, false),
        cuerdo   = try(function() return dfhack.units.isSane(u) end, true),
        activo   = try(function() return dfhack.units.isActive(u) end, false),
        vivo     = try(function() return dfhack.units.isAlive(u) end, false),
    })
    return
end

-- ---------------------------------------------------------------- enums
-- Dice que enums traen caption real de DF y cuales hay que humanizar. Es la
-- unica forma de saberlo sin suponer: se pregunta a la build instalada.
if sub == 'enums' then
    local r = {enums = {}}
    for _, tipo in ipairs({'unit_thought_type', 'emotion_type', 'personality_facet_type',
                           'job_skill', 'skill_rating', 'unitpref_type',
                           'unit_relationship_type', 'value_type'}) do
        local existe = try(function() return df[tipo] ~= nil end, false)
        local con_caption, muestra = false, nil
        if existe then
            for v = 0, 40 do
                local cap = try(function() return df[tipo].attrs[v].caption end, nil)
                if type(cap) == 'string' and cap ~= '' then
                    con_caption = true
                    muestra = enum(tipo, v) .. ' -> ' .. cap
                    break
                end
            end
        end
        r.enums[#r.enums + 1] = {tipo = tipo, existe = existe,
                                 caption = con_caption, muestra = muestra}
    end
    responder(r)
    return
end

-- ---------------------------------------------------------------- ui
-- Que ofrece la interfaz de DFHack en ESTA build. Mismo patron que 'enums': no
-- se supone nada, se le pregunta a la instalacion. Existe porque el panel de
-- anuncios es el unico canal que tenemos y no tiene identidad, ni fecha, ni
-- sitio: hace falta saber que alternativas hay ANTES de decidir si se abre un
-- plugin en C++ (que ata a compilar y se rompe en cada actualizacion) o basta
-- con un overlay en Lua (que no tiene ninguna de las dos cosas).
if sub == 'ui' then
    local r = {gui = {}, modulos = {}, colores = {}}

    -- Funciones candidatas. Que esten en esta lista NO significa que existan:
    -- significa que queremos saberlo. La respuesta la da 'tipo'.
    local candidatas = {
        'showAnnouncement', 'showZoomAnnouncement', 'showPopupAnnouncement',
        'showAutoAnnouncement', 'makeAnnouncement', 'writeToGamelog',
        'revealInDwarfmodeMap', 'refreshSidebar', 'getSelectedUnit',
        'getCurViewscreen', 'getDFViewscreen', 'pauseRecenter',
        -- Para el boton: saber que mira el jugador y a quien tiene abierto.
        'getCurFocus', 'getFocusStrings', 'getWidget', 'getSelectedItem',
    }
    for _, nombre in ipairs(candidatas) do
        local tipo = try(function() return type(dfhack.gui[nombre]) end, 'ausente')
        r.gui[#r.gui + 1] = {n = nombre, tipo = tipo}
    end

    -- Modulos que habria que requerir para pintar encima de la interfaz.
    for _, nombre in ipairs({'plugins.overlay', 'gui.widgets', 'gui.dwarfmode',
                             'gui.textures', 'gui.script',
                             -- eventful.onReport avisa de cada anuncio nuevo:
                             -- posible fuente de sucesos mejor que comparar sondeos.
                             'plugins.eventful', 'repeat-util'}) do
        local ok = pcall(require, nombre)
        r.modulos[#r.modulos + 1] = {n = nombre, hay = ok and true or false}
    end

    -- Ida y vuelta de verdad, no de memoria: se escribe cada caracter, se
    -- convierte a CP437 y se vuelve a leer. Lo que no sobreviva sale aqui.
    r.cp437 = {}
    for _, par in ipairs({{'a con tilde', '\195\161'}, {'e con tilde', '\195\169'},
                          {'i con tilde', '\195\173'}, {'o con tilde', '\195\179'},
                          {'u con tilde', '\195\186'}, {'n virgulilla', '\195\177'},
                          {'N virgulilla', '\195\145'}, {'u dieresis', '\195\188'},
                          {'A con tilde', '\195\129'}, {'E con tilde', '\195\137'},
                          {'I con tilde', '\195\141'}, {'O con tilde', '\195\147'},
                          {'U con tilde', '\195\154'},
                          {'interrogacion abre', '\194\191'},
                          {'exclamacion abre', '\194\161'}}) do
        local ida = try(function() return dfhack.utf2df(par[2]) end, nil)
        local vuelta = ida and try(function() return dfhack.df2utf(ida) end, nil) or nil
        r.cp437[#r.cp437 + 1] = {n = par[1], entra = par[2], sale = vuelta or '',
                                 sobrevive = (vuelta == par[2])}
    end

    -- Los anuncios que el propio DF ya ha escrito, en prosa, con marca de
    -- tiempo y posicion. Candidata a fuente de sucesos.
    r.reports = {
        hay = try(function() return df.global.world.status.reports ~= nil end, false),
        n = math.floor(try(function() return #df.global.world.status.reports end, -1)),
        ultimo = try(function()
            local v = df.global.world.status.reports
            local n = #v
            if n == 0 then return '' end
            local rep = v[n - 1]                     -- vector de DF: 0-indexado
            return dfstr(rep.text) .. '  [id ' .. tostring(rep.id)
                   .. ', ano ' .. tostring(rep.year) .. ']'
        end, ''),
    }

    r.screen = try(function() return type(dfhack.screen) end, 'ausente')
    r.textures = try(function() return type(dfhack.textures) end, 'ausente')

    -- Colores del anuncio. Hoy TODAS las lineas del LLM salen en COLOR_YELLOW,
    -- las mismas que los avisos del propio juego.
    for _, c in ipairs({'COLOR_YELLOW', 'COLOR_LIGHTCYAN', 'COLOR_LIGHTMAGENTA',
                        'COLOR_LIGHTGREEN', 'COLOR_WHITE', 'COLOR_GREY'}) do
        local v = try(function() return _ENV[c] or _G[c] end, nil)
        r.colores[#r.colores + 1] = {n = c, v = (type(v) == 'number') and v or -1}
    end

    responder(r)
    return
end

-- ---------------------------------------------------------------- emociones
-- Diagnostico: la fila CRUDA de cada emocion, antes de tocar nada. Existe
-- porque en el prompt salio 'loneliness after varying' -- una caption a medias
-- que el modelo completo por su cuenta. Para arreglarlo hay que ver primero que
-- da DF de verdad, no suponerlo.
-- Que puede SER un 'subthought'. No se supone: se prueba cada interpretacion
-- y se devuelven las que resuelven a algo. La caption dice cual encaja:
-- 'after [varying]' pide un need_type, "saw [somebody]'s dead body" pide una
-- persona. Ver las seis juntas es lo que permite decidir sin adivinar.
local function pistas_de(n)
    if type(n) ~= 'number' or n < 0 then return nil end
    local p = {}
    local function anota(k, v)
        if type(v) == 'string' and v ~= '' then p[#p + 1] = k .. '=' .. v end
    end
    anota('need', try(function() return df.need_type[n] end, nil))
    anota('skill', try(function() return df.job_skill[n] end, nil))
    anota('rel', try(function() return df.unit_relationship_type[n] end, nil))
    anota('edificio', try(function() return df.building_type[n] end, nil))
    anota('histfig', try(function()
        local h = df.historical_figure.find(n)
        return h and dfstr(dfhack.translation.translateName(h.name)) or nil
    end, nil))
    anota('unidad', try(function()
        local u2 = df.unit.find(n)
        return u2 and dfstr(nombre_de(u2)) or nil
    end, nil))
    if #p == 0 then return nil end
    return p
end

if sub == 'emociones' then
    local id = math.floor(tonumber(opt.id) or -1)
    local u = try(function() return df.unit.find(id) end, nil)
    if not u then fallo('no existe la unidad ' .. id); return end
    local r = {id = id, nombre = dfstr(nombre_de(u)), filas = {}, render = {}}

    -- Alguna funcion que componga el texto entero del pensamiento? Se pregunta,
    -- no se supone.
    for _, n in ipairs({'getThoughtText', 'getThoughtDescription', 'getUnitThought'}) do
        r.render[#r.render + 1] = {n = n,
            tipo = try(function() return type(dfhack.units[n]) end, 'ausente')}
    end

    cada(try(function() return u.status.current_soul.personality.emotions end, nil),
        function(m, i)
            local tp = math.floor(try(function() return m.type end, -1))
            local th = math.floor(try(function() return m.thought end, -1))
            local sb = math.floor(try(function() return m.subthought end, -1))
            r.filas[#r.filas + 1] = {
                i = i,
                tipo = enum('emotion_type', tp), tipo_n = tp,
                causa = enum('unit_thought_type', th), causa_n = th,
                -- La caption TAL CUAL la da DF, sin quitarle los corchetes.
                cap = try(function()
                    return df.unit_thought_type.attrs[th].caption end, ''),
                sub_n = sb,
                pistas = pistas_de(sb),
                a = math.floor(try(function() return m.year end, -1)),
                t = math.floor(try(function() return m.year_tick end, -1)),
            }
        end)
    responder(r)
    return
end

fallo('uso: df_estado estado | enums | ui | emociones id= | unidad id= | enanos [n=] [desde=] [id=] [detalle=] [pensamientos=] | anuncio <texto>')
