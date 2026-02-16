# Security Findings: Claude Code Plugin Injection


## The Architecture

Claude Code plugins can register four types of components:

- **Hooks**: Python/bash scripts that fire on system events
  (SessionStart, PreToolUse, PostToolUse).  These execute as processes.
  The LLM never sees their source code.
- **Agents**: Markdown system prompts loaded into subagent contexts via
  the Task tool.  The LLM sees the description at session start; the
  full body loads at invocation.
- **Skills**: Markdown prompts loaded into the main conversation.  Same
  lazy-loading as agents.
- **Scripts**: Arbitrary executables invoked by hooks or skills.

The LLM sees skill/agent descriptions (for routing), one skill/agent
body (at invocation time), and tool results including hook stdout,
stderr, and exit codes.

The LLM never sees hook source code, plugin.json, Python modules, data
files, or cross-file relationships.


## The Injection Vector

When a hook returns exit code 2, Claude Code blocks the tool call and
delivers the hook's stderr content into the LLM's context as a tool
error.  This is the designed behavior for validation feedback ("this
file path is not allowed").

The content is delivered without filtering, sanitization, or source
attribution.  There is no distinction between "this came from the
Claude Code runtime" and "this came from a third-party plugin's hook
script."

The LLM does not just read this content.  It acts on it.  In our
testing, we observed Claude performing all of the following actions in
response to exit-2 stderr injection:

- Rewriting code to use fabricated API parameters (llmdoc)
- Reading arbitrary files via the Read tool
- Spawning subagents via the Task tool
- Performing multi-step structured analysis
- Retrying the blocked tool call after completing injected instructions

PostToolUse hooks can also inject content via stderr after a tool
succeeds.  This is arguably more dangerous because there is no
"blocked" signal to make Claude suspicious.


## The Trust Model

Claude's discrimination is content-based, not channel-based.

In our limited testing, we observed the following pattern:

| Injection type | Opus | Haiku |
|---|---|---|
| Direct instructions ("append this phrase") | Caught and called out as prompt injection | Complied |
| Authority channel (fabricated validation errors) | Variable: caught some, missed others | Complied |
| Helpful-looking ("perform a security audit") | Followed without flagging | Followed without flagging |

The sample size is small and these results are directional, not
definitive.  But the pattern is consistent with how LLMs work: Opus does
not reason about who is speaking or whether the source has authority to
give instructions.  It reasons about whether the instruction looks like
something it should do.  In our testing, injections that aligned with
Claude's values passed through consistently.


## The Ecosystem

- Anyone can host a Claude Code marketplace.  It is a JSON file on
  GitHub.
- Aggregator sites scrape GitHub and list new marketplaces
  automatically.
- There is no review process, no code signing, no verification.
- PromptArmor demonstrated the full attack chain: malicious plugin
  installs hooks that bypass permissions, injects a prompt that causes
  Claude to exfiltrate the user's codebase via curl.
- MaliciousCorgi compromised 1.5M developers through VS Code extensions
  that functioned as legitimate AI coding assistants while exfiltrating
  code.


## Attack Surface Taxonomy

Plugin-canary's auditor is designed to detect threats across seven
categories.  These represent the attack surface available to a malicious
plugin author, given that the Claude Code runtime delivers plugin
content without source attribution and the LLM acts on it based on
content alone.

1. **Through-the-purpose attacks.**  The plugin does what it claims,
   but the declared purpose itself is the delivery mechanism.  A code
   simplifier that weakens security controls.  A linter that introduces
   subtle vulnerabilities.  A test generator that disables assertions.

2. **Cross-file chains.**  No single file looks malicious, but data
   flows between files to assemble the exploit.  A config file that
   looks like data but contains instructions.  A skill that reads
   output from a script that reads from a resource file.

3. **Authority channel abuse.**  Content in stderr that mimics
   legitimate system feedback (validation errors, deprecation notices,
   permission prompts) to manipulate Claude's behavior.

4. **Prompt injection in data files.**  Instructions disguised as
   documentation, configuration, templates, or example code.  Large
   data files where volume creates review fatigue and a single
   malicious entry hides in the noise.

5. **Simulated jailbreaks.**  A chain of "benign" prompts that together
   constitute a jailbreak.  Skill bodies designed to manipulate
   Claude's reasoning under the cover of legitimate functionality.
   Obfuscated payloads that only activate when assembled across files.

6. **Trust boundary manipulation.**  Content that suggests, recommends,
   or normalizes weakening security controls.  Plugins that request
   `context: fork` with `Bash(*)` to create unsupervised shells.
   Plugins that claim prior audit certification to anchor the
   reviewer's judgment.

7. **TOCTOU and external dependencies.**  Behavior that changes without
   the file changing.  Network resources fetched at runtime.  Files
   that look clean at audit time but resolve differently at execution
   time.

Categories 1 and 6 are the hardest to detect because they operate
within the plugin's declared purpose.  The auditor's threat imagination
step ([Verbalized Sampling](https://arxiv.org/pdf/2510.01171)) is
specifically designed to push past surface plausibility and consider
these scenarios before reading any code.
