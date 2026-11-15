# Handoff: make Beacon click the search result the user names

Branch: `claude/project-thread-r4z51h` (pushed; Helper changes are done but **not compiled or tested**, and the Brain part hasn't been started).
Repo: justincodner/LocalAiHackathonCornell. The live app runs from `~/AIHackathon/wt-live` with logs in `wt-live/logs`. Other threads restart the live app too, so build and test in a separate worktree.

## The bug
In Chrome, on Google results for "james webb", Justin said "Click on the Wikipedia article". The HUD said "I pressed James E. Webb - Wikipedia." but no page opened.

## What the logs and a live capture show
- `session.jsonl` t_15: the model chose `click {"id": 46}`. `helper.log`: `POST /ax/act ok=true 32ms`.
- A real mouse click (`SyntheticEvents.click` with restoreCursor) takes at least 85 ms of pauses, so this 32 ms press went through **AXPress**. That means `webClickPoint` returned nil. Chrome reports AXPress on a web link as a success but doesn't follow it, so nothing happened. (The comment in `AccessibilityService.act` already says this.)
- Captured snapshot of the same page (fresh, unscrolled, 81 elements, `truncated=false`):
  - id 46 is `link "James E. Webb - Wikipedia" [908,428,366,118]`, a source card in the **AI Overview side panel**, not an organic result. So the model picked the wrong target too.
  - Organic results have labels like `James E. Webb Wikipedia https://en.wikipedia.org › wiki › James_E` and `James Webb Space Telescope Wikipedia https://en.wikipedia.org › wiki › James_...`. The site name and the cite URL are part of the link label.
  - Chrome reports elements below the fold with a **zero-height frame pinned to the page's edge**, e.g. `[177,840,341,0]` where 840 is the bottom of the web area. Elements scrolled above the view are pinned to the top edge. The snapshot drops these, so the organic Wikipedia results are **missing from the snapshot**, and only `/ax/find` reaches them. Find ids belong to find's own snapshot_id and can be acted on with it.
  - The knowledge-panel heading `heading "James Webb Space Telescope"` (id 55) shows what the query most likely means.
- Most likely cause: Justin had scrolled, so card 46 was under the toolbar or HUD, or pinned with height 0. `canClick` and the hit test then failed, and the code fell back to AXPress, which does nothing in Chrome.

## Done on the branch (Helper, Swift, uncompiled)
1. `AXReader.swift`: `AXURL` was added to `nodeAttributes`. There's a new `AX.webURL(_:)` that keeps only http and https. `readNode` sets `n.url` for `AXLink`.
2. `AXNode.swift`: new `var url: String?`.
3. `SnapshotBuilder.swift`: `Out.url` and `SnapshotElement.url` were added, and the element JSON includes `"url"` only for links that have one. The model's text block is unchanged. `/ax/find` matches carry `url` too (`AccessibilityService.find`).
4. `AccessibilityService.act("press")` for web elements:
   - Tries `webClickPoint`, which now uses `clickPoints(in:visible:screen:)`. It clips the frame to the AXWebArea's frame and tries the centre, then 4 points near the edges, and hit-tests each one (same pid, and the hit must be the element or a descendant).
   - If no point works, it performs `AXScrollToVisible`, waits 250 ms, and tries again.
   - Only after that does it fall back to AXPress, and it logs when it does.
   - New `webArea(of:)`. `isInWebArea` now uses it.
5. Tests were added in `helper/Tests/BeaconHelperTests/AccessibilityTests.swift`: `WebClickTests` (click points) and `LinkURLTests`.
6. `contracts/snapshot.schema.json` still needs an optional `url` (string) on element. `additionalProperties` isn't false, so this is documentation only. Also mention it in `contracts/openapi.yaml` if there's a place for it.

**Verify on the Mac:** run `cd helper && swift build && swift test`. Check that `v as? URL` works for a CFURL from AXUIElementCopyMultipleAttributeValues. If it doesn't, use `CFGetTypeID(v) == CFURLGetTypeID()` and `(v as! NSURL).absoluteString`.

## Still to do (Brain, Python)
Add `brain/beacon_brain/results.py` with tests in `brain/tests/test_results.py`. Hook it into `loop.py` just before the `named_button` check (~line 553). Skip it for screen questions and summaries.

1. **Results page detection:** the app is in `page_summary.BROWSERS`, and the query comes from the address-bar textfield value (`q=` on google, bing, duckduckgo, yahoo, brave or ecosia hosts) or from the window title (`"<query> - Google Search - ..."`, `"<query> at DuckDuckGo"`).
2. **Parsing the request:** a verb (click, open, tap, select, choose, pick, go to, visit, take me to, pull up, show me) with an optional "on". The target can be:
   - an ordinal: first, second, top, last, "result number 2"
   - a site or title name: "the Wikipedia article", "the NASA one", "wikipedia"
   - a topic: "the article about the telescope", "the one from nasa"
   - a mix: "the second wikipedia result"

   The nouns to strip are result, link, article, page, site, website, one, entry, video. If nothing matches, return None and let the normal flow handle it, so "click images" and "click next" are unaffected.
3. **Candidates:** links from the snapshot, plus `helper.request("/ax/find", {"query": "http", "limit": 50})`. That returns every organic result in page order, including offscreen ones, because their labels contain the cite URL. Also run find for the main name word ("wikipedia"). Wrap find calls in try/except `HelperError`.
   - Dedupe by url, else by label. Skip links on the search engine's own host, and nav links like All, Images, News and Videos.
   - **Organic** means the label contains `https://` or `›` (or `>` / `&gt;`). If no label on the page has that (Bing or DDG), treat a link as organic when its url is off the engine's host and its label has 2 or more words.
   - **Spoken title:** the label text before the URL, with trailing site words found in the host removed ("James Webb Space Telescope NASA (.gov)" becomes "James Webb Space Telescope"). For a "Title - Site" card, use the part before " - ".
4. **Matching:** every name word must appear in the title words, the url host, or the joined text. Rank by: organic first, then context score, then page order.
   - **Context score:** how many title words are in the query words plus the words of headings that contain every query word. Get the headings from the snapshot and from find(query).
   - For "james webb", this picks the organic "James Webb Space Telescope" Wikipedia result over "James E. Webb" and over the AI Overview card. Ordinals index the organic list.
5. **Acting:** add a `run_open_result` in loop.py.
   - Press the element with `/ax/act` using that element's own snapshot_id (find ids use find's snapshot_id).
   - Then poll `helper.frontmost()["window"]` every 250 ms for up to about 4 s and wait for it to differ from the snapshot's window title.
   - If the title doesn't change and a url is known, press cmd+l, type the url, and press return. This is the guaranteed navigation.
   - If there's no url, say "I clicked X, but the page didn't open."
   - Say "Opening <title>." Log each step with `append_step`, the way `run_browser_plan` does.
6. **Brain tests:** use a fixture built from the captured snapshot and find output above. Cover:
   - "Click on the Wikipedia article" picks the JWST Wikipedia organic result.
   - "open the first result" picks the NASA result.
   - "the second one" works.
   - "click the James E. Webb one" works.
   - "click images" returns None.
   - A non-search page returns None.
   - The url fallback runs when the title doesn't change.

   Run `uv run --project brain pytest brain/tests -q`.

## Then
Open a PR from `claude/project-thread-r4z51h`. Don't restart the live app (wt-live) unless Justin says so in the thread.
