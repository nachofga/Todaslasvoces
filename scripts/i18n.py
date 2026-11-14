#!/usr/bin/env python
"""
Traducciones sin necesidad de instalar gettext.

    python scripts/i18n.py            # busca textos nuevos, actualiza el .po y compila el .mo
    python scripts/i18n.py missing    # lista los textos que faltan por traducir al inglés

El español es el idioma de origen: los textos están escritos en español en el
código y en las plantillas. Las traducciones al inglés viven en
tlv/locale/en/LC_MESSAGES/django.po, que se puede editar a mano o con Poedit.
"""
import ast
import re
import sys
from pathlib import Path

import polib

ROOT = Path(__file__).resolve().parent.parent
PO = ROOT / "tlv" / "locale" / "en" / "LC_MESSAGES" / "django.po"
FUNCS = {"_", "gettext", "gettext_lazy", "N_", "gettext_noop"}

TRANS = re.compile(r"""{%\s*trans\s+(?:"((?:[^"\\]|\\.)*)"|'((?:[^'\\]|\\.)*)')""")
INLINE = re.compile(r"""_\(\s*(?:"((?:[^"\\]|\\.)*)"|'((?:[^'\\]|\\.)*)')\s*\)""")
BLOCK = re.compile(r"{%\s*blocktrans(?:late)?\b(.*?)%}(.*?){%\s*endblocktrans(?:late)?\s*%}", re.S)
VAR = re.compile(r"{{\s*(\w+)\s*}}")


def _block(text):
    text = VAR.sub(r"%(\1)s", text)
    return " ".join(line.strip() for line in text.strip().splitlines() if line.strip())


def from_templates():
    found = {}
    for path in sorted((ROOT / "tlv" / "templates").rglob("*")):
        if path.suffix not in (".html", ".txt"):
            continue
        src = path.read_text(encoding="utf-8")
        for m in BLOCK.finditer(src):
            body = m.group(2)
            if "{% plural %}" in body:
                one, many = body.split("{% plural %}")
                found[_block(one)] = _block(many)
            else:
                found.setdefault(_block(body), None)
        src_no_blocks = BLOCK.sub("", src)
        for rx in (TRANS, INLINE):
            for m in rx.finditer(src_no_blocks):
                found.setdefault((m.group(1) or m.group(2)).replace('\\"', '"'), None)
    return found


def from_python():
    found = {}
    for path in sorted((ROOT / "tlv").rglob("*.py")):
        if "migrations" in path.parts or "tests" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", "")) in FUNCS and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    found.setdefault(arg.value, None)
    return found


def collect():
    found = from_templates()
    for k, v in from_python().items():
        found.setdefault(k, v)
    return found


def load():
    if PO.exists():
        return polib.pofile(str(PO))
    po = polib.POFile()
    po.metadata = {
        "Project-Id-Version": "todaslasvoces", "Language": "en", "MIME-Version": "1.0",
        "Content-Type": "text/plain; charset=UTF-8", "Content-Transfer-Encoding": "8bit",
        "Plural-Forms": "nplurals=2; plural=(n != 1);",
    }
    return po


def sync():
    po = load()
    found = collect()
    existing = {e.msgid: e for e in po}
    for msgid, plural in found.items():
        entry = existing.get(msgid)
        if entry is None:
            entry = polib.POEntry(msgid=msgid)
            po.append(entry)
        entry.obsolete = False
        if plural and not entry.msgid_plural:
            entry.msgid_plural = plural
            entry.msgstr_plural = {0: entry.msgstr or "", 1: ""}
            entry.msgstr = ""
    for entry in po:
        if entry.msgid not in found:
            entry.obsolete = True
    PO.parent.mkdir(parents=True, exist_ok=True)
    po.save(str(PO))
    po.save_as_mofile(str(PO.with_suffix(".mo")))
    return po


def missing(po):
    out = []
    for e in po:
        if e.obsolete:
            continue
        if (e.msgid_plural and not all(e.msgstr_plural.get(i) for i in (0, 1))) or (not e.msgid_plural and not e.msgstr):
            out.append(e)
    return out


if __name__ == "__main__":
    po = sync()
    todo = missing(po)
    total = len([e for e in po if not e.obsolete])
    if len(sys.argv) > 1 and sys.argv[1] == "missing":
        for i, e in enumerate(todo, 1):
            print(f"{i}|{e.msgid}" + (f" ||| {e.msgid_plural}" if e.msgid_plural else ""))
    elif len(sys.argv) > 2 and sys.argv[1] == "fill":
        # Uso interno: rellena las que faltan desde un fichero «número|traducción».
        lines = [l for l in Path(sys.argv[2]).read_text(encoding="utf-8").splitlines() if "|" in l]
        ph = re.compile(r"%\((\w+)\)s")
        bad = 0
        for line in lines:
            num, text = line.split("|", 1)
            entry = todo[int(num) - 1]
            if entry.msgid_plural:
                one, many = [t.strip() for t in text.split("|||")]
                entry.msgstr_plural = {0: one, 1: many}
                text = one
            else:
                entry.msgstr = text
            if set(ph.findall(entry.msgid)) != set(ph.findall(text)):
                bad += 1
                print("VARIABLES DISTINTAS:", num, entry.msgid[:60], "->", text[:60])
        po.save(str(PO))
        po.save_as_mofile(str(PO.with_suffix(".mo")))
        print(f"{len(lines)} traducciones cargadas, {bad} con variables distintas; faltan {len(missing(po))}")
    else:
        print(f"{total} textos, {len(todo)} sin traducir. Compilado: {PO.with_suffix('.mo').name}")
