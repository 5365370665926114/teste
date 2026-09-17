from pathlib import Path

path = Path("Metrolist/app/build.gradle.kts")
text = path.read_text(encoding="utf-8")
old = "compileSdk = 37"
new = "compileSdk = 36"
if old not in text:
    raise RuntimeError(f"Expected {old!r} in {path}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
print("Patched compileSdk 37 -> 36 for stable GitHub runner SDK")
