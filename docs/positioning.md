# Runcorder Positioning Spec

## One-line pitch

Runcorder is an always-on flight recorder for Python scripts. You run your script, or run it under a batch system, and if it fails you get a useful failure bundle instead of just a traceback.

## Why this exists

There are deeper tracing tools, but they ask for more setup and more commitment.

The everyday problem is earlier than that:

- you wrote a script,
- you want to see that it is making progress,
- if it crashes, you want more than a raw traceback,
- you want to hand the artifact to intelligent tools and get to a fix fast.

That is the gap Runcorder fills.

## Product thesis

Every script should have a lightweight debugging harness by default.

That harness should:

- show what the script is doing now, live in interactive runs and as progress updates in non-interactive logs,
- capture uncaught exception details automatically,
- preserve enough run context that the failure has useful surrounding state,
- emit a compact artifact that intelligent tools can read and reason about,
- stay much lighter-weight than full tracing.

## Who this is for

- Python engineers writing one-off scripts, CLIs, and scripts launched under batch systems
- data and ML engineers running long scripts locally or in notebooks
- ops and automation authors who need faster failure diagnosis
- developers who want intelligent tools to fix broken scripts without first adding logging by hand

## Core workflow

1. A user writes a Python script.
2. They run it directly or under a batch system with Runcorder enabled by default, or via a thin wrapper.
3. While it runs, Runcorder shows progress in the terminal or emits useful status updates into logs.
4. If it succeeds, the user got visibility with almost no overhead.
5. If it fails, Runcorder writes a compact failure bundle.
6. The user gives that bundle to intelligent tools.
7. If that still is not enough, the user escalates to deeper tracing.

## The artifact

The output should feel like a flight recorder for a script run, not like a trace dump.

The important constraint: this stays coarse. It must not drift into a second tracing system.

The exact artifact schema and contents live in the product spec. This document is about why the artifact matters and how to talk about it.

## Integration

Runcorder should feel easy to adopt:

- no-code CLI wrapper for existing scripts
- useful for scripts run under batch systems as well as directly in a terminal
- library entry points for applications that want explicit integration
- lightweight enough to leave on by default for everyday script work

The concrete API surface, option names, and behavior live in the product spec.

## Watchpoints

Runcorder should capture coarse progress context, not turn into a second tracing system.

The implementation details for how that context is captured belong in the spec. The positioning point is that users should get meaningful progress and failure context without paying the cognitive or runtime cost of full tracing.

## Positioning

### Runcorder

Default-on visibility for CLI scripts and batch-run scripts.

- lightweight
- fast to adopt
- live visibility
- intelligent-tool-ready failure bundle

The message should be simple: start with Runcorder, escalate to deeper tracing when needed.

## What it is not

- not a general logging framework
- not a full debugger
- not a tracing system
- not an observability platform
- not a replacement for deeper tracing tools

## Why users should care

Today, a failed script usually gives you too little context. You rerun it with print statements, add ad hoc logging, or start debugging by hand.

Runcorder changes that default. The first failure already comes with enough context for intelligent tools or a human to start reasoning.

## Messaging pillars

### 1. Put it on every script

The setup cost must feel low enough that people do not debate whether to use it.

### 2. See what the script is doing

The watch output reduces anxiety during long runs and gives immediate signal when the script is stuck, whether you are watching a terminal or reading batch logs.

### 3. Better than a traceback

The failure artifact should explain what the script was doing before it crashed, not just where it crashed.

### 4. AI-ready by default

The artifact is designed to be pasted into an intelligent-tool workflow without extra cleanup.

### 5. Escalate cleanly

If the lightweight artifact is insufficient, there is an obvious next step: use deeper tracing.


## Homepage draft

### Hero

Runcorder is a flight recorder for Python scripts.

Watch your script run. If it crashes, hand the bundle to intelligent tools and get to a fix faster.

### Subhead

Visibility while it runs, and a useful failure bundle when it breaks, without the weight of full tracing.

### CTA ideas

- Put it on every script
- Bring script visibility to batch runs
- See what broke, before you add logging
- Start with Runcorder, escalate to deeper tracing when needed

## Current decisions

- This document defines positioning and messaging, not the implementation contract.
- [docs/spec.md](docs/spec.md) is the source of truth for integration, runtime behavior, artifact format, and operational details.
- Positioning should stay stable even if the exact API or artifact structure changes.

## Current recommendation

Treat Runcorder as the default lightweight product and deeper tracing as the escalation path.

That product ladder is easier to explain, easier to adopt, and more aligned with how people actually debug scripts.