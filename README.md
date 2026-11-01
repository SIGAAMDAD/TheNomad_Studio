# Studio communications evidence branch

This branch is reserved for authenticated, immutable, project-scoped GitHub
command and receipt records. Its historical V1 seed remains preserved in Git
history; source and current deployment authority live on
[main](https://github.com/SIGAAMDAD/TheNomad_Studio/blob/main/README.md).

The R5R coordinator owns APSW worker/shift/lease state and persists the effect
intent before a configured GitHub publisher writes a command. It accepts a
receipt only after trusted, exact-commit remote readback. A branch write alone
does not prove browser execution, lease ownership, QA or source approval.

Do not use this branch as a source integration target, scheduled worker pool,
queue authority or repository for secrets. Preserve the immutable history and
refuse force-push/deletion. See
[docs/CHATGPT_SETUP.md](https://github.com/SIGAAMDAD/TheNomad_Studio/blob/main/docs/CHATGPT_SETUP.md).
