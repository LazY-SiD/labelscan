import json

d = json.load(open("analyze_test.json", encoding="utf-8"))

if "error" in d:
    print("ERROR:", d["error"])
    raise SystemExit(1)

print("source:", d["source"], "| basis:", d.get("macros_basis"))
print("ingredients:", (d["ingredients"] or "")[:300])
print("\nmacros:")
for k, v in d["macros"].items():
    print(f"  {k}: {v['value']} {v['unit']}  (raw: {v['raw']})")
print("\nfindings:")
for f in d["findings"]:
    print(f"  [{f['tier']}] {f['name']} -- matched '{f['matched']}'")
    print(f"      {f['note'][:90]}")
print("\nalt terms:", d["alt_terms"])
print("alt reason:", d["alt_reason"])
print("\nalternatives:")
for a in d["alternatives"]:
    print(f"  - {a['name'][:45]} | {a['price']} | {a['variant']} | via '{a['term']}'")
