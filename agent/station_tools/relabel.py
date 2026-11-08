"""Relabels a LeRobot v2.1 dataset: episodes [0, split) get task 0, the rest task 1."""
import glob, json, sys
import pyarrow as pa, pyarrow.parquet as pq
d, split = sys.argv[1], int(sys.argv[2])
TASKS = ["can you shuffle these cards using both arms",
         "pick up the card from the handle and show it, then put it back down"]
with open(f"{d}/meta/tasks.jsonl", "w") as f:
    for i, t in enumerate(TASKS):
        f.write(json.dumps({"task_index": i, "task": t}) + "\n")
eps = [json.loads(l) for l in open(f"{d}/meta/episodes.jsonl") if l.strip()]
with open(f"{d}/meta/episodes.jsonl", "w") as f:
    for e in eps:
        e["tasks"] = [TASKS[0 if e["episode_index"] < split else 1]]
        f.write(json.dumps(e) + "\n")
for p in glob.glob(f"{d}/data/*/*.parquet"):
    t = pq.read_table(p)
    ep = t.column("episode_index")[0].as_py()
    ti = 0 if ep < split else 1
    col = pa.array([ti] * t.num_rows, type=t.schema.field("task_index").type)
    t = t.set_column(t.schema.get_field_index("task_index"), "task_index", col)
    pq.write_table(t, p)
info = json.load(open(f"{d}/meta/info.json")); info["total_tasks"] = len(TASKS)
json.dump(info, open(f"{d}/meta/info.json", "w"), indent=4)
print("relabelled", len(eps), "episodes:", sum(e["episode_index"] < split for e in eps), "shuffle,",
      sum(e["episode_index"] >= split for e in eps), "reveal")
