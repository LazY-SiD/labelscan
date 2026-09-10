# ============================================================================
# LabelScan - packaged food label scanner (prototype)
#
# What it does:
#   1. Scan a food label photo (AI vision reads ingredients + macros)
#      OR search a product by name on Blinkit (live data via their web API)
#   2. Flags risky / banned additives (FSSAI bans, EU/US bans, caution list)
#   3. Suggests healthier alternatives actually buyable on Blinkit
#
# Run:  python app.py     ->  http://127.0.0.1:5000
# Deps: pip install flask cloudscraper requests
# ============================================================================

import json
import os
import re
import secrets
import threading
import uuid

import requests
from curl_cffi import requests as browser_requests
from flask import Flask, jsonify, request, render_template_string

# ---------------------------------------------------------------- config ----
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
AI_MODEL           = "google/gemini-2.5-flash"

# location used for Blinkit results (prices/stock vary by area) - Delhi default
BLINKIT_LAT = "28.6139"
BLINKIT_LON = "77.2090"

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024  # 8 MB photo cap

# ------------------------------------------------------- additive database --
# tier: banned_india | banned_abroad | caution
ADDITIVES = [
    {
        "aliases": ["potassium bromate", "924", "ins 924", "e924"],
        "name": "Potassium Bromate (INS 924)",
        "tier": "banned_india",
        "note": "Banned in India by FSSAI (2016). Probable human carcinogen (IARC 2B); was used in bread/bakery.",
    },
    {
        "aliases": ["potassium iodate", "917", "ins 917", "e917"],
        "name": "Potassium Iodate (INS 917)",
        "tier": "banned_india",
        "note": "Banned as a flour treatment agent in India (FSSAI). Excess iodine can affect thyroid function.",
    },
    {
        "aliases": ["rhodamine b", "rhodamine"],
        "name": "Rhodamine B",
        "tier": "banned_india",
        "note": "Synthetic dye banned in food in India. Sometimes found illegally in sweets, spices, and sauces. Carcinogenic.",
    },
    {
        "aliases": ["brominated vegetable oil", "bvo", "443", "e443", "ins 443"],
        "name": "Brominated Vegetable Oil (BVO, E443)",
        "tier": "banned_abroad",
        "note": "Banned by US FDA (2024) and in the EU. Bromine accumulates in body fat; used in some citrus soft drinks.",
    },
    {
        "aliases": ["tbhq", "tert-butylhydroquinone", "tertiary butylhydroquinone", "319", "e319", "ins 319"],
        "name": "TBHQ (E319)",
        "tier": "banned_abroad",
        "note": "Synthetic antioxidant not approved in the EU. High doses linked to immune/neurological effects. Common in fried snacks.",
    },
    {
        "aliases": ["bha", "butylated hydroxyanisole", "320", "e320", "ins 320"],
        "name": "BHA (E320)",
        "tier": "banned_abroad",
        "note": "Possible carcinogen (IARC 2B). Banned in Japan, restricted elsewhere. Preservative in chips/fried snacks.",
    },
    {
        "aliases": ["bht", "butylated hydroxytoluene", "321", "e321", "ins 321"],
        "name": "BHT (E321)",
        "tier": "caution",
        "note": "Synthetic preservative; endocrine/organ effects in animal studies at high doses. Best limited.",
    },
    {
        "aliases": ["tartrazine", "102", "e102", "ins 102", "fd&c yellow 5", "yellow 5"],
        "name": "Tartrazine (E102)",
        "tier": "banned_abroad",
        "note": "EU requires a hyperactivity warning label on products with it. Linked to attention effects in children.",
    },
    {
        "aliases": ["sunset yellow", "110", "e110", "ins 110", "yellow 6"],
        "name": "Sunset Yellow FCF (E110)",
        "tier": "banned_abroad",
        "note": "EU mandates hyperactivity warning. Southampton-study colour linked to child behaviour effects.",
    },
    {
        "aliases": ["carmoisine", "azorubine", "122", "e122", "ins 122"],
        "name": "Carmoisine (E122)",
        "tier": "banned_abroad",
        "note": "EU hyperactivity warning colour. Banned in some countries (e.g. Japan, US).",
    },
    {
        "aliases": ["ponceau 4r", "124", "e124", "ins 124", "cochineal red a"],
        "name": "Ponceau 4R (E124)",
        "tier": "banned_abroad",
        "note": "EU hyperactivity warning colour; not permitted in US foods.",
    },
    {
        "aliases": ["allura red", "129", "e129", "ins 129", "red 40"],
        "name": "Allura Red AC (E129)",
        "tier": "banned_abroad",
        "note": "EU hyperactivity warning colour. Animal studies flag potential gut inflammation.",
    },
    {
        "aliases": ["monosodium glutamate", "msg", "621", "e621", "ins 621", "ajinomoto"],
        "name": "MSG (E621)",
        "tier": "caution",
        "note": "Not banned, but some people report sensitivity (headaches, flushing). FSSAI restricts use in infant food.",
    },
    {
        "aliases": ["sodium benzoate", "211", "e211", "ins 211"],
        "name": "Sodium Benzoate (E211)",
        "tier": "caution",
        "note": "Combined with vitamin C it can form benzene (a carcinogen) in drinks. Limit frequent intake.",
    },
    {
        "aliases": ["sodium nitrite", "250", "e250", "ins 250", "sodium nitrate", "251", "e251"],
        "name": "Nitrites/Nitrates (E250-E251)",
        "tier": "caution",
        "note": "Used in processed meat. Can form nitrosamines; processed meat is an IARC Group 1 carcinogen.",
    },
    {
        "aliases": ["acesulfame", "acesulfame k", "950", "e950", "ins 950"],
        "name": "Acesulfame K (E950)",
        "tier": "caution",
        "note": "Artificial sweetener. WHO advises against routine use of non-sugar sweeteners for weight control.",
    },
    {
        "aliases": ["aspartame", "951", "e951", "ins 951"],
        "name": "Aspartame (E951)",
        "tier": "caution",
        "note": "Classified IARC 2B 'possibly carcinogenic' (2023) but within ADI is considered safe. Keep intake low.",
    },
    {
        "aliases": ["sucralose", "955", "e955", "ins 955"],
        "name": "Sucralose (E955)",
        "tier": "caution",
        "note": "Artificial sweetener; emerging concerns on gut microbiome and glucose response. Moderate use.",
    },
    {
        "aliases": ["saccharin", "954", "e954", "ins 954"],
        "name": "Saccharin (E954)",
        "tier": "caution",
        "note": "Oldest artificial sweetener; permitted within limits. Prefer products without it.",
    },
    {
        "aliases": ["partially hydrogenated", "hydrogenated vegetable oil", "vanaspati", "trans fat", "transfat", "interestified", "interesterified"],
        "name": "Partially Hydrogenated Oil / Trans Fat",
        "tier": "caution",
        "note": "Industrial trans fat. FSSAI caps it at 2% of total fat (2022); WHO pushing global elimination. Raises LDL cholesterol.",
    },
    {
        "aliases": ["high fructose corn syrup", "hfcs", "corn syrup", "liquid glucose", "invert syrup", "invert sugar syrup"],
        "name": "Corn/Glucose Syrup (HFCS-type sugars)",
        "tier": "caution",
        "note": "Dense added sugars linked to fatty liver and insulin resistance when consumed regularly.",
    },
    {
        "aliases": ["palmolein", "palm oil", "palmolein oil"],
        "name": "Palm Oil / Palmolein",
        "tier": "caution",
        "note": "High in saturated fat and ubiquitous in Indian fried snacks. Not banned - just a 'limit' fat.",
    },
    {
        "aliases": ["phosphoric acid", "338", "e338", "ins 338"],
        "name": "Phosphoric Acid (E338)",
        "tier": "caution",
        "note": "Cola acidulant. High habitual intake is tied to lower bone density and enamel erosion.",
    },
    {
        "aliases": ["sulphur dioxide", "220", "e220", "sulphite", "sulfite", "223", "e223", "metabisulphite"],
        "name": "Sulphites (E220-E228)",
        "tier": "caution",
        "note": "Preservatives that can trigger asthma/allergy in sensitive people.",
    },
]

TIER_META = {
    "banned_india":  {"label": "BANNED IN INDIA",  "color": "#dc2626"},
    "banned_abroad": {"label": "BANNED/WARNED ABROAD", "color": "#ea580c"},
    "caution":       {"label": "CAUTION",          "color": "#ca8a04"},
}

# ------------------------------------------------------------- blinkit api --
# Blinkit is protected by Cloudflare TLS fingerprinting. curl_cffi makes the
# request with a real Chrome-like TLS handshake; ordinary requests/cloudscraper
# can still receive a 403 even when every HTTP header looks correct.
_BLINKIT_BASE = "https://blinkit.com"
_BLINKIT_REQ_KEY = os.environ.get(
    "BLINKIT_REQ_KEY", "c0e6868e-1180-400c-be51-f473479f1f0a"
)
_BLINKIT_DEVICE_ID = secrets.token_hex(8)
_BLINKIT_SESSION_UUID = str(uuid.uuid4())
_BLINKIT_AUTH_KEY = None
_BLINKIT_LOCK = threading.RLock()
_scraper = browser_requests.Session(impersonate="chrome")
_BLINKIT_HEADERS = {
    "app_client": "consumer_web",
    "platform": "desktop_web",
    "lat": BLINKIT_LAT,
    "lon": BLINKIT_LON,
    "web_app_version": "1008010016",
    "rn_bundle_version": "1009003012",
    "app_version": "52434332",
    "x-age-consent-granted": "false",
    "accept": "*/*",
    "accept-language": "en-US,en;q=0.9",
    "origin": _BLINKIT_BASE,
    "referer": f"{_BLINKIT_BASE}/",
    "device_id": _BLINKIT_DEVICE_ID,
    "session_uuid": _BLINKIT_SESSION_UUID,
}


def _blinkit_auth_key():
    """Create the anonymous device session required by Blinkit's web API."""
    global _BLINKIT_AUTH_KEY
    if _BLINKIT_AUTH_KEY:
        return _BLINKIT_AUTH_KEY

    r = _scraper.get(
        f"{_BLINKIT_BASE}/v2/accounts/auth_key/",
        headers={**_BLINKIT_HEADERS, "req_key": _BLINKIT_REQ_KEY},
        timeout=30,
    )
    r.raise_for_status()
    _BLINKIT_AUTH_KEY = r.json().get("auth_key")
    if not _BLINKIT_AUTH_KEY:
        raise RuntimeError("Blinkit did not issue an anonymous session key")
    return _BLINKIT_AUTH_KEY


def _blinkit_request(method, path, *, params=None, json_body=None):
    """Call Blinkit with one consistent Chrome-like anonymous session."""
    with _BLINKIT_LOCK:
        headers = {
            **_BLINKIT_HEADERS,
            "auth_key": _blinkit_auth_key(),
        }
        r = _scraper.request(
            method,
            f"{_BLINKIT_BASE}{path}",
            params=params,
            json=json_body,
            headers=headers,
            timeout=30,
        )
        r.raise_for_status()
        return r.json()


def blinkit_search(query, limit=10):
    """Search Blinkit. Returns [{id, name, brand, price, mrp, variant, image}]."""
    data = _blinkit_request(
        "POST",
        "/v1/layout/search",
        params={"q": query, "search_type": "type_to_search"},
        json_body={
            "applied_filters": None,
            "sort": "",
            "previous_search_query": query,
        },
    )

    products = []
    seen = set()

    def text(v):
        if isinstance(v, str):
            return v
        return (v or {}).get("text", "") if isinstance(v, dict) else ""

    def walk(o):
        if isinstance(o, dict):
            candidates = [o]
            if isinstance(o.get("data"), dict):
                candidates.append(o["data"])
            for d in candidates:
                cart = ((d.get("atc_action") or {}).get("add_to_cart") or {}).get("cart_item") or {}
                ident = d.get("product_id") or d.get("type_id") or cart.get("product_id")
                ident = ident or (d.get("identity") or {}).get("id")
                ident = str(ident) if ident is not None else None
                looks_product = ident and (
                    cart or d.get("atc_action") or d.get("normal_price")
                    or d.get("mrp") or d.get("assets")
                )
                if ident and ident not in seen:
                    if not looks_product:
                        continue
                    seen.add(ident)
                    price = text(d.get("normal_price")) or d.get("price") or cart.get("price", "")
                    mrp = text(d.get("mrp")) or cart.get("mrp", "")
                    if isinstance(price, (int, float)):
                        price = f"₹{price:g}"
                    if isinstance(mrp, (int, float)):
                        mrp = f"₹{mrp:g}"
                    products.append({
                        "id": str(ident),
                        "name": text(d.get("display_name")) or text(d.get("name"))
                                or cart.get("display_name") or cart.get("product_name")
                                or d.get("group_name", ""),
                        "brand": text(d.get("brand_name")) or d.get("brand") or cart.get("brand", ""),
                        "price": price,
                        "mrp": mrp,
                        "variant": text(d.get("variant")) or d.get("unit") or cart.get("unit", ""),
                        "image": (d.get("image") or {}).get("url")
                                 or ((d.get("assets") or [{}])[0].get("image_url"))
                                 or cart.get("image_url", ""),
                    })
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(data)
    return products[:limit]


def blinkit_product_details(product_id):
    """Fetch PDP attributes, ingredient text, and Blinkit's product gallery."""
    data = _blinkit_request(
        "POST",
        f"/v1/layout/product/{product_id}",
        json_body={},
    )

    attributes = {}
    image_urls = []

    def walk(o):
        if isinstance(o, dict):
            image_url = o.get("image_url")
            if isinstance(image_url, str) and image_url.startswith("https://"):
                if image_url not in image_urls:
                    image_urls.append(image_url)
            t, s = o.get("title"), o.get("subtitle")
            if isinstance(t, dict) and isinstance(s, dict):
                title = str(t.get("text", "")).strip()
                val = str(s.get("text", "")).strip()
                if title and val and len(title) < 80:
                    attributes[title] = val
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(data)

    ingredients = ""
    for k, v in attributes.items():
        if "ingredient" in k.lower() and len(v) > len(ingredients):
            ingredients = v
    return attributes, ingredients, image_urls


# -------------------------------------------------------------- ai helpers --
def _openrouter(messages, json_mode=True):
    r = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": AI_MODEL,
            "messages": messages,
            "temperature": 0,
            **({"response_format": {"type": "json_object"}} if json_mode else {}),
        },
        timeout=90,
    )
    if r.status_code == 402:
        raise RuntimeError("OpenRouter says 'insufficient credits' for this key. "
                           "Top up at openrouter.ai/credits or swap OPENROUTER_API_KEY in app.py.")
    r.raise_for_status()
    content = r.json()["choices"][0]["message"]["content"]
    content = re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=re.M).strip()
    return json.loads(content) if json_mode else content


def ai_extract_label(image_b64, mime):
    """Vision: photo of a food label -> structured product data."""
    prompt = (
        "You are reading a packaged food label photo (Indian product). Extract strictly as JSON:\n"
        '{"name": str, "brand": str, "ingredients": str (full ingredient list as text), '
        '"macros": {"energy_kcal": number|null, "protein_g": number|null, "carbs_g": number|null, '
        '"sugar_g": number|null, "fat_g": number|null, "sat_fat_g": number|null, '
        '"trans_fat_g": number|null, "fibre_g": number|null, "sodium_mg": number|null}, '
        '"macros_basis": "per 100g"|"per serving"|"unknown"}\n'
        "Rules: prefer per-100g values when both are printed; null when not visible; "
        "ingredients must include everything listed incl. INS/E-numbers. No commentary."
    )
    return _openrouter([{
        "role": "user",
        "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{image_b64}"}},
        ],
    }])


def ai_extract_blinkit_gallery(image_urls):
    """Read ingredients/macros from product-pack images supplied by Blinkit."""
    prompt = (
        "These are gallery images for one packaged food product listed on Blinkit. "
        "Find the image showing the ingredient list and nutrition panel, then extract strict JSON:\n"
        '{"ingredients": str, "macros": {"energy_kcal": number|null, '
        '"protein_g": number|null, "carbs_g": number|null, "sugar_g": number|null, '
        '"fat_g": number|null, "sat_fat_g": number|null, "trans_fat_g": number|null, '
        '"fibre_g": number|null, "sodium_mg": number|null}, '
        '"macros_basis": "per 100g"|"per serving"|"unknown"}\n'
        "Copy every visible ingredient including INS/E-numbers. Use an empty string only if no "
        "ingredient panel is readable. Use null for nutrition values that are not visible."
    )
    content = [{"type": "text", "text": prompt}]
    # The first photo is normally the pack front; later gallery photos contain
    # label details. Six images keeps the request quick while covering the PDP.
    for url in image_urls[-6:]:
        content.append({"type": "image_url", "image_url": {"url": url}})
    return _openrouter([{"role": "user", "content": content}])


def macros_from_ai(data):
    macros = {}
    for k, v in (data.get("macros") or {}).items():
        if isinstance(v, (int, float)):
            key = k.replace("_g", "").replace("_kcal", "").replace("_mg", "")
            unit = "kcal" if "kcal" in k else ("mg" if "mg" in k else "g")
            macros[key] = {"value": v, "unit": unit, "raw": f"{v} {unit}"}
    return macros


def ai_alternative_terms(name, ingredients):
    """Ask the AI for healthier Blinkit-searchable alternatives (Indian market)."""
    prompt = (
        f"Product: {name}\nIngredients: {ingredients[:600]}\n\n"
        "Suggest healthier alternative products an Indian consumer can buy on Blinkit (quick commerce). "
        'Return strict JSON: {"terms": ["...", "...", "...", "..."], "reason": str}\n'
        "- terms: 3-5 short search queries for genuinely healthier swaps of the same snack category "
        "(e.g. 'baked chips', 'roasted makhana', 'multigrain chips').\n"
        "- reason: one short sentence why these swaps are healthier.\nNo commentary."
    )
    try:
        out = _openrouter([{"role": "user", "content": prompt}])
        terms = [t for t in out.get("terms", []) if isinstance(t, str)][:5]
        if terms:
            return terms, out.get("reason", "")
    except Exception:
        pass
    return ["baked chips", "roasted makhana", "healthy snacks"], "Lower-fat, less-processed swaps."


# ------------------------------------------------------------ risk scanning -
_NUM_ONLY = re.compile(r"^\d{3}$")

def scan_ingredients(ingredients_text):
    """Match the additive DB against the ingredient text. Returns findings list."""
    text = " " + re.sub(r"\s+", " ", ingredients_text.lower()) + " "
    findings = []
    for add in ADDITIVES:
        for alias in add["aliases"]:
            hit = False
            if _NUM_ONLY.match(alias):
                # numeric INS code: require word-ish boundary so "330" etc. match, "2330" doesn't
                hit = re.search(rf"(?:ins\s*|e\s*|[^\d]){re.escape(alias)}(?:[^\d]|$)", text) is not None
            else:
                hit = alias in text
            if hit:
                findings.append({**{k: add[k] for k in ("name", "tier", "note")},
                                 "matched": alias,
                                 **TIER_META[add["tier"]]})
                break
    order = {"banned_india": 0, "banned_abroad": 1, "caution": 2}
    findings.sort(key=lambda f: order[f["tier"]])
    return findings


# ----------------------------------------------------------- macro parsing --
_MACRO_MAP = [
    ("energy",     ["energy"],                        "kcal"),
    ("protein",    ["protein"],                       "g"),
    ("carbs",      ["carbohydrate", "carb"],          "g"),
    ("sugar",      ["sugar"],                         "g"),
    ("fat",        ["total fat"],                     "g"),
    ("sat_fat",    ["saturated fat", "saturated fatty"], "g"),
    ("trans_fat",  ["trans fat", "trans fatty"],      "g"),
    ("fibre",      ["fibre", "fiber"],                "g"),
    ("sodium",     ["sodium"],                        "mg"),
]

def macros_from_attributes(attributes):
    """Pull per-100g macros out of Blinkit's PDP title->value attributes."""
    macros = {}
    for title, val in attributes.items():
        tl = title.lower()
        if "per 100" not in tl and "per100" not in tl:
            continue
        for key, words, unit in _MACRO_MAP:
            if key in macros:
                continue
            if any(w in tl for w in words):
                m = re.search(r"([\d.]+)", val.replace(",", ""))
                if m:
                    macros[key] = {"value": float(m.group(1)), "unit": unit, "raw": val}
    return macros


# ------------------------------------------------------------------- routes -
@app.route("/api/search")
def api_search():
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"error": "empty query"}), 400
    try:
        return jsonify({"products": blinkit_search(q)})
    except Exception as e:
        return jsonify({"error": f"Blinkit search failed: {e}"}), 502


@app.route("/api/analyze/blinkit/<product_id>")
def api_analyze_blinkit(product_id):
    try:
        attributes, ingredients, image_urls = blinkit_product_details(product_id)
    except Exception as e:
        return jsonify({"error": f"Could not fetch product details: {e}"}), 502

    macros = macros_from_attributes(attributes)
    macros_basis = "per 100g"
    if not ingredients and image_urls:
        try:
            gallery_data = ai_extract_blinkit_gallery(image_urls)
            ingredients = gallery_data.get("ingredients") or ""
            image_macros = macros_from_ai(gallery_data)
            if image_macros:
                macros = image_macros
                macros_basis = gallery_data.get("macros_basis", "unknown")
        except Exception:
            # The user still gets the clear scan-label message below when a
            # gallery is incomplete or the vision provider is unavailable.
            pass

    if not ingredients:
        return jsonify({"error": "Blinkit does not list ingredients for this product. "
                                 "Try scanning the label photo instead."}), 404

    findings = scan_ingredients(ingredients)
    name = request.args.get("name") or product_id
    terms, alt_reason = ai_alternative_terms(name, ingredients)
    alternatives = _collect_alternatives(terms, exclude_id=product_id)

    return jsonify({
        "source": "blinkit",
        "ingredients": ingredients,
        "macros": macros,
        "macros_basis": macros_basis,
        "findings": findings,
        "alt_terms": terms,
        "alt_reason": alt_reason,
        "alternatives": alternatives,
    })


@app.route("/api/analyze/photo", methods=["POST"])
def api_analyze_photo():
    f = request.files.get("photo")
    if not f:
        return jsonify({"error": "no photo uploaded"}), 400
    import base64
    b64 = base64.b64encode(f.read()).decode()
    mime = f.mimetype or "image/jpeg"

    try:
        data = ai_extract_label(b64, mime)
    except Exception as e:
        return jsonify({"error": f"AI could not read the label: {e}"}), 502

    ingredients = data.get("ingredients") or ""
    if not ingredients:
        return jsonify({"error": "No ingredient list found in the photo. "
                                 "Use a sharper close-up of the ingredients section."}), 404

    macros = macros_from_ai(data)

    findings = scan_ingredients(ingredients)
    name = data.get("name") or "Scanned product"
    terms, alt_reason = ai_alternative_terms(name, ingredients)
    alternatives = _collect_alternatives(terms, exclude_id=None)

    return jsonify({
        "source": "photo",
        "name": name,
        "brand": data.get("brand", ""),
        "ingredients": ingredients,
        "macros": macros,
        "macros_basis": data.get("macros_basis", "unknown"),
        "findings": findings,
        "alt_terms": terms,
        "alt_reason": alt_reason,
        "alternatives": alternatives,
    })


def _collect_alternatives(terms, exclude_id):
    """Search Blinkit for each healthier term, merge + dedupe results."""
    alts, seen = [], {exclude_id}
    for t in terms:
        try:
            for p in blinkit_search(t, limit=6):
                if p["id"] not in seen:
                    seen.add(p["id"])
                    alts.append({**p, "term": t})
        except Exception:
            continue
        if len(alts) >= 8:
            break
    return alts[:8]


# -------------------------------------------------------------------- page --
PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>LabelScan &middot; what's actually in your food</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,500;12..96,600;12..96,700&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  :root {
    --paper: #f7f8f3;  --card: #ffffff;
    --ink:   #191813;  --muted: #706f66;  --faint: #a8a79d;
    --line:  #e7e7e1;  --soft: #f5f5f1; --green-ink: #245c32;
    --red:   #dc2626;  --orange: #c2410c; --amber: #a16207; --ok: #15803d;
  }
  * { box-sizing: border-box; margin: 0; }
  html { -webkit-font-smoothing: antialiased; -moz-osx-font-smoothing: grayscale; }
  body { font-family: "Inter", -apple-system, "Segoe UI", Roboto, sans-serif;
         background: var(--paper); color: var(--ink); font-size: 15px; line-height: 1.5; min-height: 100vh; }
  ::selection { background: #191813; color: #fafaf7; }

  .wrap { max-width: 820px; margin: 0 auto; padding: 34px 24px 96px; }

  /* ---------- brand ---------- */
  .brand { display: flex; align-items: baseline; gap: 10px; }
  .wordmark { font-family: "Bricolage Grotesque", "Inter", sans-serif; font-weight: 700;
              font-size: 21px; letter-spacing: -0.02em; }
  .wordmark::after { content: ""; display: inline-block; width: 7px; height: 7px;
                     background: var(--ok); border-radius: 2px; margin-left: 3px; }
  .brand .sub { font-size: 12.5px; color: var(--faint); font-weight: 500; letter-spacing: 0.01em; }

  /* ---------- hero ---------- */
  .hero { margin: 72px 0 30px; max-width: 650px; }
  .hero h1 { font-family: "Bricolage Grotesque", "Inter", sans-serif; font-weight: 600;
             font-size: clamp(30px, 5.5vw, 42px); line-height: 1.08; letter-spacing: -0.025em;
             text-wrap: balance; }
  .hero p { margin-top: 14px; color: var(--muted); font-size: 16px; max-width: 55ch; text-wrap: pretty; }

  /* ---------- primary action panel ---------- */
  .action-card { background: var(--card); border: 1px solid var(--line); border-radius: 16px; padding: 18px; }
  .action-label { margin-bottom: 10px; font-size: 12px; color: var(--muted); }
  .action-label b { color: var(--ink); font-weight: 650; }

  /* ---------- search bar ---------- */
  .bar { display: flex; align-items: center; gap: 8px; background: var(--card);
         border: 1px solid #d9dbd0; border-radius: 12px; padding: 6px;
         transition: border-color .15s ease, box-shadow .15s ease; }
  .bar:focus-within { border-color: var(--ink); box-shadow: 0 0 0 3px rgba(25,24,19,.07); }
  .bar input { flex: 1; border: 0; outline: none; background: transparent;
               font: inherit; font-size: 16px; padding: 8px 6px 8px 12px; color: var(--ink); }
  .bar input::placeholder { color: var(--faint); }
  .btn { font: inherit; font-weight: 600; font-size: 14px; border: 0; cursor: pointer;
         background: var(--ink); color: var(--paper); border-radius: 9px; padding: 10px 20px;
         transition: opacity .15s ease, transform .1s ease; }
  .btn:hover { opacity: .85; }
  .btn:active { transform: scale(.97); }
  .btn:disabled { opacity: .45; cursor: default; transform: none; }
  .divider { display: flex; align-items: center; gap: 12px; margin: 15px 0; color: var(--faint);
             font-size: 10px; font-weight: 700; letter-spacing: .1em; text-transform: uppercase; }
  .divider::before, .divider::after { content: ""; flex: 1; height: 1px; background: var(--line); }
  .scanlink { display: flex; align-items: center; justify-content: center; gap: 9px; width: 100%;
              background: var(--soft); border: 1px solid #dce9d5; border-radius: 11px;
              padding: 13px 16px; cursor: pointer; font: inherit; font-size: 13.5px;
              font-weight: 600; color: var(--green-ink); transition: background .15s ease, transform .1s ease; }
  .scanlink:hover { background: #e4f0dc; }
  .scanlink:active { transform: scale(.99); }
  .scanlink svg { flex-shrink: 0; }
  .how { margin-top: 36px; }
  .how-title { font-size: 11px; font-weight: 700; letter-spacing: .1em; text-transform: uppercase; color: var(--faint); }
  .steps { display: grid; grid-template-columns: repeat(3, 1fr); gap: 18px; margin-top: 12px;
           padding-top: 14px; border-top: 1px solid var(--line); }
  .step { color: var(--muted); font-size: 12px; }
  .step b { display: block; color: var(--ink); margin-bottom: 2px; font-size: 12.5px; }
  .step span { color: var(--ok); font-weight: 700; margin-right: 5px; }
  .disclaimer { margin-top: 22px; color: var(--faint); font-size: 11px; line-height: 1.6; max-width: 66ch; }

  :focus-visible { outline: 2px solid var(--ink); outline-offset: 2px; border-radius: 4px; }

  /* ---------- status / errors ---------- */
  .status { margin-top: 28px; font-size: 13.5px; color: var(--muted); }
  .loadbar { height: 2px; background: var(--line); border-radius: 2px; overflow: hidden; margin-bottom: 12px; }
  .loadbar i { display: block; height: 100%; width: 38%; background: var(--ink);
               border-radius: 2px; animation: slide 1.1s ease-in-out infinite; }
  @keyframes slide { 0% { transform: translateX(-100%);} 100% { transform: translateX(290%);} }
  .err { margin-top: 28px; font-size: 14px; color: var(--red); background: #fef2f1;
         border: 1px solid #f5d4d1; border-radius: 10px; padding: 12px 16px; }

  /* ---------- results ---------- */
  .results { margin-top: 40px; }
  .card { background: var(--card); border: 1px solid var(--line); border-radius: 16px;
          padding: 24px; animation: rise .3s cubic-bezier(.2,.7,.2,1) both; }
  @keyframes rise { from { opacity: 0; transform: translateY(6px);} to { opacity: 1; transform: none;} }

  .pname { font-family: "Bricolage Grotesque", "Inter", sans-serif; font-weight: 600;
           font-size: 20px; letter-spacing: -0.015em; line-height: 1.2; }
  .pmeta { margin-top: 5px; font-size: 12.5px; color: var(--muted); }

  .sect { margin-top: 26px; }
  .sect-label { font-size: 11px; font-weight: 600; letter-spacing: .12em; text-transform: uppercase;
                color: var(--faint); padding-bottom: 10px; border-bottom: 1px solid var(--line); }
  .sect-label em { font-style: normal; color: var(--ink); }

  /* macros strip */
  .macros { display: flex; flex-wrap: wrap; }
  .macro { padding: 14px 18px 12px 0; margin-right: 18px; border-right: 1px solid var(--line); }
  .macro:last-child { border-right: 0; margin-right: 0; }
  .macro b { display: block; font-size: 19px; font-weight: 650; letter-spacing: -0.01em;
             font-variant-numeric: tabular-nums; }
  .macro b small { font-size: 11px; font-weight: 500; color: var(--faint); margin-left: 2px; }
  .macro span { display: block; margin-top: 2px; font-size: 10.5px; font-weight: 600;
                letter-spacing: .09em; text-transform: uppercase; color: var(--muted); }

  /* findings */
  .finding { display: flex; gap: 12px; padding: 13px 0; border-bottom: 1px solid var(--line); }
  .finding:last-child { border-bottom: 0; }
  .chip { flex-shrink: 0; height: fit-content; font-size: 9.5px; font-weight: 700;
          letter-spacing: .06em; color: #fff; padding: 4px 7px 3px; border-radius: 4px;
          white-space: nowrap; margin-top: 2px; }
  .finding b { display: block; font-size: 14px; font-weight: 600; }
  .finding small { display: block; margin-top: 2px; font-size: 13px; line-height: 1.55; color: var(--muted); }
  .allclear { display: flex; align-items: center; gap: 9px; padding: 14px 0 4px;
              font-size: 14px; font-weight: 500; color: var(--ok); }

  .ingr { font-size: 13.5px; line-height: 1.7; color: #3d3c35; max-width: 65ch;
          overflow-wrap: break-word; }

  /* alternatives / search results */
  .alt-head { font-family: "Bricolage Grotesque", "Inter", sans-serif; font-weight: 600;
              font-size: 18px; letter-spacing: -0.015em; margin-top: 36px; }
  .alt-sub { margin-top: 5px; font-size: 13px; color: var(--muted); max-width: 58ch; text-wrap: pretty; }
  .alts { display: grid; grid-template-columns: repeat(auto-fill, minmax(148px, 1fr));
          gap: 10px; margin-top: 16px; }
  .alt { background: var(--card); border: 1px solid var(--line); border-radius: 12px;
         padding: 12px; cursor: pointer; text-align: left;
         transition: transform .15s ease, border-color .15s ease;
         animation: rise .3s cubic-bezier(.2,.7,.2,1) both; }
  .alt:hover { transform: translateY(-2px); border-color: var(--ink); }
  .alt img { width: 100%; height: 92px; object-fit: contain; }
  .alt .an { font-size: 12.5px; font-weight: 600; line-height: 1.35; margin-top: 10px;
             display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
             overflow: hidden; min-height: 34px; }
  .alt .av { font-size: 11px; color: var(--faint); margin-top: 3px; }
  .alt .ap { font-size: 13px; font-weight: 700; margin-top: 4px; font-variant-numeric: tabular-nums; }
  .alt .ap s { color: var(--faint); font-weight: 500; font-size: 11px; margin-left: 5px; }
  .empty { font-size: 13.5px; color: var(--muted); padding: 10px 0; }

  @media (max-width: 640px) {
    .wrap { padding: 24px 16px calc(72px + env(safe-area-inset-bottom)); }
    .brand .sub { display: none; }
    .hero { margin: 48px 0 24px; }
    .hero p { font-size: 14.5px; }
    .bar { padding: 5px; }
    .bar input { min-width: 0; }
    .btn { min-height: 44px; padding: 10px 13px; }
    .action-card { padding: 12px; border-radius: 15px; }
    .action-label span { display: none; }
    .scanlink { min-height: 46px; }
    .steps { grid-template-columns: 1fr; gap: 0; }
    .step { display: block; padding: 13px 2px; }
    .step b { display: block; margin-bottom: 3px; }
    .card { padding: 18px; border-radius: 14px; }
    .macros { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px 8px; }
    .macro { border-right: 0; padding: 0; margin: 0; }
    .macro b { font-size: 17px; }
    .alts { grid-template-columns: repeat(2, 1fr); }
    .finding { gap: 10px; }
    .chip { font-size: 9px; }
  }
  @media (prefers-reduced-motion: reduce) {
    *, *::before, *::after { animation: none !important; transition: none !important; }
  }
</style>
</head>
<body>
<div class="wrap">
  <div class="brand">
    <span class="wordmark">LabelScan</span>
    <span class="sub">packaged food, decoded</span>
  </div>

  <div class="hero">
    <h1>What's actually in your food?</h1>
    <p>Search a packaged food or scan its label. We'll translate the fine print into
       clear nutrition, additive flags, and smarter swaps.</p>
  </div>

  <div class="action-card">
    <div class="action-label"><b>Find a packaged food</b></div>
    <div class="bar">
      <input type="search" id="q" aria-label="Search for a packaged food" placeholder="Try Lay's Magic Masala or Coke&hellip;" autocomplete="off" autofocus>
      <button class="btn" id="searchBtn" onclick="doSearch()">Check food</button>
    </div>
    <div class="divider">or</div>
    <button class="scanlink" onclick="document.getElementById('file').click()" aria-controls="file">
      <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/></svg>
      Scan a label photo
    </button>
    <input type="file" id="file" accept="image/*" style="display:none" onchange="doPhoto(this)">
  </div>

  <div id="status" role="status" aria-live="polite"></div>
  <div id="err" role="alert"></div>
  <div id="results" class="results">
    <section class="how" aria-label="How LabelScan works">
      <div class="how-title">How it works</div>
      <div class="steps">
        <div class="step"><b><span>01</span>Search or scan</b>Choose a product or upload its label.</div>
        <div class="step"><b><span>02</span>Read the flags</b>See additives and nutrition in plain language.</div>
        <div class="step"><b><span>03</span>Swap smarter</b>Compare alternatives available nearby.</div>
      </div>
      <p class="disclaimer">LabelScan is an informational guide, not medical advice. Always check the physical pack for the latest ingredient and allergen information.</p>
    </section>
  </div>
</div>

<script>
const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

function setStatus(text) {
  $("status").innerHTML = text
    ? `<div class="loadbar"><i></i></div>${esc(text)}`
    : "";
}
function fail(msg) { $("err").innerHTML = `<div class="err">${esc(msg)}</div>`; setStatus(""); }

function productCard(p, i) {
  const disc = p.mrp && p.price && p.mrp !== p.price ? `<s>${esc(p.mrp)}</s>` : "";
  return `
    <div class="alt" style="animation-delay:${i * 30}ms" onclick="analyzeBlinkit('${p.id}', this)" data-name="${esc(p.name)}">
      ${p.image ? `<img src="${esc(p.image)}" loading="lazy" alt="">` : ""}
      <div class="an">${esc(p.name)}</div>
      <div class="av">${esc(p.variant)}</div>
      <div class="ap">${esc(p.price)}${disc}</div>
    </div>`;
}

async function doSearch() {
  const q = $("q").value.trim();
  if (!q) { $("q").focus(); return; }
  $("searchBtn").disabled = true;
  $("err").innerHTML = ""; $("results").innerHTML = "";
  setStatus("Searching Blinkit");
  try {
    const r = await fetch("/api/search?q=" + encodeURIComponent(q));
    const d = await r.json();
    if (d.error) return fail(d.error);
    setStatus("");
    if (!d.products.length) { $("results").innerHTML = '<p class="empty">Nothing on Blinkit matches that. Try another name.</p>'; return; }
    $("results").innerHTML = '<div class="alt-head">Pick a product</div><div class="alts">' +
      d.products.map(productCard).join("") + "</div>";
  } catch (e) { fail(e.message); }
  finally { $("searchBtn").disabled = false; }
}

async function analyzeBlinkit(pid, el) {
  $("err").innerHTML = "";
  const name = el.dataset.name || "";
  setStatus("Reading ingredients and nutrition from Blinkit");
  try {
    const r = await fetch(`/api/analyze/blinkit/${pid}?name=` + encodeURIComponent(name));
    const d = await r.json();
    if (d.error) return fail(d.error);
    renderAnalysis(d, {name});
  } catch (e) { fail(e.message); }
}

async function doPhoto(input) {
  const f = input.files[0];
  if (!f) return;
  $("err").innerHTML = ""; $("results").innerHTML = "";
  setStatus("AI is reading your label photo");
  const fd = new FormData();
  fd.append("photo", f);
  try {
    const r = await fetch("/api/analyze/photo", {method: "POST", body: fd});
    const d = await r.json();
    if (d.error) return fail(d.error);
    renderAnalysis(d, {});
  } catch (e) { fail(e.message); }
  input.value = "";
}

function renderAnalysis(d, extra) {
  const macroCell = (k, label) => d.macros[k]
    ? `<div class="macro"><b>${d.macros[k].value}<small>${d.macros[k].unit}</small></b><span>${label}</span></div>` : "";

  const findings = d.findings.length
    ? d.findings.map(f => `
      <div class="finding">
        <span class="chip" style="background:${f.color}">${esc(f.label)}</span>
        <div><b>${esc(f.name)}</b><small>${esc(f.note)}</small></div>
      </div>`).join("")
    : `<div class="allclear"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>No banned or flagged additives detected</div>`;

  const alts = d.alternatives.length
    ? d.alternatives.map(productCard).join("")
    : '<p class="empty">No live alternatives on Blinkit right now.</p>';

  const metaBits = [d.brand, d.macros_basis ? "macros " + d.macros_basis : "", d.source === "photo" ? "label photo" : "via Blinkit"]
    .filter(Boolean).map(esc).join(" &middot; ");

  setStatus("");
  $("results").innerHTML = `
    <div class="card">
      <div class="pname">${esc(d.name || extra.name || "Product")}</div>
      <div class="pmeta">${metaBits}</div>

      <div class="sect">
        <div class="sect-label"><em>01</em> &nbsp;Macros</div>
        <div class="macros">
          ${macroCell("energy","Energy")}${macroCell("protein","Protein")}${macroCell("carbs","Carbs")}
          ${macroCell("sugar","Sugar")}${macroCell("fat","Fat")}${macroCell("sat_fat","Sat fat")}
          ${macroCell("trans_fat","Trans fat")}${macroCell("fibre","Fibre")}${macroCell("sodium","Sodium")}
        </div>
      </div>

      <div class="sect">
        <div class="sect-label"><em>02</em> &nbsp;Additive risk &middot; ${d.findings.length}</div>
        ${findings}
      </div>

      <div class="sect">
        <div class="sect-label"><em>03</em> &nbsp;Ingredients</div>
        <p class="ingr" style="margin-top:12px">${esc(d.ingredients)}</p>
      </div>
    </div>

    <div class="alt-head">Better swaps on Blinkit</div>
    <p class="alt-sub">${esc(d.alt_reason || "")} Tried: ${d.alt_terms.map(esc).join(", ")}</p>
    <div class="alts">${alts}</div>`;
  window.scrollTo({top: 0, behavior: "smooth"});
}

$("q").addEventListener("keydown", e => { if (e.key === "Enter") doSearch(); });
</script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(PAGE)


if __name__ == "__main__":
    # host=0.0.0.0 so you can open it from your phone on the same WiFi:
    # http://<your-PC-LAN-IP>:5000
    app.run(debug=False, host="0.0.0.0", port=5000)
