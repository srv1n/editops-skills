# Producer Integration Docs

These docs describe how a platform/product repo (“producer”) emits **run dirs** that the CreativeOps toolchain can compile and render:

**Producer → Director → ClipOps**

Producer responsibility is to emit **facts** (inputs + signals), not edits.

If you’re confused about “toolkit vs producer”:
- `clipper/` (this repo) is the toolkit (Director + ClipOps + schemas).
- A “producer” is just your product repo (web app / tauri app / iOS app / etc.) that writes `creativeops/runs/...`.

Start here:
- Layout and “what to copy vs install”: `docs/CREATIVEOPS_PRODUCER_ADAPTERS_LAYOUT_V0.1.md`
- Run-dir portability rules: `docs/CLIPOPS_RUN_DIR_PORTABILITY_AND_BUNDLING_V0.4.md`
- External handoff packet (screenshots + demo videos, non-technical): `docs/CREATIVEOPS_ASSET_REQUEST_PACKET_V0.1.md`

Platform guides:
- iOS: `docs/producers/IOS_PRODUCER_V0.1.md`
- iOS handoff checklist (roles + next steps): `docs/producers/IOS_PRODUCER_HANDOFF_CHECKLIST_V0.1.md`
- iOS drop-in kit (templates + scripts): `docs/producers/IOS_PRODUCER_DROP_IN_KIT_V0.1.md`
- Web (Playwright): `docs/producers/WEB_PLAYWRIGHT_PRODUCER_V0.1.md`
- Tauri (desktop): `docs/producers/TAURI_PRODUCER_V0.1.md`

More detail / historical:
- iOS integration deep-dive: `docs/IOS_PRODUCER_INTEGRATION_V0.2.md`

