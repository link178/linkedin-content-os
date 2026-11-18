# Portfolio copy

Proposed text for a later case study on `jjsarroyo.dev/en`. This file does not change that site.

## Project description

**Evidence-Grounded Content Engine**

An AI-assisted editorial workflow that checks claims against a proof registry, runs semantic, editorial, and critic gates, and keeps publication behind an explicit human command.

It is not a wrapper that asks a model for a LinkedIn post and stores the reply. The model may propose text. Python decides whether the text still matches the cited evidence, whether the body hash is current, and whether a person has approved that exact body. A scheduler can prepare a candidate. It cannot approve or publish.

The public repository demonstrates the deterministic contracts with a synthetic corpus. Live generation needs the Cursor CLI and is a separate, optional path. The system does not claim that every draft is factually correct, or that an unattended weekday run has been proven.

## Suggested case-study sections

1. **Problem.** Generic generation does not remember which sentences are allowed to be public, and it collapses drafting, approval, and posting into one step.
2. **Approach.** A local proof registry, hash-bound gates, a capped revision loop, and a publish command that the rest of the pipeline cannot import.
3. **What is deterministic.** Claim class, stale hashes, unsupported causes, readiness, and the refusal to publish without a separate command. Show the offline demo.
4. **What still needs a model.** Discovery wording, draft composition, and the critic's qualitative pass. Say plainly that those calls are untrusted input.
5. **What a person still does.** APPROVE, REVISE, or DROP, then a separate approval, then a separate publish.
6. **Limits.** No guarantee of truth. No verified unattended calendar run. The public corpus is synthetic.
