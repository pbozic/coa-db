-- Indexing and profit maths. Everything else in the addon reads from here.
local ADDON = ...
CoAFarm = CoAFarm or {}
local F = CoAFarm

F.usedIn = {}     -- reagent id -> { product id, ... }
F.data   = nil

local function index()
    local d = CoAFarmData
    if not d then return end
    F.data = d
    for product, recipe in pairs(d.recipes) do
        for _, pair in ipairs(recipe.reagents) do
            local id = pair[1]
            F.usedIn[id] = F.usedIn[id] or {}
            table.insert(F.usedIn[id], product)
        end
    end
end

function F:Item(id) return self.data and self.data.items[id] end
function F:Recipe(id) return self.data and self.data.recipes[id] end

-- Copper formatted the way the game shows it.
function F:Money(copper)
    if not copper then return "|cff808080-|r" end
    local neg = copper < 0
    copper = math.floor(math.abs(copper) + 0.5)
    local g, s, c = math.floor(copper / 10000), math.floor((copper % 10000) / 100), copper % 100
    local out
    if g > 0 then out = string.format("|cffffd700%d|rg |cffc0c0c0%d|rs", g, s)
    elseif s > 0 then out = string.format("|cffc0c0c0%d|rs |cffb87333%d|rc", s, c)
    else out = string.format("|cffb87333%d|rc", c) end
    return (neg and "-" or "") .. out
end

--[[ What one unit costs.

     Mirrors the web model: farmed materials are free, otherwise buy at the
     auction price, or make it when that is cheaper. `seen` breaks the cycle a
     recipe graph can contain. ]]
function F:UnitCost(id, seen)
    seen = seen or {}
    if seen[id] then return nil end
    if CoAFarmSaved and CoAFarmSaved.farm and CoAFarmSaved.farm[id] then return 0, "farmed" end

    local item = self:Item(id)
    local best, how = item and item.buy, item and item.buy and "bought" or nil

    local recipe = self:Recipe(id)
    if recipe then
        seen[id] = true
        local total, complete = 0, true
        for _, pair in ipairs(recipe.reagents) do
            local sub = self:UnitCost(pair[1], seen)
            if not sub then complete = false break end
            total = total + sub * pair[2]
        end
        seen[id] = nil
        if complete then
            local made = total / math.max(1, recipe["yield"] or 1)
            if not best or made < best then best, how = made, "crafted" end
        end
    end
    return best, how
end

--[[ Cost, revenue and profit for one execution of a recipe. ]]
function F:Evaluate(productId)
    local recipe = self:Recipe(productId)
    if not recipe then return nil end

    local cost, missing = 0, nil
    for _, pair in ipairs(recipe.reagents) do
        local unit = self:UnitCost(pair[1])
        if not unit then
            missing = missing or {}
            table.insert(missing, pair[1])
        else
            cost = cost + unit * pair[2]
        end
    end

    local saleItem = self:Item(recipe.sale or productId)
    local sale = saleItem and saleItem.sell
    local count = math.max(1, recipe["yield"] or 1)
    local revenue = sale and sale * count * (1 - (self.data.cut or 0.05))
    local profit
    if revenue and not missing then profit = revenue - cost end

    return {
        recipe = recipe,
        cost = (not missing) and cost or nil,
        revenue = revenue,
        profit = profit,
        missing = missing,
        sale = sale,
        count = count,
    }
end

-- Every recipe this item feeds, best profit first.
function F:Feeds(id)
    local out = {}
    for _, product in ipairs(self.usedIn[id] or {}) do
        local result = self:Evaluate(product)
        if result then table.insert(out, result) end
    end
    table.sort(out, function(a, b)
        return (a.profit or -math.huge) > (b.profit or -math.huge)
    end)
    return out
end

--[[ The finished product a material eventually reaches.

     "Used in" only answers the next step: Spectral Teardrops make an Ancient
     Tear, which tells you nothing about whether the chain is worth starting.
     This walks up the reagent graph until it reaches something sellable and
     returns the best of those, so the tooltip can name the flask or enchant at
     the end of the line.

     Belt buckles are skipped: they out-earn everything, so leaving them in
     means every material reports a buckle and the answer stops being useful.
     Drop `EXCLUDED_FAMILY` below to include them. ]]
local EXCLUDED_FAMILY = "buckle"

function F:FinalProduct(id)
    local best, seen, queue, depth = nil, { [id] = true }, { id }, 0
    while #queue > 0 and depth < 8 do
        local nextQueue = {}
        for _, current in ipairs(queue) do
            for _, product in ipairs(self.usedIn[current] or {}) do
                if not seen[product] then
                    seen[product] = true
                    table.insert(nextQueue, product)
                    local recipe = self:Recipe(product)
                    if recipe and recipe.seed and recipe.family ~= EXCLUDED_FAMILY then
                        local result = self:Evaluate(product)
                        if result and result.profit
                           and (not best or result.profit > best.profit) then
                            best = result
                        end
                    end
                end
            end
        end
        queue, depth = nextQueue, depth + 1
    end
    return best
end

-- Everything worth crafting, best first.
function F:Ranked(onlySeeds)
    local out = {}
    for product in pairs(self.data and self.data.recipes or {}) do
        local recipe = self:Recipe(product)
        if not onlySeeds or recipe.seed then
            local result = self:Evaluate(product)
            if result and result.profit then table.insert(out, result) end
        end
    end
    table.sort(out, function(a, b) return a.profit > b.profit end)
    return out
end

function F:ToggleFarm(id)
    CoAFarmSaved.farm = CoAFarmSaved.farm or {}
    if CoAFarmSaved.farm[id] then CoAFarmSaved.farm[id] = nil else CoAFarmSaved.farm[id] = true end
    return CoAFarmSaved.farm[id]
end

local loader = CreateFrame("Frame")
loader:RegisterEvent("ADDON_LOADED")
loader:SetScript("OnEvent", function(_, _, name)
    if name ~= ADDON then return end
    CoAFarmSaved = CoAFarmSaved or {}
    CoAFarmSaved.farm = CoAFarmSaved.farm or {}
    index()
    if F.data then
        DEFAULT_CHAT_FRAME:AddMessage(string.format(
            "|cff33ff99CoA Farm|r loaded: %d items, prices from %s. |cffffd700/farm|r",
            F:Count(F.data.items), F.data.scanned or "unknown"))
    else
        DEFAULT_CHAT_FRAME:AddMessage("|cff33ff99CoA Farm|r: Data.lua missing or empty.")
    end
end)

function F:Count(t)
    local n = 0
    for _ in pairs(t or {}) do n = n + 1 end
    return n
end
