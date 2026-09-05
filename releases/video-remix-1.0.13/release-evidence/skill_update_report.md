# video-remix-1.0.13 release report

- Baseline: `video-remix-1.0.12`
- Candidate: `video-remix-1.0.13`
- Supersedes: `video-remix-1.0.12`
- Candidate paired release gate: `valid`, 0 errors
- Live paired release gate after installation: `valid`, 0 errors

## Fixed

- Seeds the user-approved individual-pouch front/side photos and retail-outer-box front/back/side photos into every new durian-daifuku-v2 project with fixed SHA-256 checks.
- Requires packaging-visible shots to bind package level, visible face and exact approved asset ID. Product-body references and packaging from another layer cannot satisfy the gate.
- Requires both `approved=true` and `user_approved=true`; free-form status strings such as `approved_master` are not promoted to approval.
- Ships no global shipping-carton artwork. A shipping carton requires a project-specific user-approved, hash-bound master; inferred or invented carton branding is blocked.
- Blocks execution from a historical project that contains `planning/execution_redirect.json` with `status=redirected`.
- Keeps identity and product replacement atomic and retains exact-original retry, pixel scale, shape, surface, filling, endpoint, bite orientation, instance count and layout continuity gates.

## Evidence

- `candidate-release-gate.json`
- `live-release-gate.json`
- `skill_update_candidates.json`

The complete `video-remix-1.0.12` tree remains in `releases/video-remix-1.0.12` and the `video-remix-1.0.12` Git tag for rollback.
