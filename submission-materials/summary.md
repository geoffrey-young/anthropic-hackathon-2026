# Submission Summary

Claude Code plugins can inject arbitrary content into Claude's reasoning
through the hook system's exit-2 stderr channel.  The runtime delivers
it without filtering or source attribution.  Claude acts on it if the
instructions look helpful... which is precisely the problem, because a
well-crafted attack looks helpful.

We discovered this while building a documentation validation plugin.
When we fed it fabricated API docs through exit-2 stderr, Claude rewrote
the user's code to use a nonexistent parameter without hesitation.  The
fake error arrived through the same channel as legitimate system
feedback, and Claude could not tell the difference.

Plugin-canary uses this same mechanism as a defense.  It intercepts
third-party plugin invocations, blocks the call, and injects a security
audit prompt through exit-2 stderr.  Opus 4.6 reads every file in the
plugin, performs a structured threat analysis using [Verbalized Sampling](https://arxiv.org/pdf/2510.01171)
techniques, and decides whether to proceed, at near-zero marginal cost,
since Claude was already going to reason about the blocked call.

The defense works.  But it works because the vulnerability exists.
Anyone can host a Claude Code marketplace.  MaliciousCorgi compromised
1.5M developers through VS Code.  PromptArmor has demonstrated the
Claude Code attack chain.  Plugin-canary adds a defense layer within
today's constraints, while surfacing an architectural gap that should
not exist.
