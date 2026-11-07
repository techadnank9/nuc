# Fortune card v1: spec

A 3D-printed fortune card that a MakerMods Maker Arm (bimanual, parallel 2-finger gripper, driven by Dream-1) can pick up every time.

## Gripper facts (i2rt YAM, linear_4310 parallel gripper)

- The arms on NUCK-8 are i2rt YAM 6-DOF arms (https://i2rt.com/products/yam-6-dof-arm): 750 mm reach, 2 kg payload.
- Parallel-jaw gripper, **95 to 96 mm throw** (`gripper_stroke: 0.096` in i2rt's linear_4310.yml), DM4310 motor, force-limited.
- A card weighs about 7 to 15 g: far below the payload, so grip force is not the constraint; position error of the learned policy is.
- Card v1's 30 × 18 × 14 mm handle was picked up by the base Dream-1 model on Nov 7.

## Design rules for a near-certain grasp

1. The arm never grips the thin card itself. It grips a **handle**: a solid block on the card's top edge.
2. The handle has **two flat, parallel grip faces**, 14 mm apart. Its back face is flush with the card's back (so the card prints flat on its back, support-free) and it stands 10 mm proud of the card's front face. 14 mm is far inside the ~100 mm opening, so the fingers can arrive up to ~40 mm off-centre and still close on it.
3. The handle is **30 mm wide** along the card's width and **18 mm tall**, so the fingers can land anywhere along it.
4. **45° chamfers** on the handle's top edges: a finger that lands on top slides down to a grip face instead of jamming.
5. Fine **horizontal ribs** (0.4 mm) on the grip faces for friction against PLA.
6. Card stands upright in a rack slot; the bottom edge is chamfered so it drops back in easily.
7. Low mass: PLA, about 15 to 20% infill.

## Card body

- 63 x 88 mm (poker size), 4 mm thick, 3 mm corner radius.
- Face: raised (1 mm) sun symbol (disc + 12 rays) and the numeral XIX built from bars. Big and high-contrast for the overhead camera.
- Back: a 30 x 30 x 0.6 mm pocket in the lower half for a printed ArUco sticker (marker id = card number), so software can always tell which card was drawn.

## Outputs

- `out/card_19_sun.stl` (and `.3mf`), printable flat on its back, no supports.
- `out/preview_*.png`: front, back, side and gripper-fit views.
- `check_card.py`: asserts watertight, volume > 0, overall size, handle grip-face distance = 14 mm, handle within gripper opening with margin, and prints mass estimate at 1.24 g/cm3 x 20% infill (rough).
