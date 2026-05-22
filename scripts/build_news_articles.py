"""Build the local news article corpus for exact-status-url coverage checks.

The rows below came from exact X/Twitter status URL search results collected on
2026-05-22. Each generated article record preserves the searched status URL
variants in `links`, and marks the basis so downstream tooling can distinguish
search-result evidence from article-body extraction.
"""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = REPO_ROOT / "data" / "news" / "articles.jsonl"

RAW_ROWS = """
POTUS	1881408246067744887	WCNC	Inauguration Day: Trump takes control of @POTUS X account	https://www.wcnc.com/article/news/politics/national-politics/inauguration/potus-x-account-resets-donald-trump/507-96f48280-ee51-4d01-a21f-957037f84d35	2025-01-20
POTUS	1881408246067744887	WKYC	Inauguration Day: Trump takes control of @POTUS X account	https://www.wkyc.com/article/news/politics/national-politics/inauguration/potus-x-account-resets-donald-trump/507-96f48280-ee51-4d01-a21f-957037f84d35	2025-01-20
POTUS	1881408246067744887	KHOU	Inauguration Day: Trump takes control of @POTUS X account	https://www.khou.com/article/news/politics/national-politics/inauguration/potus-x-account-resets-donald-trump/507-96f48280-ee51-4d01-a21f-957037f84d35	2025-01-20
POTUS	1881408246067744887	WBIR	Inauguration Day: Trump takes control of @POTUS X account	https://www.wbir.com/article/news/politics/national-politics/inauguration/potus-x-account-resets-donald-trump/507-96f48280-ee51-4d01-a21f-957037f84d35	2025-01-20
POTUS	1881408246067744887	WFAA	Inauguration Day: Trump takes control of @POTUS X account	https://www.wfaa.com/article/news/politics/national-politics/inauguration/potus-x-account-resets-donald-trump/507-96f48280-ee51-4d01-a21f-957037f84d35	2025-01-20
POTUS	1881408246067744887	NEWS CENTER Maine	Inauguration Day: Trump takes control of @POTUS X account	https://www.newscentermaine.com/article/news/politics/national-politics/inauguration/potus-x-account-resets-donald-trump/507-96f48280-ee51-4d01-a21f-957037f84d35	2025-01-20
POTUS	1881408246067744887	KGW	Inauguration Day: Trump takes control of @POTUS X account	https://www.kgw.com/article/news/politics/national-politics/inauguration/potus-x-account-resets-donald-trump/507-96f48280-ee51-4d01-a21f-957037f84d35	2025-01-20
POTUS	1881408246067744887	Local Memphis	Inauguration Day: Trump takes control of @POTUS X account	https://www.localmemphis.com/article/news/politics/national-politics/inauguration/potus-x-account-resets-donald-trump/507-96f48280-ee51-4d01-a21f-957037f84d35	2025-01-20
POTUS	1881408246067744887	12 News	Inauguration Day: Trump takes control of @POTUS X account	https://www.12news.com/article/news/politics/national-politics/inauguration/potus-x-account-resets-donald-trump/507-96f48280-ee51-4d01-a21f-957037f84d35	2025-01-20
DHSgov	1971584538171023759	Fox News	Immigrants charged with shooting up youth baseball game granted legal status under Biden	https://www.foxnews.com/politics/immigrants-charged-shooting-up-youth-baseball-game-major-city-granted-legal-status-under-biden	2025-09-26
DHSgov	1971584538171023759	The Blaze	Two MONSTERS arrested in shooting of children's baseball coach were granted status under Biden	https://www.theblaze.com/news/baseball-coach-shot-dhs-biden	2025-09-26
DHSgov	1971584538171023759	Houston Public Media	Department of Homeland Security weighs in on shooting of youth baseball coach near Houston	https://www.houstonpublicmedia.org/articles/news/crime/2025/09/30/532249/three-suspects-identified-charged-in-katy-baseball-field-shooting/	2025-09-30
DHSgov	1971584538171023759	KHOU	3 arrested after Texas baseball coach shot during pregame prayer at tournament	https://www.khou.com/article/news/crime/3-men-arrested-katy-texas-baseball-coach-shot/285-aabc16d9-6cd5-4482-b35f-7e2a9c213c9e	2025-09-26
DHSgov	1971584538171023759	Texas Scorecard	Two Charged in Katy Ballpark Shooting Gained Legal Status Under Biden	https://texasscorecard.com/local/two-charged-in-katy-ballpark-shooting-gained-legal-status-under-biden/	2025-09-26
DHSgov	1971584538171023759	The Texan	DHS Blasts Biden Administration for Granting Entry to Two Katy Shooting Suspects	https://thetexan.news/issues/criminal-justice/dhs-blasts-biden-administration-for-granting-entry-to-two-katy-youth-baseball-shooting-suspects/article_6662bc67-ccef-4af8-b48a-ad51773b6f52.html	2025-09-26
DHSgov	1971584538171023759	Lead Stories	Fact Check: Police Did NOT Charge That Three Men Opened Fire On A Children's Baseball Field	https://leadstories.com/hoax-alert/2025/09/fact-check-three-men-did-not-open-fire-on-a-childrens-baseball-field-in-katy-texas-it-was-irresponsible-target-shooting.html	2025-09-30
DHSgov	1971584538171023759	Yahoo News	Fact Check: Police Did NOT Charge That Three Men Opened Fire On A Children's Baseball Field	https://www.yahoo.com/news/articles/fact-check-police-did-not-075136215.html	2025-09-30
DHSgov	1971584538171023759	American Thinker	Jihad in Texas?	https://www.americanthinker.com/blog/2025/09/jihad_in_texas.html	2025-09-26
DHSgov	1971584538171023759	Infowars	Muslim Migrants Arrested for Opening Fire at Texas Youth Baseball Game Were Let Into US by Biden	https://www.infowars.com/posts/muslim-migrants-arrested-for-opening-fire-at-texas-youth-baseball-game-were-let-into-us-by-biden-dhs	2025-09-26
DHSgov	1971584538171023759	Border Hawk	Muslim Migrants Arrested for Opening Fire at Texas Youth Baseball Game Were Let Into US by Biden	https://borderhawk.news/muslim-migrants-arrested-for-opening-fire-at-texas-youth-baseball-game-were-let-into-us-by-biden-dhs/	2025-09-26
DHSgov	1971584538171023759	PJ Media	Texas Jihad Update: It's WAY Worse Than We Thought	https://pjmedia.com/kevindowneyjr/2025/09/26/texas-jihad-update-guess-who-let-the-shooters-in-n4944156	2025-09-26
DHSgov	1971584538171023759	Covering Katy	Third Katy Baseball Shooting Suspect Remains A Mystery	https://coveringkaty.com/news/katy/still-no-information-on-mysterious-3rd-suspect-in-rac-baseba/	2025-09-30
WhiteHouse	1979297874031555036	Fox News	White House joins liberal platform Bluesky with viral meme video post	https://www.foxnews.com/media/white-house-taunts-liberals-provocative-meme-filled-debut-bluesky	2025-10-17
WhiteHouse	1979297874031555036	NBC News	White House slams Mark Hamill over Bluesky post depicting Trump in a grave	https://www.nbcnews.com/pop-culture/pop-culture-news/trump-mark-hamill-post-grave-white-house-slams-star-wars-actor-rcna344101	2025-10-19
WhiteHouse	1979297874031555036	Gizmodo	White House Invades Bluesky to Troll, Predictably Gets Mass Blocked	https://gizmodo.com/white-house-blocked-on-bluesky-2000674092	2025-10-19
WhiteHouse	1979297874031555036	Yahoo News	The White House Joins BlueSky and Instantly Becomes the 2nd-Most Blocked Account	https://www.yahoo.com/news/articles/white-house-joins-bluesky-instantly-185014888.html	2025-10-19
WhiteHouse	1979297874031555036	TechCrunch	The White House is already one of the most blocked accounts on Bluesky	https://techcrunch.com/2025/10/19/the-white-house-is-already-one-of-the-most-blocked-accounts-on-bluesky/	2025-10-19
PressSec	2043738726178669041	The Daily Beast	DoorDash PR Boss Melts Down After Donald Trump Oval Office Stunt Backfires	https://www.thedailybeast.com/doordash-meltdown-after-donald-trump-oval-office-stunt-backfires/	2026-04-14
PressSec	2043738726178669041	KIRO 7	DoorDash Grandma delivers McDonald's order to Trump at Oval Office	https://www.kiro7.com/news/trending/doordash-grandma-delivers-mcdonalds-order-trump-oval-office-receives-tip/62UQDGTTXNCQNOAGQKT7EAJBFQ/	2026-04-13
PressSec	2043738726178669041	CBS News	DoorDasher joins Trump for White House press event after delivering McDonald's	https://www.cbsnews.com/news/doordash-worker-delivers-trump-mcdonalds/	2026-04-13
PressSec	2043738726178669041	Fox Baltimore	PHOTOS: President Trump gets DoorDash delivery of McDonald's outside Oval Office	https://foxbaltimore.com/news/nation-world/trump-doordash-mcdonalds-oval-office-grandma-big-beautiful-bill-iran-jesus-photo-middle-east-strait-of-hormuz-white-house	2026-04-13
PressSec	2043738726178669041	KSAT	Trump tips DoorDash driver $100 for delivering McDonald's to Oval Office	https://www.ksat.com/news/politics/2026/04/13/trump-tips-doordash-driver-100-for-delivering-mcdonalds-to-oval-office/	2026-04-13
PressSec	2043738726178669041	Fox News	Trump gets McDonald's DoorDash delivery at Oval Office on Tax Day	https://www.foxnews.com/politics/trumps-mcdonalds-order-comes-cash-surprise-doordash-grandma-outside-white-house	2026-04-13
PressSec	2043738726178669041	MS.now	Trump's McDonald's photo-op shows he has run out of answers on the economy	https://www.ms.now/the-briefing-with-jen-psaki/trumps-oval-office-mcdonalds-photo-op-economy-gas-prices-midterms	2026-04-14
USDOL	1969161021232148795	Newsweek	H-1B visa update: Department of Labor to prioritize Americans	https://www.newsweek.com/h-1b-visa-update-department-labor-americans-11104262	2025-09-19
USDOL	1969161021232148795	Boundless	Project Firewall Steps Up H-1B Visa Enforcement	https://www.boundless.com/blog/project-firewall-h1b-enforcement	2025-09-19
USDOL	1969161021232148795	Global Immigration Blog	DOL Project Firewall Increases Employer Risks and Penalties	https://www.globalimmigrationblog.com/2025/12/dols-recently-launched-project-firewall-increases-employer-risks-penalties-for-h-1b-practices/	2025-12-01
USDOL	1969161021232148795	Greenspoon Marder LLP	U.S. Department of Labor Launches Project Firewall	https://www.gmlaw.com/news/u-s-department-of-labor-launches-project-firewall/	2025-09-20
USDOL	1969161021232148795	Snell & Wilmer	DOL Launches Project Firewall Targeting H-1B Employers	https://www.swlaw.com/publication/dol-launches-project-firewall-targeting-h-1b-employers/	2025-09-22
USDOL	1979310850403160244	Newsweek	H-1B visa update: Department of Labor to prioritize Americans	https://www.newsweek.com/h-1b-visa-update-department-labor-americans-11104262	2025-09-19
USDOL	1979310850403160244	Greenspoon Marder LLP	U.S. Department of Labor Launches Project Firewall	https://www.gmlaw.com/news/u-s-department-of-labor-launches-project-firewall/	2025-09-20
WhiteHouse	2008963216630796516	CBS News	Trump says he will seek to ban institutional investors from buying single-family homes	https://www.cbsnews.com/news/trump-ban-institutional-investors-single-family-homes/	2026-01-07
WhiteHouse	2008963216630796516	NPR	Senate passes bipartisan housing bill targeting large investors	https://www.npr.org/2026/03/12/nx-s1-5742566/senate-bipartisan-housing-bill-investors-ban	2026-03-12
WhiteHouse	2008963216630796516	CNBC	Big investors have been fleeing for-sale housing market	https://www.cnbc.com/2026/03/04/institutional-investors-housing-market.html	2026-03-04
WhiteHouse	2008963216630796516	Fortune	Banning institutional investors from buying homes will backfire, experts say	https://fortune.com/2026/03/15/institutional-investors-ban-single-family-home-sales-affordability-trump-senate-low-income-rent/	2026-03-15
WhiteHouse	2008963216630796516	Mayer Brown	US Senate Advances Housing Legislation Including Institutional Investor Ban	https://www.mayerbrown.com/en/insights/publications/2026/03/us-senate-advances-housing-legislation-that-includes-a-ban-on-institutional-investors-purchasing-single-family-homes	2026-03-12
WhiteHouse	2008963216630796516	Davis Polk	Congress weighs sweeping ban on institutional investor ownership	https://www.davispolk.com/insights/client-update/congress-weighs-sweeping-ban-institutional-investor-ownership-single-family	2026-03-12
WhiteHouse	2008963216630796516	American Banker	House drops institutional investor ban from housing bill	https://www.americanbanker.com/news/house-drops-institutional-investor-ban-from-housing-bill	2026-04-01
WhiteHouse	2008963216630796516	Brookings	The ripple effects of banning institutional purchases of single-family rentals	https://www.brookings.edu/articles/the-ripple-effects-of-banning-institutional-purchases-of-single-family-rentals/	2026-02-01
WhiteHouse	2008963216630796516	Urban Institute	Will Regulating Large Institutional Investors Actually Make Housing More Affordable?	https://www.urban.org/urban-wire/will-regulating-large-institutional-investors-actually-make-housing-more-affordable	2026-02-15
WhiteHouse	2055770869847281967	NBC News	Trump exit from global climate treaty leaves U.S. without a voice	https://www.nbcnews.com/science/climate-change/trump-exit-climate-treaty-us-no-voice-global-negotiations-rcna252980	2026-01-15
WhiteHouse	2055770869847281967	Al Jazeera	US to withdraw from dozens of UN, international organisations	https://www.aljazeera.com/news/2026/1/8/trump-to-withdraw-us-from-dozens-of-un-international-organisations	2026-01-08
WhiteHouse	2055770869847281967	Al Jazeera	Which are the 66 global organisations the US is leaving under Trump?	https://www.aljazeera.com/news/2026/1/8/which-are-the-66-global-organisations-the-us-is-leaving-under-trump	2026-01-08
WhiteHouse	2055770869847281967	The Energy Mix	U.S. Exits UN Climate Bodies, 66 International Organizations	https://www.theenergymix.com/breaking-u-s-exits-un-climate-bodies-66-international-organizations/	2026-01-09
WhiteHouse	2055770869847281967	World Resources Institute	STATEMENT: The United States Withdraws from the UNFCCC	https://www.wri.org/news/statement-united-states-withdraws-unfccc	2026-01-08
DHSgov	2008543534086029591	NBC News	How ICE raids in Minnesota connect to a years-old fraud scandal	https://www.nbcnews.com/tech/internet/ice-raids-minnesota-connect-years-old-fraud-scandal-rcna253151	2026-01-09
DHSgov	2008543534086029591	CNN	Minnesota, Twin Cities sue Trump administration over immigration operations	https://www.cnn.com/2026/01/12/us/minneapolis-immigration-officers-mobilizing-protests	2026-01-12
DHSgov	2008543534086029591	PBS News	Twin Cities on edge as ICE raids ignite fear and protests	https://www.pbs.org/newshour/show/twin-cities-on-edge-as-ice-raids-ignite-fear-and-protests	2026-01-12
DHSgov	2008543534086029591	ABC News	Minneapolis ICE shooting updates: Over 3,000 arrested in Minnesota	https://abcnews.com/US/live-updates/minneapolis-ice-shooting-live-updates/?id=129124338	2026-01-19
DHSgov	2008543534086029591	NBC News	Minneapolis schools cancel classes after ICE raid at high school	https://www.nbcnews.com/news/us-news/minneapolis-schools-cancel-classes-ice-raid-high-school-day-renee-nico-rcna253081	2026-01-08
DHSgov	2008543534086029591	NBC News	Crowds in Minneapolis confront federal agents as immigration enforcement ramps up	https://www.nbcnews.com/news/us-news/hundreds-federal-agents-are-headed-minnesota-noem-says-rcna253547	2026-01-09
WhiteHouse	2007501848358596656	CNN	January 3, 2026 - Maduro in US custody	https://www.cnn.com/world/live-news/venezuela-explosions-caracas-intl-hnk-01-03-26	2026-01-03
WhiteHouse	2007501848358596656	NPR	New details emerging on how the White House intends to run Venezuela	https://www.npr.org/2026/01/04/nx-s1-5666318/new-details-emerging-on-how-the-white-house-intends-to-run-venezuela	2026-01-04
WhiteHouse	2007501848358596656	NPR	We are going to run the country, Trump says after strike on Venezuela	https://www.npr.org/2026/01/03/g-s1-104329/explosions-caracas-venezuela	2026-01-03
WhiteHouse	2007501848358596656	NBC News	Trump says U.S. will govern Venezuela until proper transition	https://www.nbcnews.com/politics/white-house/trump-venezuela-nicolas-maduro-strikes-run-country-transition-military-rcna252044	2026-01-03
WhiteHouse	2007501848358596656	Roll Call	Press Conference: Donald Trump Discusses the Capture of Nicolas Maduro	https://rollcall.com/factbase/trump/transcript/donald-trump-press-conference-venezuela-maduro-january-3-2026/	2026-01-03
WhiteHouse	2007501848358596656	CBS News	U.S. special forces soldier charged for betting on Maduro removal	https://www.cbsnews.com/news/u-s-special-forces-won-409k-bet-maduro-removal-venezuela/	2026-01-15
WhiteHouse	2007501848358596656	Al Jazeera	Venezuela Maduro set to appear in US court months after abduction	https://www.aljazeera.com/news/2026/3/26/venezuelas-maduro-set-to-appear-in-us-court-months-after-abduction	2026-03-26
DHSgov	2037224777598042472	Fox News	ICE agent saves life of unresponsive 1-year-old boy in JFK airport	https://www.foxnews.com/us/ice-agent-saves-life-unresponsive-1-year-old-jfk-airport-panic-ensues-tsa-security-line	2026-03-26
DHSgov	2037224777598042472	Daily Wire	WATCH: ICE Agent Saves Baby Who Stopped Breathing In TSA Line	https://www.dailywire.com/news/watch-ice-agent-saves-baby-who-stopped-breathing-in-tsa-line	2026-03-26
DHSgov	2037224777598042472	The National Desk	ICE agent saves unresponsive baby at JFK Airport	https://thenationaldesk.com/top-videos/ice-agent-saves-unresponsive-baby-at-jfk-airport-dhs-says-heimlich-maneuver-choking-immigration-department-of-homeland-security-john-f-kennedy-international-airport-new-york-transportation-security-administration-tsa-government-shutdown	2026-03-26
DHSgov	2037224777598042472	MEAWW	ICE agent saves unresponsive 1-year-old at JFK airport	https://news.meaww.com/ice-agent-saves-unresponsive-1-year-old-at-jfk-airport-dhs-hails-heroic-action	2026-03-26
DHSgov	2037224777598042472	Newsweek	ICE agent at JFK Airport saved child's life	https://www.newsweek.com/jfk-airport-ice-agents-save-child-markwayne-mullin-11743392	2026-03-26
PressSec	2048411637690831288	Fox News	WHCA dinner attack puts security, political rhetoric under scrutiny	https://www.foxnews.com/live-news/security-rhetoric-under-microscope-aftermath-whca-dinner-attack-april-28	2026-04-28
PressSec	2048411637690831288	NBC News	White House Correspondents Dinner shooting suspect charged	https://www.nbcnews.com/news/us-news/live-blog/live-updates-correspondents-dinner-shooting-suspect-trump-writing-rcna342249	2026-04-27
PressSec	2048411637690831288	Fox News	Mentalist Oz Pearlman backs out of Kimmel after WHCA Dinner shooting	https://www.foxnews.com/media/mentalist-oz-pearlman-pulls-kimmel-guest-appearance-replaced-left-wing-podcaster	2026-04-28
PressSec	2048411637690831288	PBS News	Trumps call for ABC to fire Jimmy Kimmel again	https://www.pbs.org/newshour/politics/trumps-call-for-abc-to-fire-jimmy-kimmel-again-after-morbid-joke-about-first-lady	2026-04-28
PressSec	2048411637690831288	The Daily Beast	Melania Blames Husband's Sworn TV Enemy for WHCA Shooting	https://www.thedailybeast.com/melania-blames-husbands-sworn-tv-enemy-for-whca-shooting/	2026-04-28
PressSec	2048411637690831288	American Tribune	Mentalist Oz Pearlman drops Jimmy Kimmel appearance	https://americantribune.com/mentalist-oz-pearlman-drops-jimmy-kimmel-appearance-after-whca-dinner-shooting-and-melania-trump-joke-backlash/	2026-04-28
PressSec	2048411637690831288	Conservative Brief	Kimmel Faces Backlash After Trump Joke Before WHCA Shooting	https://conservativebrief.com/kimmel-whca-100772/	2026-04-28
WhiteHouse	2048857221824381171	Comicsands	White House Tried To Rebrand ICE Agents As NICE Agents	https://www.comicsands.com/trump-rebrands-nice-agents	2026-04-28
WhiteHouse	2048857221824381171	DNyuz	White House scorched over Orwellian word game to rebrand ICE	https://dnyuz.com/2026/04/28/embarrassment-white-house-scorched-over-orwellian-word-game-to-rebrand-ice/	2026-04-28
WhiteHouse	2048857221824381171	Tyla	Trump wants to rename ICE to NICE	https://www.tyla.com/news/politics/donald-trump-ice-rebrand-nice-agents-why-452690-20260428	2026-04-28
WhiteHouse	2048857221824381171	Raw Story	White House scorched over Orwellian word game to rebrand ICE	https://www.rawstory.com/ice-2676821486/	2026-04-28
WhiteHouse	2048857221824381171	Snopes	Did Trump endorse changing ICE's name to NICE?	https://www.snopes.com/fact-check/ice-nice-name-change/	2026-04-28
WhiteHouse	2048857221824381171	Yahoo News	Fact Check: Trump supported changing ICE's name to NICE	https://www.yahoo.com/news/articles/fact-check-trump-supported-changing-211300331.html	2026-04-28
WhiteHouse	2048857221824381171	The Daily Beast	Trump Reveals New Logo for Cringe-Inducing ICE Rebrand	https://www.thedailybeast.com/trump-reveals-new-logo-for-cringe-inducing-ice-rebrand/	2026-04-28
WhiteHouse	2048857221824381171	Yahoo News	Will Trump Make It Happen? Turning ICE Into NICE	https://www.yahoo.com/news/articles/trump-happen-turning-ice-nice-214833462.html	2026-04-28
StephenM	2039360279792795664	U.S. News & World Report	Supreme Court arguments over birthright citizenship order	https://www.usnews.com/news/world/articles/2026-04-01/the-latest-supreme-court-to-hear-arguments-over-trumps-birthright-citizenship-order	2026-04-01
StephenM	2039360279792795664	Al Jazeera	US Supreme Court hears birthright citizenship case	https://www.aljazeera.com/news/2026/4/1/hits-close-to-home-us-supreme-court-hears-birthright-citizenship-case	2026-04-01
StephenM	2039360279792795664	WTOP News	Supreme Court arguments over birthright citizenship order end	https://wtop.com/national/2026/04/the-latest-supreme-court-to-hear-arguments-over-trumps-birthright-citizenship-order/	2026-04-01
StephenM	2039360279792795664	Joe.My.God	Birthright Citizenship and inheritance	https://www.joemygod.com/2026/04/voldemort-birthright-citizenship-means-the-children-of-illegals-will-tax-and-seize-your-kids-inheritance/	2026-04-01
StephenM	2031921611415208357	Latin Times	Stephen Miller Argues Birthright Citizenship Steals the Actual Birthright	https://www.latintimes.com/stephen-miller-argues-birthright-citizenship-steals-actual-birthright-every-american-595600	2026-03-12
StephenM	2031921611415208357	Latin Times	Stephen Miller links birthright citizenship to remittance farming	https://www.latintimes.com/stephen-miller-says-migrant-claims-all-fake-links-birthright-citizenship-remittance-farming-596301	2026-03-18
StephenM	2031921611415208357	Fox News	Child born during international flight to US sparks citizenship debate	https://www.foxnews.com/travel/child-born-international-flight-us-sparks-heated-debate-about-citizenship-legal-identity	2026-03-12
StephenM	2031921611415208357	CNBC	Trump calls U.S. STUPID for birthright citizenship	https://www.cnbc.com/2026/04/01/trump-supreme-court-birthright-citizenship.html	2026-04-01
DHSgov	2015273624174023098	The Epoch Times	Protestor apprehended for using caltrops to deflate law enforcement tires	https://www.theepochtimes.com/us/protestor-apprehended-for-using-caltrops-to-deflate-tires-of-law-enforcement-vehicles-dhs-5976420	2026-01-24
USDOL	2005692451084726301	CNN	Parsing the rhetoric around Minnesota Somali child care fraud firestorm	https://www.cnn.com/2025/12/31/politics/trump-walz-minnesota-child-care-fraud	2025-12-31
USDOL	2005692451084726301	Fox News	Minnesota welfare fraud probe targets Somali hawala money transfers	https://www.foxnews.com/politics/minnesota-investigation-shadowy-money-system-somalis-rely-terrorists-can-exploit	2025-12-30
USDOL	2005692451084726301	Western Journal	Somali Fraud compared to Indian fraud scheme	https://www.westernjournal.com/somali-fraud-drop-bucket-compared-indian-fraud-scheme/	2025-12-31
USDOL	2005692451084726301	Heritage Foundation	Somali Welfare Fraud in Minnesota Has Cost Taxpayers Billions	https://www.heritage.org/welfare/commentary/somali-welfare-fraud-minnesota-has-cost-american-taxpayers-billions	2025-12-15
USDOL	2005692451084726301	Washington Post	Fraud schemes in Minneapolis undermine welfare programs	https://www.washingtonpost.com/opinions/2025/11/22/welfare-fraud-is-far-too-common/	2025-11-22
USDOL	2005692451084726301	Newsweek	Conventional Wisdom: Somali Minnesota Welfare Fraud Edition	https://www.newsweek.com/conventional-wisdom-somali-minnesota-welfare-fraud-trump-walz-11133825	2026-01-05
USDOL	2005692451084726301	Daily Wire	The Somali Welfare Fraud Scandal Is Even Worse Than You Think	https://www.dailywire.com/news/the-somali-welfare-fraud-scandal-is-even-worse-than-you-think	2025-11-29
StephenM	1881445859159941131	Townhall	Miller Tells Illegal Aliens Trying to Get Into US to Turn Back Now	https://townhall.com/tipsheet/jeremyfrankel/2025/01/21/stephen-miller-has-warning-for-illegal-immigrants-n2650875	2025-01-21
StephenM	1881445859159941131	CalMatters	Trump declares a border emergency on Day 1	https://calmatters.org/justice/2025/01/trump-border-orders-california-immigrants/	2025-01-21
StephenM	1905791054558744582	Rep. Chip Roy	Rep. Roy files articles of impeachment against Judge Deborah Boardman	https://roy.house.gov/media/press-releases/rep-roy-files-articles-impeachment-against-us-district-judge-deborah-l	2025-03-29
StephenM	1905791054558744582	Courthouse News Service	Republican lawmakers resurrect impeachment of DC Fed Judge Boasberg	https://www.courthousenews.com/republican-lawmakers-resurrect-impeachment-of-dc-fed-judge-boasberg/	2025-03-29
StephenM	1905791054558744582	Newsweek	Judge John McConnell Jr Faces Impeachment for Obstructing Trump	https://www.newsweek.com/judge-john-mcconnell-jr-faces-impeachment-obstructing-trump-2030510	2025-03-29
StephenM	2056837961006682327	Twitchy	Massie deletes date from old Trump quote	https://twitchy.com/justmindy/2026/05/19/massie-removes-date-from-trump-endorsement-n2428366	2026-05-19
StephenM	2056837961006682327	Spectrum News	Trump-backed Gallrein unseats Massie in Kentucky primary upset	https://spectrumnews1.com/ky/louisville/news/2026/05/20/trump-endorsement-carries-ed-gallrein-to-primary-win	2026-05-20
StephenM	2056837961006682327	CNBC	Kentucky Republican Thomas Massie is Trump revenge tour's next target	https://www.cnbc.com/2026/05/18/thomas-massie-primary-trump-kentucky-elections-2026-midterms.html	2026-05-18
StephenM	2056837961006682327	Townhall	Massie Doubles Down on Fake Trump Endorsement Text	https://townhall.com/tipsheet/josephchalfant/2026/05/19/massie-doubles-down-on-fake-trump-endorsement-text-after-backlash-n2676350	2026-05-19
StephenM	2056837961006682327	The Hill	Massie knocks Trump ballroom after primary loss	https://thehill.com/homenews/campaign/5886685-thomas-massie-donald-trump-ballroom-kentucky-primary-loss/	2026-05-20
StephenM	2056837961006682327	The Hill	Donald Trump scores major victory with Thomas Massie primary defeat	https://thehill.com/homenews/campaign/5883936-massie-loses-kentucky-house-primary/	2026-05-19
StephenM	2056837961006682327	Time	Massie primary defeat underscores Trump's hold on GOP	https://time.com/article/2026/05/19/massie-trump-kentucky-house-republican-primary-gallrein/	2026-05-19
StephenM	2056837961006682327	PBS News	Massie loss leaves no doubt about Trump's power over the GOP	https://www.pbs.org/newshour/politics/massies-loss-leaves-no-doubt-about-trumps-power-over-the-gop-6-takeaways-from-tuesdays-primaries	2026-05-20
WhiteHouse	2055492189115789463	Al Jazeera	Abu-Bilal al-Minuki: ISIL shadow commander in West Africa	https://www.aljazeera.com/news/2026/5/16/abu-bilal-al-minuki-isils-shadow-commander-in-west-africa	2026-05-16
WhiteHouse	2055492189115789463	The Washington Post	Top Islamic State leader killed in Nigeria strike	https://www.washingtonpost.com/world/2026/05/16/senior-isis-commander-killed-by-us-nigerian-forces-trump-says/	2026-05-16
WhiteHouse	2055492189115789463	Al Jazeera	ISIL second-in-command Abu-Bilal al-Minuki killed	https://www.aljazeera.com/news/2026/5/16/trump-says-isil-second-in-command-abu-bilal-al-minuki-killed	2026-05-16
WhiteHouse	2055492189115789463	The Globe and Mail	ISIS second in command killed in joint operation	https://www.theglobeandmail.com/world/article-isis-second-in-command-abu-bilal-al-minuki-killed-in-us-operation/	2026-05-16
WhiteHouse	2055492189115789463	Al Jazeera	US military carries out more strikes against ISIL fighters in Nigeria	https://www.aljazeera.com/news/2026/5/18/us-military-carries-out-more-strikes-against-isil-fighters-in-nigeria	2026-05-18
WhiteHouse	2055492189115789463	CSIS	The Killing of Abu-Bilal al-Minuki and U.S. involvement in Nigeria	https://www.csis.org/analysis/killing-abu-bilal-al-minuki-and-us-militarys-deepening-involvement-nigeria	2026-05-18
WhiteHouse	2030300999190040852	CBS News	Trump meets with Latin American leaders at Shield of the Americas Summit	https://www.cbsnews.com/news/trump-shield-of-the-americas-summit/	2026-03-07
WhiteHouse	2030300999190040852	NPR	What is Trump's Shield of Americas security initiative?	https://www.npr.org/2026/03/07/nx-s1-5739198/trumps-shield-of-the-americas	2026-03-07
WhiteHouse	2030300999190040852	Wilson Center	Key Takeaways from the 2026 Shield of the Americas Summit	https://www.wilsoncenter.org/article/key-takeaways-2026-shield-americas-summit	2026-03-10
WhiteHouse	2030300999190040852	CSIS	The Shield of Americas Gathering and counter-China strategy	https://www.csis.org/analysis/shield-americas-gathering-and-new-strategy-counter-china-western-hemisphere	2026-03-10
WhiteHouse	2030300999190040852	The National Interest	The Shield of the Americas Summit and Trump Latin America Strategy	https://nationalinterest.org/feature/the-shield-of-the-americas-summit-and-donald-trumps-latin-america-strategy	2026-03-09
WhiteHouse	2054358805199131027	Human Events	White House declares replacement migration will never be the standard	https://humanevents.com/2026/05/13/white-house-declares-replacement-migration-will-never-be-the-standard-under-trump-rejects-un-immigration-pact	2026-05-13
WhiteHouse	2054358805199131027	Fox News	Trump administration rejects UN migration declaration	https://www.foxnews.com/world/trump-administration-rejects-un-migration-declaration-says-mass-migration-never-safe	2026-05-12
WhiteHouse	2054358805199131027	GB News	US State Department blows lid on UN replacement plot	https://www.gbnews.com/politics/us/us-state-department-un-replacement-illegal-immigrants-deported-britain	2026-05-12
WhiteHouse	2054358805199131027	Newsweek	US rejects UN migration declaration over replacement immigration	https://www.newsweek.com/us-rejects-un-migration-replacement-claims-11939723	2026-05-12
WhiteHouse	2054358805199131027	Santa Monica Observer	US Accuses UN of Moving Migrants Into Europe and America	https://www.smobserved.com/story/2026/05/15/news/us-accuses-un-of-moving-migrants-into-europe-and-america-to-replace-citizens-refuses-to-implement-2018-global-compact-for-safe-orderly-and-regular-migration/9847.html	2026-05-15
WhiteHouse	2054358805199131027	The National	Washington accuses UN of enabling mass migration	https://www.thenationalnews.com/news/us/2026/05/12/washington-accuses-un-of-enabling-mass-migration-into-us-and-europe/	2026-05-12
PressSec	2048771378099159360	Fox News	Melania Trump calls for ABC to fire Jimmy Kimmel	https://www.foxnews.com/media/melania-trump-calls-abc-take-stand-against-jimmy-kimmel-over-hateful-violent-rhetoric	2026-04-27
WhiteHouse	2054607212513730674	CNN	Trump China state visit and meetings with Xi Jinping	https://www.cnn.com/politics/live-news/trump-china-visit-xi-meeting-hnk	2026-05-14
WhiteHouse	2054607212513730674	CNBC	Trump leaves China after talks dominated by trade and Taiwan	https://www.cnbc.com/2026/05/15/trump-wraps-up-two-day-china-trip-invites-xi-for-a-september-visit.html	2026-05-15
WhiteHouse	2054607212513730674	NPR	Trump lands in China as Iran war smolders	https://www.npr.org/2026/05/12/nx-s1-5818529/trump-china-iran-war	2026-05-12
WhiteHouse	2054607212513730674	NPR	Key takeaways from Trump's China trip	https://www.npr.org/2026/05/15/nx-s1-5822512/trump-china-xi-summit-takeaways	2026-05-15
WhiteHouse	2054893552249843720	CNN	Trump China state visit and meetings with Xi Jinping	https://www.cnn.com/politics/live-news/trump-china-visit-xi-meeting-hnk	2026-05-14
WhiteHouse	2054893552249843720	CNBC	Trump leaves China after talks dominated by trade and Taiwan	https://www.cnbc.com/2026/05/15/trump-wraps-up-two-day-china-trip-invites-xi-for-a-september-visit.html	2026-05-15
WhiteHouse	2051321953022042350	Axios	White House Star Wars Day post divides the galaxy	https://www.axios.com/2025/05/04/trump-white-house-star-wars-post	2025-05-04
WhiteHouse	2051321953022042350	Rolling Stone	Trump Co-Opts Star Wars and May the Fourth to Demonize Immigrants	https://www.rollingstone.com/politics/politics-news/trump-star-wars-may-fourth-memes-1235331075/	2025-05-04
WhiteHouse	2051321953022042350	Fox News	White House shares AI image of Trump with lightsaber	https://www.foxnews.com/politics/white-house-celebrates-star-wars-day-ai-image-muscular-trump-wielding-lightsaber	2025-05-04
WhiteHouse	2051321953022042350	Irish Star	Donald Trump humiliated over Star Wars White House post	https://www.irishstar.com/news/us-news/donald-trump-reminds-its-may-35169039	2025-05-04
WhiteHouse	2051321953022042350	The A.V. Club	White House wishes Radical Left Lunatics a happy May 4th	https://www.avclub.com/star-wars-day-white-house-donald-trump-jedi-ai	2025-05-04
WhiteHouse	2051321953022042350	Variety	White House Posts AI-Generated Image of Trump as a Buff Jedi	https://variety.com/2025/film/news/ai-generated-image-trump-buff-jedi-star-wars-day-1236386522/	2025-05-04
StephenM	2027929599154278542	CNN	Operation Epic Fury in Iran is over	https://www.cnn.com/2026/05/05/world/live-news/iran-war-news	2026-05-05
StephenM	2027929599154278542	CNN	Word of the Week: Operation Epic Fury	https://www.cnn.com/2026/03/04/us/word-of-week-epic-cec	2026-03-04
StephenM	2027929599154278542	Peoples Dispatch	US declares end of Operation Epic Fury	https://peoplesdispatch.org/2026/05/06/us-declares-end-of-operation-epic-fury-and-project-freedom-while-threatening-iran-in-talks/	2026-05-06
DHSgov	2049189253993681221	Minnesota Reformer	A timeline of Operation Metro Surge	https://minnesotareformer.com/2026/02/20/a-chronology-of-operation-metro-surge/	2026-02-20
DHSgov	2049189253993681221	Immigration Policy Tracking Project	DHS launches Operation Metro Surge in Minnesota	https://immpolicytracking.org/policies/dhs-launches-operation-metro-surge-in-minnesota/	2025-12-04
StephenM	2052565287136883044	The Hill	Republicans seek to shift blame for unpopular redistricting war	https://thehill.com/homenews/house/5883852-republicans-blame-redistricting-democrats/	2026-05-04
StephenM	2052565287136883044	Fox News	Democrat New England is the most gerrymandered region	https://www.foxnews.com/opinion/david-marcus-democrat-new-england-most-gerrymandered-region-american-history	2026-05-04
StephenM	2052565287136883044	WGBH	Is gerrymandering to blame for Massachusetts delegation?	https://www.wgbh.org/news/politics/2025-10-29/is-gerrymandering-to-blame-for-massachusetts-all-democrat-congressional-delegation	2025-10-29
DHSgov	2015115351797780500	NBC News	Border Patrol agents in Alex Pretti fatal shooting put on leave	https://www.nbcnews.com/news/us-news/live-blog/minneapolis-shooting-alex-pretti-live-updates-rcna256278	2026-01-25
DHSgov	2015115351797780500	NBC News	Border Patrol plans to reduce presence after Alex Pretti killed	https://www.nbcnews.com/news/us-news/live-blog/live-updates-alex-pretti-shooting-minneapolis-rcna255859	2026-01-26
DHSgov	2015115351797780500	Newsweek	Alex Pretti Shooting Update	https://www.newsweek.com/alex-pretti-shooting-minneapolis-border-agents-administrative-leave-11431567	2026-01-25
DHSgov	2015115351797780500	The Washington Post	DHS investigating body-cam footage related to Pretti shooting	https://www.washingtonpost.com/nation/2026/01/26/live-updates-minneapolis-shooting-alex-pretti-border-patrol/	2026-01-26
DHSgov	2015115351797780500	RiftTV	DHS Confirms Body-Cam Footage of Border Patrol Shooting Alex Pretti	https://www.rifttv.com/dhs-confirms-body-cam-footage-of-border-patrol-shooting-alex-pretti-in-minneapolis/	2026-01-26
DHSgov	2015115351797780500	CBS News	Minneapolis mayor responds after Homan ICE drawdown remarks	https://www.cbsnews.com/minnesota/live-updates/dhs-secretary-kristi-noem-under-scrutiny-bovino-exits-minnesota-after-alex-pretti-killing/	2026-01-26
DHSgov	2015115351797780500	NewsNation	ICE in Minneapolis: Greg Bovino relieved as commander-at-large	https://www.newsnationnow.com/us-news/immigration/border-coverage/person-dies-minneapolis-shooting-involving-border-patrol/	2026-01-26
DHSgov	2015115351797780500	CNN	Top Border Patrol official Bovino expected to leave Minneapolis	https://edition.cnn.com/us/live-news/minneapolis-shooting-ice-protests-01-26-26	2026-01-26
DHSgov	2009427948541993323	KOIN	What is Tren de Aragua? Portland shooting connection	https://www.koin.com/news/national/what-is-tren-de-aragua/	2026-01-10
DHSgov	2009427948541993323	Latin Times	Portland Couple Shot by Border Patrol Identified as Venezuelans	https://www.latintimes.com/portland-couple-shot-border-patrol-identified-venezuelans-dhs-links-them-tren-de-aragua-593437	2026-01-10
DHSgov	2009427948541993323	MyNorthwest	2 people shot by federal agents in Portland	https://mynorthwest.com/local/federal-agents-in-portland/4184578	2026-01-09
DHSgov	2009427948541993323	IBTimes	Portland Couple Shot by Border Patrol Identified as Venezuelans	https://www.ibtimes.com/portland-couple-shot-border-patrol-identified-venezuelans-dhs-links-them-tren-de-aragua-3794929	2026-01-10
DHSgov	2009427948541993323	WHIO	2 shot by Border Patrol agents near Portland hospital identified	https://www.whio.com/news/trending/2-shot-by-border-patrol-agents-near-portland-hospital/3R7CWMFRJZFOBG5QZ4D5MZRITI/	2026-01-10
DHSgov	2009427948541993323	The New American	TdA Member Charged With Assault for Attacking Border Patrol in Portland	https://thenewamerican.com/us/tda-member-charged-with-assault-for-attacking-border-patrol-in-portland/	2026-01-10
StephenM	1899981583592947918	Fortune	Radical rogue judges targeted by Trump administration	https://fortune.com/2025/03/17/radical-rogue-judges-targeted-trump-administration-legal-setbacks/	2025-03-17
StephenM	1899981583592947918	Roll Call	Trump pushes back against judges who rule against him	https://rollcall.com/2025/05/08/trump-pushes-back-against-the-judges-who-rule-against-him/	2025-05-08
DHSgov	1949913619644493930	The Bulwark	Bizarre DHS Social-Media Strategy	https://www.thebulwark.com/p/bizarre-dhs-social-media-strategy-homeland-security-propaganda-white-nationalist	2025-08-01
DHSgov	1949913619644493930	MS.now	DHS is quoting Bible verses to defend deporting migrants	https://www.ms.now/opinion/msnbc-opinion/dhs-bible-verses-videos-deportations-rcna223009	2025-08-01
DHSgov	1998909254854746523	CNN	Pentagon watchdog evaluating strikes on alleged drug boats	https://www.cnn.com/2026/05/19/politics/pentagon-watchdog-strikes-drug-boats	2026-05-19
DHSgov	1998909254854746523	USNI News	SOUTHCOM Strike on Suspected Drug Boat Kills 6	https://news.usni.org/2026/03/09/southcom-strike-on-suspected-drug-boat-kills-6	2026-03-09
DHSgov	2042326493406138806	Rafu Shimpo	Japanese Artist Objects to DHS Use of His Work	https://rafu.com/2026/01/japanese-artist-objects-to-dhs-use-of-his-work/	2026-04-11
DHSgov	2042326493406138806	Raw Story	DHS caught using artist work for MAGA agenda without permission	https://www.rawstory.com/trump-dhs-2674844068/	2026-04-11
DHSgov	2042326493406138806	Common Dreams	Trump DHS Post Calling for 100 Million Deportations	https://www.commondreams.org/news/dhs-100-million-deportations	2026-04-11
DHSgov	2042326493406138806	NextShark	Japanese artist blasts DHS for using his art in deportation post	https://nextshark.com/japanese-artist-blasts-dhs-deportation	2026-04-11
DHSgov	2042326493406138806	The Daily Beast	Artist accuses DHS of stealing his work	https://www.thedailybeast.com/artist-accuses-dhs-of-stealing-his-work-to-plug-vile-deportations/	2026-04-11
WhiteHouse	2040244118706602390	Fox News	White House marks Holy Week with days of prayer	https://www.foxnews.com/politics/white-house-marks-holy-week-easter-days-prayer-centered-religious-liberty	2026-04-04
StephenM	1902170071063056527	NPR	Federal judge says U.S. must give due process to deported Venezuelans	https://www.npr.org/2025/12/22/nx-s1-5652187/alien-enemies-act-deportations-case	2025-12-22
StephenM	1902170071063056527	The Hill	Boasberg orders return of Venezuelan deportees	https://thehill.com/regulation/court-battles/5660475-trump-administration-venezuelan-deportees-return-ruling/	2025-12-22
StephenM	1902170071063056527	Washington Examiner	Appeals court blocks Boasberg contempt inquiry	https://www.washingtonexaminer.com/news/justice/4528250/boasberg-contempt-inquiry-deportations-blocked/	2026-04-01
StephenM	2026496327744360856	Fox News	Thune calls out two Americas after SOTU	https://www.foxnews.com/politics/thune-calls-out-two-americas-democrats-refuse-stand-war-heroes-law-enforcement-sotu	2026-02-25
StephenM	2026496327744360856	Breitbart	Democrats Refuse When Asked to Stand Up for Americans	https://www.breitbart.com/politics/2026/02/24/watch-democrats-refuse-to-stand-when-asked-to-stand-up-for-american-citizens/	2026-02-24
StephenM	2026496327744360856	Majority Leader	Scalise on SOTU: The American People Saw Who Stands with Them	https://www.majorityleader.gov/news/documentsingle.aspx?DocumentID=5856	2026-02-25
StephenM	2026524666286788765	Fox News	Thune calls out two Americas after SOTU	https://www.foxnews.com/politics/thune-calls-out-two-americas-democrats-refuse-stand-war-heroes-law-enforcement-sotu	2026-02-25
WhiteHouse	2040644451513598220	Time	How a U.S. Airman Shot Down in Iran Was Rescued	https://time.com/article/2026/04/05/-safe-and-sound-how-a-u-s-airman-shot-down-in-iran-was-rescued-from-a-mountain-crevice/	2026-04-05
WhiteHouse	2040644451513598220	CNN	Inside the mission to recover a downed American airman	https://www.cnn.com/2026/04/05/politics/american-airman-rescue-mission-trump-iran	2026-04-05
WhiteHouse	2040644451513598220	The Maine Wire	Trump Announces Successful Rescue of U.S. Colonel Behind Enemy Lines	https://www.themainewire.com/2026/04/trump-announces-successful-rescue-of-u-s-colonel-behind-enemy-lines-in-iran/	2026-04-05
WhiteHouse	2040644451513598220	KGOU	Trump provides details behind dramatic rescue of American airman	https://www.kgou.org/world/2026-04-06/trump-provides-details-behind-the-dramatic-rescue-of-an-american-airman-trapped-in-iran	2026-04-06
WhiteHouse	2040644451513598220	New Hampshire Public Radio	Trump provides details behind dramatic rescue of American airman	https://www.nhpr.org/2026-04-06/trump-provides-details-behind-the-dramatic-rescue-of-an-american-airman-trapped-in-iran	2026-04-06
WhiteHouse	2040644451513598220	WUSF	Trump provides details behind dramatic rescue of American airman	https://www.wusf.org/2026-04-06/trump-provides-details-behind-the-dramatic-rescue-of-an-american-airman-trapped-in-iran	2026-04-06
StephenM	2040761953518264828	Time	How a U.S. Airman Shot Down in Iran Was Rescued	https://time.com/article/2026/04/05/-safe-and-sound-how-a-u-s-airman-shot-down-in-iran-was-rescued-from-a-mountain-crevice/	2026-04-05
StephenM	2040761953518264828	CNN	Inside the mission to recover a downed American airman	https://www.cnn.com/2026/04/05/politics/american-airman-rescue-mission-trump-iran	2026-04-05
WhiteHouse	2052773177785229762	Fox News	Trump admin releases files documenting UFOs	https://www.foxnews.com/politics/trump-admin-releases-highly-anticipatedfiles-documents-ufos-extraterrestrial-life	2026-05-08
WhiteHouse	2052773177785229762	Fox News	Second batch of UFO files set to be released	https://www.foxnews.com/us/second-batch-ufo-files-set-released-lawmaker-teased-holy-crap-moment	2026-05-15
WhiteHouse	2052773177785229762	Christian Science Monitor	Declassified UFO files reopen transparency debate	https://www.csmonitor.com/USA/Society/2026/0519/ufo-secret-file-release-alien-trump	2026-05-19
WhiteHouse	2052773177785229762	CNN	Pentagon releases initial batch of declassified files detailing UFOs	https://www.cnn.com/2026/05/08/politics/ufo-files-pentagon-release-aliens	2026-05-08
WhiteHouse	2049581451809620135	CNN	Trump welcomes Artemis II astronauts to Oval Office	https://www.cnn.com/2026/04/29/politics/astronauts-artemis-iran-trump-oval-office	2026-04-29
WhiteHouse	2049581451809620135	Space.com	Trump invited Artemis 2 astronauts to the White House	https://www.space.com/space-exploration/artemis/trump-invited-the-artemis-2-moon-astronauts-to-the-oval-office-heres-what-happened	2026-04-29
WhiteHouse	2049581451809620135	UPI	Artemis II crew visits with Trump in the Oval Office	https://www.upi.com/Top_News/US/2026/04/29/artemis-ii-crew-trump/8771777490859/	2026-04-29
WhiteHouse	2049581451809620135	CNN	Trump welcomes Artemis II astronauts to Oval Office	https://www.cnn.com/2026/04/29/science/video/artemis-ii-trump-oval-office-vrtc	2026-04-29
WhiteHouse	2049581451809620135	13WHAM	Artemis II crew visits Oval Office	https://13wham.com/news/nation-world/president-donald-trump-to-welcome-artemis-ii-crew-at-white-house-oval-office-after-historic-lunar-moon-flyby-mission-nasa-astronauts-wiseman-glover-koch-hansen	2026-04-29
WhiteHouse	2053581347084501106	NBC News	Iran-U.S. peace talks deadlocked after Trump rejects proposal	https://www.nbcnews.com/world/iran/iran-us-peace-talks-trump-rejects-totally-unacceptable-hormuz-rcna344501	2026-05-11
WhiteHouse	2053581347084501106	The Hill	Donald Trump calls Iran proposal totally unacceptable	https://thehill.com/homenews/administration/5871825-trump-iran-proposal-totally-unacceptable/	2026-05-11
WhiteHouse	2053581347084501106	Al Jazeera	What is Iran peace proposal that Trump rejected?	https://www.aljazeera.com/news/2026/5/11/unacceptable-whats-irans-peace-proposal-that-trump-has-rejected	2026-05-11
WhiteHouse	2053581347084501106	PBS News	Trump calls Iran response unacceptable	https://www.pbs.org/newshour/world/trump-calls-irans-response-to-ceasefire-proposal-unacceptable	2026-05-11
WhiteHouse	2053581347084501106	NPR	Trump calls Iran latest response totally unacceptable	https://www.npr.org/2026/05/11/nx-s1-5817535/trump-calls-irans-latest-response-to-u-s-ceasefire-proposal-totally-unacceptable	2026-05-11
WhiteHouse	2053581347084501106	ABC News	Trump calls Iran latest response totally unacceptable	https://abcnews.com/International/live-updates/iran-live-updates-ukmto-reports-attacks-2-ships/?id=132626582	2026-05-11
WhiteHouse	2053581347084501106	CNN	Trump issues warning to Iran after national security team meeting	https://www.cnn.com/2026/05/17/politics/iran-strikes-trump-china	2026-05-17
WhiteHouse	2053581347084501106	RT	Trump rejects Iran peace offer as totally unacceptable	https://www.rt.com/news/639845-trump-iran-totally-unacceptable/	2026-05-11
WhiteHouse	2056058474954436923	Axios	Trump warns Iran clock is ticking	https://www.axios.com/2026/05/17/trump-iran-warning-harder-strikes	2026-05-17
WhiteHouse	2056058474954436923	Fox News	Trump warns Iran clock is ticking	https://www.foxnews.com/politics/trump-warns-irans-clock-ticking-move-fast-there-wont-anything-left	2026-05-17
WhiteHouse	2056058474954436923	The Hill	Trump warns Iran clock is ticking as negotiations stall	https://thehill.com/homenews/administration/5882193-trump-iran-clock-ticking/	2026-05-17
WhiteHouse	2055697898130551229	NPR	Trump says he called off Iran strike at request of Gulf allies	https://www.npr.org/2026/05/19/g-s1-122762/trump-says-hes-called-off-iran-strike	2026-05-19
WhiteHouse	2055697898130551229	CNBC	Trump postponing scheduled attack of Iran	https://www.cnbc.com/2026/05/18/trump-iran-attack-saudi-uae-qatar-deal.html	2026-05-18
WhiteHouse	2055697898130551229	World Socialist Web Site	After Trump China trip, White House plans new attack on Iran	https://www.wsws.org/en/articles/2026/05/18/lqbt-m18.html	2026-05-18
GregoryKBovino	2053206305004028087	Border Hawk	Feds Spending Millions on HIV Healthcare for Illegal Aliens in Oklahoma	https://borderhawk.news/exclusive-feds-spending-millions-on-hiv-healthcare-for-illegal-aliens-in-oklahoma-whistleblower/	2026-05-09
GregoryKBovino	2053206305004028087	The Post Millennial	Oklahoma clinic using federal cash to treat illegal immigrants for HIV	https://thepostmillennial.com/oklahoma-state-university-clinic-using-federal-cash-to-treat-illegal-immigrants-for-hiv-report	2026-05-09
GregoryKBovino	2053206305004028087	Tickle The Wire	Former Border Patrol Commander Threatens Rogue Deportation Effort	https://ticklethewire.com/former-border-patrol-commander-threatens-rogue-deportation-effort/	2026-05-12
RealTomHoman	1726353860610974192	Washington Examiner	Tom Homan touts record deportations	https://www.washingtonexaminer.com/policy/immigration/4575734/tom-homan-insists-ice-not-narrowing-deportation-agenda/	2026-05-15
RealTomHoman	1726353860610974192	CNN	Defiant border czar Tom Homan says mass deportations are coming	https://www.cnn.com/2026/05/05/politics/tom-homan-border-security-deportations	2026-05-05
RealTomHoman	1726353860610974192	Axios	Trump copies Obama playbook on counting deportations	https://www.axios.com/2026/05/21/trump-obama-deportations-statistics	2026-05-21
PressSec	1891613906621100044	Newsweek	Elon Musk DOGE Tried to Fire Air Traffic Controllers	https://www.newsweek.com/elon-musks-doge-tried-fire-air-traffic-controllers-report-2041505	2025-02-17
PressSec	1891613906621100044	Townhall	Associated Press Busted for Story About FAA Layoffs	https://townhall.com/tipsheet/mattvespa/2025/02/19/the-associated-press-busted-for-peddling-fake-news-about-the-faa-and-the-toronto-plane-crash-n2652465	2025-02-19
PressSec	1891613906621100044	RedState	Karoline Leavitt Sets AP Straight on FAA Firings and DOGE	https://redstate.com/sister-toldjah/2025/02/17/fake-news-karoline-leavitt-sets-the-associated-press-straight-in-story-about-faa-firings-and-doge-n2185698	2025-02-17
PressSec	1891613906621100044	Twitchy	Karoline Leavitt Slams AP Story Over DOGE Facebook Account	https://twitchy.com/warren-squire/2025/02/17/karoline-leavitt-slams-ap-tara-copp-over-bon-existent-doge-fb-page-and-other-lies-n2408491	2025-02-17
StephenM	2027356805773353054	WJLA	Emails show Fairfax police warned prosecutor about suspect in Hybla Valley killing	https://wjla.com/news/local/illegal-immigrant-with-long-criminal-record-accused-of-killing-woman-in-fairfax-county-abdul-emails-jalloh-stephanie-minter-commonwealths-attorney-steve-descano	2026-02-26
StephenM	2027356805773353054	WJLA	DHS says man accused of Fairfax County bus stop killing resided illegally	https://wjla.com/news/local/fairfax-county-dhs-bus-stop-killing-illegally-sierra-leon-steve-descano-jalloh-crime-richmond-highway-fredericksburg-arrest-homeland-security	2026-02-28
StephenM	2027356805773353054	NBC Washington	Fairfax police emails warned about man suspected of killing woman at bus stop	https://www.nbcwashington.com/news/local/northern-virginia/fairfax-county-police-emails-warned-about-stabbing-suspect-in-womans-death/4070910/	2026-02-26
StephenM	2027356805773353054	Newsweek	Fury As Immigrant With Over 30 Arrests Allegedly Kills Woman At Bus Stop	https://www.newsweek.com/virginia-bus-stop-stabbing-11610829	2026-02-26
StephenM	2027356805773353054	FOX 5 DC	Virginia family calls for more accountability after repeat offender kills woman	https://www.fox5dc.com/news/virginia-family-calls-more-accountability-after-repeat-offender-kills-woman-bus-stop	2026-02-27
StephenM	2027356805773353054	WJLA	Family of murdered mother pushing for recall of Fairfax prosecutor	https://wjla.com/news/local/family-murdered-mother-recall-fairfax-county-prosecutor-stephanie-minter-steve-descano-abdul-jalloh	2026-03-01
StephenM	2027356805773353054	Front Page Detectives	Virginia Mom Fatally Stabbed at Bus Stop	https://www.frontpagedetectives.com/p/virginia-mom-fatally-stabbed-at-bus-stop-by-illegal-immigrant-with-over-30-prior-arrests-police-say	2026-02-26
StephenM	2041693793733189706	CBS Miami	Haitian man faces deportation after Fort Myers gas station attack	https://www.cbsnews.com/miami/news/haitian-man-deportation-fort-myers-gas-station-hammer-attack/	2026-04-09
StephenM	2041693793733189706	KWTX	Illegal immigrant from Haiti charged in deadly Florida gas station attack	https://www.kwtx.com/2026/04/10/illegal-immigrant-haiti-charged-deadly-attack-florida-mother-gas-station/	2026-04-10
StephenM	2041693793733189706	RedState	FL Haitian protected by TPS bludgeoned a gas station attendant	https://redstate.com/jenniferoo/2026/04/07/fl-haitian-who-bludgeoned-a-gas-station-attendant-was-protected-by-biden-under-tps-n2201057	2026-04-07
StephenM	2041693793733189706	CBS12	Undocumented immigrant accused of killing mother with hammer	https://cbs12.com/news/florida/undocumented-immigrant-accused-of-killing-mother-with-hammer-at-florida-gas-station-florida-news-ice-us-immigration-and-customs-enforcement-department-of-homeland-security-haitian-man-fort-myers-police-department-crime-arrest-ice-detainer	2026-04-08
PressSec	2043750850779042147	Fox News	Gabbard says declassified testimony exposes plot behind impeachment	https://www.foxnews.com/politics/gabbard-claims-coordinated-effort-intelligence-community-advance-narrative-impeach-trump	2026-04-13
PressSec	2043750850779042147	Newsweek	DNI Denies CIA Raided Tulsi Gabbard Office	https://www.newsweek.com/dni-denies-cia-tulsi-gabbard-raid-11949426	2026-04-14
PressSec	2043750850779042147	CBS News	Gabbard releases more Russia documents	https://www.cbsnews.com/news/gabbard-releases-russia-documents-concerns-intelligence-sources/	2026-04-13
StephenM	2038778363393843351	NPR	What to know about Trump's future presidential library	https://www.npr.org/2026/03/31/nx-s1-5768094/trump-presidential-library-renderings-miami	2026-03-31
StephenM	2038778363393843351	CNN	Trump shares renderings of a towering presidential library	https://www.cnn.com/2026/03/30/politics/trump-library-renderings-miami	2026-03-30
StephenM	2038778363393843351	Fox News	Trump proposed presidential library revealed	https://www.foxnews.com/politics/trump-proposed-presidential-library-revealed-towering-miami-skyscraper-striking-new-video	2026-03-30
PressSec	2034302387523883128	CNN	Fact check: Trump does not have 100 percent approval among Republicans	https://www.cnn.com/2026/05/06/politics/fact-check-trump-approval-rating	2026-05-06
PressSec	2034302387523883128	Comicsands	Leavitt Dragged Over MAGA 100 percent Approval Of Trump	https://www.comicsands.com/leavitt-trump-100-percent-approval	2026-03-19
PressSec	2034302387523883128	Yahoo News	CNN Data Guru Hits Trump With a Reality Check	https://www.yahoo.com/news/articles/cnn-data-guru-hits-trump-091916628.html	2026-03-19
StephenM	1905783803731493011	Fortune	Radical rogue judges targeted by Trump administration	https://fortune.com/2025/03/17/radical-rogue-judges-targeted-trump-administration-legal-setbacks/	2025-03-17
StephenM	1906024686674133371	CNN	Trump describes US as an occupied country in closing message	https://www.cnn.com/2024/11/04/politics/donald-trump-closing-message	2024-11-04
StephenM	1906024686674133371	Yahoo News	Trump describes US as an occupied country in closing message	https://www.yahoo.com/news/trump-describes-us-occupied-country-022238117.html	2024-11-04
StephenM	1884366093533241734	National Memo	Is Funding Freeze A Media Hoax Or A Gift To Terrorists?	https://www.nationalmemo.com/stephen-miller-2671028246	2025-01-29
StephenM	1884366093533241734	Alternet	Stephen Miller slammed after calling OMB funding freeze a media hoax	https://www.alternet.org/the-right-wing/stephen-miller-omb/	2025-01-29
StephenM	1884366093533241734	NAFSA	OMB Memorandum on Temporary Pause of Federal Funding	https://www.nafsa.org/regulatory-information/omb-memorandum-temporary-pause-agency-grant-loan-and-other-financial	2025-01-28
StephenM	1884366093533241734	Mayer Brown	Updates and Summary of Federal Funding Freeze	https://www.mayerbrown.com/en/insights/publications/2025/02/updates-and-summary-of-the-evolving-executive-federal-funding-freeze	2025-02-03
StephenM	1884366093533241734	AAMC	OMB Issues and Rescinds Memo Ordering Pause of Federal Funding	https://www.aamc.org/advocacy-policy/washington-highlights/omb-issues-and-rescinds-memo-ordering-pause-federal-funding	2025-01-30
WhiteHouse	2042402293446767065	NBC News	Trump vows to pause migration from third world countries	https://www.nbcnews.com/politics/politics-news/trump-pause-migration-third-world-countries-national-guard-shooting-dc-rcna246299	2025-11-27
WhiteHouse	2042402293446767065	Al Jazeera	Trump pauses immigration from Third World countries	https://www.aljazeera.com/news/2025/11/28/trump-pauses-immigration-from-third-world-countries-what-that-means	2025-11-28
WhiteHouse	2042402293446767065	TIME	Trump Anti-Immigration Tirade Sparks Major Backlash	https://time.com/7339768/trump-anti-immigration-speech-backlash/	2025-11-29
WhiteHouse	2042402293446767065	PBS News	Trump vows to stop immigration from poorer countries	https://www.pbs.org/newshour/show/trump-vows-to-stop-immigration-from-poorer-countries-after-fatal-national-guard-shooting	2025-11-28
WhiteHouse	2042402293446767065	CBS News	Trump says he will suspend immigration from all Third World Countries	https://www.cbsnews.com/news/trump-says-he-will-suspend-immigration-from-all-third-world-countries/	2025-11-27
WhiteHouse	2042402293446767065	Newsweek	Map Shows 19 Countries Impacted as Trump Threatens Migration Halt	https://www.newsweek.com/map-19-countries-impacted-trump-threatens-migration-halt-11127499	2025-11-29
WhiteHouse	2040507389812568086	NBC News	Trump vows to pause migration from third world countries	https://www.nbcnews.com/politics/politics-news/trump-pause-migration-third-world-countries-national-guard-shooting-dc-rcna246299	2025-11-27
WhiteHouse	2040507389812568086	TIME	Trump Anti-Immigration Tirade Sparks Major Backlash	https://time.com/7339768/trump-anti-immigration-speech-backlash/	2025-11-29
USDOL	2010141673389769214	Krishnamoorthi	Congressman Krishnamoorthi condemns extremist rhetoric in DHS and DOL communications	https://krishnamoorthi.house.gov/media/press-releases/congressman-krishnamoorthi-leads-38-house-democrats-condemning-white	2025-09-25
USDOL	2010141673389769214	PBS News	Trump administration posts echo rhetoric linked to extremist groups	https://www.pbs.org/newshour/show/trump-administration-posts-echo-rhetoric-linked-to-extremist-groups	2025-09-26
USDOL	2010141673389769214	Yahoo News	Trump Labor Department slogan draws comparisons	https://www.yahoo.com/news/articles/trump-labor-departments-chilling-slogan-072825243.html	2025-09-25
USDOL	2010141673389769214	Jewish Democratic Council	Nazi Slogans and White Nationalist Anthems	https://jewishdems.org/nazi-slogans-and-white-nationalist-anthems/	2025-09-26
StephenM	2036400784594629097	Fox News	Resurfaced Murphy clip fuels conservative outrage	https://www.foxnews.com/politics/senators-resurfaced-comment-on-who-democrats-care-about-the-most-sparks-online-outrage-he-really-said-it	2026-03-24
StephenM	2036400784594629097	Yahoo News	Senator resurfaced comment sparks online outrage	https://www.yahoo.com/news/articles/senators-resurfaced-democrats-care-most-161856637.html	2026-03-24
StephenM	2036400784594629097	930 WFMD	Senator resurfaced comment sparks online outrage	https://www.wfmd.com/2026/03/24/senators-resurfaced-comment-on-who-democrats-care-about-the-most-sparks-online-outrage-he-really-said-it/	2026-03-24
RapidResponse47	2019052764555358602	Daily Signal	700 Immigration Agents Are Leaving Minnesota	https://www.dailysignal.com/2026/02/04/homan-700-immigration-agents-leaving-minnesota-after-unprecedented-cooperation/	2026-02-04
RapidResponse47	2019052764555358602	RedState	Homan Nukes ICE Retreat Narrative With Drawdown Announcement	https://redstate.com/terichristoph/2026/02/04/tom-homan-announces-draw-down-at-latest-presser-n2198817	2026-02-04
RapidResponse47	2019052764555358602	Western Journal	Tom Homan Announces Significant Personnel Draw Down in Minneapolis	https://www.westernjournal.com/tom-homan-announces-significant-personnel-draw-minneapolis-thanks-unprecedented-cooperation-local-officials/	2026-02-04
RapidResponse47	2019052764555358602	Raw Story	Trump will draw down 700 DHS officers in Minneapolis	https://www.rawstory.com/homan-minneapolis-draw-down/	2026-02-04
RapidResponse47	2019052764555358602	PJ Media	Tom Homan Pulls 700 Agents Out of Minnesota	https://pjmedia.com/matt-margolis/2026/02/04/tom-homan-pulls-700-agents-out-of-minnesota-heres-why-thats-bad-news-for-the-left-n4949092	2026-02-04
RapidResponse47	2016872709121049056	America First Report	Tom Homan Announces ICE Drawdown in Minneapolis	https://americafirstreport.com/tom-homan-announces-ice-drawdown-in-minneapolis-with-promise-of-unprecedented-cooperation-from-local-law-enforcement/	2026-02-04
RapidResponse47	2016872709121049056	Daily Signal	Tom Homan: What Must Happen in Minnesota Before Withdrawal	https://www.dailysignal.com/2026/01/29/border-czar-reveals-what-must-happen-in-minnesota-before-ice-withdrawal/	2026-01-29
""".strip()


def status_links(handle: str, tweet_id: str) -> list[str]:
    return [
        f"https://x.com/{handle}/status/{tweet_id}",
        f"https://twitter.com/{handle}/status/{tweet_id}",
        f"https://x.com/i/web/status/{tweet_id}",
        f"https://twitter.com/i/web/status/{tweet_id}",
    ]


def iter_records() -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    seen: set[tuple[str, str, str]] = set()
    for line_no, line in enumerate(RAW_ROWS.splitlines(), start=1):
        parts = line.split("\t")
        if len(parts) != 6:
            raise ValueError(f"bad row {line_no}: expected 6 tab-separated fields")
        handle, tweet_id, source, title, url, published_at = (part.strip() for part in parts)
        key = (tweet_id, url, title)
        if key in seen:
            continue
        seen.add(key)
        records.append(
            {
                "source": source,
                "title": title,
                "url": url,
                "canonical_url": url,
                "published_at": published_at,
                "links": status_links(handle, tweet_id),
                "match_method": "google-exact-status-url-search",
                "coverage_basis": "search_result_for_exact_status_url",
                "discovered_at": "2026-05-22",
            }
        )
    return records


def main() -> None:
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    records = iter_records()
    with OUT_PATH.open("w", encoding="utf-8", newline="\n") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=True, sort_keys=True) + "\n")
    print(f"Wrote {len(records)} articles to {OUT_PATH}")


if __name__ == "__main__":
    main()
