"""The SpaceX compliance walkthrough.

Scores appear only when audits/spacex-jobs/ is fully judged. A partial results.json
does not fill the block. The earlier consumer run stays on disk and is not the page.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from datetime import datetime
from pathlib import Path

from searchaudit import stats
from searchaudit.gap.loss import norm_url
from searchaudit.models import FAILURE_MODES, AuditResults, Run
from searchaudit.report.format import money, secs
from searchaudit.report.model import build_view_model

COLUMNS = ["exa", "parallel", "keen", "select", "grok", "routed"]
ARMS = ("keen", "select", "routed")
LABELS = {
    "exa": "Exa",
    "parallel": "Parallel",
    "keen": "Keenable search",
    "select": "Keenable SELECT",
    "grok": "Grok native",
    "routed": "Keenable router",
}
EMPTY = "Not run yet."
# The written questions in audits/spacex-jobs/prompts.json. Each row is one question.
PLAN = [
    ("amazon-milestone", "Did the FCC give Amazon the 24-month extension?", 1,
     "The extension to 30 July 2028 was granted.", "English", "DA-26-553"),
    ("amazon-systems", "Do Gen2 and Polar share Gen1's milestone dates?", 1,
     "Every Amazon constellation is due 30 July 2026 and 30 July 2029.", "English", "DA-26-553, footnote 8"),
    ("canada-eband", "Did Canada approve Starlink E-band nationwide?", 1,
     "Approved, primary, for the country.", "French", "ISDE decision, April 2026"),
    ("part-100", "Did the 22 July 2026 vote repeal Part 25 the next day?", 1,
     "Part 25 ended when the Commission voted.", "English", "FCC-26-47"),
    ("echostar", "Does SpaceX hold the EchoStar licenses in every US market?", 1,
     "The sale closed, and the licenses are SpaceX's.", "English", "DA-26-471"),
    ("gen2-count", "Did the FCC authorize all 29,988 Gen2 satellites?", 1,
     "The January 2026 order granted the full request.", "English", "DA-26-36"),
    ("gen2-shells", "Are the new Gen2 shells a single altitude and inclination?", 1,
     "One shell, and the old altitudes were cancelled.", "English", "DA-26-36"),
    ("gen2-then-april", "Did the January grant leave Big LEO open outside the US?", 1,
     "Still pending, and 2 GHz inside the US was granted with it.", "English", "DA-26-36 and DA-26-398"),
    ("epfd-rule", "Did the May 2026 order end EPFD limits that day, in every band?", 1,
     "EPFD limits ended on publication, for all satellite bands.", "English", "FCC 26-26"),
    ("datacenter", "Did the FCC license a million-satellite orbital data center?", 1,
     "Granted on 4 February 2026, milestones waived.", "English", "DA-26-113"),
    ("uk-gateways", "Does Starlink now hold permanent UK gateway licences at every site it asked for?", 1,
     "All of them were granted, including E-band, with no end date.", "English", "Ofcom, 2025 and 2026"),
    ("eloy-sta", "Is the Eloy quad-band gateway still running under its July STA?", 1,
     "The 60-day STA is still in force, and Iridium lost outright.", "English", "SES-STA-20260511-01297"),
    ("anatel-2027", "A Anatel deu proteção contra interferência até 2027?", 1,
     "Concedeu proteção plena, sem preço público.", "Portuguese", "Acórdão 91/2025"),
    ("insurance-ceiling", "Must every Falcon launch carry $500 million and $100 million?", 1,
     "Those figures are the minimums, and cover lasts until disposal.", "English", "14 CFR Part 440"),
    ("part100-rewrite", "Must existing licenses be rewritten the day after the Part 100 vote?", 1,
     "Yes. A Part 25 citation died with the vote.", "English", "FCC-26-47"),
    ("scs-lease", "Did the T-Mobile deal give SpaceX its own primary mobile license?", 1,
     "SpaceX is the licensee, on a primary basis, for the EchoStar bands.", "English", "DA-24-1193"),
    ("scs-bands", "Is every mobile band eligible for supplemental coverage from space?", 1,
     "WCS is in, the service is primary, and no lease is required.", "English", "FCC-24-28"),
    ("australia-expiry", "Are both Starlink Australia area licences still in force?", 1,
     "The register says Granted, so both are current.", "English", "ACMA 11240791 and 11240792"),
    ("acma-wband", "Did ACMA license W-band earth stations in Melbourne, Perth, and Sydney?", 1,
     "All three cities, renewable, with interference protection.", "English", "ACMA interim W-band"),
    ("gen2-directions", "Is every Ku range in the January grant a downlink, nationwide?", 1,
     "Ku is downlink only, and the V-band range is authorized inside the United States.", "English", "DA-26-36"),
    ("four-files", "Are the Gen2, direct-to-cell, data-center, and Eloy files one granted call sign?", 1,
     "One constellation, and the million-satellite system was licensed.", "English", "S2992, S00735, S00798, E260362"),
    ("april-others", "Did the April 2026 order dismiss only SpaceX and grant the others?", 1,
     "Kepler, Sateliot, and AST were granted. Only SpaceX was dismissed.", "English", "DA-26-398"),
    ("part-450", "Did Falcon miss the deadline to move onto Part 450?", 1,
     "The deadline passed and Falcon 9, Falcon Heavy, and Dragon are still on the old part.", "English", "FAA, 17 March 2026"),
    ("trai-charge", "Did the Cabinet set one satellite charge for every service?", 1,
     "Five percent of revenue, the same fee for fixed and mobile satellite.", "English", "TRAI, 9 May 2025"),
    ("starlink-trai", "Did TRAI defer direct-to-device on IMT spectrum until after WRC-2027?", 1,
     "TRAI adopted that deferral. Starlink's filing is the decision.", "English", "Starlink India, 13 May 2026"),
    ("arcep-steam", "Did Arcep authorize Starlink to use USASAT-NGSO-3X in France?", 1,
     "France authorized USASAT-NGSO-3X, and the terminals are protected from interference.", "French", "Arcep 2024-2687"),
    ("echostar-pending", "Did the EchoStar assignment also license direct-to-device service?", 1,
     "The May 2026 order licensed the direct-to-device constellation, including 2000-2020 MHz space-to-Earth.", "English", "DA-26-471"),
    ("echostar-qualified", "Did the FCC refuse the EchoStar assignment because of the control allegation?", 1,
     "The assignment was denied because Elon Musk is controlled by the Chinese Communist Party.", "English", "DA-26-471"),
    ("eband-7500", "Did the March 2024 E-band order add satellites and grant the beacons?", 1,
     "DA-24-222 authorized E-band on a new tranche, and granted the beacons the same day.", "English", "DA-24-222"),
    ("shells-and-court", "Did the court strike the 2022 grant, and did 2024 fill the low shells?", 1,
     "The D.C. Circuit struck down the 2022 grant, and November 2024 put all 29,988 satellites in the 340-360 km shells.", "English", "DA-24-1193"),
    ("itu-epfd", "Did FCC 26-26 repeal the ITU EPFD limits outside the United States?", 1,
     "FCC 26-26 repealed Article 22 for Starlink downlinks abroad, and WRC-23 already changed those limits.", "English", "FCC 26-26"),
    ("beacon-450", "Did the January 2026 order put VHF beacons on all 15,000 Gen2 satellites?", 1,
     "All 15,000 Gen2 satellites may carry the VHF beacon, granted on 9 January 2026.", "English", "Beacon order, 20 November 2024"),
    ("oobe-limit", "Did the November 2024 order waive the SCS out-of-band limit to -110.6?", 1,
     "The November 2024 order authorized -110.6 dBW/m2/MHz.", "English", "DA-24-1193 and 7 March 2025"),
    ("ofcom-consult", "Must Ofcom still consult before every Ka gateway, including temporary E-band?", 1,
     "Every Ka gateway is still consulted, and the review abolished consultation for temporary E-band.", "English", "Ofcom, 20 July 2026"),
    ("no-protection", "Are these six Starlink authorizations permanent rights to interference protection?", 1,
     "They are permanent protected grants. France covers USASAT-NGSO-3X, and Melbourne was included.", "English", "Six authorizations"),
    ("mexico-concession", "Does the 30-year Mexican concession, by itself, authorize the spectrum?", 1,
     "The 30-year concession is the spectrum title, and it only covers fixed internet.", "Spanish", "IFT P/IFT/070721/319"),
    ("mod-00022", "Was the March 2024 Big LEO file parked until the rulemaking?", 1,
     "The Bureau parked 00022 until the rulemaking, and it is the T-Mobile SCS file.", "English", "DA-24-300"),
    ("hurricane-sta", "Was the Helene and Milton coverage the commercial SCS license?", 1,
     "Helene and Milton service was the commercial PCS G Block license, and December 2023 was already commercial service.", "English", "DA-24-1193"),
    ("ofcom-twelve", "Did the 12-month build clock start on the day Ofcom decided to grant?", 1,
     "The build clock started on 20 July 2026, and the licences were already in force that day.", "English", "Ofcom, 20 July 2026"),
    ("norway-26", "Did Starlink's April 2024 letter grant it the 26 GHz band in Norway?", 1,
     "Nkom assigned Starlink the 26 GHz band that day, and the Norwegian gateway uses Ku band.", "English", "Starlink to Nkom, 29 April 2024"),
    ("germany-2027", "Is the German Starlink earth-station assignment open-ended and protected?", 1,
     "The assignment is open-ended, 14.25-14.5 GHz is protected against fixed links, and every terminal needs its own assignment.", "German", "BNetzA Vfg 69/2026"),
    ("sa-comment", "Did the June 2026 filing grant Starlink a South African licence?", 1,
     "ICASA licensed Starlink for 10 years that day, and the letter abolished the 30 percent equity rule.", "English", "ICASA, 29 June 2026"),
    ("vband-fold", "Is the separate V-band constellation still authorized beside Gen2?", 1,
     "The separate V-band constellation still sits alongside Gen2, and November 2024 granted the bond and milestone change.", "English", "DA-24-1193"),
    ("vband-uplink", "Does the January 2026 grant still exclude both of those V-band uplinks?", 1,
     "Both uplinks are still refused because of the 2018 order. 50.4-51.4 GHz was granted in 2018.", "English", "FCC-18-161 and DA-26-36"),
    ("chile-thirty", "Does 27.5-28.35 GHz let Starlink put user terminals inside a Chilean city?", 1,
     "That band authorizes user terminals in the city, and the 21 March 2026 journal already granted the new gateway.", "Spanish", "Decreto 5, 9 January 2025"),
    ("mpl-amend", "Is the FAA's first maximum probable loss figure frozen?", 1,
     "The first number cannot be raised, the 90 days run from the application even without the required information, and this section sets a $500 million minimum.", "English", "14 CFR 440.7"),
    ("gen2-2022", "Did December 2022 add 7,500 satellites on top of every existing authorization?", 1,
     "December 2022 added 7,500 satellites on top of every existing authorization, and granted E-band and the beacons that day.", "English", "FCC-22-91"),
    ("one-beam", "Could one Gen2 satellite light two co-frequency beams on the same area?", 1,
     "Each satellite could light two co-frequency beams on the same area, and 580 km was a floor.", "English", "FCC-22-91"),
    ("earth-point", "Did the satellite grant update every existing Starlink earth-station license?", 1,
     "The satellite grant automatically added Gen2 as a point of communication on every earth-station license.", "English", "FCC-22-91"),
    ("arcep-esim", "Did the May 2022 Arcep decision already authorize earth stations in motion?", 1,
     "ESIMs were already in the 25 May 2022 decision, and the 28 March 2023 decision authorizes STEAM-1B.", "French", "Arcep 2023-0680"),
    ("bonaire-fifteen", "Did the RDI grant SpaceX Netherlands a concession for the whole of the Netherlands?", 1,
     "SpaceX Netherlands B.V. received a concession for the whole of the Netherlands in February 2025, and the December 2025 concession went to the same company.", "Dutch", "RDI, 1 December 2025"),
    ("failure-pause", "Does one favorable ITU finding close the Gen2 reporting condition?", 1,
     "Any one favorable ITU finding closes the reporting condition, and launches may continue after the failure threshold is crossed.", "English", "FCC-22-91"),
    ("sta-expired", "Were the March 2026 earth-station actions ordinary licenses still in force in October?", 1,
     "The March 2026 actions were ordinary licenses still running in October, and the two section 1.62 actions expired on 1 May 2026.", "English", "DOC-419216"),
    ("gen2-petitions", "Did DISH stop the December 2022 partial grant?", 1,
     "DISH's opposition stopped the partial grant, and the Commission certified the insurance The Balance Group demanded.", "English", "FCC-22-91"),
    ("nz-28ghz", "Do satellite gateways share 27.5-28.35 GHz inside New Zealand cities with mobile?", 1,
     "Satellite gateways and mobile share that band inside cities without a geographic split, and a new gateway application could be filed on 2 April 2026.", "English", "RSM New Zealand"),
    ("itu-before-service", "Did the December 2022 grant make SpaceX wait for the ITU finding?", 1,
     "SpaceX had to wait for the ITU finding before the operations authorized that day, and the waiver removed the combined-filings report.", "English", "FCC-22-91"),
    ("kenya-landing", "May a foreign satellite operator keep serving Kenya with no landing rights?", 1,
     "A foreign satellite firm already serving Kenyan networks was excused from landing rights, and the 2025 note is Starlink's Kenyan licence.", "English", "Communications Authority of Kenya"),
    ("nigeria-landing", "Is the Nigerian landing permit a licence for 6,000 satellites?", 1,
     "The landing permit is 6,000 satellites through 2032, and the Ka uplink is continuous from 27.5 to 30 GHz.", "English", "Nigerian Communications Commission"),
    ("below-iss", "Did December 2022 authorize the shells below the space station?", 1,
     "The December 2022 order authorized the 19,440 satellites below the ISS and the retrograde shells at 604 and 614 km.", "English", "FCC-22-91"),
    ("beacon-direction", "Did December 2022 authorize the Gen2 tracking beacons?", 1,
     "The December 2022 grant authorized the beacons, and the application put 137-138 MHz on the space-to-Earth link.", "English", "FCC-22-91"),
    ("vandenberg-rod", "Did the October 2025 Vandenberg record issue the license?", 1,
     "The October 2025 record of decision is the launch license for SLC-6, and the FAA preferred the no-action alternative.", "English", "FAA, October 2025"),
    ("nepa-nprm", "Has the FAA notice already waived NEPA for a Falcon license?", 1,
     "Notice 26-11 has already waived NEPA for Falcon licenses, and the notice is a final rule with an effective date.", "English", "FAA-2026-8614"),
    ("vandenberg-100", "Is the Vandenberg cadence 100 Falcon 9 launches from SLC-4?", 1,
     "The decision authorizes 100 Falcon 9 launches a year from SLC-4 and five Falcon Heavy launches in addition, and the number is the FAA launch license.", "English", "FAA, October 2025"),
    ("jalama-4f", "Does the Vandenberg record find no environmental harm?", 1,
     "The record finds no environmental harm, and the Jalama Beach and vegetation measures are already license conditions because the record of decision exists.", "English", "FAA, October 2025"),
    ("waiver-fault", "Can a payload customer keep the right to sue for its own employee?", 1,
     "The customer may sue the licensee for injury to the customer's own employee, the participant is inside the customer's waiver, and the agreements may always be signed on launch day.", "English", "14 CFR 440.17"),
    ("mcmc-class", "Does a Malaysian class assignment mean an earth station needs no paperwork?", 1,
     "A fixed-satellite earth station on a class assignment needs no registration and no fee, and Class Assignment No. 1 of 2025 expired on 20 January 2025.", "English", "MCMC Class Assignment No. 1 of 2025"),
    ("after-policy", "After the policy expires, does the United States still pay only above it?", 1,
     "After the policy expires the government still pays only above the old insurance limit, and participant additional-insured status ended on 30 September 2025.", "English", "51 USC 50914"),
    ("appropriation-cap", "Does the United States automatically pay the next $1.5 billion?", 1,
     "The $1.5 billion is paid automatically once claims exceed the insurance, an experimental permit gets the same payment, and willful misconduct by the licensee is covered.", "English", "51 USC 50915"),
    ("federal-forum", "May a space flight participant sue in state court?", 1,
     "The participant may sue in state court, and subsection (g) is the permit rule as well as the license rule.", "English", "51 USC 50914(g)"),
    ("suborbital-clock", "Is a suborbital flight insured until 30 days after payload separation?", 1,
     "A suborbital flight is insured until 30 days after payload separation, and coverage attaches when the policy is issued.", "English", "14 CFR 440.11"),
    ("party-misconduct", "Does only the licensee's misconduct block a government payment?", 1,
     "Only the licensee's willful misconduct blocks payment, and a usual exclusion is paid even without a certification that the cover is not commercially available.", "English", "14 CFR 440.19"),
    ("sixty-days", "Is every United States property claim inside the $100 million cover?", 1,
     "Every United States property claim is inside the $100 million cover, a non-insurance demonstration is due on the 30-day schedule, and the insurer's certificate without the broker's opinion is enough.", "English", "14 CFR 440.9"),
    ("no-cost", "Does launch insurance protect only the licensee?", 1,
     "The policy names only the licensee, and the customer's contractor pays the United States for this cover.", "English", "51 USC 50914(a)(4)"),
    ("above-property", "Does the government waiver cover everything above the third-party insurance?", 1,
     "The government waiver covers everything above the third-party insurance, and a space flight participant is not a party to the Secretary's waiver.", "English", "51 USC 50914"),
    ("first-flight", "Did the April 2023 Starship license authorize a series of flights?", 1,
     "The April 2023 order authorized repeated flights, and the flight is over at impact or splashdown.", "English", "FAA VOL 23-129"),
    ("cadence-fonsi", "Did the Boca Chica finding license 25 launches a year?", 1,
     "This finding is a license for 25 launches, the pad is authorized for night landings, and the no-action case allows no flights.", "English", "FAA, April 2025"),
    ("beach-hours", "Can Boca Chica beach stay closed for the whole campaign?", 1,
     "The beach can be closed for the whole campaign, summer weekends are ordinary closure days, and an anomaly permanently takes the state parks.", "English", "FAA, April 2025"),
    ("hazard-area", "Did the later airspace study authorize a night landing at the pad?", 1,
     "This study licenses a night landing of Starship at the pad, and the only airspace at issue is over the Gulf.", "English", "FAA tiered EA"),
    ("risk-numbers", "Is everyone near a launch held to one-in-a-million until disposal?", 1,
     "One 1e-6 limit covers the public, neighbors, and aircraft, and an orbital launch is held to these numbers until disposal.", "English", "14 CFR 450.101"),
    ("who-is-public", "Are SpaceX pad technicians neighboring operations personnel?", 1,
     "The launch operator's own pad crew are neighboring operations personnel, and a visitor inside the gate is outside the public.", "English", "14 CFR 401.7"),
    ("mishap-clock", "Can the first notice of a malfunction wait for the five-day report?", 1,
     "Only a death or an injury is a mishap, the first notice of a safety-critical malfunction can wait for the five-day report, and the next flight can launch before the preventive measure is implemented.", "English", "14 CFR 450.173"),
    ("launch-ends", "Does a US launch license start at ignition?", 1,
     "At a US site, launch starts at ignition, and an orbital launch with a returning booster ends at payload separation.", "English", "14 CFR 450.3"),
    ("payload-class", "Does one payload-class finding clear every later satellite?", 1,
     "A class determination clears each later satellite, and the FAA's determination covers the aspects the FCC regulates.", "English", "14 CFR 450.43"),
    ("permit-year", "Can an experimental permit be used for an orbital test?", 1,
     "An experimental permit authorizes an orbital test, it authorizes only one launch, and it lasts until the test program ends.", "English", "14 CFR 437.5"),
    ("no-hire", "Can a permittee charge a passenger for crew training?", 1,
     "Crew training authorizes a paying passenger, and a payload flown for a fee is allowed because the ban covers only people.", "English", "14 CFR 437.91"),
    ("eight-beams", "Did January 2026 leave the one-beam cap in place?", 1,
     "The December 2022 one-beam cap still limits 12.2-12.7 and 19.7-20.2, and Ku-band beams are unlimited once it is lifted.", "English", "DA-26-36"),
    ("seventeen-moot", "Was the 17.3-17.8 GHz downlink waiver granted?", 1,
     "The Bureau granted the 17.3-17.8 GHz downlink waiver, and it denied the uplink in that band.", "English", "DA-26-36"),
    ("impact-point", "Can the engine light with the impact point over a city?", 1,
     "An operating area may border a densely populated area, and engine ignition over a city is allowed because the vehicle is climbing.", "English", "14 CFR 437.57"),
    ("not-certified", "Does an FAA license certify the vehicle as safe for passengers?", 1,
     "An FAA license certifies the vehicle as safe for passengers, and oral consent on the day of flight is enough.", "English", "14 CFR 460.45"),
    ("crew-card", "Must a participant carry a pilot certificate and a second-class medical?", 1,
     "A participant must carry a pilot certificate and a second-class medical, and the pilot may leave the certificate in the office.", "English", "14 CFR 460.5"),
    ("crew-oxygen", "Must every person have a backup oxygen supply?", 1,
     "Every person on board must have a backup oxygen supply, and the vehicle must both prevent depressurization and keep every occupant conscious.", "English", "14 CFR 460.11"),
    ("verify-first", "Can a participant ride the verification flight?", 1,
     "A participant may ride the verification flight, and a successful ground test of the hardware and software is enough.", "English", "14 CFR 460.17"),
    ("crew-before-hire", "Can a new crew member be told on launch day?", 1,
     "Every crew member may be told on launch day, and a person employed on 23 December 2004 had to be told before being hired.", "English", "14 CFR 460.9"),
    ("restrain-all", "Do the human-factors rules restrain only the crew?", 1,
     "Only the crew must be restrained, and the flight must be operated so the participant can withstand the acceleration.", "English", "14 CFR 460.15"),
    ("abort-increases-risk", "Must the vehicle be destroyed whenever it leaves the planned path?", 1,
     "The vehicle must be destroyed whenever it leaves the planned path, invalid tracking always means abort, and every flight uses the highly reliable abort standard through landing.", "English", "14 CFR 450.108"),
    ("epfd-inside", "Does the EPFD waiver apply worldwide and survive the rulemaking?", 1,
     "The waiver lets Gen2 exceed single-entry EPFD limits worldwide, and it survives whatever the pending rulemaking adopts.", "English", "DA-26-36"),
    ("epfd-stop", "Can SpaceX keep the higher power while it studies interference?", 1,
     "SpaceX may keep operating above the EPFD limits while it studies the complaint, and the waiver cannot be revoked.", "English", "DA-26-36"),
    ("wband-receive", "Is the W-band grant a protected downlink with no elevation floor?", 1,
     "The W-band grant is a protected space-to-Earth downlink, and the order sets no elevation floor.", "English", "DA-26-36"),
    ("feeder-user", "May 19.4-19.6 and 29.1-29.5 GHz user links start immediately?", 1,
     "User links may start the day the waiver is granted, and they are protected against MSS feeder links.", "English", "DA-26-36"),
    ("aggregate-epfd", "Does the single-entry waiver also lift the aggregate EPFD limits?", 1,
     "Waiving the single-entry limit lets SpaceX consume the aggregate EPFD budget by itself.", "English", "DA-26-36"),
    # 2026-10-09 reset: 45 prompts every provider got right were cut; these 45 replaced them.
    ("arcep-passerelles", "Quelles décisions Arcep ont autorisé les passerelles Starlink en France, et lesquelles subsistent ?", 1,
     "Trois stations, toutes encore autorisées, sous le nom Starlink dès 2020.", "French", "Arcep 2020-0727 à 2022-0182"),
    ("subtel-gateways", "¿Cuántas estaciones terrenas autorizan los tres extractos de Subtel, y en qué comunas?", 1,
     "Una sola concesión, Cabrero 1 en la comuna de Cabrero, todo ya en servicio.", "Spanish", "Subtel 24-SP157939, 25-SP69683, 25-SP69681"),
    ("crown-dependency-licences", "Which Starlink entity holds which licence in Guernsey, Jersey, Cayman, TCI and the Falklands?", 1,
     "One entity everywhere, Jersey Class III, TCI licensed in March.", "English", "GCRA, JCRA, OfReg, TCI, FIG"),
    ("comreg-killala", "What did ComReg's three Killala documents each decide, and who objected?", 1,
     "A licence number and grant date already in 25/31; ComReg ran the EPFD assessment itself.", "English", "ComReg 25/31, 25/36R, 25/79"),
    ("cran-starlink-refusal", "What did Namibia's gazette notice decline, and what happened to the reconsideration?", 1,
     "Starlink never filed; the appeal was decided on the merits.", "English", "Gazette 8872, notice 197"),
    ("ift-starlink-titles", "¿Qué ampara cada uno de los tres títulos del IFT de Starlink México?", 1,
     "La modificación de 2022 amplió Ku/Ka para Gen2; USASAT-NGSO-3X figura en los títulos.", "Spanish", "IFT AUT-SAT-EXT-029/2021, 1262/2022, P/IFT/070721/319"),
    ("ofcom-gateway-history", "Which UK gateway sites did Ofcom grant, see surrendered, then vary, with which licence numbers?", 1,
     "Nine sites still licensed; Morn Hill varied; 2022 band edges quoted as 2024's.", "English", "Ofcom 2022, 2023, 2024"),
    ("enacom-starlink-acts", "¿Qué dispone cada una de las Resoluciones ENACOM 4/2024, 372/2026 y 663/2026?", 1,
     "Licencia directa en 2024; atribución general de banda W; USD 9.978.000 leídos como millones.", "Spanish", "ENACOM 4/2024, 372/2026, 663/2026"),
    ("anatel-starlink-consultas", "O que pede cada uma das três frentes da Starlink na Anatel, e quando foi publicada?", 1,
     "CP 9 ou CP 29 já concedidas; banda E entre as faixas da CP 9.", "Portuguese", "Anatel CP 9/2026, CP 29/2026, SEI 12964217"),
    ("hillsboro-atlanta-sta-chain", "Every 2026 STA for the Hillsboro and Atlanta gateways: file, dates, term, basis?", 1,
     "Four dated STAs only; the two section 1.62 grants are 60-day STAs.", "English", "SES-02843, 02845, 02864, 02895"),
    ("esim-gen2-mods", "Which of the seven blanket-terminal modifications were granted, and which are still pending?", 1,
     "The 13.75-14.0 and 14.5-14.75 GHz additions were granted on 15 April.", "English", "SES-02842, 02850, 02852, 02853"),
    ("swarm-starlink-vhf", "What became of Swarm's filings to add Starlink as a point of communication?", 1,
     "Seven or eight STAs granted; the points of communication were added permanently.", "English", "SES-02738 to 02766"),
    ("gateway-surrenders-april-2026", "Which five gateway licences did SpaceX surrender on 16 April 2026, and when were they granted?", 1,
     "Expirations or Bureau cancellations; a different count than five.", "English", "SES-02855, 02754, 02772, 02779"),
    ("space-station-filings-2026", "Status of each SpaceX space-station filing placed on notice in 2026, as of 9 October?", 1,
     "The one-million-satellite application is granted; S00887 belongs to another file.", "English", "DA 26-113, DA 26-1078, SAT notices"),
    ("texas-quadband-sta-to-licence", "Where do the five Texas quad-band gateways stand: STA, re-grant, 42 GHz STA, licence?", 1,
     "The licences are granted; Falfurrias has a September re-grant.", "English", "SES-02880 to 02901"),
    ("redmond-vband-sta", "List every 2026 action on the Redmond V-band gateway E240163.", 1,
     "Only three rows; 60-day terms for the section 1.62 grants; a Ka-band station.", "English", "SES-02843, 02864"),
    ("starship-els-2026", "What do the ELS records for SpaceX's 2026 Starship STA applications show?", 1,
     "A file number and grant date read off a mirror site as if from the ELS record.", "English", "FCC ELS STA_Print records"),
    ("42ghz-sta-wave", "Which call signs got 42.0-42.5 GHz STAs on 28 September 2026, and which other STA was in force for each?", 1,
     "The dual-band Atlanta E250226 is in the batch; Wills Point and Falfurrias have a parallel STA.", "English", "SES-02901, 02887, 02897, 02899"),
    ("directv-recon", "Where does DIRECTV's reconsideration of FCC 26-26 stand, and what did the Bureau say in DA 26-973?", 1,
     "The petition is decided; compatibility showings are already under review.", "English", "FCC 26-26, Report 3234, DA 26-973"),
    ("sateliot-afr", "How did Sateliot's second petition end, and what came after the dismissal?", 1,
     "The petition was denied after public notice; EchoStar opposed the application for review.", "English", "DA 26-398, DA 25-20"),
    ("bigleo-rm-11975", "What happened to SpaceX's Big LEO petition for rulemaking and the Kuiper and Iridium filings around it?", 1,
     "Denied rather than dismissed under 1.401(e); Iridium's petition went on public notice.", "English", "DA 24-298, DA 26-398"),
    ("object-years-recon", "What is the object-years condition Kuiper and Telesat tried to remove, and where did it come from?", 1,
     "A threshold of 100 satellites; the condition was removed in 2026.", "English", "DA 26-36, FCC 22-91"),
    ("part100-transition", "When does Part 100 take effect, and what does the transition guidance require?", 1,
     "Effective 23 July 2026 or 30 days after publication; two eligibility conditions.", "English", "FCC 26-47, DA 26-1080"),
    ("starship-nepa-final", "How did the 2023 Starship NEPA lawsuit end, by which orders and judge?", 1,
     "The September ruling dismissed the whole case; an appeal is pending.", "English", "D.D.C. 1:23-cv-01204"),
    ("gso-reference-links", "How many GSO reference links were adopted, and what did the September 2026 notice change?", 1,
     "328 GSO satellites or networks; 12.6 dB adopted; the selection criteria changed.", "English", "FCC 26-26, DA 26-467, DA 26-973"),
    ("echostar-2ghz-inquiry", "What happened to the 2 GHz MSS inquiry into EchoStar, and what did DA 26-471 add?", 1,
     "Carr's September 2025 letter closed the docket; the FCC opened the band.", "English", "DA 25-405, DA 26-398, DA 26-471"),
    ("part100-congress-letters", "What did the House Science Committee ask the FCC about Part 100, and what did Carr reply?", 1,
     "The reply came before the vote; orbital debris is an independent authority.", "English", "Babin/Lofgren letters, DOC-424156"),
    ("ofcom-gx10-starlink-coordination", "What does the GX10 application annex say about Starlink, and what did Ofcom decide?", 1,
     "USASAT filing names; Starlink objected; decided 2 September.", "English", "Ofcom GX10 application and decision"),
    ("eu-2ghz-mss-proposal", "Que propose la Commission pour la bande MSS 2 GHz après 2027, et pour quels opérateurs ?", 1,
     "Un règlement adopté ; Inmarsat et Solaris comme titulaires visés par l'article 20.", "French", "COM(2026) 311"),
    ("cayman-d2d-amendment", "What did the Cayman licence authorise, and what did Amendment No. 1 add for direct-to-device?", 1,
     "The amendment is dated 5 August; the MSS bands are exclusive.", "English", "OfReg TL-01 of 2025, Amendment No. 1"),
    ("tci-starlink-licence-chain", "Reconstruct the chain of notices that led to Starlink Caribbean's TCI licence.", 1,
     "Granted on 12 or 14 August; direct-to-device authorised standalone.", "English", "TCI DN 2024-6 to DN 2026-8"),
    ("japan-kddi-d2d-licence-chain", "KDDIの衛星ダイレクト通信用無線局は、いつ、どの種別で免許され、どの省令改正に基づくか？", 1,
     "12月25日に免許が付与された。", "Japanese", "関東総合通信局・総務省 報道資料"),
    ("enacom-banda-w-starlink", "¿Qué segmentos exactos de banda W autoriza la Resolución ENACOM 372/2026 a Starlink Argentina?", 1,
     "Un bloque continuo de 92 a 114,25 GHz con protección contra interferencias.", "Spanish", "ENACOM 372/2026"),
    ("d2d-step-two-testing", "What bond deadline, milestones, term and testing rules does the D2D authorization set?", 1,
     "Testing in the EchoStar bands only after Step Two closes; one 330 km shell.", "English", "DA-26-1078"),
    ("d2d-2ghz-direction", "In which territories may 2000-2020 MHz be a downlink, and what was deferred or dismissed?", 1,
     "Downlink nationwide including Puerto Rico; 2020-2025 MHz granted.", "English", "DA-26-1078"),
    ("d2d-aws3-scs-waivers", "Which SCS rules were waived for SpaceX's own spectrum, and what coordination comes first?", 1,
     "A general relaxation across all bands; 911 duties waived.", "English", "DA-26-1078"),
    ("third-round-cutoff", "Which SpaceX bands fall inside the third processing rounds, and which stay in the first?", 1,
     "All SpaceX Gen2 V-band satellites are in the third V-band round.", "English", "DA-26-552 and erratum"),
    ("starlink-router-coveredlist", "Who approved Starlink routers after the Covered List addition, for how long?", 1,
     "The FCC granted it; the approval is permanent.", "English", "DA-26-775, DA 26-278"),
    ("nepa-not-yet", "Do the new FCC NEPA rules already apply to the D2D grant?", 1,
     "The grant used the new rules; Gomez dissented in full.", "English", "FCC-26-64, DA-26-1078"),
    ("gso-arc-three-vs-four", "What GSO arc avoidance angle does FCC 26-26 set, and how does SpaceX's waiver condition differ?", 1,
     "SpaceX operates under the rule's 3-degree angle.", "English", "FCC-26-26, DA-26-1078"),
    ("twelve-seven-downlink", "How much spectrum does FCC-26-65 unlock at 12.7 GHz, and on what footing for NGSO downlinks?", 1,
     "550 MHz for consumer dishes and 1,050 MHz in total.", "English", "FCC-26-65"),
    ("lc39a-rod", "What did the LC-39A Record of Decision decide on closures, mitigation and the licence?", 1,
     "The ROD issued the licence; the preferred alternative is environmentally preferable.", "English", "FAA ROD, 29 January 2026"),
    ("faa-fr-notices-2026", "Every 2026 Federal Register notice from the FAA that concerns SpaceX: document, docket, date?", 1,
     "Fewer than six rows; a Falcon notice dated 2026.", "English", "federalregister.gov, FAA 2026"),
    ("ksc-starship-eis-timeline", "Trace the KSC Starship environmental review from notice of intent to Record of Decision.", 1,
     "A 2025 ROD; the ROD issued the licence.", "English", "FAA EIS notices, VOL 23-129"),
    ("florida-starship-sites", "Compare the LC-39A and SLC-37 Starship decisions as of 9 October 2026.", 1,
     "SpaceX is licensed to launch Starship from Florida today.", "English", "FAA ROD, DAF ROD, FAA-2026-10792"),
]
PRIOR = ("exa", "parallel", "keen", "select", "routed")
# Flight facts an engineer looks up. Not source code: this file has none.
VEHICLE = {1, 3, 4, 43}
GLOSS = {
    1: "Flight 14: date, booster, ship, satellites",
    3: "Crew-13: commander, capsule, docking time",
    4: "Transporter-18: count, booster, landing zone",
    5: "Presidents Cup: score and clincher",
    43: "Starship flight tests, January through September 2026",
    53: "Amazon Leo half-deployment order",
    54: "EchoStar bands transferred to SpaceX",
    55: "Part 25 replaced by Part 100",
    56: "Starship launches per year approved at LC-39A",
    57: "DSA non-compliance decision against X",
    58: "EU Digital Omnibus on AI, regulation number",
    59: "Upper C-band range opened for wireless",
    60: "Two Irish GDPR inquiries into X",
}
GRADE_WORD = {"c": "correct", "p": "partial", "w": "wrong"}


def build_pitch(root: Path, spacex_vm: dict | None = None) -> dict:
    plan = _plan(root / "spacex-jobs")
    return {
        "columns": [{"id": pid, "label": LABELS[pid]} for pid in COLUMNS],
        "plan": plan,
        "plan_n": sum(row["n"] for row in plan),
        "reset": _reset(root / "spacex-jobs"),
        "prior": _prior(root / "spacex", spacex_vm),
        "jobs": _block(root / "spacex-jobs", hypothesis=True),
    }


def _plan(directory: Path) -> list[dict]:
    """PLAN rows in the order of the live prompt file; rows for cut prompts are left out."""
    rows = {i: {"id": i, "title": title, "n": n, "kind": kind, "lang": lang, "why": why}
            for i, title, n, kind, lang, why in PLAN}
    path = directory / "prompts.json"
    if not path.is_file():
        return list(rows.values())
    prompts = json.loads(path.read_text())
    return [rows[p["use_case"]] for p in prompts if p.get("use_case") in rows]


def _reset_ids(directory: Path) -> frozenset[int]:
    batch = directory / "prompts.batch55.json"
    if not batch.is_file():
        return frozenset()
    return frozenset(p["id"] for p in json.loads(batch.read_text()))


def _reset(directory: Path) -> dict | None:
    """The 2026-10-09 prompt reset, if this audit had one: how many were cut and how many added."""
    path = directory / "cut-2026-10-09.json"
    if not path.is_file():
        return None
    cut = json.loads(path.read_text())
    batch = directory / "prompts.batch55.json"
    added = len(json.loads(batch.read_text())) if batch.is_file() else 0
    return {"date": "2026-10-09", "cut": len(cut), "added": added}


def _block(directory: Path, hypothesis: bool = False) -> dict:
    unavailable = _unavailable(directory)
    required = [pid for pid in COLUMNS if pid not in unavailable]
    results = _load(directory / "results.json")
    ready = _ready(results, required)
    block = {
        "status": "complete" if ready else "empty",
        "empty": EMPTY,
        "unavailable": [{"id": pid, "label": LABELS.get(pid, pid), "reason": reason}
                        for pid, reason in unavailable.items() if pid in COLUMNS],
        "counts": [],
        "hypothesis": "",
        "study": None,
        "second": (results.meta.second_judge or None) if results is not None else None,
        "slug": directory.name,
        "missing": [{"prompt": pid, "provider": LABELS.get(prov, prov)} for pid, prov in _missing(results, required)],
    }
    if not ready or results is None:
        return block
    grades = {(j.prompt_id, j.provider): j.grade for j in results.judgments}
    n = len(results.prompts)
    block["counts"] = [
        {"id": pid, "label": LABELS[pid], "correct": _correct(results, grades, pid), "n": n}
        for pid in required
    ]
    if hypothesis:
        block["hypothesis"] = _hypothesis(results, grades, required)
        block["study"] = _study(results, grades, required, _reset_ids(directory))
        block["study"]["second"] = results.meta.second_judge or None
    return block


def _prior(directory: Path, vm: dict | None = None) -> dict | None:
    """The finished run of these prompts. Not a Grok score."""
    results = _load(directory / "results.json")
    if results is None or not results.prompts:
        return None
    if vm is None:
        vm = build_view_model(results)
    rows = []
    for pid in PRIOR:
        summary = vm["summary"].get(pid)
        if summary is None:
            continue
        rows.append({
            "id": pid,
            "label": LABELS[pid],
            "correct": summary["k"],
            "partial": summary["partial"],
            "n": summary["n"],
            "per_prompt": summary["per_prompt"],
            "per_correct": summary["per_correct"],
            "t50_correct": summary["t50_correct"],
            "failed": summary["failed_calls"],
        })
    if not rows:
        return None
    slices = []
    for kind in vm["types"]:
        cells = [{"id": pid, "correct": kind["k"].get(pid, 0)} for pid in PRIOR if pid in kind["k"]]
        if cells:
            slices.append({"name": kind["name"], "n": kind["n"], "cells": cells})
    reads = vm["reads"]
    lines = [
        reads["acc"]["title"] + ". " + reads["acc"]["body"],
        reads["cost"]["title"] + ". " + reads["cost"]["body"],
        reads["time"]["title"] + ". " + _clip(reads["time"]["body"]),
        reads["search"]["title"] + ". " + reads["search"]["body"],
        reads["fail"]["title"] + ". " + reads["fail"]["body"],
    ]
    wide = next((kind for kind in vm["types"] if kind["id"] == "wide"), None)
    if wide is not None:
        lines.append(
            f"List-building is the wide-search slice, {wide['n']} prompts. "
            f"Exa got {wide['k'].get('exa', 0)} right, Keenable SELECT {wide['k'].get('select', 0)}, "
            f"the router {wide['k'].get('routed', 0)}."
        )
    both, faster = _both_correct(vm, "exa", "select")
    return {
        "model": results.meta.agent_model,
        "note": results.meta.agent_model_note,
        "when": _when(results.meta.created_at),
        "slug": results.meta.slug,
        "rows": rows,
        "slices": slices,
        "lines": lines,
        "both_correct": both,
        "select_faster": faster,
        "jobs": _jobs(vm),
        "picture": _picture(vm),
        "gap": _gap(vm),
        "shape": {
            "filings": 44,
            "calls": 30,
            "queries": 1,
        },
    }


def _jobs(vm: dict) -> list[dict]:
    prompts = vm["prompts"]
    compliance = [p for p in prompts if p["type"] == "domain"]
    vehicle = [p for p in prompts if p["id"] in VEHICLE]
    other = [p for p in prompts if p["type"] != "domain" and p["id"] not in VEHICLE]
    jobs = [
        ("compliance", "Compliance", compliance, True),
        ("vehicle", "Flight configuration", vehicle, True),
        ("other", "The rest of the 60", other, False),
    ]
    return [_job(vm, job_id, title, group, gloss=gloss) for job_id, title, group, gloss in jobs if group]


def _job(vm: dict, job_id: str, title: str, prompts: list[dict], *, gloss: bool) -> dict:
    rows = _slice_rows(vm, prompts)
    gaps = _gaps(prompts)
    return {
        "id": job_id,
        "title": title,
        "n": len(prompts),
        "rows": rows,
        "ids": [p["id"] for p in prompts],
        "questions": [{"id": p["id"], "gloss": GLOSS.get(p["id"], _short(p["text"]))} for p in prompts] if gloss else [],
        "gaps": gaps,
        "read": _read(job_id, rows, prompts, gaps),
    }


def _slice_rows(vm: dict, prompts: list[dict]) -> list[dict]:
    rows = []
    n = len(prompts)
    for pid in PRIOR:
        if not prompts or pid not in prompts[0]["cells"]:
            continue
        cells = [p["cells"][pid] for p in prompts]
        grades = Counter(c["grade"] for c in cells)
        ran = [c for c in cells if not c.get("missing")]
        spread = stats.time_spread([c["seconds"] for c in ran])
        spread_ok = stats.time_spread([c["seconds"] for c in ran if c["grade"] == "c"])
        total = sum(c["cost"] for c in cells)
        provider = vm["provider"].get(pid, {})
        rows.append({
            "id": pid,
            "label": LABELS[pid],
            "short": provider.get("short") or LABELS[pid],
            "hex": provider.get("hex") or "#111827",
            "correct": grades["c"],
            "partial": grades["p"],
            "wrong": grades["w"],
            "n": n,
            "per_prompt": total / n if n else 0.0,
            "t50": spread["median"] or 0.0,
            "t_mean": spread["mean"] or 0.0,
            "p50": spread["p50"] or 0.0,
            "p90": spread["p90"] or 0.0,
            "p99": spread["p99"] or 0.0,
            "t50_correct": spread_ok["median"],
            "t_mean_correct": spread_ok["mean"],
            "p90_correct": spread_ok["p90"],
            "p99_correct": spread_ok["p99"],
        })
    return rows


def _gaps(prompts: list[dict]) -> list[dict]:
    gaps = []
    for prompt in prompts:
        grades = [prompt["cells"][pid]["grade"] for pid in PRIOR if pid in prompt["cells"]]
        if len(set(grades)) < 2:
            continue
        bits = []
        for pid in PRIOR:
            cell = prompt["cells"].get(pid)
            if cell is None:
                continue
            bits.append(f"{LABELS[pid]} {GRADE_WORD[cell['grade']]}, {cell['seconds']:.0f}s, ${cell['cost']:.4f}")
        gaps.append({
            "id": prompt["id"],
            "gloss": GLOSS.get(prompt["id"], _short(prompt["text"])),
            "detail": " · ".join(bits),
        })
    return gaps


def _read(job_id: str, rows: list[dict], prompts: list[dict], gaps: list[dict]) -> str:
    by = {row["id"]: row for row in rows}
    exa, keen, select = by["exa"], by["keen"], by["select"]
    if job_id == "compliance":
        arms = [by[i]["correct"] for i in ("exa", "keen", "select", "routed")]
        if len(set(arms)) == 1:
            head = f"Exa, Keenable search, SELECT and the router each got {arms[0]} of {exa['n']}."
        else:
            head = ", ".join(f"{by[i]['label']} {by[i]['correct']}" for i in ("exa", "keen", "select", "routed"))
            head = f"{head}, of {exa['n']}."
        return (
            f"Eight regulation questions. {head} Parallel got {by['parallel']['correct']}. "
            f"When the answer was right, Exa's median was {_when_right(exa)} and SELECT's was {_when_right(select)}. "
            f"Mean cost is ${exa['per_prompt']:.3f} for Exa and ${keen['per_prompt']:.3f} for search. {_gap_clause(gaps)}"
        )
    if job_id == "vehicle":
        matched = (
            f"Exa and Keenable search each got {exa['correct']} of {exa['n']}."
            if exa["correct"] == keen["correct"]
            else f"Exa got {exa['correct']} of {exa['n']}. Keenable search got {keen['correct']}."
        )
        miss = (
            "SELECT's miss is the one that hit the time limit."
            if select["correct"] < select["n"]
            else "SELECT matched them."
        )
        return (
            f"Four flight questions: booster, ship, payload count, crew, and the year's Starship tests. "
            f"This file has no source-code prompt. {matched} SELECT got {select['correct']}. {miss} "
            f"Exa's median when right was {_when_right(exa)}. SELECT's, on the ones it got right, was "
            f"{_when_right(select)}. {_gap_clause(gaps)}"
        )
    return (
        f"The other {len(prompts)} prompts are news, sport, elections and town budgets. "
        f"Exa got {exa['correct']} of {exa['n']}, Keenable search {keen['correct']}, SELECT {select['correct']}. "
        f"The headline gap between Exa and SELECT lives here, not in the two SpaceX blocks. {_gap_clause(gaps)}"
    )


def _when_right(row: dict) -> str:
    seconds = row["t50_correct"]
    if seconds is None:
        return "no correct answer"
    return f"{seconds:.0f}s"


def _gap_clause(gaps: list[dict]) -> str:
    if not gaps:
        return "Every provider got the same grade on every prompt in this block."
    shown = ", ".join(str(gap["id"]) for gap in gaps[:8])
    extra = f" and {len(gaps) - 8} more" if len(gaps) > 8 else ""
    return f"Grades split on prompt {shown}{extra}."


def _short(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= 88 else text[:85] + "..."


def _both_correct(vm: dict, base: str, other: str) -> tuple[int, int]:
    both = faster = 0
    for row in vm["prompts"]:
        a = row["cells"].get(base)
        b = row["cells"].get(other)
        if not a or not b or a["grade"] != "c" or b["grade"] != "c":
            continue
        both += 1
        if b["seconds"] < a["seconds"]:
            faster += 1
    return both, faster


SHORT = {
    "exa": "Exa",
    "keen": "Search",
    "select": "SELECT",
    "routed": "Router",
    "parallel": "Parallel",
}
BAR = {"compliance": "bar-ink", "vehicle": "bar-fade", "other": "bar-muted"}
JOB_NAME = {
    "compliance": "Compliance",
    "vehicle": "Flight configuration",
    "other": "The other prompts",
}


def _gap(vm: dict) -> dict | None:
    """Match rate when Exa was correct, and dollars per correct answer. Not a leaderboard."""
    prompts = vm["prompts"]
    if not prompts or "exa" not in prompts[0]["cells"]:
        return None
    arms = [pid for pid in ("keen", "select") if pid in prompts[0]["cells"]]
    if not arms:
        return None
    tagged = [{**p, "job": _job_of(p)} for p in prompts]
    classes = []
    losses = []
    for job_id, title in (("compliance", "Compliance"), ("vehicle", "Flight configuration"), ("other", "The rest")):
        group = [p for p in tagged if p["job"] == job_id]
        if not group:
            continue
        exa_ok = [p for p in group if _grade(p, "exa") == "c"]
        row = {
            "id": job_id,
            "title": title,
            "n": len(group),
            "exa_correct": len(exa_ok),
            "exa_per_correct": _per_correct(group, "exa"),
            "arms": {},
        }
        for pid in arms:
            matched = [p for p in exa_ok if _grade(p, pid) == "c"]
            row["arms"][pid] = {
                "label": LABELS[pid],
                "matched": len(matched),
                "of": len(exa_ok),
                "per_correct": _per_correct(group, pid),
            }
            for prompt in exa_ok:
                if _grade(prompt, pid) == "c":
                    continue
                losses.append({
                    "id": prompt["id"],
                    "gloss": GLOSS.get(prompt["id"], _short(prompt["text"])),
                    "job": title,
                    "arm": LABELS[pid],
                    "grade": GRADE_WORD.get(_grade(prompt, pid), _grade(prompt, pid)),
                    "label": _miss_label(prompt, pid),
                })
        classes.append(row)
    search_only = [p for p in tagged if _grade(p, "keen") == "c" and _grade(p, "exa") != "c"]
    select_only = [
        _split_card(p) for p in tagged
        if "select" in p["cells"] and _grade(p, "select") == "c" and _grade(p, "exa") != "c"
    ]
    return {
        "classes": classes,
        "arms": arms,
        "losses": losses,
        "n": len(tagged),
        "search_only": len(search_only),
        "select_only": select_only,
        "lead": _case_lead(classes, len(search_only), len(select_only)),
    }


def _case_lead(classes: list[dict], search_only: int, select_only: int) -> str:
    by = {row["id"]: row for row in classes}
    bits = []
    comp = by.get("compliance")
    flight = by.get("vehicle")
    if comp and "keen" in comp["arms"]:
        got = comp["arms"]["keen"]
        bits.append(
            f"On the {comp['n']} compliance questions, Keenable search matches Exa "
            f"on {got['matched']} of the {got['of']} Exa got right."
        )
    if flight and "keen" in flight["arms"]:
        got = flight["arms"]["keen"]
        bits.append(
            f"On the {flight['n']} flight questions, search matches Exa on {got['matched']} of {got['of']}."
        )
    noun = "prompt" if search_only == 1 else "prompts"
    bits.append(f"Search is correct where Exa is not on {search_only} {noun}.")
    if select_only:
        noun = "prompt" if select_only == 1 else "prompts"
        bits.append(f"SELECT is graded correct where Exa is not on {select_only} {noun}.")
    else:
        bits.append("SELECT is not graded correct on any prompt Exa missed.")
    return " ".join(bits)


def _split_card(prompt: dict) -> dict:
    exa = prompt["cells"]["exa"]
    select = prompt["cells"]["select"]
    return {
        "id": prompt["id"],
        "text": prompt["text"],
        "gloss": GLOSS.get(prompt["id"], _short(prompt["text"])),
        "job": prompt["job"],
        "same_claim": _same_claim(exa["answer"], select["answer"]),
        "exa": _arm_view(exa),
        "select": _arm_view(select),
    }


def _same_claim(left: str, right: str) -> bool:
    """True when both answers name the same clincher. A grade gap is then the judge, not a new fact."""
    needle = "jacob bridgeman"
    return needle in left.lower() and needle in right.lower()


def _arm_view(cell: dict) -> dict:
    return {
        "g": cell["grade"],
        "grade": GRADE_WORD.get(cell["grade"], cell["grade"]),
        "seconds": cell["seconds"],
        "cost": cell["cost"],
        "note": cell.get("note") or "",
        "answer": _excerpt(cell.get("answer") or ""),
        "sources": [
            {"url": s["url"], "title": s.get("title") or s.get("domain") or s["url"], "domain": s.get("domain") or ""}
            for s in (cell.get("sources") or [])[:10]
        ],
        "hits": [_hit_row(url) for url in (cell.get("hits") or [[]])[0][:8]],
    }


def _hit_row(url: str) -> dict:
    host = url.split("/")[2] if "://" in url else url
    host = host.removeprefix("www.")
    return {"url": url, "domain": host}


def _excerpt(text: str, limit: int = 320) -> str:
    flat = " ".join(text.split())
    if len(flat) <= limit:
        return flat
    return flat[: limit - 1] + "…"


def _per_correct(prompts: list[dict], pid: str) -> float | None:
    costs = [p["cells"][pid]["cost"] for p in prompts if pid in p["cells"] and _grade(p, pid) == "c"]
    if not costs:
        return None
    return sum(costs) / len(costs)


def _evidence(cell: dict) -> set[str]:
    found = {norm_url(s["url"]) for s in cell.get("sources") or [] if s.get("url")}
    found |= {norm_url(url) for url in cell.get("fetched_urls") or [] if url}
    return {url for url in found if url}


def _miss_label(prompt: dict, pid: str) -> str:
    exa_urls = {norm_url(s["url"]) for s in prompt["cells"]["exa"].get("sources") or [] if s.get("url")}
    exa_urls = {url for url in exa_urls if url}
    if not exa_urls:
        return "no Exa URL"
    if exa_urls & _evidence(prompt["cells"][pid]):
        return "extraction miss"
    return "index gap"


def _picture(vm: dict) -> dict | None:
    """The paired comparison. Numbers come from the finished run, not from a written claim."""
    prompts = vm["prompts"]
    if not prompts or "exa" not in prompts[0]["cells"] or "keen" not in prompts[0]["cells"]:
        return None
    have = [pid for pid in ("exa", "keen", "select", "routed", "parallel") if pid in prompts[0]["cells"]]
    tagged = [{**p, "job": _job_of(p)} for p in prompts]
    spacex = [p for p in tagged if p["job"] != "other"]
    both = [p for p in tagged if _grade(p, "exa") == "c" and _grade(p, "keen") == "c"]
    extra_s = [p["cells"]["keen"]["seconds"] - p["cells"]["exa"]["seconds"] for p in both]
    extra_usd = [p["cells"]["keen"]["cost"] - p["cells"]["exa"]["cost"] for p in both]
    keen_c = sum(1 for p in spacex if _grade(p, "keen") == "c")
    exa_c = sum(1 for p in spacex if _grade(p, "exa") == "c")
    unique = [p["id"] for p in spacex if _grade(p, "keen") == "c" and _grade(p, "exa") != "c"]
    tied = bool(spacex) and not unique and keen_c == exa_c
    select_both = [p for p in tagged if "select" in have and _grade(p, "exa") == "c" and _grade(p, "select") == "c"]
    select_faster = sum(1 for p in select_both if p["cells"]["select"]["seconds"] < p["cells"]["exa"]["seconds"])
    groups = [
        ("compliance", [p for p in tagged if p["job"] == "compliance"]),
        ("vehicle", [p for p in tagged if p["job"] == "vehicle"]),
        ("other", [p for p in tagged if p["job"] == "other"]),
    ]
    series = []
    for job_id, group in groups:
        if not group:
            continue
        series.append({
            "name": f"{JOB_NAME[job_id]}, {len(group)} prompts",
            "cls": BAR[job_id],
            "data": [round(100 * sum(1 for p in group if _grade(p, pid) == "c") / len(group), 1) for pid in have],
        })
    return {
        "title": "Search ties Exa on the SpaceX questions" if tied else "Search against Exa on the SpaceX questions",
        "lead": _picture_lead(spacex, keen_c, exa_c, unique, both, extra_s, extra_usd),
        "unique_search": len(unique),
        "both": len(both),
        "takeaways": _takeaways(spacex, exa_c, keen_c, unique, both, extra_s, extra_usd, select_both, select_faster),
        "share": _columns([SHORT[pid] for pid in have], series) if series else None,
        "pair": _hbars(spacex, [("exa", "bar-muted", "Exa"), ("keen", "bar-ink", "Keenable search")], cap=_nice(_max_seconds(spacex, ("exa", "keen")))) if spacex else None,
        "select": _hbars(spacex, [("select", "bar-select", "SELECT")], cap=_nice(_max_seconds(spacex, ("select",))), ref=_ref(vm)) if spacex and "select" in have else None,
        "scatter": _scatter(tagged),
        "rows": [_grade_row(p, have) for p in spacex],
        "aside": _aside(tagged, have),
    }


def _takeaways(spacex, exa_c, keen_c, unique, both, extra_s, extra_usd, select_both, select_faster) -> list[str]:
    lines = []
    if spacex:
        lines.append(
            f"On the {len(spacex)} SpaceX prompts, Exa is correct on {exa_c} and Keenable search on {keen_c}. "
            f"Search is right where Exa is not on {len(unique)} of them."
        )
    if extra_s:
        lines.append(
            f"Where both were graded correct ({len(both)} prompts), search took a median of "
            f"{stats.median(extra_s):+.1f}s and {_signed_money(stats.median(extra_usd))}."
        )
    if select_both:
        lines.append(
            f"SELECT was the faster correct answer {select_faster} times, "
            f"of {len(select_both)} prompts Exa and SELECT both got right."
        )
    return lines


def _picture_lead(spacex, keen_c, exa_c, unique, both, extra_s, extra_usd) -> str:
    if not spacex:
        return "This file has no compliance or flight prompt. The chart is the whole set."
    median_s = stats.median(extra_s) if extra_s else 0.0
    median_usd = stats.median(extra_usd) if extra_usd else 0.0
    return (
        f"On the {len(spacex)} compliance and flight prompts, Keenable search is correct on {keen_c} "
        f"and Exa on {exa_c}. Prompts in that set where search is right and Exa is not: {len(unique)}. "
        f"On the {len(both)} prompts in the file that both got right, search took a median of "
        f"{median_s:+.1f}s and {_signed_money(median_usd)}."
    )


def _aside(prompts: list[dict], have: list[str]) -> str:
    behind = [p for p in prompts if _grade(p, "exa") == "c" and _grade(p, "keen") != "c"]
    ahead = [p for p in prompts if "select" in have and _grade(p, "select") == "c" and _grade(p, "exa") != "c"]
    parts = []
    if behind:
        ids = ", ".join(str(p["id"]) for p in behind)
        jobs = ", ".join(sorted({p["job"] for p in behind}))
        parts.append(
            f"Search is not correct, and Exa is, on prompt {ids}. "
            f"{'That prompt is' if len(behind) == 1 else 'Those prompts are'} in {jobs}."
        )
    if ahead:
        ids = ", ".join(str(p["id"]) for p in ahead)
        parts.append(
            f"SELECT is graded correct where Exa is not on prompt {ids}. "
            "The answers are at the top, with the sources in citation order."
        )
    if "routed" in have:
        spacex = [p for p in prompts if p["job"] != "other"]
        if spacex:
            got = sum(1 for p in spacex if _grade(p, "routed") == "c")
            parts.append(f"The router was correct on {got} of {len(spacex)} of the SpaceX prompts.")
    return " ".join(parts)


def _grade_row(prompt: dict, have: list[str]) -> dict:
    order = [pid for pid in ("exa", "keen", "select", "routed") if pid in have]
    return {
        "label": _axis_label(prompt),
        "cells": [
            f"{GRADE_WORD.get(prompt['cells'][pid]['grade'], prompt['cells'][pid]['grade'])} · {secs(prompt['cells'][pid]['seconds'])}"
            for pid in order
        ],
        "heads": [SHORT[pid] for pid in order],
        "warn": prompt["cells"].get("select", {}).get("grade") not in (None, "c"),
    }


def _job_of(prompt: dict) -> str:
    if prompt["type"] == "domain":
        return "compliance"
    if prompt["id"] in VEHICLE:
        return "vehicle"
    return "other"


def _grade(prompt: dict, pid: str) -> str:
    cell = prompt["cells"].get(pid)
    return cell["grade"] if cell else ""


def _axis_label(prompt: dict) -> str:
    gloss = GLOSS.get(prompt["id"])
    if not gloss:
        return str(prompt["id"])
    head = gloss.split(":")[0]
    label = f"{prompt['id']} {head}"
    return label if len(label) <= 28 else label[:25] + "..."


def _max_seconds(prompts: list[dict], ids: tuple[str, ...]) -> float:
    vals = [p["cells"][pid]["seconds"] for p in prompts for pid in ids if pid in p["cells"]]
    return max(vals) if vals else 1.0


def _ref(vm: dict) -> dict | None:
    summary = vm["summary"].get("exa") or {}
    value = summary.get("t50")
    if not value:
        return None
    return {"value": value, "label": f"Exa median {secs(value)}"}


def _signed_money(x: float) -> str:
    sign = "+" if x >= 0 else "−"
    return f"{sign}${abs(x):.3f}"


def _nice(value: float) -> float:
    step = 10 if value > 20 else (5 if value > 5 else 1)
    return float(max(step, math.ceil(value / step) * step))


def _columns(categories: list[str], series: list[dict], *, y_max: float = 100, w: int = 680, h: int = 240) -> dict:
    left, right, top, bottom = 36, 12, 16, 32
    plot_w = w - left - right
    plot_h = h - top - bottom
    group = plot_w / max(len(categories), 1)
    bar_w = min(16, (group - 28) / max(len(series), 1))
    cluster = len(series) * bar_w + max(len(series) - 1, 0) * 3
    groups = []
    for i, cat in enumerate(categories):
        cx = left + group * i + group / 2
        x0 = cx - cluster / 2
        bars = []
        for j, item in enumerate(series):
            val = item["data"][i]
            bh = 0 if y_max == 0 else val / y_max * plot_h
            bars.append({
                "x": round(x0 + j * (bar_w + 3), 1),
                "y": round(top + plot_h - bh, 1),
                "h": round(max(bh, 0), 1),
                "w": round(bar_w, 1),
                "cls": item["cls"],
                "title": f"{item['name']}. {cat}: {val:g}%",
            })
        groups.append({"label": cat, "x": round(cx, 1), "bars": bars})
    base = top + plot_h
    return {
        "w": w, "h": h, "base": base, "left": left, "groups": groups,
        "legend": [{"name": item["name"], "cls": item["cls"]} for item in series],
        "yticks": [{"y": round(base - (v / y_max) * plot_h, 1), "label": f"{v:g}%"} for v in (0, 50, 100)],
    }


def _hbars(prompts: list[dict], series: list[tuple[str, str, str]], *, cap: float, ref: dict | None = None, w: int = 680) -> dict | None:
    if not prompts or cap <= 0:
        return None
    label_w, right, top = 176, 56, 16
    bar_h, gap = 7, 3
    row_h = 8 + len(series) * (bar_h + gap)
    height = top + len(prompts) * row_h + 20
    plot_w = w - label_w - right

    def sx(value: float) -> float:
        return label_w + min(value, cap) / cap * plot_w

    rows = []
    for i, prompt in enumerate(prompts):
        y0 = top + i * row_h
        bars = []
        for j, (pid, cls, _name) in enumerate(series):
            if pid not in prompt["cells"]:
                continue
            seconds = prompt["cells"][pid]["seconds"]
            bars.append({
                "x": label_w,
                "y": y0 + j * (bar_h + gap),
                "w": round(max(sx(seconds) - label_w, 0), 1),
                "h": bar_h,
                "cls": cls,
                "lx": round(sx(seconds) + 4, 1),
                "ly": y0 + j * (bar_h + gap) + bar_h - 1,
                "text": secs(seconds),
                "title": f"{SHORT.get(pid, pid)} {secs(seconds)}, {GRADE_WORD.get(prompt['cells'][pid]['grade'], '')}",
            })
        rows.append({"label": _axis_label(prompt), "y": y0 + row_h / 2, "bars": bars})
    ticks = [0, cap / 2, cap]
    return {
        "w": w, "h": height, "rows": rows, "label_w": label_w,
        "legend": [{"name": name, "cls": cls} for _pid, cls, name in series],
        "xticks": [{"x": round(sx(v), 1), "label": secs(v)} for v in ticks],
        "axis_y": height - 16,
        "ref": None if ref is None else {"x": round(sx(ref["value"]), 1), "label": ref["label"], "y": top - 2},
    }


def _scatter(prompts: list[dict], *, w: int = 680, h: int = 360) -> dict:
    exa = [p["cells"]["exa"]["seconds"] for p in prompts]
    keen = [p["cells"]["keen"]["seconds"] for p in prompts]
    x_max = _nice(max(exa) if exa else 1)
    y_max = _nice(max(keen) if keen else 1)
    left, right, top, bottom = 44, 16, 16, 32

    def sx(value: float) -> float:
        return left + min(value, x_max) / x_max * (w - left - right)

    def sy(value: float) -> float:
        return top + (1 - min(value, y_max) / y_max) * (h - top - bottom)

    dots = []
    for prompt in prompts:
        both = _grade(prompt, "exa") == "c" and _grade(prompt, "keen") == "c"
        dots.append({
            "cx": round(sx(prompt["cells"]["exa"]["seconds"]), 1),
            "cy": round(sy(prompt["cells"]["keen"]["seconds"]), 1),
            "r": 3.5 if prompt["job"] == "other" else 5,
            "cls": f"job-{prompt['job']}",
            "open": not both,
            "title": (
                f"Prompt {prompt['id']}, {prompt['job']}. "
                f"Exa {secs(prompt['cells']['exa']['seconds'])} {GRADE_WORD.get(_grade(prompt, 'exa'), '')}. "
                f"Search {secs(prompt['cells']['keen']['seconds'])} {GRADE_WORD.get(_grade(prompt, 'keen'), '')}."
            ),
        })
    return {
        "w": w, "h": h, "left": left, "base": h - bottom, "top": top,
        "dots": dots,
        "line": {"x1": round(sx(0), 1), "y1": round(sy(0), 1), "x2": round(sx(min(x_max, y_max)), 1), "y2": round(sy(min(x_max, y_max)), 1)},
        "xticks": [{"x": round(sx(v), 1), "label": secs(v)} for v in (0, x_max / 2, x_max)],
        "yticks": [{"y": round(sy(v), 1), "label": secs(v)} for v in (0, y_max / 2, y_max)],
    }


def _when(iso: str) -> str:
    try:
        parsed = datetime.fromisoformat(iso)
    except ValueError:
        return iso[:10]
    return f"{parsed.day} {parsed.strftime('%B %Y')}"


def _clip(text: str) -> str:
    return text.replace(" A hollow dot means the answer was wrong. It is not a time.", "").strip()


def _unavailable(directory: Path) -> dict[str, str]:
    path = directory / "unavailable.json"
    if not path.is_file():
        return {}
    data = json.loads(path.read_text())
    return {str(k): str(v) for k, v in data.items() if v}


def _load(path: Path) -> AuditResults | None:
    if not path.is_file():
        return None
    return AuditResults.model_validate_json(path.read_text())


MAX_MISSING = 3  # cells with no run at all (both arms errored, so the router had nothing to copy)


def _missing(results: AuditResults | None, required: list[str]) -> list[tuple[int, str]]:
    if results is None:
        return []
    judged = {(j.prompt_id, j.provider) for j in results.judgments}
    return [(prompt.id, pid) for prompt in results.prompts for pid in required if (prompt.id, pid) not in judged]


def _ready(results: AuditResults | None, required: list[str]) -> bool:
    if results is None or not results.prompts or not required:
        return False
    return len(_missing(results, required)) <= MAX_MISSING


def _correct(results: AuditResults, grades: dict, pid: str) -> int:
    return sum(1 for prompt in results.prompts if grades.get((prompt.id, pid)) == "c")


def _hypothesis(results: AuditResults, grades: dict, available: list[str]) -> str:
    """Printed only when search, SELECT, and the router each beat Exa, Parallel, and Grok native."""
    rivals = [pid for pid in ("exa", "parallel", "grok") if pid in available]
    if "grok" not in available:
        return "Grok native is unavailable, so this run cannot show a lead over it."
    if not rivals:
        return "This run does not show a lead."
    scores = {
        pid: sum(1 for prompt in results.prompts if grades.get((prompt.id, pid)) == "c")
        for pid in (*ARMS, *rivals)
    }
    for arm in ARMS:
        if arm not in available or any(scores[arm] <= scores[rival] for rival in rivals):
            return ("This run does not show Keenable ahead overall. To ask for a place inside Grok or Cursor, "
                    "search, SELECT, and the router would each need more correct answers than Exa, "
                    "Parallel, and Grok native.")
    return ("On this run, Keenable search, SELECT, and the router each have more correct answers than "
            "Exa, Parallel, and Grok native.")


def _study(results: AuditResults, grades: dict, providers: list[str], reset_ids: frozenset[int] = frozenset()) -> dict:
    """One point per provider, and the answer each provider wrote."""
    runs = {(run.prompt_id, run.provider): run for run in results.runs}
    judged = {(j.prompt_id, j.provider): j for j in results.judgments}
    prompts = results.prompts
    items = []
    gaps = []
    rivals = [pid for pid in ("exa", "parallel", "grok") if pid in providers]
    arms = [pid for pid in ARMS if pid in providers]
    by_case: dict[str, list] = {}
    for prompt in prompts:
        case = prompt.use_case or "unlabeled"
        cells = []
        for pid in providers:
            run = runs.get((prompt.id, pid))
            judgment = judged.get((prompt.id, pid))
            cell = _cell(run, judgment.grade if judgment else grades.get((prompt.id, pid), ""), judgment)
            cell["id"] = pid
            cell["label"] = LABELS[pid]
            cell["answer"] = _excerpt(run.answer if run else "")
            cells.append(cell)
        item = {
            "id": prompt.id,
            "text": prompt.text,
            "short": _short(prompt.text),
            "use_case": case,
            "type": prompt.type,
            "class_title": {row[0]: row[1] for row in PLAN}.get(case, case),
            "cells": cells,
        }
        items.append(item)
        by_case.setdefault(case, []).append(item)
        rival_right = [LABELS[pid] for pid in rivals if grades.get((prompt.id, pid)) == "c"]
        arm_miss = [LABELS[pid] for pid in arms if grades.get((prompt.id, pid)) != "c"]
        if rival_right and arm_miss and all(grades.get((prompt.id, pid)) != "c" for pid in arms):
            gaps.append({
                "id": prompt.id,
                "use_case": case,
                "class_title": {row[0]: row[1] for row in PLAN}.get(case, case),
                "text": _short(prompt.text),
                "rivals": ", ".join(rival_right),
                "arms": ", ".join(arm_miss),
            })
    rows = _provider_rows(items, providers)
    misses = _misses(items, arms)
    wins = [
        {
            "id": item["id"],
            "class_title": item["class_title"],
            "text": _short(item["text"]),
            "arms": ", ".join(c["label"] for c in item["cells"] if c["id"] in arms and c["grade"] == "c"),
        }
        for item in items
        if any(c["id"] in arms and c["grade"] == "c" for c in item["cells"])
        and not any(c["id"] in rivals and c["grade"] == "c" for c in item["cells"])
    ]
    read = _study_read(rows, gaps, items)
    split = _budget_split(items, providers)
    foreign = _foreign(items, providers)
    tests = _pair_tests(items, providers)
    for row in rows:
        low, high = stats.wilson(row["correct"], row["n"])
        row["ci_low"], row["ci_high"] = 100 * low, 100 * high
    return {
        "model": results.meta.agent_model,
        "judge": results.meta.judge_model,
        "budget": results.meta.budget,
        "when": _when(results.meta.created_at),
        "takeaways": _findings(rows, split, wins, gaps, foreign, items, tests, reset_ids),
        "tests": tests,
        "rows": rows,
        "categories": _categories(by_case, providers),
        "items": items,
        "gaps": gaps,
        "wins": wins,
        "split": split,
        "foreign": foreign,
        "misses": misses,
        "read": read,
        "closing": _closing(misses),
        "charts": {
            "speed_cost": _chart(
                rows, "per_prompt", "t50", "Dollars per prompt", "Median seconds", "usd", "seconds", "cost", "seconds",
            ),
            "cost_acc": _chart(
                rows, "per_prompt", "accuracy", "Dollars per prompt", "Correct (%)", "usd", "pct", "cost", "hit",
            ),
            "acc_speed": _chart(rows, "accuracy", "t50", "Correct (%)", "Median seconds", "pct", "seconds"),
        },
    }


def _provider_rows(items: list[dict], providers: list[str]) -> list[dict]:
    rows = []
    n = len(items)
    for pid in providers:
        cells = [next(cell for cell in item["cells"] if cell["id"] == pid) for item in items]
        points = [
            {
                "prompt": item["id"],
                "cost": cell["cost"],
                "seconds": cell["seconds"],
                "hit": 100.0 if cell["grade"] == "c" else 0.0,
                "grade_word": cell["grade_word"],
            }
            for item, cell in zip(items, cells, strict=True)
        ]
        correct = [cell for cell in cells if cell["grade"] == "c"]
        ran = [cell for cell in cells if not cell.get("missing")]  # a prompt with no run has no time or cost
        costs = [cell["cost"] for cell in ran]
        times = [cell["seconds"] for cell in ran]
        searches = [cell["searches"] for cell in ran]
        rows.append({
            "id": pid,
            "label": LABELS[pid],
            "cls": f"dot-{pid}",
            "correct": len(correct),
            "partial": sum(1 for cell in cells if cell["grade"] == "p"),
            "wrong": sum(1 for cell in cells if cell["grade"] == "w"),
            "budget_hit": sum(1 for cell in cells if cell["budget_hit"]),
            "n": n,
            "accuracy": (100 * len(correct) / n) if n else 0.0,
            "per_prompt": sum(costs) / n if n else 0.0,
            "per_correct": (sum(cell["cost"] for cell in correct) / len(correct)) if correct else None,
            "cost_min": min(costs) if costs else None,
            "cost_max": max(costs) if costs else None,
            "search_per_prompt": sum(cell["cost_search"] for cell in cells) / n if n else 0.0,
            "model_per_prompt": sum(cell["cost_model"] for cell in cells) / n if n else 0.0,
            "searches_median": stats.median(searches),
            "searches_mean": (sum(searches) / n) if n else 0.0,
            "searches_min": min(searches) if searches else None,
            "searches_max": max(searches) if searches else None,
            "t50": stats.median(times),
            "t_mean": (sum(times) / n) if n else 0.0,
            "t_min": min(times) if times else None,
            "t_max": max(times) if times else None,
            "t50_correct": _median_grade(cells, "c"),
            "t50_partial": _median_grade(cells, "p"),
            "t50_wrong": _median_grade(cells, "w"),
            "points": points,
        })
    return rows


def _pair_tests(items: list[dict], providers: list[str]) -> list[dict]:
    """Each Keenable column against each rival on the same prompts.

    Accuracy: exact McNemar on the prompts where exactly one was correct, with a paired bootstrap 95%
    interval for the difference. Seconds and dollars: Wilcoxon signed-rank on the per-prompt pairs.
    Holm corrects each family of tests for the number of pairs.
    """
    arms = [pid for pid in ARMS if pid in providers]
    rivals = [pid for pid in ("exa", "parallel", "grok") if pid in providers]
    if not arms or not rivals:
        return []

    def series(pid: str, key: str) -> list:
        out = []
        for item in items:
            cell = next(c for c in item["cells"] if c["id"] == pid)
            out.append(cell["grade"] == "c" if key == "hit" else cell[key])
        return out

    tests = []
    for arm in arms:
        for rival in rivals:
            a, b = series(arm, "hit"), series(rival, "hit")
            only_arm, only_rival = stats.discordant(a, b)
            ci = stats.paired_bootstrap_diff(a, b)
            secs_a, secs_b = series(arm, "seconds"), series(rival, "seconds")
            usd_a, usd_b = series(arm, "cost"), series(rival, "cost")
            tests.append({
                "arm": LABELS[arm], "rival": LABELS[rival], "arm_id": arm, "rival_id": rival,
                "diff": 100 * ci.diff, "low": 100 * ci.low, "high": 100 * ci.high,
                "only_arm": only_arm, "only_rival": only_rival,
                "p_acc": stats.mcnemar_exact(only_arm, only_rival),
                "d_secs": stats.median([x - y for x, y in zip(secs_a, secs_b, strict=True)]),
                "p_secs": stats.wilcoxon(secs_a, secs_b),
                "d_usd": stats.median([x - y for x, y in zip(usd_a, usd_b, strict=True)]),
                "p_usd": stats.wilcoxon(usd_a, usd_b),
            })
    for key in ("p_acc", "p_secs", "p_usd"):
        for test, adjusted in zip(tests, stats.holm([t[key] for t in tests]), strict=True):
            test[key + "_holm"] = adjusted
    for test in tests:
        if test["p_acc_holm"] >= 0.05:
            test["verdict"] = "No clear difference"
        else:
            test["verdict"] = f"{test['arm'] if test['diff'] > 0 else test['rival']} ahead"
    return tests


def _grade_of(item: dict, pid: str) -> str:
    return next((c["grade"] for c in item["cells"] if c["id"] == pid), "")


def _versus_exa(items: list[dict], arms: list[dict]) -> list[str]:
    """The Keenable arm that gets the most prompts right that Exa missed, by prompt type. One sentence."""
    from searchaudit.models import PROMPT_TYPE_NAMES
    best, best_only = None, []
    for arm in arms:
        only = [it for it in items if _grade_of(it, arm["id"]) == "c" and _grade_of(it, "exa") != "c"]
        if len(only) > len(best_only):
            best, best_only = arm, only
    if best is None or not best_only:
        return []
    exa_only = sum(1 for it in items if _grade_of(it, "exa") == "c" and _grade_of(it, best["id"]) != "c")
    by_type = Counter(it["type"] for it in best_only)
    split = ", ".join(f"{PROMPT_TYPE_NAMES.get(t, t).lower()} {n}" for t, n in by_type.most_common())
    return [(f"Against Exa alone, {best['label']} got {len(best_only)} prompts right that Exa missed ({split}); "
             f"Exa got {exa_only} that {best['label']} missed.")]


def _reset_lines(items: list[dict], rows: list[dict], reset_ids: frozenset[int]) -> list[str]:
    """Correct answers on the prompts added in the reset, printed only when a Keenable arm matches or beats Exa there."""
    added = [it for it in items if it["id"] in reset_ids]
    if not added:
        return []
    correct = {row["id"]: sum(1 for it in added if _grade_of(it, row["id"]) == "c") for row in rows}
    if "exa" not in correct or not any(correct[a] >= correct["exa"] for a in ARMS if a in correct):
        return []
    order = sorted(correct, key=lambda pid: -correct[pid])
    listing = ", ".join(f"{LABELS[pid]} {correct[pid]}" for pid in order)
    return [(f"On the {len(added)} cross-document prompts added in the reset: {listing} correct. "
             "That is the slice the reset was built to test, and the one place a Keenable column is ahead of Exa.")]


def _p(value: float) -> str:
    return "p < 0.001" if value < 0.001 else f"p = {value:.3f}" if value < 0.01 else f"p = {value:.2f}"


def _findings(rows, split, wins, gaps, foreign, items, tests=(), reset_ids: frozenset[int] = frozenset()) -> dict[str, list[str]]:
    """Where Keenable wins, where it falls short, and what would change the result.

    Each sentence is printed only when this run's numbers support it, so a re-run cannot leave a stale claim.
    """
    by = {row["id"]: row for row in rows}
    exa, keen, select, routed = by.get("exa"), by.get("keen"), by.get("select"), by.get("routed")
    rivals = [by[pid] for pid in ("exa", "parallel", "grok") if pid in by]
    arms = [by[pid] for pid in ARMS if pid in by]
    if not (exa and arms and rivals):
        return {"wins": [], "short": [], "next": []}
    n = exa["n"]
    best_rival = max(rivals, key=lambda r: (r["correct"], -r["per_prompt"]))
    best_arm = max(arms, key=lambda r: (r["correct"], -r["per_prompt"]))
    priced = [r for r in rows if r["per_correct"] is not None]
    cheapest = min(priced, key=lambda r: r["per_correct"]) if priced else None
    fastest = min(rows, key=lambda r: r["t50"])
    wins_out, short, nxt = [], [], []

    if wins:
        wins_out.append(f"On {len(wins)} prompt{'s' if len(wins) != 1 else ''}, Keenable was right and "
                        "Exa, Parallel, and Grok native all missed. They are listed below.")
    for arm in arms:
        for rival in rivals:
            if arm["correct"] > rival["correct"]:
                wins_out.append(f"{arm['label']} ({arm['correct']}) beat {rival['label']} ({rival['correct']}).")
    if cheapest and cheapest["id"] in ARMS and exa["per_correct"] is not None:
        wins_out.append(f"{cheapest['label']} was the cheapest per correct answer: "
                        f"{money(cheapest['per_correct'], 3, False)}, against "
                        f"{exa['label']}'s {money(exa['per_correct'], 3, False)}.")
    if fastest["id"] in ARMS:
        wins_out.append(f"{fastest['label']} was the fastest, a median of {secs(fastest['t50'])} per prompt.")
    wins_out += _versus_exa(items, arms)
    wins_out += _reset_lines(items, rows, reset_ids)
    if foreign and foreign["n"]:
        for arm in arms:
            if foreign["correct"][arm["id"]] >= foreign["correct"]["exa"]:
                wins_out.append(f"On the {foreign['n']} prompts not in English, {arm['label']} got "
                                f"{foreign['correct'][arm['id']]}, Exa {foreign['correct']['exa']}.")
                break

    gap = best_rival["correct"] - best_arm["correct"]
    level = [t for t in tests if t["verdict"] == "No clear difference"]
    if level:
        wins_out.append("On accuracy, these pairs are not distinguishable on 100 prompts (Holm-corrected "
                        "McNemar, 5% level): " + "; ".join(f"{t['arm']} and {t['rival']}" for t in level) + ".")
    if gap > 0:
        head = next((t for t in tests if t["arm_id"] == best_arm["id"] and t["rival_id"] == best_rival["id"]), None)
        line = (f"The best Keenable column, {best_arm['label']}, got {best_arm['correct']} of {n}. "
                f"{best_rival['label']} got {best_rival['correct']}, {gap} more.")
        if head:
            raw_clear = head["high"] < 0 or head["low"] > 0
            line += f" Paired 95% interval for the gap: {-head['high']:.0f} to {-head['low']:.0f} points."
            if head["p_acc_holm"] < 0.05:
                line += f" It holds after correcting for the {len(tests)} comparisons ({_p(head['p_acc_holm'])})."
            elif raw_clear:
                line += (f" On its own that is a clear gap, but after correcting for the {len(tests)} "
                         f"comparisons it is not ({_p(head['p_acc_holm'])}).")
            else:
                line += f" Not a clear difference on this many prompts ({_p(head['p_acc_holm'])})."
        short.append(line)
    if gaps:
        short.append(f"On {len(gaps)} prompt{'s' if len(gaps) != 1 else ''} a rival was right and all three "
                     "Keenable columns were wrong. They are listed under Final thoughts.")
    if keen and keen["budget_hit"]:
        line = (f"Keenable search ran out of its budget on {keen['budget_hit']} of {n} prompts "
                f"(Exa on {exa['budget_hit']}).")
        out = next((g for g in split if g["label"].startswith("Search ran out")), None)
        if out:
            got = {c["id"]: c["correct"] for c in out["cells"]}
            line += f" On those {out['n']}, search got {got['keen']} right and Exa {got['exa']}."
        short.append(line)
    if select and select["t50"] > 2 * exa["t50"]:
        short.append(f"Keenable SELECT is slow: a median of {secs(select['t50'])} per prompt against "
                     f"Exa's {secs(exa['t50'])}. Fine for a compliance memo, too slow for chat.")
    if foreign and foreign["n"] and keen and foreign["correct"]["keen"] < foreign["correct"]["exa"]:
        short.append(f"On the {foreign['n']} prompts not in English, Keenable search got "
                     f"{foreign['correct']['keen']} right and Exa {foreign['correct']['exa']}.")

    vias = Counter(c["via"] for item in items for c in item["cells"] if c["id"] == "routed" and c["via"])
    if routed and keen and select and vias:
        pick = "search" if vias.get("keen", 0) >= vias.get("select", 0) else "SELECT"
        better = "SELECT" if select["correct"] > keen["correct"] else "search"
        if pick != better:
            nxt.append(f"The router sent {vias.get('keen', 0)} of {n} prompts to search, but {better} "
                       f"scored higher ({max(select['correct'], keen['correct'])} against "
                       f"{min(select['correct'], keen['correct'])}). Routing more to {better} would help.")
        elif routed["correct"] < best_arm["correct"]:
            nxt.append(f"The router ({routed['correct']}) did worse than {best_arm['label']} on its own "
                       f"({best_arm['correct']}). Its choice is not yet adding accuracy.")
    if keen and keen["budget_hit"] >= n // 10:
        nxt.append("A larger tool-call budget would show whether Keenable search's misses are budget or "
                   "index. That is a re-run of the prompts where the budget ran out.")
    if gap > 0:
        nxt.append(f"For SpaceX today, {best_rival['label']} is the most accurate. Keenable has to close "
                   f"the {gap}-answer gap on this set before it is the better choice here.")
    else:
        nxt.append(f"For SpaceX today, {best_arm['label']} is as accurate as any rival on this set.")
    return {"wins": wins_out, "short": short, "next": nxt}


def _foreign(items: list[dict], providers: list[str]) -> dict | None:
    """Correct counts on the prompts asked in a language other than English."""
    lang = {row[0]: row[4] for row in PLAN}
    group = [item for item in items if lang.get(item["use_case"], "English") != "English"]
    if not group:
        return None
    correct = {
        pid: sum(1 for item in group for c in item["cells"] if c["id"] == pid and c["grade"] == "c")
        for pid in providers
    }
    return {"n": len(group), "correct": correct}


def _budget_split(items: list[dict], providers: list[str]) -> list[dict]:
    """Correct counts on the prompts where Keenable search finished in budget, and where it ran out."""
    if "keen" not in providers:
        return []
    out = []
    for hit in (False, True):
        group = [item for item in items if any(c["id"] == "keen" and c["budget_hit"] for c in item["cells"]) == hit]
        if not group:
            continue
        out.append({
            "label": "Search ran out of budget" if hit else "Search finished in budget",
            "n": len(group),
            "cells": [
                {"id": pid, "label": LABELS[pid],
                 "correct": sum(1 for item in group for c in item["cells"] if c["id"] == pid and c["grade"] == "c")}
                for pid in providers
            ],
        })
    return out


def _median_grade(cells: list[dict], grade: str) -> float | None:
    times = [cell["seconds"] for cell in cells if cell["grade"] == grade]
    return stats.median(times) if times else None


def _misses(items: list[dict], arms: list[str]) -> list[dict]:
    """Keenable grades that are not correct, grouped by the judge's cause."""
    out = []
    for pid in arms:
        groups: dict[str, list] = {}
        for item in items:
            cell = next(cell for cell in item["cells"] if cell["id"] == pid)
            if cell["grade"] == "c":
                continue
            groups.setdefault(cell["failure"] or "unspecified", []).append({
                "id": item["id"],
                "text": item["short"],
                "note": cell["note"],
                "grade_word": cell["grade_word"],
            })
        for mode, prompts in groups.items():
            name, why = FAILURE_MODES.get(mode, (
                "Wrong, no cause named",
                "The judge marked it partial or wrong and did not name a cause.",
            ))
            out.append({
                "id": pid,
                "label": LABELS[pid],
                "mode": mode,
                "name": name,
                "why": why,
                "n": len(prompts),
                "prompts": prompts,
            })
    out.sort(key=lambda row: (-row["n"], row["label"], row["name"]))
    return out


def _closing(misses: list[dict]) -> str:
    if not misses:
        return "Keenable search, SELECT, and the router have no partial or wrong answer on this run."
    top = misses[0]
    return (
        f"The judge's most common reason for a Keenable miss is {top['name'].lower()}, "
        f"on {top['label']}, {top['n']} time{'s' if top['n'] != 1 else ''}."
    )


def _categories(by_case: dict, providers: list[str]) -> list[dict]:
    titles = {row[0]: row[1] for row in PLAN}
    order = [row[0] for row in PLAN]
    for case in by_case:
        if case not in order:
            order.append(case)
    out = []
    for case in order:
        group = by_case.get(case)
        if not group:
            continue
        out.append({
            "id": case,
            "title": titles.get(case, case),
            "n": len(group),
            "rows": _provider_rows(group, providers),
        })
    return out


def _cell(run: Run | None, grade: str, judgment=None) -> dict:
    failure = ""
    note = ""
    if judgment is not None and grade != "c":
        failure = judgment.failure_mode or ""
        note = judgment.note or ""
    if run is None:
        failure = "error"
        note = ("No run: one Keenable arm failed on this prompt, and the router replay needs a finished "
                "answer from both arms before Jev picks one. Counted as not correct.")
    return {
        "missing": run is None,
        "grade": grade,
        "grade_word": GRADE_WORD.get(grade, grade or "—"),
        "cost": run.cost_usd if run else 0.0,
        "cost_search": run.cost_search_usd if run else 0.0,
        "cost_model": run.cost_model_usd if run else 0.0,
        "searches": run.searches if run else 0,
        "seconds": (run.latency_ms / 1000) if run else 0.0,
        "budget_hit": bool(run and run.budget_hit),
        "via": (run.via or "") if run else "",
        "failure": failure,
        "failure_name": FAILURE_MODES[failure][0] if failure in FAILURE_MODES else "",
        "note": note,
    }


def _study_read(rows: list[dict], gaps: list[dict], items: list[dict]) -> str:
    """The lede: the ranking, then why the Keenable columns lost, from the runs themselves."""
    if not rows:
        return ""
    ranked = sorted(rows, key=lambda row: (-row["correct"], row["per_prompt"]))
    n = ranked[0]["n"]
    lead = ", ".join(f"{row['label']} {row['correct']}" for row in ranked) + f" correct out of {n}."
    by_id = {row["id"]: row for row in rows}
    keen, exa = by_id.get("keen"), by_id.get("exa")
    if keen and keen["budget_hit"]:
        lead += (
            f" Keenable search ran out of its budget on {keen['budget_hit']} of {n} prompts"
        )
        if exa:
            lead += (
                f", with a median of {keen['searches_median']:.0f} searches per prompt"
                f" against Exa's {exa['searches_median']:.0f}"
            )
        lead += "."
    vias = Counter(cell["via"] for item in items for cell in item["cells"] if cell["id"] == "routed" and cell["via"])
    routed = by_id.get("routed")
    if vias.get("keen") and keen and routed:
        lead += f" The router sent {vias['keen']} of {n} to search"
        lead += ", so it scored like search." if abs(routed["correct"] - keen["correct"]) <= 2 else "."
    if gaps:
        lead += f" On {len(gaps)} prompts a rival was right and all three Keenable columns were wrong."
    return lead


def _chart(
    rows: list[dict], x_key: str, y_key: str, xlabel: str, ylabel: str, x_kind: str, y_kind: str,
    point_x: str | None = None, point_y: str | None = None,
) -> dict:
    w, h = 720, 300
    left, right, top, bottom = 64, 24, 28, 44
    xs = [float(row[x_key]) for row in rows]
    ys = [float(row[y_key]) for row in rows]
    x_max = _axis_max(max(xs) if xs else 1, x_kind)
    y_max = _axis_max(max(ys) if ys else 1, y_kind)
    plot_w, plot_h = w - left - right, h - top - bottom

    def sx(value: float) -> float:
        return left + (0 if x_max == 0 else min(max(value, 0), x_max) / x_max) * plot_w

    def sy(value: float) -> float:
        return top + (1 - (0 if y_max == 0 else min(max(value, 0), y_max) / y_max)) * plot_h

    def pips(row: dict) -> list[dict]:
        if not point_x or not point_y:
            return []
        out = []
        for point in row.get("points") or []:
            out.append({
                "cx": round(sx(float(point[point_x])), 1),
                "cy": round(sy(float(point[point_y])), 1),
                "title": (
                    f"Prompt {point['prompt']}. {point['grade_word']}. "
                    f"{_tip(float(point['cost']), 'usd')}. {_tick(float(point['seconds']), 'seconds')}."
                ),
            })
        return out

    return {
        "w": w, "h": h, "left": left, "top": top, "base": h - bottom,
        "xlabel": xlabel, "ylabel": ylabel,
        "xticks": [{"x": round(sx(v), 1), "label": _scale_label(v, x_kind)} for v in _axis_ticks(x_max, x_kind)],
        "yticks": [{"y": round(sy(v), 1), "label": _scale_label(v, y_kind)} for v in _axis_ticks(y_max, y_kind)],
        "dots": [
            {
                "id": row["id"],
                "cx": round(sx(float(row[x_key])), 1),
                "cy": round(sy(float(row[y_key])), 1),
                "cls": row["cls"],
                "title": (
                    f"{row['label']}. {xlabel} {_tip(float(row[x_key]), x_kind)}. "
                    f"{ylabel} {_tip(float(row[y_key]), y_kind)}."
                ),
                "pips": pips(row),
            }
            for row in rows
        ],
    }


def _axis_max(value: float, kind: str) -> float:
    if kind == "pct":
        return 100.0
    if kind == "usd":
        return _money_max(value)
    return _nice(value if value else 1)


def _axis_ticks(limit: float, kind: str) -> list[float]:
    if kind == "pct":
        return [0, 50, 100]
    if kind == "usd":
        return _money_ticks(limit)
    return [0, limit / 2, limit]


def _money_max(value: float) -> float:
    """A dollar ceiling just above the data. $0.36 becomes $0.40, not $1."""
    if value <= 0:
        return 0.1
    if value < 1:
        return max(0.1, math.ceil(value * 10 - 1e-9) / 10)
    step = 0.5 if value < 2 else (1 if value < 5 else 5)
    return float(math.ceil((value - 1e-9) / step) * step)


def _money_ticks(limit: float) -> list[float]:
    """Positive dollar ticks. The origin and a bare $0.50 are not labeled."""
    step = 0.1 if limit <= 0.5 else (0.25 if limit <= 1 else (0.5 if limit <= 2 else limit / 4))
    ticks: list[float] = []
    value = step
    while value < limit - 1e-9:
        rounded = round(value, 2)
        if rounded > 0 and abs(rounded - 0.5) > 1e-9:
            ticks.append(rounded)
        value = round(value + step, 2)
    top = round(limit, 2)
    if top not in ticks and abs(top - 0.5) > 1e-9:
        ticks.append(top)
    return ticks or [top]


def _scale_label(value: float, kind: str) -> str:
    if kind == "usd":
        return f"${value:.2f}"
    return _tick(value, kind)


def _tip(value: float, kind: str) -> str:
    if kind == "usd":
        return str(money(value, 4 if value < 1 else 2, False))
    return _tick(value, kind)


def _tick(value: float, kind: str) -> str:
    if kind == "pct":
        return f"{value:.0f}%"
    if kind == "usd":
        return str(money(value, 4 if value < 1 else 2, False))
    # The axis is labeled in seconds, so a long run stays "312.0s" rather than "5.2m".
    return f"{value:.1f}s"
