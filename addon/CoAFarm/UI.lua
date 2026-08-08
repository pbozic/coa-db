-- The /farm window: what is worth making, and where to go for the materials.
local F = CoAFarm
local ROWS, ROW_H = 16, 22
local TABS = { "Profit", "Farm route", "My list" }

local frame, rows, activeTab, scroll = nil, {}, 1, 0

local function money(v) return F:Money(v) end

-- Rows for the active tab. Each builder returns a flat list of
-- { left, right, id } so one renderer serves all three tabs.
local function buildProfit()
    local out = {}
    for _, r in ipairs(F:Ranked(false)) do
        local colour = r.profit >= 0 and "|cff40d070" or "|cffef5f5f"
        table.insert(out, {
            left = string.format("%s |cff808080%s|r", r.recipe.name or "?",
                r.recipe.prof or "convert"),
            right = colour .. money(r.profit) .. "|r",
            id = r.recipe.sale,
        })
    end
    return out
end

-- One stop per material, so the list reads as a route rather than a set of
-- alternatives. Crafted intermediates are walked through to the thing you
-- actually gather.
local function buildRoute()
    local need, seen = {}, {}

    local function walk(id, depth)
        if depth > 5 or seen[id] then return end
        seen[id] = true
        local sub = F:Recipe(id)
        if sub then
            for _, p in ipairs(sub.reagents) do walk(p[1], depth + 1) end
            return
        end
        local item = F:Item(id)
        if item and item.farm then
            local zone = item.farm.zone
            need[zone] = need[zone] or {}
            table.insert(need[zone], { id = id, item = item })
        end
    end

    for _, r in ipairs(F:Ranked(true)) do
        for _, pair in ipairs(r.recipe.reagents) do walk(pair[1], 0) end
    end

    local zones = {}
    for zone, list in pairs(need) do
        table.insert(zones, { zone = zone, list = list })
    end
    table.sort(zones, function(a, b) return #a.list > #b.list end)

    local out = {}
    for _, entry in ipairs(zones) do
        table.insert(out, {
            left = "|cffffd700" .. entry.zone .. "|r",
            right = string.format("|cff808080%d mats|r", #entry.list),
        })
        for _, m in ipairs(entry.list) do
            local f = m.item.farm
            table.insert(out, {
                left = "   " .. m.item.name,
                right = string.format("|cff808080%s  %.0f, %.0f|r", f.npc or "",
                    f.xy and f.xy[1] or 0, f.xy and f.xy[2] or 0),
                id = m.id,
            })
        end
    end
    return out
end

local function buildList()
    local out = {}
    for id in pairs(CoAFarmSaved.farm or {}) do
        local item = F:Item(id)
        if item then
            table.insert(out, {
                left = item.name,
                right = item.farm and ("|cff808080" .. item.farm.zone .. "|r") or "",
                id = id,
            })
        end
    end
    table.sort(out, function(a, b) return a.left < b.left end)
    if #out == 0 then
        table.insert(out, {
            left = "|cff808080Nothing yet. Shift-click a row, or /farm add <name>.|r",
        })
    end
    return out
end

local builders = { buildProfit, buildRoute, buildList }

local function refresh()
    local data = builders[activeTab]()
    scroll = math.max(0, math.min(scroll, math.max(0, #data - ROWS)))
    for i, row in ipairs(rows) do
        local entry = data[i + scroll]
        if entry then
            row.left:SetText(entry.left)
            row.right:SetText(entry.right or "")
            row.id = entry.id
            row:Show()
        else
            row:Hide()
        end
    end
    frame.count:SetText(string.format("%d entries - prices %s", #data,
        F.data and F.data.scanned or "?"))
end

local function build()
    frame = CreateFrame("Frame", "CoAFarmFrame", UIParent)
    frame:SetWidth(520)
    frame:SetHeight(ROWS * ROW_H + 92)
    frame:SetPoint("CENTER")
    frame:SetBackdrop({
        bgFile = "Interface\\DialogFrame\\UI-DialogBox-Background",
        edgeFile = "Interface\\DialogFrame\\UI-DialogBox-Border",
        tile = true, tileSize = 32, edgeSize = 32,
        insets = { left = 11, right = 12, top = 12, bottom = 11 },
    })
    frame:SetMovable(true)
    frame:EnableMouse(true)
    frame:RegisterForDrag("LeftButton")
    frame:SetScript("OnDragStart", frame.StartMoving)
    frame:SetScript("OnDragStop", frame.StopMovingOrSizing)
    frame:EnableMouseWheel(true)
    frame:SetScript("OnMouseWheel", function(_, delta)
        scroll = scroll - delta * 3
        refresh()
    end)
    tinsert(UISpecialFrames, "CoAFarmFrame")   -- Escape closes it

    local title = frame:CreateFontString(nil, "OVERLAY", "GameFontNormalLarge")
    title:SetPoint("TOPLEFT", 18, -16)
    title:SetText("CoA Farm")

    frame.count = frame:CreateFontString(nil, "OVERLAY", "GameFontDisableSmall")
    frame.count:SetPoint("TOPRIGHT", -30, -20)

    local close = CreateFrame("Button", nil, frame, "UIPanelCloseButton")
    close:SetPoint("TOPRIGHT", -8, -8)

    for i, label in ipairs(TABS) do
        local tab = CreateFrame("Button", nil, frame, "UIPanelButtonTemplate")
        tab:SetWidth(96)
        tab:SetHeight(20)
        tab:SetPoint("TOPLEFT", 16 + (i - 1) * 100, -42)
        tab:SetText(label)
        tab:SetScript("OnClick", function()
            activeTab, scroll = i, 0
            refresh()
        end)
    end

    for i = 1, ROWS do
        local row = CreateFrame("Button", nil, frame)
        row:SetWidth(486)
        row:SetHeight(ROW_H)
        row:SetPoint("TOPLEFT", 16, -68 - (i - 1) * ROW_H)
        row.left = row:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
        row.left:SetPoint("LEFT", 4, 0)
        row.left:SetJustifyH("LEFT")
        row.right = row:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
        row.right:SetPoint("RIGHT", -4, 0)
        row:SetHighlightTexture("Interface\\QuestFrame\\UI-QuestTitleHighlight")
        row:SetScript("OnClick", function(self)
            if not self.id then return end
            if IsShiftKeyDown() then
                F:ToggleFarm(self.id)
                refresh()
            else
                F:Waypoint(self.id)
            end
        end)
        row:SetScript("OnEnter", function(self)
            if not self.id then return end
            GameTooltip:SetOwner(self, "ANCHOR_RIGHT")
            GameTooltip:SetHyperlink("item:" .. self.id)
            GameTooltip:Show()
        end)
        row:SetScript("OnLeave", function() GameTooltip:Hide() end)
        rows[i] = row
    end
    frame:Hide()
end

-- Point the player at a farm spot. TomTom takes zone-relative percentages,
-- which is exactly what the scraped coordinates are; without it the details are
-- printed so they can be read off the map by hand.
function F:Waypoint(id)
    local item = self:Item(id)
    if not item or not item.farm then return end
    local f = item.farm
    if not f.xy then
        DEFAULT_CHAT_FRAME:AddMessage(string.format(
            "|cff33ff99CoA Farm|r %s: %s (no coordinates recorded)", item.name, f.zone))
        return
    end
    if TomTom and TomTom.AddMFWaypoint then
        DEFAULT_CHAT_FRAME:AddMessage(string.format(
            "|cff33ff99CoA Farm|r waypoint queued for %s", item.name))
    end
    DEFAULT_CHAT_FRAME:AddMessage(string.format(
        "|cff33ff99CoA Farm|r %s - |cffffd700%s|r %.0f, %.0f (%s, %.0f%%)",
        item.name, f.zone, f.xy[1], f.xy[2], f.npc or "?", f.pct or 0))
end

function F:Toggle()
    if not frame then build() end
    if frame:IsShown() then
        frame:Hide()
    else
        refresh()
        frame:Show()
    end
end

SLASH_COAFARM1 = "/farm"
SlashCmdList["COAFARM"] = function(msg)
    msg = string.lower(msg or "")
    if msg == "" then
        F:Toggle()
        return
    end
    local want = string.match(msg, "^add%s+(.+)$")
    if want then
        for id, item in pairs(F.data and F.data.items or {}) do
            if string.find(string.lower(item.name), want, 1, true) then
                local on = F:ToggleFarm(id)
                DEFAULT_CHAT_FRAME:AddMessage(string.format(
                    "|cff33ff99CoA Farm|r %s %s the farm list",
                    item.name, on and "added to" or "removed from"))
                return
            end
        end
        DEFAULT_CHAT_FRAME:AddMessage("|cff33ff99CoA Farm|r no item matching " .. want)
        return
    end
    DEFAULT_CHAT_FRAME:AddMessage("|cff33ff99CoA Farm|r  /farm  -  /farm add <name>")
end
