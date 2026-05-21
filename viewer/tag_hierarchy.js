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
  ['frame:criminal', ['topic:immigration']],
  ['shape:lineup', ['topic:immigration']],
  ['subject:angel-family', ['topic:immigration']],
  ['subject:cbp-home-app', ['topic:immigration']],
  ['subject:enforcement-op', ['topic:immigration']],
  ['theme:border', ['topic:immigration']],
  ['theme:cbp-home', ['topic:immigration']],
  ['theme:nativism', ['topic:immigration']],
  ['theme:pop-culture-enforcement', ['topic:immigration']],
  ['theme:sanctuary-cities', ['topic:immigration']],
  ['theme:worksite-enforcement', ['topic:economy', 'topic:immigration']],
  ['slogan:criminal-illegal-alien', ['topic:immigration']],
  ['slogan:free-ticket-home', ['topic:immigration']],
  ['slogan:go-home', ['topic:immigration']],
  ['slogan:illegal-alien', ['topic:immigration']],
  ['slogan:mass-deportation', ['topic:immigration']],
  ['slogan:project-homecoming', ['topic:immigration']],
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
]);

const PREFIX_PARENTS = [
  ['branch:', ['topic:military']],
  ['origin:', ['topic:immigration']],
  ['country:', ['topic:immigration']],
];

export function tagEntryName(entry) {
  return typeof entry === 'string' ? entry : entry?.tag;
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

  if (name.startsWith('homicide:')) {
    return firstPresent(['crime:homicide', 'crime:murder'], byName);
  }

  for (const [prefix, parents] of PREFIX_PARENTS) {
    if (name.startsWith(prefix)) return firstPresent(parents, byName);
  }
  return null;
}

function firstPresent(candidates, byName) {
  return candidates.find((candidate) => byName.has(candidate)) ?? null;
}
