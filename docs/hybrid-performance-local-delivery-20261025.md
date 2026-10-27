# Optional hybrid ranking and performance preferences — local delivery

The existing HTML/API frontend now defaults to TOPSIS and offers an explicit experimental hybrid selection. The experimental parameters are fixed (`theta=1`, `alpha=0.5`, `k=10`); there is no claim of learned weights or improved accuracy. Consumer reasons, images and catalog categories remain in their existing flow.

Photo, game and battery importance are visible controls. A zero means the criterion does not participate, not a zero phone score. Photo uses comparable DXOMARK photo subscores; game uses comparable Honor of Kings average FPS only. Other games and unknown game scope cannot activate that measurement without explicit user choice. No mixed-use battery duration is available, and capacity is not substituted. Positive unsupported preferences stay positive until the user changes them; the dedicated “暂不计入” actions preserve hard filters and original intent and revoke confirmation.

Extraction keeps source evidence, inferred judgments and game intent visible. Only allowed request fields are sent to the API. The selected method/version and game scope propagate into the confirmed request reused for recommendations, explanations and exports. Editing needs text, filters, importance, scope or method revokes the previous result/download. A refreshed runtime version also invalidates confirmation even when the four release identity fields are unchanged.

The coverage counts shown before a recommendation are explicitly the default new-phone reference, with the current conditions pending validation. A result replaces those counts with current-filter coverage. Counts for the three metrics are independent, not their common intersection.

## Verification state

- Frontend Node suite: 31 tests passed, including execution of the real app event handlers through a minimal DOM boundary; no paid provider is used.
- Complete Python application suite: 723 tests in 143.592 seconds, `OK`, exit 0.
- Root-owned independent backend/data integration: pipeline 37 tests passed; release machine check passed with overall/manual/WPS acceptance still pending. Default legacy fingerprints, hybrid exports and explanation consistency, hard-condition preservation and missing-performance guards were checked separately.
- Local source TCP: offline fixture meta, static module, hybrid recommendation (51 ranked configurations) and extraction of unsupported game plus positive game/battery preference all passed.
- Runtime bundle: 198 files; isolated temporary extraction served the new module with matching SHA-256 and generated 51 hybrid results over TCP. No bundle was uploaded.
- Database, release manifest and image manifest fingerprints remain unchanged.
- Real desktop/mobile browser acceptance is owned by the coordinating task and is pending at this document's handoff. Node DOM tests and TCP checks do not establish layout, gestures or physical-device acceptance.
- Public upload, merge, deployment, real DeepSeek accuracy, physical iOS/Android testing and manual data acceptance are not completed by this local delivery.

Commands run from the project root:

```powershell
node --test tests/frontend/*.test.mjs
& 'C:/Users/zhang/Documents/ChatGPT/人工智能算法精英大赛/outputs/zhixuan-phone-recommender-v2-worktree/.venv/Scripts/python.exe' -m unittest discover -s tests -p 'test_*.py' -q
& 'C:/Users/zhang/Documents/ChatGPT/人工智能算法精英大赛/outputs/zhixuan-phone-recommender-v2-worktree/.venv/Scripts/python.exe' scripts/build_deploy_bundle.py --root . --output local_build/task-3-hybrid-runtime.zip
```

An ignored local preview launcher and exact evidence are recorded under `.superpowers/sdd/2026-10-24-production-hybrid-performance/`. The launcher explicitly discloses its deterministic offline classifier, uses a temporary quota store and disables paid explanations. It is a test fixture, not production service configuration.
