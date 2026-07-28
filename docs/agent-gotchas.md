# Agent gotchas

> **status:** current
> **owner:** Dan Browne
> **updated:** 2026-07-28
> **superseded-by:** —

Real failure modes hit by agents working on this repo, with the fix. Not repo-specific
enough to pay for in [`../CLAUDE.md`](../CLAUDE.md) on every session, but each one cost
real turns. Read this when drafting a message to a character-limited surface, or when a
shell command is failing in a way that makes no sense.

## Length-limited message surfaces (2026-05-27)

When drafting for a hard character limit — Discord (2000 default, 4000 with Nitro; Dan has
Nitro), Twitter/X (280), SMS (160) — **write the final text to a file and measure it with
`wc -m`. Never eyeball.** Two pitfalls that compounded in one session and produced an
over-limit message:

- **`wc -c` counts BYTES, not characters.** UTF-8 multi-byte glyphs inflate the byte count
  without inflating the character count: 🔴 = 4 bytes, em-dash — = 3 bytes, arrow → = 3
  bytes, all 1 codepoint each. `wc -m` (and Python's `len(str)`) count codepoints, which is
  what Discord/Twitter/SMS count.
- **An estimate is not a measurement.** "Roughly 3,500 chars" stacks an arbitrary error on
  top of any tool error. Run the count on the exact final text — not an earlier draft, not
  an inferred-from-structure estimate.

```bash
# Right — measure codepoints on the exact final text:
wc -m < /tmp/discord-msg.md                                  # 3754
python3 -c "print(len(open('/tmp/discord-msg.md').read()))"  # 3754

# Wrong — bytes; undercounts content with emoji/em-dashes:
wc -c < /tmp/discord-msg.md                                  # 3808
```

Aim for ≥5% headroom below the limit so trivial edits don't push you over. The target
surface's own counter is authoritative; `wc -m` agrees with Discord to within a handful of
characters (typically CRLF-vs-LF or grapheme-cluster edge cases).

When presenting the final text to the user for copy-paste, render it inside a **4-backtick
fence** (` ```` `) — not 3-backtick. Inner triple-backticks in the message (code snippets)
terminate a 3-backtick outer fence and produce a fragmented copy-block. The 4-backtick
outer fence keeps it as one contiguous copy region.

## Shell quoting in zsh (2026-06-28)

The interactive shell here is **zsh**, and its quoting/word-splitting rules differ from bash
in ways that have repeatedly bitten agents building commands on the fly. Two failure modes
and their fixes:

- **zsh does NOT word-split unquoted variables.** In bash, `P="--profile X"; aws s3 ls $P`
  splits `$P` into two args; in zsh it passes the whole string as one arg and the command
  errors ("Unknown options: --profile X"). **Fix:** never stuff multi-token flags into a
  single var. For AWS, set the environment instead: `export
  AWS_PROFILE=ArchimedesDanAdmin AWS_REGION=us-east-1` and drop the per-command
  `--profile/--region` flags entirely.
- **Inline command-building hits parse errors fast.** Nested `$( … )`, escaped `\$(...)`,
  globs like `--include=*.py` (zsh tries to glob `*.py` → "no matches found"), and
  especially building an `aws ssm send-command --parameters 'commands=[...]'` payload inline
  produce `parse error near ')'`-class failures that waste turns. **Fix:** for anything
  non-trivial, write the script to a file and feed it in opaquely rather than escaping
  through the shell:

  ```bash
  # robust: build the remote command + tool input as data, not shell text
  cat > /tmp/remote.sh <<'EOF'      # quoted heredoc → no local expansion
  …multi-line script runs verbatim on the target…
  EOF
  python3 -c "import json,base64;b=base64.b64encode(open('/tmp/remote.sh','rb').read()).decode();\
  json.dump({'InstanceIds':['i-…'],'DocumentName':'AWS-RunShellScript',\
  'Parameters':{'commands':[f'echo {b}|base64 -d|bash']}},open('/tmp/ssm.json','w'))"
  aws ssm send-command --cli-input-json file:///tmp/ssm.json   # no quoting hell
  ```

  Quote globs (`--include='*.py'`) or use `rg`. When a command must interpolate a value with
  special characters, build it in Python (proper escaping) and write a `--cli-input-json` /
  file argument rather than hand-escaping in zsh.
