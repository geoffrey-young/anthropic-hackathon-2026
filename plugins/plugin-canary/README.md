# Claude Code Canary

Early warning system for Claude Code plugin security.

For full documentation, findings, and project context, see the
[project README](https://github.com/geoffrey-young/anthropic-hackathon-2026).


## Install

```bash
claude marketplace add geoffrey-young/anthropic-hackathon-2026
claude plugin install plugin-canary@anthropic-hackathon-2026
```


## Usage

### Automatic auditing

Use any third-party plugin agent as normal.  On first use, plugin-canary
will intercept the call, perform a security review, and either clear the
plugin or warn you.

### Manual auditing

```
/plugin-canary code-simplifier
```

### Manage audit state

```bash
python3 scripts/manage.py list       # show all discovered plugins
python3 scripts/manage.py status     # show audit status
python3 scripts/manage.py approve <key>   # manually approve
python3 scripts/manage.py revoke <key>    # revoke approval, re-audit next use
```


## License

MIT
