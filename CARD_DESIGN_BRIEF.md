# Brief: 3D-printed fortune cards a robot arm can pick up

> **For the Claude reading this:** you're helping a hackathon team design and CAD fortune-teller cards and a card holder that a robot gripper can reliably pick up. Everything you need is below. Ask the team for the measurements in "Measure first" before finalizing dimensions. Do not guess them.

## The project

A team at the MakerMods × Dream Labs Physical AI Hackathon (Nov 7–8, 2026, San Francisco) is building **"Madame Dream"**, a sassy robot fortune teller:

- A guest asks a question out loud. A LiveKit voice agent (the fortune teller's personality) answers.
- To "draw a card", the agent sends the robot a plain-English instruction such as **"pick up the red card and hold it up"**. The robot is driven by **Dream-1**, a vision-language-action model from Dream Labs. It looks at the scene through cameras and moves the arms.
- The card is chosen at random in software, **by color**. So the robot must be able to tell the cards apart by color and grab the one it's asked for.

The cards are 3D-printed because paper cards lie flat, and the gripper can't get under them.

## The robot

- **Arms:** two MakerMods **Maker Arm v1** follower arms, each with the standard **i2RT Robotics Linear Gripper**: a two-finger parallel gripper whose fingers slide straight toward each other, driven by a Damiao DM4310 motor.
- **Gripper stroke:** the station's i2RT config (`i2rt/robots/config/linear_4310.yml`) gives `gripper_stroke: 0.096`, so **about 96 mm of travel**. Confirm it with a ruler (see below), because "stroke" may be total travel rather than the usable inside gap. The config also has a clog/force limiter, so the gripper stops squeezing when it meets resistance: **rigid cards are good, while thin or flexy parts may slip.**
- **Cameras:** three **Intel RealSense D405**: one overhead and one on each wrist. The model sees them at low resolution (**640×360, about 5 frames per second**), so fine details are invisible to it.
- **The model:** Dream-1, used as the base model at first. It may be fine-tuned later on about 50 human-teleoperated demonstrations of picking up these exact cards. **That means the final design must stay the same once demos are recorded.**
- **Precision:** it's a learned policy, not a CNC machine. Expect errors of about ±1–2 cm in where it grabs. **Design for forgiveness.**

## Measure first (ask the team for these)

1. **Gripper max opening**: the inside gap between the fingers when fully open, in mm. Expected to be about 96 mm (i2RT Linear Gripper); measure to confirm.
2. **Finger pad size**: the width and length of each finger's contact surface, in mm.
3. **Finger depth**: how far below the gripper the fingertips reach. This limits how tall a grab tab can be.
4. **3D printer**: build plate size, nozzle size, and available filament colors (or whether they'll paint).
5. **Table area** in front of the robot where the holder will sit: rough size, and the table's color.

## Design requirements

### Cards
- **Thick and rigid:** about **5–8 mm** thick, more like tiles than paper.
- **Grab tab or ridge on the top edge,** about 10–15 mm tall, so there's an obvious place to pinch. Its thickness should be well under the gripper's max opening (aim for **at most half** of it, so a slightly off grab still closes on it).
- **Size:** start around **60 × 90 mm** and adjust to the gripper. The card must be easy to hold up to the camera afterward.
- **Light:** a hollow or infill design (about 15–20% infill) so the gripper doesn't drop it.
- **Grippy texture** on the tab (ridges, or a slightly rough top layer) so it doesn't slip out of smooth fingers.
- **Big, bold, high-contrast visuals.** Each card gets **one solid bright color** (the model identifies cards by color) and **one big raised symbol** that fills most of the face, visible at 640×360. No small text.
- **Matte finish:** shiny plastic confuses the depth cameras.

### The deck (5 cards; color → card)
| Color | Card | Symbol idea |
|---|---|---|
| Red | The Flame | Flame |
| Blue | The Moon | Crescent moon |
| Green | The Coin | Coin or "$" |
| Yellow | The Sun | Sun |
| Black | The Skull | Skull |

The colors must be **clearly distinct from each other and from the table**. If the table is black, change the black card to purple, for example, and tell the team so they can update `DECK` in `madame/agent.py`.

### Holder ("toast rack")
- Holds all 5 cards **standing upright** with the tabs facing up, so the gripper comes straight down and pinches.
- **Spacing between cards: at least the gripper's finger width plus about 15 mm of clearance on each side,** so the fingers fit between neighbors without knocking them over.
- **Slots slightly loose** (about 0.5–1 mm per side) so a card lifts out without sticking, but **tilted back about 5–10°** so cards don't fall forward.
- **Heavy, wide base** (or room for coins or weights inside) so pulling one card doesn't drag the holder. Add a mark on the bottom so it's always placed in the same spot. The model learns positions, so consistency matters.
- **Optional:** a raised "theatre" look (arches, stars) for the demo video, as long as nothing sits above the card tabs where the gripper descends.

## Deliverables

1. A **parametric OpenSCAD file** (`cards.scad`), or another text-based CAD format, with variables at the top: `card_w`, `card_h`, `card_t`, `tab_h`, `tab_t`, `slot_gap`, `card_spacing`, `tilt_deg`, `n_cards`. The team can then retune it from the gripper measurements without redrawing.
2. **STL exports:** one card blank, one per symbol (or a multi-color/painting plan), and the holder.
3. **Print notes:** orientation, infill, supports, and approximate print time. **Time is tight:** cards should print in under about 1 hour each, the holder in under about 2–3 hours. Print **one test card first**.

## Test plan

1. Print **one card and a 1-slot test holder**. Test the grip by hand, closing the gripper on it if possible.
2. On the robot (the team runs this; someone keeps a hand on the e-stop):
   `./run.sh --live --task "pick up the red card and hold it up"`
3. Watch what goes wrong and adjust the parameters: missed the tab (taller or wider tab), knocked the card over (more spacing, more tilt), slipped (more texture, thinner tab), dragged the holder (heavier base).
4. Once it works, freeze the design before recording any demonstrations.

## Fallbacks if picking up fails
- The robot **tips a card forward** out of a slanted holder, which still makes a dramatic reveal.
- The robot **slides a card** out of a row along the table (pushing is easier than grasping).
- The holder could have a **raised handle or knob per card** instead of a flat tab.
