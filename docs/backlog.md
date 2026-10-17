# Backlog

## Show registered resumable sessions on each trail

Requested 2026-10-17. Implemented; see [continuous tracking](continuous-tracking.md#resumable-session-registry).

When a new agent session is detected and assigned to a Taskmaster work trail,
retain that registration and list it on the trail. The user should be able to
find every registered session associated with the work and use its actual
provider session ID to resume it later.

The current Dias card ID (`dias:codex-hook:<hash>`) is an internal identity, not
the provider's resumable session ID. Preserve both identities and their verified
mapping; never attempt to recover a provider ID from the hash or infer one from
a title, project name, or timestamp.

Acceptance criteria:

- Capture the actual provider session ID from a verified lifecycle record or
  other authoritative provider metadata, together with the provider name and
  corresponding Dias card ID. Missing IDs remain explicitly unavailable.
- Register a newly detected session against its detected or user-selected trail
  using the existing assignment rules. Ambiguous matches stay unassigned until
  resolved.
- Show a persistent session list on each trail, including sessions that are no
  longer live. Display provider, actual resumable ID, assignment provenance,
  registration time, last-seen time and current or historical assignment status.
- Let the user copy the resume ID and, where verified for that provider, its
  resume command. Do not launch or resume a session automatically.
- Preserve corrections: moving or clearing a session changes its current trail
  registration and retains clearly labelled assignment history. A resumed session
  with the same provider identity must not create duplicate registrations.
- Keep session registrations separate from verified Autowork execution membership.
  Registering a chat must not manufacture evidence that work ran for an objective.
- Expose a consistent session list to Dias and the Taskmaster CLI/report consumers,
  within their existing data-access boundaries. Decide retention and non-loopback
  exposure explicitly before extending the contracts.

Implementation starts with inspecting the existing lifecycle adapters for an
authoritative provider-ID-to-card-ID mapping, then adding durable registration
history and the per-trail projection. Verify new detection, resume deduplication,
manual correction, ambiguity, unavailable IDs and persistence across restarts.
