# V1.6 manual High pilot checklist (replaces retired V1 task activation)

1. Obtain owner/architect review and independent exact-SHA QA for WG-A contracts. Confirm source snapshot SHA, authorized owned paths, role ID and 12-department head from `studio/v16/worker/ROLE_REGISTRY.v1.json`.
2. Run `python -m studio.v16.worker.manual_kernel registry` and `python -m unittest discover -s tests -q` on the local snapshot. Generate one manual launcher from the job file; human opens GPT-6 High chat.
3. Work locally using uploaded source. No GitHub or Drive connector call without first identifying exact provider/resource/ref/operation/reason and receiving human consent; shared account identity is never an authenticated job grant.
4. Worker completes local scope and tests, produces binary patch, manifest, audit report card and compact RUN-V16 resume capsule. Report exact NOT_RUN gates and ledger/relay pending where appropriate.
5. Independent QA validates exact patch hash and source scope. Authorized owner/head publishes only through separately verified permissions and provider readback; no worker merges or deploys.
6. WG-G subsequently removes legacy Work task/PR router runtime and workflow code. Do not delete immutable prior receipts. No scheduled, event, automated or paid AI worker launch is a valid pilot step.
