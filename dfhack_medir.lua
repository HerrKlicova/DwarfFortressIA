-- dfhack_medir.lua -- INSTRUMENTO DE MEDIDA. Se tira al acabar la fase 1.
--
-- No toca dfhack_spike.lua a proposito: el spike es la base que funciona y no
-- quiero contaminarla con instrumentacion que luego hay que quitar.
--
-- INSTALACION: copiar junto al otro, en
--   <Dwarf Fortress>/dfhack-config/scripts/dfhack_medir.lua
--
-- Subcomandos:
--   dfhack_medir estado             -> mundo/mapa/pausa/frame/ciudadanos. Funciona SIN partida.
--   dfhack_medir contexto [n]       -> volcado completo de un enano (prompt largo)
--   dfhack_medir bench [n]          -> lee n enanos y cronometra el lado Lua
--   dfhack_medir anuncio <texto>    -> anuncia y reporta longitud y tiempo
--
-- INVARIANTES QUE YA COSTARON UNA EJECUCION FALLIDA CADA UNA:
--   1. Los contenedores de DFHack son 0-INDEXADOS (Lua API.rst:250) y lanzan
--      error al salirse. Con #v == n, los indices validos son 0..n-1. Las
--      tablas Lua que devuelve getCitizens() SI son 1-indexadas. No confundir.
--   2. Solo pasar por df2utf las cadenas que vienen de DF, nunca los
--      delimitadores: en la tabla CP437 de DF el byte 0x09 es el glifo ○.

local args = {...}
local sub = args[1]

local function out(s) print(tostring(s)) end
local function dfstr(s) return dfhack.df2utf(tostring(s)) end

-- Todo acceso a campo va envuelto: si un nombre no existe en esta build,
-- degrada a un valor por defecto en vez de tirar el script entero.
local function try(fn, fallback)
    local ok, v = pcall(fn)
    if ok and v ~= nil then return v end
    return fallback
end

-- Nombre legible de un enum. Sirve tanto si el campo devuelve el numero
-- como si ya devuelve la clave.
local function enum(tipo, valor)
    local nombre = try(function() return df[tipo][valor] end, nil)
    if type(nombre) == 'string' then return nombre end
    if type(valor) == 'string' then return valor end
    return tostring(valor)
end

local function hay_partida()
    return try(function() return dfhack.isWorldLoaded() end, false)
       and try(function() return dfhack.isMapLoaded() end, false)
end

local function elegir(idx_txt)
    local cs = try(function() return dfhack.units.getCitizens() end, nil)
    if not cs or #cs == 0 then return nil end
    local adultos = {}
    for _, c in ipairs(cs) do
        if try(function() return dfhack.units.isAdult(c) end, false) then
            adultos[#adultos + 1] = c
        end
    end
    local pool = (#adultos > 0) and adultos or cs
    local idx = (math.floor(tonumber(idx_txt) or 0) % #pool) + 1
    return pool[idx], #cs, #adultos
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

-- ---------------------------------------------------------------- estado
-- Tiene que funcionar SIN partida cargada: es el experimento 3.
if sub == 'estado' then
    out('MUNDO|' .. tostring(try(function() return dfhack.isWorldLoaded() end, 'n/d')))
    out('MAPA|' .. tostring(try(function() return dfhack.isMapLoaded() end, 'n/d')))
    out('PAUSA|' .. tostring(try(function() return dfhack.world.ReadPauseState() end, 'n/d')))
    out('FRAME|' .. tostring(try(function() return df.global.world.frame_counter end, -1)))
    out('CIUDADANOS|' .. tostring(try(function()
        local c = dfhack.units.getCitizens(); return c and #c or 0
    end, -1)))
    out('RELOJ|' .. string.format('%.4f', os.clock()))
    return
end

-- ---------------------------------------------------------------- contexto
if sub == 'contexto' then
    if not hay_partida() then out('ERROR|sin partida cargada'); return end
    local u, n_cs, n_ad = elegir(args[2])
    if not u then out('ERROR|sin ciudadanos'); return end

    local t0 = os.clock()
    out('CIUDADANOS|' .. tostring(n_cs) .. '|ADULTOS|' .. tostring(n_ad))
    out('ID|' .. tostring(u.id))
    out('NOMBRE|' .. dfstr(nombre_de(u)))
    out('PROFESION|' .. dfstr(try(function() return dfhack.units.getProfessionName(u) end, '?')))
    out('EDAD|' .. tostring(math.floor(try(function() return dfhack.units.getAge(u) end, -1))))
    out('SEXO|' .. enum('pronoun_type', try(function() return u.sex end, -1)))
    out('ESTRES|' .. tostring(try(function() return u.status.current_soul.personality.stress end, 'n/d')))

    -- Rasgos: array estatico indexado por personality_facet_type (df.personality.xml:1495)
    local tr = try(function() return u.status.current_soul.personality.traits end, nil)
    if tr then
        local n = try(function() return #tr end, 0)
        for i = 0, n - 1 do                      -- 0-indexado
            local v = try(function() return tr[i] end, nil)
            if v then out('RASGO|' .. enum('personality_facet_type', i) .. '|' .. tostring(v)) end
        end
    end

    -- Valores: vector de personality_valuest {type: value_type, strength}
    local va = try(function() return u.status.current_soul.personality.values end, nil)
    if va then
        for i = 0, try(function() return #va end, 0) - 1 do
            local v = try(function() return va[i] end, nil)
            if v then
                out('VALOR|' .. enum('value_type', try(function() return v.type end, -1))
                    .. '|' .. tostring(try(function() return v.strength end, 0)))
            end
        end
    end

    -- Pensamientos: personality_moodst {type: emotion_type, thought: unit_thought_type}
    local emo = try(function() return u.status.current_soul.personality.emotions end, nil)
    if emo then
        for i = 0, try(function() return #emo end, 0) - 1 do
            local e = try(function() return emo[i] end, nil)
            if e then
                out('PENSAMIENTO|' .. enum('emotion_type', try(function() return e.type end, -1))
                    .. '|' .. enum('unit_thought_type', try(function() return e.thought end, -1))
                    .. '|' .. tostring(try(function() return e.strength end, 0)))
            end
        end
    end

    -- Relaciones: array de 9 indexado por unit_relationship_type (df.unit.xml:2731)
    local rel = try(function() return u.relationship_ids end, nil)
    if rel then
        for i = 0, try(function() return #rel end, 0) - 1 do
            local id = try(function() return rel[i] end, -1)
            if id and id >= 0 then
                local otro = try(function() return df.unit.find(id) end, nil)
                local quien = otro and dfstr(nombre_de(otro)) or ('unidad ' .. tostring(id))
                out('RELACION|' .. enum('unit_relationship_type', i) .. '|' .. quien)
            end
        end
    end

    -- Preferencias: vector de unit_preference {type: unitpref_type} (df.unit.xml:1504)
    local pref = try(function() return u.status.current_soul.preferences end, nil)
    if pref then
        for i = 0, try(function() return #pref end, 0) - 1 do
            local p = try(function() return pref[i] end, nil)
            if p then out('PREFERENCIA|' .. enum('unitpref_type', try(function() return p.type end, -1))) end
        end
    end

    -- Habilidades: vector de unit_skill {id: job_skill, rating: skill_rating}
    local sk = try(function() return u.status.current_soul.skills end, nil)
    if sk then
        for i = 0, try(function() return #sk end, 0) - 1 do
            local s = try(function() return sk[i] end, nil)
            if s then
                out('HABILIDAD|' .. enum('job_skill', try(function() return s.id end, -1))
                    .. '|' .. enum('skill_rating', try(function() return s.rating end, -1)))
            end
        end
    end

    out('LUA_SEG|' .. string.format('%.4f', os.clock() - t0))
    return
end

-- ---------------------------------------------------------------- bench
if sub == 'bench' then
    if not hay_partida() then out('ERROR|sin partida cargada'); return end
    local cs = try(function() return dfhack.units.getCitizens() end, nil)
    if not cs or #cs == 0 then out('ERROR|sin ciudadanos'); return end

    local n = math.min(math.floor(tonumber(args[2]) or 5), #cs)
    local f0 = try(function() return df.global.world.frame_counter end, -1)
    local t0 = os.clock()
    local campos = 0

    for i = 1, n do                              -- getCitizens devuelve tabla Lua: 1-indexada
        local u = cs[i]
        try(function() return nombre_de(u) end, nil)
        try(function() return dfhack.units.getProfessionName(u) end, nil)
        local emo = try(function() return u.status.current_soul.personality.emotions end, nil)
        if emo then
            for j = 0, try(function() return #emo end, 0) - 1 do   -- vector DF: 0-indexado
                try(function() return emo[j].type end, nil); campos = campos + 1
            end
        end
        local tr = try(function() return u.status.current_soul.personality.traits end, nil)
        if tr then
            for j = 0, try(function() return #tr end, 0) - 1 do
                try(function() return tr[j] end, nil); campos = campos + 1
            end
        end
    end

    local dt = os.clock() - t0
    local f1 = try(function() return df.global.world.frame_counter end, -1)
    out('ENANOS|' .. tostring(n))
    out('CAMPOS|' .. tostring(campos))
    out('LUA_SEG|' .. string.format('%.4f', dt))
    out('FRAME_INI|' .. tostring(f0))
    out('FRAME_FIN|' .. tostring(f1))
    return
end

-- ---------------------------------------------------------------- anuncio
if sub == 'anuncio' then
    local parts = {}
    for i = 2, #args do parts[#parts + 1] = args[i] end
    local text = table.concat(parts, ' ')
    text = text:gsub('\\n', '\n')     -- permite probar multilinea desde Python
    if text == '' then out('ERROR|sin texto'); return end
    local t0 = os.clock()
    local ok, err = pcall(function()
        dfhack.gui.showAnnouncement(dfhack.utf2df(text), COLOR_YELLOW, true)
    end)
    if ok then
        out('OK|BYTES|' .. tostring(#text) .. '|LUA_SEG|' .. string.format('%.4f', os.clock() - t0))
    else
        out('ERROR|' .. tostring(err))
    end
    return
end

out('ERROR|uso: dfhack_medir estado / contexto [n] / bench [n] / anuncio <texto>')
