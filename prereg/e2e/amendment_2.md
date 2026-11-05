# Amendment 2: restore omitted archive-member metadata

This is an append-only input-transport amendment to the protocol with SHA256 `4014bb7613ec28aa5edd4e162b5ccdec8e95bb638ad387e14a01e1f02565bcbf` and original registration with SHA256 `f885ef087947e4b85e8d53aa30c4fe576f2d378dcacb44118b789366b0a829c9`. It is independent of Amendment 1's capped-API transport repair. Original files and attempt records remain unchanged. Commit, separately timestamp and independently read back this amendment and its registration before affected acquisition reruns.

## Observed staging defect

For 121 fixed-sample recordings, the private fetch manifest omitted the archive-member field already present in the frozen source manifest. Their unchanged URLs returned ZIP containers, which were incorrectly sent directly to the audio decoder. All 121 attempts ended in explicit preparation failure. The custodian traced the missing field to the frozen manifest and matched the same selected recordings. No aircraft labels, detector decisions, locator answers or scores selected this repair.

## Repair and scope

Restore only those 121 omitted member fields from the exact frozen manifest. The custodian's independently reopened before/after comparison confirms that every one of the 1,600 packet identities, row order, URL, source timestamp, duration and source assignments remains unchanged. Bind the original and corrected private fetch-manifest hashes, frozen source-manifest hash, opaque 121-recording scope and comparison receipt in a separate hash-only registration.

Use the original preparation code with SHA256 `4b561dfff08aab59d06448bb361e044125e64dee1b6a47fe0c7db97c5af8a5bd`. Its existing ZIP index/range and stored-or-deflated streaming path, already exercised before the original run, selects the exact registered member and decodes at most the eligible first hour. No whole-container download, new code, replacement recording, alternate member or truth-based fallback is authorized. Apply the corrected metadata uniformly to all 121 affected recordings, preserve their original failures and report the later attempts separately.

The existing fleet-wide per-server concurrency, backoff, server holds and transfer ownership still apply. A stamped amendment does not bypass another lane's durable server hold.

## Unchanged commitments

The fixed population and sample, priors, donors, uncorrected claimed times, first-hour prefix, selected-window rule and duration, detector, entrants, P/W/A arms, budgets, scoring and intervals are unchanged. Both affected scopes retain the original processing stop of 2026-11-05 15:30 EST and independent scoring/readback deadline of 16:00 EST. Unfinished or failed selected recordings remain misses in the fixed denominator. No amended acquisition is represented as completed by this registration.

Only this sanitized amendment, its hash-only registration and their timestamp proofs are additional public candidates. The detailed comparison, private manifests, member names, URLs and affected-recording list are not public artifacts. Pending calendar attestations are not Bitcoin-confirmed timestamps.
