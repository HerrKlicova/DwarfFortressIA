-- dfhack_spike.lua -- script feo de spike, se tira despues
--
-- INSTALACION: copiar este fichero a
--   D:\Steam\steamapps\common\Dwarf Fortress\dfhack-config\scripts\dfhack_spike.lua
-- Cualquier .lua en dfhack-config/scripts se convierte en un comando DFHack
-- con el nombre del fichero (docs/dev/Lua API.rst, seccion "Scripts").
--
-- Se invoca desde fuera con el RPC RunCommand (id 1). El print() vuelve al
-- cliente como mensajes CoreTextNotification.
--
-- Subcomandos:
--   dfhack_spike dwarf              -> imprime NOMBRE / ID de un enano vivo
--   dfhack_spike fields             -> dice que campos existen de verdad (sin volcarlos)
--   dfhack_spike announce <texto>   -> mete <texto> en el log de anuncios

local args = {...}
local sub = args[1]

-- OJO: solo se convierten las cadenas que vienen de DF, nunca nuestro propio
-- andamiaje. En CP437 el byte 0x09 no es un tabulador sino el caracter ○, asi
-- que pasar una linea entera por df2utf destroza los separadores.
local function out(s)
    print(tostring(s))
end

-- DF guarda las cadenas en CP437; el socket lleva UTF-8.
local function dfstr(s)
    return dfhack.df2utf(tostring(s))
end

local function get_citizens()
    local ok, list = pcall(dfhack.units.getCitizens)
    if ok and list and #list > 0 then return list end
    return nil
end

-- ---------------------------------------------------------------- dwarf
if sub == 'dwarf' then
    local citizens = get_citizens()
    if not citizens then
        out('ERROR|no hay ciudadanos (fortaleza cargada? modo fortaleza?)')
        return
    end
    local u = citizens[1]

    -- getVisibleName devuelve un language_name; translateName lo pasa a string.
    local name = nil
    local ok, vis = pcall(dfhack.units.getVisibleName, u)
    if ok and vis then
        local ok2, s = pcall(dfhack.translation.translateName, vis)
        if ok2 then name = s end
    end
    if not name or name == '' then
        -- respaldo documentado
        local ok3, s = pcall(dfhack.units.getReadableName, u)
        if ok3 then name = s end
    end

    out('ID|' .. tostring(u.id))
    out('NOMBRE|' .. dfstr(name or '(sin nombre)'))
    return
end

-- ---------------------------------------------------------------- fields
-- Sondea que rutas de datos existen DE VERDAD en esta build, sin volcar
-- contenido. Esto es lo que alimenta HALLAZGOS.md.
if sub == 'fields' then
    local citizens = get_citizens()
    if not citizens then
        out('ERROR|no hay ciudadanos')
        return
    end
    local u = citizens[1]

    local function probe(label, fn)
        local ok, v = pcall(fn)
        if not ok or v == nil then
            out('NO|' .. label)
        elseif type(v) == 'userdata' or type(v) == 'table' then
            local n = nil
            pcall(function() n = #v end)
            out('OK|' .. label .. '|' .. (n and ('n=' .. n) or 'compound'))
        else
            out('OK|' .. label .. '|' .. type(v))
        end
    end

    probe('unit.id',                          function() return u.id end)
    probe('unit.race',                        function() return u.race end)
    probe('unit.caste',                       function() return u.caste end)
    probe('unit.sex',                         function() return u.sex end)
    probe('unit.civ_id',                      function() return u.civ_id end)
    probe('unit.hist_figure_id',              function() return u.hist_figure_id end)
    probe('unit.relationship_ids',            function() return u.relationship_ids end)
    probe('unit.status.current_soul',         function() return u.status.current_soul end)
    probe('soul.skills',                      function() return u.status.current_soul.skills end)
    probe('soul.preferences',                 function() return u.status.current_soul.preferences end)
    probe('soul.mental_attrs',                function() return u.status.current_soul.mental_attrs end)
    probe('personality.traits',               function() return u.status.current_soul.personality.traits end)
    probe('personality.values',               function() return u.status.current_soul.personality.values end)
    probe('personality.emotions (pensamientos)', function() return u.status.current_soul.personality.emotions end)
    probe('personality.dreams',               function() return u.status.current_soul.personality.dreams end)
    probe('personality.stress',               function() return u.status.current_soul.personality.stress end)
    probe('dfhack.units.getReadableName',     function() return dfhack.units.getReadableName(u) end)
    probe('dfhack.units.getProfessionName',   function() return dfhack.units.getProfessionName(u) end)
    probe('dfhack.units.getAge',              function() return dfhack.units.getAge(u) end)
    return
end

-- ---------------------------------------------------------------- announce
if sub == 'announce' then
    local parts = {}
    for i = 2, #args do parts[#parts + 1] = args[i] end
    local text = table.concat(parts, ' ')
    if text == '' then
        out('ERROR|sin texto')
        return
    end
    -- El socket trae UTF-8; DF quiere CP437.
    local ok, err = pcall(function()
        dfhack.gui.showAnnouncement(dfhack.utf2df(text), COLOR_YELLOW, true)
    end)
    if ok then
        out('OK|anuncio inyectado')
    else
        out('ERROR|showAnnouncement fallo: ' .. tostring(err))
    end
    return
end

out('ERROR|uso: dfhack_spike dwarf / fields / announce <texto>')
