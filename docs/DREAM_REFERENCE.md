# Dream-1 / DreamAPI / dreamcli: practical reference for station NUCK-8

Written 2026-11-07 for the MakerMods Physical AI Hackathon. Station: two i2rt YAM arms (linear_4310 gripper,
96 mm stroke), three RealSense cameras (top, left, right), `dreamlabs` 1.0.37 and `dreamlabs-client` 1.1.3.

Sources. Everything here was read on the station (`pah@10.103.0.133`). Nothing was run that opens or moves an arm.

| Short name | Path on the station |
| --- | --- |
| client docs | `~/dream-env/lib/python3.12/site-packages/dreamlabs_client-1.1.3.dist-info/METADATA` (the long-form client README) |
| `dream/` | `~/dream-env/lib/python3.12/site-packages/dream/` |
| `robots/` | `~/dream-env/lib/python3.12/site-packages/robots/` |
| kit | `~/physical-ai-hacks/` (`README.md`, `docs/*.md`, `tools/finetune.py`) |
| station config | `~/.dreamlabs/station.yaml` |

What I could not reach:

- `platform.dreamlabs.ai/docs/fine-tuning/` needs a login; its pricing, hold, resume and cancel rules were read logged in on Nov 7 and are folded in below.
- The `robots` README, which `config.py` points to for the *Config file* and *Run report* sections, does not ship in the wheel. The `config.py` docstrings cover the same keys.
- Public pages: `dreamlabs.ai/research/dream-1` (read), and third-party write-ups such as eesel.ai. Numbers taken from those are marked as such.

---

## 1. How Dream-1 control works, end to end

### The model

Dream-1 is a "World Action Model": it predicts video and actions together (dreamlabs.ai/research/dream-1).

- **Inputs:** three cameras (head plus one wrist per arm), the joint state, and a text prompt.
- **Outputs:** one action chunk per call, covering about 1.6 s.
- **Two answer types:** joint angles, for bodies it knows natively such as the YAM, or end-effector (EEF) poses. EEF poses are solved to joints by IK on the client.
- **Status:** a research beta, not validated for safety.
- **Measured success on YAM** (third-party write-up): pick-and-place about 40%, insert/fit 24%.

### One cycle

The loop lives in `robots/control.py` and the client in `dream/`.

1. **Cameras.** Each camera captures at its configured size, 30 fps on this station. Every frame is downscaled to `policy_image_size` (default 640x360) and JPEG-encoded on a separate thread.

   The resize **stretches**: it never crops (`robots/cameras.py shape_warning`). The model itself resizes to 320x176 (client docs, *Running a fine-tuned model*).

2. **Observation window.** The client keeps one frame every `1/obs_fps` s (5 fps) and holds the last `obs_window` frames per camera (9). So the model sees about 1.6 s of video history plus the newest joint state and the prompt (`dream/_window.py`).
3. **`get_action()`.** The client sends the window, state and prompt and gets back a chunk of **48 rows** (`action_horizon: 48`).

   Each row is 6 joint targets plus a gripper value (0 = closed, 1 = open). The rows are 30 Hz actions: the training data is 30 fps.

4. **Seam smoothing.** This is always on and has no setting (`dream/smoothing.py`, `dream/seam_join.py`).
   - A Savitzky-Golay filter (window 11, order 3) removes jitter.
   - The first 16 rows are then a velocity blend from the motion the arm is on into the new plan, so position and velocity carry over the seam.
5. **Playback.** The loop plays one row per tick at `control_hz`, which is 20 Hz by default on the YAM.

   Since the rows are 30 Hz actions, **the robot runs every plan at 2/3 of the demonstrated speed** (`AppConfig.control_hz` docstring: "the safer pace").

6. **Command path.** Each tick does this, in order (`control.py`):
   1. Ramp: blend from the measured pose for `ramp_s` after start and after each new session.
   2. Grip force cap.
   3. Clamp: end-effector speed and gripper step.
   4. Command writer: interpolates to the motors at `command_hz` (250 Hz), or System 0 takes over (below).

### Pacing: sync or async (`dream/pacing.py`, `robots/run.py`)

- **async:** the next request goes out while rows are still running, timed so it lands as the arm finishes `execution_horizon` (30) rows. The arm never stops.
- **sync:** the arm runs all 48 rows, then **holds still** while the next chunk is computed.
- **How the mode is picked:** by the median of the last 5 round trips against a threshold.
  - The client's own default threshold is 700 ms.
  - dreamcli overrides it: **900 ms** with `robot run` (no System 0), and **1000 ms** with `robot up` (System 0).
- **Why 900:** at 20 Hz, a 900 ms round trip is 18 rows; 18 + 30 = 48 rows is all one chunk covers.
- **This station's round trips** (`~/robots-inference.jsonl`, Nov 6 runs): median `rtt_ms` 700 (max 3053), `serve_infer_ms` 305, `gateway_ms` 573, `network_ms` about 12.

  So the time is spent at Dream's gateway and serve, not on our network. At 0.8 to 1 s per call you sit right at the async/sync edge. In sync the arm pauses about 1 s every 2.4 s (48 rows at 20 Hz).

- **Rate limit:** 3 calls in quick succession, then one every 0.7 s per session (client docs, *Running a fine-tuned model*).
- **Docs' figure** for a running model: "about 250 to 450 ms through the gateway".

### System 0 (`robots/sys0/`)

System 0 is a learned motor-level tracker: a small MLP running at 250 Hz on the CPU. It sets joint torques so the measured arm follows the plan.

- **Effect:** it cuts the arm's lag behind the plan from about **200 ms (plain PD) to 60 to 90 ms**.
- **Gripper:** stays a plain position servo, governed by the grip force cap.
- **When it runs:** `dreamcli robot up` (the station agent) runs it **on a YAM by default**. `dreamcli robot up nosys0` turns it off.
- **`dreamcli robot run` does not run System 0.** `run.py` never enables it, so one-shot runs use the 200 ms PD path.
- **Supervisor:** latches a hold on overspeed, a joint limit, tracking error, a clamped torque or a late tick. The run then ends with an error.

### Action spaces on the YAM

| Space | What runs | When to use |
| --- | --- | --- |
| `eef` (**YAM default** since 1.0.x, `config.YAM_ACTION_SPACE`) | The model's tool poses, solved to YAM joints by dream's IK (`ik_solve: traj` by default, which solves the whole chunk at once) | Base model `dreamlabs/dream-1-beta` |
| `joint` | The model's own joint chunk, as it comes | **Our YAM fine-tune.** The kit's YAM fine-tunes train joints only (`tools/finetune.py`: `use_eef=False`), so run them with `--action-space joint` (`robot run`) or `dreamcli robot up joint` (station agent) |

Notes:

- A model with no EEF head run on `eef` falls back to its joints, with one warning (client docs, *EEF checkpoints*).
- The YAM tool centre point (TCP) is 0.1347 m past the wrist flange, along the last link's z axis (`dream/fine_tuning.py`).
- The default YAM model is `dreamlabs/dream-1-beta` (`robots/bodies.py YAM_CHECKPOINT`).

---

## 2. station.yaml / AppConfig keys

All keys come from `robots/config.py` (`AppConfig`, `ArmConfig`). An unknown key fails at load. Keys left out of the file take the defaults below.

The current file holds: `body: yam`, both arms with `home`, the cameras, `action_horizon: 48`, `stale_after_s: 0.2`, `max_stall_ticks: 240`, `max_ee_speed_m_s: 0.6`, `face_blur: true`, `live.robot_id`, and `grip_force_cap: 2.0`.

### Grip strength and holding

| Key | Default | What it does | Tuning |
| --- | --- | --- | --- |
| `grip_force_cap` | **0.65** | Gripper effort (i2rt `gripper_eff`, motor torque, not newtons) a grasp is **held at**. See the control law below this table. `0` disables the cap and hands force back to the motor PD. | Code comment: "at 2.0 grasps come out harder than any task needs, and at 1.5 they crush cups". So **2.0 is the top of the documented range**: keep it for rigid objects, use about 1.0 to 1.5 for anything crushable. Raising it does nothing if the model never commands a close, or if the jaws close on air (section 6). |
| `max_gripper_step` | **0.05** per tick | Largest gripper change per tick: the full stroke takes 20 ticks, **1.0 s at 20 Hz**. This is the last word on gripper speed, applied after the force cap. | Every faster gripper command is **counted as a clamp**. A slow close can nudge a light object away before the jaws meet. 0.08 to 0.1 halves the close time. |
| `follower_*.sdk_gripper_force_limit` | false | i2rt's own motor-level 50 N gripper limiter | Leave off: it "fights the loop-side `grip_force_cap`". Only worth turning on with `grip_force_cap: 0`. |
| `follower_*.gripper` | `linear_4310` | i2rt gripper type | Matches our gripper. |
| `follower_*.ee_mass_kg` / `gravity_comp_factor` | 0.62 / 1.0 | i2rt gravity model | Leave. |

How the force cap works (`robots/gripper_force.py`):

- **Free motion:** pure position control.
- **In contact** (filtered effort above 25% of the cap, and the jaw nearly stalled): the close command is backed off until the measured effort equals the cap.
- **A floor:** the command never sits more than `cap/stiffness` closed of the measured jaw, so the PD can never squeeze past the cap.
- **A ceiling:** in contact, the command never opens past the measured jaw, so the cap cannot release the object.

### Speed and smoothness

| Key | Default | What it does | Tuning |
| --- | --- | --- | --- |
| `control_hz` | 20 | Row playback rate (the rows are 30 Hz actions) | 20 plays at 2/3 speed. 30 would play at the demonstrated speed, but it also changes the pacing math (30 rows = 1 s) and is untested here. Leave at 20 unless you are deliberately experimenting. |
| `max_ee_speed_m_s` | 0.6 (required in the file) | Caps each arm's grasp-point speed against the previous command; over the cap, the joint step is scaled down (`control._clamp_arm`). This is the "last line of defence". | Our last run asked at most 0.36 m/s and never reached the cap, so leave it. |
| `ramp_s` / `ramp_max_rad_s` | 2.0 s / 0.5 rad/s | Blend from the measured pose into the first target, after start and after every new session or prompt change | Shorter is a faster start but a harsher first move. |
| `command_hz` / `command_interp_max_s` | 250 / 0.1 s | Command-writer rate and interpolation cap | Leave. |
| `sync_above_ms` / `sys0_sync_above_ms` | 900 / 1000 ms (set in `run.py`) | Sync threshold without / with System 0 | Lower means holding still sooner on slow replies. |
| `action_horizon` | 48 (required) | Chunk length | Must stay 48. |
| `ik_solve` | `traj` | How an `eef` run solves poses to joints: `traj` (whole chunk) or `tick` (row by row) | `traj` cut jerk about 75% in Dream's tests. |
| `action_space` | `eef` on YAM | See section 1 | Use `joint` for our fine-tune. |

### What gets clamped, and why

The loop clamps two things (`control._clamp_arm`):

1. **The gripper step:** more than `max_gripper_step` per tick.
2. **End-effector speed:** more than `max_ee_speed_m_s`.

The first clamped command of a run is logged once ("command clamped: ..."). After that, only the `clamped` count in the trace and the inference log record them.

In our 17:15 run (190 s), **4.8% of ticks were clamped and none for speed** (peak 0.36 m/s against the 0.6 cap). **All our clamps are gripper-step clamps**, which are harmless but slow the close.

Other limits on the motion:

- **IK (`eef` space):** keeps joints within the URDF velocity limits, at most 3 rad/s.
- **System 0 supervisor:** holds the arm on overspeed or joint limits.

### Startup and timeouts

| Key | Default | What it does |
| --- | --- | --- |
| `first_chunk_timeout_s` | 120 s | Bounds a wait **with no answer** from DreamAPI. A warming pool is waited out for up to 30 min (`POOL_WARMING_LIMIT_S`). |
| (constant) `MOVE_AFTER_WAIT_LIMIT_S` | 60 s | A first chunk that lands more than 60 s after the first tick is **not run**: the run stops with `ModelReady` so the arms never start on their own. **Warm a fresh fine-tune with `--dry-run` first**, then start the real run. |
| `action_timeout_s` | 3.0 s | A refill older than this is discarded. |
| `stale_after_s` | 0.2 s | A camera frame older than this fails the tick. |
| `max_stall_ticks` | 240 | Empty-buffer ticks tolerated before the loop stops. |

Where our ~20 s to first motion goes with `dreamcli robot run`:

| Step | Time | Source |
| --- | --- | --- |
| Open arms and cameras | up to 5 s for first frames | `FIRST_FRAME_TIMEOUT_S` |
| Exposure settle | 2 s | `EXPOSURE_SETTLE_S` |
| `traj` IK compile | about 5 s (cached in `~/.cache/dream_ik`, up to 60 s on a slow core) | `TRAJ_WARMUP_S` |
| Home move | 3 s | `home.HOME_DURATION_S` |
| First round trip | about 0.7 to 1 s | |
| Ramp | 2 s | `ramp_s` |

`dreamcli robot up` (the station agent) **opens the arms and cameras once and pre-warms the IK at boot** (`station.py warm_client_ik`). After that, each Start from Live View pays only for the home move, the first call and the ramp.

### Cameras

| Key | Default | Notes |
| --- | --- | --- |
| `cameras[]` | | `name` must be `top`, `left` or `right`. Exactly one of `device`, `serial` or `source`. Also `width`, `height`, `fps` (30) and `rotate`. |
| `policy_image_size` | 640x360 | Every frame is **stretched** (not cropped) to this size. A non-16:9 camera triggers the setup warning "Running the robot squishes these...", and `dreamcli robot report` flags it as `stretched`. |
| `obs_window` / `obs_fps` | 9 / 5 | The checkpoint's `video_context_frames` and video rate. **Do not change.** |
| `exposure` | managed, `target_median` 90 | RealSense exposure steered between runs, never during one. |
| `face_blur` | true | Faces blurred before frames leave the box. |

**Our station has a camera-shape problem.** The `right` camera was changed at 15:15 from 1280x720 to **640x480**: the backup `station.yaml.before-usb2-1515` shows the change, apparently for USB2 bandwidth.

- 640x480 is 4:3, so every right-wrist frame is squished into 640x360. **The model sees the right wrist view 1.33x wider than reality**, and it is the right arm that grasps.
- The kit records the wrist cameras at 1280x720, center-cropped to 16:9, so recording and running also disagree.

Fix: use a 16:9 mode the USB link sustains. RealSense colour offers 640x360, 848x480 and 424x240. Then use the same view for recording and for running.

### Other keys

- `task`: default prompt.
- `checkpoint`: default model.
- `trace_path`: default `~/.dreamlabs/last-run.csv`.
- `inference_log_path`: the station agent writes `~/robots-inference.jsonl` when unset.
- `live.robot_id` and `live.cameras`.
- `rollouts.enabled`: saves every run as a LeRobot episode on disk; off by default.
- `home_on_key_refused`: default true; homes the arms when the key is refused for being out of credit.
- `home_before_motors_off`: used by `dreamcli setup` only.
- `follower_*.home`: home joints.
- `follower_*.temp_export`.

---

## 3. Prompting

- **Write short, concrete, plain-English instructions.** Dream's own example: "Pick up the mug by its handle and place it on the tray".
- **Base model:** the prompt is the only task signal. Name the object by colour or shape and name the destination.
- **Fine-tune:** the model learned the task texts in `meta/tasks.jsonl`. The client compares your runtime prompt with them and prints a `UserWarning` when it is "none of the tasks ... trained on", suggesting the closest one (`dream/trained_inputs.py`).

  So **record with `--task "<exact phrase>"` and run with the identical phrase** (kit `docs/running_your_model.md`).

- **Without `--task`:** episodes record the neutral text `do the demonstrated task`, and Dream's service adds dense labels from the overhead camera.

  Either way, reusing the recorded phrase is the documented advice. For a single-task model, also try the neutral text.

- **Changing the prompt mid-run** resets the episode, drops the buffer and ramps again (`control.set_task`).

---

## 4. Fine-tuning

### Pipeline

1. **Register the body (once).** The kit's YAM config is `src/dream_hackathon/embodiments/yam.json`:
   - `state` and `action` laid out as `right_arm[0:6]`, `right_gripper[6:7]`, `left_arm[7:13]`, `left_gripper[13:14]`.
   - `video` roles `overhead`, `wrist_l` and `wrist_r`.

   `tools/finetune.py` registers it as `hackathon-yam`.
2. **Check the dataset locally, free:**
   `~/dream-env/bin/python tools/finetune.py ~/hackathon_data/<set> --arm yam --check`
3. **Upload.** The upload zips the dataset into `TMPDIR`, checks free space first, and sends it in 64 MiB parts.

   Status goes `uploading` → `ready_to_process` → `processing` → `processed`, or `failed`. Keep the `ds_...` id.
4. **Queue the job.** The model id will be `dream-1-ft:<uid8>-<suffix>`. The price is **held from credits when the job is queued**, and **a queued job cannot be cancelled**.
5. **Wait.** Poll until `fine_tuned_model` is set, not merely until the status is `succeeded`. A failed job costs nothing (kit `docs/fine_tuning.md`).
6. **Run it.** The first call hits a **cold start: `DA-2000 pool_warming` for 25 to 50 minutes**. "Allow an hour."

### All `finetune()` options

`DreamClient.finetune(dataset, suffix, *, ...)` (`dream/fine_tuning.py create()`):

| Option | Values | Default | Notes |
| --- | --- | --- | --- |
| `steps` | multiples of 100, from 500 to 2000 | 1000 | **Priced by the step: 2000 costs 4x 500.** Time grows with steps. |
| `learning_rate` | 1e-6 to 1e-4 | 1e-5 | |
| `lr_schedule` | `constant`, `cosine` | `constant` | Both warm up first; cosine decays to 0 at the last step. "Cosine decay works best for specialization (a fine-tune on a single task)." |
| `base_share` | `0.5` or `0.0` | 0.5 | 0.5 mixes Dream's general data into each batch ("preserves general skills"); 0.0 trains on your data only ("fits your data most closely" but "can lose general skill"). A resumed job keeps the original's value. |
| `resume_from` | `dream-1-ft:...` (yours) | none | Continues training for `steps` **new** steps, **charged for those alone**, optimizer included, and publishes a new model under a new suffix. See the limits below the table. |
| `batch_size` | | service sets it | Leave unset. |
| `use_eef` | bool | False | Trains EEF poses too. The kit's YAM setup trains joints (False), so run with `--action-space joint`. |

Limits on `resume_from`:

- The model must be yours and have finished to its last step.
- The dataset must be on the same robot.
- `learning_rate`, `batch_size` and `use_eef` stay the original model's.
- Resuming runs **at the constant schedule**, so a cosine first run does not combine cleanly with resuming.

**`tools/finetune.py` only exposes `--learning-rate` and `--lr-schedule`.** For `steps`, `base_share` or `resume_from`, call `client.finetune(...)` from Python in `~/dream-env`. That is a paid call: confirm the price first.

```python
import dream
c = dream.DreamClient()
# dataset name is "<username>/<name>" as printed by the upload / c.get_datasets()
job = c.finetune("<username>/myteam-task", "myteam-task-v1", steps=1000, lr_schedule="cosine")  # PAID
print(job["id"], job["hyperparameters"])
```

### Price and time

- **Price (platform docs, Nov 7):** priced per step in hundreds: **$100 for 500 steps, $200 for 1000, $400 for 2000**.
  - The price is held from prepaid credits when the job is queued and charged when the model is published. Not enough credit: `payment_required` naming price and balance.
  - A run that ends early is charged only for its last saved checkpoint's share of steps; no checkpoint saved costs nothing.
  - While the hold is open, robot keys can spend only what is left above it. If the price equals the balance, keys pause until the job ends: add credit first if the arms must keep running Dream-1.
  - Once queued a job runs to the end; cancel is refused with `conflict`.
  - `resume_from` charges only the new steps, only for models finished within 29 days, and not for models trained with `lr_schedule="cosine"`.
- **Training time:** "about **7 to 8 hours** from submit to model" for a 1000-step job with 2 to 3 cameras. More steps take longer.
  - A third-party write-up says 4 to 5.5 h.
  - There may also be a queue: `dream.fine_tuning.queue()` is a free read.
- **Running cost** after training: about $3 per robot-hour, billed per call (dreamlabs.ai).

### Dataset requirements (`dream/datasets.py`; client docs, *Datasets*)

- **Format:** LeRobot v2.1, `codebase_version: "v2.1"`.
- **`meta/info.json`** needs `fps`, `chunks_size`, `total_episodes`, `total_frames`, `data_path`, `video_path` and `features`, with `action` and `observation.state` among the features.
- **Widths:** the parquet widths must equal the span width (14).
- **Videos:**
  - The `videos/` folders must be **exactly** the config's video keys.
  - Each video sits at `videos/chunk-NNN/<key>/episode_NNNNNN.mp4`, one per episode per key.
- **Consistency:** the same fps and feature names in every subfolder, and at least one episode.
- **Tasks:** every `task_index` must be defined in `meta/tasks.jsonl`.
- **Several sessions:** a parent folder of several datasets is uploaded as several sessions trained together.
- **Kit rules:**
  - An episode must be at least 1.6 s or it is not saved.
  - Recording runs at 30 fps.
  - Frames are cropped to 16:9 at 640x360.
  - The YAM state order is right arm, right gripper, left arm, left gripper.

### How many demos

- **Kit:** "**50 or more episodes** of one task, each 10 to 30 seconds".
- **Dream's blog:** 5 minutes of data improved a Maker Arm, and 98 minutes were used for YAM gripper swaps.

What makes demos train well (kit `docs/data_collection.md`):

- Do the task the same way every time: same arm, same approach side.
- Vary the object's position, not the method.
- Start and end with the arms still.
- Discard failures with B. A recovered grasp is fine.
- Never move the cameras.
- Move smoothly.

### Checks before and after

- **Free and local:** `tools/finetune.py ... --check` runs `validate_dataset`.
- **Paid, nothing moves:** `python -m dream.check_model SESSION --config policy.json [--episode 0] [--moments 8]`. It feeds recorded moments to a model and compares the chunk with your recorded actions, printing PASS or PROBLEM for answers, start, direction and size.
  - **Each moment is one billed call.**
  - It needs `policy.json` with the checkpoint and the embodiment.
  - It is useful against the base model before training, and against the fine-tune after.
- **`python -m dream.check -- <loop>`** and **`python -m dream.check_loop`** move the arms, so they are for a person to run. **A coding agent must not run them.**

### Failure modes

- **`conflict`:** the dataset is not yet processed, the suffix or name is already used, or the account is at its active-job cap.
- **`invalid_request`:** a knob is out of range, or the `resume_from` model cannot be continued.
- **`payment_required`:** not enough credits; the message names the price.
- **Dataset `failed`:** the cause is one of three:
  - your files: the error line names what to fix; re-upload under the same name;
  - the dataset is too large: split it;
  - Dream's side: `retry_dataset(id)` reprocesses without a re-upload.
- **Job `failed`:** `error` names a dataset fix, or says the training did not finish. No model is published, and the job costs nothing.
- **Running the model:**
  - `DA-2000 pool_warming`: the model is starting; retry after `retry_after_s`.
  - `DA-6000 cannot_scale`: the model cannot be started.
  - `DA-2000 rate_limited`: back off 0.7 s.

### Running the result on the YAM

```bash
source ~/dream-env/bin/activate
# 1) warm the model with a dry run (calls the model for real, writes nothing to the arms)
dreamcli robot run --checkpoint dream-1-ft:<uid8>-<suffix> --task "<exact recorded task>" --action-space joint --dry-run --view
# 2) person at the robot, hand on e-stop: same command without --dry-run
# or with System 0 and Live View Start:
dreamcli robot up joint     # then pick the checkpoint and press Start on platform Live View
```

- `--view` serves the DebugView on port 8093: the frames, state, chunk, timing and refusals of every round trip. Forward it with `ssh -L 8093:localhost:8093`.
- `dreamcli robot up --view` does the same for the station agent.
- `check_model` refuses `joint` for anything but the account's own fine-tune of that embodiment. On a YAM either head is allowed.

---

## 5. Diagnostics

| Tool | What it gives |
| --- | --- |
| `dreamcli robot report [trace]` | Reads `~/.dreamlabs/last-run.csv` and its `last-run_inputs/` folder. Prints PASS or PROBLEM for each camera (black, frozen, **stretched**), the state (NaN), the loop (pauses longer than 0.25 s) and each joint (**diverging**: off its command by more than 0.1 rad for 0.5 s; **oscillating**). Ends with the worst problem and its fix, plus a PNG of commanded against achieved for every joint. Sends nothing anywhere. |
| `last-run.csv` columns | `t_s, chunk_id, row_index, ramp_alpha, clamped`, `measured_l0..5`, `measured_r0..5`, `gripper_l`, `gripper_r` (measured 0..1), `commanded_l0..5`, `commanded_r0..5`, `cmd_gripper_l`, `cmd_gripper_r` (after cap and clamp), `ee_speed_l_m_s`, `ee_speed_r_m_s` (asked, before the clamp), `commands_written`. One row per tick; each run overwrites the last. `last-run_inputs/` keeps one set of model inputs per second. |
| Inference log, `~/robots-inference.jsonl` (station agent) | JSON lines of kind `inference` (`rtt_ms`, `serve_infer_ms`, `gateway_ms`, `network_ms`, bytes, `session_id`), `error`, `stall`, `run_start` and `run_stop` (with chunk, stall and clamp counts). Set `inference_log_path` to get one from `robot run`. |
| `dreamcli robot log [-n N]` | Tail of `~/.dreamlabs/station.log`. A startup failure is printed first as a single line naming the device and the fix. |
| Client log, `~/.dream/logs/dream.log` | Session boundaries and warnings. `DREAM_LOG=debug` raises the detail. |
| `client.last_timing` | Per call: `rtt_ms`, `serve_infer_ms`, `gateway_ms`, `network_ms`, `up_mbps`, `tcp_*`. |
| `DreamSlowNetworkWarning` | Fires when the median round trip of the last 5 is at least 1000 ms, and names network or model. |
| Inputs-checked reminder | One `UserWarning` per run until a person records `python -m dream.input_check <id>`. A person must do that, not an agent. Done here on Nov 7 (`~/.dreamlabs/inputs_checked`). |

### Common errors and fixes

| Error | Fix |
| --- | --- |
| `ModelReady` (first chunk more than 60 s after start) | The model was cold. Run again now that it is warm, or warm it with `--dry-run` first. |
| `pool_warming` for a long time | A fresh fine-tune takes 25 to 50 min. Keep a dry run going. |
| `ControlLoopError`: no chunk within `first_chunk_timeout_s` | No answer from DreamAPI. Check the network and credentials. |
| `camera '<x>' frame is ... s old` (`stale_after_s`) | USB bandwidth or cable. Use a lower resolution, but **keep it 16:9**. |
| CAN errors: "No buffer space available", "fail to communicate with motor N" (seen in `station.log` on Nov 6) | Run `dreamcli robot down`, check the power and CAN cables, then `robot up`, which brings the links up again. |
| `DA-1000` / `DreamAuthError` | Key refused, or out of credit: the station homes the arms (`home_on_key_refused`). |
| `DreamSessionExpired` | The server replaced the session mid-episode. The loop continues on the new session. |
| `DreamConfigError` at `policy()` | Unknown config key, checkpoint not in `get_models()`, or a camera role mismatch. |

---

## 6. For our team tonight

### Make grasps hold

1. **Fix the right-wrist camera shape first.** It runs at 640x480 and is squished into 640x360 for the model, and the right arm is the one that grasps.

   Pick a 16:9 RealSense mode the USB link sustains, such as 640x360 at 30 fps (or 848x480), for both `~/.dreamlabs/station.yaml` and the kit's `configs/station.yaml`. Confirm with `dreamcli robot report`, which should show no `stretched` line.

   This is the most likely hidden cause of poor grasp placement. Recordings made tonight must use the same view the robot will run with.

2. **Check whether "slip" is really a missed grasp.** In the 17:15 run (cap 0.65), the right gripper's measured opening fell to **0.048**: the jaws closed to about 5 mm, nearly shut. With a rigid object between the fingers, the measured opening should stay at the object's width.
   - If the trace shows the opening collapsing toward 0 after the close, the jaws missed or lost the object. **More grip force will not fix that**; better approach and placement will (camera fix, System 0, fine-tune).
   - If it plateaus at the object's width and the object still slides out, it is force: try `grip_force_cap` 2.0.
3. **Keep `grip_force_cap: 2.0` for rigid objects**, the documented top. Do not enable `sdk_gripper_force_limit`.
4. **Raise `max_gripper_step` from 0.05 to about 0.1.** The jaws then close in 0.5 s instead of 1 s, which gives fewer chances to push the object away. All of our clamps were gripper-step clamps.
5. **Run through `dreamcli robot up` (System 0) instead of `dreamcli robot run`.** The arm then tracks the plan with 60 to 90 ms of lag instead of about 200 ms, which matters for where the fingers end up. It also removes most of the 20 s per-run startup: the arms, cameras and IK stay open, and you Start from Live View. For the fine-tune, use `dreamcli robot up joint`.
6. **Prompting:** use one short, concrete sentence naming the object and the destination, the same wording every time. Do not change it mid-run: a change resets the episode and ramps the arms again.

### Best fine-tune for about $150 to $200

1. **Check the price before anything else.** $100 / 500 steps, $200 / 1000, $400 / 2000.
   - With $150: 500 steps ($100) fits, 1000 steps ($200) does not. Leave credit above the hold so the arms can keep running during training.
   - The amount is held when the job is queued, and a queued job cannot be cancelled.
2. **Queue tonight.** Training takes 7 to 8 h for 1000 steps, plus any queue, plus a 25 to 50 min cold start on the first call.
   - Start the upload as early as possible.
   - Check `dream.fine_tuning.queue()` (a free read) to see the line.
3. **Data quality beats steps.** Collect 50 or more clean episodes of one task, 10 to 30 s each.
   - Use one strategy and one grasping arm.
   - Vary the object placement across the area the cameras see.
   - Record with `--task "<exact phrase>"`, and with the corrected camera shapes.
   - Run `tools/finetune.py ... --check` (free) before uploading.
4. **Recommended settings for one paid job:** `steps=1000` if affordable (else 500), `lr_schedule="cosine"`, `learning_rate` left at 1e-5, `base_share` left at 0.5.
   - Cosine is documented as best for single-task specialization.
   - Base share 0.5 is safer with about 50 demos. Use 0.0 only with many very consistent demos and a fixed scene.
   - `tools/finetune.py` cannot set steps or base_share; use the Python snippet in section 4.
   - If you plan to **resume** later (pay 500, test, then pay 500 more with `resume_from`), the first job must use the **constant** schedule. That plan also needs two training waits, which will not fit before the demo.
5. **Have a fallback.** The base model `dreamlabs/dream-1-beta` (eef head) keeps working while the job trains, and if it does not finish in time.
6. **When the model is ready:**
   1. Warm it with a `--dry-run --action-space joint` run until real chunks come back.
   2. Then run it for real, with a person on the e-stop.
   3. Then run `dreamcli robot report`.
