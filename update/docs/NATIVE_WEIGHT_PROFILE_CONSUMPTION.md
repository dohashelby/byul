# Native weight file profile consumption

The existing `capture-native-weight-files` command consumes the explicitly
prepared common native profile. Its file-selection stage previously applied the
default `_safe` whitelist before the common parser and hard-coded the legacy
512-row / 256-KiB bounds. Valid `/9` recordings containing approved Online
metadata or a larger recording were consequently dropped as `native_gap`.

The dedicated consumer now uses the selected, byte-verified prepared profile's
common constants: `/9` is at most 2,048 rows / 2 MiB, while the original legacy
limits remain unchanged. Each canonical row remains limited to 16 KiB. The
canonical buffer stops at the selected bound before joining. The common
`parse_recording` remains responsible for metadata, exact source/compiler/code,
callback, session/process, hash-chain and row admission. No common validator,
sensor, transport, CLI, registry, pack or source pin is changed by this patch.

The existing 32-recording / 64-MiB input-document, 256 selection-event and 32-file
bounds remain. Invalid or oversized recordings are incomplete; earlier valid
recordings and independently read file mismatch observations remain available.
Unknown compilers, altered source/code, cross-session metadata and raw fields
remain rejected by the common parser. `/8` does not inherit `/9` read limits.

The focused regression uses the fixed common `/9` EXAMPLE writer, callback and
parser. It combines declared legacy weight iterator factory events with an
approved copied Online method body, then consumes them through the existing
Kubernetes target and capture CLI boundary. Only the file reader is injected.
The model iterator remains unconsumed. These are CPU EXAMPLE observations;
they do not prove an actual serving worker, tensor loading, final model or
approved runtime image. The existing UNKNOWN verdict and all producer,
actual-consumption and whole-run proof flags remain unchanged.

Common prerequisite: `e24dfb5653d66f9d82fc4537a995208f29ad113e`.
The original `0df509e` IPC and `90f839b` transport-report patches are separate
preserved prerequisites in the A-2 integration branch. A-1 must integrate each
patch once, review the final immutable full-product snapshot and update its
literal consumption pin and matching test. A-2 does not edit that gate.

Validation: the focused test plus native file, Kubernetes file, transport-report,
origin/Ref/incomplete-functional invariants and the existing architecture gate.
Preserve the original three-case failure before this change. Native `/9`
Kubernetes/worker/GPU execution, formal SUP05/SUP06 security EVD/RES/RPT branches,
and the parent/A-2 complete goal remain pending.
