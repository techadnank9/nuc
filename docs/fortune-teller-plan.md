# Robot Fortune Teller: build plan

Creative Applications track, MakerMods Physical AI Hacks (Nov 7 to 8, 2026). Station NUCK-8, Maker Arms, Dream-1, LiveKit.

## The pitch (one sentence)

You ask the fortune teller a question out loud; its arms draw a card from the deck, reveal it to you, and it reads your fortune. If its hands fail, it asks you for help, in character.

Why the arm is the point: a screen can pick a random card, but it cannot *draw* one. The physical draw is the ritual.

## The demo, second by second (about 60 s)

| Time | What happens | Who does it |
|---|---|---|
| 0 to 10 s | Guest walks up. Voice: "Come, sit. What do you wish to know?" Guest asks a question. | LiveKit voice agent |
| 10 to 15 s | "Let us see what the cards say..." Agent calls `draw_card()`. | Voice agent, harness |
| 15 to 40 s | Right arm reaches into the deck tray, grips the front card by its handle, lifts it and drops it into the reveal stand facing the guest. | Dream-1 (fine-tuned) |
| 40 to 45 s | Overhead camera reads the card's tag: "The Sun". | Harness |
| 45 to 60 s | Agent reads the fortune, tied to the guest's question. | Voice agent |
| If the grab fails | The run stops and the arms rest. "The spirits are hesitant... would you set the card back in the deck?" Guest reseats it and steps back, arm retries once. | Harness, voice agent |

## The key design decision: one motion, done perfectly

Dream-1 is a learned policy. Every extra task, slot or variation needs more demonstrations and lowers the success rate. So the arm does **exactly one task, always the same motion**:

> "Draw the front card from the deck and place it in the stand."

The randomness comes from the deck, not the robot. The deck tray is a gravity feeder: cards lean against each other at an angle and the front card always sits in the same place. The guest (or a helper) shuffles and loads the deck. Same grab every time means fewer demos and higher reliability.

## System overview

```
Guest's voice
   │
   ▼
LiveKit voice agent  (laptop: STT → LLM "fortune teller" → TTS)
   │  tool call: draw_card()
   ▼
Harness  (laptop, talks to NUCK-8 over SSH)
   ├─ 1. start dreamcli robot run --task "<draw task>" on the station
   ├─ 2. wait the action budget (~25 s), then stop the run (Ctrl+C: arms home, release)
   ├─ 3. grab one overhead camera frame, read the ArUco tag on the card in the stand
   └─ 4. return {"card": "The Sun"} or {"stuck": true}
   │
   ▼
Voice agent reads the fortune (card meaning + guest's question), or asks for help
```

Notes:
- The run must finish the whole place inside the budget: when dreamcli stops, the arms go home and go limp, so the card has to already be in the stand.
- dreamcli owns the cameras while it runs, so the harness reads the card only after the run stops.
- One program drives the arms at a time. The harness refuses to start if `dhk-collect` or another `dreamcli` holds them (as `run.sh` already does).

## Hardware to make

| Part | Spec | Status |
|---|---|---|
| Card v1 (The Sun) | 63 × 88 mm, 14 mm grip handle | Ordered from Mirdain, request 214a889f |
| Card v2 × 7 | v1 plus 4 mm handle chamfers and a 0.6 mm front sticker pocket. Deck: Sun, Moon, Star, Wheel of Fortune, Lovers, Strength, World (original art). | To model |
| Deck tray (gravity feeder) | Cards lean at ~15°, front card's handle always at the same spot, side walls with 3 mm play | To model |
| Reveal stand | Angled slot (~60°) with a wide funnel mouth, so a card dropped roughly above it slides in. Faces the guest and the overhead camera. | To model |
| Stickers | Full-colour card art on sticker paper, plus a 25 to 30 mm ArUco tag (DICT_4X4_50, id = card number) on each face; the overhead camera looks forward across the table, so tags face it (tag id = card number) | To print |
| Table mat | Dark cloth, marked spots for tray and stand, so they never move between demos and runs | Buy or improvise |

Mount the tray and stand to the table (tape or clamps). If they move, the model's demos no longer match.

## Data collection (Quest teleop, `dhk-collect`)

- Task text, identical every time: `draw the front card from the deck and place it in the stand`
- **50 to 60 clean episodes**, 15 to 25 s each, right arm only, left arm resting.
- Vary within reason: different card on the front each time, small shifts of the deck (±5 mm), normal room lighting changes. Never move the tray or stand.
- **8 to 10 recovery episodes**: card slightly crooked in the tray, grab adjusts.
- Start each episode from the same rest pose; end with the arm back near it.
- Drop bad episodes right away (`dhk-dataset drop`, confirm numbers first).

## Fine-tuning (the long pole)

1. `tools/add_eef.py <dataset> --arm maker` (local, free).
2. `tools/finetune.py <dataset>-eef --arm maker --name fortune --suffix fortune --check` (local, free).
3. Queue for real (costs credits, $200 at last check: confirm with the organizers first).
4. Training takes **7 to 8 hours**, plus queue time, plus up to an hour for the first start.

**Queue it by 9 pm tonight**, so the model is ready by early morning.

## Harness (code)

`harness/draw_card.py`, run from the laptop:

- `draw_card(timeout_s=25)`: preflight (arms free, links up), start the run over SSH, stop it after the budget, read the tag, return the result.
- Success check: a tag is detected in the stand region of the overhead frame. Anything else counts as a failure.
- Recovery: at most **one** retry after the guest helps. After two failures, the agent ends gracefully ("The cards are tired tonight...").
- Log every attempt (time, success, card, duration) to a CSV, to know the real success rate before the pitch.

## Voice agent (LiveKit)

- LiveKit Agents (Python), LiveKit Cloud project, run with `python agent.py dev`.
- One tool: `draw_card()`, wrapping the harness.
- Prompt: a warm, theatrical fortune teller. Short sentences. Never more than ~15 s of speech at a time. Card meanings in a small table it must use (upright only).
- "Stuck" path: ask the guest to help, in character, then call `draw_card()` once more.
- Test the whole conversation today with a *human* drawing the card, while the model trains.

## Timeline

| When | Who | What |
|---|---|---|
| **Sat Nov 7, now to 3 pm** | Arms | Switch the kit config to YAM; dry run; first teleop with the Quest |
| | Hardware | Model card v2, deck tray, reveal stand; send to print; print stickers |
| | Voice | LiveKit agent skeleton, prompt, card table |
| **3 pm to 7 pm** | Arms | Fit tray + stand on the table; collect 50 to 60 demos |
| | Voice | `draw_card()` tool with a manual stub; full conversation working |
| **7 pm to 9 pm** | Arms | Check dataset, add_eef, `--check`, **queue the fine-tune** |
| | All | Harness: run base Dream-1 through the whole loop as a dry run |
| **Overnight** | | Model trains |
| **Sun Nov 8 morning** | Arms | First live runs with the fine-tune; measure success rate over 10 tries |
| | All | Join voice + harness + arm; tune the action budget |
| **Midday** | Demo | Film a rough demo early; fix the worst failure; film the clean one (one continuous shot) |
| **Before judging** | Pitch | 2 to 3 min pitch walking the rubric in order |

## Roles (adjust to team size)

- **Arms**: setup, teleop demos, fine-tune, live runs (always at the e-stop).
- **Hardware**: cards, tray, stand, stickers, table layout.
- **Voice**: LiveKit agent, prompt, fortune table.
- **Harness + demo**: draw_card, logging, video, pitch.

## Risks and fallbacks

| Risk | Fallback |
|---|---|
| Fine-tune not ready or weak | Base Dream-1 with a simpler task ("push the front card forward"); the voice agent stays the same |
| Grab fails often | The "stuck" path *is* the feature: guest helps, in character. Film a run that shows it. |
| Card falls out of the stand | Make the funnel wider; record more place demos |
| Tag not read | Bigger tag, better light over the stand; the agent can ask "Which card do you see?" |
| Wrong arm type in a config | NUCK-8's arms are YAMs: Dream's `body: yam` is correct (a live run at 11:43 tracked its commands); the kit's `configs/station.yaml` says `maker` and must be switched to the YAM template before teleop |
| Printing delays | Ask Mirdain for a pickup time early; the Prusa CORE One+ in the room is the backup |

## Safety (every live run)

- One person on the emergency stop, nobody within reach of the arms.
- Arms rest where they can go limp before every start and stop.
- `--dry-run` first after any config change.

## Changes after Codex review (Nov 7, 1:15 pm)

1. Grasp tolerance is unproven: the 100 mm opening gives clearance along one axis only. Test card v1 in the real jaws (teleop) before printing the deck.
2. Start with **one card in a fixed slot**, loaded unseen by the guest; the 7-card gravity feeder is a stretch goal.
3. The time budget is not completion detection: measure homing time and what the policy does after placing; test SIGINT stop, disconnects and camera release.
4. The stuck path cannot ask the guest to guide the card into the hand: after a stop the arms are home and limp. The guest reseats the card in the tray instead.
5. Prove teleop transfers and a small fine-tune dataset in the next two hours; set a hard cut-off for queueing.
6. The harness now requires an empty stand before a draw, enforces 2 attempts per reading with a lock, and logs every attempt.
7. Film early; keep the conversation to name + question + draw + fortune.

## Correction (Nov 7, 1:40 pm)

NUCK-8's arms are **YAM arms**. The 11:43 live run under Dream's `body: yam` config moved them and the measured joints tracked the commands (`~/.dreamlabs/last-run.csv`). The setup wizard set to Maker Arm misread their motors (258 to 335 °C) and refused, which confirms it. Keep Dream's config; fix the kit's `configs/station.yaml` (`arm: maker`) to the YAM template before collecting demos. YAM fine-tunes run with `--action-space joint`.
