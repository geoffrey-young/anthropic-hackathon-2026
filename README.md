# Claude Code Canary

Early warning system for Claude Code plugin security.


## The Short Version

Claude Code plugins can inject arbitrary content into Claude's reasoning
through the hook system's exit-2 stderr channel.  The runtime delivers
this content without filtering or source attribution.  Claude treats it
as authoritative and acts on it, including following complex
instructions, reading files, and spawning subagents.

Plugin-canary uses this same mechanism to force a cross-file security
review before any third-party plugin runs.  It intercepts plugin
invocations, injects a structured audit prompt, and lets Opus 4.6
analyze every file in the plugin before deciding whether to proceed.

The defense works.  But it works because the vulnerability exists.


## The Discovery

This project started as something else entirely.  Day 1 was about
documentation: a plugin called **llmdoc** that ships LLM-optimized
API docs with packages and validates code at write time via PreToolUse
hooks.

The validation worked by intercepting Write tool calls, checking the
code against a documentation database, and blocking with corrective
feedback via exit-2 stderr when issues were found.  Claude received the
feedback and rewrote the code.  Five seconds, full cycle.

Then we tested it with fabricated documentation.

The LLMDOC data file claimed FastAPI v0.201.0 renamed `pattern=` to
`match_expr=`.  Neither the version nor the parameter exists.  Claude
believed it without question.  Haiku and Opus both rewrote the user's
code to use the nonexistent parameter and never flagged the
discrepancy.  The fake validation error arrived through the same channel
as legitimate system feedback, and Claude treated it identically.

That was the moment the project pivoted.  If a plugin hook can override
Claude's training knowledge by injecting fabricated information through
exit-2 stderr, then the plugin system has an unaddressed security
surface.


## Testing the Boundaries

We tested systematically.

**Direct instruction injection** ("append this phrase to your
response"):  Opus caught and refused every attempt, explicitly calling
them prompt injection.  Haiku complied with all of them.

**Authority channel attacks** (fabricated validation errors mimicking
real tooling):  Opus caught some and missed others.  The same attack
produced opposite results across runs.

**Helpful-looking injections** ("perform a security audit of these
files"):  Opus followed every one without hesitation.

The pattern: Claude does not reason about who is speaking or whether the
source has authority.  It reasons about whether the instruction looks
like something it should do.  This is not "no trust boundaries."
Opus clearly discriminates.  But the discrimination is content-based,
not channel-based.  Well-crafted injections that align with Claude's
values pass through consistently.


## Two Missing Boundaries

**1. The runtime passes everything through.**  A third-party hook
script can put anything into exit-2 stderr and the Claude Code runtime
delivers it straight into the LLM's context.  No filtering.  No
sanitization.  No flag saying "this came from a third-party plugin."

**2. Claude will act on it.**  Not just passively process the
content, but actively follow the instructions, including spawning
subagents with real side effects.  Our audit prompt triggers agent
creation from a hook's stderr output.  This should not be possible.


## What Plugin-Canary Does

Plugin-canary sits in that gap.  It uses exit-2 injection to force
Claude to review all of a plugin's files before allowing it to run.

### Automatic mode (hooks)

1. **SessionStart**: `discover.py` reads `installed_plugins.json`,
   walks every file under each plugin's install path, writes the
   registry to `state.json`.

2. **PreToolUse (Task)**: When Claude invokes a third-party plugin's
   agent, `gate.py` checks state.  If unaudited, it blocks (exit 2)
   and injects: "call the security-auditor agent with these file paths."

3. **Claude performs the audit.**  The security-auditor agent reads
   every file in the plugin and runs a 3-step analysis:
   - **Threat imagination**: brainstorm specific attack scenarios before
     reading code ([Verbalized Sampling](https://arxiv.org/pdf/2510.01171):
     "think about thin tail distributions, push past your first
     hypotheses")
   - **Data and control flow tracing**: systematic review of every
     file against 10 security heuristics, with cross-file interaction
     analysis
   - **Hypothesis testing**: revisit brainstormed scenarios against
     evidence: SUPPORTED, REFUTED, or OPEN

4. **Claude retries.**  `gate.py` sees `audited: true`, allows through.

5. **PostToolUse**: Records `decision: "approved"`.  Future sessions
   skip the audit.

### Manual mode

`/plugin-canary <plugin-name>` triggers the same pipeline on demand.
Presents findings and offers the user a choice: approve, keep auditing,
or remove.

### The "double dip"

When a tool call is blocked by exit-2, Claude was already going to
spend tokens reasoning about the error.  Plugin-canary redirects that
reasoning through a security lens.  No extra API calls.  Just
redirected attention.


## Why This Matters Now

**Anyone can host a marketplace.**  A Claude Code marketplace is a
JSON file on GitHub.  No review process, no signing, no verification.
Aggregator sites scrape GitHub hourly and list new marketplaces
automatically.

**This is not theoretical.**
[MaliciousCorgi](https://www.koi.ai/blog/maliciouscorgi-the-cute-looking-ai-extensions-leaking-code-from-1-5-million-developers)
compromised 1.5 million developers through two VS Code extensions that
looked like legitimate AI coding assistants while exfiltrating code to
servers in China.

**The Claude Code attack chain has been demonstrated.**  PromptArmor's
["Hijacking Claude Code via Injected Marketplace Plugins"](https://promptarmor.substack.com/p/hijacking-claude-code-via-injected)
showed a malicious plugin using hooks to bypass permissions and inject
prompts that cause Claude to exfiltrate the user's codebase to an
attacker's server.

Claude Code's architecture loads plugin components lazily: skill
descriptions at session start, full bodies only at invocation, hook
scripts never.  Claude never sees all of a plugin's files at the same
time.  A plugin can have a clean-looking skill prompt paired with hook
scripts that exfiltrate data.  Claude has no way to know those scripts
exist.

Plugin-canary forces all files into view simultaneously and asks for a
thorough review.  It does not solve the architectural gap.  It adds a
layer of defense within the constraints that exist today.


## The Tension

Plugin-canary works because:

1. The runtime allows arbitrary content from third-party hooks to enter
   Claude's context via exit-2 stderr.
2. Claude follows well-crafted instructions it finds in that content,
   including spawning subagents.

These are the same two properties that make the attack possible.  We
built a defense that depends on the vulnerability it defends against.
This is not a clever trick.  It is the only mechanism available.  The
plugin system provides no first-party API for "review this plugin's
files before allowing it to run."

The defense is real.  The value is real.  But the fact that we could
build it means something is missing in the architecture.


## Version History

We explored four injection strategies:

| Version | Method | Stderr payload | Context isolation |
|---------|--------|---------------|-------------------|
| v1 | File contents in stderr | Up to 50KB | None |
| v2 | File paths; Claude reads via Read tool | ~7KB | None |
| v3 | "Call this agent" instruction | ~1KB | Full (subagent) |
| v4 | v3 + /plugin-canary skill | ~1KB | Full (subagent) |

v1 hit a byte cap.  v2 eliminated it.  v3 isolated the review in a
subagent.  v4 added user-facing control.  Plugin-canary ships v4.


## Limitations and Caveats

This is a hackathon project, not a production security tool.  I am not
a security researcher.  It demonstrates a real vulnerability and a real
defense, but it is not "install this and you're safe."  The limitations
below are real, and several are fundamental.

- **Only officially installed plugins.**  Plugin-canary discovers
  plugins from `installed_plugins.json`, populated by `claude plugins
  add`.  Plugins loaded via `--plugin-dir` or dropped directly into
  the plugin directory are invisible to the discovery hook.

- **Hook-only plugins bypass the gate.**  The PreToolUse gate fires
  on Task calls (agent invocations).  A plugin that installs only
  hooks and never exposes an agent, command, or skill will never
  trigger the gate.  Its hooks just run.  Scanning all plugins at
  SessionStart would partially address this, but even that would not
  catch a malicious SessionStart hook, since it would already be
  running.

- **Model-dependent.**  Audit quality depends on the reviewing model.
  The agent is pinned to Opus.

- **The defense is a prompt injection.**  A sufficiently sophisticated
  attacker could craft plugin content that undermines the review
  methodology.  The auditor and the attacker share the same context.

- **Plugin-layer defense for a runtime-layer problem.**  A proper fix
  would involve the Claude Code runtime itself: content source
  tagging, a first-party plugin review API, or trust boundaries
  between hook output and system instructions.


## Project Structure

```
anthropic-hackathon-2026/
  README.md                         This file
  FINDINGS.md                       Security analysis and attack taxonomy
  journal/
    hackathon-journal.md              6-day development narrative
    DAILY_HIGHLIGHTS.md               Daily highlights and key moments
  plugins/
    plugin-canary/
      .claude-plugin/plugin.json      Hooks: discover, gate (pre+post)
      hooks/discover.py               SessionStart: populate registry
      hooks/gate.py                   PreToolUse/PostToolUse: gate + record
      agents/plugin-canary.md         3-step audit methodology (Opus)
      skills/plugin-canary/SKILL.md   /plugin-canary user command
      scripts/manage.py               CLI: list, status, approve, revoke
      core/__init__.py                Shared state I/O
  submission-materials/
    summary.md                        197-word project summary
```

## License

MIT
