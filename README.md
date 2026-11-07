# Finc Hackathon: USB unplug

Our team's tools for the Physical AI Hackathon, where we teach a Dream-1 robot arm to unplug a USB cable.

## Run the model in one command

From your laptop, on the station's Wi-Fi:

```bash
./run.sh                 # dry run: the model runs, the arms do NOT move
./run.sh --live          # live run: the arms MOVE (hand on the e-stop)
./run.sh --live --checkpoint dream-1-ft:<our-model-id>   # our fine-tuned model
```

- It asks for the station password (from the organizers).
- While it runs, open http://localhost:8093 to see what the model sees.
- **Ctrl+C** stops it. The arms go home, then switch off and **go limp**, so support them.
- If it says the arms are in use, stop the other program first (only one can drive the arms at a time).

Options: `--task "text"`, `--checkpoint ID`, `--host user@ip` (or set `ROBOT_HOST`).

## Safety

Always have someone on the emergency stop during `--live` runs. Keep everyone out of the arms' reach.
