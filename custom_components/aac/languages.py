"""The home's languages (core): one list, main language first, that the cards use (Speech to text listens for them,
Voice out picks a voice per language, templates read {{ languages }}). Until the owner sets it on the Audio page, it
comes from Home Assistant: HA's language, then the country's languages; English if neither."""

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


def code_of(value: str | None) -> str | None:
    """de / german / de_DE / de-DE → de (None when unknown)."""
    if not value:
        return None
    low = str(value).strip().lower()
    if low in _BY_NAME:
        return _BY_NAME[low]
    code = low.replace("-", "_").split("_")[0]
    return code if code in NAMES else None


def from_ha(language: str | None, country: str | None) -> list[str]:
    """The home's languages as HA describes it: its language, then the country's; English if neither."""
    found = [c for c in [code_of(language), *COUNTRY.get((country or "").upper(), [])] if c]
    return list(dict.fromkeys(found)) or ["en"]


def describe(codes: list[str]) -> list[dict[str, Any]]:
    """For the panel: [{code, name}]."""
    return [{"code": c, "name": NAMES.get(c, c)} for c in codes]
