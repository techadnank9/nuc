# Fortune Teller page: design notes

The page's job: a guest standing at the desk **talks to the Fortune Teller** and watches what the robot's hands are doing. The guest can already see the arms in person, so the cameras are compact; voice and the transcript are the centre, with live captions on the overhead camera and the robot action log beside it. An operator box sends direct commands to Dream-1. With the robot connected everything is live: every draw and every command moves the arms; Stop arms and the emergency stop halt them.

## Layout

```
┌ The Fortune Teller ──────────────────────────── ● Robot ready  ● Voice  [Begin a reading] ┐
│ ┌ Cameras ─────────┐ ┌ Conversation ──────────────────────┐ ┌ Your reading ───────────┐ │
│ │ Overhead [you]   │ │            ( big talk button )     │ │ Listening … Reveal      │ │
│ │ Left | Right     │ │   Tap to talk, or hold (or space)  │ ├ Your card ──────────────┤ │
│ ├ What the hands   │ │ Transcript (tall, timestamped)     │ │                         │ │
│ │ are doing (log)  │ │ [Or type your question]  [Send]    │ ├ Command the arms ───────┤ │
│ └──────────────────┘ │ [cam] [camera ▾] [End reading]     │ │ presets, task, Stop     │ │
│                      └────────────────────────────────────┘ └─────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────────────────────────┘
```
- **Robot cameras** come from the station through `station_cam_relay.py`. When idle they are the cameras themselves; during a draw they show **what Dream-1 sees** (frames read from `dreamcli robot run --view`), labelled as such.
- **What the hands are doing** is a live log of every robot step (checks, Dream-1 running, each Dream-1 round trip with its time, stopping, reading the card) sent by the harness through the agent.

## Visual system

| Token | Value | Use |
|---|---|---|
| Booth | `#1b1230` aubergine | Background: a fortune teller's booth, not a generic dark mode |
| Velvet | `#2b1d47` | Panels |
| Brass | `#c9a227` | The machine and the mystic share it: frames, primary action, card |
| Parchment | `#efe4c8` | Text |
| Jade | `#4fb3a2` | Robot healthy, live, mic on |
| Ember | `#e0745a` | Stop, errors, End reading |

Type: **IM Fell DW Pica** (an old printing face, like real tarot decks) for the title and card names; **Instrument Sans** for interface text; log times use tabular figures, not a monospace font.

## Laws of UX, and where each one is applied

| Law | How the page applies it |
|---|---|
| **Jakob's law** (people expect your page to work like others they know) | Video-call conventions: round mic and camera toggles at the bottom right, a red End button, chat transcript with a type box, a camera picker, picture-in-picture self view. |
| **Fitts's law** (big, close targets are faster) | A 128 px talk button at the top of the centre column (tap, hold, or hold the space bar); the 44 px primary button sits top right; all controls at least 44 px high. |
| **Hick's law** (fewer choices, faster decisions) | One primary action per state: *Begin a reading* → talk / type / End → *New reading*. The command box offers four presets instead of a blank field. |
| **Miller's law** (about 7 items in working memory) | The action log keeps the latest 7 entries fully visible; older ones fade. The progress track has only 4 steps. |
| **Doherty threshold** (respond under 400 ms) | Robot cameras refresh every ~140 ms; status pills and the log update the moment an event arrives; typed messages appear instantly before the agent answers. |
| **Aesthetic-usability effect** (beautiful feels easier) | One cohesive booth theme: brass frames, antique type, a real card that flips. |
| **Law of proximity** | Robot cameras grouped together; reading status, log and card stacked together; conversation and its controls in one strip. |
| **Law of common region** | Each group sits in its own surface: the stage, the reading column, the conversation bar. |
| **Von Restorff effect** (the different thing is remembered) | The overhead camera has the brass frame and glows while the hands are drawing; the revealed card is the only bright object on the page. |
| **Serial position effect** (first and last are remembered) | First: the title, robot/voice status and *Begin a reading*. Last: *End reading* at the end of the control row. |
| **Peak-end rule** (people judge by the peak and the end) | Peak: the card flip with its name and meaning. End: "The reading has ended" with a single *New reading* button. |
| **Goal-gradient effect** (motivation rises near the goal) | The Listening → Question → Drawing → Reveal track fills as the reading progresses. |
| **Zeigarnik effect** (unfinished tasks stay in mind) | Unfilled progress steps and the "Not drawn yet" card keep the reading's open loop visible until the reveal. |
| **Tesler's law** (complexity can only be moved, not removed) | The page server absorbs it: LiveKit tokens, agent dispatch, the SSH tunnel, starting the camera relay and handing cameras to Dream-1. The guest presses one button. |

## Accessibility and states

- Visible keyboard focus, labels on icon buttons, `aria-live` on the log and transcript.
- Reduced motion turns off the card flip animation.
- Every failure says what happened and what to do: microphone blocked → type instead; robot offline; stand not empty → clear it first; no card reached the stand → set it back in the deck.
