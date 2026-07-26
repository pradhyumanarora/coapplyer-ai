# Playwright MCP — LinkedIn Easy-Apply Auto-Applier Playbook

Living notebook of everything learned (things that worked AND things that failed) while
supervised-applying to SDE2 / Software Engineer 2 roles on LinkedIn via the Playwright MCP.
Seeds the `coapplyer_ai` project. Companion file: `SELENIUM_ELEMENTS.md` (element/id reference).

Applicant: Pradhyuman Arora · pradhyumanarora@gmail.com · +91 8439803020 ·
Hyderabad, India · total/relevant exp 2 yrs · CTC 50 LPA → expected 70 LPA · notice 60 days ·
relocation Yes (anywhere in India) · Indian citizen, no sponsorship · Bachelor's = Yes ·
skills: Scala 0, Java 2, JavaScript 2.

---

## 0. The single biggest fork: CLASSIC modal vs SDUI (Server-Driven UI)

LinkedIn serves Easy-Apply in **two totally different renderers**. Detect BEFORE anything else.

| | Classic modal | SDUI (Server-Driven UI) |
|---|---|---|
| Apply trigger | `button.jobs-apply-button` | `a[href*='openSDUIApplyFlow=true']` |
| Form DOM | light DOM — normal selectors work | **inside SHADOW DOM** — normal selectors return null |
| Field ids | `...-<JOB_ID>-<QID>-<kind>` in light DOM | same id shape but nested in nested shadow roots |
| Dialog | `div[role=dialog]` findable directly | must recurse shadow roots to find `role=dialog` |
| Success | URL navigates to post-apply page | a NEW `role=dialog` appears; URL may NOT change |

**Rule:** decide the branch from the trigger, then use the matching field-access strategy for the
whole flow. Mixing them wastes a lot of turns.

---

## 1. Shadow-DOM is the core SDUI challenge

Every SDUI field/button lives in (often deeply nested) shadow roots. `page.locator(...)` and
`document.querySelector(...)` do NOT pierce shadow DOM. Use a recursive walker inside
`browser_evaluate` / `browser_run_code_unsafe`:

```js
function* walk(root){
  for (const el of root.querySelectorAll('*')) {
    yield el;
    if (el.shadowRoot) yield* walk(el.shadowRoot);
  }
}
// find the apply dialog:
const dialog = [...walk(document)].find(e => e.getAttribute && e.getAttribute('role')==='dialog');
```

Everything (read fields, list buttons, set inputs, click) is built on `[...walk(document)]`.

---

## 2. `browser_run_code_unsafe` gotchas (cost me the most time)

1. **`setTimeout` is NOT defined** in the run-code sandbox. Use `await page.waitForTimeout(ms)`
   for delays. `setInterval` also unavailable.
2. **Outer async-scope variables are invisible inside `page.evaluate(() => {...})`.** The evaluate
   body runs in the PAGE context, not your Node scope. Either (a) read state in a *separate*
   `evaluate` and return it, or (b) pass values as `evaluate` args:
   `await page.evaluate((id) => {...use id...}, myId)`.
3. **Real typing into shadow inputs:** `page.keyboard.type(text, {delay:120})` works AFTER you
   `.focus()` the target element inside an evaluate. Needed for typeahead fields (see §5).
4. Return small JSON summaries from evaluate — don't return DOM nodes (non-serializable).

---

## 3. Verify-then-fill: set ONE field at a time

LinkedIn/React reverts values that are set "wrong". Reliable pattern per field:

1. Focus/clear the input.
2. Set `.value`, then dispatch BOTH `input` and `change` events (bubbles:true).
3. Read `.value` back and confirm it stuck **before** moving to the next field.

Batch-setting several controlled inputs in one pass silently loses most of them. One field,
read-back, next.

---

## 4. Label → field mapping heuristic

Field ids are opaque (`...(<JOB_ID>,<QID>,<kind>)`). To know WHAT a field asks, climb up to ~6
ancestors and grab the first `label`/`legend` innerText. In shadow DOM, "parent" can be the
shadow host: use `el.parentElement || el.getRootNode().host`.

```js
function labelFor(el){
  let n = el;
  for (let i=0;i<6 && n;i++){
    const l = n.querySelector && n.querySelector('label,legend');
    if (l && l.innerText.trim()) return l.innerText.trim();
    n = n.parentElement || (n.getRootNode && n.getRootNode().host);
  }
  return '';
}
```

---

## 5. GEO-LOCATION / city typeahead — special, easy to get wrong

Location fields (id ends `-location-GEO-LOCATION`) run an **async suggestion fetch**. Setting the
native `.value` synthetically does NOT fire that fetch, so Continue then fails with
"Required / Select an option" and the step silently won't advance.

Correct sequence:
1. `.focus()` the input (inside evaluate).
2. `await page.keyboard.type('Hyderabad', {delay:120})` — real key events.
3. `await page.waitForTimeout(2500)` for suggestions.
4. Read `role="option"` nodes (ids like `basic-result-1234`) via the walker.
5. **Click** the option whose innerText matches `/Hyderabad, Telangana, India/i` — don't assume
   the first one.

Leaving location empty is the #1 cause of a "stuck at Continue" dead-end.

---

## 6. Consent / privacy step is a distinct step near the end

Multi-step SDUI flows often insert a **privacy-consent step at ~86%** (just before Review).
It's a single REQUIRED `input[type=checkbox]` (id ends `...,<QID>,multipleChoice)-0`) with a
label like "I consent … privacy notice". If it's unchecked, `.click()` it, then the button label
changes to "Review your application". Easy to miss because prior steps had no checkboxes.

---

## 7. Success detection (SDUI) — check the DIALOG scope

After Submit, `await page.waitForTimeout(3000)`, then confirm success by finding a NEW
`role=dialog` whose heading/body matches `/application was sent/i` (URL does NOT navigate for SDUI).

**Gotcha:** a whole-page `innerText` join sometimes returns only `<head>`/`<script>` noise → false
negative. Locate the newest `role=dialog` node first and test *its* text. Dismiss via the button
with `aria-label="Dismiss"`.

---

## 8. Double-render & console noise

- **Double render:** SDUI sometimes mounts the step twice briefly; read fields only after the
  step settles (a short `waitForTimeout(800-1200)` after each "Continue" avoids grabbing a
  half-rendered form or duplicate field ids).
- **Console noise:** expect ~185-200 benign telemetry/CSP errors per action. Ignore them; they are
  NOT failures.

---

## 9. Answer heuristics (used across all jobs)

| Question | Answer |
|---|---|
| Years of a skill on the resume | ~`2` |
| Years of a skill NOT on the resume (e.g. Scala) | `0` |
| Willing to work from <Hyderabad> office | `Yes` |
| Bachelor's degree | `Yes` |
| Nationality / Citizenship | `Indian` |
| Valid work permit (India) | `Yes` |
| Require sponsorship | `No` |
| Previously employed at <company> | `No` (unless true) |
| Gender | `Male` |
| Notice period | `60 days` |
| Current / Expected CTC | `50 LPA` / `70 LPA` |
| Experience bucket dropdown | `1-3 years` |

When unsure, ASK the user rather than guess — a wrong screening answer can hard-fail the app.

---

## 10. Per-job log (what each flow actually looked like)

> QIDs & exact ids are in `SELENIUM_ELEMENTS.md §3`. This is the narrative / step map.

### Classic modal
- **PDI (4407312731), Deloitte (4442311710), Charles Schwab (4445340586)** — light DOM,
  standard `getElementById`. 2-4 steps: contact → screening → review → submit. Submitted. ✅

### SDUI
- **DAZN (4440465288)** — first full SDUI mapping; multi-step with numeric skill years + dropdowns.
  Established the walker/read-fields/set-fields helpers. Submitted. ✅
- **Teradata** — SDUI but effectively single screening page → review → submit. Submitted. ✅
- **MathWorks (4431071241)** — contact → resume → optional top-choice (left unchecked) →
  1 additional Q (Bachelor's radio) → review → submit. Submitted. ✅
- **Turing (4444486366)** — 5-step. email select + phone + resume radio + privacy select=Yes.
  Submitted. ✅
- **Kore.ai (4432365959)** — 4-step. email + phone + resume + education radio(Yes) +
  Node.js years numeric=1. Submitted. ✅
- **Endowus (4436492838)** — 5-step. resume + Scala=0 + Java=2 + JS=2 + Hyderabad-office select=Yes.
  First job that exercised the skill-absent→0 heuristic. Submitted. ✅
- **Freshworks (4434221716)** — longest: 7 steps + consent.
  Contact(~14%) → Resume(29%) → optional(~29%) → Work exp(43%, prefilled) →
  Education(57%, prefilled) → Additional Qs(71%) → **Privacy consent(86%)** → Review(100%).
  Exercised GEO-LOCATION typeahead (§5) for the location field and the consent checkbox (§6).
  Submitted. ✅

**Total submitted this project: 10** — PDI, Deloitte, Charles Schwab, Teradata, DAZN, MathWorks,
Turing, Kore.ai, Endowus, Freshworks.

---

## 11. Recommended driver loop for `coapplyer_ai`

```
open recommended collection URL
for each job card:
    open job → read trigger
    if button.jobs-apply-button      -> CLASSIC branch (light DOM helpers)
    elif a[...openSDUIApplyFlow=true] -> SDUI branch (shadow-DOM walker helpers)
    else -> external/Apply-on-company-site -> SKIP + log
    loop steps:
        read fields (walker) -> map labels -> fill one-at-a-time w/ read-back
        handle GEO-LOCATION via keyboard.type + pick option
        click Continue; waitForTimeout(~1000) to let step settle
        if step label ~ consent -> tick checkbox
    Review -> Submit -> waitForTimeout(3000)
    verify success via dialog-scope /application was sent/i
    Dismiss; record status in DB
```

Keep classic vs SDUI as two field-access strategies behind one step-runner interface.
