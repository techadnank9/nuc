"""Uploads a dataset to the Dream API and fine-tunes Dream-1 on it, with a chosen number of training steps.

Our copy of the hackathon kit's tools/finetune.py (dreamlabs-co/physical-ai-hack-2026, Apache-2.0) with two additions:
--steps (500 to 2000 in hundreds; default 700, about $140 at $20 per 100 steps) and --base-share (0.5 mixes in
Dream Labs' general data, default; 0.0 trains on ours only). Before queueing it prints the job and waits for "yes":
a queued job is charged from the account's credits and cannot be cancelled. Run it on the station:

    ~/dream-env/bin/python ~/nuc-tools/finetune_steps.py ~/hackathon_data/card-reveal --arm yam --check
    ~/dream-env/bin/python ~/nuc-tools/finetune_steps.py ~/hackathon_data/card-reveal --arm yam \\
        --name card-reveal --suffix card-reveal --steps 700 --lr-schedule cosine

The kit's own description follows.

Run it in ~/dream-env (the Dream library's environment, with your API key saved by the Setup API steps):

```
# YAM: the collected dataset as it is
~/dream-env/bin/python tools/finetune.py ~/hackathon_data/cubes --arm yam --name cubes --suffix cubes
# Metal or Maker arm: the dataset tools/add_eef.py wrote
~/dream-env/bin/python tools/finetune.py ~/hackathon_data/cubes-eef --arm metal --name cubes --suffix cubes
```

The steps: register your robot's embodiment (once; reused after), check the dataset on this computer, upload it,
wait for processing, queue the job and wait for your model. Training takes about 7 to 8 hours and costs the price
the Billing page shows (a job's price is held from your credits when it is queued). Every wait can be stopped with
Ctrl+C and picked up again: `--dataset-id ds_...` resumes after the upload, `--job-id ftj_...` after the job is queued.
`--check` only runs the dataset checks, on this computer: nothing is sent and nothing is charged.
"""

import argparse
import json
import pathlib
import sys
import time
from typing import Any

import dream

EMBODIMENTS = pathlib.Path("~/physical-ai-hacks/src/dream_hackathon/embodiments").expanduser()
## Per arm: its embodiment file, the embodiment name registered by default, and whether its fine-tune trains the
## tool poses (Dream's MakerMods models are end-effector models; a YAM fine-tune trains joints).
ARMS = {
    "yam": ("yam.json", "hackathon-yam", False),
    "metal": ("makermods.json", "hackathon-metal", True),
    "maker": ("makermods.json", "hackathon-maker", True),
}
DATASET_POLL_S = 30
JOB_POLL_S = 60


def embodiment_for(client: dream.DreamClient, name: str, config: dict[str, Any]) -> str:
    """Registers the embodiment, or reuses the one already registered under that name if its config is the same.

    @return The embodiment's full name, `<username>/<name>`.
    """
    try:
        return client.add_embodiment(name, config).name
    except dream.DreamServerError as error:
        if error.code != "conflict":
            raise
    existing = next(e for e in client.get_embodiments() if e.name.endswith(f"/{name}"))
    if existing.embodiment_config != config:
        sys.exit(f"{existing.name} is registered with another config; pass --embodiment with a new name")
    return existing.name


def wait_for_dataset(client: dream.DreamClient, dataset_id: str) -> Any:
    """Polls a dataset until it is processed.

    @throws SystemExit Processing failed; the message says why.
    """
    dataset = client.get_dataset_status(dataset_id)
    while dataset.status not in ("processed", "failed"):
        print(f"dataset {dataset.id}: {dataset.status}", flush=True)
        time.sleep(DATASET_POLL_S)
        dataset = client.get_dataset_status(dataset_id)
    if dataset.status == "failed":
        sys.exit(f"dataset {dataset.id} failed processing: {dataset.error}")
    return dataset


def wait_for_model(client: dream.DreamClient, job_id: str) -> str:
    """Polls a fine-tuning job until its model is published.

    @return The model id, `dream-1-ft:<account>-<suffix>`.
    """
    job = client.get_finetuning_status(job_id)
    while job["fine_tuned_model"] is None and job["status"] in ("queued", "running", "succeeded"):
        progress = job.get("progress") or {}
        detail = job.get("queue_position") if job["status"] == "queued" else progress.get("phase")
        loss = f" loss {progress['loss']:.4f}" if progress.get("loss") is not None else ""
        print(f"job {job['id']}: {job['status']} {detail or ''}{loss}", flush=True)
        time.sleep(JOB_POLL_S)
        job = client.get_finetuning_status(job_id)
    if not job["fine_tuned_model"]:
        sys.exit(f"job {job['id']} ended {job['status']} without a model: {job.get('error')}")
    if job.get("notice"):
        print(f"note: {job['notice']}")
    return job["fine_tuned_model"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("dataset", type=pathlib.Path, nargs="?", help="the LeRobot v2.1 dataset folder")
    parser.add_argument("--arm", required=True, choices=sorted(ARMS))
    parser.add_argument("--name", help="the dataset's name on the service: lowercase letters, digits and hyphens")
    parser.add_argument("--suffix", help="the end of your model's id: 1 to 40 lowercase letters, digits, hyphens")
    parser.add_argument("--embodiment", help="the embodiment name to register or reuse (default: hackathon-<arm>)")
    parser.add_argument("--learning-rate", type=float, help="1e-6 to 1e-4 (default: the service's, 1e-5)")
    parser.add_argument("--lr-schedule", choices=("constant", "cosine"), help="cosine suits a single-task fine-tune")
    parser.add_argument("--dataset-id", help="resume: an uploaded dataset's id (ds_...); skips the upload")
    parser.add_argument("--job-id", help="resume: a queued job's id (ftj_...); only waits for the model")
    parser.add_argument("--check", action="store_true", help="only check the dataset, locally; send nothing")
    parser.add_argument("--steps", type=int, default=700, help="training steps, 500 to 2000 in hundreds (default 700)")
    parser.add_argument("--base-share", type=float, default=0.5, choices=(0.5, 0.0),
                        help="0.5 mixes in Dream Labs data (default), 0.0 trains on this dataset only")
    parser.add_argument("--yes", action="store_true", help="queue without asking (the price is still printed)")
    args = parser.parse_args()
    file_name, default_embodiment, use_eef = ARMS[args.arm]
    config = json.loads((EMBODIMENTS / file_name).read_text())
    if args.check:
        if args.dataset is None:
            parser.error("--check needs the dataset folder")
        report = dream.datasets.validate_dataset(args.dataset.expanduser(), config)  # needs no API key
        if not report.ok:
            sys.exit(f"the dataset needs fixing before it uploads: {report.error}")
        print(f"ready to upload: {report.episodes} episodes, {report.frames} frames at {report.fps} fps")
        for note in report.notes:
            print(f"note: {note}")
        return

    client = dream.DreamClient()
    job_id = args.job_id
    if job_id is None:
        if args.suffix is None:
            parser.error("--suffix is required to start a job")
        dataset_id = args.dataset_id
        if dataset_id is None:
            if args.dataset is None or args.name is None:
                parser.error("the dataset folder and --name are required to upload")
            path = args.dataset.expanduser()
            report = client.validate_dataset(str(path), config)
            if not report.ok:
                sys.exit(f"the dataset needs fixing before it uploads: {report.error}")
            print(
                f"{report.episodes} episodes, {report.frames} frames at {report.fps} fps, cameras {report.video_keys}"
            )
            embodiment = embodiment_for(client, args.embodiment or default_embodiment, config)
            dataset = client.upload_dataset(str(path), args.name, embodiment)
            dataset_id = dataset.id
            print(f"uploaded as {dataset.id} ({dataset.name}); keep this id to resume with --dataset-id")
        dataset = wait_for_dataset(client, dataset_id)
        if args.steps % 100 or not 500 <= args.steps <= 2000:
            sys.exit("--steps must be a multiple of 100 from 500 to 2000")
        print(f"\nabout to queue: dataset {dataset.name}, model suffix {args.suffix}, {args.steps} steps "
              f"(about ${args.steps // 100 * 20} at $20 per 100 steps; the platform holds its own price), "
              f"base_share {args.base_share}, lr_schedule {args.lr_schedule or 'constant'}, "
              f"learning_rate {args.learning_rate or 'default 1e-5'}")
        print("a queued job is charged from the account's credits and cannot be cancelled.")
        if not args.yes and input("type yes to queue it: ").strip().lower() != "yes":
            sys.exit(f"not queued. The dataset stays uploaded: resume with --dataset-id {dataset_id}")
        job = client.finetune(
            dataset.name, args.suffix, steps=args.steps, base_share=args.base_share, use_eef=use_eef,
            learning_rate=args.learning_rate, lr_schedule=args.lr_schedule,
        )
        job_id = job["id"]
        print(f"queued job {job_id} (position {job.get('queue_position')}); keep this id to resume with --job-id")
    model = wait_for_model(client, job_id)
    print(f"\nyour model: {model}")
    print("try it without moving the arms first, from the robot's computer in ~/dream-env:")
    print(f'  dreamcli robot run --checkpoint {model} --task "<the task, in plain words>" --dry-run')


if __name__ == "__main__":
    main()
