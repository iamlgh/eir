"""
Dynamically verifies that every localization string containing a %{placeholder}
actually gets substituted correctly by i18n.t(), for every locale.

Assumes JSON locale files named <basename>.<locale>.json living in
path_handler('i18n_path'), with no locale key inside the file itself
(the locale is derived from the filename), and %{name} placeholder syntax, e.g.:

    app.success_notifications_unlinked.en.json:
        { "success_notifications_unlinked": "Success! %{service_name} notifications have been unlinked." }
"""

import json
import re
from pathlib import Path

import i18n
import pytest
from shared import path_handler

i18n.load_path.append(path_handler('i18n_path'))
i18n.set('file_format', 'json')
i18n.set('skip_locale_root_data', True)
i18n.set('fallback', 'en')

# Matches <basename>.<locale>.json, e.g. "app.en.json" -> basename="app", locale="en"
FILENAME_PATTERN = re.compile(r'^(?P<basename>.+)\.(?P<locale>[a-zA-Z_-]+)\.json$')

# Matches what python-i18n actually parses as a placeholder: %{word_chars}
STRICT_PLACEHOLDER = re.compile(r'%\{(\w+)\}')

# Matches anything that *looks* like a placeholder, including invisible/odd
# characters between the braces (zero-width space, non-breaking space, etc.)
LOOSE_PLACEHOLDER = re.compile(r'%\{([^}]*)\}')

# Placeholder names that need a real int, not a string (e.g. used for
# pluralization or numeric formatting). Extend as you hit more of these.
NUMERIC_PLACEHOLDER_NAMES = {'count', 'num', 'number', 'amount', 'total'}

# A dict whose keys are a subset of these is treated as a plural entry,
# not a nested namespace. Adjust if your library uses different category
# names (this project appears to use a simplified one/many scheme).
PLURAL_FORM_KEYS = {'zero', 'one', 'two', 'few', 'many', 'other'}

# count value used to trigger each plural category when testing. Adjust if
# your i18n library's plural-selection rule differs (e.g. locale-specific
# CLDR rules rather than a simple one/many split).
PLURAL_TEST_COUNTS = {
    'zero': 0,
    'one': 1,
    'two': 2,
    'few': 3,
    'many': 5,
    'other': 5,
}


def _synthetic_value(name):
    if name.lower() in NUMERIC_PLACEHOLDER_NAMES:
        return 2
    return f'TEST_{name.upper()}'


def _flatten(data, prefix, locale, out):
    for k, v in data.items():
        key = f'{prefix}.{k}' if prefix else k
        if isinstance(v, dict):
            if v and set(v.keys()) <= PLURAL_FORM_KEYS:
                out[(locale, key)] = v  # plural entry: keep as dict, don't recurse
            else:
                _flatten(v, key, locale, out)
        elif isinstance(v, str):
            out[(locale, key)] = v


def _load_locale_strings():
    """Returns {(locale, dotted.key): raw_string} for every string in every locale file."""
    strings = {}
    i18n_dir = Path(path_handler('i18n_path'))
    for filepath in i18n_dir.glob('*.json'):
        match = FILENAME_PATTERN.match(filepath.name)
        if not match:
            continue  # skip files that don't match <basename>.<locale>.json
        locale = match.group('locale')
        basename = match.group('basename')
        with open(filepath, encoding='utf-8') as f:
            data = json.load(f)
        _flatten(data, prefix=basename, locale=locale, out=strings)
    return strings


ALL_STRINGS = _load_locale_strings()


@pytest.mark.parametrize(
    'locale,key,raw',
    [(loc, k, v) for (loc, k), v in ALL_STRINGS.items()],
    ids=[f'{loc}:{k}' for (loc, k) in ALL_STRINGS.keys()],
)
def test_placeholder_integrity_and_render(locale, key, raw):
    if isinstance(raw, dict):
        _check_plural_entry(locale, key, raw)
    else:
        _check_string_entry(locale, key, raw)


def _check_string_entry(locale, key, raw):
    loose_matches = LOOSE_PLACEHOLDER.findall(raw)
    strict_matches = STRICT_PLACEHOLDER.findall(raw)

    # If loose and strict disagree, something non-word-character is sitting
    # inside the braces -- almost certainly a hidden/invisible character.
    assert loose_matches == strict_matches, (
        f'{locale}:{key} has a placeholder containing unexpected characters '
        f'(likely invisible/hidden unicode). Raw: {raw!r} '
        f'loose={loose_matches!r} strict={strict_matches!r}'
    )

    if not strict_matches:
        return  # plain string, nothing to substitute

    kwargs = {name: _synthetic_value(name) for name in strict_matches}
    rendered = i18n.t(key, locale=locale, **kwargs)

    # No literal placeholder syntax should survive rendering
    assert '%{' not in rendered, (
        f'{locale}:{key} left an unsubstituted placeholder after render: {rendered!r} (raw was {raw!r}, kwargs were {kwargs!r})'
    )

    # Every synthetic value should actually appear in the rendered output
    for name, value in kwargs.items():
        assert str(value) in rendered, f"{locale}:{key} did not substitute '{name}' correctly. Rendered: {rendered!r}, raw: {raw!r}"


def _check_plural_entry(locale, key, forms):
    for form_name, form_raw in forms.items():
        loose_matches = LOOSE_PLACEHOLDER.findall(form_raw)
        strict_matches = STRICT_PLACEHOLDER.findall(form_raw)

        assert loose_matches == strict_matches, (
            f'{locale}:{key}.{form_name} has a placeholder containing unexpected '
            f'characters (likely invisible/hidden unicode). Raw: {form_raw!r} '
            f'loose={loose_matches!r} strict={strict_matches!r}'
        )

        count = PLURAL_TEST_COUNTS.get(form_name)
        if count is None:
            continue  # unknown category, nothing sensible to test with

        # count itself always needs to be an int for pluralization to trigger
        kwargs = {'count': count}
        kwargs.update({name: _synthetic_value(name) for name in strict_matches if name != 'count'})

        rendered = i18n.t(key, locale=locale, **kwargs)

        assert rendered != key, (
            f'{locale}:{key} rendered as its own key name — likely a missing translation or misconfigured i18n.load_path, not a placeholder bug'
        )

        assert '%{' not in rendered, (
            f'{locale}:{key} (form={form_name}, count={count}) left an '
            f'unsubstituted placeholder after render: {rendered!r} '
            f'(raw was {form_raw!r}, kwargs were {kwargs!r})'
        )

        for name, value in kwargs.items():
            if name not in strict_matches:
                continue  # e.g. count wasn't actually used in this particular form
            assert str(value) in rendered, (
                f"{locale}:{key} (form={form_name}, count={count}) did not substitute '{name}' correctly. Rendered: {rendered!r}, raw: {form_raw!r}"
            )
