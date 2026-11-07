# How we made the fortune card (and how to refine it)

Robot Fortune Teller · MakerMods Physical AI Hacks, Founders Inc, Nov 7 to 8, 2026 · station NUCK-8

This explains how the 3D-printed fortune card was designed, checked, printed and tested on the robot, and gives a brief for refining the next version: the **gothic arch tab** (concept 4).

---

## 1. The problem

The robot (two **i2rt YAM** arms with parallel-jaw grippers, driven by the **Dream-1** model) has to pick a card out of a deck and place it in a reveal stand. A normal flat card is almost impossible for a parallel gripper: it is 0.3 mm thick and lies flat. So the card had to be redesigned **for the robot first, then for looks**.

Gripper facts that drive every dimension (i2rt `linear_4310`, see `SPEC.md`):

| Fact | Value | Why it matters |
|---|---|---|
| Gripper type | Two parallel fingers | Needs two flat, parallel faces to squeeze |
| Opening (throw) | 95 to 96 mm | Anything 10 to 20 mm thick fits easily |
| Payload | 2 kg | A card is 7 to 15 g, so strength is never the issue |
| Real limit | Dream-1's aim (a few cm of position error, some wrist tilt) | The grip area must be **wide and forgiving** |

## 2. Card v1: the design that worked

**Idea:** the robot never grips the thin card. It grips a **handle** on the card's top edge.

- Card body: **63 × 88 × 4 mm** (poker size), 3 mm corner radius.
- Handle on top: **30 × 18 × 14 mm** block, flush with the card's back, standing 10 mm out of the front.
- Two **flat parallel grip faces 14 mm apart**, with 0.4 mm horizontal ribs for friction.
- 45° chamfers on the handle's top edges so a finger that lands on top slides down onto a grip face.
- Front: raised sun and "XIX" (The Sun). Back: 30 × 30 mm pocket for an ArUco tag sticker, so the camera can tell which card was drawn.

**Result:** on Nov 7 the base Dream-1 model (not yet fine-tuned) **picked card v1 up by its handle** on the real arm.

## 3. How it was made (the workflow)

1. **Spec first.** We wrote the requirements as a plain spec (`SPEC.md`): gripper facts, design rules, dimensions, required outputs and checks.
2. **Code-generated CAD (no Blender).** OpenAI **Codex** (CLI, model `gpt-6-astra`, high reasoning) wrote `generate_card.py`, a Python script that builds the card from simple solids with **trimesh** and **manifold3d** (robust boolean operations, so the mesh is always watertight). Every dimension is a variable, so changes are one-line edits.
3. **Automatic checks.** `check_card.py` verifies the mesh is watertight and one solid, sits flat on the bed (z = 0), has the right overall size, that the grip faces are 14 mm apart, and estimates the mass. Codex re-ran it until everything passed.
4. **Previews.** The script also renders front, back, side and "gripper fit" PNGs (`out/preview_*.png`) so a person can check the shape without opening CAD.
5. **Review loop.** A second Codex pass reviewed the plan and the grasp assumptions; its fixes went into v2 (bigger chamfers, sticker pocket, "one card in a fixed slot" approach).
6. **Outputs.** `out/card_19_sun.stl` and `.3mf`: about **7 g of PLA**, prints **flat on its back with no supports**.

Rebuild everything:

```bash
cd cards
python3 -m venv .venv && .venv/bin/pip install trimesh manifold3d numpy shapely matplotlib
.venv/bin/python generate_card.py && .venv/bin/python check_card.py      # card v1
.venv/bin/python generate_v2.py && .venv/bin/python check_v2.py          # card v2, deck tray, reveal stand
```

## 4. How it was printed

- **Service:** the free hackathon print service from **Mirdain × MakerMods** (QR code at the print table → https://mirdainsystems.com/labs.html).
- **Upload:** `card_19_sun.3mf` (19 KB). Mirdain's page sliced it automatically and gave a free estimate ($0.72 for one piece; free at the event).
- **Settings:** **PLA, blue**, quantity 1. Recommended print settings for any version: 0.2 mm layers, 15 to 20% infill, 3 walls, **no supports**, printed flat on the back.
- **Order:** submitted Nov 7, request `214a889f-c70c-40e3-ac0a-dde79788168e`; Mirdain follows up by email. Printed in Mirdain's farm (not in the room), so ask for a pickup time.
- **Backup printer:** the Prusa CORE One+ in the room (slice with PrusaSlicer, profile "Prusa CORE One+ / 0.4 nozzle / Prusament PLA / 0.20 mm STRUCTURAL").

## 5. Version 2 and the props

`SPEC_V2.md`, `generate_v2.py`, all checks pass:

| Part | Key dimensions | File |
|---|---|---|
| Card v2 | Same body and handle, 4 mm handle chamfers, 55 × 80 × 0.6 mm front pocket for a full-colour art sticker, flat back | `out/card_v2.stl` |
| Deck tray | 110 × 185 mm base, cards lean back 15°, 8 mm front lip, front card's handle always in the same spot (1 or 7 cards). Advance the next card by hand after each draw. | `out/deck_tray.stl` |
| Reveal stand | 120 × 100 mm base, 40 mm funnel into a 6 mm slot, card ends up at 60° facing the guest | `out/reveal_stand.stl` |

**Stickers:** colour art on the **front** (faces the guest); the ArUco tag (`agent/tags/tag_NN.png`, 25 to 30 mm, DICT_4X4_50, id = card number) on the **back**, because the overhead camera sits behind the arms and sees the card's back in the stand.

## 6. Why change the handle

The grey block works but does not look like part of a fortune card. We mocked up **10 handle concepts** that keep the two flat grip faces but hide them in a meaningful shape, and had Codex score each for grasp robustness and printability (`HANDLE_CONCEPTS.md`, `docs/handle-concepts.html`). Codex's top three: **ornate header bar, gothic arch tab, antique key bow.** We are going with the **gothic arch tab**.

---

## 7. Brief: Card v3, gothic arch tab (for refinement)

**Goal:** a card that looks like a small cathedral window rising from the top edge, and still grips as well as v1.

### Must keep (robot requirements: do not change without testing on the arm)

| Requirement | Value |
|---|---|
| Card body | 63 × 88 × 4 mm, 3 mm corner radius, front 55 × 80 × 0.6 mm sticker pocket, flat back |
| Tab position | Centred on the top edge, back face **flush with the card back** (so it prints flat, no supports) |
| Tab thickness (grip-face spacing) | **12 to 14 mm** (v1 = 14 mm, proven) |
| Tab width | **30 to 34 mm** at the base |
| **Straight-sided grip zone** | **at least 18 mm tall** of vertical, parallel sides **below** where the arch starts to curve. This is Codex's key fix: fingers that land high on the taper slip. |
| Grip faces | Flat front and back; 0.4 mm horizontal ribs allowed; **no relief or recess deeper than 0.5 mm inside the grip zone** |
| Arch crown | Pointed (gothic) arch above the grip zone; tip blunted to at least 4 mm radius so it cannot snap or poke |
| Total tab height | about 30 to 34 mm above the card's top edge (18 mm straight + 12 to 16 mm arch) |
| Lead-in | 45° chamfer of 2 to 4 mm on the front top edges of the straight section |
| Printing | PLA, flat on the back, no supports, no overhang steeper than 45° |
| Fit | Tab must stay **above** the reveal-stand funnel (stand funnel top is about 49 mm; card handle region sits about 50 mm above it) and clear the next card in the deck tray (pick only from the front seat) |

### Free to refine (design)

- The **rose window**: tracery, petals, a symbol per card (sun, moon, star…). Keep it **shallow (0.3 to 0.5 mm)** and **only on the arch crown**, not in the grip zone.
- Arch proportions (lancet vs equilateral), mouldings on the arch edge, a small keystone.
- Matching the tab outline to the card's art frame.
- Multi-colour: a second colour for the rose (filament swap at a layer height) is fine; the grip zone stays one smooth colour.

### Acceptance checks before printing a deck

1. Run the checks (watertight, one solid, z = 0, grip-face spacing, straight-zone height): extend `check_v2.py` with the 18 mm straight-zone check.
2. Print **one** card and test it on the arm: 10 picks from the deck tray with the Quest teleop, then with Dream-1. It must match v1 (at least 8 of 10).
3. Check it slides into the reveal stand and that the overhead camera still reads the back tag.
4. Only then print the full deck (Sun 19, Moon 18, Star 17, Wheel of Fortune 10, Lovers 6, Strength 8, World 21).

### Starting point in code

`generate_v2.py` → `build_card()` builds the body and the block handle. Replace the handle prism with an arch profile (a 2D outline: rectangle for the straight zone + pointed arch on top) extruded 12 to 14 mm from the back plane, then union it with the body. Keep the ribs and lead-in chamfer on the straight zone only.

---

## Files

| File | What |
|---|---|
| `SPEC.md`, `SPEC_V2.md` | Specs for v1 and v2 (+ tray and stand) |
| `HANDLE_CONCEPTS.md` | The 10 handle concepts |
| `generate_card.py`, `check_card.py` | Card v1 generator and checks |
| `generate_v2.py`, `check_v2.py` | Card v2, deck tray, reveal stand generator and checks |
| `out/` | STL / 3MF files and preview images |
| `../docs/handle-concepts.html` | Mockups of all 10 handles with Codex's scores |
| `../docs/fortune-teller-plan.md` | The whole build plan |
