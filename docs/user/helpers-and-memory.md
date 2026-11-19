# Helpers and your saved memory

Helpers (subagents) the main agent starts now see what Clover has saved about
you: your `USER.md` preferences and `MEMORY.md` notes. So a helper knows, for
example, how you want logins handled instead of asking you.

- It is a read-only copy taken when the helper starts. Helpers cannot write to
  your memory, so nothing collides with the main agent.
- It applies to every helper, whichever model or provider it uses.
- To go back to clean-slate helpers: `clover config set delegation.inherit_memory false`.
