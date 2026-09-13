import json

with open("config.json", "r", encoding="utf-8") as fh:
    config = json.load(fh)

print("Paste has worked fine in this prompt. Enter each key and press Enter.")
print("(Right-click or press Ctrl+Shift+V to paste into the terminal.)")
print()

gemini = input("Gemini API key (optional, recommended): ").strip()
vision = input("Google Vision API key (optional, fallback): ").strip()

if gemini:
    config["ocr"]["gemini_api_key"] = gemini
if vision:
    config["ocr"]["google_vision_api_key"] = vision

with open("config.json", "w", encoding="utf-8") as fh:
    json.dump(config, fh, ensure_ascii=False, indent=2)

print("\nSaved. Current keys:")
print("  gemini_api_key:", (config["ocr"]["gemini_api_key"] or "(empty)")[:12] + "...")
print("  google_vision_api_key:", (config["ocr"]["google_vision_api_key"] or "(empty)")[:12] + "...")