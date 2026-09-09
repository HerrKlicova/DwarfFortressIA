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

local function estado_base()
    return {
        mundo = try(function() return dfhack.isWorldLoaded() end, false),
        mapa  = try(function() return dfhack.isMapLoaded() end, false),
        pausa = try(function() return dfhack.world.ReadPauseState() end, false),
        frame = math.floor(try(function() return df.global.world.frame_counter end, -1)),
        n_ciudadanos = math.floor(try(function()
            local c = dfhack.units.getCitizens(); return c and #c or 0
        end, 0)),
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

local function enano_tabla(u, detalle, max_pens)
    local e = {
        id        = math.floor(try(function() return u.id end, -1)),
        nombre    = dfstr(nombre_de(u)),
        profesion = dfstr(try(function() return dfhack.units.getProfessionName(u) end, 'desconocida')),
        edad      = math.floor(try(function() return dfhack.units.getAge(u) end, -1)),
        adulto    = try(function() return dfhack.units.isAdult(u) end, false),
        estres    = math.floor(try(function() return u.status.current_soul.personality.stress end, 0)),
    }
    if detalle ~= 'completo' then return e end

    local alma = try(function() return u.status.current_soul end, nil)
    e.rasgos, e.pensamientos, e.relaciones = {}, {}, {}
    e.preferencias, e.habilidades = {}, {}

    if alma then
        cada(try(function() return alma.personality.traits end, nil), function(v, i)
            e.rasgos[#e.rasgos + 1] = {n = enum('personality_facet_type', i), v = math.floor(v)}
        end)

        cada(try(function() return alma.personality.emotions end, nil), function(m)
            e.pensamientos[#e.pensamientos + 1] = {
                emocion = enum('emotion_type', try(function() return m.type end, -1)),
                causa   = enum('unit_thought_type', try(function() return m.thought end, -1)),
                fuerza  = math.floor(try(function() return m.strength end, 0)),
            }
        end, max_pens)

        cada(try(function() return alma.preferences end, nil), function(p)
            e.preferencias[#e.preferencias + 1] = enum('unitpref_type', try(function() return p.type end, -1))
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
            e.habilidades[#e.habilidades + 1] = {
                n = enum('job_skill', try(function() return s.id end, -1)),
                nivel = enum('skill_rating', bruto),
                nivel_n = math.floor(nivel_n),
            }
        end)
    end

    cada(try(function() return u.relationship_ids end, nil), function(id, i)
        if id and id >= 0 then
            local otro = try(function() return df.unit.find(id) end, nil)
            e.relaciones[#e.relaciones + 1] = {
                tipo = enum('unit_relationship_type', i),
                quien = otro and dfstr(nombre_de(otro)) or ('unidad ' .. tostring(id)),
            }
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
    local cuantos = math.max(1, math.floor(tonumber(opt.n) or 5))
    local detalle = opt.detalle or 'basico'
    local max_pens = math.floor(tonumber(opt.pensamientos) or 8)

    local t0 = os.clock()
    local r = estado_base()
    r.total_pool = #pool
    r.enanos = {}
    for k = 0, cuantos - 1 do
        local idx = ((desde + k) % #pool) + 1        -- pool es tabla Lua: 1-indexada
        r.enanos[#r.enanos + 1] = enano_tabla(pool[idx], detalle, max_pens)
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

fallo('uso: df_estado estado | enanos [n=] [desde=] [detalle=] [pensamientos=] | anuncio <texto>')
