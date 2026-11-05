# Amendment 1: bounded segment-API transport

This is an append-only amendment to the protocol with SHA256 `4014bb7613ec28aa5edd4e162b5ccdec8e95bb638ad387e14a01e1f02565bcbf` and original registration with SHA256 `f885ef087947e4b85e8d53aa30c4fe576f2d378dcacb44118b789366b0a829c9`. The original protocol, registration, proofs and preparation code remain unchanged. This amendment is made after initial audio acquisition began and must be separately committed, timestamped and read back before amended acquisition runs.

## Observed transport defect

Controlled requests for the same selected recordings showed that a single request for 0 through 3,600 seconds was refused with HTTP 422. The server's error header identified a maximum segment duration of 300 seconds. Requests for 0 through 300 and 300 through 600 seconds both returned audio that independently decoded to 300 seconds. Another refusal identified an endpoint beyond the actual recording duration. These observations concern transport, not aircraft labels or localization outcomes. No localization scores informed this amendment.

## Repair and scope

For selected recordings served by this capped segment API, read its recording-duration and segment-limit metadata privately. Retrieve contiguous, nonoverlapping segments of at most 300 seconds covering exactly the first min(actual duration,3,600 seconds), independently decode the segments, concatenate them in chronological order and run the unchanged detector and window rule once on that prefix. A recording of at most 300 seconds is still supplied whole. Retain the original nullable manifest duration in the original-duration receipt. Do not replace an unknown manifest duration with 300 seconds. Metadata and every segment request use the same production-wide server locks, backoff and concurrency policy as other study acquisition.

The amended registration binds the exact candidate code, its executed-check receipt, the private real-probe receipt and an opaque affected-recording list derived from the fixed sample. Apply the repair uniformly to all 343 recordings on that list, including two earlier transport failures and 341 not yet attempted, without replacement or outcome-based selection. None has an already-ready original-code window. Preserve all original attempt records and report initial failures, retries and final processing coverage separately. Failed or unfinished rows at the original processing stop remain misses in the same fixed denominator.

## Unchanged commitments

The 3,912,415-recording frame, 1,600 selected recordings, sample key and draw, priors, donors, claimed timestamps, first-hour cap, 300-second selected-window cap, released detector and defaults, event ranking, keyed fallback, entrant versions, P/W/A controls, timeout limits, scoring and intervals are unchanged. Other source acquisition continues using the original registered code. The processing stop remains 2026-11-05 15:30 EST and independent scoring/readback remains due at 16:00 EST.

The amended registration and this amendment's separate timestamp proof must be independently parsed and matched to their actual bytes. Pending calendar attestations are not described as Bitcoin-confirmed timestamps. Detailed probes, private paths and the affected-recording list are not public artifacts.
