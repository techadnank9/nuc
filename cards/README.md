# Sun XIX fortune card

Generate and validate using the existing environment; no installation required:

```sh
.venv/bin/python generate_card.py
.venv/bin/python check_card.py
```

Outputs are `out/card_19_sun.stl`, `out/card_19_sun.3mf`, and
`out/preview_front.png`, `preview_back.png`, `preview_side.png`, and
`preview_grip.png`. All dimensions are millimetres; 3MF explicitly records the
units. The previews render the actual mesh; side views show its center section.
The gripper fingers are schematic, since their dimensions are not in the spec.

The body is **63 × 88 × 4 mm**, with **3 mm corner radii**. A centered
**30 × 18 × 14 mm** handle extends above its top edge, giving overall dimensions
of **63 × 106 × 14 mm**. Its rear grip lands are flush with the back at **Z=0**;
its front lands sit at **Z=14**, **10 mm proud** of the card front. The top has
**2 mm, 45° chamfers** on all four edges. Horizontal grip lands have **0.4 mm
relief**, defined by recessed triangular channels on both faces (1.6 mm pitch,
0.8 mm flat lands). This keeps the opposing contact planes exactly 14 mm apart
without ribs sticking below the bed. The bottom front edge has a **0.8 mm, 45°
bevel**; the rear outline remains flat.

The front carries a **1 mm raised** sun (18 mm diameter disc and 12 rays,
36 mm outer diameter) and a bar-built **XIX**. The lower back has a
**30 × 30 × 0.6 mm** square pocket for a printed ArUco sticker with **marker ID
19**. The sticker itself is not generated; use the marker dictionary configured
in the station's vision software.

Print in **PLA**, at **0.2 mm layer height**, **20% infill**, **no supports**.
Use the supplied orientation, with the flat back on the bed at **Z=0**. Enable
bridge settings: the pocket's first roof layer bridges 30 mm, so bridge cooling
and calibration matter. The chamfers and shallow rib channels use 45° slopes.
The checker reports the requested rough mass estimate using solid volume ×
1.24 g/cm³ × 20%; actual sliced mass will be higher because of walls and skins.

The thick handle gives the parallel fingers broad landing faces instead of
asking them to grasp a 4 mm card. A 14 mm grip inside a 100 mm opening leaves
43 mm of geometric centering margin on either side. The top chamfers guide a
misaligned finger toward a face, while the ribs provide friction. The flush
back permits flat printing and the bottom bevel helps insertion into a rack.
This geometric clearance is not a measured grasp-success guarantee.
