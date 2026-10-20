# Konstructor implementation plan

## P0: safety and integrity
- [x] Require explicit approval and a successful, current validation before apply.
- [x] Bind validation to staged artifact hashes and the plan identity; reject changed staged files.
- [x] Reject `add` collisions and unsafe target paths immediately before writing.
- [x] Keep web terminal opt-in and loopback-only; reject remote binds.
- [ ] Add HTTP authentication, limits and server-side authorization before enabling any remote mode.

## P1: usable project manager
- [ ] Add multi-project profiles and onboarding commands; this is a separate product slice.

## P2/P3: generation and validation
- [ ] Connect a real provider and structured generation contract; existing deterministic generator is only a demo.
- [ ] Validate staged changes in an isolated copy with bounded commands.
- [ ] Expand scanner coverage and vertical-slice generation.

## P4: install and release
- [ ] Add Debian packaging and CI only after package metadata and supported runtime are settled.

## Acceptance for this safety increment
- Missing, failed, stale validation blocks apply.
- Tampered staging and pre-existing add destinations block apply.
- Remote web bind is unavailable and web command requires explicit opt-in.
- Target path and file state are rechecked before each write.
