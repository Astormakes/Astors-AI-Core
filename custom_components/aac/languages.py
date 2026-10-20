"""Languages: the home's list, names, codes and which language a text is in.

The home's languages are one global list (owner, 2026-10-20: "a setup step and a global thing that modules use
as well"), first = primary. Until the owner sets it, it comes from HA: HA's language, then the country's
languages; English if HA says nothing. Detection guesses only among them, and the audio modules decide what to
do with a line in another language.

Codes are ISO 639-1 (de, en, …). Whisper servers report either the code (faster-whisper) or the English name
(whisper.cpp: "german"); code_of() takes both, and TTS locales such as de_DE. Text detection: py3langid
(~97 languages, offline, ~0.1 ms per sentence). One-word answers are unreliable ("Done." → it) unless it
chooses among the home's languages.
"""

from __future__ import annotations

from typing import Any

# Whisper's languages (the ones AAC can hear), code → English name
NAMES = {
    "af": "Afrikaans", "am": "Amharic", "ar": "Arabic", "as": "Assamese", "az": "Azerbaijani", "ba": "Bashkir",
    "be": "Belarusian", "bg": "Bulgarian", "bn": "Bengali", "bo": "Tibetan", "br": "Breton", "bs": "Bosnian",
    "ca": "Catalan", "cs": "Czech", "cy": "Welsh", "da": "Danish", "de": "German", "el": "Greek", "en": "English",
    "es": "Spanish", "et": "Estonian", "eu": "Basque", "fa": "Persian", "fi": "Finnish", "fo": "Faroese",
    "fr": "French", "gl": "Galician", "gu": "Gujarati", "ha": "Hausa", "haw": "Hawaiian", "he": "Hebrew",
    "hi": "Hindi", "hr": "Croatian", "ht": "Haitian Creole", "hu": "Hungarian", "hy": "Armenian", "id": "Indonesian",
    "is": "Icelandic", "it": "Italian", "ja": "Japanese", "jw": "Javanese", "ka": "Georgian", "kk": "Kazakh",
    "km": "Khmer", "kn": "Kannada", "ko": "Korean", "la": "Latin", "lb": "Luxembourgish", "ln": "Lingala",
    "lo": "Lao", "lt": "Lithuanian", "lv": "Latvian", "mg": "Malagasy", "mi": "Maori", "mk": "Macedonian",
    "ml": "Malayalam", "mn": "Mongolian", "mr": "Marathi", "ms": "Malay", "mt": "Maltese", "my": "Myanmar",
    "ne": "Nepali", "nl": "Dutch", "nn": "Nynorsk", "no": "Norwegian", "oc": "Occitan", "pa": "Punjabi",
    "pl": "Polish", "ps": "Pashto", "pt": "Portuguese", "ro": "Romanian", "ru": "Russian", "sa": "Sanskrit",
    "sd": "Sindhi", "si": "Sinhala", "sk": "Slovak", "sl": "Slovenian", "sn": "Shona", "so": "Somali",
    "sq": "Albanian", "sr": "Serbian", "su": "Sundanese", "sv": "Swedish", "sw": "Swahili", "ta": "Tamil",
    "te": "Telugu", "tg": "Tajik", "th": "Thai", "tk": "Turkmen", "tl": "Tagalog", "tr": "Turkish", "tt": "Tatar",
    "uk": "Ukrainian", "ur": "Urdu", "uz": "Uzbek", "vi": "Vietnamese", "yi": "Yiddish", "yo": "Yoruba",
    "zh": "Chinese", "yue": "Cantonese",
}
_BY_NAME = {name.lower(): code for code, name in NAMES.items()} | {"javanese": "jw", "burmese": "my"}

# Languages spoken in a country (HA's country setting), most common first; countries not listed add nothing
COUNTRY = {
    "AT": ["de"], "AU": ["en"], "BE": ["nl", "fr", "de"], "BR": ["pt"], "CA": ["en", "fr"], "CH": ["de", "fr", "it"],
    "CN": ["zh"], "CZ": ["cs"], "DE": ["de"], "DK": ["da"], "EE": ["et"], "ES": ["es", "ca"], "FI": ["fi", "sv"],
    "FR": ["fr"], "GB": ["en"], "GR": ["el"], "HR": ["hr"], "HU": ["hu"], "IE": ["en"], "IL": ["he"], "IN": ["hi", "en"],
    "IS": ["is"], "IT": ["it"], "JP": ["ja"], "KR": ["ko"], "LT": ["lt"], "LU": ["lb", "fr", "de"], "LV": ["lv"],
    "MX": ["es"], "NL": ["nl"], "NO": ["no"], "NZ": ["en"], "PL": ["pl"], "PT": ["pt"], "RO": ["ro"], "RS": ["sr"],
    "RU": ["ru"], "SE": ["sv"], "SI": ["sl"], "SK": ["sk"], "TR": ["tr"], "UA": ["uk"], "US": ["en"], "ZA": ["en", "af"],
}

_identifier: Any = None


def from_ha(language: str | None, country: str | None) -> list[str]:
    """The home's languages as HA describes it: its language, then the country's; English if neither."""
    found = [c for c in [code_of(language), *COUNTRY.get((country or "").upper(), [])] if c]
    return list(dict.fromkeys(found)) or ["en"]


def home_languages(hass, store) -> list[str]:
    """The owner's list, or HA's description of the home until the owner sets one."""
    return store.data.get("languages") or from_ha(hass.config.language, hass.config.country)


def code_of(value: str | None) -> str | None:
    """de / german / de_DE / de-DE → de (None when unknown)."""
    if not value:
        return None
    low = value.strip().lower()
    if low in _BY_NAME:
        return _BY_NAME[low]
    code = low.replace("-", "_").split("_")[0]
    return code if code in NAMES or len(code) in (2, 3) else None


def load() -> None:
    """Load the detector (blocking: run it in an executor once)."""
    global _identifier  # noqa: PLW0603
    if _identifier is None:
        from py3langid.langid import MODEL_FILE, LanguageIdentifier  # noqa: PLC0415

        _identifier = LanguageIdentifier.from_model_file(MODEL_FILE)


def detect(text: str, candidates: list[str] | None = None) -> str | None:
    """The language of a text; among the candidates when there are two or more (None if not loaded)."""
    if _identifier is None or not text.strip():
        return None
    ranked = [lang for lang, _score in _identifier.rank(text)]
    if candidates and len(set(candidates)) >= 2:
        return next((lang for lang in ranked if lang in candidates), ranked[0])
    return ranked[0]
