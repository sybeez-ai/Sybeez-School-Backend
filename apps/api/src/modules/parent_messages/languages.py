"""Languages a parent can choose. WhatsApp template shells exist for each (alert catalog)."""

LANGUAGES: dict[str, str] = {
    "en": "English",
    "hi": "हिन्दी (Hindi)",
    "ta": "தமிழ் (Tamil)",
    "te": "తెలుగు (Telugu)",
    "kn": "ಕನ್ನಡ (Kannada)",
    "ml": "മലയാളം (Malayalam)",
    "mr": "मराठी (Marathi)",
    "bn": "বাংলা (Bengali)",
    "gu": "ગુજરાતી (Gujarati)",
    "pa": "ਪੰਜਾਬੀ (Punjabi)",
}
SOURCE_LANGUAGE = "en"
SOURCE_TYPES = ("announcement", "progress_update")
