"""Source-quality lists for web evidence. Data only; the policy that uses them lives in search.filter_hits.

Three tiers, because this tool covers ~9,500 markets across every industry and no list can name every
trade publication:
  TRUSTED   — real news / trade press / institutions. Their hits get a relaxed topical gate (see
              search.filter_hits) because the source itself already vouches for "this is reporting".
  JUNK      — hosts that never yield a citable market fact (stock-quote and earnings-transcript sites,
              code/note/game/search-result pages, content farms, more rival research-report mills).
              Dropped outright; merged into search._BLOCKED_HOSTS.
  anything else — unknown host: still allowed, but must pass the strict topical gate as before.
"""

# Matched as the host itself or any subdomain of it.
TRUSTED_HOSTS = frozenset({
    # wires and major general / business press
    "reuters.com", "apnews.com", "bloomberg.com", "businesswire.com", "prnewswire.com", "globenewswire.com",
    "accesswire.com", "prweb.com", "wsj.com", "ft.com", "cnbc.com", "forbes.com", "fortune.com",
    "businessinsider.com", "economist.com", "barrons.com", "axios.com", "nytimes.com", "washingtonpost.com",
    "theguardian.com", "bbc.com", "bbc.co.uk", "cnn.com", "usatoday.com", "latimes.com", "inc.com",
    "fastcompany.com", "hbr.org", "marketwatch.com", "theglobeandmail.com", "financialpost.com", "afr.com",
    "handelsblatt.com", "lesechos.fr", "nikkei.com", "scmp.com", "straitstimes.com", "channelnewsasia.com",
    "economictimes.indiatimes.com", "livemint.com", "business-standard.com", "financialexpress.com",
    "thehindubusinessline.com", "japantimes.co.jp", "koreaherald.com", "thenationalnews.com", "arabnews.com",
    "gulfnews.com", "smh.com.au",
    # finance, wealth, banking, insurance, fintech trade press
    "finextra.com", "fintechfutures.com", "fintech.global", "fintechnews.ch", "pymnts.com", "americanbanker.com",
    "bankingdive.com", "paymentsdive.com", "investmentnews.com", "wealthmanagement.com", "financial-planning.com",
    "citywire.com", "thinkadvisor.com", "advisorhub.com", "wealthbriefing.com", "ignites.com",
    "institutionalinvestor.com", "pionline.com", "riabiz.com", "hubbis.com", "ibsintelligence.com",
    "morningstar.com", "insurancejournal.com", "insurancetimes.co.uk", "carriermanagement.com",
    "coindesk.com", "theblock.co", "bankrate.com", "thebanker.com", "risk.net", "ai-cio.com",
    # technology, software, cybersecurity, telecom, semiconductors
    "techcrunch.com", "venturebeat.com", "wired.com", "zdnet.com", "computerworld.com", "cio.com", "infoworld.com",
    "techtarget.com", "siliconangle.com", "theregister.com", "arstechnica.com", "thenextweb.com", "crn.com",
    "darkreading.com", "securityweek.com", "bleepingcomputer.com", "csoonline.com", "helpnetsecurity.com",
    "lightreading.com", "fiercewireless.com", "fiercetelecom.com", "telecoms.com", "rcrwireless.com",
    "eetimes.com", "semiengineering.com", "semiconductor-today.com", "datacenterdynamics.com", "unite.ai",
    "crunchbase.com", "news.crunchbase.com", "sdtimes.com", "computerweekly.com", "itpro.com", "tomshardware.com",
    # marketing, advertising, media, retail, e-commerce
    "martechseries.com", "martech.org", "cmswire.com", "adweek.com", "adage.com", "digiday.com",
    "marketingdive.com", "marketingland.com", "demandgenreport.com", "mediapost.com", "campaignlive.com",
    "searchengineland.com", "thedrum.com", "prweek.com", "martechcube.com", "retaildive.com", "modernretail.co",
    "grocerydive.com", "chainstoreage.com", "retailwire.com", "digitalcommerce360.com", "skift.com",
    "travelweekly.com", "restaurantdive.com", "hospitalitynet.org", "lbbonline.com",
    # healthcare, pharma, biotech, medtech
    "fiercebiotech.com", "fiercepharma.com", "fiercehealthcare.com", "statnews.com", "biopharmadive.com",
    "healthcaredive.com", "medtechdive.com", "endpts.com", "pharmaceutical-technology.com", "pharmatimes.com",
    "mobihealthnews.com", "healthcareitnews.com", "beckershospitalreview.com", "modernhealthcare.com",
    "medicaldevice-network.com", "massdevice.com", "drugdiscoverytrends.com", "genengnews.com", "nature.com",
    "science.org", "thelancet.com", "nejm.org", "medscape.com", "pharmaphorum.com", "europeanpharmaceuticalreview.com",
    "medicaldesignandoutsourcing.com", "labiotech.eu", "bioworld.com", "contractpharma.com",
    # industrial, chemicals, materials, energy, utilities, construction, transport, aerospace, auto
    "chemanager-online.com", "chemicalweek.com", "icis.com", "chemistryworld.com", "cen.acs.org", "plasticstoday.com",
    "packagingdive.com", "supplychaindive.com", "freightwaves.com", "manufacturingdive.com", "industryweek.com",
    "automationworld.com", "powermag.com", "utilitydive.com", "energy-storage.news", "pv-magazine.com",
    "renewableenergyworld.com", "rechargenews.com", "oilprice.com", "spglobal.com", "argusmedia.com", "mining.com",
    "constructiondive.com", "enr.com", "autonews.com", "electrek.co", "spacenews.com", "aviationweek.com",
    "defensenews.com", "breakingdefense.com", "flightglobal.com", "waterworld.com", "chemengonline.com",
    "paintsquare.com", "coatingsworld.com", "european-coatings.com", "metalminer.com", "steelorbis.com",
    "agriculture.com", "world-grain.com", "feednavigator.com", "foodnavigator.com", "foodnavigator-usa.com",
    "fooddive.com", "beveragedaily.com", "cosmeticsdesign.com", "cosmeticsdesign-europe.com", "just-food.com",
    "thefabricator.com", "assemblymag.com", "ttnews.com", "logisticsmgmt.com", "joc.com", "tradewindsnews.com",
    # consulting, analysts, standards bodies, governments, multilaterals (primary / institutional sources)
    "gartner.com", "forrester.com", "idc.com", "mckinsey.com", "deloitte.com", "pwc.com", "kpmg.com", "ey.com",
    "bcg.com", "bain.com", "accenture.com", "capgemini.com", "weforum.org", "oecd.org", "worldbank.org", "imf.org",
    "iea.org", "who.int", "europa.eu", "sec.gov", "fda.gov", "ftc.gov", "federalreserve.gov", "census.gov", "bls.gov",
    "nih.gov", "cdc.gov", "energy.gov", "epa.gov", "nist.gov", "bis.org", "ecb.europa.eu", "gov.uk",
})

# A whole domain family, matched by suffix: every Industry Dive title, every .gov site.
TRUSTED_SUFFIXES = ("dive.com", ".gov")

# Hosts that never carry a citable market fact. Merged into search._BLOCKED_HOSTS.
JUNK_HOSTS = frozenset({
    # stock-quote / earnings / retail-investor commentary
    "seekingalpha.com", "gurufocus.com", "marketscreener.com", "tradingview.com", "wallstreet-online.de",
    "news.futunn.com", "futunn.com", "ainvest.com", "investorideas.com", "financialcontent.com", "zacks.com",
    "fool.com", "benzinga.com", "stocktwits.com", "simplywall.st", "stockanalysis.com", "tipranks.com",
    "investing.com", "moneycontrol.com", "finviz.com", "barchart.com",
    # crypto exchanges' own blogs / scorecards
    "kucoin.com", "bitcoinist.com", "htx.com", "binance.com",
    # code, notes, games, search-result and support pages
    "github.com", "gitlab.com", "note.com", "poki.com", "bing.com", "r.search.yahoo.com", "search.yahoo.com",
    "support.google.com", "google.com", "sports.yahoo.com", "medium.com", "substack.com", "scribd.com",
    "slideshare.net", "academia.edu", "researchgate.net", "sciencedirect.com", "statista.com",
    "journals.sagepub.com", "browsercat.com", "wikihow.com", "indeed.com", "glassdoor.com", "ziprecruiter.com",
    # content farms, paid-placement and syndicated PR reposts
    "thetechedvocate.org", "tycoonstory.com", "debuglies.com", "pr.newsaegis.com", "pr.cullmantimes.com",
    "bastillepost.com", "builtin.com", "influencermarketinghub.com", "analyticsinsight.net",
    # rival research / report-ad mills not already in search._BLOCKED_HOSTS
    "verifiedmarketreports.com", "growthmarketreports.com", "market.us", "dataintelo.com", "qyresearch.com",
    "industryresearch.biz", "usdanalytics.com", "datahorizzonresearch.com", "markettrendsanalysis.com",
    "marketreportsworld.com", "marketsandata.com", "reportocean.com", "cognitivemarketresearch.com",
    "360researchreports.com", "wiseguyreports.com", "gminsights.com", "mrfr.com", "kbvresearch.com",
    "prophecymarketinsights.com", "acumenresearchandconsulting.com", "meticulousresearch.com",
    "vantagemarketresearch.com", "straitsresearch.com", "market-research-reports.biz",
})


# ---- expansion: more outlets, by vertical and region (a wrong/dead domain here is harmless — it simply
# never matches; trusted status only relaxes the topical gate, SELECT still judges every hit). ----
TRUSTED_HOSTS = TRUSTED_HOSTS | frozenset({
    # fintech / wealth / banking / payments / markets trade press (global)
    "pulse2.com", "crowdfundinsider.com", "thefinancialtechnologyreport.com", "fintechmagazine.com",
    "thepaypers.com", "ffnews.com", "wealthprofessional.ca", "thewealthmosaic.com", "financemagnates.com",
    "marketsmedia.com", "theasianbanker.com", "internationalfinance.com", "moneymanagement.com.au",
    "tradersmagazine.com", "finovate.com", "familywealthreport.com", "wealthsolutionsreport.com",
    "enterpriseam.com", "globalbankingandfinance.com", "privsource.com", "cerulli.com", "celent.com",
    "marshberry.com", "thedailyupside.com", "pehub.com", "financefeeds.com", "fxnewsgroup.com", "dwealth.news",
    "fintechoutlook.com", "financialnews.com", "dealbook.nytimes.com", "efinancialcareers.com", "thetradenews.com",
    "waterstechnology.com", "bankingtech.com", "finextra.com", "bobsguide.com", "paymentsjournal.com",
    "paymentsdive.com", "nacha.org", "bankinghub.eu", "retailbankerinternational.com", "bankautomationnews.com",
    "creditunions.com", "cutimes.com", "bankdirector.com", "bankrate.com", "thinkadvisor.com", "napa-net.org",
    "etftrends.com", "etf.com", "fundfire.com", "ai-cio.com",
    "alternativeswatch.com", "privateequitywire.co.uk", "peinsights.com", "buyoutsinsider.com",
    "realdeals.eu.com", "therealdeal.com", "globest.com", "bisnow.com", "commercialobserver.com", "housingwire.com",
    "nationalmortgagenews.com", "mpamag.com", "inman.com", "propertyweek.com", "costar.com",
    "insurancebusinessmag.com", "reinsurancene.ws", "artemis.bm", "insurancenewsnet.com", "propertycasualty360.com",
    "benefitnews.com", "plansponsor.com", "planadviser.com", "emergingmarketsinvestors.com", "ftadviser.com",
    "professionaladviser.com", "investmentweek.co.uk", "portfolio-adviser.com", "fundweb.co.uk", "ft.com",
    # technology / software / AI / cloud / data / security / devices
    "techradar.com", "geekwire.com", "techrepublic.com", "eweek.com", "aithority.com", "techtimes.com",
    "calcalistech.com", "pcquest.com", "cxotoday.com", "inc42.com", "yourstory.com", "entrackr.com",
    "techinasia.com", "e27.co", "dealstreetasia.com", "kr-asia.com", "36kr.com", "tech.eu", "sifted.eu",
    "maddyness.com", "techmeme.com", "engadget.com", "theverge.com", "gizmodo.com", "cnet.com", "pcmag.com",
    "9to5mac.com", "9to5google.com", "androidauthority.com", "xda-developers.com", "digitaltrends.com",
    "protocol.com", "thenewstack.io", "devops.com", "containerjournal.com", "datanami.com", "enterpriseai.news",
    "hpcwire.com", "nextplatform.com", "servethehome.com", "storagenewsletter.com", "blocksandfiles.com",
    "channelfutures.com", "channele2e.com", "msspalert.com", "sdxcentral.com", "networkworld.com",
    "esecurityplanet.com", "scmagazine.com", "infosecurity-magazine.com", "thehackernews.com", "securityboulevard.com",
    "therecord.media", "cyberscoop.com", "nextgov.com", "fedscoop.com", "govtech.com", "statescoop.com",
    "gcn.com", "federalnewsnetwork.com", "defenseone.com", "c4isrnet.com", "militaryaerospace.com",
    "insideunmannedsystems.com", "dronelife.com", "dronedj.com", "therobotreport.com", "roboticsbusinessreview.com",
    "robotics247.com", "automationworld.com", "controleng.com", "plantengineering.com", "designnews.com",
    "engineering.com", "machinedesign.com", "electronicdesign.com", "edn.com", "embedded.com", "allaboutcircuits.com",
    "electronicsweekly.com", "newelectronics.co.uk", "digitimes.com", "trendforce.com", "tomshardware.com",
    "3dprintingindustry.com", "3dprint.com", "voxelmatters.com", "additivemanufacturing.media", "quantumcomputingreport.com",
    "thequantuminsider.com", "venturebeat.com", "artificialintelligence-news.com", "marktechpost.com",
    "saastr.com", "saasworthy.com", "computerweekly.com", "silicon.co.uk", "channelweb.co.uk", "itnews.com.au",
    "zdnet.com", "cio.com.au", "arnnet.com.au", "itbrief.com.au", "itbrief.co.nz", "techwireasia.com",
    "digitalcommerce360.com", "ecommercebytes.com", "internetretailing.net", "retailtouchpoints.com",
    "nrf.com", "chainstoreage.com", "supermarketnews.com", "progressivegrocer.com", "thegrocer.co.uk",
    "talkingretail.com", "retailgazette.co.uk", "drapersonline.com", "fashionunited.com", "voguebusiness.com",
    "businessoffashion.com", "wwd.com", "sourcingjournal.com", "just-style.com", "apparelresources.com",
    "fibre2fashion.com", "textileworld.com", "textileexcellence.com", "ecotextile.com", "jewellermagazine.com",
    "nationaljeweler.com", "professionaljeweller.com", "luxurydaily.com", "luxurysociety.com", "gcimagazine.com",
    "premiumbeautynews.com", "happi.com", "cosmeticsbusiness.com", "beautypackaging.com", "thegrocer.co.uk",
    # marketing / advertising / media / PR / CX
    "chiefmartec.com", "contentgrip.com", "destinationcrm.com", "customerthink.com", "smartinsights.com",
    "thecmo.com", "exchange4media.com", "afaqs.com", "campaignindia.in", "campaignasia.com", "mediabrief.com",
    "marketingweek.com", "marketing-interactive.com", "brandchannel.com", "adexchanger.com", "admonsters.com",
    "ppcland.com", "socialmediatoday.com", "socialmediaexaminer.com", "sproutsocial.com", "emarketer.com",
    "marketingcharts.com", "mmaglobal.com", "iab.com", "ana.net", "cmo.com.au", "mumbrella.com.au",
    "adnews.com.au", "bandt.com.au", "thedrum.com", "ragan.com", "prnewser.com", "holmesreport.com",
    "prweek.com", "mediaweek.com", "tvtechnology.com", "tvnewscheck.com", "broadcastingcable.com", "deadline.com",
    "variety.com", "hollywoodreporter.com", "screendaily.com", "tbivision.com", "advanced-television.com",
    "radioworld.com", "sportico.com", "sportsbusinessjournal.com", "sportspromedia.com", "insideworldfootball.com",
    "esportsinsider.com", "gamesindustry.biz", "gamedeveloper.com", "pocketgamer.biz", "venturebeat.com",
    "legalweek.com", "law.com", "jdsupra.com", "abajournal.com", "lawfaremedia.org", "reuters.com",
    "salesforceben.com", "msdynamicsworld.com", "diginomica.com", "cmswire.com", "consumergoods.com",
    # healthcare / life sciences / medtech / pharma / diagnostics / animal health
    "fiercebiotech.com", "fiercepharma.com", "fiercehealthcare.com", "fiercemedtech.com", "fiercewireless.com",
    "healthcarefinancenews.com", "healthleadersmedia.com", "managedhealthcareexecutive.com", "ajmc.com",
    "formularywatch.com", "pharmexec.com", "pharmavoice.com", "pharmaceuticalcommerce.com", "pharmalive.com",
    "biospace.com", "bioprocessonline.com", "bioprocessintl.com", "labmanager.com", "labroots.com",
    "technologynetworks.com", "drugtargetreview.com", "clinicaltrialsarena.com", "pharmaceutical-journal.com",
    "chemistryworld.com", "outsourcing-pharma.com", "pharmtech.com", "cphi.com", "evaluate.com", "scrip.pharmaintelligence.informa.com",
    "pink.pharmaintelligence.informa.com", "medicalmarketing.com", "mmm-online.com", "healthcareitnews.com",
    "mobihealthnews.com", "medcitynews.com", "histalk2.com", "hitconsultant.net", "digitalhealth.net",
    "healthtechmagazine.net", "healthcaredive.com", "medtechdive.com", "medicaldevice-network.com",
    "medicaldesignbriefs.com", "mddionline.com", "orthospinenews.com", "orthopedicsthisweek.com",
    "cardiovascularbusiness.com", "radiologybusiness.com", "auntminnie.com", "dotmed.com", "hospitalimpact.com",
    "ophthalmologytimes.com", "dentistryiq.com", "dentaltown.com", "drbicuspid.com", "veterinarypracticenews.com",
    "dvm360.com", "petfoodindustry.com", "petfoodprocessing.net", "petfoodnews.org", "avma.org", "feedstuffs.com",
    "thepigsite.com", "thepoultrysite.com", "dairyreporter.com", "cannabisbusinesstimes.com", "mjbizdaily.com",
    "greenentrepreneur.com", "hempindustrydaily.com", "newfrontierdata.com", "nutraingredients.com",
    "nutraingredients-usa.com", "nutritionaloutlook.com", "naturalproductsinsider.com", "supplysideshow.com",
    "fiercehealthcare.com", "statnews.com", "medscape.com", "healio.com", "medpagetoday.com", "mdedge.com",
    "centerwatch.com", "appliedclinicaltrialsonline.com", "firstwordpharma.com", "biopharmcatalyst.com",
    "biocentury.com", "bioworld.com", "genengnews.com", "xconomy.com", "endpts.com", "pharmaphorum.com",
    # chemicals / materials / mining / metals / energy / oil & gas / utilities / environment
    "chemweek.com", "chemicalprocessing.com", "chemicalonline.com", "chemanager-online.com", "specchemonline.com",
    "adhesivesmag.com", "paint.org", "coatingsworld.com", "pcimag.com", "specialtychemicals.eu", "icis.com",
    "chemicals-technology.com", "chemlinked.com", "chemanalyst.com", "plasticsnews.com", "plasticstoday.com",
    "polymerupdate.com", "rubbernews.com", "tirebusiness.com", "tiretechnologyinternational.com", "composites.media",
    "compositesworld.com", "metalbulletin.com", "fastmarkets.com", "kitco.com", "mining.com", "miningweekly.com",
    "mining-technology.com", "northernminer.com", "australianmining.com.au", "mining-journal.com", "steelorbis.com",
    "worldsteel.org", "recyclingtoday.com", "wastedive.com", "waste360.com", "wastemanagementreview.com",
    "waterworld.com", "wateronline.com", "smartwatermagazine.com", "esemag.com", "environmentalleader.com",
    "greenbiz.com", "edie.net", "sustainablebrands.com", "carbonbrief.org", "climatechangenews.com", "e360.yale.edu",
    "oilandgasjournal.com", "ogj.com", "worldoil.com", "rigzone.com", "offshore-technology.com", "offshore-mag.com",
    "upstreamonline.com", "hartenergy.com", "naturalgasintel.com", "lngprime.com", "gasworld.com", "hydrocarbonprocessing.com",
    "oilprice.com", "energyvoice.com", "energy-pedia.com", "platts.com", "spglobal.com", "woodmac.com", "rystadenergy.com",
    "iea.org", "irena.org", "utilitydive.com", "tdworld.com", "powerengineeringint.com", "power-technology.com",
    "renewablesnow.com", "pv-tech.org", "pv-magazine.com", "solarpowerworldonline.com", "solarpowereurope.org",
    "windpowermonthly.com", "windpowerengineering.com", "offshorewind.biz", "reneweconomy.com.au", "cleantechnica.com",
    "electrek.co", "greencarreports.com", "insideevs.com", "evtalk.co.uk", "chargedevs.com",
    "batterytechonline.com", "batteriesnews.com", "energy-storage.news", "h2-view.com", "hydrogeninsight.com",
    "fuelcellsworks.com", "world-nuclear-news.org", "neimagazine.com", "ans.org", "nuclearnewswire.ans.org",
    "geothermal-energy.com", "thinkgeoenergy.com", "biomassmagazine.com", "ethanolproducer.com", "biodieselmagazine.com",
    "renewableenergyworld.com", "rechargenews.com", "tradewindsnews.com", "lloydslist.com", "splash247.com",
    "seatrade-maritime.com", "maritime-executive.com", "gcaptain.com", "ship-technology.com", "safety4sea.com",
    "marinelink.com", "theloadstar.com", "freightwaves.com", "joc.com", "ttnews.com", "ccjdigital.com", "fleetowner.com",
    "trucknews.com", "logisticsmanagement.com", "dcvelocity.com", "scmr.com", "supplychain247.com", "inboundlogistics.com",
    "logistics-manager.com", "lloydsloadinglist.com", "railwayage.com", "railjournal.com", "railway-technology.com",
    "railfreight.com", "intelligenttransport.com", "aviationweek.com", "flightglobal.com", "aviationtoday.com",
    "aerotime.aero", "simpleflying.com", "airwaysmag.com", "ch-aviation.com", "routesonline.com", "airlinegeeks.com",
    "aero-mag.com", "aerospacetestinginternational.com", "airforce-technology.com", "army-technology.com",
    "naval-technology.com", "defense-aerospace.com", "janes.com", "militarytimes.com", "spacenews.com",
    "space.com", "payloadspace.com", "satellitetoday.com", "advanced-television.com", "autonews.com", "autonewseurope.com",
    "automotive-iq.com", "just-auto.com", "automotivelogistics.media", "autocar.co.uk", "motortrend.com", "caranddriver.com",
    "carscoops.com", "autoblog.com", "thedrive.com", "wardsauto.com", "autobodynews.com", "cbtnews.com", "autoevolution.com",
    "teslarati.com", "motorcycle.com", "cycleworld.com", "bikebiz.com", "bicycleretailer.com", "bikeradar.com",
    "constructiondive.com", "enr.com", "constructionweekonline.com", "building.co.uk", "constructionnews.co.uk",
    "builderonline.com", "forconstructionpros.com", "equipmentworld.com", "khl.com", "international-construction.com",
    "cemnet.com", "worldcement.com", "globalcement.com", "concreteproducts.com", "architectmagazine.com", "archdaily.com",
    "dezeen.com", "designboom.com", "architecturaldigest.com", "furninfo.com", "furnituretoday.com", "woodworkingnetwork.com",
    "hardwoodmarketreport.com", "lumbermarketsreport.com", "pulpandpapernews.com", "paperage.com", "packagingdigest.com",
    "packagingstrategies.com", "packworld.com", "packagingeurope.com", "packaging-gateway.com", "packagingnews.com.au",
    "labelsandlabeling.com", "print-business.com", "printweek.com", "whattheythink.com", "glassonweb.com", "glassinternational.com",
    "ceramicindustry.com", "ceramicworldweb.it", "tilebusiness.org", "homebuildingmag.com", "hvacrbusiness.com", "achrnews.com",
    "contractingbusiness.com", "plumbingandmechanical.com", "ecmweb.com", "tdworld.com", "electricalcontractor.org",
    "fireengineering.com", "securityinfowatch.com", "securitymagazine.com", "asmag.com", "ifsecglobal.com", "ifpinternational.com",
    # food / beverage / agriculture / consumer goods
    "fooddive.com", "foodbusinessnews.net", "foodengineeringmag.com", "foodprocessing.com", "foodmanufacturing.com",
    "foodnavigator-asia.com", "foodnavigator-latam.com", "foodingredientsfirst.com", "foodtechnology.com", "foodsafetynews.com",
    "foodqualitynews.com", "bakeryandsnacks.com", "confectionerynews.com", "dairyfoods.com", "meatpoultry.com",
    "thepoultrysite.com", "seafoodsource.com", "undercurrentnews.com", "intrafish.com", "fishfarmingexpert.com",
    "thefishsite.com", "aquaculturealliance.org", "worldgrain.com", "agweb.com", "agriculture.com", "farmprogress.com",
    "farmjournal.com", "farmweek.com", "agfundernews.com", "agri-pulse.com", "agprofessional.com", "croplife.com",
    "agribusinessglobal.com", "fruitnet.com", "producebusiness.com", "thepacker.com", "freshplaza.com", "hortidaily.com",
    "gfpmag.com", "greenhousegrower.com", "urbanagnews.com", "beveragedaily.com", "beveragedaily.com", "foodbev.com",
    "just-drinks.com", "thespiritsbusiness.com", "thedrinksbusiness.com", "decanter.com", "winespectator.com",
    "wine-searcher.com", "brewersassociation.org", "brewbound.com", "beveragedynamics.com", "bevindustry.com",
    "coffeereview.com", "dailycoffeenews.com", "teaandcoffee.net", "restaurantbusinessonline.com", "nrn.com",
    "qsrmagazine.com", "pizzamarketplace.com", "foodserviceandhospitality.com", "foodservicedirector.com", "thecaterer.com",
    "hospitalitynet.org", "hotelmanagement.net", "hoteldive.com", "hotelnewsnow.com", "hospitalitytech.com", "phocuswire.com",
    "skift.com", "travelweekly.com", "travelpulse.com", "travelagentcentral.com", "ttgmedia.com", "ttgasia.com", "traveldailynews.com",
    "cruiseindustrynews.com", "cruisecritic.com", "seatrade-cruise.com", "tourismreview.com", "eturbonews.com", "hospitalityupgrade.com",
    "casinoreports.com", "igamingbusiness.com", "egamingreview.com", "sbcnews.co.uk", "casino.org", "gamblinginsider.com",
    "focusgn.com", "europeangaming.eu", "legalsportsreport.com", "covers.com", "gamingamerica.com", "agb.org",
    "toynews-online.biz", "toybook.com", "licensemag.com", "licenseglobal.com", "gamesindustry.biz", "sportsbusinessjournal.com",
    "sgbonline.com", "sportsmanagement.com", "fitnessbusiness.com", "athleticbusiness.com", "clubindustry.com", "ihrsa.org",
    "educationdive.com", "k12dive.com", "highereddive.com", "edsurge.com", "insidehighered.com", "chronicle.com",
    "universitybusiness.com", "thepienews.com", "eschoolnews.com", "edweek.org", "hechingerreport.org", "the74million.org",
    "campustechnology.com", "thejournal.com", "edtechmagazine.com", "hrdive.com", "shrm.org", "hrexecutive.com", "hrmorning.com",
    "workforce.com", "peoplemanagement.co.uk", "hrgrapevine.com", "talentculture.com", "ere.net", "recruiter.com",
    "staffingindustry.com", "ceoworld.biz", "chiefexecutive.net", "cfo.com", "cfodive.com", "treasuryandrisk.com",
    "accountingtoday.com", "accountancyage.com", "journalofaccountancy.com", "cpapracticeadvisor.com", "gtreview.com",
    "supplychaindive.com", "procurementmag.com", "procurementleaders.com", "spendmatters.com", "sourcingjournal.com",
    "realestatenews.com", "rebusinessonline.com", "multihousingnews.com", "connectcre.com", "commercialsearch.com",
    "multifamilydive.com", "propmodo.com", "unlockmlstech.com", "facilitiesnet.com", "buildings.com", "ifma.org",
    # telecom / networking / media tech / infra
    "lightreading.com", "fiercetelecom.com", "telecomtv.com", "telecompaper.com", "telecomlead.com", "telecomreseller.com",
    "mobileworldlive.com", "mobilebusinessinsights.com", "5gtechnologyworld.com", "rcrwireless.com", "telecoms.com",
    "capacitymedia.com", "datacenterknowledge.com", "datacenterdynamics.com", "datacenterfrontier.com", "convergedigest.com",
    "fibre-systems.com", "ispreview.co.uk", "broadbandtvnews.com", "advanced-television.com", "tvtech.com",
    "satellitetoday.com", "spaceflightnow.com", "nasaspaceflight.com", "orbitaltoday.com", "gpsworld.com", "insidegnss.com",
    "geospatialworld.net", "gim-international.com", "directionsmag.com", "geoconnexion.com", "gisuser.com",
    "iotworldtoday.com", "iot-now.com", "iotbusinessnews.com", "iotforall.com", "iotinsider.com", "embeddedcomputing.com",
    "smartcitiesworld.net", "smartcitiesdive.com", "intelligentcio.com", "intelligentcommunitytechnologies.com",
    "wearable-technologies.com", "wearabletechnologies.com", "voicebot.ai", "mobilesyrup.com", "mobileidworld.com",
    "biometricupdate.com", "securityweek.com", "identityweek.net", "avinteractive.com", "avnetwork.com", "sourcesecurity.com",
    # regional business press (Asia, MENA, Africa, LatAm, Europe, Oceania, Canada)
    "zawya.com", "gulfbusiness.com", "arabianbusiness.com", "agbi.com", "thenationalnews.com", "khaleejtimes.com",
    "gulf-times.com", "arabtimesonline.com", "timesofisrael.com", "jpost.com", "haaretz.com", "globes.co.il", "calcalist.co.il",
    "businessday.ng", "thisdaylive.com", "nairametrics.com", "techcabal.com", "techpoint.africa", "disruptafrica.com",
    "iafrica.com", "moneyweb.co.za", "businesslive.co.za", "fin24.com", "dailymaverick.co.za", "theeastafrican.co.ke",
    "businessdailyafrica.com", "africanbusinessmagazine.com", "theafricareport.com", "ventureburn.com", "weetracker.com",
    "latamlist.com", "bnamericas.com", "mexicobusiness.news", "mexiconewsdaily.com", "riotimesonline.com", "valor.globo.com",
    "americaeconomia.com", "buenosairesherald.com", "batimes.com.ar", "infobae.com", "elfinanciero.com.mx", "eleconomista.com.mx",
    "expansion.mx", "portafolio.co", "larepublica.co", "elcomercio.pe", "gestion.pe", "df.cl", "latinfinance.com", "contxto.com",
    "livemint.com", "business-standard.com", "financialexpress.com", "thehindubusinessline.com", "thehindu.com", "indianexpress.com",
    "hindustantimes.com", "timesofindia.indiatimes.com", "economictimes.indiatimes.com", "ndtvprofit.com",
    "businesstoday.in", "outlookbusiness.com", "forbesindia.com", "fortuneindia.com", "bwbusinessworld.com", "bwdisrupt.com",
    "cnbctv18.com", "entrepreneur.com", "thehindubusinessline.com", "telecomlead.com", "elets.in", "etretail.com",
    "bfsi.economictimes.indiatimes.com", "ciol.com", "expresscomputer.in", "analyticsindiamag.com", "medianama.com",
    "thenewsminute.com", "scroll.in", "theprint.in", "firstpost.com", "deccanherald.com", "tribuneindia.com",
    "bangkokpost.com", "nationthailand.com", "thaipbsworld.com", "vietnamnews.vn", "vir.com.vn", "vietnamplus.vn",
    "thejakartapost.com", "jakartaglobe.id", "kontan.co.id", "philstar.com", "bworldonline.com", "inquirer.net",
    "manilatimes.net", "businessmirror.com.ph", "thestar.com.my", "theedgemalaysia.com", "theedgesingapore.com",
    "businesstimes.com.sg", "straitstimes.com", "todayonline.com", "channelnewsasia.com", "cna.asia", "koreatimes.co.kr",
    "koreaherald.com", "koreajoongangdaily.joins.com", "businesskorea.co.kr", "pulsenews.co.kr", "theinvestor.co.kr",
    "japantimes.co.jp", "asia.nikkei.com", "nikkei.com", "japantoday.com", "kyodonews.net", "mainichi.jp", "asahi.com",
    "scmp.com", "hongkongfp.com", "ejinsight.com", "thestandard.com.hk", "chinadaily.com.cn", "globaltimes.cn", "caixinglobal.com",
    "yicaiglobal.com", "sixthtone.com", "technode.com", "pandaily.com", "thewire.in", "dawn.com", "tribune.com.pk", "brecorder.com",
    "thedailystar.net", "tbsnews.net", "dhakatribune.com", "financialexpress.com.bd", "dailymirror.lk", "economynext.com",
    "kathmandupost.com", "theborneopost.com", "gulfnews.com", "arabnews.com", "saudigazette.com.sa", "zawya.com", "ameinfo.com",
    "wamda.com", "magnitt.com", "egyptindependent.com", "dailynewsegypt.com", "ahram.org.eg", "egypttoday.com", "moroccoworldnews.com",
    "thecitizen.co.tz", "monitor.co.ug", "newvision.co.ug", "theexchange.africa", "thecable.ng", "premiumtimesng.com", "punchng.com",
    "vanguardngr.com", "guardian.ng", "nation.africa", "standardmedia.co.ke", "capitalfm.co.ke", "itweb.co.za", "mybroadband.co.za",
    "bdlive.co.za", "iol.co.za", "news24.com", "thenewhumanitarian.org", "euronews.com", "dw.com", "france24.com", "politico.eu",
    "euractiv.com", "euobserver.com", "thelocal.com", "thelocal.de", "thelocal.fr", "thelocal.se", "thelocal.es", "thelocal.it",
    "handelsblatt.com", "manager-magazin.de", "wiwo.de", "faz.net", "sueddeutsche.de", "spiegel.de", "zeit.de", "tagesschau.de",
    "lesechos.fr", "lefigaro.fr", "lemonde.fr", "latribune.fr", "challenges.fr", "ilsole24ore.com", "corriere.it", "repubblica.it", "milanofinanza.it", "elpais.com", "expansion.com", "cincodias.elpais.com",
    "eleconomista.es", "lavanguardia.com", "publico.pt", "jornaldenegocios.pt", "expresso.pt", "nrc.nl", "fd.nl", "volkskrant.nl",
    "tijd.be", "lecho.be", "nzz.ch", "finews.com", "finews.ch", "cash.ch", "swissinfo.ch", "fintechnews.ch", "derstandard.at",
    "diepresse.com", "wienerzeitung.at", "svd.se", "dn.se", "di.se", "breakit.se", "dagensps.se", "e24.no", "dn.no", "finansavisen.no",
    "borsen.dk", "business.dk", "kauppalehti.fi", "hs.fi", "yle.fi", "irishtimes.com", "independent.ie",
    "businesspost.ie", "siliconrepublic.com", "thetimes.co.uk", "telegraph.co.uk", "independent.co.uk", "standard.co.uk",
    "cityam.com", "thisismoney.co.uk", "moneyweek.com", "investorschronicle.co.uk", "proactiveinvestors.com", "sharesmagazine.co.uk",
    "alliancenews.com", "morningstar.co.uk", "ft.com", "economist.com", "newstatesman.com", "spectator.co.uk", "scotsman.com",
    "heraldscotland.com", "walesonline.co.uk", "bbc.co.uk", "itv.com", "skynews.com", "sky.com", "theguardian.com", "metro.co.uk",
    "afr.com", "theaustralian.com.au", "smh.com.au", "theage.com.au", "abc.net.au", "news.com.au", "businessnewsaustralia.com",
    "startupdaily.net", "smartcompany.com.au", "themandarin.com.au", "itnews.com.au", "crn.com.au", "nzherald.co.nz", "stuff.co.nz",
    "rnz.co.nz", "interest.co.nz", "nbr.co.nz", "businessdesk.co.nz", "thestar.com", "theglobeandmail.com", "financialpost.com",
    "nationalpost.com", "cbc.ca", "ctvnews.ca", "globalnews.ca", "bnnbloomberg.ca", "canadianbusiness.com", "betakit.com",
    "canadianmanufacturing.com", "canadianminingjournal.com", "canadiangrocer.com", "newswire.ca", "marketwired.com",
    "cision.com", "prnewswire.ca", "newsfilecorp.com", "accesswire.com", "issuerdirect.com",
    "eurekalert.org", "sciencedaily.com", "phys.org", "newscientist.com", "scientificamerican.com", "technologyreview.com",
    "ieee.org", "spectrum.ieee.org", "acm.org", "nature.com", "science.org", "cell.com", "thelancet.com", "bmj.com", "nejm.org",
    "jamanetwork.com", "who.int", "unicef.org", "undp.org", "unido.org", "unctad.org", "wto.org", "ilo.org", "fao.org", "iea.org",
    "irena.org", "ifc.org", "adb.org", "afdb.org", "iadb.org", "eib.org", "ebrd.com", "europa.eu", "eurostat.eu", "esma.europa.eu",
    "eba.europa.eu", "eiopa.europa.eu", "fca.org.uk", "bankofengland.co.uk", "mas.gov.sg", "rbi.org.in", "sebi.gov.in", "hkma.gov.hk",
    "sfc.hk", "asic.gov.au", "apra.gov.au", "rba.gov.au", "osfi-bsif.gc.ca", "bankofcanada.ca", "finra.org", "nasdaq.com",
    "nyse.com", "cmegroup.com", "lseg.com", "theice.com", "iosco.org", "fsb.org", "bis.org", "imf.org", "worldbank.org",
})

# Whole domain families (by suffix) that are institutional or syndicated news networks.
TRUSTED_SUFFIXES = TRUSTED_SUFFIXES + (".gov.uk", ".gov.au", ".gc.ca", ".europa.eu")

JUNK_HOSTS = JUNK_HOSTS | frozenset({
    # more report-ad mills and low-quality "market insight" farms seen in real logs
    "snsinsider.com", "pdf.marketpublishers.com", "marketpublishers.com", "marketresearchintellect.com",
    "introspectivemarketresearch.com", "intelmarketresearch.com", "strategicmarketresearch.com", "marketsizeandtrends.com",
    "datiqueinsightsmarket.blog", "micromarketinsights.com", "globalinforesearch.com", "imrmarketreports.com", "orbisresearch.com",
    "reportsanddata.com", "resoinsights.com", "citiusresearch.com", "credenceresearch.com", "htfmarketintelligence.com",
    "congruencemarketinsights.com", "datainsightsmarket.com", "marketresearchreports.com", "worldmetrics.org", "gitnux.org",
    "wifitalents.com", "electroiq.com", "konsulteer.com", "editorialge.com", "competitiveintelligencealliance.io",
    "fourweekmba.com", "explodingtopics.com", "macrotrends.net", "whoearns.com", "datanyze.com", "ventureradar.com",
    "marketresearchfuture.com", "coherentmarketinsights.com", "bccresearch.com", "freedoniagroup.com", "frost.com",
    "ibisworld.com", "mintel.com", "euromonitor.com", "globaldata.com", "gii.tw", "kenresearch.com", "qyresearch.com",
    "lucintel.com", "stratviewresearch.com", "openpr.com", "bigmarketresearch.com", "reportprime.com", "reportsnreports.com",
    "researchdive.com", "emergenresearch.com", "alliedmarketresearch.com", "marketdecipher.com", "360iresearch.com",
    "coherentmi.com", "market.biz", "marketwatch-reports.com", "industryarc.com", "chemanalyst-reports.com", "statzon.com",
    "thebrainyinsights.com", "market-prospects.com", "reportsinsights.com", "researchnester.com", "researchnreports.com",
    # vendors' own marketing / docs / app-store / cloud-console sites (never third-party reporting)
    "microsoft.com", "learn.microsoft.com", "apps.microsoft.com", "robo.microsoft.com", "aws.amazon.com", "amazon.com",
    "oracle.com", "ibm.com", "adobe.com", "get.adobe.com", "salesforce.com", "hubspot.com", "academy.hubspot.com",
    "blog.hubspot.com", "community.hubspot.com", "openai.com", "chatgpt.com", "icloud.com", "apple.com", "g2.com",
    "learn.g2.com", "sofi.com", "robinhood.com", "webull.com", "support.temenos.com", "shareholder.broadridge.com",
    "broadridge-ir.com", "marketplace.broadridge.com", "investor.fisglobal.com", "assets.contentstack.io",
    "docs.familiarize.com", "docs.firecrawl.dev", "roboform.com", "remove.bg", "joinmassive.com", "zenscrape.com",
    "prowebscraper.com", "ranktracker.com", "webbycrown.com", "hublead.io",
    # dictionaries / utilities / games / unrelated lifestyle / academic preprint & journal mirrors
    "dictionary.cambridge.org", "timeanddate.com", "simplicable.com", "financesonline.com", "flippa.com", "psychologytoday.com",
    "xnxx.com", "broadwayworld.com", "autoguide.com", "travel.yahoo.com", "autos.yahoo.com", "papers.ssrn.com", "arxiv.org",
    "link.springer.com", "springer.com", "tandfonline.com", "onlinelibrary.wiley.com", "elgaronline.com", "ijrpr.com", "ijsra.net",
    "ijirt.org", "astesj.com", "creditdonkey.com", "investingiq.net", "purelyinvesting.com", "scanx.trade", "tradersunion.com",
    "coinpedia.org", "cribfb.com", "aimarketingcompare.com", "digitaltrainee.com", "spiralytics.com", "newbreedrevenue.com",
    "foundationinc.co", "f2strategy.com", "aidigital.com", "50states.com", "bayelsawatch.com", "kalkinemedia.com", "247wallst.com",
    "newstrail.com", "alleywatch.com", "hotelandcatering.com", "thearabtribune.com", "delawarebusinessnow.com", "yazooherald.net",
    "greenbaypressgazette.com", "krdo.com", "bossierpress.com", "wowktv.com", "pr.valdostadailytimes.com", "news.europawire.eu",
    "prsync.com", "ad-hoc-news.de", "digitaljournal.com", "tbbwmag.com", "wealtharc.com", "wealthcompanyamc.in", "aetrust.com",
    "reliancedigital.in", "scrb.bihar.gov.in", "shs.bihar.gov.in", "digitalindia.gov.in", "aljazeera.com", "ijr.com",
})


# Junk always wins: a domain listed as both is treated as junk.
TRUSTED_HOSTS = TRUSTED_HOSTS - JUNK_HOSTS


def _host_matches(host: str, domains) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def is_trusted(host: str) -> bool:
    host = host.lower()
    return _host_matches(host, TRUSTED_HOSTS) or any(host.endswith(s) for s in TRUSTED_SUFFIXES)


# ---------------------------------------------------------------------------------------------------
# Targeted source groups. The open-web queries rely on whichever sites the engines happen to rank; these
# groups let a run ALSO aim queries at the trade press and regulators that actually cover the market's
# industry (launches, funding, regulation, innovation). One LLM field picks the group(s) per market; the
# hosts are ordered best-first. Bing news accepts "site:a OR site:b" (verified live), but a batch gets
# dominated by one site, so batches stay small (3 hosts).
# ---------------------------------------------------------------------------------------------------
PACKS: dict[str, dict] = {
    "fintech_wealth": {
        "desc": "wealth/asset management, investing platforms, wealthtech, advisory, brokerage, capital markets technology",
        "trade": ["wealthmanagement.com", "investmentnews.com", "finextra.com", "fintechfutures.com", "thinkadvisor.com",
                  "citywire.com", "financial-planning.com", "pymnts.com", "fintech.global", "wealthbriefing.com"],
        "regulators": ["sec.gov", "finra.org", "fca.org.uk", "esma.europa.eu", "federalreserve.gov", "bis.org"],
    },
    "banking_payments": {
        "desc": "banking, lending, payments, cards, core banking, digital banking, credit",
        "trade": ["americanbanker.com", "bankingdive.com", "paymentsdive.com", "finextra.com", "thepaypers.com", "pymnts.com",
                  "ffnews.com", "fintechfutures.com", "bankingtech.com"],
        "regulators": ["federalreserve.gov", "bankofengland.co.uk", "ecb.europa.eu", "bis.org", "mas.gov.sg", "rbi.org.in"],
    },
    "insurance": {
        "desc": "insurance, reinsurance, insurtech, risk",
        "trade": ["insurancejournal.com", "carriermanagement.com", "insurancebusinessmag.com", "artemis.bm",
                  "propertycasualty360.com", "reinsurancene.ws"],
        "regulators": ["eiopa.europa.eu", "naic.org", "fca.org.uk"],
    },
    "enterprise_software_cloud": {
        "desc": "enterprise software, SaaS, cloud, IT services, developer tools, data platforms",
        "trade": ["techcrunch.com", "venturebeat.com", "zdnet.com", "siliconangle.com", "computerworld.com", "infoworld.com",
                  "cio.com", "techtarget.com", "crn.com", "theregister.com"],
        "regulators": ["nist.gov", "ftc.gov", "europa.eu"],
    },
    "ai_data_robotics": {
        "desc": "artificial intelligence, machine learning, analytics, robotics, quantum, 3D printing, emerging deep tech",
        "trade": ["venturebeat.com", "techcrunch.com", "artificialintelligence-news.com", "therobotreport.com", "datanami.com",
                  "thenewstack.io", "marktechpost.com", "3dprintingindustry.com", "thequantuminsider.com"],
        "regulators": ["nist.gov", "europa.eu", "ftc.gov"],
    },
    "cybersecurity_identity": {
        "desc": "cybersecurity, identity, authentication, biometrics, fraud, privacy",
        "trade": ["darkreading.com", "securityweek.com", "bleepingcomputer.com", "csoonline.com", "scmagazine.com",
                  "infosecurity-magazine.com", "cyberscoop.com", "biometricupdate.com"],
        "regulators": ["nist.gov", "cisa.gov", "europa.eu"],
    },
    "marketing_media_adtech": {
        "desc": "marketing technology, advertising, adtech, CRM, customer experience, media, content, PR",
        "trade": ["adweek.com", "adage.com", "digiday.com", "marketingdive.com", "martechseries.com", "martech.org", "cmswire.com",
                  "adexchanger.com", "searchengineland.com", "thedrum.com"],
        "regulators": ["ftc.gov", "europa.eu"],
    },
    "retail_ecommerce_consumer": {
        "desc": "retail, e-commerce, consumer goods, grocery, consumer brands, marketplaces",
        "trade": ["retaildive.com", "modernretail.co", "digitalcommerce360.com", "grocerydive.com", "chainstoreage.com",
                  "retailwire.com", "supermarketnews.com", "retailgazette.co.uk"],
        "regulators": ["ftc.gov", "europa.eu"],
    },
    "healthcare_digital_health": {
        "desc": "healthcare services, hospitals, digital health, health IT, telehealth, payers, care delivery",
        "trade": ["fiercehealthcare.com", "healthcaredive.com", "healthcareitnews.com", "modernhealthcare.com", "mobihealthnews.com",
                  "beckershospitalreview.com", "statnews.com", "medcitynews.com"],
        "regulators": ["fda.gov", "cms.gov", "nih.gov", "who.int", "europa.eu"],
    },
    "pharma_biotech": {
        "desc": "pharmaceuticals, drugs and therapies, biotech, clinical trials, drug delivery, CDMO/CRO",
        "trade": ["fiercepharma.com", "fiercebiotech.com", "biopharmadive.com", "endpts.com", "statnews.com",
                  "pharmaceutical-technology.com", "pharmaphorum.com", "biospace.com", "genengnews.com"],
        "regulators": ["fda.gov", "ema.europa.eu", "who.int", "nih.gov"],
    },
    "medtech_diagnostics": {
        "desc": "medical devices, diagnostics, imaging, surgical, lab equipment, dental, veterinary",
        "trade": ["medtechdive.com", "massdevice.com", "medicaldevice-network.com", "mddionline.com", "dotmed.com",
                  "cardiovascularbusiness.com", "medicaldesignandoutsourcing.com", "dvm360.com"],
        "regulators": ["fda.gov", "ema.europa.eu", "who.int"],
    },
    "chemicals_materials": {
        "desc": "chemicals, specialty chemicals, plastics, polymers, coatings, adhesives, composites, advanced materials",
        "trade": ["icis.com", "chemanager-online.com", "chemweek.com", "chemicalprocessing.com", "chemistryworld.com",
                  "plasticstoday.com", "plasticsnews.com", "compositesworld.com", "coatingsworld.com", "fastmarkets.com"],
        "regulators": ["epa.gov", "echa.europa.eu", "osha.gov"],
    },
    "metals_mining": {
        "desc": "mining, metals, minerals, steel, rare earths, battery materials",
        "trade": ["mining.com", "mining-technology.com", "miningweekly.com", "northernminer.com", "fastmarkets.com",
                  "steelorbis.com", "kitco.com"],
        "regulators": ["usgs.gov", "epa.gov"],
    },
    "energy_power_renewables": {
        "desc": "power generation, utilities, renewables (solar, wind, hydrogen, storage), grid, nuclear, clean energy",
        "trade": ["utilitydive.com", "power-technology.com", "pv-magazine.com", "renewableenergyworld.com", "energy-storage.news",
                  "cleantechnica.com", "windpowermonthly.com", "rechargenews.com", "hydrogeninsight.com", "electrek.co"],
        "regulators": ["energy.gov", "iea.org", "irena.org", "ferc.gov", "epa.gov"],
    },
    "oil_gas": {
        "desc": "oil and gas, LNG, petrochemicals, oilfield services, refining",
        "trade": ["ogj.com", "worldoil.com", "rigzone.com", "offshore-technology.com", "upstreamonline.com", "hartenergy.com",
                  "oilprice.com", "naturalgasintel.com"],
        "regulators": ["energy.gov", "epa.gov", "iea.org"],
    },
    "industrial_manufacturing": {
        "desc": "manufacturing, industrial automation, machinery, tools, process equipment, industrial IoT",
        "trade": ["manufacturingdive.com", "industryweek.com", "automationworld.com", "controleng.com", "plantengineering.com",
                  "designnews.com", "engineering.com", "therobotreport.com"],
        "regulators": ["nist.gov", "osha.gov", "europa.eu"],
    },
    "electronics_semiconductors": {
        "desc": "semiconductors, electronics, sensors, displays, IoT devices, embedded systems, photonics",
        "trade": ["eetimes.com", "semiengineering.com", "electronicdesign.com", "digitimes.com", "trendforce.com", "iot-now.com",
                  "iotworldtoday.com", "embedded.com", "semiconductor-today.com"],
        "regulators": ["nist.gov", "europa.eu"],
    },
    "automotive_mobility": {
        "desc": "automotive, electric vehicles, batteries, autonomous driving, auto parts, mobility services, two-wheelers",
        "trade": ["autonews.com", "just-auto.com", "electrek.co", "insideevs.com", "wardsauto.com", "automotivelogistics.media",
                  "thedrive.com", "batterytechonline.com"],
        "regulators": ["nhtsa.gov", "epa.gov", "europa.eu"],
    },
    "transport_logistics": {
        "desc": "logistics, freight, shipping, ports, trucking, rail, warehousing, supply chain, packaging for transport",
        "trade": ["supplychaindive.com", "freightwaves.com", "joc.com", "theloadstar.com", "logisticsmanagement.com",
                  "dcvelocity.com", "lloydslist.com", "gcaptain.com", "railjournal.com", "ttnews.com"],
        "regulators": ["dot.gov", "fmc.gov"],
    },
    "aerospace_defense_space": {
        "desc": "aerospace, aviation, defense, space, satellites, drones, airlines",
        "trade": ["aviationweek.com", "flightglobal.com", "spacenews.com", "defensenews.com", "breakingdefense.com",
                  "airforce-technology.com", "aviationtoday.com", "satellitetoday.com"],
        "regulators": ["faa.gov", "nasa.gov", "defense.gov"],
    },
    "food_beverage_agri": {
        "desc": "food, beverages, ingredients, agriculture, agtech, animal feed, pet food, nutrition and supplements",
        "trade": ["fooddive.com", "foodnavigator.com", "foodnavigator-usa.com", "beveragedaily.com", "agfundernews.com",
                  "world-grain.com", "foodbusinessnews.net", "just-food.com", "farmprogress.com", "nutraingredients.com"],
        "regulators": ["fda.gov", "usda.gov", "efsa.europa.eu", "fao.org"],
    },
    "consumer_beauty_fashion": {
        "desc": "beauty and personal care, cosmetics, fashion, apparel, textiles, luxury, jewelry, household products",
        "trade": ["cosmeticsdesign.com", "premiumbeautynews.com", "happi.com", "businessoffashion.com", "fashionunited.com",
                  "wwd.com", "just-style.com", "sourcingjournal.com", "luxurydaily.com"],
        "regulators": ["fda.gov", "ftc.gov", "europa.eu"],
    },
    "construction_real_estate": {
        "desc": "construction, building materials, real estate, proptech, architecture, HVAC, smart buildings",
        "trade": ["constructiondive.com", "enr.com", "bisnow.com", "commercialobserver.com", "therealdeal.com", "globest.com",
                  "housingwire.com", "builderonline.com", "equipmentworld.com"],
        "regulators": ["hud.gov", "epa.gov"],
    },
    "telecom_infrastructure": {
        "desc": "telecom, 5G, networking, broadband, data centers, satellite communications, cloud infrastructure",
        "trade": ["lightreading.com", "fiercewireless.com", "fiercetelecom.com", "telecomtv.com", "rcrwireless.com",
                  "datacenterdynamics.com", "datacenterknowledge.com", "capacitymedia.com", "mobileworldlive.com"],
        "regulators": ["fcc.gov", "ntia.gov", "itu.int", "europa.eu"],
    },
    "education_workforce": {
        "desc": "education, edtech, training, HR technology, recruiting, staffing, workforce",
        "trade": ["highereddive.com", "k12dive.com", "edsurge.com", "insidehighered.com", "edweek.org", "hrdive.com",
                  "hrexecutive.com", "shrm.org", "staffingindustry.com", "workforce.com"],
        "regulators": ["ed.gov", "dol.gov"],
    },
    "travel_hospitality_gaming": {
        "desc": "travel, hospitality, hotels, restaurants, gaming and gambling, esports, entertainment, sports business",
        "trade": ["skift.com", "phocuswire.com", "hoteldive.com", "travelweekly.com", "restaurantdive.com", "igamingbusiness.com",
                  "gamesindustry.biz", "deadline.com", "variety.com", "sportico.com"],
        "regulators": [],
    },
    "environment_water_waste": {
        "desc": "environmental services, water treatment, waste management, recycling, carbon, sustainability, climate tech",
        "trade": ["wastedive.com", "waste360.com", "waterworld.com", "greenbiz.com", "edie.net", "environmentalleader.com",
                  "recyclingtoday.com", "smartwatermagazine.com", "carbonbrief.org"],
        "regulators": ["epa.gov", "europa.eu"],
    },
    "public_sector_govtech": {
        "desc": "government technology, public safety, smart cities, civic and defense IT",
        "trade": ["govtech.com", "nextgov.com", "fedscoop.com", "statescoop.com", "federalnewsnetwork.com", "defenseone.com"],
        "regulators": ["gsa.gov"],
    },
}

# Always searched, whatever the industry: general business press, and deal/funding coverage.
GENERAL_BUSINESS_HOSTS = ["reuters.com", "bloomberg.com", "cnbc.com", "wsj.com", "ft.com", "forbes.com"]
DEAL_FUNDING_HOSTS = ["news.crunchbase.com", "techcrunch.com", "pehub.com"]

_BATCH = 3  # hosts per site: query; larger batches get dominated by a single site


def pack_menu() -> str:
    return "\n".join(f"  - {pid}: {p['desc']}" for pid, p in PACKS.items())


def _site_query(core: str, hosts: list[str], extra: str = "") -> str:
    sites = " OR ".join(f"site:{h}" for h in hosts)
    return f"{core} {extra}".strip() + f" ({sites})"


def targeted_queries(core: str, pack_ids: list[str]) -> list[str]:
    """Site-restricted queries for a confirmed market's core phrase. `pack_ids` are LLM-chosen, best first;
    unknown ids are ignored. Budget is fixed (<= 9 queries) so run time stays bounded."""
    core = core.strip()
    queries = [
        _site_query(core, GENERAL_BUSINESS_HOSTS[:_BATCH]),
        _site_query(core, GENERAL_BUSINESS_HOSTS[_BATCH:2 * _BATCH]),
        _site_query(core, DEAL_FUNDING_HOSTS, "funding"),
    ]
    ids = [i for i in dict.fromkeys(pack_ids) if i in PACKS][:3]
    for rank, pid in enumerate(ids):
        trade, regs = PACKS[pid]["trade"], PACKS[pid]["regulators"]
        queries.append(_site_query(core, trade[:_BATCH]))
        if rank == 0:
            queries.append(_site_query(core, trade[_BATCH:2 * _BATCH]))
            if regs:
                queries.append(_site_query(core, regs[:_BATCH], "regulation"))
    return queries
