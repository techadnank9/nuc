# The Robot Fortune Teller

Our Creative-track project for the MakerMods Physical AI Hackathon (Nov 7 to 8, 2026). You ask the
Fortune Teller a question out loud; its arm draws a card from the deck, places it in the reveal stand,
and it reads your fortune. Plan, timeline and roles: [docs/fortune-teller-plan.md](docs/fortune-teller-plan.md).

| Folder | What |
|---|---|
| `agent/` | Voice agent, web app and robot harness. Start with `agent/start_web.sh` (see its header). |
| `cards/` | Card, deck tray and reveal stand: generators, checks, STL/3MF files. |
| `docs/` | Build plan and explainer pages. |
| `run.sh` | Run Dream-1 on the station by hand (below). |

Testing the conversation only (no robot, does not take the station's cameras):

```bash
cd agent && uv sync && DRAW_MODE=manual ROBOT_CAMERAS=0 ./start_web.sh   # then open http://127.0.0.1:8765
```

## Run the model in one command

From your laptop, on the station's Wi-Fi:

```bash
./run.sh                 # dry run: the model runs, the arms do NOT move
./run.sh --live          # live run: the arms MOVE (hand on the e-stop)
./run.sh --live --checkpoint dream-1-ft:<our-model-id>   # our fine-tuned model (adds --action-space joint)
```

- It asks for the station password (from the organizers).
- While it runs, open http://localhost:8093 to see what the model sees.
- **Ctrl+C** stops it. The arms go home, then switch off and **go limp**, so support them.
- If it says the arms are in use, stop the other program first (only one can drive the arms at a time).

Options: `--task "text"`, `--checkpoint ID`, `--host user@ip` (or set `ROBOT_HOST`).

## Safety

Always have someone on the emergency stop during `--live` runs. Keep everyone out of the arms' reach.
