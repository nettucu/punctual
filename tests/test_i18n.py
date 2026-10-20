import re
from pathlib import Path

import pytest

from punctual import i18n
from punctual.settings import settings

SRC = Path(__file__).parent.parent / "src" / "punctual"
STATIC = SRC / "static"
EN, RO = i18n.catalog("en"), i18n.catalog("ro")
PLACEHOLDER = re.compile(r"\{(\w+)\}")
DIACRITICS = re.compile("[ăâîșțĂÂÎȘȚşţ]")


def test_catalogs_have_the_same_keys_and_placeholders():
    assert EN.keys() == RO.keys()
    for key in EN:
        assert set(PLACEHOLDER.findall(EN[key])) == set(PLACEHOLDER.findall(RO[key])), (
            key
        )


def test_every_used_key_exists():
    pattern = re.compile(r"""\bt\(\s*f?['"]([\w.]+)['"]""")
    attr = re.compile(r'data-i18n(?:-ph|-title|-aria)?="([\w.]+)"')
    used: set[str] = set()
    for path in [*SRC.glob("*.py"), *STATIC.glob("*.html"), *STATIC.glob("*.js")]:
        text = path.read_text(encoding="utf-8")
        used |= set(pattern.findall(text)) | set(attr.findall(text))
    missing = [
        k
        for k in used
        if not (k in EN or (k.endswith(".") and any(e.startswith(k) for e in EN)))
    ]
    assert not missing


def test_static_text_matches_the_english_catalog():
    """Markup keeps the English text as a no-JS fallback; it must not drift from the catalog."""
    tag = re.compile(r"<(\w+)([^>]*?)>([^<]*)")
    for path in STATIC.glob("*.html"):
        if ".ro." in path.name:
            continue
        for _, attrs, text in tag.findall(path.read_text(encoding="utf-8")):
            if m := re.search(r'data-i18n="([\w.]+)"', attrs):
                assert text.strip() == EN[m[1]], (path.name, m[1])
            for name, target in (
                ("ph", "placeholder"),
                ("title", "title"),
                ("aria", "aria-label"),
            ):
                if m := re.search(rf'data-i18n-{name}="([\w.]+)"', attrs):
                    assert (
                        re.search(rf'(?<![\w-]){target}="([^"]*)"', attrs)[1]
                        == EN[m[1]]
                    ), (path.name, m[1])


def test_romanian_lives_only_in_its_catalog_and_manuals():
    offenders = [
        p.name
        for p in SRC.rglob("*")
        if p.is_file()
        and p.suffix in {".py", ".html", ".js", ".json", ".css"}
        and p.name != "ro.json"
        and ".ro." not in p.name
        and DIACRITICS.search(p.read_text(encoding="utf-8"))
    ]
    assert not offenders


@pytest.mark.parametrize("locale", i18n.LOCALES)
def test_i18n_js_serves_the_active_language(client, monkeypatch, locale):
    monkeypatch.setattr(settings, "locale", locale)
    r = client.get("/i18n.js")
    assert r.status_code == 200 and f'window.LOCALE="{locale}"' in r.text
    assert i18n.t("login.submit") == i18n.catalog(locale)["login.submit"]


def test_help_pages_follow_the_locale(client, monkeypatch):
    for locale in i18n.LOCALES:
        monkeypatch.setattr(settings, "locale", locale)
        assert f'<html lang="{locale}">' in client.get("/help").text
        assert f'<html lang="{locale}">' in client.get("/admin/help").text
