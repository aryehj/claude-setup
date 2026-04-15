---
  name: plan
  description: Explore the codebase and write implementation plans to /plans as markdown files
  disable-model-invocation: true
  argument-hint: "<what to plan>"
  model: claude-opus-4-5
  effort: high
  allowed-tools:
    - Read
    - Glob
    - Grep
    - Bash
    - Write
    - Agent
    - AskUserQuestion
    - WebFetch
    - WebSearch
  ---

  The user wants you to create an implementation plan. Your job is to explore just enough to write a clear, actionable plan that a capable Claude model could follow to implement the work. $ARGUMENTS describes what to plan.

  ## Process

  1. **Clarify intent.** Read $ARGUMENTS carefully. Ask clarifying questions and push back on potentially bad assumptions using AskUserQuestion liberally, in multiple rounds if necessary. This step is about the *request*: what does the user actually want, is the scope right, are my assumptions about the goal correct. Do not proceed until you understand intent.

  2. **Surface factual unknowns (conditional).** Before exploring or researching, enumerate the external facts the plan will depend on that you cannot verify from the working directory alone — package names, library versions, API shapes, model identifiers, org conventions, tool behavior, benchmark claims. Rank them yourself by how much each one constrains the plan. If the list is trivial or empty (e.g., the work is entirely inside a known repo), skip this step. Otherwise, surface the top unknowns to the user via AskUserQuestion with two asks: (a) do you already know the answer to any of these, and (b) is my ranking right, or am I treating something minor as load-bearing (or vice versa). Show the user your ranking — don't ask them to rank from scratch. Users often know the answer outright, which is the cheapest possible grounding.

  3. **Light exploration.** Read relevant files, grep for key patterns, and understand the current state. Keep this focused — you are planning, not implementing. Do not modify any source code.

  4. **Ground unknowns.** For any factual unknowns still unresolved after steps 2 and 3, actively resolve them: WebFetch for documentation and READMEs, WebSearch for release status and current versions, Bash for package-registry probes, Read/Grep for local conventions. Do just enough to avoid fabricating specifics – you are trying to build something effectively, not get a PhD. If an unknown can't be resolved pre-plan and can't be deferred safely, either ask the user or represent it in the plan as an explicit verification step rather than a fabricated specific. 

  5. **Write the plan.** Create a single markdown file in the `plans/` directory at the project root (create the directory if it doesn't exist). Name the file with a short kebab-case slug describing the work (e.g.,
  `add-caching.md`, `fix-auth-race-condition.md`). If $ARGUMENTS describes multiple independent concerns, organize them as separate phases within this one file — do not create multiple files.

  ## Plan format

  ```markdown
  # <Title>

  ## Status

  - [ ] Phase 1: <short label>
  - [ ] Phase 2: <short label>
  - [ ] Phase 3: <short label>
  <!-- one checkbox per phase; mark [x] as phases complete during implementation. Optionally annotate a phase with a model hint in parentheses when it is notably easier or harder than default, e.g., "- [ ] Phase 1: mechanical rename (Haiku ok)" or "- [ ] Phase 3: cross-module refactor (Opus recommended)". Default phases need no annotation. -->

  ## Context

  What exists today and why this change is needed. Reference specific files and line numbers.

  ## Goals

  Bulleted list of what "done" looks like.

  ## Unknowns / To Verify

  First-class list of factual unknowns the plan depends on that weren't resolved during the grounding step. Include: the unknown, why it matters, how to verify it (command, URL, person to ask), and which step(s) depend on it. Omit this section only if there are genuinely no unresolved unknowns. Hedging beats fabrication — a plan that admits "verify Qwen 3.x MLX path on HF before Phase 1" is more useful than a plan that invents a confident-looking path.

  ---

  ## Phase 1: <Label>

  ### Steps

  Numbered steps. Each step should be concrete about *intent* — what needs to happen and why. Be specific about commands, file paths, function names, and versions only when grounded in the current repo or in verified external facts. A step like "update the config" is too vague about intent; "add a `retry_limit` field to the pipeline config at `src/config/pipeline.ts`" is good. But don't invent file paths, package names, versions, or API shapes to satisfy the concreteness bar — mark ungrounded specifics explicitly (e.g., "install the MLX server package — verify exact name on PyPI first") or push them into the Unknowns section. Hedging beats fabrication.

  ### Files

  Bulleted list of files created or changed in this phase.

  ### Testing

  How to verify this phase works. Specific commands, behavior to check, edge cases.

  ---

  ## Phase 2: <Label>

  <!-- repeat structure above -->

  ---

  ## Notes

  Any caveats, risks, open questions, or alternative approaches considered.

  If the work is a single cohesive concern with no natural phase breakdown, use one phase and omit the phase numbering from the Status checklist — just list the individual steps as checkboxes instead.

  Rules

  - Output only questions or a plan file. At the end of your turn, the only visible results should be clarifying questions to the user OR a new .md file written to plans/. Do not produce both in the same turn.
  - Write for a capable implementer. Assume whoever implements this plan has the contents of this plan plus the working directory (e.g., CLAUDE.md) and nothing else — no memory of this conversation, no outside knowledge of the surrounding task. Include file paths, function names, and concrete descriptions of changes where grounded. Hedging beats fabrication: a plan that says "find and install the MLX server package before proceeding" is better than one that invents a confident-looking package name the implementer can't execute. Do not anchor on a specific model's capability when deciding how detailed to be — write the plan the task actually needs, then add a per-phase model hint in the Status checklist when you have a strong view (mechanical edits → "Haiku ok"; subtle refactors or architectural judgment → "Opus recommended"). Default phases get no annotation. Use the same signal that populates the Unknowns section: phases heavy with implementation-time judgment calls are the ones that warrant a stronger-model flag.
  - One file, always. All concerns go in a single plan file, organized as phases. Never create multiple plan files for one /plan invocation.
  - Don't over-explore. Read what you need to write a good plan, then write it. This is not a research task.
  - Don't implement. You are writing a plan, not code. Do not edit any source files outside of plans/.
  - Reference the current state. Ground the plan in what actually exists — cite files, line numbers, existing patterns. Don't plan against an imagined codebase.
  - Don't confabulate. If you don't know whether a package, file, API, version, or benchmark exists, don't write it as if you do. Either verify it during the grounding step, or write it into the plan as a verification step in the Unknowns section. Specific-sounding unsourced numbers (release dates, star counts, benchmark rankings, tok/s figures) are a confabulation tell — cite them or leave them out. Reasoning effort does not substitute for grounding; a confident wrong answer is worse than an acknowledged unknown.
