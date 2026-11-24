#!/usr/bin/env python3
"""Report missing and outdated translations in the TEI P5 specs as Markdown.

Reads P5/Source/Specs/*.xml directly (no build needed). For every English
<gloss>, <desc> and <remarks> it checks whether a sibling in the target
language exists, and whether that translation's @versionDate is older than
the English one's. Same rules as P5/Utilities/generate_i18n_spec_lists.xsl,
but reported per element instead of per spec. Missing <remarks> are listed
in a separate, lower-priority section.

Usage (from anywhere inside the TEI repo):
    python3 translation_status.py                    # German -> translation-status-de.md
    python3 translation_status.py --lang fr -o fr.md
"""

import argparse
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

from lxml import etree

TEI = "http://www.tei-c.org/ns/1.0"
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
TRANSLATABLE = ("gloss", "desc", "remarks")
SPEC_TYPES = {
    "elementSpec": "Elements",
    "classSpec": "Classes",
    "macroSpec": "Macros",
    "dataSpec": "Datatypes",
}
GITHUB_BLOB = "https://github.com/TEIC/TEI/blob/dev/P5/Source/Specs"


def repo_root() -> Path:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=True,
        )
        return Path(out.stdout.strip())
    except (subprocess.CalledProcessError, FileNotFoundError):
        return Path.cwd()


def local(el) -> str:
    return etree.QName(el).localname


def parse_date(value):
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def location(el, spec) -> str:
    """Human-readable path from the spec down to the element's parent,
    e.g. 'att.global / @rend' or 'gap / @reason / "cancelled"'."""
    parts = []
    node = el.getparent()
    while node is not None and node is not spec:
        ident = node.get("ident")
        if ident is not None:
            name = local(node)
            if name == "attDef":
                parts.append(f"@{ident}")
            elif name == "valItem":
                parts.append(f'"{ident}"')
            else:
                parts.append(f"{name} {ident}")
        node = node.getparent()
    return " / ".join([spec.get("ident")] + list(reversed(parts)))


def check_spec(path: Path, lang: str):
    """Yield one finding dict per problem in this spec file."""
    tree = etree.parse(str(path))
    spec = tree.getroot()
    if local(spec) not in SPEC_TYPES:
        return

    # Group translatable children by parent, then by element name.
    parents = defaultdict(lambda: defaultdict(lambda: {"en": [], lang: []}))
    for el in spec.iter(*(f"{{{TEI}}}{n}" for n in TRANSLATABLE)):
        el_lang = el.get(XML_LANG, "en")  # specs are English unless marked
        if el_lang in ("en", lang):
            parents[el.getparent()][local(el)][el_lang].append(el)

    for parent, by_name in parents.items():
        for name, versions in by_name.items():
            en_list, tr_list = versions["en"], versions[lang]
            base = {
                "spec": spec.get("ident"),
                "type": local(spec),
                "file": path.name,
                "element": name,
            }
            if not en_list:
                # Translation without English source (cf. TEIC/TEI#2037)
                for tr in tr_list:
                    yield {**base, "kind": "no-english", "line": tr.sourceline,
                           "where": location(tr, spec)}
                continue
            # Pair English and translation by position.
            for i, en in enumerate(en_list):
                f = {**base, "line": en.sourceline, "where": location(en, spec),
                     "en_date": en.get("versionDate")}
                if i >= len(tr_list):
                    yield {**f, "kind": "missing"}
                    continue
                tr = tr_list[i]
                f["tr_date"] = tr.get("versionDate")
                en_d, tr_d = parse_date(f["en_date"]), parse_date(f["tr_date"])
                if tr_d is None:
                    yield {**f, "kind": "undated", "line": tr.sourceline}
                elif en_d is not None and tr_d < en_d:
                    yield {**f, "kind": "outdated", "line": tr.sourceline}


def link(f) -> str:
    return f"[{f['file']}#L{f['line']}]({GITHUB_BLOB}/{f['file']}#L{f['line']})"


def render(findings, lang: str, spec_counts: Counter, commit: str) -> str:
    def is_remarks_missing(f):
        return f["kind"] == "missing" and f["element"] == "remarks"

    by_kind = defaultdict(list)
    for f in findings:
        by_kind[f["kind"]].append(f)
    missing_main = [f for f in by_kind["missing"] if not is_remarks_missing(f)]
    missing_remarks = [f for f in by_kind["missing"] if is_remarks_missing(f)]

    out = [f"# Translation status: `{lang}`", ""]
    out.append(f"Generated on {date.today().isoformat()} from TEIC/TEI"
               + (f" at commit `{commit}`" if commit else "") + ".")
    out.append("")
    out.append("- **Missing**: English gloss/desc with no translation. "
               "These show up in editor tooltips and at the top of every reference page.")
    out.append("- **Missing remarks**: English remarks with no translation. "
               "Longer usage notes further down the reference pages; lower priority.")
    out.append("- **Outdated**: translation's `@versionDate` is older than the English one's.")
    out.append("- **Undated**: translation exists but has no `@versionDate` "
               "(counted as missing by the upstream XSLT).")
    out.append("- **No English**: translation without an English counterpart (see TEIC/TEI#2037).")
    out.append("")

    # Summary per spec type
    def counts(fs):
        k = Counter(f["kind"] for f in fs)
        rem = sum(1 for f in fs if is_remarks_missing(f))
        return k["missing"] - rem, rem, k["outdated"], k["undated"], k["no-english"]

    out.extend(["## Summary", "",
                "| Type | Specs | Specs with issues | Missing (gloss/desc) | Missing remarks "
                "| Outdated | Undated | No English |",
                "|---|---:|---:|---:|---:|---:|---:|---:|"])
    for t, label in SPEC_TYPES.items():
        fs = [f for f in findings if f["type"] == t]
        cells = " | ".join(str(n) for n in counts(fs))
        out.append(f"| {label} | {spec_counts[t]} | {len({f['spec'] for f in fs})} | {cells} |")
    cells = " | ".join(f"**{n}**" for n in counts(findings))
    out.append(f"| **Total** | **{sum(spec_counts.values())}** | "
               f"**{len({f['spec'] for f in findings})}** | {cells} |")
    out.append("")

    def table(title, rows, with_dates):
        if not rows:
            return
        out.extend([f"## {title} ({len(rows)})", ""])
        if with_dates:
            out.extend(["| Where | Element | English date | Translation date | Source |",
                        "|---|---|---|---|---|"])
        else:
            out.extend(["| Where | Element | Source |", "|---|---|---|"])
        for f in sorted(rows, key=lambda f: (f["spec"].lower(), f["line"])):
            if with_dates:
                out.append(f"| {f['where']} | {f['element']} | {f.get('en_date') or '–'} | "
                           f"{f.get('tr_date') or '–'} | {link(f)} |")
            else:
                out.append(f"| {f['where']} | {f['element']} | {link(f)} |")
        out.append("")

    def checklist(title, rows, intro=None):
        """Missing entries grouped by spec, since there are many."""
        if not rows:
            return
        out.extend([f"## {title} ({len(rows)})", ""])
        if intro:
            out.extend([intro, ""])
        grouped = defaultdict(list)
        for f in rows:
            grouped[(f["type"], f["spec"])].append(f)
        for t, label in SPEC_TYPES.items():
            specs = sorted((s for (tt, s) in grouped if tt == t), key=str.lower)
            if not specs:
                continue
            out.extend([f"### {label} ({len(specs)} specs)", ""])
            for s in specs:
                items = sorted(grouped[(t, s)], key=lambda f: f["line"])
                details = ", ".join(
                    f"{f['element']}"
                    + (f" ({f['where'].split(' / ', 1)[1]})" if " / " in f["where"] else "")
                    for f in items)
                out.append(f"- [ ] **{s}**: {details} – {link(items[0])}")
            out.append("")

    table("Outdated", by_kind["outdated"], with_dates=True)
    table("Undated", by_kind["undated"], with_dates=True)
    table("No English counterpart", by_kind["no-english"], with_dates=False)
    checklist("Missing", missing_main)
    checklist("Missing remarks (lower priority)", missing_remarks,
              intro="Usage notes on the reference pages. Translate these after the "
                    "gloss/desc of the same spec.")

    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lang", default="de", help="target language code (default: de)")
    ap.add_argument("--specs", type=Path, help="spec directory (default: <repo>/P5/Source/Specs)")
    ap.add_argument("-o", "--output", type=Path,
                    help="output file (default: translation-status-<lang>.md)")
    args = ap.parse_args()

    root = repo_root()
    specs_dir = args.specs or root / "P5" / "Source" / "Specs"
    if not specs_dir.is_dir():
        sys.exit(f"Spec directory not found: {specs_dir}")
    output = args.output or Path(f"translation-status-{args.lang}.md")

    findings, spec_counts = [], Counter()
    for path in sorted(specs_dir.glob("*.xml")):
        try:
            spec_type = local(etree.parse(str(path)).getroot())
        except etree.XMLSyntaxError as e:
            print(f"Skipping {path.name}: {e}", file=sys.stderr)
            continue
        if spec_type in SPEC_TYPES:
            spec_counts[spec_type] += 1
            findings.extend(check_spec(path, args.lang))

    try:
        commit = subprocess.run(["git", "-C", str(specs_dir), "rev-parse", "--short", "HEAD"],
                                capture_output=True, text=True, check=True).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        commit = ""

    output.write_text(render(findings, args.lang, spec_counts, commit), encoding="utf-8")
    k = Counter(f["kind"] for f in findings)
    rem = sum(1 for f in findings if f["kind"] == "missing" and f["element"] == "remarks")
    print(f"Wrote {output}: {k['missing'] - rem} missing gloss/desc, {rem} missing remarks, "
          f"{k['outdated']} outdated, "
          f"{k['undated']} undated, {k['no-english']} without English")


if __name__ == "__main__":
    main()