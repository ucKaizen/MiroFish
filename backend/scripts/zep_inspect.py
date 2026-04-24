import os
from collections import Counter, defaultdict
from zep_cloud.client import Zep

GRAPH_ID = "mirofish_c881d86ff4cf4082"
PANELISTS = {"Margaret", "Raj", "Linda", "Fiona", "David", "Chioma", "Tom", "Gareth", "Helen", "Orla", "Sam", "Dev", "Jane", "Amy", "Klaus"}

client = Zep(api_key=os.environ["ZEP_API_KEY"])
nodes = list(client.graph.node.get_by_graph_id(graph_id=GRAPH_ID))
print(f"TOTAL ENTITIES: {len(nodes)}\n")

type_counts = Counter()
by_type = defaultdict(list)
for n in nodes:
    labels = getattr(n, "labels", []) or []
    name = getattr(n, "name", "<unnamed>")
    real_type = next((l for l in labels if l != "Entity"), "Entity")
    type_counts[real_type] += 1
    by_type[real_type].append(name)

print("BY TYPE:")
for t, c in sorted(type_counts.items(), key=lambda x: -x[1]):
    print(f"  {c:3d}  {t}")
print()

found = set()
for n in nodes:
    nm = getattr(n, "name", "")
    for p in PANELISTS:
        if nm == p or nm.startswith(p + " ") or nm.startswith(p + "'"):
            found.add(p)
missing = PANELISTS - found
print(f"PANELISTS FOUND AS NAMED ENTITIES: {len(found)}/15")
print(f"  Found:   {sorted(found)}")
print(f"  Missing: {sorted(missing)}")
print()

print("ENTITIES BY TYPE:")
for t, names in sorted(by_type.items(), key=lambda x: -len(x[1])):
    print(f"\n[{t}] - {len(names)} entities")
    for nm in names:
        marker = "  <- PANELIST" if nm in PANELISTS else ""
        print(f"  - {nm}{marker}")
