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
--     -> {ok, ...estado..., enanos:[{id,nombre,profesion,edad,adulto,estres,
--                                    rasgos,pensamientos,relaciones,preferencias,habilidades}]}
--   df_estado anuncio <texto>
--     -> {ok, lineas}     (parte el texto por \n: showAnnouncement ignora los saltos)
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

    -- Emociones: cada una lleva marca de tiempo del juego (year, year_tick, de
    -- df.personality.xml). Con ella la deteccion de "emocion nueva" es exacta;
    -- comparar contadores fallaria porque DF tambien PODA emociones viejas.
    local emo = try(function() return u.status.current_soul.personality.emotions end, nil)
    e.emo_n, e.emo_a, e.emo_t = 0, -1, -1
    if emo then
        local n = try(function() return #emo end, 0)
        e.emo_n = n
        for i = 0, n - 1 do                       -- vector de DF: 0-indexado
            local m = try(function() return emo[i] end, nil)
            if m then
                local a = math.floor(try(function() return m.year end, -1))
                local t = math.floor(try(function() return m.year_tick end, -1))
                if a > e.emo_a or (a == e.emo_a and t > e.emo_t) then
                    e.emo_a, e.emo_t = a, t
                    -- enum_txt, no enum: esto acaba en el prompt como disparador.
                    -- Con el identificador crudo el modelo lo repite literalmente
                    -- ("¡Euphoria! ¡Que alegria...!"). unit_thought_type tiene
                    -- captions de verdad, asi que aqui se gana bastante.
                    e.emo_tipo   = enum_txt('emotion_type', try(function() return m.type end, -1))
                    e.emo_causa  = enum_txt('unit_thought_type', try(function() return m.thought end, -1))
                    e.emo_fuerza = math.floor(try(function() return m.strength end, 0))
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

        cada(try(function() return alma.personality.emotions end, nil), function(m)
            local tipo_e = try(function() return m.type end, -1)
            local causa_e = try(function() return m.thought end, -1)
            e.pensamientos[#e.pensamientos + 1] = {
                emocion     = enum('emotion_type', tipo_e),
                emocion_txt = enum_txt('emotion_type', tipo_e),
                causa       = enum('unit_thought_type', causa_e),
                causa_txt   = enum_txt('unit_thought_type', causa_e),
                fuerza      = math.floor(try(function() return m.strength end, 0)),
            }
        end, max_pens)

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
            dfhack.gui.showAnnouncement(dfhack.utf2df(l), COLOR_YELLOW, true)
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

fallo('uso: df_estado estado | enums | unidad id= | enanos [n=] [desde=] [detalle=] [pensamientos=] | anuncio <texto>')
