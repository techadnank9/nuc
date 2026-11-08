# Robot Fortune Teller deck: print files

Units: millimetres. Every file is already in print orientation: **face down on the bed, handle pointing up**.
No supports. Each card is 63 x 113 x 14 mm, about 8 g.

## Settings (all plates)

- PLA (matte if possible), 0.4 mm nozzle, **0.2 mm layers**, 15 to 20% infill, 3 walls, no supports.
- **Colour change at 0.6 mm** (before layer 4): add a filament change / M600 at Z = 0.6 in the slicer.
  The first 0.6 mm is the card's face; the engraved art then fills with the second colour.

## Plates (one filament pair per plate)

| File | Cards | First 0.6 mm | Then |
|---|---|---|---|
| plate_red_tower_ace_of_wands.stl / .3mf | The Tower, Ace of Wands | black | red |
| plate_blue_star_ace_of_cups.stl / .3mf | The Star, Ace of Cups | black | blue |
| plate_orange_sun_judgement.stl / .3mf | The Sun, Judgement | black | orange |
| plate_black_moon_world.stl / .3mf | The Moon, The World | orange | black |

Each plate is 138 x 113 mm. Single cards: card_*.stl (same orientation).

No colour change possible? Print each card in its main colour only: the art still shows as engraving.

## Test first

Print **one** card (e.g. card_17_star.stl) and test it on the arm (about 10 grabs), in the deck tray and the
reveal stand, before printing the whole deck: the smooth domed handle has not been gripped yet.
