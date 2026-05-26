// Display-only tag hierarchy helpers.
//
// The tagger emits flat tags because filters/search work best that way. The
// viewer can still show narrower tags tucked beneath their implied parent so
// a row reads like a small outline instead of a bag of unrelated labels.

const EXACT_PARENTS = new Map([
  ['action:deportation', ['topic:immigration']],
  ['action:self-deportation', ['topic:immigration']],
  ['action:report-immigrants', ['topic:immigration']],
  ['agency:ICEgov', ['topic:immigration']],
  ['agency:CBP', ['topic:immigration']],
  ['agency:DHSgov', ['topic:immigration']],
  ['agency:HSI_HQ', ['topic:immigration']],
  ['agency:USBPChief', ['topic:immigration']],
  ['agency:DeptofWar', ['topic:military']],
  ['agency:CENTCOM', ['topic:military']],
  ['agency:Southcom', ['topic:military']],
  ['agency:USCG', ['topic:military']],
  ['agency:USArmyNorth', ['topic:military']],
  ['agency:USNationalGuard', ['topic:military']],
  ['agency:USNorthernCmd', ['topic:military']],
  ['theme:criminal', ['topic:immigration']],
  ['genre:lineup', ['topic:immigration']],
  ['subject:angel-family', ['theme:martyrdom', 'topic:immigration']],
  ['subject:crime-victim', ['theme:martyrdom', 'topic:immigration']],
  ['subject:cbp-home-app', ['topic:immigration']],
  ['subject:enforcement-op', ['topic:immigration']],
  ['policy:border', ['topic:immigration']],
  ['policy:cbp-home', ['topic:immigration']],
  ['policy:sanctuary-cities', ['topic:immigration']],
  ['policy:worksite-enforcement', ['topic:economy', 'topic:immigration']],
  ['theme:nativism', ['topic:immigration']],
  ['theme:pop-culture-reference', ['topic:immigration']],
  ['religion:christianity', ['theme:religion']],
  ['slogan:criminal-illegal-alien', ['topic:immigration']],
  ['slogan:free-ticket-home', ['topic:immigration']],
  ['slogan:go-home', ['topic:immigration']],
  ['slogan:illegal-alien', ['topic:immigration']],
  ['slogan:mass-deportation', ['topic:immigration']],
  ['slogan:masa', ['topic:immigration']],
  ['slogan:find-and-kill', ['topic:immigration']],
  ['slogan:import-third-world', ['topic:immigration']],
  ['slogan:most-secure-border', ['topic:immigration', 'policy:border']],
  ['slogan:catch-release', ['topic:immigration']],
  ['slogan:project-homecoming', ['topic:immigration']],
  ['legal:birthright-citizenship', ['topic:immigration']],
  ['genre:parody', ['theme:pop-culture-reference']],
  ['phrase:immigrant', ['topic:immigration']],
  ['phrase:migrant', ['topic:immigration']],
  ['slogan:maga', ['topic:general']],
  ['slogan:maha', ['topic:general']],
  ['slogan:america-first', ['topic:general']],
  ['slogan:golden-age', ['topic:general']],
  ['slogan:save-america', ['topic:general']],
  ['slogan:law-and-order', ['topic:general']],
  ['slogan:peace-through-strength', ['topic:general']],
  ['slogan:promises-kept', ['topic:general']],
  ['event:los-angeles-disturbance', ['theme:civil-disturbance']],
  ['event:minneapolis-disturbance', ['theme:civil-disturbance']],
  ['event:portland-disturbance', ['theme:civil-disturbance']],
  ['video:produced', ['media:video']],
  ['crime:murder', ['crime:homicide']],
  ['crime:rape', ['crime:sexual']],
  ['crime:sodomy', ['crime:sexual']],
  ['crime:child-sexual', ['crime:sexual']],
  ['crime:perjury', ['crime:disobedience']],
  ['crime:fentanyl', ['crime:narcotics']],
  ['crime:cocaine', ['crime:narcotics']],
  ['crime:meth', ['crime:narcotics']],
]);

const PREFIX_PARENTS = [
  ['military:', ['topic:military']],
  ['origin:', ['topic:immigration']],
  ['country:', ['topic:immigration']],
];

const TAG_ALIASES = new Map([
  ['media:produced-video', 'video:produced'],
  ['homicide:murder', 'crime:murder'],
  ['shape:lineup', 'genre:lineup'],
  ['branch:army', 'military:army'],
  ['branch:navy', 'military:navy'],
  ['branch:air-force', 'military:air-force'],
  ['branch:space-force', 'military:space-force'],
  ['branch:marines', 'military:marines'],
  ['branch:coast-guard', 'military:coast-guard'],
  ['branch:national-guard', 'military:national-guard'],
  // Namespace-migration aliases
  ['frame:criminal', 'theme:criminal'],
  ['media:montage', 'video:montage'],
  ['media:text-overlay', 'video:text-overlay'],
  ['media:voiceover', 'video:voiceover'],
  ['media:music-video', 'genre:music-video'],
  ['media:short-video', 'video:short'],
  ['media:archived', 'media-status:archived'],
  ['media:described', 'media-status:described'],
  ['media:has-alt-text', 'media-status:has-alt-text'],
  ['media:needs-vision', 'media-status:needs-vision'],
  ['media:needs-ocr', 'media-status:needs-ocr'],
  ['media:graphic-content', 'media-status:graphic-content'],
  ['theme:border', 'policy:border'],
  ['theme:sanctuary-cities', 'policy:sanctuary-cities'],
  ['theme:worksite-enforcement', 'policy:worksite-enforcement'],
  ['theme:cbp-home', 'policy:cbp-home'],
  ['theme:statistics', 'format:statistics'],
  ['format:directive', 'theme:directive'],
]);

export function tagEntryName(entry) {
  const name = typeof entry === 'string' ? entry : entry?.tag;
  return TAG_ALIASES.get(name) ?? name;
}

export function tagNamespaceFor(name) {
  return String(name || '').split(':', 1)[0] || 'tag';
}

export function uniqueTagEntries(tags) {
  const seen = new Set();
  const out = [];
  for (const entry of tags) {
    const name = tagEntryName(entry);
    if (!name || seen.has(name)) continue;
    seen.add(name);
    out.push(entry);
  }
  return out;
}

export function tagTreeFromEntries(tags) {
  const entries = uniqueTagEntries(Array.isArray(tags) ? tags : []);
  const byName = new Map(entries.map((entry) => [tagEntryName(entry), entry]));
  const childNames = new Set();
  const childrenByParent = new Map();

  for (const entry of entries) {
    const name = tagEntryName(entry);
    const parent = parentTagFor(name, byName);
    if (!parent) continue;
    childNames.add(name);
    const children = childrenByParent.get(parent) ?? [];
    children.push(entry);
    childrenByParent.set(parent, children);
  }

  return entries
    .filter((entry) => !childNames.has(tagEntryName(entry)))
    .map((entry) => {
      const name = tagEntryName(entry);
      return {
        entry,
        name,
        namespace: tagNamespaceFor(name),
        children: childrenByParent.get(name) ?? [],
      };
    });
}

function parentTagFor(name, byName) {
  if (!name) return null;
  const exact = EXACT_PARENTS.get(name);
  if (exact) return firstPresent(exact, byName);

  for (const [prefix, parents] of PREFIX_PARENTS) {
    if (name.startsWith(prefix)) return firstPresent(parents, byName);
  }
  return null;
}

function firstPresent(candidates, byName) {
  return candidates.find((candidate) => byName.has(candidate)) ?? null;
}
