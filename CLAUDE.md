# CLAUDE.md

**kinesis** is a PureRef-style reference board you drive with your hands — a
webcam watches you, MediaPipe finds your hands, and a pinch grabs an image on an
infinite canvas. A Claude agent can drive the same board over MCP, so the images
in front of you can be put there by a sentence instead of a file dialog.

Use plain language with the user and explain things at a high level; go into the
nitty-gritty of Qt, MediaPipe, or the tracker loop only when it's needed to
address something or for the user to understand what is going on. You can still
speak in a technical, detailed way when writing issues and PRs — those are
documentation left for a future Claude to understand what is being planned/done.
Tell the user when something is bottlenecking you. Don't recite the rules of this
file unless one is blocking you or explains a conclusion.

## Scope — this project has an end

kinesis is deliberately **not** a north-star project. It is not going to be sold,
it has no users but one, and the backlog in `auxmd.md` is roughly the whole of
what is left to build. Once that is done, the honest answer to "what's next?" is
usually *nothing* — and the right move is to say so rather than to invent work.

What it is *for*, beyond being fun to use: having reference material to hand
while recording, presenting, or on a call, where reaching for a mouse would break
what you are doing. That is the use to weigh a feature against.

None of this is licence to be sloppy. The reason this file exists is the opposite
— a small project with a finite scope is exactly the one where discipline is
cheap, so there is no excuse for skipping it. The rules below are carried over
from a project where they are load-bearing, and they earn their place here too.

### What kinesis is, and is not

kinesis is a **reference board**: images on a canvas, moved and scaled by hand.
It is **not** an image editor, not a compositor, not a whiteboard suite, not a
presentation tool. No filters, no cropping, no layers-with-blend-modes, no
export pipeline.

**PureRef is the reference for taste**, not a specification — when the question
is "what should this feel like?" rather than "what should this do?", that is
where to look. Worth copying in spirit: the plainness, the fact that the board
gets out of the way, the fact that nothing is modal. Worth ignoring: anything
that exists because someone wanted a full application.

The word to hold onto is **feel**. This is a project about a gesture landing when
you expect it to. A feature that makes the board more capable but the pinch less
certain is a bad trade, every time. This is a scope rule, so it cuts both ways:
it is a reason to *refuse* an elaborate feature, and equally a reason to spend a
day on a number that makes grabbing an image feel right.

Two things reliably matter to the user, and they are the first place to look when
something feels off: **pinch trigger size** and **responsiveness** (cursor lag).

## Start here

- **BUILD.md** — the original brief the project was built from: the stack and its
  rationale, the process architecture, the gesture layer, and the macOS setup
  notes. It is a historical document in the places the code moved past it (there
  is no rotation, scaling is two-hand and there are no corner handles, and there
  is no README yet) — read it for *why the shape is the shape*, and trust the
  code where they disagree.
- **auxmd.md** — the user's throwaway working notes, gitignored, holding the
  deferred backlog and the design thinking behind each item. **Don't write to it
  unless asked**, and don't start anything in it unasked. If work here would be
  affected by a design recorded there, read it first.
- **Module docstrings are the boundary docs.** Every module opens with a
  docstring saying what it is *and why it is that way* — which process it runs
  in, what it may not import, what problem its shape avoids. Read the docstring
  before adding a dependency between modules; that line is the contract.

## Architecture — decided, do not redesign

These decisions are settled. Changing one is `architecture`-label work, not a
side effect of a feature PR.

- **The tracker is a separate process, not a thread.** Capture and inference must
  never be able to stutter the UI, a camera crash must not take the canvas down,
  and tracking must be restartable without restarting the app. `multiprocessing`
  with spawn; `ui/hand_control.py` owns its lifetime.
- **Only small float dataclasses cross the queue** (`tracking/protocol.py`) —
  never video frames, except an optional small JPEG when the debug preview is on.
  The queue is `maxsize=2` and the producer **drops the oldest frame when full**;
  the UI timer **drains it completely and uses only the last item**. Stale hand
  data is worse than no hand data, and a backlog is never processed.
- **All smoothing and every pinch decision happen in the tracker process**, so
  the UI consumes clean, already-decided state and never second-guesses it.
- **`tracking/gestures.py` and `tracking/filters.py` are pure.** No camera, no
  Qt, no MediaPipe imports — plain sequences of `(label, landmarks)` in, state
  transitions out. This is the code most likely to need fixing and the only code
  that can be tested honestly, and those two facts are the same fact. Keep it
  that way.
- **One Euro filtering is non-negotiable** (Casiez, Roussel & Vogel, CHI 2012).
  Adaptive cutoff — heavy smoothing when the hand is still, light when it moves
  fast. A fixed low-pass can only trade one for the other, and both matter.
- **Every mutation of the board goes through `BoardScene`.** Drag-drop, paste,
  the menu, the hand, and the MCP server all call the same methods. That single
  surface is what makes the board drivable from outside; an event handler that
  mutates items directly is a bug even when it works.
- **The interaction model is settled**: one-hand pinch moves, two-hand pinch
  scales, no rotation, no corner handles, trash target bottom-right. `Alt`+drag
  is the mouse equivalent of a scale, so the app stays fully usable with the
  camera off — and **it starts with tracking off.**
- **Overlays and on-canvas buttons are painted, not child widgets.** The viewport
  is a `QOpenGLWidget`, and a stacked transparent widget composites badly over
  it. The trash target, the camera button, the cursors and the HUD are all drawn
  inside `BoardView.drawForeground` / `drawBackground`. New chrome follows suit.
- **The camera background has its own capture thread, in the main process.** It
  does no inference, so a thread is enough, and staying in-process means the
  frame lands as a `QImage` with no encode/decode round trip. It is deliberately
  independent of hand tracking: the background works with the tracker off.
- **Every live-tunable number lives in `Tuning`** (`tracking/protocol.py`) and is
  edited in the `T` panel and nowhere else. Nothing that appears in that panel
  may be hardcoded anywhere in the app.
- **MCP talks to a running app over a local control channel** — loopback TCP,
  newline-delimited JSON, token in `~/.config/kinesis/control.json`, handlers on
  the Qt event loop so they touch the scene on the GUI thread and need no
  locking. **The MCP server never launches the app**, so closing the board can't
  leave an orphaned window behind.
- **MCP is a protocol, not a Claude feature.** `kinesis/mcp_server.py` speaks MCP
  to whatever client is on the other end. Claude is who we develop and test
  against, not a dependency; nothing in the server may assume otherwise.
- **Python, and the stack is decided — do not re-litigate it.** `QGraphicsScene`
  already provides the scene graph, transforms, z-order, hit-testing and
  selection this app is most of the way made of, and there is no mature Rust
  MediaPipe binding, so a Rust frontend would need a Python sidecar anyway. This
  was re-checked with measurements, not opinion: at 640×480/30, `cap.read()` is
  20.2 ms of blocking wait, MediaPipe inference 13.0 ms, and **all of our own
  Python is 0.01 ms — about 0.1% of the frame.** Rewriting it buys nothing.
  Responsiveness is still a real problem, but it lives in the filter constants,
  in prediction, and in the sensor pipeline — never in the language.
- **The version pins are load-bearing.** `mediapipe==0.10.35` (1.0.x aborts on
  macOS/arm64 — Metal graph service unavailable, and `delegate=CPU` doesn't
  avoid it), `opencv-python<5`, Python `>=3.12,<3.13` (mediapipe has no 3.13
  wheels). Each has its reason written next to it in `pyproject.toml`. Read the
  reason before changing the pin.

### Module map

`kinesis/tracking/` — `filters.py` and `gestures.py` are pure and depend on
nothing but `protocol.py`; `worker.py` is the tracker process entry and is the
only module that runs MediaPipe inference; `model.py` locates/downloads the landmarker
up front so setup fails loudly rather than mid-capture. `kinesis/canvas/` —
`scene.py` (the one mutation surface), `items.py` (`ImageItem` + LOD),
`view.py` (mouse/keyboard interaction), `chrome.py` (all canvas painting --
trash target, camera button, toolbar, cursors, HUD), `persistence.py`
(`.kinesis` save/load). `kinesis/ui/` — `hand_control.py`
(tracker lifetime, the 60 Hz timer, the grab state machine), `overlay.py`
(preview/cursors/HUD), `tuning.py` (the `T` panel), `camera_feed.py` (background
capture). `kinesis/app.py` is the window and the wiring; `kinesis/control.py` is
the control channel; `kinesis/mcp_server.py` is a thin client of it.

**The dependency direction that must not invert:** `tracking/` never imports
from `canvas/` or `ui/`, and nothing outside `tracking/worker.py` and
`tracking/model.py` imports MediaPipe. `gestures.py` and `filters.py` additionally never import Qt. These
are checked, not remembered: `scripts/lint/architecture.py` runs in
`./run.sh check`, and adding an invariant there is adding a row to `RULES`.

## How we work

**Where this is developed, and what it targets.** One machine, one person: a
MacBook Air with an M4, on macOS. macOS on Apple Silicon is also the *target* --
the user and the coworkers this is for are all on Macs -- which is why CI runs on
`macos-latest`/arm64 rather than a cheaper Linux runner: a green Linux run would
prove the Python sound and nothing about the machine the app runs on. Portability
is a nice-to-have and nothing is owed to it. That is a current fact and not a
decided invariant, but it is the premise several rules below are shaped by, and it is written down so nobody re-derives
the industry default of many contributors on many cold machines and proposes the
tooling that goes with it. Concretely: the camera is the built-in one at 30 fps,
Apple Silicon is why the MediaPipe pin exists, and there is no second machine on
which "works here" could be a separate claim from "works".

**The tooling that does exist**, so nobody rebuilds it: `./run.sh check` runs
every mechanical gate in one command -- ruff, the conventions linter
(`scripts/lint/`), an import sweep over every module, and pytest. A versioned
pre-push hook runs the same command before a push leaves the machine, and CI runs
it again on `macos-latest`/arm64 for any PR marked ready for review. There is
still no `make` and none is wanted: `run.sh` is the entry point.

**What is deliberately absent:** a formatter that rewrites code, and a dead-code
detector. On the second -- it was measured, not assumed: 224 top-level
definitions scanned, zero dead. `control.py` dispatches through
`getattr(self, f"cmd_{command}")` and Qt calls its overrides by name, so a static
detector reports those as dead and finds nothing real. Ruff's unused-import and
unused-variable rules are the whole dead-code gate until the repo has code nobody
remembers.

- **The gates (push back before you build).** An idea becomes an issue only when
  all three hold; if any fails, **push back instead of complying**:
  1. **Understanding.** Claude actually understands the idea — the user has a
     clear intent and Claude can restate it. If unsure, restate it back and
     confirm before proceeding; don't guess.
  2. **Value.** The issue adds real value to the project. No busywork, no
     features for their own sake. Given the scope rule above, "this project may
     simply be finished" is an acceptable answer here.
  3. **Craft.** It follows Python good practices and the decided architecture
     above. If it doesn't, say so and propose the right shape.
- **Flow:** idea → issue → branch → PR → merge. New work starts as an issue, not
  a surprise diff, and the PR references the issue it closes. **Issue-less PRs
  are allowed only** for documentation updates or bug fixes; everything else
  starts as an issue. Either way the PR description still has to clear the three
  gates.
- **Pull requests — open early, draft until ready.** The moment a branch has its
  first commit, open a PR for it — as a **draft**. Draft while in progress or
  blocked (say why in the description); **ready for review** once done and
  nothing further is needed from the user. The description says **what changed
  and the effect** — not process; how you got there appears only when it's needed
  to understand the diff.
- **A ready pull request claims it passes; a draft makes no such claim.** With no
  CI, that claim rests entirely on having run the gates by hand, which makes
  lying about it easy and therefore worth naming: **do not mark a PR ready
  without having actually run `./run.sh test` and, when the change can be seen or
  felt, launched the app.** A draft is not decoration on unfinished work — it is
  how work survives a session that got cut short, and none of those cases assert
  the work is finished.
- **Nothing depends on a hand-back comment existing.** A draft is a saved state
  as best the last session could manage. The durable context is the **issue**,
  written before the work started and meant to be read cold. Write a comment when
  there is a chance to; never rely on one being there.
- **Branch naming.** A PR that closes an issue uses `{issue_number}-short-slug`
  (e.g. `7-alpha-hit-testing`). An issue-less PR uses a readable short slug of
  its subject. Lowercase-hyphenated, brief.
- **One branch per issue, and merge one at a time.** Issues that block neither
  each other nor a common third can be worked in parallel, each on its own
  branch. Merging is still serialized — rebase onto the latest `main`, re-run
  `./run.sh check` on the rebased state, merge, repeat. Two PRs that are each
  fine alone can break the app together, and this is not theoretical: #23 and #24
  were both green in isolation, and the moment they met, ruff rejected #23's
  import formatting. Note *why* the rule survives having CI — the reason a
  compiled project serializes merges is queue time, and that reason is gone here.
  What remains is the semantic one, and a re-check now costs seconds, so skipping
  it buys nothing.
  Worktrees are worth it when two branches are genuinely open at once and worth
  skipping when they are not — this is a 4,000-line Python project with no build
  step, so the isolation buys correctness, not compile time. Agents working
  worktrees in parallel share the main repo's venv rather than building a 500 MB
  one each; **verify `kinesis.__file__` resolves under the worktree before
  trusting a green run**, or the gates graded the wrong tree (issue #28).
- **Architecture- then infrastructure-first (NOT "make it up as we go").** When
  we find a problem — something that bites or will bite more than once, a pattern
  worth adopting, or a practice we should have had — we document it and fix it
  **before** continuing. Architecture and infrastructure problems **halt feature
  work.** Each such fix gets its own issue when it carries its own
  responsibility.
- **Dependencies (not batches).** Record how issues relate using GitHub's native
  **Blocked by / Blocks** relationships, and **sub-issues** when one issue is
  literal groundwork for another. There are no rigid batches: **the dependency
  graph is the plan.** Any issue with no open blockers and no stage label is fair
  game. Re-read the board after every merge, not after a batch — a merge changes
  the graph, and whatever it unblocked is startable the moment it lands.
- **Assign the user when you start.** The moment work begins on an issue, assign
  the user to it so it's visibly taken. Unassigned = fair game; assigned = in
  progress by someone.
- **A stage label is the only thing that stops an issue being started.** `idea`,
  `planning` and `human` mean *not yet*, and they are absolute. Absent one, an
  issue is startable the moment it exists, including one Claude filed a minute
  ago. The judgement about whether something is ready to be worked is made **when
  the label goes on**, or when it deliberately does not.
- **Run the gates before claiming the work is finished.** They are:
  1. **`./run.sh check`** — ruff, the conventions linter, the import sweep and
     the full suite. Green, no exceptions. (`./run.sh test` still runs pytest
     alone, for the tight loop.)
  2. **`./run.sh` — actually launch it.** This is the gate that tests cannot be.
     Pinch feel, cursor lag and drop reliability are not assertable; the only
     honest check is a person using it. **Launch the app for the user rather than
     describing what should happen** — the loop that matters here is *change a
     number → relaunch → they try it*, and keeping that loop tight is worth more
     than any amount of careful explanation.
  Gate 1 already covers what used to be a third, hand-run gate: the import
  sweep walks every module in the package and imports it, catching a circular
  import that the tests, which avoid most of Qt, will not.
  Verify before asserting: if a gate wasn't run, say it wasn't run. CI running
  green is not a substitute for gate 2 — no runner can tell you a pinch landed.
- **Size gate: source files ≤ 300 lines of code, test files ≤ 250.** Blank
  lines, `#` comments and docstrings do not count — the house style is to
  explain the *why* in module docstrings, and a cap that counted prose would put
  those two rules in opposition. **Group by subfolder, not filename prefix.**
  `scripts/lint/size.py` counts it off the AST and `./run.sh check` runs it, so
  "lines of code" is one number rather than one per person who measures. Nothing
  is grandfathered — a file over the limit gets split, not excused.
- **Agent velocity is first-class.** Agents drive this repo. Write code that is
  readable by design and lean — clear code is cheaper to reason about and faster
  for the next agent to extend. This is part of **Craft**, not a trade-off
  against it.

## Repo-specific conventions

- **No camera and no display in tests, ever.** The suite runs on synthetic
  landmark sequences, and `KINESIS_NO_GL=1` keeps Qt off the GL path. A test that
  needs a webcam, a window, or a person's hands is a bug — that is precisely what
  the "launch it" gate is for. This is *why* `gestures.py` and `filters.py` are
  pure; it is not an incidental property to be traded away.
- **`~/.config/kinesis/tuning.json` silently overrides the defaults in code.**
  Change a default in `Tuning` and nothing appears to happen, because the saved
  file wins on load. **Delete that file when changing a default**, or the change
  is invisible and the next hour goes to debugging the wrong thing.
- **Every module opens with a docstring that says why, not what.** "Runs as a
  separate process so capture can never stutter the UI" is the house style;
  "tracker module" is not. These docstrings are the architecture as it is
  actually written down, so a module that gains a constraint gains a line here.
- **Docstrings on MCP tools and control commands are an agent-facing surface**,
  and they are the only documentation of it. Every tool and every argument
  describes itself, in the terms a caller would use rather than the terms the
  implementation uses. Adding a tool without one is incomplete work, and
  `scripts/lint/agent_docs.py` fails the build over it — for a control command
  the arguments it wants named are the JSON keys the handler reads out of the
  request, not the parameter called `request`.
- **There is no backwards compatibility for `.kinesis` files, and that is the
  policy until the user says otherwise.** No migrations, no reading an older
  `FORMAT_VERSION`, no field kept alive because something might still write it.
  There is one machine and one person, and no saved board anybody would mind
  losing. **The version bump stays mandatory**: loading checks the format *and*
  version fields — and refuses before clearing the board you already had — so
  bumping is exactly what turns a silent misreading into a loud refusal. That
  check was found missing and added in #23; the rule had been documented and
  unenforced, which is the failure this file exists to prevent. The
  day the user has a board they can't afford to lose, this rule gets revisited —
  and that is their call, not a thing to infer.
- **Nothing in the codebase is temporary**, except small JSON or log files.
  Anything added must benefit the project long-term or be necessary to its
  development. Debug scaffolding either becomes part of the tuning panel or the
  overlay — both of which are permanent, deliberate features — or it goes.
- **`auxmd.md` is gitignored and stays that way** unless the user says otherwise.
  A consequence worth remembering rather than rediscovering: the backlog and its
  design notes do not exist in the GitHub repo, so anything in there that becomes
  a real commitment belongs in an issue, where it is durable.

## Issues, labels & priority

- **Issues come before PRs.** The unit of work is a well-specified issue: the
  **what**, **why it belongs in the project**, and the **roadmap — not the
  implementation intrinsics**. A future Claude reads it cold and says *"I
  understand the assignment, I know how to proceed."* That is what lets an issue
  run unattended.
- **File what you notice.** Claude may open an issue autonomously — for anything
  that will be a recurring theme or problem, or when it realises a tool would be
  useful more than once. Only things whose benefit outweighs the cost of
  implementing them get an issue; given a project with a finite scope, that bar
  is higher here than it would be elsewhere. If a Claude-written issue is a
  breaking change, changes how the board feels to use, or needs a human's
  judgement call, it must carry one of `idea`, `planning` or `human`. A `bug`
  usually should not wait — it is specific, the deciding already happened when
  the code broke.
- **Priority by label:** **architecture → infrastructure → bug → foundation →
  feature.** If the way we build isn't solid — a structural shape or convention
  missing (**architecture**), a tool or guardrail missing (**infrastructure**),
  or something broken (**bug**) — we halt and fix that first. Then **foundation**
  work makes the board itself more complete. Then **feature** work adds something
  new. **documentation** can be done at any time and never waits its turn.

### Labels

Stage labels (at most one; absence means ready):

- **idea** — might not add value; parked until the user decides. Must **NOT** be
  started.
- **planning** — has value, but the approach is still being discussed. Must
  **NOT** be started. Most of `auxmd.md` is at this stage by nature: the user
  wrote it down precisely because the shape isn't settled.
- **human** — can't be done end-to-end by an agent; needs a person. On this
  project that is a large category and not an escape hatch: anything whose
  success condition is *it feels right* needs hands in front of the camera.
- *(no stage label)* — ready: anyone can say "do issue N" and Claude can read it,
  implement it and merge it. This includes an issue Claude just filed itself.

Type labels (combinable with a stage label):

- **architecture** — process boundaries, the module dependency direction, the
  control-channel and `.kinesis` formats, the interaction model.
- **infrastructure** — tools and guardrails for the development process: CI, a
  size linter, a one-command gate runner.
- **bug** — something isn't working.
- **documentation** — edit/add documentation; never waits its turn.
- **feature** — a new board capability.
- **foundation** — groundwork that makes the board itself more complete.
- **feel** — responsiveness, gesture reliability, tuning. Its own label because
  it is the project's actual subject and it is neither a bug nor a feature; it
  almost always also carries `human`.

If a `planning` issue would affect how another issue gets implemented or is
thought of, that other issue must be marked **blocked by** the planning issue.
We prioritize anything that accelerates the improvement of the project itself.

## Overrides

Any rule in this file may be overridden by the user's explicit say-so — in the
current prompt or a previous one. The **one exception**: an issue carrying a
planning-stage label (`idea`, `planning` or `human`) must never be started while
the label is on it. The user may tell you to **remove the label and then do it** —
never to do it with the label still on. (The user *may* greenlight an issue that
is blocked by another; doing so lifts that block.)
