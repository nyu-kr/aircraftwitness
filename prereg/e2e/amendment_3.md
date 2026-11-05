# Amendment 3: seek-required media transport and revised deadlines

Date: 2026-11-05.

This is an append-only amendment to the protocol with SHA256 `4014bb7613ec28aa5edd4e162b5ccdec8e95bb638ad387e14a01e1f02565bcbf`. It supplements the original registration and Amendments 1 and 2 without changing their bytes or proofs. Commit, separately timestamp and independently read back this amendment and its hash-only registration before any affected primary acquisition rerun or deadline switch.

## Observed transport refusal

The original preparation guard explicitly rejected seek-required media containers with `unsupported_seek_required`. The affected scope is the 258 fixed-sample recordings whose original attempts ended in that refusal. None has an already-ready window. This is a transport limitation, not evidence that their audio is unreadable. The scope is fixed from the retained preparation failures, not from detector flags, localization answers or scores.

## Uniform transport change

Permit a separately frozen bounded-seek fallback for this direct-media refusal. A local HTTP byte-range broker serves the decoder while routing every upstream request through the original production HTTP client, shared per-server locks, redirect handling and backoff. The decoder receives only the local broker endpoint, not source credentials. The broker validates upstream range responses, closes them when the decoder stops and does not download or spool the whole source merely to make it seekable. An unsupported or incorrectly served range remains an explicit preparation failure.

Decode the chronological audio prefix starting at zero, capped at the first min(available recording duration,3,600 seconds). Container indexing and seeking do not authorize scanning later audio, a second window search or a later selected window. Run the unchanged released detector, event ranking, millisecond offset rule, keyed fallback and 300-second window selection once on this prefix. All other transport branches remain unchanged, including the previously registered segment-API and archive-member paths.

Apply exactly the same candidate to all 258 affected recordings without replacement or selection by diagnostic success. The fixed first-five primary smoke, if used, belongs to that same attempt roster and does not create a five-success eligibility barrier. Diagnostic bodies and outputs cannot replace primary windows. Preserve the original terminal bytes and their links to the initial attempt journal, record the new attempts separately and report initial failures, retries and final coverage. Failed or unfinished recordings remain misses in the fixed denominator.

The accompanying registration binds the candidate and base-code hashes, executed checks, the opaque affected scope and its baseline receipt, safe source-range diagnostic evidence and the authorization receipt. The private manifests, URLs, member names, detailed probes and affected-recording list are not public artifacts.

## Global deadline change

The fixed processing stop for the entire E2E sample changes from **2026-11-05 15:30:00 EST (20:30:00 UTC)** to **2026-11-05 16:15:00 EST (21:15:00 UTC, Unix 1793913300)**. Independent scoring, reconciliation and aggregate readback change from **16:00 EST** to **16:45 EST (21:45:00 UTC, Unix 1793915100)** on the same date.

This 45-minute extension permits the uniformly scoped transport attempt and its downstream frozen pipeline while accommodating the existing shared-server scheduling. It is registered before localization outcome inspection. It applies to all 1,600 selected recordings and every P/W/A arm, not only the affected transport scope. It does not extend any per-request, detector, entrant or allocation limit. The new deadlines become effective only after the same dated amendment and registration are committed, timestamped and independently read back with their authorization binding.

Every selected recording without a valid terminal entrant result at 16:15 EST is an explicit miss. Results arriving later are separate. Any retained 15:30 snapshot is historical or diagnostic, not the amended primary cutoff. The primary scorer must require the amended cutoff and its exact copied-input and runtime bindings before reading study outcomes. This remains a time-bounded pipeline rate, not an unlimited eventual-completion rate.

## Unchanged commitments

The 3,912,415-recording frame, 1,600 selected recordings, sample key and draw, priors, donors, uncorrected source timestamps, first-hour eligibility, selected-window cap, detector and defaults, entrant versions, P/W/A controls, numerical settings, per-run limits, hit rule, fixed denominators, weighting, bootstrap plans and interval reporting remain unchanged. No localization score informed this transport scope or extension. Positions and scoring remain with the independent custodian.

All acquisition and compute remain on the approved cluster. Every public server, including redirects and byte ranges, retains the shared four-request limit and registered backoff. This amendment does not release another lane's durable server hold or transfer ownership. Credentials remain file-path-only.

Only this sanitized amendment, its hash-only registration and their original timestamp proofs are additional public candidates. The original frozen history remains private. Pending calendar attestations are not Bitcoin-confirmed timestamps. Registration does not claim that the amended acquisition or scoring has completed.
