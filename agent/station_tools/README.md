# Station helper scripts (NUCK-8)

Copied to the station under `~/nuc-tools/` and run there. None of them moves the arms.

| Script | What it does |
| --- | --- |
| `check_eps.py <dataset>` | One line per saved episode: length, how far each arm moved, how far each gripper closed; flags takes where the arms stood still |
| `ep_sheet.py <dataset> <episode> <out.jpg>` | Contact sheet of one episode: overhead, right wrist and left wrist frames across the take |
| `relabel.py <dataset> <split>` | Gives episodes `0..split-1` the shuffle task and the rest the reveal task (tasks.jsonl, episodes.jsonl, parquet task_index, info.json) |
| `probe_prompts.py "<prompt>" ...` | Offline prompt check: sends saved camera frames + state to Dream-1 with each prompt and prints the planned first chunk; no arm is opened |
| `keep_traces.sh` | Keeps a copy of every Dream-1 run trace (`~/.dreamlabs/last-run.csv`) in `~/run-traces/` |

The fine-tune itself uses `agent/finetune_steps.py` (also in `~/nuc-tools/`).

Nov 7 dataset: `~/hackathon_data/fortune-final` = 41 takes (32 two-arm card shuffles, 9 one-arm card reveals) from
`~/hackathon_data/fortune` (51 recorded; 10 dropped: arms still, too short, or a hand in the shot). Uploaded as
`ds_885f0d9dc3df021b`, fine-tune of 500 steps.
