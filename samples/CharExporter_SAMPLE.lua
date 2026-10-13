-- SYNTHETIC SAMPLE for trying out the importer. Two made-up characters.
-- Laid out the way WoW writes SavedVariables and the way CharExporter's source
-- builds its data (CharExporterDB.global["Name-Realm"]); it was NOT captured from the game.
CharExporterDB = {
	["global"] = {
		["Testalch-Area 52"] = {
			["timestamp"] = 1760000000,
			["basic"] = {
				["name"] = "Testalch",
				["realm"] = "Area 52",
				["class"] = "Mage",
				["spec"] = "Frost",
				["level"] = 90,
				["ilvl"] = 250.5,
				["race"] = "Human",
				["faction"] = "Alliance",
			},
			["professions"] = {
				{
					["name"] = "Alchemy",
					["skillLevel"] = 100,
					["maxSkillLevel"] = 100,
					["concentration"] = 850,
					["concentrationMax"] = 1000,
					["knowledgePoints"] = 12,
					["recipesKnown"] = 150,
					["equipment"] = {
						{
							["name"] = "Test Alchemist's Apron",
							["ilvl"] = 250,
							["link"] = "|cnIQ4:|Hitem:12345::::::::90:|h[Test Alchemist's Apron]|h|r",
							["slot"] = "Accessory",
							["slotID"] = 20,
						}, -- [1]
					},
					["specializations"] = {
						{
							["name"] = "Potion Mastery",
							["state"] = 1,
							["paths"] = {
								{
									["name"] = "Potion Mastery",
									["currentRank"] = 5,
									["maxRank"] = 10,
									["state"] = 1,
									["perks"] = {
										{
											["name"] = "Test Perk A",
											["state"] = 2,
											["isMajor"] = false,
										}, -- [1]
										{
											["name"] = "Test Perk B",
											["state"] = 0,
											["isMajor"] = true,
										}, -- [2]
									},
									["children"] = {
										{
											["name"] = "Test Child Path",
											["currentRank"] = 7,
											["maxRank"] = 7,
											["state"] = 2,
											["perks"] = {},
											["children"] = {},
										}, -- [1]
									},
								}, -- [1]
							},
						}, -- [1]
					},
				}, -- [1]
				{
					["name"] = "Herbalism",
					["skillLevel"] = 75,
					["maxSkillLevel"] = 100,
					["concentration"] = 0,
					["concentrationMax"] = 0,
					["knowledgePoints"] = 0,
					["recipesKnown"] = 0,
					["equipment"] = {},
					["specializations"] = {},
				}, -- [2]
			},
		},
		["Testjewel-Azjol-Nerub"] = {
			["timestamp"] = 1760086400,
			["basic"] = {
				["name"] = "Testjewel",
				["realm"] = "Azjol-Nerub",
				["class"] = "Priest",
				["level"] = 90,
			},
			["professions"] = {
				{
					["name"] = "Jewelcrafting",
					["skillLevel"] = 90,
					["maxSkillLevel"] = 100,
					["concentration"] = 1000,
					["concentrationMax"] = 1000,
					["knowledgePoints"] = 30,
					["recipesKnown"] = 200,
					["equipment"] = {},
					["specializations"] = {},
				}, -- [1]
			},
		},
	},
	["profileKeys"] = {
		["Testalch - Area 52"] = "Default",
	},
	["profiles"] = {
		["Default"] = {
			["autoSave"] = true,
		},
	},
}
