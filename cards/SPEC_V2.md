# Fortune teller props v2: spec

Extends `SPEC.md` (card v1). Same tools: Python, trimesh + manifold3d in `cards/.venv`, STL + 3MF out, preview PNGs, a check script.
All parts print in PLA, 0.2 mm layers, no supports.

## 1. Card v2 (`out/card_v2.stl`)

Same as v1 (63 × 88 × 4 mm body, 3 mm corner radius, top handle 30 × 18 × 14 mm, flush with the back, 10 mm proud of the front, 0.4 mm grip ribs, chamfered bottom edge) with these changes:

- Handle top chamfers: **4 mm at 45°** (v1 had 2 mm), on both the front and back top edges.
- Front face: **no raised relief**. Instead a **0.6 mm deep pocket, 55 × 80 mm**, centred on the body, 1.5 mm corner radius, for a full-colour sticker (the sticker carries the art and the card's ArUco tag).
- Back: **no pocket** (flat, prints on the bed).
- One STL serves all 7 cards; the stickers make them different.

## 2. Deck tray (`out/deck_tray.stl`)

Holds up to 7 cards standing on their bottom edge, leaning back, handles up, so the **front card's handle is always in the same place** for the arm.

- Cards lean back **15° from vertical** (towards the back wall).
- Inner slot width (across the card's 63 mm width): **67 mm** (2 mm play each side). Side walls 3 mm thick, 40 mm tall.
- Floor: inclined so the bottom edges of the cards slide forward against a **front stop**: the stop is a lip **8 mm tall**, so the arm only has to lift a card 10 mm before it clears.
- Back wall: inclined at 15°, tall enough (60 mm) to support a full stack of 7 (each card is 14 mm thick at the handle, 4 mm at the body).
- Depth front-to-back: enough for 7 cards leaning at 15° (compute it; about 75 mm at the handle line).
- Base footprint at least 110 × 120 mm, with two 4 mm holes or ears for taping/clamping to the table.
- The front of the tray must leave the front card's handle fully exposed on both grip faces (no walls within 25 mm of the handle sides), so the gripper fingers can close on it.

## 3. Reveal stand (`out/reveal_stand.stl`)

The arm drops a card into it; the card ends up leaning back at about 60° from horizontal, face towards the guest and the overhead camera, handle up.

- A slot 6 mm wide (card body is 4 mm), 66 mm long, 20 mm deep, tilted back 30° from vertical.
- A **funnel mouth** above the slot: opens to **40 mm wide** front-to-back and 80 mm long at the top, narrowing over 25 mm to the 6 mm slot, so a card released roughly above it slides in.
- Note: the handle is 14 mm thick, so the handle must stay above the funnel: the funnel's total height (mouth + slot) must be at most 60 mm so the 18 mm handle and the top of the card stand out.
- Base footprint at least 120 × 100 mm, low centre of mass, two tape/clamp ears.

## Previews

`out/preview_tray.png` (side section showing 7 cards leaning in it), `out/preview_stand.png` (side section with one card seated), `out/preview_card_v2.png` (front and side).

## Checks (`check_v2.py`)

Each part: watertight, one solid, lowest z = 0, fits a 250 × 220 × 220 mm bed. Tray: front card's handle position is the same with 1 or 7 cards (report it). Stand: a 4 mm card fits the slot and the handle top is at least 18 mm above the funnel. Print the key dimensions.
