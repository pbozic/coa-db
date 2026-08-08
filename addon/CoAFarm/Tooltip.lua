-- Adds crafting and farming context to any item tooltip.
local F = CoAFarm
local MAX_FEEDS = 3

local function itemIdFromLink(link)
    if not link then return nil end
    local id = link:match("item:(%d+)")
    return id and tonumber(id) or nil
end

local function addFarmLine(tip, item)
    local farm = item.farm
    if not farm then
        if item.gather then
            tip:AddDoubleLine("Gather", "Herbalism " .. item.gather, 0.6, 0.6, 0.6, 1, 1, 1)
        end
        return
    end
    local where = farm.zone or "?"
    if farm.xy then where = string.format("%s  %.0f, %.0f", where, farm.xy[1], farm.xy[2]) end
    tip:AddDoubleLine("Farm", where, 0.6, 0.6, 0.6, 0.4, 0.9, 0.4)
    if farm.npc then
        tip:AddDoubleLine(" ", string.format("%s  |cff808080%.0f%% · lvl %s|r",
            farm.npc, farm.pct or 0, farm.lvl or "?"), 0, 0, 0, 0.8, 0.8, 0.8)
    end
end

--[[ One tooltip pass.

     Shown for anything the addon knows: what the item sells for, what it can be
     turned into and for how much, and where to farm it. Kept to a few lines --
     a tooltip that fills the screen stops being read. ]]
local function decorate(tip, link)
    if not F.data then return end
    local id = itemIdFromLink(link)
    if not id then return end

    local item, recipe = F:Item(id), F:Recipe(id)
    local feeds = F:Feeds(id)
    if not item and not recipe and #feeds == 0 then return end

    tip:AddLine(" ")

    if item and (item.buy or item.sell) then
        local depth = item.qty and string.format("  |cff808080%d listed|r", item.qty) or ""
        tip:AddDoubleLine("Auction", F:Money(item.buy) .. depth, 0.6, 0.6, 0.6, 1, 1, 1)
    end
    if item and item.moves then
        tip:AddDoubleLine("Leaving the AH", string.format("~%.0f/day", item.moves),
            0.6, 0.6, 0.6, 0.8, 0.8, 0.8)
    end

    -- If this item is itself craftable, show whether making it beats buying it.
    if recipe then
        local result = F:Evaluate(id)
        if result then
            if result.profit then
                local colour = result.profit >= 0 and "|cff40d070" or "|cffef5f5f"
                tip:AddDoubleLine(
                    string.format("Craft %s%s", recipe.prof or "", result.count > 1
                        and string.format(" (makes %d)", result.count) or ""),
                    string.format("%s profit %s%s|r", F:Money(result.cost), colour,
                        F:Money(result.profit)),
                    0.6, 0.6, 0.6, 1, 1, 1)
            elseif result.missing then
                tip:AddDoubleLine("Craft", "|cff808080material has no price|r",
                    0.6, 0.6, 0.6, 0.5, 0.5, 0.5)
            end
        end
    end

    if #feeds > 0 then
        tip:AddLine("Used in", 0.6, 0.6, 0.6)
        for i = 1, math.min(MAX_FEEDS, #feeds) do
            local r = feeds[i]
            local right = r.profit and (r.profit >= 0 and "|cff40d070" or "|cffef5f5f")
                .. F:Money(r.profit) .. "|r" or "|cff808080-|r"
            tip:AddDoubleLine("  " .. (r.recipe.name or "?"), right, 1, 1, 1, 1, 1, 1)
        end
        if #feeds > MAX_FEEDS then
            tip:AddLine(string.format("  |cff808080and %d more — /farm|r", #feeds - MAX_FEEDS))
        end
    end

    if item then addFarmLine(tip, item) end

    if CoAFarmSaved.farm[id] then
        tip:AddLine("|cff40d070On your farm list|r")
    end
    tip:Show()
end

local function hook(tip)
    tip:HookScript("OnTooltipSetItem", function(self)
        local _, link = self:GetItem()
        decorate(self, link)
    end)
end

hook(GameTooltip)
hook(ItemRefTooltip)
if ShoppingTooltip1 then hook(ShoppingTooltip1) end
if ShoppingTooltip2 then hook(ShoppingTooltip2) end
